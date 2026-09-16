# Vivado command-line build for AMD Artix-7 AC701 (XC7A200T-2FBG676).
# Requires Vivado (vivado -mode batch) and the IP listed in ip.md.

create_project -force spinalvoodoo_ac701 . -part xc7a200tfbg676-2
set_property target_language Verilog [current_project]

add_files -fileset sources_1 [file normalize ../../rtl/artix/AmdAc701.v]
foreach f [glob -nocomplain ../../rtl/artix/*.v] {
    add_files -fileset sources_1 [file normalize $f]
}
add_files -fileset sources_1 [file normalize src/board_top.v]
add_files -fileset sources_1 [file normalize src/adv7511_i2c.v]
foreach f [glob -nocomplain ip/*.xci] {
    add_files -fileset sources_1 [file normalize $f]
}

add_files -fileset constrs_1 [file normalize ac701.xdc]

set_property top board_top [current_fileset]
update_compile_order -fileset sources_1

launch_runs synth_1 -jobs 8
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
