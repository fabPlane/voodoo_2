#include <algorithm>
/*
 * sim_pcie_harness.cpp - sim_harness.h backend that drives the real board.
 *
 * The SpinalVoodoo Glide2x "sim" backend talks to hardware only through
 * sim_init / sim_write / sim_read / sim_idle_wait / sim_shutdown. This file
 * implements them over the Tang Mega 138K Pro Dock's PCIe BAR0 (see
 * sw/pcie/pci_bar.h for the 1 MiB window + status-word contract), so an
 * unmodified Glide program renders on the FPGA instead of in Verilator.
 *
 * Environment:
 *   VOODOO_PCI_BDF=0000:05:00.0   pick a device (default: first match)
 *   VOODOO_PCI_STATS=1            print MMIO counts at shutdown
 *   VOODOO_PCI_LOG=file           log slow reads (fdatasync'd); with
 *   VOODOO_PCI_LOG_ALL=1          every access, written before it is issued
 *   VOODOO_PCI_LOG_SYNC=1         fdatasync each line (survives a host power
 *                                 loss; slow - without it the page cache
 *                                 keeps the log if only the process dies)
 *   VOODOO_PCI_THREAD=1           queue writes in a 1M-entry ring and feed the
 *                                 card from a writer thread, so it keeps
 *                                 drawing while the game runs its logic (like
 *                                 the card's memory-backed FIFO); reads drain it
 *   VOODOO_PCI_WC=1               write-combined BAR0 writes (B173+, voodoo_dma.ko)
 *   VOODOO_REG_HIST=1             per-register write counts (and how many
 *                                 repeat the previous value) every 100 swaps
 *   VOODOO_PCI_DRYRUN=1           don't touch the board; reads return idle
 *   VOODOO_PCI_NOFLOW=1           disable the flow control below
 *   VOODOO_PCI_MIN_FRAME_MS=N     idle until N ms have passed since the last
 *                                 swap (duty-cycles the card's load)
 *   VOODOO_PCI_WRITE_GAP_NS=N     at least N ns between MMIO writes (caps the
 *                                 PCIe posted-write rate)
 *   VOODOO_PCI_PROFILE=1          one line per swap: where the frame's time
 *                                 went (FIFO-full stalls, idle waits, reads,
 *                                 host) plus write counts and pixel counters
 *
 * Flow control: a PCIe read must complete within the root port's completion
 * timeout (50 us - 50 ms, not configurable on the AMD root port), and PCIe
 * ordering makes a read wait behind every earlier posted write. If writes
 * back up because the core's 64-entry PCI FIFO is full, a following read can
 * take milliseconds and the host platform shuts down (seen with GLQuake at
 * full speed). So writes are only issued while the last status read says the
 * FIFO has room, and non-status reads wait for the core to be idle first.
 * Reads slower than 20 us are logged to stderr (and VOODOO_PCI_LOG).
 *
 * SPDX-License-Identifier: MIT
 */
#include "../../pcie/pci_bar.h"

#include <atomic>
#include <cstring>
#include <chrono>
#include <thread>
#include <sched.h>
#include <cstdarg>
#include <ctime>
#include <cstdlib>

static inline void _mm_pause_compat() { __asm__ __volatile__("pause" ::: "memory"); }

extern "C" {
#include "sim_harness.h"
}

namespace {
pci_bar::Device g_dev;
pci_bar::Bar g_bar;
bool g_open = false;
uint64_t g_writes = 0, g_reads = 0, g_idle_polls = 0;
int g_logFd = -1;
bool g_dry = false;
bool g_logAll = false;
bool g_logSync = false;
bool g_flow = true;
uint32_t g_budget = 0;  // writes the PCI FIFO can take without stalling
uint64_t g_budgetPolls = 0, g_slowReads = 0, g_maxReadNs = 0;
// VOODOO_BUSY_SAMPLE (with VOODOO_PCI_PROFILE, hwDebugRegs bitstreams): while
// stalled on a full PCI FIFO, also read debugBusy (0x240) on every poll and
// report per frame how often each pipeline-stage bit was set, i.e. where the
// board spends the time the host waits for.
bool g_busySample = false;
// VOODOO_PERF (with VOODOO_PCI_PROFILE, B70+ bitstreams): read the 16 perf
// counters at 0x280-0x2e4 after each swap and print the per-frame deltas.
bool g_perf = false;
uint32_t g_lastPerf[32];
uint32_t g_lastDdr[8];
uint64_t g_busyN = 0, g_busyCnt[32];
const char *const kBusyNames[32] = {
    "setupV", "raster", "tmuInV", "tmuBusy", "fbAccBusy", "ccInV", "fogBusy", "fbAccInV",
    "wrColInV", "wrAuxInV", "ffRun", "swapWait", "lfbBusy", "ffOutV", "ffOutR", "ffWrV",
    "ffWrR", "preDitMV", "preDitMR", "preDitPV", "preDitPR", "ditOutV", "ditOutR", "ditJoinV",
    "ditJoinR", "colForkV", "colForkR", "auxForkV", "auxForkR", "wrColR", "wrAuxR", "ffAuxWr"};
uint32_t g_maxReadAddr = 0;

// VOODOO_PCI_PROFILE: per-frame accounting, reported at each swapbufferCMD.
struct FrameProfile {
  uint64_t start = 0, stallNs = 0, idleNs = 0, readNs = 0, writeNs = 0;
  uint64_t regWrites = 0, lfbWrites = 0, texWrites = 0, tris = 0, fastfills = 0;
};
bool g_profile = false;
uint64_t g_minFrameNs = 0, g_lastSwapNs = 0;
uint64_t g_writeGapNs = 0, g_lastWriteNs = 0;
FrameProfile g_frame;
uint64_t g_frameNo = 0;
uint32_t g_lastPixIn = 0, g_lastPixOut = 0;

void logOp(const char *fmt, ...) {
  if (g_logFd < 0) return;
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  if (write(g_logFd, buf, n) == n && g_logSync) fdatasync(g_logFd);
}

// SST-1 status register (Voodoo address 0x000).
constexpr uint32_t kSstBusy = 1u << 9;
constexpr uint32_t kPciFifoFreeMask = 0x3fu;
// Flow-control budget from a status word. Bitstreams with
// Config.statusRealFifoFree report the PCI FIFO's real free count in the
// memory-FIFO field (bits 12-27, hard-wired 0xffff otherwise): one status
// read then covers up to ~1000 writes instead of 59.
constexpr uint32_t kMemFifoShift = 12, kMemFifoMask = 0xffffu << kMemFifoShift;
uint32_t g_budgetCap = 1024; // VOODOO_PCI_BUDGET_CAP
inline uint32_t budgetFrom(uint32_t status) {
  uint32_t mem = (status & kMemFifoMask) >> kMemFifoShift;
  uint32_t freeEntries = (mem != 0xffffu) ? (mem > g_budgetCap ? g_budgetCap : mem) : (status & kPciFifoFreeMask);
  return freeEntries > 4 ? freeEntries - 4 : 0;
}
// What Glide sees: the memory-FIFO field as it expects it (no memory FIFO).
inline uint32_t statusForGlide(uint32_t status) {
  return status == 0xffffffffu ? status : (status | kMemFifoMask);
}

uint64_t nowNs() {
  timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return uint64_t(t.tv_sec) * 1000000000ull + t.tv_nsec;
}

void logOp(const char *fmt, ...);

// Status as last read by the writer (flow control), and swaps queued in the
// ring vs. already written to the card: a status read while writes are
// queued is answered from these instead of draining the ring.
std::atomic<uint32_t> g_lastStatus{0};
std::atomic<uint32_t> g_swapsQueued{0}, g_swapsWritten{0};

// Sort buffer (default on; VOODOO_PCI_NOSORT=1 off): triangle parameter writes (0x008-0x07c and the float
// aliases 0x088-0x0fc, any chip) are held until the next other access and
// then written in ascending address order, so write-combining merges them
// into a few TLPs (Glide writes start / dX / dY per parameter, which broke
// the ascending run and fenced on almost every write). A register already
// held under another chip address flushes first (broadcast vs. per-TMU
// writes keep their order); the same address twice keeps the later value.
bool g_sort = false;
struct SOp { uint32_t addr, data; };
SOp g_sbuf[64];
int g_sn = 0;
uint64_t g_sortFlushes = 0, g_sortOps = 0;
bool sortable(uint32_t a) {
  uint32_t reg = a & 0x3fcu;
  return a < 0x4000u && ((reg >= 0x008 && reg < 0x080) || (reg >= 0x088 && reg < 0x100));
}
void sortFlush() {
  if (!g_sn) return;
  std::stable_sort(g_sbuf, g_sbuf + g_sn, [](const SOp &x, const SOp &y) { return x.addr < y.addr; });
  for (int i = 0; i < g_sn; i++) g_bar.write32(g_sbuf[i].addr, g_sbuf[i].data);
  g_sortFlushes++;
  g_sortOps += g_sn;
  g_sn = 0;
}
void sortWrite(uint32_t addr, uint32_t data) {
  if (!g_sort || !sortable(addr)) { sortFlush(); g_bar.write32(addr, data); return; }
  for (int i = 0; i < g_sn; i++) {
    if (g_sbuf[i].addr == addr) { g_sbuf[i].data = data; return; }
    if ((g_sbuf[i].addr & 0x3fcu) == (addr & 0x3fcu)) { sortFlush(); break; }
  }
  if (g_sn == 64) sortFlush();
  g_sbuf[g_sn++] = SOp{addr, data};
}

uint32_t timedRead(uint32_t addr) {
  sortFlush();
  uint64_t t0 = nowNs();
  uint32_t v = g_bar.read32(addr);
  if (addr == 0 && v != 0xffffffffu) g_lastStatus.store(v, std::memory_order_relaxed);
  uint64_t dt = nowNs() - t0;
  if (dt > g_maxReadNs) {
    g_maxReadNs = dt;
    g_maxReadAddr = addr;
  }
  if (dt > 20000) {
    g_slowReads++;
    if (g_slowReads <= 50) fprintf(stderr, "[pcie] slow read %06x: %llu us\n", addr, (unsigned long long)(dt / 1000));
    logOp("slow r32 %06x %llu us\n", addr, (unsigned long long)(dt / 1000));
  }
  return v;
}

bool checkBridge(const char *where) {
  if (g_dry) return true;
  // All-ones = read timed out while the core was busy; Bar::wedged() retries.
  if (g_bar.wedged()) {
    sortFlush();
    uint32_t s = g_bar.status();
    fprintf(stderr, "[pcie] %s: host bridge wedged or gone (%s)\n", where, pci_bar::statusText(s));
    return false;
  }
  return true;
}
}  // namespace

static void doWrite32(uint32_t addr, uint32_t data);
static void doWrite16(uint32_t addr, uint16_t data);

// --- VOODOO_PCI_THREAD: writer thread ------------------------------------
// Single producer (the game thread) / single consumer (the writer). Every
// other harness entry point first waits until the ring is empty and the
// writer has finished its write (drainWrites), so the BAR, the window
// register and the flow-control budget are only ever used by one thread at
// a time, in program order.
namespace {
struct QOp { uint32_t addr, data; uint32_t wide; };
constexpr uint32_t kQSize = 1u << 20;
QOp *g_q = nullptr;
// head: next write; tail: next read. Each on its own cache line, and each
// side keeps a copy of the other's index, reloaded only when the ring looks
// full / empty: with head, tail and busy on one line it moved between the
// two cores on every write (about the cost of the MMIO write itself).
alignas(64) std::atomic<uint32_t> g_qHead{0};
alignas(64) std::atomic<uint32_t> g_qTail{0};
alignas(64) std::atomic<bool> g_qBusy{false};
alignas(64) std::atomic<bool> g_qStop{false}, g_writerDone{false};
uint32_t g_tailSeen = 0; // producer's copy of g_qTail
// Heap-held and never destroyed: a joinable std::thread destroyed at exit
// terminates the process (seen when GLQuake quits on SIGTERM).
std::thread *g_writer = nullptr;
std::thread::id g_writerId;
bool g_threaded = false;

void writerLoop() {
  g_writerId = std::this_thread::get_id();
  struct Done { ~Done() { g_writerDone.store(true, std::memory_order_release); } } done;
  uint32_t spins = 0;
  uint32_t headSeen = 0;
  for (;;) {
    uint32_t t = g_qTail.load(std::memory_order_relaxed);
    if (t == headSeen) headSeen = g_qHead.load(std::memory_order_acquire);
    if (t == headSeen) {
      if (g_qBusy.load(std::memory_order_relaxed)) {
        sortFlush();
        g_bar.fenceWc();  // flush this core's write-combining buffers
        g_qBusy.store(false, std::memory_order_release);
      }
      if (g_qStop.load(std::memory_order_acquire)) return;
      if (++spins > 4096) sched_yield();
      continue;
    }
    spins = 0;
    g_qBusy.store(true, std::memory_order_relaxed);
    const QOp &op = g_q[t & (kQSize - 1)];
    if (op.wide) doWrite32(op.addr, op.data); else doWrite16(op.addr, (uint16_t)op.data);
    if (op.wide && op.addr < 0x400000 && (op.addr & 0x3fc) == 0x128) g_swapsWritten.fetch_add(1, std::memory_order_release);
    g_qTail.store(t + 1, std::memory_order_release);
  }
}

uint64_t g_drainNs = 0, g_drainCalls = 0, g_qSwaps = 0, g_qMax = 0;
uint32_t g_rdAddr[8]; uint64_t g_rdCnt[8];
void drainWrites() {
  // The writer itself reaches here through a swap's profile report
  // (reportFrame -> sim_idle_wait): its queue drains as it returns.
  if (!g_threaded || std::this_thread::get_id() == g_writerId) return;
  g_drainCalls++;
  uint64_t t0 = nowNs();
  struct Acc { uint64_t t0; ~Acc() { g_drainNs += nowNs() - t0; } } acc{t0};
  while (g_qTail.load(std::memory_order_acquire) != g_qHead.load(std::memory_order_relaxed) ||
         g_qBusy.load(std::memory_order_acquire)) {
    _mm_pause_compat();
  }
}

// Stop the writer once its queue is written, waiting at most `ms`; it is
// detached if it does not finish (a wedged card), so exit never hangs here.
void stopWriter(int ms) {
  if (!g_threaded || !g_writer) return;
  g_qStop.store(true, std::memory_order_release);
  for (int i = 0; i < ms * 10 && !g_writerDone.load(std::memory_order_acquire); i++) usleep(100);
  if (g_writerDone.load(std::memory_order_acquire)) g_writer->join(); else g_writer->detach();
  g_threaded = false;
}

void enqueue(uint32_t addr, uint32_t data, bool wide) {
  uint32_t h = g_qHead.load(std::memory_order_relaxed);
  while (h - g_tailSeen >= kQSize) {
    g_tailSeen = g_qTail.load(std::memory_order_acquire);
    if (h - g_tailSeen >= kQSize) sched_yield();
  }
  g_q[h & (kQSize - 1)] = QOp{addr, data, wide ? 1u : 0u};
  if (wide && addr < 0x400000 && (addr & 0x3fc) == 0x128) g_swapsQueued.fetch_add(1, std::memory_order_relaxed);
  uint32_t depth = h - g_tailSeen; // upper bound (stale tail copy)
  if (depth > g_qMax) g_qMax = depth;
  if (wide && addr < 0x400000 && (addr & 0x3fc) == 0x128 && ++g_qSwaps % 100 == 0) {
    fprintf(stderr, "[pcie] thread: 100 swaps, %llu drains, %.1f ms waiting, max depth %llu\n",
            (unsigned long long)g_drainCalls, g_drainNs / 1e6, (unsigned long long)g_qMax);
    for (int i = 0; i < 8 && g_rdCnt[i]; i++) fprintf(stderr, "[pcie]   read %06x x%llu\n", g_rdAddr[i], (unsigned long long)g_rdCnt[i]);
    memset(g_rdCnt, 0, sizeof g_rdCnt);
    g_drainCalls = 0; g_drainNs = 0; g_qMax = 0;
  }
  // (No busy store here: drainWrites sees the op through head != tail, and
  // the writer sets busy itself while it writes.)
  g_qHead.store(h + 1, std::memory_order_release);
}
}  // namespace

extern "C" int sim_init(void) {
  if (g_open) return 0;
  if (const char *log = getenv("VOODOO_PCI_LOG"))
    g_logFd = open(log, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  g_dry = getenv("VOODOO_PCI_DRYRUN") != nullptr;
  g_flow = getenv("VOODOO_PCI_NOFLOW") == nullptr;
  g_logAll = getenv("VOODOO_PCI_LOG_ALL") != nullptr;
  g_logSync = getenv("VOODOO_PCI_LOG_SYNC") != nullptr;
  g_profile = getenv("VOODOO_PCI_PROFILE") != nullptr;
  // On by default since the posted BAR bridge (b205: Q2 48 -> 52, GLQuake
  // 54.2 -> 55.1); VOODOO_PCI_NOSORT=1 turns it off.
  g_sort = getenv("VOODOO_PCI_NOSORT") == nullptr;
  if (const char *cap = getenv("VOODOO_PCI_BUDGET_CAP")) g_budgetCap = (uint32_t)atoi(cap);
  g_busySample = g_profile && getenv("VOODOO_BUSY_SAMPLE") != nullptr;
  g_perf = g_profile && getenv("VOODOO_PERF") != nullptr;
  if (const char *g = getenv("VOODOO_PCI_WRITE_GAP_NS")) g_writeGapNs = strtoull(g, nullptr, 0);
  if (const char *m = getenv("VOODOO_PCI_MIN_FRAME_MS")) g_minFrameNs = strtoull(m, nullptr, 0) * 1000000ull;
  logOp("init dry=%d\n", g_dry);
  if (g_dry) {
    g_open = true;
    return 0;
  }
  const char *bdf = getenv("VOODOO_PCI_BDF");
  if (!pci_bar::findDevice(pci_bar::kDefaultVendor, pci_bar::kDefaultDevice, bdf, &g_dev)) {
    fprintf(stderr, "[pcie] no Voodoo PCIe device found (set VOODOO_PCI_BDF?)\n");
    return -1;
  }
  pci_bar::enableDevice(g_dev);  // best effort; fails harmlessly if already enabled
  if (!g_bar.open(g_dev)) {
    fprintf(stderr, "[pcie] cannot map %s/resource0 (run as root)\n", g_dev.sysfs.c_str());
    return -1;
  }
  g_bar.setFlowControl(false);  // the harness does its own flow control (above)
  // VOODOO_PCI_WC=1: write through the write-combined BAR0 mapping
  // (/dev/voodoo_bar0 from voodoo_dma.ko; needs a B173+ bitstream).
  if (getenv("VOODOO_PCI_WC") && !g_bar.openWc()) return -1;
  g_open = true;
  fprintf(stderr, "[pcie] Voodoo on %s (%04x:%04x), bridge %s\n", g_dev.bdf.c_str(),
          g_dev.vendor, g_dev.device, pci_bar::statusText(g_bar.status()));
  if (!checkBridge("init")) return -1;
  if (getenv("VOODOO_PCI_THREAD") && !g_dry) {
    g_q = new QOp[kQSize];
    g_threaded = true;
    g_writer = new std::thread(writerLoop);
    // Runs before the static destructors (registered after g_bar was
    // constructed): the writer must not touch the BAR once it is unmapped
    // (segfault on exit without this).
    atexit([] { stopWriter(200); });
    fprintf(stderr, "[pcie] writer thread on (%u-entry ring)\n", kQSize);
  }
  return 0;
}

extern "C" void sim_shutdown(void) {
  if (!g_open) return;
  if (g_threaded) stopWriter(2000);
  sortFlush();  // the writer flushed its own before going idle
  if (getenv("VOODOO_PCI_STATS"))
    fprintf(stderr, "[pcie] %llu writes, %llu reads, %llu idle polls; bridge %s\n",
            (unsigned long long)g_writes, (unsigned long long)g_reads,
            (unsigned long long)g_idle_polls, g_dry ? "dry-run" : pci_bar::statusText(g_bar.status()));
  if (getenv("VOODOO_PCI_STATS"))
    fprintf(stderr, "[pcie] flow %s: %llu budget polls; reads: max %llu us at %06x, %llu over 20 us\n",
            g_flow ? "on" : "off", (unsigned long long)g_budgetPolls,
            (unsigned long long)(g_maxReadNs / 1000), g_maxReadAddr, (unsigned long long)g_slowReads);
  logOp("shutdown\n");
  if (!g_dry) g_bar.close();
  g_open = false;
}

// Block until the PCI FIFO has room for at least one more write.
void waitForRoom() {
  if (g_writeGapNs) {
    while (nowNs() - g_lastWriteNs < g_writeGapNs) {}
    g_lastWriteNs = nowNs();
  }
  if (!g_flow || g_dry) return;
  if (g_budget == 0 && g_profile) {
    uint64_t t0 = nowNs();
    while (g_budget == 0) {
      g_budgetPolls++;
      g_budget = budgetFrom(timedRead(0));
      if (g_budget == 0 && (g_budgetPolls & 0xfff) == 0 && !checkBridge("flow control")) { g_flow = false; g_budget = 1; break; }
      if (g_busySample && g_budget == 0) {
        uint32_t b = timedRead(0x240);
        if (b != 0xffffffffu) {
          g_busyN++;
          for (int i = 0; i < 32; ++i) g_busyCnt[i] += (b >> i) & 1;
        }
      }
    }
    g_frame.stallNs += nowNs() - t0;
  }
  while (g_budget == 0) {
    g_budgetPolls++;
    // Leave a little slack for anything in flight between BAR and core.
    g_budget = budgetFrom(timedRead(0));
    // A wedged bridge reads status 0: stop instead of spinning forever.
    if (g_budget == 0 && (g_budgetPolls & 0xfff) == 0 && !checkBridge("flow control")) { g_flow = false; g_budget = 1; break; }
  }
  g_budget--;
}

// Non-status reads wait for the core to drain first so they answer quickly.
void idleBeforeRead(uint32_t addr) {
  if (!g_flow || g_dry || (addr & 0xfffffcu) == 0) return;
  struct Timer {
    uint64_t t0 = nowNs();
    ~Timer() { if (g_profile) g_frame.idleNs += nowNs() - t0; }
  } timer;
  for (int i = 0;; i++) {
    uint32_t s = timedRead(0);
    if (!(s & kSstBusy) && (s & kPciFifoFreeMask) == kPciFifoFreeMask) {
      g_budget = budgetFrom(s);
      return;
    }
    if (i > 200000) return;  // give up after a while: the read is still bounded by the BAR timeout
  }
}

void reportFrame();

void countWrite(uint32_t addr) {
  if (addr >= 0x800000) g_frame.texWrites++;
  else if (addr >= 0x400000) g_frame.lfbWrites++;
  else {
    g_frame.regWrites++;
    uint32_t r = addr & 0x3fc;
    if (r == 0x080 || r == 0x100) g_frame.tris++;
    if (r == 0x124) g_frame.fastfills++;
  }
}

// VOODOO_PCI_MIN_FRAME_MS: hold the swap until the frame period has passed.
void frameCap() {
  if (!g_minFrameNs) return;
  uint64_t now = nowNs();
  if (g_lastSwapNs && now - g_lastSwapNs < g_minFrameNs) {
    uint64_t left = g_minFrameNs - (now - g_lastSwapNs);
    timespec ts{time_t(left / 1000000000ull), long(left % 1000000000ull)};
    nanosleep(&ts, nullptr);
  }
  g_lastSwapNs = nowNs();
}

// VOODOO_REG_HIST: index = chip-select nibble (addr bits 10-13) * 256 + reg.
int g_regHist = -1;
uint64_t g_histCnt[16 * 256], g_histSame[16 * 256], g_histSwaps = 0;
uint32_t g_histLast[16 * 256];
bool g_histSeen[16 * 256];

void regHist(uint32_t addr, uint32_t data) {
  if (g_regHist < 0) g_regHist = getenv("VOODOO_REG_HIST") != nullptr;
  if (!g_regHist || addr >= 0x400000) return;
  uint32_t i = ((addr >> 10) & 0xf) * 256 + ((addr & 0x3fc) >> 2);
  g_histCnt[i]++;
  if (g_histSeen[i] && g_histLast[i] == data) g_histSame[i]++;
  g_histSeen[i] = true;
  g_histLast[i] = data;
  if ((addr & 0x3fc) != 0x128 || ++g_histSwaps % 100) return;
  fprintf(stderr, "[reghist] swaps %llu:", (unsigned long long)g_histSwaps);
  for (uint32_t j = 0; j < 16 * 256; j++)
    if (g_histCnt[j])
      fprintf(stderr, " %x:%03x=%llu/%llu", j >> 8, (j & 0xff) << 2,
              (unsigned long long)g_histCnt[j], (unsigned long long)g_histSame[j]);
  fprintf(stderr, "\n");
}

static void doWrite32(uint32_t addr, uint32_t data) {
  regHist(addr, data);
  if (addr < 0x400000 && (addr & 0x3fc) == 0x128) frameCap();
  waitForRoom();
  g_writes++;
  if (g_logAll) logOp("w32 %06x %08x\n", addr, data);
  if (g_profile) {
    countWrite(addr);
    uint64_t t0 = nowNs();
    if (!g_dry) sortWrite(addr & 0xfffffcu, data);
    g_frame.writeNs += nowNs() - t0;
    if (addr < 0x400000 && (addr & 0x3fc) == 0x128) reportFrame();
    return;
  }
  if (!g_dry) sortWrite(addr & 0xfffffcu, data);
}

static void doWrite16(uint32_t addr, uint16_t data) {
  waitForRoom();
  g_writes++;
  if (g_profile) countWrite(addr);
  if (g_logAll) logOp("w16 %06x %04x\n", addr, data);
  sortFlush();
  if (!g_dry) g_bar.write16(addr & 0xfffffeu, data);
}



extern "C" void sim_write(uint32_t addr, uint32_t data) {
  if (g_threaded) enqueue(addr, data, true); else doWrite32(addr, data);
}

extern "C" void sim_write16(uint32_t addr, uint16_t data) {
  if (g_threaded) enqueue(addr, data, false); else doWrite16(addr, data);
}

extern "C" uint32_t sim_read(uint32_t addr) {
  if (g_threaded) {
    for (int i = 0; i < 8; i++) {
      if (g_rdCnt[i] && g_rdAddr[i] == (addr & 0xfffffc)) { g_rdCnt[i]++; break; }
      if (!g_rdCnt[i]) { g_rdAddr[i] = addr & 0xfffffc; g_rdCnt[i] = 1; break; }
    }
  }
  // Status while writes are still queued: answer from the writer's last
  // status read, with the swaps still in the ring added to swapsPending and
  // the busy bits set, so Glide's swap throttling works without draining.
  if (g_threaded && (addr & 0xfffffcu) == 0 &&
      g_qTail.load(std::memory_order_acquire) != g_qHead.load(std::memory_order_relaxed)) {
    uint32_t st = g_lastStatus.load(std::memory_order_relaxed);
    uint32_t inRing = g_swapsQueued.load(std::memory_order_relaxed) - g_swapsWritten.load(std::memory_order_acquire);
    uint32_t pending = ((st >> 28) & 7) + inRing;
    if (pending > 7) pending = 7;
    return statusForGlide((st & ~(7u << 28) & ~kPciFifoFreeMask) | (pending << 28) | kSstBusy | (3u << 7) | 0x3fu);
  }
  drainWrites();
  g_reads++;
  if (g_logAll) logOp("r32 %06x\n", addr);
  if (g_dry) return (addr & 0xfffffcu) == 0 ? kPciFifoFreeMask : 0;
  idleBeforeRead(addr);
  uint64_t t0 = nowNs();
  uint32_t v = timedRead(addr & 0xfffffcu);
  if (g_profile) g_frame.readNs += nowNs() - t0;
  if ((addr & 0xfffffcu) == 0) {
    g_budget = budgetFrom(v);
    v = statusForGlide(v);
  }
  if (g_logAll) logOp("  = %08x\n", v);
  return v;
}

extern "C" uint32_t sim_idle_wait(void) {
  drainWrites();
  sortFlush();
  // Wait for the core to drain: SST busy clear and the PCI FIFO empty.
  struct Timer {
    uint64_t t0 = nowNs();
    ~Timer() { if (g_profile) g_frame.idleNs += nowNs() - t0; }
  } timer;
  auto start = std::chrono::steady_clock::now();
  for (;;) {
    g_idle_polls++;
    uint32_t s = g_dry ? kPciFifoFreeMask : timedRead(0);
    if (!(s & kSstBusy) && (s & kPciFifoFreeMask) == kPciFifoFreeMask) return statusForGlide(s);
    if ((g_idle_polls & 0x3ff) == 0) {
      if (!checkBridge("idle_wait")) return statusForGlide(s);
      if (std::chrono::steady_clock::now() - start > std::chrono::seconds(5)) {
        fprintf(stderr, "[pcie] idle_wait: still busy after 5 s (status 0x%08x)\n", s);
        return statusForGlide(s);
      }
    }
  }
}

// Called after the swapbufferCMD write: the frame's command stream is in.
// The pixel counters are read after the swap drains (time not charged to the
// frame), so the numbers cover the whole frame.
void reportFrame() {
  uint64_t now = nowNs();
  FrameProfile f = g_frame;
  if (g_dry) {
    g_frame = FrameProfile();
    return;
  }
  sim_idle_wait();
  g_frame = FrameProfile();  // the drain above is reported separately
  g_budget = kPciFifoFreeMask - 4;
  uint32_t in = g_bar.read32(0x14c) & 0xffffff, out = g_bar.read32(0x15c) & 0xffffff;
  uint32_t dIn = (in - g_lastPixIn) & 0xffffff, dOut = (out - g_lastPixOut) & 0xffffff;
  g_lastPixIn = in;
  g_lastPixOut = out;
  uint32_t perf[32], dp[32];
  if (g_perf) {
    for (int i = 0; i < 32; ++i) {
      perf[i] = g_bar.read32(0x280 + 4 * i);
      dp[i] = perf[i] - g_lastPerf[i];
      g_lastPerf[i] = perf[i];
    }
  }
  // DDR adapter counters (window 31: write 0x74 = snapshot, read 0x78-0x94;
  // B74+). The read after the write gives the snapshot time to land.
  uint32_t dd[8] = {0};
  if (g_perf) {
    const uint32_t w31 = 31u << 20;
    g_bar.write32(w31 + 0x74, 1);
    (void)g_bar.read32(w31 + 0x78);
    for (int i = 0; i < 8; ++i) {
      uint32_t v = g_bar.read32(w31 + 0x78 + 4 * i);
      dd[i] = v - g_lastDdr[i];
      g_lastDdr[i] = v;
    }
  }
  uint64_t done = nowNs();
  if (f.start && g_frameNo++ > 0) {
    auto ms = [](uint64_t ns) { return ns / 1e6; };
    uint64_t wall = now - f.start;
    uint64_t host = wall - f.stallNs - f.idleNs - f.readNs - f.writeNs;
    fprintf(stderr,
            "[prof] frame %llu: %.0f ms = stall %.0f + idle %.0f + reads %.0f + writes %.0f + host %.0f;"
            " swap drain %.0f ms; writes reg %llu lfb %llu tex %llu; tris %llu fills %llu;"
            " pixels in %u out %u\n",
            (unsigned long long)g_frameNo, ms(wall), ms(f.stallNs), ms(f.idleNs), ms(f.readNs),
            ms(f.writeNs), ms(host), ms(done - now), (unsigned long long)f.regWrites,
            (unsigned long long)f.lfbWrites, (unsigned long long)f.texWrites,
            (unsigned long long)f.tris, (unsigned long long)f.fastfills, dIn, dOut);
    if (g_perf && dp[0]) {
      auto pc = [&](int i) { return 100.0 * dp[i] / dp[0]; };
      fprintf(stderr,
              "[perf] frame %llu: %.1f Mcyc busy %.0f%%; tmuInStall %.0f%% fbAccInStall %.0f%%;"
              " fill hit %u miss %u stall %.0f%%; write stall %.0f%%;"
              " blocked colRd %.0f%% auxRd %.0f%% colWr %.0f%% auxWr %.0f%%;"
              " raster px %u overhead %.0f%% setup-bound %.0f%% pci fifo nearly full %.0f%%;"
              " tex words %u misses %u fill %.0f%% samples %u (%.2f words/sample);"
              " fb rd col wait %.0f%% rspfull %.0f%% issue-blocked %.0f%%,"
              " aux wait %.0f%% rspfull %.0f%% issue-blocked %.0f%%;"
              " held: raster %.0f%% syncWait %.0f%% texWait %.0f%% postWait %.0f%%; no triangle: fifo empty %.0f%% fifo busy %.0f%%\n",
              (unsigned long long)g_frameNo, dp[0] / 1e6, pc(13), pc(14), pc(15), dp[1], dp[2],
              pc(3), pc(4), pc(5), pc(6), pc(7), pc(8), dp[9], pc(10), pc(11), pc(12), dp[16],
              dp[17], pc(18), dp[19], dp[19] ? double(dp[16]) / dp[19] : 0.0, pc(20), pc(21), pc(22),
              pc(23), pc(24), pc(25), pc(26), pc(27), pc(28), pc(29), pc(30), pc(31));
    }
    if (g_perf && dd[0]) {
      auto pd = [&](int i) { return 100.0 * dd[i] / dd[0]; };
      fprintf(stderr,
              "[ddr] frame %llu: %.1f Mcyc; rd %u wr %u cmds (%.0f%% of cycles); rd data waits %.1f cyc;"
              " fb AR stalled %.0f%% head waits aux R %.0f%%; rd latency %.1f cyc; head waits col R %.0f%%\n",
              (unsigned long long)g_frameNo, dd[0] / 1e6, dd[1], dd[2], 100.0 * (dd[1] + dd[2]) / dd[0],
              dd[1] ? double(dd[3]) / dd[1] : 0.0, pd(4), pd(5), dd[1] ? double(dd[6]) / dd[1] : 0.0, pd(7));
    }
    if (g_busySample && g_busyN) {
      fprintf(stderr, "[busy] frame %llu: %llu samples:", (unsigned long long)g_frameNo,
              (unsigned long long)g_busyN);
      for (int i = 0; i < 32; ++i)
        if (g_busyCnt[i] * 50 >= g_busyN)  // >= 2%
          fprintf(stderr, " %s=%.0f%%", kBusyNames[i], 100.0 * g_busyCnt[i] / g_busyN);
      fprintf(stderr, "\n");
    }
  }
  g_busyN = 0;
  for (auto &c : g_busyCnt) c = 0;
  g_frame.start = nowNs();
}

// Unused by libglide2x but part of sim_harness.h; keep the link complete for
// tools that expect the full API.
extern "C" void sim_tick(int) {}
extern "C" void sim_flush_fb_cache(void) {}
