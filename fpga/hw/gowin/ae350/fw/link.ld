MEMORY { ROM (rx) : ORIGIN = 0x80000000, LENGTH = 8K
         RAM (rw) : ORIGIN = 0x00000000, LENGTH = 16K }
ENTRY(_start)
SECTIONS {
  .text : { *(.text.start) *(.text*) *(.rodata*) *(.srodata*) } > ROM
  .bss (NOLOAD) : { __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > RAM
  /DISCARD/ : { *(.data*) *(.sdata*) *(.comment) *(.riscv.attributes) }
}
