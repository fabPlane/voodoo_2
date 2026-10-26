# Core clock target 10 ns (100 MHz); HDMI pixel clock 74.25 MHz (720p).
create_clock -name core -period 10.0 [get_ports coreClk]
create_clock -name hdmi -period 13.468 [get_ports io_hdmi_clock]
set_clock_groups -asynchronous -group core -group hdmi
set_false_path -from [get_ports {coreReset io_hdmi_reset}]
set ins [delete_from_list [all_inputs] [get_ports {coreClk io_hdmi_clock coreReset io_hdmi_reset}]]
set_input_delay 2.0 -clock core $ins
set_output_delay 2.0 -clock core [all_outputs]
set_load 0.02 [all_outputs]
set_driving_cell -lib_cell gf180mcu_fd_sc_mcu7t5v0__buf_4 -pin Z $ins
