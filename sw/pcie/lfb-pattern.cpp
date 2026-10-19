// LFB write/read-back test over PCIe: fills the back buffer through the
// linear frame buffer with a position-encoding RGB565 pattern, reads it back
// and reports mismatches (first few with coordinates).
//
//   sudo ./lfb-pattern [width height]
#include "pci_bar.h"

#include <cstdlib>

static uint16_t pat(unsigned x, unsigned y) { return static_cast<uint16_t>((x * 7 + y * 131) ^ (y << 5)); }

int main(int argc, char **argv) {
  const unsigned w = argc > 2 ? strtoul(argv[1], nullptr, 0) : 640;
  const unsigned h = argc > 2 ? strtoul(argv[2], nullptr, 0) : 480;
  pci_bar::Device dev;
  if (!pci_bar::findDevice(pci_bar::kDefaultVendor, pci_bar::kDefaultDevice, getenv("VOODOO_PCI_BDF"), &dev)) return 1;
  pci_bar::enableDevice(dev);
  pci_bar::Bar bar;
  if (!bar.open(dev)) return 1;

  constexpr uint32_t kLfbMode = 0x114;
  const uint32_t saved = bar.read32(kLfbMode);
  // write format 0 (RGB565), write buffer = back (bits 5:4 = 1), read buffer = back (7:6 = 1)
  bar.write32(kLfbMode, (1u << 4) | (1u << 6));
  const bool readOnly = argc > 3 && std::string(argv[3]) == "readonly";
  for (unsigned y = 0; y < (readOnly ? 0u : h); ++y)
    for (unsigned x = 0; x < w; x += 2)
      bar.write32(pci_bar::kLfbBase + (y << 11) + (x << 1),
                  static_cast<uint32_t>(pat(x, y)) | (static_cast<uint32_t>(pat(x + 1, y)) << 16));
  // Let the write buffer drain: a register read goes behind the FIFO.
  (void)bar.read32(0x000);
  unsigned bad = 0, shown = 0;
  for (unsigned y = 0; y < h; ++y)
    for (unsigned x = 0; x < w; x += 2) {
      uint32_t v = bar.read32(pci_bar::kLfbBase + (y << 11) + (x << 1));
      uint32_t e = static_cast<uint32_t>(pat(x, y)) | (static_cast<uint32_t>(pat(x + 1, y)) << 16);
      if ((v >> 16) != (e >> 16)) {
        ++bad;
        if (shown++ < 100000) printf("mismatch x=%u y=%u got %08x exp %08x xor %08x\n", x, y, v, e, v ^ e);
      }
    }
  bar.write32(kLfbMode, saved);
  printf("%u of %u words mismatched (high pixel); bridge %s\n", bad, w * h / 2, pci_bar::statusText(bar.status()));
  return bad ? 1 : 0;
}
