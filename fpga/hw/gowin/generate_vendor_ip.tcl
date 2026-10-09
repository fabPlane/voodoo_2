# Generate DDR3, DVI TX, and PCIe SerDes from the .ipc files in ip/.
# Run as: gw_sh generate_vendor_ip.tcl
# Needs Standard Edition; Education's IP catalog is empty for this part.

set here [file normalize [file dirname [info script]]]
set ipdir [file join $here ip]
set work [file join $here .ipgen]

file mkdir $work
cd $work

create_project -name vendor_ip -dir $work -pn GW5AST-LV138FPG676AES -device_version B -force

proc gen_one {ipc module} {
    puts "==== read_ipc $ipc ===="
    read_ipc $ipc
    puts "==== generate_target $module ===="
    generate_target [get_ips $module]
}

gen_one [file join $ipdir DVI_TX_Top DVI_TX_Top.ipc] DVI_TX_Top
gen_one [file join $ipdir DDR3_Memory_Interface_Top DDR3_Memory_Interface_Top.ipc] DDR3_Memory_Interface_Top
gen_one [file join $ipdir PCIE_Controller_Top serdes.ipc] SerDes_Top

puts "==== generate_vendor_ip done ===="
exit
