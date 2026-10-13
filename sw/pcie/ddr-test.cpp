// DDR3 pattern test through BAR0's raw-DDR windows (B26+): fills raw DDR
// [8 MiB, 15 MiB) - above the Voodoo's framebuffer (0) and textures (4 MiB) -
// with pseudo-random words, reads them back and counts mismatches. Used to
// check DDR3 write integrity, e.g. with the DRAM ODT held low (board_top
// DDR_ODT_ENABLE = 0). Hold the board lock; nothing else may use the BAR.
//
//   sudo ./ddr-test [passes]
#include "pci_bar.h"

#include <cstdio>
#include <cstdlib>

int main(int argc, char **argv) {
  const int passes = argc > 1 ? atoi(argv[1]) : 2;
  pci_bar::Device dev;
  if (!pci_bar::findDevice(pci_bar::kDefaultVendor, pci_bar::kDefaultDevice, nullptr, &dev)) {
    fprintf(stderr, "dock not found\n");
    return 1;
  }
  pci_bar::Bar bar;
  if (!bar.open(dev)) {
    fprintf(stderr, "cannot map BAR0 (run as root)\n");
    return 1;
  }
  const uint32_t lo = pci_bar::kRawBase + (8u << 20), hi = pci_bar::kRawBase + (15u << 20);  // window 31 is board registers, not DDR
  auto pattern = [](uint32_t a, int pass) {
    uint32_t x = a * 2654435761u ^ (0x9e3779b9u * (pass + 1));
    x ^= x >> 15;
    return pass & 1 ? ~x : x;
  };
  long errors = 0;
  for (int pass = 0; pass < passes; pass++) {
    for (uint32_t a = lo; a < hi; a += 4) bar.write32(a, pattern(a, pass));
    long passErrors = 0;
    for (uint32_t a = lo; a < hi; a += 4) {
      uint32_t got = bar.read32(a), exp = pattern(a, pass);
      if (got != exp && ++passErrors <= 8)
        fprintf(stderr, "  raw 0x%06x: got %08x expected %08x\n", a - pci_bar::kRawBase, got, exp);
    }
    printf("pass %d: %ld mismatches in %u words\n", pass, passErrors, (hi - lo) / 4);
    errors += passErrors;
  }
  printf("%s: %ld mismatches\n", errors ? "FAIL" : "PASS", errors);
  return errors ? 1 : 0;
}
