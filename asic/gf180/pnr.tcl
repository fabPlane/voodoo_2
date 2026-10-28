# pnr.tcl: place and route of the split die (die.v in the working directory)
# on the wafer.space 1x1 slot core (template slot_1x1.yaml: die 3932 x 5122,
# core 442..3490 x 442..4680 um = 12.92 mm²). Pins on the core boundary stand
# in for the pad ring. Power grid after the template (Metal4 / Metal5 straps
# 5 um wide, 75 um pitch, Metal1 rails). Fit check: utilization, routing
# overflow / DRC, typical-corner timing.
set D /home/hyper/.cache/asic-est
set W [pwd]
set threads 24
set_thread_count $threads
read_lef $D/dl/lef/tech_5LM_1TM_9K.lef
foreach f [glob $D/dl/lef/gf180mcu_fd_sc_mcu7t5v0__*.lef] { read_lef $f }
read_liberty $D/gf180_7t_tt5v.lib
read_verilog $W/die.v
link_design [expr {[info exists ::env(TOP)] ? $::env(TOP) : "voodoo_split_die"}]
read_sdc $D/split/split.sdc
set_wire_rc -signal -layer Metal2
set_wire_rc -clock -layer Metal4

proc stage {tag} {
  puts "==== $tag"
  report_design_area
  report_wns
  report_tns
  report_checks -path_delay max -digits 2 -format end
  flush stdout
}

initialize_floorplan -die_area {0 0 3932 5122} -core_area {442 442 3490 4680} -site GF018hv5v_mcu_sc7
make_tracks
place_pins -hor_layers Metal3 -ver_layers Metal2
tapcell -distance 50 -tapcell_master gf180mcu_fd_sc_mcu7t5v0__filltie -endcap_master gf180mcu_fd_sc_mcu7t5v0__endcap

add_global_connection -net VDD -pin_pattern {^VDD$} -power
add_global_connection -net VSS -pin_pattern {^VSS$} -ground
set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name core
add_pdn_stripe -grid core -layer Metal1 -width 0.6 -followpins
add_pdn_stripe -grid core -layer Metal4 -width 5 -pitch 75 -spacing 1 -offset 37
add_pdn_stripe -grid core -layer Metal5 -width 5 -pitch 75 -spacing 1 -offset 37
add_pdn_connect -grid core -layers {Metal1 Metal4}
add_pdn_connect -grid core -layers {Metal4 Metal5}
pdngen
stage "floorplan"

# Placement density: utilization plus some room.
set util [expr {[rsz::design_area] * 1e12 / (3048.0 * 4238.0)}]
set dens [expr {min(0.97, $util + 0.08)}]
puts "utilization (cells / core) [format %.3f $util], placement density $dens"
global_placement -density $dens -routability_driven
estimate_parasitics -placement
repair_design
detailed_placement
stage "placed"
write_db $W/placed.odb

# Trees for the clock and the inverted clocks feeding the latch RAMs' clock
# gates only: each gate's output drives one byte of latches next to it (a
# hardened RAM block would not be touched either).
set ctsNets {}
foreach n [concat [get_nets clk] [get_nets -hierarchical *nclk*]] { lappend ctsNets [get_full_name $n] }
puts "CTS roots: [llength $ctsNets]"
clock_tree_synthesis -clk_nets [join $ctsNets " "] -root_buf gf180mcu_fd_sc_mcu7t5v0__clkbuf_16 \
  -buf_list {gf180mcu_fd_sc_mcu7t5v0__clkbuf_4 gf180mcu_fd_sc_mcu7t5v0__clkbuf_8 gf180mcu_fd_sc_mcu7t5v0__clkbuf_16}
set_propagated_clock [all_clocks]
estimate_parasitics -placement
repair_timing -setup
detailed_placement
check_placement
stage "cts + repair_timing"
write_db $W/cts.odb

set_routing_layers -signal Metal2-Metal5 -clock Metal2-Metal5
global_route -allow_congestion -verbose
estimate_parasitics -global_routing
stage "global route"
write_db $W/grt.odb

detailed_route -output_drc $W/drc.rpt -droute_end_iter 30 -verbose 1
stage "detailed route"
write_db $W/routed.odb
puts "DONE"
