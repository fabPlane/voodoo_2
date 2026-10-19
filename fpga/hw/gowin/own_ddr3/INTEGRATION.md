# own_ddr3 in the Voodoo build

**Default since 2026-10-19:** the tracked `fpga/hw/gowin/build.tcl`,
`src/board_top.v`, CST and SDC use own_ddr3. For the Gowin IP, define
`USE_GOWIN_DDR3_IP` in a wrapper and use the left-hand column below. (The
`USE_OWN_DDR3` define described next is now a no-op.)

`fpga/hw/gowin/src/board_top.v` picks the DDR3 controller at compile time:

- default: the Gowin IP (`DDR3_Memory_Interface_Top u_ddr3`)
- `` `define USE_OWN_DDR3 ``: `own_ddr3 u_ddr3` (same native UI; debug port tied off)

gw_sh has no command-line define, so a build dir compiles a wrapper:

```tcl
set own /path/to/fpga/hw/gowin
file mkdir gen
set f [open gen/board_top_own.v w]
puts $f "`define USE_OWN_DDR3\n`include \"$own/src/board_top.v\""
close $f
add_file -type verilog [file normalize gen/board_top_own.v]
foreach f {own_ddr3.v own_ddr3_ctrl.v own_ddr3_phy.v} {
    add_file -type verilog $own/own_ddr3/rtl/$f
}
# and skip ip/DDR3_Memory_Interface_Top/*.v in the ip glob
```

Constraints that name the IP's internals change:

| file | Gowin IP | own_ddr3 |
|---|---|---|
| CST | `INS_LOC "u_ddr3/gw3_top/u_ddr_phy_top/u_dll" DDRDLLM_BL;` | `INS_LOC "u_ddr3/u_dll" DDRDLLM_BL;` |
| CST | `INS_LOC "u_ddr3/gw3_top/u_ddr_phy_top/fclkdiv" LEFTSIDE[4];` | `INS_LOC "u_ddr3/u_clkdiv" LEFTSIDE[4];` |
| CST | `SET_PARAM ".../u_dll" CLKIN_SEL=CLKIN5;` | (none) |
| SDC | `create_clock ... [get_pins {u_ddr3/gw3_top/u_ddr_phy_top/fclkdiv/CLKOUT}]` | `[get_pins {u_ddr3/u_clkdiv/CLKOUT}]` |
| SDC | `set_false_path -from [get_regs {u_ddr3/.../ddr_init_complete_d_3_s0}]` | `-from [get_regs {u_ddr3/u_ctrl/cal_done_s*}]` |

`fpga/hw/gowin-own` (untracked, like the other build dirs) is set up this way
from `gowin-q2` (B126a: rtl_b125, 48 MHz core).

Board results:

- standalone JTAG BIST (`test/`): 1 GiB clean at command gaps 0..15, 140
  back-to-back full passes, ~2 GB/s at gap 0
- B126a + own_ddr3 (`gowin-own`, 48 MHz core, all clocks meet timing):
  GLQuake 1 h 07 min / 104k frames, no reload, link up, picture correct;
  29.0 ms/frame median vs 31 ms with the Gowin IP, DDR read latency median
  50 vs 78 cycles; BSRAM 244 -> 196, registers -2960
