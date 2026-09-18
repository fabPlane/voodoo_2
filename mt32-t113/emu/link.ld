/* link.ld - bare-metal ARMv7-A image for the mt32-t113 port layer.
 *
 * Load address 0x40200000. That number is not a QEMU number: it is the T113's
 * DRAM base (0x40000000, boot/BRINGUP.md section 4.2) plus the conventional
 * 2 MB offset that leaves U-Boot's low memory alone. QEMU's `-M virt` happens
 * to put its RAM at the same 0x40000000, which is why one linker script can
 * serve both the emulator today and an mkimage-wrapped payload later.
 *
 * Regions, in order:
 *   .text/.rodata/.data/.bss    the image
 *   page table                  16 KB, 16 KB-aligned (ARMv7 short descriptor)
 *   stacks                      SVC/IRQ/ABT/UND/FIQ, see src/start.S
 *   heap                        _sbrk walks this; mtp arena + malloc live here
 *   .dma                        1 MB-aligned, mapped Normal Non-cacheable by
 *                               src/mmu.c, because on real silicon the I2S DMA
 *                               engine reads the audio ring out of it
 *                               (port/DESIGN.md section 2.3)
 *
 * SPDX-License-Identifier: 0BSD
 */

ENTRY(_start)

/* Sized from port/PORTING.md section 7: a 4 MB arena covers CM-32L int16 with
 * the ROM buffers resident, plus this port's own ~100 KB. 12 MB here so the
 * same image can hold the ROM staging buffers (1 MB + 128 KB) at the same
 * time without tuning, on a part with 512 MB. */
HEAP_SIZE  = 12M;
SVC_STACK  = 64K;   /* port/PORTING.md section 4.4 wants 32 KB; double it */
IRQ_STACK  = 8K;
FIQ_STACK  = 1K;
ABT_STACK  = 4K;
UND_STACK  = 4K;

MEMORY
{
    RAM (rwx) : ORIGIN = 0x40200000, LENGTH = 64M
}

SECTIONS
{
    . = ORIGIN(RAM);

    .text : ALIGN(32)
    {
        __image_start = .;
        KEEP(*(.vectors))          /* must be first: VBAR points here */
        *(.text .text.*)
        *(.glue_7 .glue_7t)
        . = ALIGN(4);
    } > RAM

    .rodata : ALIGN(8)
    {
        *(.rodata .rodata.*)
        . = ALIGN(4);
        /* C++ static constructors. mt32emu has a handful (Tables, ROMInfo
         * tables); start.S runs them before main. */
        __init_array_start = .;
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array))
        __init_array_end = .;
        . = ALIGN(8);
    } > RAM

    .ARM.exidx : { *(.ARM.exidx*) *(.gnu.linkonce.armexidx.*) } > RAM

    .data : ALIGN(8)
    {
        __data_start = .;
        *(.data .data.*)
        . = ALIGN(8);
        __data_end = .;
    } > RAM

    .bss (NOLOAD) : ALIGN(8)
    {
        __bss_start = .;
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(8);
        __bss_end = .;
    } > RAM

    /* First-level translation table: 4096 x 32-bit sections, 16 KB aligned. */
    .pagetable (NOLOAD) : ALIGN(16384)
    {
        __pagetable_start = .;
        . += 16K;
        __pagetable_end = .;
    } > RAM

    .stacks (NOLOAD) : ALIGN(16)
    {
        . += FIQ_STACK; __stack_fiq_top = .;
        . += UND_STACK; __stack_und_top = .;
        . += ABT_STACK; __stack_abt_top = .;
        . += IRQ_STACK; __stack_irq_top = .;
        . += SVC_STACK; __stack_svc_top = .;
        __stack_limit = . - SVC_STACK;
    } > RAM

    .heap (NOLOAD) : ALIGN(4096)
    {
        __heap_start = .;
        . += HEAP_SIZE;
        __heap_end = .;
    } > RAM

    /* Uncached window. One 1 MB section so the MMU code can retype exactly
     * one descriptor; the audio ring is the only thing in it. */
    .dma (NOLOAD) : ALIGN(1M)
    {
        __dma_start = .;
        . += 1M;
        __dma_end = .;
    } > RAM

    __image_end = .;

    /DISCARD/ : { *(.comment) *(.note.*) *(.ARM.attributes) }
}
