# Out-of-context implementation of the Voodoo fabric alone (no pcie_7x,
# MIG, MMCM or pins): real utilisation and per-clock Fmax on the part,
# before a board is chosen.
#   vivado -mode batch -source ooc.tcl -tclargs <rtl dir> <out dir> [part] [core MHz]
set rtl [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set part [expr {[llength $argv] > 2 ? [lindex $argv 2] : "xc7k160tffg676-2"}]
set coreMhz [expr {[llength $argv] > 3 ? [lindex $argv 3] : 100.0}]
set_param general.maxThreads 8
file mkdir $out
# $readmemb paths in the generated Verilog are relative to the run directory.
foreach f [glob -nocomplain $rtl/*.bin] { file copy -force $f $out }
cd $out

read_verilog [glob $rtl/*.v]
set corePeriod [format %.3f [expr {1000.0 / $coreMhz}]]
set xdc [open $out/ooc.xdc w]
puts $xdc "create_clock -name core -period $corePeriod \[get_ports coreClk\]"
puts $xdc "create_clock -name pcie -period 8.000 \[get_ports pcieClk\]"
puts $xdc "create_clock -name ddr_ui -period 10.000 \[get_ports clk\]"
puts $xdc "create_clock -name pixel -period 40.000 \[get_ports hdmiClk\]"
puts $xdc "set_clock_groups -asynchronous -group core -group pcie -group ddr_ui -group pixel"
close $xdc
read_xdc -mode out_of_context $out/ooc.xdc

synth_design -top Kintex7Voodoo -part $part -mode out_of_context -flatten_hierarchy rebuilt
report_utilization -file $out/util_synth.rpt
report_utilization -hierarchical -hierarchical_depth 5 -file $out/util_synth_hier.rpt
write_checkpoint -force $out/synth.dcp
opt_design
place_design
phys_opt_design
route_design
write_checkpoint -force $out/routed.dcp
report_utilization -file $out/util_routed.rpt
report_timing_summary -max_paths 30 -file $out/timing.rpt
report_clock_utilization -file $out/clock_util.rpt
foreach clk {core pcie ddr_ui pixel} {
    report_timing -group $clk -max_paths 20 -nworst 1 -file $out/paths_$clk.rpt
}
