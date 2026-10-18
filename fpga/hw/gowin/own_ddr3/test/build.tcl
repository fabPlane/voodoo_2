# own_ddr3 standalone JTAG test bitstream (Tang Mega 138K Pro Dock).
# Run from this directory: . ../../env.sh && gw_sh build.tcl
# VENDOR=1 in the environment builds the same top with the Gowin DDR3 IP,
# for a like-for-like resource and timing comparison.
set vendor [expr {[info exists ::env(VENDOR)] && $::env(VENDOR) == "1"}]
set_device -name GW5AST-138B GW5AST-LV138FPG676AES

# gw_sh has no command-line define: the own variant compiles a wrapper
# that defines OWN_DDR3 and includes the top.
file mkdir gen
set f [open gen/top_own.v w]
puts $f "`define OWN_DDR3\n`include \"[file normalize own_ddr3_jtag_top.v]\""
close $f
if {$vendor} {
    add_file -type verilog [file normalize own_ddr3_jtag_top.v]
} else {
    add_file -type verilog [file normalize gen/top_own.v]
}
add_file -type verilog [file normalize ../../ip/Gowin_PLL_Sys/Gowin_PLL_Sys.v]
add_file -type verilog [file normalize ../../ip/Gowin_PLL_Sys/Gowin_PLL_Sys_mod.v]
add_file -type verilog [file normalize $::env(GOWINHOME)/IDE/data/ipcores/gw_jtag.v]
if {$vendor} {
    add_file -type verilog [file normalize ../../ip/DDR3_Memory_Interface_Top/DDR3_Memory_Interface_Top.v]
    add_file -type cst [file normalize vendor.cst]
    add_file -type sdc [file normalize vendor.sdc]
    set_option -output_base_name ddr3_vendor
} else {
    add_file -type verilog [file normalize ../rtl/own_ddr3.v]
    add_file -type verilog [file normalize ../rtl/own_ddr3_ctrl.v]
    add_file -type verilog [file normalize ../rtl/own_ddr3_phy.v]
    add_file -type cst [file normalize own_ddr3.cst]
    add_file -type sdc [file normalize own_ddr3.sdc]
    set_option -output_base_name own_ddr3
}

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

run all
