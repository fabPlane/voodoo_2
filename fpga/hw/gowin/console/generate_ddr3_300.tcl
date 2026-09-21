# Generate only the Console 138K DDR3 controller for the 300 MHz clock
# produced by Gowin_PLL_Sys. This avoids using the copied 297 MHz controller
# with mismatched training and delay parameters.

set here [file normalize [file dirname [info script]]]
set ipdir [file normalize [file join $here .. ip]]
set work [file normalize /tmp/gowin_console_ddr3_300]
file delete -force $work
file mkdir $work
file mkdir $ipdir

create_project -name console_ddr3_300 -dir $work \
    -pn GW5AST-LV138PG484AC1/I0 -device_version C -force

set dest [file join $ipdir DDR3_Memory_Interface_300]
file delete -force $dest
file mkdir $dest

create_ipc -name ddr3 -module_name DDR3_Memory_Interface_Top \
    -language Verilog -file_name ddr3_memory_interface \
    -dir $dest -force

set_property -dict {
    CONFIG.User_Interface Controller
    CONFIG.Memory_Type Components
    CONFIG.Memory_Clock 300
    CONFIG.CLK_Ratio 1:4
    CONFIG.Dq_Width 16
    CONFIG.Dram_Width 16
    CONFIG.Ecc false
    CONFIG.Rank_Address 1
    CONFIG.Bank_Address 3
    CONFIG.Row_Address 14
    CONFIG.Column_Address 10
    CONFIG.Burst_Mode 8
    CONFIG.Burst_Type SEQ
    CONFIG.CAS_Latency 5
    CONFIG.CW_Latency 5
    CONFIG.Additive_Latency 0
    CONFIG.Wrire_Recovery 6
    CONFIG.User_Refresh true
    CONFIG.SIM false
} [get_ips DDR3_Memory_Interface_Top]
catch {set_property CONFIG.tRFC_Period 160000 [get_ips DDR3_Memory_Interface_Top]}

generate_target [get_ips DDR3_Memory_Interface_Top]
report_property [get_ips DDR3_Memory_Interface_Top]
puts "DDR3_300_DEST=$dest"
