# Gowin EDA command-line build for Sipeed Tang Mega 138K Pro Dock.
# Requires Gowin EDA (gw_sh) on PATH and the IP files listed in ip.md.

set_device -name GW5AST-138B GW5AST-LV138FPG676AES

add_file -type verilog [file normalize ../../rtl/gowin/TangMega138kPro.v]
add_file -type verilog [file normalize src/board_top.v]
foreach f [glob -nocomplain ../../rtl/gowin/*.v] {
    add_file -type verilog [file normalize $f]
}
foreach f [glob -nocomplain ip/*/*.v] {
    add_file -type verilog [file normalize $f]
}

add_file -type cst [file normalize tang_mega_138k_pro.cst]
add_file -type sdc [file normalize tang_mega_138k_pro.sdc]

set_option -top_module board_top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -rw_check_on_ram 1
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name spinalvoodoo_tang_mega_138k_pro

run all
