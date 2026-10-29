// wbench: host -> card register write throughput over BAR0 (root; hold the
// board lock; no game running). Writes vertex register 0x008 (vertexAx,
// harmless without a triangle command) N times per mode and prints ns/write:
//   uc       uncached writes, harness-style flow control (status every ~59)
//   uc-nofc  uncached writes, no flow control
//   wc-line  write-combined, 16 ascending words per 64-byte line, fence per line
//   wc-each  write-combined, fence after every write
// plus the cost of one status read.
#include "pci_bar.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static double nsNow() {
  return std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 200000;
  pci_bar::Device dev;
  if (!pci_bar::findDevice(0x22c2, 0x1100, nullptr, &dev) &&
      !pci_bar::findDevice(pci_bar::kDefaultVendor, pci_bar::kDefaultDevice, nullptr, &dev)) {
    fprintf(stderr, "dock not found\n");
    return 1;
  }
  pci_bar::Bar bar;
  if (!bar.open(dev)) return 1;

  const char *only = argc > 2 ? argv[2] : nullptr; // run just this mode
  auto run = [&](const char *name, auto body) {
    if (only && strcmp(only, name) != 0) return;
    // let the card's FIFO drain between modes
    for (int i = 0; i < 1000; i++) (void)bar.status();
    double t0 = nsNow();
    body();
    double dt = nsNow() - t0;
    printf("%-8s %8.1f ns/write  (%d writes, %.1f ms)\n", name, dt / n, n, dt / 1e6);
  };

  bar.setFlowControl(true);
  run("uc", [&] { for (int i = 0; i < n; i++) bar.write32(0x008, i); });
  bar.setFlowControl(false);
  run("uc-nofc", [&] {
    for (int i = 0; i < n; i++) {
      bar.write32(0x008, i);
      if ((i & 31) == 31) (void)bar.status(); // keep the FIFO from overflowing
    }
  });
  bool wc = bar.openWc(); // after the uncached runs: write32 then takes the WC path
  if (wc) {
    bar.setFlowControl(true);
    // ascending run: 0x008..0x044 then wrap (16 words in one 64-byte line)
    run("wc-line", [&] { for (int i = 0; i < n; i++) bar.write32(0x040 + 4 * (i & 15), i); bar.fenceWc(); });
    run("wc-each", [&] { for (int i = 0; i < n; i++) { bar.write32(0x008, i); bar.fenceWc(); } });
  } else {
    printf("(no WC mapping)\n");
  }
  if (only) return 0;
  double t0 = nsNow();
  for (int i = 0; i < 2000; i++) (void)bar.status();
  printf("status read %.0f ns\n", (nsNow() - t0) / 2000);
  return 0;
}
