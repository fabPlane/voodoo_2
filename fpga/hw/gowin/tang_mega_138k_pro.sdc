create_clock -name clk50 -period 20 [get_ports {clk50}]
# "clk100" is the PCIe TLP clock, now 75 MHz (see Gowin_PLL_Sys.v).
create_clock -name clk100 -period 13.333 [get_nets {clk100}]
create_clock -name clk400 -period 2.5 [get_nets {clk400}]
create_clock -name clk_core -period 21.667 [get_nets {clk_core}]
create_clock -name jtag_tck -period 100 [get_nets {u_diag/jtag_tck}]
create_clock -name clk_hdmi_serial -period 8 [get_nets {clk_hdmi_serial}]
create_clock -name clk_hdmi_pixel -period 40 [get_nets {clk_hdmi_pixel}]
# DDR3 UI clock is memory_clk / 4. Name it so PnR can time the fabric.
create_clock -name ddr_clk_out -period 10 [get_pins {u_ddr3/gw3_top/u_ddr_phy_top/fclkdiv/CLKOUT}]
# Gowin SDC has no line continuations; keep this on one line.
set_clock_groups -asynchronous -group [get_clocks {clk50}] -group [get_clocks {clk100}] -group [get_clocks {clk400}] -group [get_clocks {ddr_clk_out}] -group [get_clocks {clk_core}] -group [get_clocks {clk_hdmi_serial}] -group [get_clocks {clk_hdmi_pixel}] -group [get_clocks {jtag_tck}]
# More than the default 25 worst paths, per clock, for timing closure work.
report_timing -setup -max_paths 200 -max_common_paths 1 -to_clock [get_clocks {clk_core}]
report_timing -setup -max_paths 200 -max_common_paths 1 -to_clock [get_clocks {clk100}]
report_timing -setup -max_paths 200 -max_common_paths 1 -to_clock [get_clocks {ddr_clk_out}]
report_timing -setup -max_paths 100 -max_common_paths 1 -to_clock [get_clocks {clk_hdmi_pixel}]
