# Small DDR training check. Red HDMI until init_calib_complete, then green.
set_device GW5AST-LV138PG484AC1/I0 -device_version C

add_file -type verilog [file normalize ddr_cal_top.v]
add_file -type verilog [file normalize ../console/dvi_tx_top.v]
add_file -type verilog [file normalize ../ip/PLL_DDR_297/pll_27.v]
add_file -type verilog [file normalize ../ip/PLL_DDR_297/pll_27_mod.v]
add_file -type verilog [file normalize ../ip/PLL_DDR_297/pll_ddr3.v]
add_file -type verilog [file normalize ../ip/PLL_DDR_297/pll_ddr3_mod.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Sys/pll_init.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi_mod.v]
add_file -type verilog [file normalize ../ip/DDR3_Memory_Interface_300/ddr3_memory_interface/ddr3_memory_interface.v]
add_file -type cst [file normalize ddr_cal.cst]

set_option -top_module ddr_cal_top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name ddr_cal_top

run all
