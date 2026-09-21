#include "voodoo_mmio.h"

static volatile uint32_t *g_base;
static int g_mmio;
static SstWrite g_log[SST_LOG_CAP];
static int g_log_n;

void sst_set_base(volatile uint32_t *base) { g_base = base; }

volatile uint32_t *sst_base(void) { return g_base; }

void sst_map(int enable) { g_mmio = enable ? 1 : 0; }

int sst_mapped(void) { return g_mmio; }

void sst_log_reset(void) { g_log_n = 0; }

int sst_log_count(void) { return g_log_n; }

const SstWrite *sst_log_at(int i) { return (i >= 0 && i < g_log_n) ? &g_log[i] : 0; }

void sst_wr(uint32_t addr, uint32_t data) {
  addr &= (SST_MAP_SIZE - 1u);
  if (g_log_n < SST_LOG_CAP) {
    g_log[g_log_n].addr = addr;
    g_log[g_log_n].data = data;
    g_log_n++;
  }
  if (g_mmio || g_base) {
    g_base[addr >> 2] = data;
  }
}

uint32_t sst_rd(uint32_t addr) {
  addr &= (SST_MAP_SIZE - 1u);
  if (!g_mmio && !g_base) {
    return 0;
  }
  return g_base[addr >> 2];
}
