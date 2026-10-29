MEMORY { LMB (rwx) : ORIGIN = 0x00000000, LENGTH = 32K }
ENTRY(_start)
SECTIONS {
  .text : { *(.text.start) *(.text*) } > LMB
  .rodata : { *(.rodata*) *(.srodata*) } > LMB
  .data : { *(.data*) *(.sdata*) } > LMB
  .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > LMB
  /DISCARD/ : { *(.comment) *(.riscv.attributes) }
}
