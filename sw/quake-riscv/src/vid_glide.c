#include "vid_riscv.h"
#include "fxhal.h"

/* Glide path: WinQuake engine, SST-1 via fxhal (sst_wr / sst_rd). Glide2x
 * sst1 ISET/IGET bind to fxHalPoke / fxHalPeek. */

void VID_GlideBeginFrame(void) {
  if (!fxHalIsOpen()) {
    fxHalOpen(sst_base());
  }
  fxHalBufferClear(0, 0);
}

void VID_GlideSwap(void) { fxHalSwap(); }
