# Gowin IP cores for the Tang Mega 138K Pro Dock

Generate these in Gowin EDA → IP Core Generator and drop the Verilog into
`fpga/hw/gowin/ip/<name>/` so `build.tcl` picks them up. Port names must match
`src/board_top.v`. If a generated wrapper uses longer names (for example
`PCIE_Controller_Top_pcie_rstn_i`), add a thin Verilog shim.

Device: `GW5AST-LV138FPG676AES` / `GW5AST-138B` for the Pro Dock.
PLLs can be generated with `hw/gowin/generate_ip.sh` (GowinModgen). DDR3,
DVI TX, and PCIe SerDes for the first bitstream are Sipeed's
already-generated cores for `GW5AST-LV138FPG676` (from
`TangMega-138KPro-example` ddr/hdmi and the Pro `pcie_dma_demo` `.gar`).
`board_top.v` instantiates `SerDes_Top` (prefixed ports). That PCIe IP
enumerates as `22c2:1100`; host tools also accept `10ee:0007`.
Regenerate with `generate_vendor_ip.tcl` when Sipeed's float license
holds long enough. This machine's Standard 1.9.12.04 is
`$HOME/gowin/standard-1.9.12.04` (`106.55.34.119:10559`).

## Gowin_PLL_Sys

- Module name: `Gowin_PLL_Sys`
- Input: 50 MHz (`clkin`)
- `clkout0`: 100 MHz (PCIe TLP clock)
- `clkout1`: 400 MHz (DDR3 `memory_clk`)
- Output `lock`

## Gowin_PLL_Hdmi

- Module name: `Gowin_PLL_Hdmi`
- Input: 50 MHz
- `clkout0`: 126 MHz (DVI serial clock, 5× 25.2 MHz)
- Pixel clock is `CLKDIV` ÷5 of that, already instantiated in `board_top.v`

## PCIE_Controller_Top

Gowin PCI Express Controller IP (IPUG1020):

- Endpoint, Gen2, x4
- TLP clock 100 MHz, data width 256
- BAR0: 32-bit memory, **largest available** (1 MiB on current IP). Host tools
  use the window register at BAR+0x250 to reach the 16 MiB Voodoo map.
- Vendor/device ID: pick something unused; 0x10ee / 0x0007 is fine for bring-up
- If the generated module is wrapped as `SerDes_Top`, either rename it or
  instantiate `SerDes_Top` in `board_top.v`

PCIe SerDes locations are inside the IP. PERST# is pin L23 (`pcie_rstn`).
Cold-plug the dock; the Sipeed demo does not hot-plug.

## DDR3_Memory_Interface_Top

Gowin DDR3 Memory Interface IP, **native user interface** (not AXI):

- 32-bit DDR3, ~1 GiB (two x16), SSTL15
- Native data 256 bits, address 29 bits
- `memory_clk` 400 MHz, `clk` 50 MHz
- Match the H5TQ4G63EFR (or the chips actually on the module) timings
- Pin names must match `O_ddr_*` / `IO_ddr_*` in the CST

Gowin changed this IP after EDA 1.9.10. Generate it with the same IDE that
runs `gw_sh`.

## DVI_TX_Top

Gowin DVI/HDMI TX IP:

- Parallel RGB 888 in, TMDS out
- `I_serial_clk` = 5× pixel, `I_rgb_clk` = pixel
