create_clock -name clk50 -period 20 [get_ports {clk50}]
# "clk100" is the PCIe TLP clock, now 75 MHz (see Gowin_PLL_Sys.v).
create_clock -name clk100 -period 13.333 [get_nets {clk100}]
create_clock -name clk400 -period 2.5 [get_nets {clk400}]
# clk_core: 48 MHz = 1200 MHz VCO / 25 (ip/Gowin_PLL_Sys/Gowin_PLL_Sys_mod.v
# ODIV1_SEL = 25). B112 ran 40 MHz; B124 48 MHz (GLQuake 30.1 ms/frame, was
# 37.0 at 40 MHz). Core Fmax ~46-51 MHz since B122; 3 of 6 placements close.
create_clock -name clk_core -period 20.833 [get_nets {clk_core}]
create_clock -name jtag_tck -period 100 [get_nets {u_diag/jtag_tck}]
create_clock -name clk_hdmi_serial -period 8 [get_nets {clk_hdmi_serial}]
create_clock -name clk_hdmi_pixel -period 40 [get_nets {clk_hdmi_pixel}]
# DDR3 UI clock is memory_clk / 4. Name it so PnR can time the fabric.
create_clock -name ddr_clk_out -period 10 [get_pins {u_ddr3/u_clkdiv/CLKOUT}]
# Gowin SDC has no line continuations; keep this on one line.
set_clock_groups -asynchronous -group [get_clocks {clk50}] -group [get_clocks {clk100}] -group [get_clocks {clk400}] -group [get_clocks {ddr_clk_out}] -group [get_clocks {clk_core}] -group [get_clocks {clk_hdmi_serial}] -group [get_clocks {clk_hdmi_pixel}] -group [get_clocks {jtag_tck}]
# More than the default 25 worst paths, per clock, for timing closure work.
report_timing -setup -max_paths 200 -max_common_paths 1 -to_clock [get_clocks {clk_core}]
report_timing -setup -max_paths 200 -max_common_paths 1 -to_clock [get_clocks {clk100}]
report_timing -setup -max_paths 200 -max_common_paths 1 -to_clock [get_clocks {ddr_clk_out}]
report_timing -setup -max_paths 100 -max_common_paths 1 -to_clock [get_clocks {clk_hdmi_pixel}]

# The DDR PHY's init-complete flop releases the DDR-domain reset once, after
# calibration, while nothing is in flight: not a timed path.
set_false_path -from [get_regs {u_ddr3/u_ctrl/cal_done_s*}]
# Own DDR3 debug port: address / data are set long before the host reads
# back or strobes a write (window 31 0x98 / 0x9C): not timed paths.
set_false_path -from [get_regs {u_voodoo/pcie_ddrDbgCmd_buffercc/*}]
set_false_path -from [get_regs {u_voodoo/pcie_ddrDbgWdata_buffercc/*}]
# Own DDR3 write timing options: written only through the debug port,
# static while traffic runs.
set_false_path -from [get_regs {u_ddr3/u_ctrl/wr_dly*}]
set_false_path -from [get_regs {u_ddr3/u_ctrl/opt_wr_shift*}]
set_false_path -from [get_regs {u_ddr3/u_ctrl/p_wdq_dly*}]
