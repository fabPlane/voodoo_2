/*
 * harness-replay.cpp - replay a VOODOO_PCI_LOG_ALL capture through the same
 * sim_pcie_harness backend a live Glide program uses (flow control, idle
 * before non-status reads, idle wait before swaps as fxsim does), so a hang
 * that only shows up live can be reproduced without the game.
 *
 *   g++ -O2 -I../../../ext/SpinalVoodoo/emu/sim/harness harness-replay.cpp \
 *       sim_pcie_harness.cpp -o harness-replay
 *   sudo ./harness-replay capture.log [max-ops]
 *
 * For bisecting: HR_NO_SWAP_IDLE=1 skips the idle wait before swaps,
 * HR_NO_READS=1 skips the captured reads.
 *
 * SPDX-License-Identifier: MIT
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "sim_harness.h"

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s capture.log [max-ops]\n", argv[0]);
    return 1;
  }
  FILE *f = fopen(argv[1], "r");
  if (!f) { perror(argv[1]); return 1; }
  const unsigned long long maxOps = argc > 2 ? strtoull(argv[2], nullptr, 0) : ~0ull;
  const bool swapIdle = !getenv("HR_NO_SWAP_IDLE");
  const bool reads = !getenv("HR_NO_READS");
  if (sim_init() != 0) return 1;
  char line[256];
  unsigned long long ops = 0, swaps = 0;
  while (ops < maxOps && fgets(line, sizeof line, f)) {
    unsigned addr = 0, data = 0;
    if (sscanf(line, "w32 %x %x", &addr, &data) == 2) {
      // fxsim drains the pipeline before every swapbufferCMD.
      if ((addr & 0x3fff) == 0x128 && addr < 0x400000) {
        if (swapIdle) sim_idle_wait();
        swaps++;
      }
      sim_write(addr, data);
    } else if (sscanf(line, "w16 %x %x", &addr, &data) == 2) {
      sim_write16(addr, static_cast<uint16_t>(data));
    } else if (sscanf(line, "r32 %x", &addr) == 1) {
      if (reads) sim_read(addr);
    } else {
      continue;
    }
    if (++ops % 20000 == 0) fprintf(stderr, "[replay] %llu ops, %llu swaps\n", ops, swaps);
  }
  fprintf(stderr, "[replay] done: %llu ops, %llu swaps; final status 0x%08x\n", ops, swaps, sim_read(0));
  sim_shutdown();
  return 0;
}
