create_clock -name core -period 25.0 [get_ports clk]
create_clock -name hdmi -period 39.7 [get_ports io_hdmi_clock]
set_clock_groups -asynchronous -group core -group hdmi
set_false_path -from [get_ports resetn]
set ins [delete_from_list [all_inputs] [get_ports {clk io_hdmi_clock resetn}]]
set_input_delay 5.0 -clock core $ins
set_output_delay 5.0 -clock core [all_outputs]
set_load 0.02 [all_outputs]
set_driving_cell -lib_cell gf180mcu_fd_sc_mcu7t5v0__buf_4 -pin Z $ins
