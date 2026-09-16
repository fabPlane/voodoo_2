# Xilinx IP for the AMD Artix-7 AC701 (XC7A200T-2FBG676)

Generate these in Vivado and drop the output next to `build.tcl` so
`source build.tcl` can pick them up. Port names must match `src/board_top.v`.
Pinout is LiteX `xilinx_ac701`, not guessed.

Device: `xc7a200t-fbg676-2`.

## axi_pcie_0

AXI Memory Mapped To PCI Express (PG055):

- Endpoint, Gen2, x4
- REFCLK 100 MHz on F11/E11
- BAR0: 32-bit memory, **16 MiB** (the full Voodoo map; no 0x250 window)
- AXI master data width 32, ID width 4
- Vendor/device ID: 0x10ee / 0x0007 is fine for bring-up
- If the generated wrapper uses longer names, add a thin Verilog shim

PERST# is pin M20 (`pcie_rst_n`, LVCMOS25). Cold-plug the card.

## mig_7series_0

MIG 7-series, AXI:

- 32-bit DDR3, first four byte lanes of the SODIMM (LiteX `PHYPadsReducer`
  `[0, 1, 2, 3]` on MT8JTF12864), SSTL15, banks 33/34/35
- Memory clock 400 MHz (DDR3-800), 4:1, AXI UI 100 MHz, data 128 bits
- Match the LiteX pinout in `ac701.xdc` (copy into the MIG pin editor)
- `INTERNAL_VREF 0.750` on banks 33, 34 and 35

## axi_mem_intercon

AXI interconnect, 2 slaves (framebuffer + texture, 32-bit) to one 128-bit
MIG master. Enable width conversion. Clock is MIG `ui_clk`.

If you skip the interconnect for a first bitstream, tie the texture AXI
inputs idle and connect only `memFb` through an AXI 32-to-128 converter.

## ADV7511

No Xilinx IP. `src/adv7511_i2c.v` bit-bangs the I2C mux (PCA9548 0x74,
channel 5 / 0x20, UG952) then the Analog power-up into 0x39. Pixel clock is
25.175 MHz from the 156.25 MHz oscillator in `board_top.v` (640x480).
`IIC_MUX_RESET_B` (R17) is released with CPU reset.
