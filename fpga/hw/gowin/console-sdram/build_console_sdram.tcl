# Experimental Voodoo build using the factory NESTang SDR-SDRAM interface.
# Keep revision C for the Voodoo fabric: unlike revision B, it honors the
# distributed-RAM annotations required to keep the generated FIFO fabric
# routable.  The SDRAM interface itself uses only ordinary I/O resources.
set_device GW5AST-LV138PG484AC1/I0 -device_version C

add_file -type verilog [file normalize ../../../rtl/gowin/TangConsole138k.v]
add_file -type verilog [file normalize board_top.v]
add_file -type verilog [file normalize sdram_line_controller.v]
add_file -type verilog [file normalize console_sdram_pll.v]
add_file -type verilog [file normalize jtag_debug_pages.v]
add_file -type verilog [file normalize $::env(GOWINHOME)/IDE/data/ipcores/gw_jtag.v]
add_file -type verilog [file normalize ../console/dvi_tx_top.v]
add_file -type verilog [file normalize ../console/uart_fixed.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi_mod.v]
add_file -type verilog [file normalize ../ip/Gowin_PLL_Sys/pll_init.v]

add_file -type cst [file normalize tang_console_138k_sdram.cst]
add_file -type sdc [file normalize tang_console_138k_sdram.sdc]

set_option -top_module board_top
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
set_option -output_base_name spinalvoodoo_tang_console_138k_sdram
set_option -place_option 1
set_option -route_option 0
set_option -clock_route_order 1
set_option -route_maxfan 100

run all
