set D /home/hyper/.cache/asic-est
read_lef $D/dl/lef/tech_5LM_1TM_9K.lef
foreach f [glob $D/dl/lef/gf180mcu_fd_sc_mcu7t5v0__*.lef] { read_lef $f }
read_liberty $D/gf180_7t_tt5v.lib
read_verilog $D/sta/core_gf180_ks.v
link_design Core
read_sdc $D/sta/core.sdc

proc rpt {tag} {
  puts "==== $tag"
  report_wns
  report_tns
  foreach c {core hdmi} {
    puts "-- $c"
    report_checks -path_delay max -to [all_registers -clock $c -data_pins] -digits 2 -format end
  }
  report_checks -path_delay max -digits 2 -fields {fanout cap slew}
  flush stdout
}

# 1. Zero-wire-load STA (cell delays only, ideal clocks)
repair_design
rpt "no wires, after repair_design"

# 2. Placement-based parasitics after high-fanout buffering
initialize_floorplan -utilization 50 -aspect_ratio 1 -core_space 10 -site GF018hv5v_mcu_sc7
make_tracks
place_pins -hor_layers Metal3 -ver_layers Metal2 -random
global_placement -density 0.6 -skip_initial_place
set_wire_rc -signal -layer Metal2
set_wire_rc -clock -layer Metal4
estimate_parasitics -placement
rpt "placed, before repair"
repair_design
estimate_parasitics -placement
rpt "placed, after repair_design"
report_design_area
write_db $D/sta/placed_ks.odb
repair_timing -setup
estimate_parasitics -placement
rpt "placed, after repair_timing"
report_design_area
write_db $D/sta/placed_ks_rt.odb
