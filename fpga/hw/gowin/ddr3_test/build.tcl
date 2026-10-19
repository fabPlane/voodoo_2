# DDR3 + JTAG bring-up bitstream for the Tang Mega 138K Pro Dock.
# Run from this directory: gw_sh build.tcl (needs ../env.sh sourced).
set_device -name GW5AST-138B GW5AST-LV138FPG676AES

add_file -type verilog [file normalize ddr3_jtag_top.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Sys/Gowin_PLL_Sys.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Sys/Gowin_PLL_Sys_mod.v]
add_file -type verilog [file normalize ../ip/DDR3_Memory_Interface_Top/DDR3_Memory_Interface_Top.v]
add_file -type verilog [file normalize $::env(GOWINHOME)/IDE/data/ipcores/gw_jtag.v]
add_file -type cst [file normalize ddr3_test.cst]
add_file -type sdc [file normalize ddr3_test.sdc]

set_option -top_module ddr3_jtag_top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name ddr3_test

run all
