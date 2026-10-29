# Kintex-7 PCIe card (XC7K160T / XC7K325T, FFG676) — prototype

Status: **prototype, builds to a bitstream in Vivado 2025.2.1, no board
chosen** (pins are a representative FFG676 pinout). Every dock feature is
ported: the full Voodoo configuration (KINTEX_FULL), PCIe, DDR3, HDMI,
supply / temperature monitor, JTAG diagnostics and the soft-CPU counterpart
of the AE350. Not tested on hardware; the PCIe shim and the board_top
helpers pass their simulations (SpinalSim, `sim/run.sh`).

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

Second run at a 120 MHz core target: met, core WNS +0.414 ns (~126 MHz),
pcie +2.607, ddr_ui +4.532. Same worst path (12 levels).
At 140 MHz: met by +0.006 ns (the ceiling with default strategies);
pcie +2.253, ddr_ui +4.831. The 20 worst core paths: TMU hintTagMem
write enable and triangle setup (scanX / yrange registers).

Worst core path: TMU1 texturePath hintTagMem BRAM -> its own write
enable, 11 levels (3 CARRY4), 65% routing.

### Full feature set (KINTEX_FULL, rtl/kintex-full)

`KINTEX_FULL=1 GOWIN_FB_LINESPLIT=1 GOWIN_FB_WLINESPLIT=1
GOWIN_DDR_STAGERS=1 GOWIN_DDR_STATS=1`: the dock's rtl_b199 (two TMUs,
1024-line caches on both with epoch validity, fb line split) plus what
the GW5AST build drops for area: Voodoo 2 setup engine, 8 texture fill
slots, triangleCMD register copy, hint lineValid checks, DDR read
stagers and stats.

| | Routed | of XC7K160T |
|---|---|---|
| LUT | 45.3k (3.5k as LUT-RAM) | 44.7% |
| FF | 53.1k | 26.2% |
| Slices | 18.9k | 74.7% |
| Block RAM | 110.5 tiles | 34.0% |
| DSP | 87 | 14.5% |

Setup engine: ~3.7k LUT / 2.3k FF of that.
At 120 MHz core: met, core +0.087 ns, pcie +2.102, ddr_ui +2.825,
hold clean. Worst core path: PCI FIFO register data -> gamma LUT-RAM
write address (0 logic levels: fanout / distance).
At 135 MHz core: also met, core +0.017 ns, pcie +2.166, ddr_ui +4.257,
hold clean; worst paths are again the TMU tag BRAM -> fill mask /
hintTagMem enable (12 levels). So the full feature set costs ~5 MHz
of the 140 MHz ceiling.

## Full board build: fabric + pcie_7x + MIG + pins (2026-10-29)

`RTL=../../rtl/kintex-full CORE_DIV=8.375 vivado -mode batch -source
build.tcl` (core 119.4 MHz). No board yet, so `ip/mig.prj` and the pins in
`kintex.xdc` are a representative FFG676 pinout (see there); the result
writes a bitstream (`board_top.bit`, 0 DRC errors) that is only a timing /
area check until the pins match a real card.

| | Routed | of XC7K160T |
|---|---|---|
| LUT | 52.0k (4.2k as LUT-RAM) | 51.3% |
| FF | 59.6k | 29.4% |
| Slices | 20.4k | 80.3% |
| Block RAM | 114.5 tiles | 35.2% |
| DSP | 87 | 14.5% |
| GTX | 4 of 8 | |
| MMCM / PLL | 3 / 1 (ours, pcie_7x, MIG) | |

MIG adds ~5.4k LUT / 4.6k FF, pcie_7x ~1.5k LUT / 1.9k FF / 4 BRAM.
All constraints met: core +0.035 ns, pcie user clock (clk_125mhz) +2.239,
MIG ui clock +0.656 (its own reset fanout into the XADC temperature
monitor), DDR PHY clocks >= +1.4, pixel +30; hold >= +0.037.
Times: IP generate + OOC synth ~4 min, top synth 3.1 min, place 1.9 min,
route 1.1 min, bitstream ~2 min: ~15 min end to end.

At CORE_DIV=7.375 (135.6 MHz core) the full board build also meets every
constraint: core +0.087 ns, pcie +0.662, MIG ui +0.582, hold >= +0.038;
82.7% slices. So adding pcie_7x and MIG costs no core Fmax against the
135 MHz OOC result.

With every dock feature ported (XADC monitor, JTAG diagnostics,
MicroBlaze V with `MCS=1`; 2026-10-30, `CORE_DIV=8.375`): 53.5k LUT /
60.7k FF / 82.0% slices / 122.5 BRAM / 89 DSP; all constraints met, core
+0.080 ns, pcie +1.109, MIG ui +1.585, hold >= +0.035. The MCS costs
~1.1k LUT / 8 BRAM, the XADC monitor and JTAG pages ~200 LUT.

The PCIe user clocks derive from the GT's TXOUTCLK (`txoutclk_x0y0` in the
IP's XDC), not from the refclk pin: the asynchronous clock group has to
name it, or the core <-> PCIe async FIFOs (barCc) time as synchronous
(-2.5 ns).

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
Supply / temperature come from the XADC (`src/xadc_monitor.v`) in the
dock's format. Not available: the own DDR3 controller debug port (0x9C
reads 0; MIG calibrates itself and has no gate maps to tune).

The card enumerates as 10ee:0007, the default the host library
(`sw/pcie/pci_bar.h`) already looks for; `voodoo_dma` and `play-web.sh`
accept it next to the dock's 22c2:1100.

Vendor edges:

| Dock (Gowin) | Kintex (this port) |
|---|---|
| PCIe controller TL, 256-bit beats | `pcie_7x` 64-bit AXI-Stream, `XilinxPcieRxToGowin` / `GowinTxToXilinxPcie` |
| Own DDR3 controller / Gowin DDR3 native UI | MIG 7-series native UI (same cmd codes, 256-bit word, mask polarity); `app_addr = ddr_addr << 3` in `board_top.v` |
| DVI_TX_Top IP | `src/dvi_tx.v` (TMDS encoder + OSERDESE2 10:1) |
| GowinMemStylePhase (small RAMs to FFs: no LUT-RAM) | XilinxMemStylePhase (small RAMs to distributed RAM) |
| Gowin ADC (`AdcMonitor`) | XADC (`src/xadc_monitor.v`, same register words; also feeds MIG's temperature input, mig.prj XADC_En Disabled) |
| GW_JTAG diagnostics (`jtag_diag.v`, `board_diag.py`) | BSCANE2 USER1 (`src/jtag_diag_xilinx.v`, `board_diag.py` here; same 8 pages, 64-bit DR without the padding bit) |
| AE350 hard RISC-V add-on (`USE_AE350`) | MicroBlaze V MCS (`MCS=1`, `mcs/`): RV32IC, 32 KB LMB, UART, mailbox on JTAG page 5 |

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

## mig_7series_0

`ip/mig.prj` (made from Vivado's SP701 board prj): 2 x MT41K256M16XX-107
(32-bit, 1 GB, 1.5 V), 400 MHz memory clock, 4:1, native UI (256-bit),
Strict ordering, chip select on, system clock No Buffer, reference = system
clock. Data in bank 33 (byte lane i = T<i>, DQS on the lane's DQS pair),
address / control in bank 32 T0-T2. app_addr is 29 bits (rank bit
included). For a real board replace the pins with the board's (or redo it
in the MIG GUI with the settings below).


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

## Diagnostics and add-ons

- `board_diag.py`: OpenOCD (xilinx-xc7 target, `--interface` = the board's
  adapter script) reads status (LTSSM, link, calibration, MMCM lock,
  resets, wedge), per-domain clock counters, TLP counts, HDMI frames.
  Status [23:16] is pcie_7x `pl_ltssm_state` (0x16 = L0).
- `MCS=1`: `mcs/build.sh` (clang + ld.lld, RV32IC) builds `mcs_fw.elf`;
  build.tcl associates it with the MCS so it is in the bitstream. IO module
  at 0x80000000 (UART TX +0x04, status +0x08, GPO1 +0x10, GPI1 +0x20); UART
  on `uart_txd` / `uart_rxd`, 115200 8N1.
- `sim/run.sh`: xsim tests of `jtag_diag_xilinx` (BSCANE2 model) and
  `xadc_monitor` (XADC model with a temperature / supply stimulus file).

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
