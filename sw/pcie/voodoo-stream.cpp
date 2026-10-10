// Live HDMI scanout from the Tang dock over PCIe DMA (FrameDmaWriter, B27+).
//
//   sudo insmod voodoo_dma/voodoo_dma.ko
//   sudo ./voodoo-stream [--skip N] [--frames N] > frames.rgb565   (640x480 rgb565le)
//   sudo ./voodoo-stream --stop          disable the writer
//   sudo ./voodoo-stream --counters      print the writer's counters
//
// Programs BAR0 window 31 once at start (before the game runs: the window
// register is shared with whatever else drives the BAR), then only reads the
// DMA buffer. Each new frame is copied to stdout; frames the reader misses
// are skipped, counts go to stderr once a second.
#include "pci_bar.h"

#include <chrono>
#include <csignal>
#include <cstdlib>
#include <thread>
#include <vector>

namespace {
constexpr uint32_t kLocalWindow = 31;
constexpr uint32_t kMagic = 0x444d4131;
constexpr uint32_t kWidth = 640, kHeight = 480;
constexpr uint32_t kFrameBytes = kWidth * kHeight * 2;
constexpr uint32_t kFrameWords = kFrameBytes / 32;

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
  bool stop = false, counters = false;
  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a == "--skip" && i + 1 < argc) skip = strtoul(argv[++i], nullptr, 0);
    else if (a == "--frames" && i + 1 < argc) maxFrames = strtol(argv[++i], nullptr, 0);
    else if (a == "--stop") stop = true;
    else if (a == "--counters") counters = true;
    else {
      fprintf(stderr, "usage: %s [--skip N] [--frames N] | --stop | --counters\n", argv[0]);
      return 2;
    }
  }

  pci_bar::Bar bar;
  if (!openBar(&bar)) return 1;
  if (reg(bar, kRegMagic) != kMagic) {
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
  const uint32_t slots = std::min<uint64_t>(255, (info.size - 4096) / kFrameBytes);
  const uint32_t statusOff = slots * kFrameBytes;
  auto *status = reinterpret_cast<volatile uint32_t *>(buf + statusOff);
  *status = 0;
  fprintf(stderr, "dma buffer bus %08llx, %u slots, status at +%x\n", (unsigned long long)info.bus,
          slots, statusOff);

  setReg(bar, kRegCtrl, 0);
  setReg(bar, kRegBase, uint32_t(info.bus));
  setReg(bar, kRegSlots, slots);
  setReg(bar, kRegFrameWords, kFrameWords);
  setReg(bar, kRegStride, kFrameBytes);
  setReg(bar, kRegStatusAddr, uint32_t(info.bus + statusOff));
  setReg(bar, kRegCtrl, 1 | (skip << 8));
  bar.selectWindow(0);  // leave the BAR as other users expect it
  bar.close();

  signal(SIGINT, onSignal);
  signal(SIGTERM, onSignal);
  signal(SIGPIPE, onSignal);
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
