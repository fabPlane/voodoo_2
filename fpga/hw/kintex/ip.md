# Kintex-7 PCIe card (XC7K160T / XC7K325T, FFG676) — prototype

Status: **prototype, not built in Vivado yet, no board chosen.** RTL
generates (`make rtl-kintex`), the PCIe shim passes its SpinalSim test, and
yosys `synth_xilinx` gives the area estimate below. Everything that needs
Vivado (IP generation, port-name check, P&R, timing) is still to do.

## Vivado out-of-context P&R (2026-10-29)

`ooc.tcl`: the fabric alone (Kintex7Voodoo, GOWIN_TMU_COUNT=2,
GOWIN_TEX_LINES=512 like the dock's s197 builds) placed and routed on
xc7k160tffg676-2 with Vivado 2025.2.1; no pcie_7x / MIG / MMCM / pins.

| | Routed | of XC7K160T |
|---|---|---|
| LUT | 39.4k | 38.8% |
| FF | 45.3k | 22.4% |
| Slices | 16.2k | 64% |
| Block RAM | 72.5 tiles | 22.3% |
| DSP | 85 | 14.2% |

Timing met on all clocks, first run, default strategies:

| Clock | Target | WNS | Fmax |
|---|---|---|---|
| core | 100 MHz | +1.130 ns | ~113 MHz (dock: 53.3) |
| pcie | 125 MHz | +2.771 ns | ~190 MHz |
| ddr_ui | 100 MHz | +4.327 ns | ~176 MHz |
| pixel | 25 MHz | +34.7 ns | |

Worst core path: TMU1 texturePath hintTagMem BRAM -> its own write
enable, 11 levels (3 CARRY4), 65% routing.

## Area estimate (yosys synth_xilinx, 2026-10-28)

`yosys synth_xilinx -family xc7 -flatten` on rtl/kintex (no vendor IP, no
P&R, no timing). yosys usually counts more LUTs than Vivado, so treat
these as upper bounds. Dock numbers: Gowin PnR report of s53_2 (2 TMUs).

| | Kintex 1 TMU | Kintex 2 TMUs | XC7K160T has | Dock 2 TMUs (GW5AST-138) |
|---|---|---|---|---|
| LUT (logic) | 37.4k | 46.1k | | 60.7k LUT4 + 13.9k ALU |
| LUT as RAM | 2.0k | 2.6k | | (no LUT-RAM) |
| LUT total | 39.4k (39%) | 48.7k (48%) | 101.4k | 74.6k (54%), CLS 85% |
| FF | 42.3k (21%) | 47.5k (23%) | 202.8k | 53.5k (39%) |
| Block RAM (36K eq.) | 55.5 (17%) | 61.5 (19%) | 325 | 297 x 18K (88%) |
| DSP | 80 (13%) | 120 (20%) | 600 | 104 (35%) |
| Carry (CARRY4) | 2.7k | 3.1k | 25.4k | |

Block RAM is no longer the limit: small FIFOs go to LUT-RAM, and RAMB36
blocks are 72 bits wide, where Gowin spends an 18K block per ~36 bits of
width at any depth.

## What is shared with the Tang dock

`voodoo.kintex.Kintex7Voodoo` wraps the dock fabric `TangMega138kPro`
unchanged (`gowinAdc = false`): TLP BAR target, posted writes, frame DMA,
raw DDR windows, DDR adapter with fill engine, the Voodoo core and its
config (`GOWIN_TMU_COUNT` etc. apply). The host software and BAR map are
the same: BAR0 1 MiB, window register at 0x250, window 31 local registers.
Not available: supply / temperature (0x68-0x70 read 0) and the own DDR3
controller debug port (0x9C reads 0).

Vendor edges:

| Dock (Gowin) | Kintex (this port) |
|---|---|
| PCIe controller TL, 256-bit beats | `pcie_7x` 64-bit AXI-Stream, `XilinxPcieRxToGowin` / `GowinTxToXilinxPcie` |
| Own DDR3 controller / Gowin DDR3 native UI | MIG 7-series native UI (same cmd codes, 256-bit word, mask polarity); `app_addr = ddr_addr << 3` in `board_top.v` |
| DVI_TX_Top IP | `src/dvi_tx.v` (TMDS encoder + OSERDESE2 10:1) |
| GowinMemStylePhase (small RAMs to FFs: no LUT-RAM) | XilinxMemStylePhase (small RAMs to distributed RAM) |
| Gowin ADC | none |

## pcie_7x_0

Created by `build.tcl`: endpoint, Gen1 x4, 64-bit interface, 125 MHz user
clock, BAR0 32-bit non-prefetchable memory 1 MiB. Gen2 x4 needs the 128-bit
interface (TLPs can straddle a beat) or 250 MHz; not done.

Check after generation: the port list in `board_top.v` was written from
PG054 and must match `pcie_7x_0.veo` (Vivado errors on unknown ports).

Open question to settle on the first bitstream: payload byte order. The
shim assumes pcie_7x presents payload dwords like the Gowin block (PCIe
byte order, lowest-addressed byte in 31:24). If BAR0+0x254 reads
0x3ff0ff0f instead of 0x0ffff03f, swap bytes in the shim.

## mig_7series_0 (needs the board)

Generate once in the MIG GUI and save the project file as `ip/mig.prj`:

- DDR3 SDRAM, the board's parts (typically 2 x16 = 32-bit), 400 MHz memory
  clock (DDR3-800; 533 MHz once it works), PHY:controller 4:1, native UI.
  `app_data_width` must come out 256 (the adapter's native word).
- **Ordering: STRICT** (the adapter relies on reads returning in command
  order and not passing earlier writes).
- System clock: No Buffer (board_top feeds the 200 MHz BUFG); reference
  clock: Use System Clock.
- Address mapping: BANK_ROW_COLUMN. Set `MIG_ADDR_WIDTH` in board_top.v to
  the generated `app_addr` width.
- Pins from the board vendor's XDC / UCF.
- MIG instantiates the XADC for temperature tracking; a future supply /
  temperature monitor has to share it (MIG option for external temp input).

## Clocks

| Clock | Source | Frequency |
|---|---|---|
| sys_clk | board oscillator (assumed 200 MHz differential) | 200 MHz |
| core | MMCM CLKOUT0, `CORE_DIV` | 100 MHz (dock: 48-53) |
| pixel / serial | MMCM CLKOUT1 / 2 | 25 / 125 MHz |
| DDR UI | MIG ui_clk | 100 MHz |
| PCIe user | pcie_7x user_clk_out | 125 MHz (dock: 75) |

## Boards

Any Kintex-7 FFG676 board with a PCIe x4+ edge, DDR3 and HDMI (direct TMDS
or a transmitter chip; a transmitter needs its own parallel RGB output
instead of dvi_tx.v). XC7K160T and XC7K325T in FFG676 are pin compatible.
Vivado ML Standard (free) covers the 160T but not the 325T.
