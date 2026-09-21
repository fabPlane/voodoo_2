OUTPUT_ARCH(riscv)
ENTRY(_start)

/* Andes A25 / AE350 view of Console DDR. Voodoo keeps the first 16 MiB
 * (4 colour + 8 tex + pad). CPU image, heap and stack occupy 32 MiB at
 * 0x80000000 (identity-mapped onto phys 16 MiB). Shareware pak0.pak is a
 * separate 20 MiB window at 0x82000000 for the loader / SD DMA. */
MEMORY
{
  RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 32M
}

SECTIONS
{
  .text : {
    KEEP(*(.text.start))
    *(.text .text.*)
    *(.rodata .rodata.*)
    . = ALIGN(8);
  } > RAM

  .data : {
    *(.data .data.*)
    *(.sdata .sdata.*)
    . = ALIGN(8);
  } > RAM

  .bss (NOLOAD) : {
    . = ALIGN(8);
    _sbss = .;
    *(.sbss .sbss.*)
    *(.bss .bss.*)
    *(COMMON)
    . = ALIGN(8);
    _ebss = .;
  } > RAM

  .heap (NOLOAD) : {
    . = ALIGN(16);
    _heap_start = .;
    . += 8M;
    _heap_end = .;
  } > RAM

  .stack (NOLOAD) : {
    . = ALIGN(16);
    . += 64K;
    _stack_top = .;
  } > RAM

  _end = .;
}

PROVIDE(end = _end);
