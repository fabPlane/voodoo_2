#include "pci_bar.h"

#include <cstdio>

int main() {
  int failed = 0;
  auto expect = [&](bool ok, const char *msg) {
    if (!ok) {
      fprintf(stderr, "FAIL %s\n", msg);
      failed = 1;
    }
  };

  expect(pci_bar::windowIndex(0x000100) == 0, "reg window");
  expect(pci_bar::windowOffset(0x000100) == 0x100, "reg offset");
  expect(pci_bar::windowIndex(pci_bar::kLfbBase + 0x20) == 4, "lfb window");
  expect(pci_bar::windowOffset(pci_bar::kLfbBase + 0x20) == 0x20, "lfb offset");
  expect(pci_bar::windowIndex(pci_bar::kTexBase + 0x1000) == 8, "tex window");
  expect(pci_bar::windowOffset(0x4fffff) == 0xfffff, "top of window");
  expect(pci_bar::windowIndex(0x4fffff) == 4, "top of lfb megabyte");
  expect(pci_bar::kWindowReg != 0 && pci_bar::kStatusReg == 0x254, "handoff regs");
  expect((pci_bar::kLfbBase + pci_bar::kBarBytes) == (5u << 20), "1MiB window size");

  if (failed) return 1;
  printf("pci_window_test ok\n");
  return 0;
}
