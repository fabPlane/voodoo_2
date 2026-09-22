#include "fxhal.h"
#include "in_usb.h"
#include "pak.h"
#include "tang_mem.h"
#include "vid_riscv.h"
#include "voodoo_mmio.h"

#include <stdio.h>

static int fail(const char *msg) {
  fprintf(stderr, "quake-riscv probe: %s\n", msg);
  return 1;
}

int main(void) {
  QuakePad pad = in_usb_map((uint16_t)(USB_UP | USB_A));
  if (!pad.forward || !pad.fire || pad.back) {
    return fail("USB SNES map");
  }

  sst_log_reset();
  sst_set_base(0);
  sst_map(0);
  if (fxHalFold(0xB00u) != 0x300u) {
    return fail("TMU0 0xB00 fold");
  }
  if (fxHalFold(SST_TEX_BASE + 16u) != SST_TEX_BASE + 16u) {
    return fail("tex aperture fold");
  }
  if (VID_Init(VID_SW) != 0 || VID_Backend() != VID_SW) {
    return fail("VID_SW init");
  }
  VID_Update();
  VID_Shutdown();

  if (VID_Init(VID_GLIDE) != 0 || VID_Backend() != VID_GLIDE) {
    return fail("VID_GLIDE init");
  }
  VID_GlideBeginFrame();
  {
    FxHalVert a = {100.f, 80.f, 255.f, 0.f, 0.f};
    FxHalVert b = {220.f, 80.f, 0.f, 255.f, 0.f};
    FxHalVert c = {160.f, 200.f, 0.f, 0.f, 255.f};
    fxHalDrawGouraudTri(&a, &b, &c);
  }
  VID_GlideSwap();
  {
    int init = 0, tri = 0, swap = 0;
    int i;
    for (i = 0; i < sst_log_count(); i++) {
      const SstWrite *w = sst_log_at(i);
      if (w->addr == SST_fbiInit3 && w->data == 0x00110000u) {
        init = 1;
      }
      if (w->addr == SST_fvertexAx) {
        tri = 1;
      }
      if (w->addr == SST_swapbufferCMD && w->data == 1u) {
        swap = 1;
      }
    }
    if (!init) {
      return fail("fbiInit write");
    }
    if (!tri) {
      return fail("fvertexAx write");
    }
    if (!swap) {
      return fail("swapbufferCMD");
    }
  }
  VID_Shutdown();

  {
    static uint32_t bar[0x100];
    sst_set_base(bar);
    fxHalWr(SST_fbiInit3, 0x00110000u);
    if (bar[SST_fbiInit3 >> 2] != 0x00110000u) {
      return fail("shadow BAR wr");
    }
    if (fxHalRd(SST_fbiInit3) != 0x00110000u) {
      return fail("shadow BAR rd");
    }
    fxHalPoke((volatile char *)bar + SST_nopCMD, 1);
    if (bar[SST_nopCMD >> 2] != 1u) {
      return fail("fxHalPoke");
    }
    fxHalWr(0xB00u, 0x42u);
    if (bar[0x300 >> 2] != 0x42u) {
      return fail("TMU fold wr");
    }
    sst_set_base(0);
  }

  {
    const char *pak_path = 0;
    const char *cands[] = {"id1/pak0.pak", "id1/PAK0.PAK", 0};
    int i;
    for (i = 0; cands[i]; i++) {
      if (pak_open(cands[i]) == 0) {
        pak_path = cands[i];
        break;
      }
    }
    if (!pak_path) {
      return fail("id1/pak0.pak (run make shareware)");
    }
    if (!pak_has("demo1.dem") || !pak_has("demo2.dem") || !pak_has("demo3.dem")) {
      pak_close();
      return fail("WinQuake demo1/2/3.dem");
    }
    printf("quake-riscv: SW and Glide backends ok, shareware %s (%d files)\n",
           pak_path, pak_count());
    printf("quake-riscv: DDR map ram=%#x+%uMiB pak=%#x play=%#x\n",
           TANG_CPU_RAM_BASE, TANG_CPU_RAM_BYTES / (1024u * 1024u),
           TANG_PAK_BASE, TANG_GLIDE_PLAY_PHYS);
    pak_close();
  }
  return 0;
}
