/* gic.c - GICv2, enough of it for one PPI.
 *
 * The audio sink in this image is driven by a real interrupt, not by polling,
 * and that is the point: on the board, the thing that advances the play cursor
 * is the I2S DMA engine's block-completion interrupt (port/DESIGN.md section
 * 2.3), so the emulator's sink has to have the same shape or it is not testing
 * the same code. Polling would have been three lines and would have proved
 * nothing about the vector table, the IRQ stack, the AAPCS-correct handler
 * frame, or the end-of-interrupt discipline.
 *
 * WHAT THIS DOES NOT PROVE. `-M virt` puts a generic ARM GIC-400 at
 * 0x08000000/0x08010000, and the T113 puts *its* GIC-400 at 0x03020000 with
 * Allwinner's own interrupt numbering (boot/BRINGUP.md section 4.3). The
 * register programming below is architectural and transfers; the base
 * addresses, the interrupt IDs and the wiring do not. This is a GIC driver
 * that works, not the T113's GIC driver.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include "emu.h"

#define GICD_BASE   0x08000000u
#define GICC_BASE   0x08010000u

#define GICD_CTLR       0x000
#define GICD_ISENABLER  0x100
#define GICD_ICENABLER  0x180
#define GICD_ICPENDR    0x280
#define GICD_IPRIORITYR 0x400
#define GICD_ITARGETSR  0x800
#define GICD_ICFGR      0xC00

#define GICC_CTLR       0x000
#define GICC_PMR        0x004
#define GICC_BPR        0x008
#define GICC_IAR        0x00C
#define GICC_EOIR       0x010

#define MAX_INTID       96u    /* SGIs + PPIs + the first 64 SPIs: covers all 32 virtio-mmio slots */

static volatile uint32_t *gicd(uint32_t o) { return (volatile uint32_t *)(uintptr_t)(GICD_BASE + o); }
static volatile uint32_t *gicc(uint32_t o) { return (volatile uint32_t *)(uintptr_t)(GICC_BASE + o); }

static emu_irq_handler g_handlers[MAX_INTID];
static uint32_t g_irqs;
static uint32_t g_spurious;

void emu_gic_init(void)
{
    unsigned i;

    *gicd(GICD_CTLR) = 0u;
    *gicc(GICC_CTLR) = 0u;

    /* Banked SGI/PPI block for this core: disable everything, clear pending,
     * middling priority. We arrive from a loader that may have left the
     * controller in any state -- U-Boot certainly has. */
    *gicd(GICD_ICENABLER + 0u) = 0xFFFFFFFFu;
    *gicd(GICD_ICPENDR  + 0u) = 0xFFFFFFFFu;
    for (i = 0; i < MAX_INTID; i += 4u)
        *gicd(GICD_IPRIORITYR + i) = 0xA0A0A0A0u;
    /* SPIs target CPU0. SGI/PPI targets are read-only and banked. */
    for (i = 32u; i < MAX_INTID; i += 4u)
        *gicd(GICD_ITARGETSR + i) = 0x01010101u;

    for (i = 0; i < MAX_INTID; i++) g_handlers[i] = 0;

    *gicc(GICC_PMR)  = 0xF0u;   /* accept anything more urgent than 0xF0   */
    *gicc(GICC_BPR)  = 0x03u;   /* no preemption grouping                   */
    *gicc(GICC_CTLR) = 1u;      /* enable the CPU interface                 */
    *gicd(GICD_CTLR) = 1u;      /* enable the distributor                   */
}

void emu_gic_set_handler(unsigned intid, emu_irq_handler h)
{
    if (intid < MAX_INTID) g_handlers[intid] = h;
}

void emu_gic_enable(unsigned intid, unsigned priority)
{
    if (intid >= MAX_INTID) return;
    {
        volatile uint8_t *pri = (volatile uint8_t *)(uintptr_t)(GICD_BASE + GICD_IPRIORITYR);
        pri[intid] = (uint8_t)priority;
    }
    *gicd(GICD_ISENABLER + (intid / 32u) * 4u) = 1u << (intid % 32u);
    __asm__ volatile("dsb\n\tisb" ::: "memory");
}

/* Called from the IRQ vector in start.S, in SVC mode, on the SVC stack. */
void emu_irq_dispatch(void)
{
    unsigned handled = 0;

    for (;;) {
        uint32_t iar = *gicc(GICC_IAR);
        uint32_t id  = iar & 0x3FFu;
        if (id >= 1020u) {
            /* 1023 means "nothing pending". Reading it once at the end of the
             * drain loop is normal and is not a spurious interrupt; only an
             * entry to the vector that finds nothing at all is. */
            if (handled == 0u) g_spurious++;
            return;
        }
        handled++;
        g_irqs++;
        if (id < MAX_INTID && g_handlers[id]) g_handlers[id]();
        *gicc(GICC_EOIR) = iar;
    }
}

uint32_t emu_irq_count(void)      { return g_irqs; }
uint32_t emu_spurious_count(void) { return g_spurious; }
