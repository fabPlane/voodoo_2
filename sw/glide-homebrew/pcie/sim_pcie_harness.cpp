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

#include <chrono>
#include <cstdarg>
#include <ctime>
#include <cstdlib>

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
// counters at 0x280-0x2cc after each swap and print the per-frame deltas.
bool g_perf = false;
uint32_t g_lastPerf[20];
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

uint64_t nowNs() {
  timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return uint64_t(t.tv_sec) * 1000000000ull + t.tv_nsec;
}

void logOp(const char *fmt, ...);

uint32_t timedRead(uint32_t addr) {
  uint64_t t0 = nowNs();
  uint32_t v = g_bar.read32(addr);
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
    uint32_t s = g_bar.status();
    fprintf(stderr, "[pcie] %s: host bridge wedged or gone (%s)\n", where, pci_bar::statusText(s));
    return false;
  }
  return true;
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
  g_open = true;
  fprintf(stderr, "[pcie] Voodoo on %s (%04x:%04x), bridge %s\n", g_dev.bdf.c_str(),
          g_dev.vendor, g_dev.device, pci_bar::statusText(g_bar.status()));
  return checkBridge("init") ? 0 : -1;
}

extern "C" void sim_shutdown(void) {
  if (!g_open) return;
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
      g_budget = timedRead(0) & kPciFifoFreeMask;
      g_budget = g_budget > 4 ? g_budget - 4 : 0;
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
    g_budget = timedRead(0) & kPciFifoFreeMask;
    // Leave a little slack for anything in flight between BAR and core.
    g_budget = g_budget > 4 ? g_budget - 4 : 0;
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
      g_budget = kPciFifoFreeMask - 4;
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

extern "C" void sim_write(uint32_t addr, uint32_t data) {
  if (addr < 0x400000 && (addr & 0x3fc) == 0x128) frameCap();
  waitForRoom();
  g_writes++;
  if (g_logAll) logOp("w32 %06x %08x\n", addr, data);
  if (g_profile) {
    countWrite(addr);
    uint64_t t0 = nowNs();
    if (!g_dry) g_bar.write32(addr & 0xfffffcu, data);
    g_frame.writeNs += nowNs() - t0;
    if (addr < 0x400000 && (addr & 0x3fc) == 0x128) reportFrame();
    return;
  }
  if (!g_dry) g_bar.write32(addr & 0xfffffcu, data);
}

extern "C" void sim_write16(uint32_t addr, uint16_t data) {
  waitForRoom();
  g_writes++;
  if (g_profile) countWrite(addr);
  if (g_logAll) logOp("w16 %06x %04x\n", addr, data);
  if (!g_dry) g_bar.write16(addr & 0xfffffeu, data);
}

extern "C" uint32_t sim_read(uint32_t addr) {
  g_reads++;
  if (g_logAll) logOp("r32 %06x\n", addr);
  if (g_dry) return (addr & 0xfffffcu) == 0 ? kPciFifoFreeMask : 0;
  idleBeforeRead(addr);
  uint64_t t0 = nowNs();
  uint32_t v = timedRead(addr & 0xfffffcu);
  if (g_profile) g_frame.readNs += nowNs() - t0;
  if ((addr & 0xfffffcu) == 0) g_budget = (v & kPciFifoFreeMask) > 4 ? (v & kPciFifoFreeMask) - 4 : 0;
  if (g_logAll) logOp("  = %08x\n", v);
  return v;
}

extern "C" uint32_t sim_idle_wait(void) {
  // Wait for the core to drain: SST busy clear and the PCI FIFO empty.
  struct Timer {
    uint64_t t0 = nowNs();
    ~Timer() { if (g_profile) g_frame.idleNs += nowNs() - t0; }
  } timer;
  auto start = std::chrono::steady_clock::now();
  for (;;) {
    g_idle_polls++;
    uint32_t s = g_dry ? kPciFifoFreeMask : timedRead(0);
    if (!(s & kSstBusy) && (s & kPciFifoFreeMask) == kPciFifoFreeMask) return s;
    if ((g_idle_polls & 0x3ff) == 0) {
      if (!checkBridge("idle_wait")) return s;
      if (std::chrono::steady_clock::now() - start > std::chrono::seconds(5)) {
        fprintf(stderr, "[pcie] idle_wait: still busy after 5 s (status 0x%08x)\n", s);
        return s;
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
  uint32_t perf[20], dp[20];
  if (g_perf) {
    for (int i = 0; i < 20; ++i) {
      perf[i] = g_bar.read32(0x280 + 4 * i);
      dp[i] = perf[i] - g_lastPerf[i];
      g_lastPerf[i] = perf[i];
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
              " cmds colRd %u auxRd %u colWr %u auxWr %u;"
              " tex words %u misses %u fill %.0f%% samples %u (%.2f words/sample)\n",
              (unsigned long long)g_frameNo, dp[0] / 1e6, pc(13), pc(14), pc(15), dp[1], dp[2],
              pc(3), pc(4), pc(5), pc(6), pc(7), pc(8), dp[9], dp[10], dp[11], dp[12], dp[16],
              dp[17], pc(18), dp[19], dp[19] ? double(dp[16]) / dp[19] : 0.0);
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
