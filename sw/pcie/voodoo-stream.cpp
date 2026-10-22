// Live HDMI scanout from the Tang dock over PCIe DMA (FrameDmaWriter, B27+).
//
//   sudo insmod voodoo_dma/voodoo_dma.ko
//   sudo ./voodoo-stream [--skip N] [--frames N] [--size WxH] > frames.rgb565
//   (rgb565le; the scanout is 720x480 CEA timing, the 640x480 Voodoo image inside it)
//   sudo ./voodoo-stream --setup-only    program the writer and exit (hold the board lock)
//   sudo ./voodoo-stream --attach ...    read frames without touching the BAR, so a
//                                        game can own the board meanwhile
//   sudo ./voodoo-stream --stop          disable the writer
//   sudo ./voodoo-stream --counters      print the writer's counters
//   --adc-log FILE  append the board's supply / temperature monitor (sent
//                   after each frame's status word, B45+) every 10 frames,
//                   synced to disk so the readings before a crash survive
//
// Programs BAR0 window 31 once at start (before the game runs: the window
// register is shared with whatever else drives the BAR), then only reads the
// DMA buffer. Each new frame is copied to stdout; frames the reader misses
// are skipped, counts go to stderr once a second.
#include "pci_bar.h"

#include <chrono>
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <fcntl.h>
#include <unistd.h>
#include <thread>
#include <vector>

namespace {
constexpr uint32_t kLocalWindow = 31;
constexpr uint32_t kMagic = 0x444d4131;

enum Reg : uint32_t {
  kRegMagic = 0x00, kRegCtrl = 0x04, kRegBase = 0x08, kRegSlots = 0x0c, kRegFrameWords = 0x10,
  kRegStatusAddr = 0x14, kRegStride = 0x18, kRegFrames = 0x20, kRegOverflows = 0x24,
  kRegResyncs = 0x28, kRegTlps = 0x2c,
};

struct DmaInfo {
  uint64_t bus;
  uint64_t size;
};

volatile sig_atomic_t gStop = 0;
void onSignal(int) { gStop = 1; }

bool openBar(pci_bar::Bar *bar) {
  pci_bar::Device dev;
  if (!pci_bar::findDevice(pci_bar::kSipeedVendor, pci_bar::kSipeedDevice, nullptr, &dev)) {
    fprintf(stderr, "dock not found\n");
    return false;
  }
  pci_bar::enableDevice(dev);
  return bar->open(dev);
}

uint32_t reg(pci_bar::Bar &bar, uint32_t r) {
  bar.selectWindow(kLocalWindow);
  return bar.readLocal32(r);
}
void setReg(pci_bar::Bar &bar, uint32_t r, uint32_t v) {
  bar.selectWindow(kLocalWindow);
  bar.writeLocal32(r, v);
}

void printCounters(pci_bar::Bar &bar) {
  fprintf(stderr, "ctrl %08x base %08x slots %u frames %u overflows %u resyncs %u tlps %u\n",
          reg(bar, kRegCtrl), reg(bar, kRegBase), reg(bar, kRegSlots), reg(bar, kRegFrames),
          reg(bar, kRegOverflows), reg(bar, kRegResyncs), reg(bar, kRegTlps));
}
}  // namespace

int main(int argc, char **argv) {
  unsigned skip = 0;
  long maxFrames = -1;
  bool stop = false, counters = false, setupOnly = false, attach = false;
  uint32_t width = 720, height = 480;
  const char *adcLog = nullptr;
  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--skip" && i + 1 < argc) skip = strtoul(argv[++i], nullptr, 0);
    else if (a == "--frames" && i + 1 < argc) maxFrames = strtol(argv[++i], nullptr, 0);
    else if (a == "--size" && i + 1 < argc) sscanf(argv[++i], "%ux%u", &width, &height);
    else if (a == "--setup-only") setupOnly = true;
    else if (a == "--attach") attach = true;
    else if (a == "--adc-log" && i + 1 < argc) adcLog = argv[++i];
    else if (a == "--stop") stop = true;
    else if (a == "--counters") counters = true;
    else {
      fprintf(stderr, "usage: %s [--skip N] [--frames N] [--size WxH] [--setup-only|--attach] [--adc-log FILE] | --stop | --counters\n", argv[0]);
      return 2;
    }
  }

  pci_bar::Bar bar;
  if (!attach && !openBar(&bar)) return 1;
  if (!attach && reg(bar, kRegMagic) != kMagic) {
    fprintf(stderr, "no frame DMA in this bitstream (magic %08x)\n", reg(bar, kRegMagic));
    return 1;
  }
  if (stop || counters) {
    if (stop) setReg(bar, kRegCtrl, 0);
    printCounters(bar);
    bar.selectWindow(0);
    return 0;
  }

  int fd = open("/dev/voodoo_dma", O_RDWR);
  if (fd < 0) {
    perror("/dev/voodoo_dma (insmod voodoo_dma.ko)");
    return 1;
  }
  DmaInfo info{};
  if (read(fd, &info, sizeof(info)) != sizeof(info)) {
    perror("read dma info");
    return 1;
  }
  if (info.bus + info.size > (1ull << 32)) {
    fprintf(stderr, "DMA buffer above 4 GiB (bus %llx)\n", (unsigned long long)info.bus);
    return 1;
  }
  auto *buf = static_cast<volatile uint8_t *>(
      mmap(nullptr, info.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0));
  if (buf == MAP_FAILED) {
    perror("mmap dma");
    return 1;
  }
  const uint32_t kFrameBytes = width * height * 2;
  if (kFrameBytes % 256) {
    fprintf(stderr, "frame size must be a multiple of 256 bytes\n");
    return 2;
  }
  const uint32_t kFrameWords = kFrameBytes / 32;
  const uint32_t slots = std::min<uint64_t>(255, (info.size - 4096) / kFrameBytes);
  const uint32_t statusOff = slots * kFrameBytes;
  auto *status = reinterpret_cast<volatile uint32_t *>(buf + statusOff);
  fprintf(stderr, "dma buffer bus %08llx, %u slots, status at +%x\n", (unsigned long long)info.bus,
          slots, statusOff);

  if (!attach) {
  *status = 0;
  setReg(bar, kRegCtrl, 0);
  // The scanout tap only stops at the next vsync: if the writer is enabled
  // again mid-frame it starts slot 0 with the rest of that frame, and when
  // that is not a whole TLP the start-of-frame marker never lands where the
  // writer checks it, so every frame stays rotated. Wait two frames.
  std::this_thread::sleep_for(std::chrono::milliseconds(40));
  setReg(bar, kRegBase, uint32_t(info.bus));
  setReg(bar, kRegSlots, slots);
  setReg(bar, kRegFrameWords, kFrameWords);
  setReg(bar, kRegStride, kFrameBytes);
  setReg(bar, kRegStatusAddr, uint32_t(info.bus + statusOff));
  setReg(bar, kRegCtrl, 1 | (skip << 8));
  bar.selectWindow(0);  // leave the BAR as other users expect it
  bar.close();
  }
  if (setupOnly) return 0;

  signal(SIGINT, onSignal);
  signal(SIGTERM, onSignal);
  signal(SIGPIPE, onSignal);
  int adcFd = adcLog ? open(adcLog, O_WRONLY | O_CREAT | O_APPEND, 0644) : -1;
  std::vector<uint8_t> frame(kFrameBytes);
  uint32_t lastSeq = 0;
  long written = 0, missed = 0, secWritten = 0;
  auto lastReport = std::chrono::steady_clock::now();
  auto lastFrame = lastReport;
  while (!gStop && (maxFrames < 0 || written < maxFrames)) {
    uint32_t st = *status;
    uint32_t seq = st >> 8, slot = st & 0xff;
    auto now = std::chrono::steady_clock::now();
    if (seq != lastSeq && slot < slots) {
      if (lastSeq && seq - lastSeq > 1) missed += (seq - lastSeq - 1) & 0xffffff;
      lastSeq = seq;
      if (adcFd >= 0 && seq % 10 == 0) {
        // status[1]: vcc | temp << 16, status[2]: vccMin | tempMax << 16,
        // status[3]: samples. VCC = /1024 V, temperature = /4 degC.
        uint32_t now1 = status[1], ext = status[2], n = status[3];
        auto lo = [](uint32_t v) { return int16_t(v & 0xffff); };
        auto hi = [](uint32_t v) { return int16_t(v >> 16); };
        timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        char line[160];
        int len = snprintf(line, sizeof line,
                           "%ld.%03ld seq %u vcc %.4f V temp %.2f C vccMin %.4f V tempMax %.2f C samples %u\n",
                           long(ts.tv_sec), long(ts.tv_nsec / 1000000), seq, lo(now1) / 1024.0,
                           hi(now1) / 4.0, lo(ext) / 1024.0, hi(ext) / 4.0, n);
        if (write(adcFd, line, len) == len) fdatasync(adcFd);
      }
      memcpy(frame.data(), const_cast<uint8_t *>(buf + size_t(slot) * kFrameBytes), kFrameBytes);
      if (fwrite(frame.data(), 1, kFrameBytes, stdout) != kFrameBytes) break;
      fflush(stdout);
      written++;
      secWritten++;
      lastFrame = now;
    } else {
      std::this_thread::sleep_for(std::chrono::microseconds(500));
    }
    if (now - lastReport >= std::chrono::seconds(1)) {
      double idle = std::chrono::duration<double>(now - lastFrame).count();
      fprintf(stderr, "[voodoo-stream] %ld fps, %ld frames, %ld missed%s\n", secWritten, written,
              missed, idle > 2 ? " (no frames: writer stalled?)" : "");
      secWritten = 0;
      lastReport = now;
    }
  }
  // Leave the writer running: stopping it needs the BAR window, which a game
  // may be using. Use --stop when nothing else drives the board.
  fprintf(stderr, "[voodoo-stream] done: %ld frames, %ld missed\n", written, missed);
  return 0;
}
