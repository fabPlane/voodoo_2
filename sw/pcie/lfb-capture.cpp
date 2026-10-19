// Read a Voodoo colour buffer back over PCIe through the linear frame buffer
// and write it as a binary PPM.
//
//   sudo ./lfb-capture out.ppm [front|back] [width height]
//
// LFB addressing on SST-1 is (y << 11) | (x << 1) for 16-bit pixels; a 32-bit
// read returns pixels x (low half) and x+1 (high half). lfbMode (0x114)
// bits 7:6 select the read buffer (0 front, 1 back); it is restored after.
#include "pci_bar.h"

#include <cstdlib>
#include <vector>

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s out.ppm [front|back] [width height]\n", argv[0]);
    return 2;
  }
  const char *out = argv[1];
  const bool back = argc > 2 && std::string(argv[2]) == "back";
  const unsigned w = argc > 4 ? strtoul(argv[3], nullptr, 0) : 640;
  const unsigned h = argc > 4 ? strtoul(argv[4], nullptr, 0) : 480;

  pci_bar::Device dev;
  if (!pci_bar::findDevice(pci_bar::kDefaultVendor, pci_bar::kDefaultDevice, getenv("VOODOO_PCI_BDF"), &dev)) {
    fprintf(stderr, "no device\n");
    return 1;
  }
  pci_bar::enableDevice(dev);
  pci_bar::Bar bar;
  if (!bar.open(dev)) return 1;

  constexpr uint32_t kLfbMode = 0x114;
  const uint32_t saved = bar.read32(kLfbMode);
  bar.write32(kLfbMode, (saved & ~0xc0u) | ((back ? 1u : 0u) << 6));

  std::vector<uint8_t> rgb(static_cast<size_t>(w) * h * 3);
  for (unsigned y = 0; y < h; ++y) {
    for (unsigned x = 0; x < w; x += 2) {
      uint32_t v = bar.read32(pci_bar::kLfbBase + (y << 11) + (x << 1));
      for (unsigned k = 0; k < 2 && x + k < w; ++k) {
        uint16_t p = static_cast<uint16_t>(v >> (16 * k));
        uint8_t *d = &rgb[(static_cast<size_t>(y) * w + x + k) * 3];
        d[0] = static_cast<uint8_t>(((p >> 11) & 0x1f) * 255 / 31);
        d[1] = static_cast<uint8_t>(((p >> 5) & 0x3f) * 255 / 63);
        d[2] = static_cast<uint8_t>((p & 0x1f) * 255 / 31);
      }
    }
  }
  bar.write32(kLfbMode, saved);

  FILE *f = fopen(out, "wb");
  if (!f) {
    perror(out);
    return 1;
  }
  fprintf(f, "P6\n%u %u\n255\n", w, h);
  fwrite(rgb.data(), 1, rgb.size(), f);
  fclose(f);
  fprintf(stderr, "wrote %s (%s buffer, %ux%u), bridge %s\n", out, back ? "back" : "front", w, h,
          pci_bar::statusText(bar.status()));
  return 0;
}
