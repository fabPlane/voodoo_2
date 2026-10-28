# AE350 hard RISC-V (Andes A25) on the GW5AST-138

The GW5AST die has a hard Andes A25 (RV32 with FPU and MMU) in Gowin's AE350
subsystem. The Gowin IP (`IDE/ipcore/RiscV_AE350_SOC`) ships encrypted Verilog
configured by a `` `define `` header: `riscv_ae350_config.v` here replaces the
IP generator. `riscv_ae350_top.v` and `riscv_ae350_soc.v` come from
`$GOWINHOME/IDE/ipcore/RiscV_AE350_SOC/data/` (add them with
`set_option -include_path` pointing at this directory).

`ae350_shell.v`: synthesis-cost test, a minimal SoC (boot ROM + 16 KB data RAM
on the fabric AHB ports, UART2, PIT, SMU, one register on the extended AHB slave).

Result (2026-10-28, synthesis only, added to rtl_b195): the SoC itself
synthesizes to a hard primitive with no fabric logic; the shell costs
+83 LUT, +53 FF, +8 BSRAM (the data RAM; the uninitialized ROM was pruned).
Place-and-route with the core clock on a dedicated clock path is not tested yet.
