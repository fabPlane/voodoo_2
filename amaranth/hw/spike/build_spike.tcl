# Phase 0.3 Gowin compatibility spike: synth + PnR of the Amaranth-generated
# SpikeBlinky on the Sipeed Tang Retro Console 138K.
#
#   cd amaranth && make rtl-spike               # -> rtl/SpikeBlinky.v
#   cd amaranth/hw/spike && mkdir -p build && cd build && gw_sh ../build_spike.tcl
#
# gw_sh always writes impl/ into the current directory, hence the `cd build`;
# the paths below are resolved against this script, not the cwd.
#
# Run this on hyper-ms-lin, and only when no other gw_sh build is running
# (`pgrep -af '[/]gw_sh build'`). See ../../../AGENTS.md.
#
# Device string and the -use_*_as_gpio options are copied from
# fpga/hw/gowin-open/vendor-reference/build_blinky.tcl so that any difference
# in the result is down to the Verilog, not the project settings.

set here [file normalize [file dirname [info script]]]

set_device GW5AST-LV138PG484AC1/I0 -device_version C

add_file -type verilog [file normalize [file join $here .. .. rtl SpikeBlinky.v]]
add_file -type cst [file join $here spike.cst]

set_option -top_module SpikeBlinky
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_mspi_as_gpio 1
set_option -use_ready_as_gpio 1
set_option -use_done_as_gpio 1
set_option -use_i2c_as_gpio 1
set_option -use_cpu_as_gpio 1
set_option -bit_compress 0
set_option -bit_encrypt 0
set_option -bit_security 0
set_option -output_base_name amaranth_spike

run all
