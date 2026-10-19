# Gowin EDA command-line build for Sipeed Tang Mega 138K Pro Dock.
# Requires Gowin EDA (gw_sh) on PATH and the IP files listed in ip.md.

set_device -name GW5AST-138B GW5AST-LV138FPG676AES

add_file -type verilog [file normalize ../../rtl/gowin/TangMega138kPro.v]
add_file -type verilog [file normalize src/board_top.v]
# own DDR3 controller (default in board_top.v; the Gowin DDR3 IP is skipped).
foreach f {own_ddr3.v own_ddr3_ctrl.v own_ddr3_phy.v} {
    add_file -type verilog [file normalize own_ddr3/rtl/$f]
}
add_file -type verilog [file normalize src/jtag_diag.v]
add_file -type verilog [file normalize $::env(GOWINHOME)/IDE/data/ipcores/gw_jtag.v]
foreach f [glob -nocomplain ip/*/*.v] {
    if {[string match *DDR3_Memory_Interface_Top* $f]} continue
    add_file -type verilog [file normalize $f]
}

add_file -type cst [file normalize tang_mega_138k_pro.cst]
add_file -type sdc [file normalize tang_mega_138k_pro.sdc]
set_csr [file normalize ip/PCIE_Controller_Top/serdes.csr]

set_option -top_module board_top
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -rw_check_on_ram 1
set_option -place_option 3
set_option -max_fanout 100
# Tried Sipeed's PCIe demo settings (clock_route_order 1, route_option 1)
# plus replicate_resources: 12k unrouted nets on this design. Leave off.
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name spinalvoodoo_tang_mega_138k_pro

# GOWIN_RUN=pnr reruns place-and-route on the existing synthesis netlist.
if {[info exists ::env(GOWIN_RUN)]} {
    run $::env(GOWIN_RUN)
} else {
    run all
}
