# Vivado batch build for the Kintex-7 PCIe card (see ip.md).
#   vivado -mode batch -source build.tcl [-tclargs <part>]
# Default part: XC7K160T-2FFG676. The XC7K325T in the same package is pin
# compatible: -tclargs xc7k325tffg676-2 (needs a licence that covers it, or
# the openXC7 flow).
# Vivado ML Standard (free) covers the XC7K160T.

set part [expr {[llength $argv] > 0 ? [lindex $argv 0] : "xc7k160tffg676-2"}]
set here [file normalize [file dirname [info script]]]
# RTL=<dir> (default rtl/kintex; rtl/kintex-full for KINTEX_FULL), BUILD=<dir>,
# CORE_DIV=<MMCM divide of the 1000 MHz VCO> (8.375 = 119.4 MHz),
# MCS=1: MicroBlaze V MCS with mcs/mcs_fw.elf (the dock's AE350 counterpart).
set rtl [file normalize [expr {[info exists ::env(RTL)] ? $::env(RTL) : "$here/../../rtl/kintex"}]]
set build [file normalize [expr {[info exists ::env(BUILD)] ? $::env(BUILD) : "$here/build"}]]
set jobs [expr {[info exists ::env(JOBS)] ? $::env(JOBS) : 8}]

create_project -force voodoo_k7 $build -part $part
set_property target_language Verilog [current_project]

# ---------------------------------------------------------------- IP
# pcie_7x: endpoint, Gen1 x4, 64-bit / 125 MHz user clock (no TLP
# straddling in a beat), BAR0 32-bit memory 1 MiB, like the dock.
create_ip -name pcie_7x -vendor xilinx.com -library ip -module_name pcie_7x_0
# Clocking module inside the core (default en_ext_clk = true leaves it to
# the example design and adds pipe_* clock ports).
set_property CONFIG.mode_selection {Advanced} [get_ips pcie_7x_0]
set_property CONFIG.en_ext_clk {false} [get_ips pcie_7x_0]
set_property -dict [list \
    CONFIG.Maximum_Link_Width {X4} \
    CONFIG.Link_Speed {2.5_GT/s} \
    CONFIG.Interface_Width {64_bit} \
    CONFIG.User_Clk_Freq {125} \
    CONFIG.Bar0_Enabled {true} \
    CONFIG.Bar0_Type {Memory} \
    CONFIG.Bar0_64bit {false} \
    CONFIG.Bar0_Prefetchable {false} \
    CONFIG.Bar0_Scale {Megabytes} \
    CONFIG.Bar0_Size {1} \
    CONFIG.Vendor_ID {10EE} \
    CONFIG.Device_ID {0007} \
    CONFIG.Class_Code_Base {03} \
    CONFIG.Class_Code_Sub {80} \
    CONFIG.Max_Payload_Size {256_bytes} \
    CONFIG.Ref_Clk_Freq {100_MHz} \
] [get_ips pcie_7x_0]

# MIG: board specific (pins, part, timing), so it comes from a project file
# made once in the MIG GUI: ip/mig.prj (see ip.md).
if {[file exists $here/ip/mig.prj]} {
    create_ip -name mig_7series -vendor xilinx.com -library ip -module_name mig_7series_0
    set_property CONFIG.XML_INPUT_FILE [file normalize $here/ip/mig.prj] [get_ips mig_7series_0]
} else {
    puts "ERROR: ip/mig.prj missing; generate it in the MIG GUI (see ip.md)"
    exit 1
}
set mcs [expr {[info exists ::env(MCS)] && $::env(MCS) ne "0"}]
if {$mcs} {
    # 32 KB LMB, UART 115200, GPO1 mailbox, GPI1 counter, on the 100 MHz
    # DDR UI clock.
    create_ip -name microblaze_mcs_riscv -vendor xilinx.com -library ip -module_name mcs_riscv
    set_property -dict [list CONFIG.FREQ {100.0} CONFIG.MEMSIZE {32768} \
        CONFIG.USE_UART_RX {1} CONFIG.USE_UART_TX {1} CONFIG.UART_BAUDRATE {115200} \
        CONFIG.USE_GPO1 {1} CONFIG.GPO1_SIZE {32} CONFIG.USE_GPI1 {1} CONFIG.GPI1_SIZE {32} \
    ] [get_ips mcs_riscv]
}
generate_target all [get_ips]
synth_ip [get_ips]

# ---------------------------------------------------------------- sources
add_files -fileset sources_1 [glob $rtl/*.v]
# $readmemb tables: as memory files synthesis finds them by name.
foreach f [glob -nocomplain $rtl/*.bin] {
    add_files -fileset sources_1 $f
    set_property file_type {Memory File} [get_files $f]
}
add_files -fileset sources_1 [list $here/src/board_top.v $here/src/dvi_tx.v \
    $here/src/xadc_monitor.v $here/src/jtag_diag_xilinx.v]
if {$mcs} {
    exec $here/mcs/build.sh
    add_files -norecurse $here/mcs/mcs_fw.elf
    set_property SCOPED_TO_REF mcs_riscv [get_files $here/mcs/mcs_fw.elf]
    set_property SCOPED_TO_CELLS {microblaze_I} [get_files $here/mcs/mcs_fw.elf]
    set_property verilog_define {USE_MCS} [current_fileset]
}
add_files -fileset constrs_1 $here/kintex.xdc
# After the IP constraints: the clock groups need pcie_7x's user clocks.
set_property PROCESSING_ORDER LATE [get_files $here/kintex.xdc]
set_property top board_top [current_fileset]
if {[info exists ::env(CORE_DIV)]} {
    set_property generic "CORE_DIV=$::env(CORE_DIV)" [current_fileset]
}

launch_runs synth_1 -jobs $jobs
wait_on_run synth_1
open_run synth_1
report_utilization -hierarchical -hierarchical_depth 4 -file $build/util_synth_hier.rpt
report_utilization -file $build/util_synth.rpt

launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1
open_run impl_1
report_utilization -file $build/util_impl.rpt
report_timing_summary -max_paths 50 -file $build/timing_impl.rpt

# MCS=1: put the firmware into the block RAM of the written bitstream
# (board_top_fw.bit) with updatemem and the run's memory map; the ELF
# association above is not applied to the routed design's BRAM INITs.
if {$mcs} {
    set run [get_property DIRECTORY [get_runs impl_1]]
    exec updatemem -force -meminfo $run/board_top.mmi -data $here/mcs/mcs_fw.elf \
        -proc mcs/inst/microblaze_I -bit $run/board_top.bit -out $build/board_top_fw.bit
    puts "firmware merged: $build/board_top_fw.bit"
}
