set_device GW5AST-LV138PG484AC1/I0 -device_version C

add_file -type verilog [file normalize ../rtl/jtag_mailbox.v]
add_file -type verilog [file normalize $::env(GOWINHOME)/IDE/data/ipcores/gw_jtag.v]
add_file -type cst [file normalize jtag_mailbox.cst]

set_option -top_module top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_i2c_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name vendor_jtag_mailbox

run all
