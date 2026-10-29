# Generates pcie_7x_0 with build.tcl's settings and writes its instantiation
# template, to check board_top.v's port list.
set here [file normalize [file dirname [info script]]]
create_project -force pcie_check $here/build/pcie_check -part xc7k160tffg676-2
create_ip -name pcie_7x -vendor xilinx.com -library ip -module_name pcie_7x_0
set_property CONFIG.mode_selection {Advanced} [get_ips pcie_7x_0]
set_property CONFIG.en_ext_clk {false} [get_ips pcie_7x_0]
set_property -dict [list CONFIG.Maximum_Link_Width {X4} CONFIG.Link_Speed {2.5_GT/s} \
    CONFIG.Interface_Width {64_bit} CONFIG.User_Clk_Freq {125} CONFIG.Bar0_Enabled {true} \
    CONFIG.Bar0_Type {Memory} CONFIG.Bar0_64bit {false} CONFIG.Bar0_Prefetchable {false} \
    CONFIG.Bar0_Scale {Megabytes} CONFIG.Bar0_Size {1} CONFIG.Vendor_ID {10EE} \
    CONFIG.Device_ID {0007} CONFIG.Class_Code_Base {03} CONFIG.Class_Code_Sub {80} \
    CONFIG.Max_Payload_Size {256_bytes} CONFIG.Ref_Clk_Freq {100_MHz}] [get_ips pcie_7x_0]
generate_target instantiation_template [get_ips pcie_7x_0]
puts "VEO: [get_files -of_objects [get_ips pcie_7x_0] *.veo]"
