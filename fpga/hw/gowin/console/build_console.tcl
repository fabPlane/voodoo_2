# Gowin EDA build for Sipeed Tang Retro Console 138K.
# Requires gw_sh on PATH and the IP cores in ../ip (PLL, DDR3, DVI TX). No PCIe.

set_device GW5AST-LV138PG484AC1/I0 -device_version C

add_file -type verilog [file normalize ../../../rtl/gowin/TangConsole138k.v]
add_file -type verilog [file normalize board_top.v]
add_file -type verilog [file normalize ../console-sdram/jtag_debug_pages.v]
add_file -type verilog [file normalize $::env(GOWINHOME)/IDE/data/ipcores/gw_jtag.v]
add_file -type verilog [file normalize dvi_tx_top.v]
add_file -type verilog [file normalize uart_fixed.v]
add_file -type verilog [file normalize diagnostic_uart.v]
foreach f [glob -nocomplain ../ip/*/*.v] {
    add_file -type verilog [file normalize $f]
}

add_file -type cst [file normalize tang_console_138k.cst]
add_file -type sdc [file normalize tang_console_138k.sdc]

set_option -top_module board_top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_cpu_as_gpio 1
# P&R was mapping SDPX9B at ~2x the synth count and blowing the 340 BSRAM
# cap. Read-during-write checks are a Gowin extra, not required for function.
set_option -rw_check_on_ram 0
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name spinalvoodoo_tang_console_138k
# Education P&R defaulted to place 3 / route 2 (fast). Route 2 finished
# phases 2–3 in 0s with 168 unrouted nets once USB CDC filled PRIMARY.
# Place 1 = routability; route 0 is the best congestion strategy tested.
# The revision-C 300 MHz DDR controller missed by only 20 nets in mode 0;
# mode 1 searched for 3h16m and regressed to 3022 unrouted nets, while mode 2
# previously stopped with 168. Clocks are ordered by frequency so GPU
# high-fanout nets do not steal the 8 PRIMARY slots from clk60/DDR.
set_option -place_option 1
set_option -route_option 0
set_option -clock_route_order 1
set_option -route_maxfan 100

run all
