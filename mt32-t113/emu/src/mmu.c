/* mmu.c - a flat 1:1 mapping with caches on, built from 1 MB sections.
 *
 * WHY THIS EXISTS AT ALL, since the image runs correctly without it.
 *
 * With the MMU off, an ARMv7-A core treats every access as Strongly Ordered
 * and the D-cache cannot be enabled. Every load and store goes to DRAM. On a
 * T113 that is roughly an order of magnitude off the real machine, and under
 * QEMU it is merely slow -- but the render loop's whole job is to meet a
 * deadline, and a deadline measured with the caches off is not a measurement
 * of anything. So the image can be built both ways and says which it is:
 *
 *   make MMU=1   (default)  flat map, L1+L2 on, audio ring Non-cacheable
 *   make MMU=0              exactly the bootm handover state, never changed
 *
 * The mapping is deliberately the simplest thing that is correct:
 *
 *   0x00000000..0x3FFFFFFF   Device, XN      peripherals (PL011, GIC, and on
 *                                            the T113 the whole 0x0.. window)
 *   0x40000000..0x7FFFFFFF   Normal WB/WA, shareable, executable   DRAM
 *   __dma_start..__dma_end   Normal Non-cacheable, shareable, XN
 *   everything else          faulting
 *
 * The non-cacheable window is the part that matters for the port rather than
 * for QEMU. port/DESIGN.md section 2.3 requires the audio ring to be
 * non-cacheable normal memory, because the CPU writes those 1536 bytes and the
 * I2S DMA engine reads them. There is no DMA engine in `-M virt`, so nothing
 * here can prove the mapping is *sufficient* -- but the mapping is built, the
 * ring is placed in it by the linker script, and the render loop writes
 * through it, which proves the attribute plumbing works and that uncached
 * int16 stores at 48 kHz are not a performance problem. On the board this
 * descriptor is the one you check first when the audio is glitchy.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>

extern uint32_t __pagetable_start[];
extern char __dma_start[], __dma_end[];

/* ARMv7 short-descriptor section, bits as in ARM ARM B3.5.1:
 *   [1:0]=0b10 section, [2]=B, [3]=C, [4]=XN, [8:5]=domain,
 *   [11:10]=AP[1:0], [14:12]=TEX, [15]=AP[2], [16]=S, [17]=nG, [31:20]=base */
#define SECT            (2u <<  0)
#define B_BIT           (1u <<  2)
#define C_BIT           (1u <<  3)
#define XN_BIT          (1u <<  4)
#define AP_RW           (3u << 10)   /* full access at PL1 and PL0          */
#define TEX(x)          (((x) & 7u) << 12)
#define S_BIT           (1u << 16)

/* Normal, Outer+Inner write-back write-allocate, shareable.               */
#define MAP_NORMAL_WB   (SECT | AP_RW | TEX(1) | C_BIT | B_BIT | S_BIT)
/* Normal, Outer+Inner non-cacheable, shareable. The DMA window.           */
#define MAP_NORMAL_NC   (SECT | AP_RW | TEX(1)                 | S_BIT | XN_BIT)
/* Device, shareable, never executable. All peripherals.                   */
#define MAP_DEVICE      (SECT | AP_RW | TEX(0)         | B_BIT | S_BIT | XN_BIT)

#define DRAM_BASE       0x40000000u
#define DRAM_TOP        0x80000000u

static inline void dsb_isb(void)
{
    __asm__ volatile("dsb\n\tisb" ::: "memory");
}

/* Called from start.S with the MMU off, BSS cleared and the FPU enabled.
 * Must not touch anything that has not been initialised yet -- no printf,
 * no heap. */
void emu_mmu_enable(void)
{
    volatile uint32_t *tt = __pagetable_start;
    uint32_t i;

    for (i = 0; i < 4096u; i++) {
        uint32_t base = i << 20;
        if (base >= DRAM_BASE && base < DRAM_TOP)
            tt[i] = base | MAP_NORMAL_WB;
        else if (base < DRAM_BASE)
            tt[i] = base | MAP_DEVICE;
        else
            tt[i] = 0u;                 /* fault: catch a wild pointer      */
    }

    /* Retype the linker's 1 MB uncached window. */
    {
        uint32_t lo = (uint32_t)(uintptr_t)__dma_start >> 20;
        uint32_t hi = ((uint32_t)(uintptr_t)__dma_end + 0xFFFFFu) >> 20;
        for (i = lo; i < hi && i < 4096u; i++)
            tt[i] = (i << 20) | MAP_NORMAL_NC;
    }

    __asm__ volatile(
        /* All domains "client", so the AP bits in the descriptors are what
         * actually decides access. */
        "mov  r0, #0\n"
        "mcr  p15, 0, r0, c2, c0, 2\n"          /* TTBCR = 0: TTBR0 only     */
        "ldr  r1, =0x55555555\n"
        "mcr  p15, 0, r1, c3, c0, 0\n"          /* DACR                      */
        /* TTBR0: table base | IRGN=WBWA, S, RGN=WBWA  (bits 6,1 / 4:3 / 1) */
        "orr  r0, %0, #0x6a\n"
        "mcr  p15, 0, r0, c2, c0, 0\n"
        "mcr  p15, 0, r0, c8, c7, 0\n"          /* TLBIALL                   */
        "dsb\n\tisb\n"
        /* ACTLR.SMP. On a Cortex-A7 the caches do not participate in
         * coherency and, per the TRM, cacheable accesses may not behave as
         * expected unless SMP is set. U-Boot's sunxi SPL already sets it
         * (boot/BRINGUP.md section 4.3); set it again because it is idempotent
         * and because we may be entered from something that did not. On a
         * non-secure PL1 this write is ignored rather than faulting. */
        "mrc  p15, 0, r0, c1, c0, 1\n"
        "orr  r0, r0, #(1 << 6)\n"
        "mcr  p15, 0, r0, c1, c0, 1\n"
        "isb\n"
        /* SCTLR: M (MMU), C (D-cache), I (I-cache), Z (branch prediction). */
        "mrc  p15, 0, r0, c1, c0, 0\n"
        "orr  r0, r0, #(1 << 0)\n"
        "orr  r0, r0, #(1 << 2)\n"
        "orr  r0, r0, #(1 << 11)\n"
        "orr  r0, r0, #(1 << 12)\n"
        "mcr  p15, 0, r0, c1, c0, 0\n"
        "dsb\n\tisb\n"
        :
        : "r" (tt)
        : "r0", "r1", "memory");
}

/* Clean+invalidate a range by MVA. Not used by the audio path -- the ring is
 * in the non-cacheable window, which port/DESIGN.md section 2.3 argues is the
 * right trade for 1536 bytes -- but this is the routine a cached ring would
 * need, and having it here means the alternative is one line away. */
void emu_dcache_clean_range(const void *addr, uint32_t len)
{
    uint32_t p = (uint32_t)(uintptr_t)addr & ~31u;
    uint32_t end = (uint32_t)(uintptr_t)addr + len;
    for (; p < end; p += 32u)
        __asm__ volatile("mcr p15, 0, %0, c7, c14, 1" :: "r"(p) : "memory");
    dsb_isb();
}
