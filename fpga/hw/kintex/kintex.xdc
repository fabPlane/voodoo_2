# Kintex-7 PCIe card timing + pins. Timing is generic; every PACKAGE_PIN
# below is a placeholder (marked TODO) until a board is chosen: take them
# from the board's schematic / vendor XDC. The PCIe lanes and refclk must be
# on the GTX quad the card edge is wired to; MIG writes its own DDR3 pin
# constraints into the IP (mig.prj), so DDR3 pins are not listed here.

# ---------------------------------------------------------------- clocks
create_clock -name sys_clk -period 5.000 [get_ports sys_clk_p]
create_clock -name pcie_refclk -period 10.000 [get_ports pcie_clk_p]
# MMCM outputs (core, pixel, serial), MIG ui_clk and the PCIe user clock
# are derived by Vivado. All domains cross only through synchronisers /
# async FIFOs (as on the Gowin dock), so they are asynchronous groups.
set_clock_groups -asynchronous \
    -group [get_clocks -of_objects [get_pins mmcm/CLKOUT0]] \
    -group [get_clocks -of_objects [get_pins {mmcm/CLKOUT1 mmcm/CLKOUT2}]] \
    -group [get_clocks -include_generated_clocks -of_objects [get_pins mig/ui_clk]] \
    -group [get_clocks -include_generated_clocks pcie_refclk]

set_false_path -from [get_ports sys_rst_n]
set_false_path -from [get_ports pcie_perst_n]

# ---------------------------------------------------------------- pins (TODO: board)
# set_property PACKAGE_PIN <pin> [get_ports sys_clk_p]
# set_property IOSTANDARD DIFF_SSTL15 [get_ports {sys_clk_p sys_clk_n}]
# set_property PACKAGE_PIN <pin> [get_ports sys_rst_n]
# set_property IOSTANDARD LVCMOS33 [get_ports sys_rst_n]

# PCIe: refclk on the quad's MGTREFCLK pair, lanes on its GTXE2_CHANNELs
# (Vivado places the lanes from the IP's LOCs once the quad is set in the
# pcie_7x GUI). PERST# on any 3.3 V pin.
# set_property PACKAGE_PIN <pin> [get_ports pcie_clk_p]
# set_property PACKAGE_PIN <pin> [get_ports pcie_perst_n]
# set_property IOSTANDARD LVCMOS33 [get_ports pcie_perst_n]
# set_property PULLUP true [get_ports pcie_perst_n]

# HDMI: TMDS_33 needs a 3.3 V bank.
# set_property PACKAGE_PIN <pin> [get_ports tmds_clk_p]
# set_property PACKAGE_PIN <pin> [get_ports {tmds_d_p[0]}]
# set_property PACKAGE_PIN <pin> [get_ports {tmds_d_p[1]}]
# set_property PACKAGE_PIN <pin> [get_ports {tmds_d_p[2]}]
# set_property IOSTANDARD TMDS_33 [get_ports {tmds_clk_p tmds_clk_n tmds_d_p[*] tmds_d_n[*]}]

# set_property PACKAGE_PIN <pin> [get_ports {led[0]}]
# set_property PACKAGE_PIN <pin> [get_ports {led[1]}]
# set_property PACKAGE_PIN <pin> [get_ports {led[2]}]
# set_property PACKAGE_PIN <pin> [get_ports {led[3]}]
# set_property IOSTANDARD LVCMOS33 [get_ports {led[*]}]

set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 33 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
