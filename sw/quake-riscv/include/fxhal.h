#ifndef FXHAL_H
#define FXHAL_H

#include "sst1_regs.h"
#include "voodoo_mmio.h"

#include <stdint.h>

/* Glide2x sst1 hardware access without PCI. ISET/IGET become these two
 * calls; the 16 MiB SST-1 map is the Console BAR (`sst_set_base(0)`).
 *
 *   #define ISET(A, D) fxHalPoke(&(A), (uint32_t)(D))
 *   #define IGET(A)    fxHalPeek(&(A))
 */

typedef struct {
  float x, y;
  float r, g, b;
} FxHalVert;

int fxHalOpen(volatile uint32_t *bar);
void fxHalClose(void);
int fxHalIsOpen(void);

uint32_t fxHalFold(uint32_t addr);
void fxHalWr(uint32_t addr, uint32_t data);
void fxHalWrf(uint32_t addr, float data);
uint32_t fxHalRd(uint32_t addr);

void fxHalPoke(volatile void *reg, uint32_t data);
uint32_t fxHalPeek(volatile void *reg);

void fxHalIdle(void);
void fxHalNop(void);
void fxHalBufferClear(uint32_t color1, uint32_t za);
void fxHalSwap(void);
void fxHalDrawGouraudTri(const FxHalVert *a, const FxHalVert *b, const FxHalVert *c);

enum { FXHAL_WIDTH = 640, FXHAL_HEIGHT = 480 };

#endif
