set D /home/hyper/.cache/asic-est
read_db $D/sta/placed_ks_rt.odb
read_liberty $D/gf180_7t_tt5v.lib
read_sdc $D/sta/core.sdc
set_wire_rc -signal -layer Metal2
set_wire_rc -clock -layer Metal4
estimate_parasitics -placement
report_checks -path_delay max -group_count 200000 -endpoint_count 1 -format end -digits 3 > $D/sta/ends_rt.txt
