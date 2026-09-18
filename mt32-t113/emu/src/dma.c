/* dma.c - the uncached allocator.
 *
 * Everything a device reads or writes behind the CPU's back comes from here:
 * the audio ring, and the virtqueues in virtio.c. The region is the .dma
 * section from link.ld, which mmu.c retypes as Normal Non-cacheable
 * (port/DESIGN.md section 2.3).
 *
 * Bump only, never freed, because it is allocated once at open() and lives
 * until reset -- the same argument as the main arena in retarget.c.
 *
 * On the T113 this allocator is the one that has to be right: the DMAC reads
 * its linked descriptors and the I2S ring out of exactly this kind of memory,
 * and a descriptor list left in write-back memory is the classic silent
 * failure. QEMU will not catch that mistake for us -- TCG does not model
 * caches at all -- which is precisely why the discipline is written down here
 * rather than discovered later with a scope.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>
#include "emu.h"

extern char __dma_start[], __dma_end[];

static char *g_next;

void *dma_alloc(size_t n, size_t align)
{
    uintptr_t p;
    if (g_next == NULL) g_next = __dma_start;
    if (align < 8u) align = 8u;
    p = ((uintptr_t)g_next + (align - 1u)) & ~(uintptr_t)(align - 1u);
    if (p + n > (uintptr_t)__dma_end) return NULL;
    g_next = (char *)(p + n);
    return (void *)p;
}

void dma_reset(void) { g_next = __dma_start; }

uint32_t dma_used(void)
{
    return (uint32_t)((g_next ? g_next : __dma_start) - __dma_start);
}
