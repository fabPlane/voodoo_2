#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

#include "pci_bar.h"
#include "trace_replay_backend.h"
#include "trace_replay_image.h"
#include "trace_replay_io.h"
#include "trace_replay_runner.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>
#include <vector>

namespace {

constexpr uint32_t kSstBusy = 1u << 9;
constexpr uint32_t kFifoMask = 0x3Fu;
constexpr uint32_t kSwapsPendingMask = 7u << 28;

struct Options {
  std::string inputPath;
  const char *bdf = nullptr;
  uint16_t vid = pci_bar::kDefaultVendor;
  uint16_t did = pci_bar::kDefaultDevice;
  bool skipState = false;
  bool skipFbState = false;
  uint32_t maxEntries = 0;
  std::string framesDir;
  uint32_t frameEvery = 0;
};

class PciReplayBackend : public TraceReplayBackend {
public:
  explicit PciReplayBackend(pci_bar::Bar *bar) : bar_(bar) {}

  bool useDe10RegisterMap() const override { return true; }

  bool writeReg32(uint32_t mappedAddr, uint32_t, uint32_t value) override {
    bar_->write32(mappedAddr & 0x3fffffu, value);
    return !wedged();
  }
  bool writeReg16(uint32_t mappedAddr, uint32_t, uint16_t value) override {
    bar_->write16(mappedAddr & 0x3fffffu, value);
    return !wedged();
  }
  bool writeFb32(uint32_t byteOffset, uint32_t value) override {
    bar_->write32(pci_bar::kLfbBase + (byteOffset & 0x3fffffu), value);
    return !wedged();
  }
  bool writeFb16(uint32_t byteOffset, uint16_t value) override {
    bar_->write16(pci_bar::kLfbBase + (byteOffset & 0x3fffffu), value);
    return !wedged();
  }
  bool writeTex32(uint32_t byteOffset, uint32_t value) override {
    bar_->write32(pci_bar::kTexBase + (byteOffset & 0x7fffffu), value);
    return !wedged();
  }
  bool writeFbBulk(uint32_t byteOffset, const uint8_t *data, uint32_t size) override {
    // Saved state is a raw memory image: write it to DDR, not through the LFB.
    return writeBulk(pci_bar::kRawBase + byteOffset, data, size);
  }
  bool writeTexBulk(uint32_t byteOffset, const uint8_t *data, uint32_t size) override {
    return writeBulk(pci_bar::kRawBase + pci_bar::kRawTexOffset + byteOffset, data, size);
  }
  bool readFbWords(uint32_t byteOffset, uint32_t *dst, uint32_t wordCount) override {
    for (uint32_t i = 0; i < wordCount; ++i) {
      dst[i] = bar_->read32(pci_bar::kRawBase + byteOffset + i * 4u);
      if ((i & 0x3fffu) == 0x3fffu && wedged()) return false;
    }
    return true;
  }
  bool idleWait() override {
    int idleCount = 0;
    int sticky = 0;
    for (int timeout = 0; timeout < 5000000; ++timeout) {
      if (wedged()) {
        fprintf(stderr, "[pci-trace] wedged during idle_wait status=0x%08x\n", bar_->status());
        return false;
      }
      uint32_t sst = bar_->read32(0x000);
      bool busy = (sst & kSstBusy) != 0;
      uint32_t fifoFree = sst & kFifoMask;
      uint32_t swaps = (sst & kSwapsPendingMask) >> 28;
      if (!busy && fifoFree == 0x3f && swaps == 0) {
        if (++idleCount >= 3) return true;
      } else {
        idleCount = 0;
      }
      if (fifoFree == 0x3f && swaps == 0) {
        if (++sticky >= 1024) {
          fprintf(stderr, "[pci-trace] WARNING: sticky-busy sst=0x%08x\n", sst);
          return true;
        }
      } else {
        sticky = 0;
      }
    }
    fprintf(stderr, "[pci-trace] WARNING: idle_wait timeout sst=0x%08x bar=%s\n",
            bar_->read32(0x000), pci_bar::statusText(bar_->status()));
    return true;
  }
  bool invalidateFbCache() override { return true; }
  bool flushFbCache() override { return true; }
  bool setSwapCount(uint32_t count) override {
    const uint32_t target = count & 0x1u;
    for (unsigned attempt = 0; attempt < 4; ++attempt) {
      const uint32_t displayed = (bar_->read32(0x000) >> 10) & 0x1u;
      if (displayed == target) return true;
      bar_->write32(0x128, 0);
      if (!idleWait()) return false;
    }
    return ((bar_->read32(0x000) >> 10) & 0x1u) == target;
  }

private:
  bool wedged() const { return bar_->wedged(); }

  bool writeBulk(uint32_t voodooAddr, const uint8_t *data, uint32_t size) {
    uint32_t off = 0;
    while (off + 4 <= size) {
      uint32_t word;
      memcpy(&word, data + off, 4);
      bar_->write32(voodooAddr + off, word);
      // Raw-DDR writes don't go through the Voodoo bridge; checking it per word
      // would also bounce the window back to 0 every time.
      if ((off & 0xffffu) == 0xfffcu && wedged()) return false;
      off += 4;
    }
    if (off < size) {
      uint32_t word = 0;
      memcpy(&word, data + off, size - off);
      bar_->write32(voodooAddr + off, word);
    }
    return !wedged();
  }

  pci_bar::Bar *bar_;
};

using Clock = std::chrono::steady_clock;

double elapsedMs(Clock::time_point start) {
  return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

void usage(const char *argv0) {
  fprintf(stderr,
          "Usage: %s <trace.bin|trace-dir> [--bdf BDF] [--vid HEX] [--did HEX] "
          "[--skip-state] [--skip-fb-state] [--max-entries N]\n"
          "       [--frames-dir DIR --frame-every N]  also save the draw buffer every N entries\n",
          argv0);
}

bool parseArgs(int argc, char **argv, Options *opts) {
  if (argc < 2) {
    usage(argv[0]);
    return false;
  }
  opts->inputPath = argv[1];
  for (int i = 2; i < argc; ++i) {
    if (strcmp(argv[i], "--bdf") == 0 && i + 1 < argc) {
      opts->bdf = argv[++i];
    } else if (strcmp(argv[i], "--vid") == 0 && i + 1 < argc) {
      opts->vid = static_cast<uint16_t>(strtoul(argv[++i], nullptr, 0));
    } else if (strcmp(argv[i], "--did") == 0 && i + 1 < argc) {
      opts->did = static_cast<uint16_t>(strtoul(argv[++i], nullptr, 0));
    } else if (strcmp(argv[i], "--skip-state") == 0) {
      opts->skipState = true;
    } else if (strcmp(argv[i], "--skip-fb-state") == 0) {
      opts->skipFbState = true;
    } else if (strcmp(argv[i], "--frames-dir") == 0 && i + 1 < argc) {
      opts->framesDir = argv[++i];
    } else if (strcmp(argv[i], "--frame-every") == 0 && i + 1 < argc) {
      opts->frameEvery = static_cast<uint32_t>(strtoul(argv[++i], nullptr, 0));
    } else if (strcmp(argv[i], "--max-entries") == 0 && i + 1 < argc) {
      opts->maxEntries = static_cast<uint32_t>(strtoul(argv[++i], nullptr, 0));
    } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
      usage(argv[0]);
      exit(0);
    } else {
      fprintf(stderr, "Unknown argument: %s\n", argv[i]);
      return false;
    }
  }
  return true;
}

}  // namespace

int main(int argc, char **argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  setvbuf(stderr, nullptr, _IONBF, 0);

  Options opts;
  if (!parseArgs(argc, argv, &opts)) return 2;

  TraceReplayFiles files;
  traceReplayResolveFiles(opts.inputPath.c_str(), &files);
  TraceReplayLoadedTrace trace;
  if (!traceReplayLoadTraceFile(files.traceFile, &trace)) {
    fprintf(stderr, "[pci-trace] ERROR: cannot read %s\n", files.traceFile.c_str());
    return 1;
  }
  fprintf(stderr, "[pci-trace] Loaded %s (%u entries)\n", files.traceFile.c_str(),
          trace.entryCount);

  pci_bar::Device dev;
  if (!pci_bar::findDevice(opts.vid, opts.did, opts.bdf, &dev)) {
    fprintf(stderr, "[pci-trace] ERROR: no PCI device %04x:%04x. Cold-plug after programming.\n",
            opts.vid, opts.did);
    free(trace.traceData);
    return 1;
  }
  pci_bar::enableDevice(dev);
  pci_bar::Bar bar;
  if (!bar.open(dev)) {
    free(trace.traceData);
    return 1;
  }
  fprintf(stderr, "[pci-trace] Mapped BAR0 of %s (%s)\n", dev.bdf.c_str(),
          pci_bar::statusText(bar.status()));

  PciReplayBackend backend(&bar);
  TraceReplayStateInfo stateInfo;
  if (!files.stateFile.empty() && !opts.skipState) {
    uint32_t stateSize = 0;
    uint8_t *stateData = traceReplayReadFile(files.stateFile.c_str(), &stateSize);
    if (!stateData || !traceReplayLoadState(backend, stateData, stateSize, opts.skipFbState, &stateInfo)) {
      fprintf(stderr, "[pci-trace] ERROR: failed to load %s\n", files.stateFile.c_str());
      free(stateData);
      free(trace.traceData);
      return 1;
    }
    free(stateData);
    fprintf(stderr, "[pci-trace] State loaded draw=0x%x disp=%ux%u\n", stateInfo.drawOffset,
            stateInfo.hDisp, stateInfo.vDisp);
  } else if (!backend.setSwapCount(0)) {
    fprintf(stderr, "[pci-trace] ERROR: failed to init swapCount\n");
    free(trace.traceData);
    return 1;
  }

  uint32_t replayEntries =
      opts.maxEntries && opts.maxEntries < trace.entryCount ? opts.maxEntries : trace.entryCount;
  auto replayStart = Clock::now();
  TraceReplayRunResult replayResult;
  // --frames-dir: after every N entries, drain the core and save the buffer being drawn,
  // so the frame can be watched building up (frame_00000.png, ...).
  const uint32_t capW = stateInfo.hDisp ? stateInfo.hDisp : 640;
  const uint32_t capH = stateInfo.vDisp ? stateInfo.vDisp : 480;
  const uint32_t capStride = stateInfo.rowWidth ? stateInfo.rowWidth / 2 : capW;
  uint8_t capLut[256];
  traceReplayInitSrgbLut(capLut);
  uint32_t framesSaved = 0;
  auto saveFrame = [&](uint32_t drawOffset) {
    std::vector<uint16_t> fb565;
    if (!traceReplayFinish(backend) ||
        !traceReplayCaptureRgb565(backend, drawOffset, capStride, capW, capH, &fb565))
      return false;
    char path[1024];
    snprintf(path, sizeof(path), "%s/frame_%05u.png", opts.framesDir.c_str(), framesSaved++);
    return traceReplayWriteRgb565Png(path, fb565.data(), static_cast<int>(capW), static_cast<int>(capH),
                                     static_cast<int>(capStride), capLut, stbi_write_png) != 0;
  };
  const bool capture = !opts.framesDir.empty() && opts.frameEvery > 0;
  if (capture && !saveFrame(stateInfo.drawOffset)) {
    fprintf(stderr, "[pci-trace] ERROR: cannot save frames to %s\n", opts.framesDir.c_str());
    free(trace.traceData);
    return 1;
  }
  bool captureOk = true;
  int64_t lastCalled = -1;
  auto onEntry = [&](uint32_t i, const voodoo_trace_entry_t &, uint32_t,
                     const TraceReplayRunResult &r, bool sawSwap) {
    // Called before (return value: run it?) and after each entry; the second call has the same i.
    const bool after = static_cast<int64_t>(i) == lastCalled;
    lastCalled = i;
    // A swap flips the draw buffer; keep showing the one that was just finished.
    if (after && capture && captureOk && ((i + 1) % opts.frameEvery == 0 || i + 1 == replayEntries))
      captureOk = saveFrame(sawSwap ? r.presentedOffset : r.currentDrawOffset);
    return true;
  };
  if (!traceReplayRunEntries(backend, trace.entries, replayEntries, stateInfo.drawOffset,
                             stateInfo.bufferOffset, &replayResult, onEntry) ||
      !captureOk) {
    fprintf(stderr, "[pci-trace] ERROR: replay failed at entry %u bar=%s\n",
            replayResult.replayedEntries, pci_bar::statusText(bar.status()));
    free(trace.traceData);
    return 1;
  }
  traceReplayFinish(backend);
  uint32_t finalStatus = bar.read32(0x000);
  uint32_t barStatus = bar.status();
  fprintf(stderr,
          "[pci-trace] Replay %.3f ms reg=%u tex=%u fb=%u swaps=%u sst=0x%08x bar=%s\n",
          elapsedMs(replayStart), replayResult.regWrites, replayResult.texWrites,
          replayResult.fbWrites, replayResult.swapWrites, finalStatus,
          pci_bar::statusText(barStatus));

  if (stateInfo.hDisp == 0 || stateInfo.vDisp == 0) {
    uint32_t dims = bar.read32(0x20c);
    stateInfo.hDisp = (dims & 0xfffu) + 1u;
    stateInfo.vDisp = (dims >> 16) & 0xfffu;
    if (stateInfo.hDisp <= 1 || stateInfo.vDisp == 0) {
      stateInfo.hDisp = 640;
      stateInfo.vDisp = 480;
    }
  }
  if (stateInfo.rowWidth == 0) stateInfo.rowWidth = stateInfo.hDisp * 2;
  if (!replayResult.havePresentedOffset) replayResult.presentedOffset = replayResult.currentDrawOffset;

  if (stateInfo.rowWidth && stateInfo.hDisp && stateInfo.vDisp) {
    std::vector<uint16_t> fb565;
    if (traceReplayCaptureRgb565(backend, replayResult.presentedOffset, stateInfo.rowWidth / 2,
                                 stateInfo.hDisp, stateInfo.vDisp, &fb565)) {
      uint8_t srgbLut[256];
      traceReplayInitSrgbLut(srgbLut);
      std::string pngPath = opts.inputPath + "/trace_board_fb.png";
      if (traceReplayIsDirectory(opts.inputPath.c_str()) &&
          traceReplayWriteRgb565Png(pngPath.c_str(), fb565.data(), static_cast<int>(stateInfo.hDisp),
                                    static_cast<int>(stateInfo.vDisp),
                                    static_cast<int>(stateInfo.rowWidth / 2), srgbLut,
                                    stbi_write_png)) {
        fprintf(stderr, "[pci-trace] Wrote %s\n", pngPath.c_str());
      }
    }
  }

  free(trace.traceData);
  return (barStatus & pci_bar::kStatusWedged) ? 3 : 0;
}
