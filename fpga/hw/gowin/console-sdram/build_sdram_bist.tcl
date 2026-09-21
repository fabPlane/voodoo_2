set_device GW5AST-LV138PG484AC1/I0 -device_version C

add_file -type verilog [file normalize sdram_bist_top.v]
add_file -type verilog [file normalize sdram_line_controller.v]
add_file -type verilog [file normalize console_sdram_pll.v]
add_file -type verilog [file normalize ../console/dvi_tx_top.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi_mod.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Sys/pll_init.v]
add_file -type cst [file normalize tang_console_138k_sdram.cst]

set_option -top_module sdram_bist_top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name tang_console_sdram_bist

run all
