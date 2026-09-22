#include "fxhal.h"

#include <stdint.h>
#include <string.h>

/* FBI bring-up from Glide2x sst1InitRegisters as captured in test04
 * (demo_test04.bin writes 0..25). Video size is 640x480 after Open. */
static const uint32_t k_init[][2] = {
    {SST_fbiInit3, 0x00110000u}, {SST_fbiInit1, 0x00000100u}, {SST_fbiInit0, 0x00000006u},
    {SST_fbiInit0, 0x00000002u}, {SST_fbiInit0, 0x00000000u}, {SST_fbiInit0, 0x00000410u},
    {SST_fbiInit1, 0x00201102u}, {SST_fbiInit2, 0x80000040u}, {SST_fbiInit3, 0x00110000u},
    {SST_fbiInit4, 0x00000001u}, {SST_trexInit0, 0x00005441u}, {SST_trexInit1, 0x0000f420u},
    {SST_trexInit0, 0x00005441u}, {SST_trexInit1, 0x0000f420u}, {SST_trexInit0, 0x00005441u},
    {SST_trexInit1, 0x0000f420u}, {SST_fbiInit1, 0x00201100u}, {SST_fbiInit1, 0x00201108u},
    {SST_fbiInit2, 0x80000040u}, {SST_fbiInit4, 0x00000003u}, {SST_fbiInit3, 0x00110001u},
    {SST_fbiInit2, 0x18000040u}, {SST_fbiInit2, 0x18400040u}, {SST_fbiInit2, 0x186000e0u},
    {SST_fbiInit2, 0x1864b0e0u}, {SST_fbiInit1, 0x002011a8u},
};

static int g_open;

static uint32_t f32u(float x) {
  uint32_t u;
  memcpy(&u, &x, sizeof(u));
  return u;
}

uint32_t fxHalFold(uint32_t addr) {
  addr &= (SST_MAP_SIZE - 1u);
  /* Glide Sstregs puts TMU0 at +0x800. Hardware is 10-bit: FBI 0x000-0x2ff,
   * TMU 0x300-0x3ff. fxsim.c folds the same way. LFB/tex pass through. */
  if (addr < SST_LFB_BASE) {
    return addr & 0x3ffu;
  }
  return addr;
}

void fxHalWr(uint32_t addr, uint32_t data) { sst_wr(fxHalFold(addr), data); }

void fxHalWrf(uint32_t addr, float data) { fxHalWr(addr, f32u(data)); }

uint32_t fxHalRd(uint32_t addr) { return sst_rd(fxHalFold(addr)); }

void fxHalPoke(volatile void *reg, uint32_t data) {
  uintptr_t base = (uintptr_t)sst_base();
  uintptr_t p = (uintptr_t)reg;
  uint32_t off = base ? (uint32_t)(p - base) : (uint32_t)p;
  fxHalWr(off, data);
}

uint32_t fxHalPeek(volatile void *reg) {
  uintptr_t base = (uintptr_t)sst_base();
  uintptr_t p = (uintptr_t)reg;
  uint32_t off = base ? (uint32_t)(p - base) : (uint32_t)p;
  return fxHalRd(off);
}

void fxHalIdle(void) {
  unsigned spins = 0;
  while (fxHalRd(SST_status) & SST_ENGINE_BUSY) {
    if (++spins > 1000000u) {
      break;
    }
  }
}

void fxHalNop(void) { fxHalWr(SST_nopCMD, 0); }

static void clip_640x480(void) {
  fxHalWr(SST_clipLeftRight, FXHAL_WIDTH);
  fxHalWr(SST_clipLowYHighY, FXHAL_HEIGHT);
}

int fxHalOpen(volatile uint32_t *bar) {
  unsigned i;
#if defined(__riscv)
  sst_map(1);
#endif
  if (bar) {
    sst_set_base(bar);
  }
  if (g_open) {
    return 0;
  }
  for (i = 0; i < (unsigned)(sizeof(k_init) / sizeof(k_init[0])); i++) {
    fxHalWr(k_init[i][0], k_init[i][1]);
  }
  clip_640x480();
  fxHalWr(SST_fbzMode, SST_FBZ_RGB_WRITE | SST_FBZ_AUX_WRITE);
  fxHalWr(SST_color0, 0);
  fxHalWr(SST_color1, 0);
  fxHalWr(SST_zaColor, 0);
  fxHalWr(SST_fbzColorPath, 0x0400612au); /* iterated RGB, Glide test04 */
  g_open = 1;
  return 0;
}

void fxHalClose(void) {
  if (!g_open) {
    return;
  }
  fxHalIdle();
  g_open = 0;
}

int fxHalIsOpen(void) { return g_open; }

void fxHalBufferClear(uint32_t color1, uint32_t za) {
  fxHalIdle();
  clip_640x480();
  fxHalWr(SST_color1, color1);
  fxHalWr(SST_zaColor, za);
  fxHalWr(SST_fbzMode, SST_FBZ_RGB_WRITE | SST_FBZ_AUX_WRITE);
  fxHalWr(SST_fastfillCMD, 0);
  fxHalWr(SST_fbzMode, 0x4200u); /* RGB+aux+clip, same as test04 / logo spin */
  fxHalWr(SST_fastfillCMD, 0);
  fxHalNop();
}

void fxHalSwap(void) {
  fxHalIdle();
  fxHalWr(SST_swapbufferCMD, 1);
  fxHalNop();
}

void fxHalDrawGouraudTri(const FxHalVert *a, const FxHalVert *b, const FxHalVert *c) {
  float area;
  uint32_t cmd;

  if (!a || !b || !c) {
    return;
  }
  area = (b->x - a->x) * (c->y - a->y) - (b->y - a->y) * (c->x - a->x);
  cmd = (area < 0.0f) ? 0x80000000u : 0u;

  fxHalWrf(SST_fvertexAx, a->x);
  fxHalWrf(SST_fvertexAy, a->y);
  fxHalWrf(SST_fvertexBx, b->x);
  fxHalWrf(SST_fvertexBy, b->y);
  fxHalWrf(SST_fvertexCx, c->x);
  fxHalWrf(SST_fvertexCy, c->y);
  fxHalWrf(SST_fstartR, a->r);
  fxHalWrf(SST_fdRdX, 0.0f);
  fxHalWrf(SST_fdRdY, 0.0f);
  fxHalWrf(SST_fstartG, a->g);
  fxHalWrf(SST_fdGdX, 0.0f);
  fxHalWrf(SST_fdGdY, 0.0f);
  fxHalWrf(SST_fstartB, a->b);
  fxHalWrf(SST_fdBdX, 0.0f);
  fxHalWrf(SST_fdBdY, 0.0f);
  fxHalWr(SST_ftriangleCMD, cmd);
}
