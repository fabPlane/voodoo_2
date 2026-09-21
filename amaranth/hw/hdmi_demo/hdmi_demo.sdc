// Timing constraints: the three clocks of the Console build that this demo
// keeps (fpga/hw/gowin/console/tang_console_138k.sdc), same names and pins.
// The PLL actually makes 125 MHz (IDIV 1, MDIV 20, ODIV0 8 from 50 MHz), so
// the 8.0 ns / 40.0 ns periods are exact.
create_clock -name clk50 -period 20 -waveform {0 10} [get_ports {clk50}]
create_clock -name clk_hdmi_serial -period 8.0 -waveform {0 4} [get_pins {u_pll_hdmi/u_pll/PLL_inst/CLKOUT0}]
create_clock -name clk_hdmi_pixel -period 40.0 -waveform {0 20} [get_pins {u_hdmi_div/CLKOUT}]
set_clock_groups -asynchronous -group [get_clocks {clk50}] -group [get_clocks {clk_hdmi_serial}]
set_clock_groups -asynchronous -group [get_clocks {clk50}] -group [get_clocks {clk_hdmi_pixel}]
set_clock_groups -asynchronous -group [get_clocks {clk_hdmi_serial}] -group [get_clocks {clk_hdmi_pixel}]
