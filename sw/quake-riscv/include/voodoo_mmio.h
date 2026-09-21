#ifndef VOODOO_MMIO_H
#define VOODOO_MMIO_H

#include <stdint.h>

/* SST-1 16 MiB map, same as SpinalVoodoo Core / CoreGowin BAR. */
#define SST_MAP_SIZE 0x1000000u
#define SST_REG_BASE 0x000000u
#define SST_LFB_BASE 0x400000u
#define SST_TEX_BASE 0x800000u

/* Host/QEMU: record writes. FPGA A25: point this at the AXI-lite BAR.
 * Console BAR is identity at 0; sst_map(1) still pokes phys 0 when base is
 * NULL (RISC-V). Host probe leaves mapping off so writes stay in the log. */
void sst_set_base(volatile uint32_t *base);
volatile uint32_t *sst_base(void);
void sst_map(int enable);
int sst_mapped(void);

void sst_wr(uint32_t addr, uint32_t data);
uint32_t sst_rd(uint32_t addr);

enum { SST_LOG_CAP = 4096 };
typedef struct {
  uint32_t addr;
  uint32_t data;
} SstWrite;

int sst_log_count(void);
const SstWrite *sst_log_at(int i);
void sst_log_reset(void);

#endif
