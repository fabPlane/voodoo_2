OUTPUT_ARCH(riscv)
ENTRY(_start)

/* Andes A25 / larger soft RV32 view of Console DDR. Voodoo keeps the first
 * 16 MiB. CPU image, heap and stack occupy 256 MiB at 0x80000000 (phys
 * 16 MiB). Shareware pak0.pak is a 32 MiB window at 0x90000000. The 32 KiB
 * BarRv32 BRAM is only a smoke ROM; this linker script is the DDR map. */
MEMORY
{
  RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 256M
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
    . += 64M;
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
