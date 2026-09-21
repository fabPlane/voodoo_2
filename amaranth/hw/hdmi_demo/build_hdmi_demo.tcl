# Gowin EDA build for the Amaranth HDMI test-pattern demo on the Sipeed Tang
# Retro Console 138K. Mirrors fpga/hw/gowin/console/build_console.tcl (device,
# options) and hw/spike/build_spike.tcl (script-relative paths).
#
#   cd amaranth && make hdmi-demo       # generate, lint, sim, then gw_sh on hyper-ms-lin
#   # or by hand, on the box:
#   cd amaranth/hw/hdmi_demo/build && gw_sh ../build_hdmi_demo.tcl
#
# gw_sh writes impl/ into the current directory, hence the `cd build`. The
# Amaranth Verilog is expected at build/HdmiDemoTop.v: `make hdmi-demo` puts
# it there rather than under amaranth/rtl/, which the AGENTS.md rsync excludes.
#
# One gw_sh per box (`pgrep -af '[/]gw_sh'`). See ../../../AGENTS.md.

set here [file normalize [file dirname [info script]]]
set ip   [file normalize [file join $here .. .. .. fpga hw gowin ip]]

set_device GW5AST-LV138PG484AC1/I0 -device_version C

add_file -type verilog [file join $here build HdmiDemoTop.v]
add_file -type verilog [file join $here hdmi_demo_board_top.v]
add_file -type verilog [file join $here dvi_tx_top.v]
# Vendor PLL (Gowin_PLL_Hdmi + its _MOD) and the PLL_INIT helper it
# instantiates, which the IP generator dropped next to the Sys PLL.
add_file -type verilog [file join $ip Gowin_PLL_Hdmi Gowin_PLL_Hdmi.v]
add_file -type verilog [file join $ip Gowin_PLL_Hdmi Gowin_PLL_Hdmi_mod.v]
add_file -type verilog [file join $ip Gowin_PLL_Sys pll_init.v]

add_file -type cst [file join $here hdmi_demo.cst]
add_file -type sdc [file join $here hdmi_demo.sdc]

set_option -top_module hdmi_demo_board_top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -rw_check_on_ram 0
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name amaranth_hdmi_demo

run all
