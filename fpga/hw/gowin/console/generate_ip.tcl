# Generate Console 138K vendor IP (PLL, DDR3 native 16-bit, DVI TX).
# Device: GW5AST-LV138PG484AC1/I0 version C. No PCIe.
# Outputs land in ../ip/<name>/ so build_console.tcl can glob them.

set here [file normalize [file dirname [info script]]]
set ipdir [file normalize [file join $here .. ip]]
set work [file normalize /tmp/gowin_console_ip]
file mkdir $work
file mkdir $ipdir

create_project -name console_ip -dir $work -pn GW5AST-LV138PG484AC1/I0 -device_version C -force

proc dump_ip {name} {
    puts "==== properties $name ===="
    if {[catch {report_property [get_ips $name]} err]} {
        puts $err
    }
}

proc copy_generated {name} {
    global ipdir work
    set dest [file join $ipdir $name]
    file mkdir $dest
    foreach dir [list \
        [file join $work console_ip src $name] \
        [file join $work src $name] \
        [file join $work console_ip/$name] \
    ] {
        if {[file isdirectory $dir]} {
            foreach f [glob -nocomplain -directory $dir *] {
                if {[file isfile $f]} {
                    file copy -force $f $dest
                }
            }
        }
    }
    # Also pick up any .v the generator dropped next to the project.
    foreach f [glob -nocomplain -directory $work/$name *] {
        if {[file isfile $f]} {
            file copy -force $f $dest
        }
    }
}

# --- Sys PLL: 50 MHz -> 100 MHz (clkout0) + 400 MHz (clkout2, enclk2=pll_stop)
create_ipc -name clock_plladv -module_name Gowin_PLL_Sys -language Verilog \
    -file_name Gowin_PLL_Sys -dir [file join $ipdir Gowin_PLL_Sys] -force
dump_ip Gowin_PLL_Sys
set_property -dict {
    CONFIG.CLKIN_FREQ 50
    CONFIG.CLKOUT0_FREQ 100
    CONFIG.CLKOUT0_TOLERANCE 0.0
    CONFIG.CLKOUT2_EN true
    CONFIG.CLKOUT2_FREQ 400
    CONFIG.CLKOUT2_TOLERANCE 0.0
    CONFIG.LOCK_EN true
    CONFIG.PLL_Reset true
} [get_ips Gowin_PLL_Sys]
# 138K DDR3 IP gates memory_clk via PLL enclk2.
catch {set_property CONFIG.ClockEnablePorts true [get_ips Gowin_PLL_Sys]}
generate_target -force [get_ips Gowin_PLL_Sys]
copy_generated Gowin_PLL_Sys

# --- HDMI PLL: 50 MHz -> 126 MHz (5x 25.2 MHz 640x480)
create_ipc -name clock_plladv -module_name Gowin_PLL_Hdmi -language Verilog \
    -file_name Gowin_PLL_Hdmi -dir [file join $ipdir Gowin_PLL_Hdmi] -force
set_property -dict {
    CONFIG.CLKIN_FREQ 50
    CONFIG.CLKOUT0_FREQ 126
    CONFIG.CLKOUT0_TOLERANCE 0.2
    CONFIG.LOCK_EN true
    CONFIG.PLL_Reset false
} [get_ips Gowin_PLL_Hdmi]
generate_target -force [get_ips Gowin_PLL_Hdmi]
copy_generated Gowin_PLL_Hdmi

# --- DDR3 native UI, one x16 chip (second chip unused on Console 138K)
create_ipc -name ddr3 -module_name DDR3_Memory_Interface_Top -language Verilog \
    -file_name ddr3_memory_interface -dir [file join $ipdir DDR3_Memory_Interface] -force
dump_ip DDR3_Memory_Interface_Top
set_property -dict {
    CONFIG.User_Interface Controller
    CONFIG.Memory_Type Components
    CONFIG.Memory_Clock 400
    CONFIG.CLK_Ratio 1:4
    CONFIG.Dq_Width 16
    CONFIG.Dram_Width 16
    CONFIG.Ecc false
    CONFIG.Rank_Address 1
    CONFIG.Bank_Address 3
    CONFIG.Row_Address 15
    CONFIG.Column_Address 10
    CONFIG.Burst_Mode 8
    CONFIG.Burst_Type SEQ
    CONFIG.CAS_Latency 6
    CONFIG.CW_Latency 5
    CONFIG.Additive_Latency 0
    CONFIG.Wrire_Recovery 6
    CONFIG.User_Refresh false
    CONFIG.SIM false
} [get_ips DDR3_Memory_Interface_Top]
catch {set_property CONFIG.tRFC_Period 260000 [get_ips DDR3_Memory_Interface_Top]}
generate_target -force [get_ips DDR3_Memory_Interface_Top]
copy_generated DDR3_Memory_Interface

# --- DVI TX
create_ipc -name dvi_tx -module_name DVI_TX_Top -language Verilog \
    -file_name dvi_tx -dir [file join $ipdir DVI_TX] -force
generate_target -force [get_ips DVI_TX_Top]
copy_generated DVI_TX

puts "==== generated IP ===="
foreach d [glob -nocomplain -directory $ipdir *] {
    puts $d
    foreach f [glob -nocomplain -directory $d *] {
        puts "  [file tail $f]"
    }
}
