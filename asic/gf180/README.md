# GF180 ASIC study (wafer.space / Tiny Tapeout)

Feasibility study, 2026-10-26/27: can the SpinalVoodoo core be taped out on
an open-PDK shuttle, and how fast would it be? Nothing here is a tape-out
flow yet; it is synthesis + placement-level estimates and simulation.

## Shuttle facts (research, 2026-10)

- wafer.space GF180MCU full slot: 3.93 x 5.12 mm die, **12.92 mm² core inside
  the pad ring**, 56 I/O by default (40 bidir, 14 input, 2 analog); ~1000 bare
  dies, ~$7-8k (+$1.5k chip-on-board). Run 3: buy by 2026-12-09, GDS by
  2026-12-16. Default cell library gf180mcu_fd_sc_mcu7t5v0, default clock 25 MHz.
- Tiny Tapeout GF180: max 4x4 tiles ≈ 1.06 mm², 24 user I/O. Far too small.
- GF180 SRAM: foundry 5 V 512x8 = 0.209 mm² (4 kbit), 4.9 ns clock-to-Q,
  6.6 ns min cycle (tt). Community 3.3 V 1024x8 ≈ 0.155 mm² per KB.

## Area and timing (dual-TMU B182 core, Yosys + OpenROAD + OpenSTA)

Scripts: `synth_ks.ys` (memories → register boundaries via `memmodel.v`,
Kogge-Stone adders, Booth multipliers), `flow_ks.tcl` (50% floorplan, global
placement, repair_design, repair_timing, placement RC), `hist_ks.tcl`,
`run_ks.sh`. Typical corner, 5 V 7-track cells.

- Logic: 6.7 mm² cells (≈7.1 mm² after buffering), ~11 mm² placed.
- On-chip RAM: 1.47 Mbit (180 KiB) in the dock config; must mostly go off-chip.
- Core clock: 22.6 ns (44 MHz) with ripple adders, **20.2 ns (~50 MHz)** with
  Kogge-Stone; slow corner ~1.5-2x worse. 682 endpoints > 17.5 ns, 2664 > 15 ns.
  Slowest: span walker interpolators, TMU stageRecip productS/T, triangle
  setup s1Px/s1Py/LOD squares, 32-bit perf counters. HDMI domain 8.8 ns.
- FO4 of the 5 V library ≈ 0.24 ns (thick-oxide devices: ~0.5 µm-class speed).

## Performance with external RAM and SLI (GLQuake frame 1923, 640x480)

Sim additions (port patch): `--sli-count N --sli-index k` (chip k renders rows
y % N == k; offloaded fills too) and bandwidth-limited channels for the
framebuffer and texture memories (`--sim-{fb,tex}-chan-bpc16`, `-overhead`).
Runner: `asim.sh` / `apar.sh`. Single TMU, read/write latency 4/2 on top of
channel time. HR66 = one x8 HyperRAM at 66 MHz DDR (133 MB/s, bpc16 53,
overhead 6 core cycles at 40 MHz); 2xHR66 = two ganged (bpc16 107).

| config | cycles/frame | fps @33 | @40 | @50 MHz |
|---|---|---|---|---|
| ideal memory, dock caches (sim of dock) | 890,584 | 37 | 45 | 56 |
| HR66 fb + HR66 tex | 1,905,640 | 17 | 21 | 26 |
| HR66 fb only limited (tex unlimited) | 1,920,599 | 17 | 21 | 26 |
| 2xHR66 fb + HR66 tex ("split"), tex cache 64 | 1,380,580 | 24 | 29 | 36 |
| split, tex 64, fb write slots 2x64 ("mid") | 1,390,613 | 24 | 29 | 36 |
| HR66, tex 64, SLI 2 | 1,060,587 | 31 | 38 | 47 |
| HR66, tex 64, SLI 4 | 605,641 | 54 | 66 | 83 |
| **mid, SLI 4** | **470,581** | **70** | **85** | **106** |
| mini (tex 32, slots 2x64), SLI 4 (worst chip) | 585,614 | 56 | 68 | 85 |
| ideal memory, SLI 4 (SLI ceiling) | 375,601 | 88 | 106 | 133 |
| HR66, tex 64, SLI 8 | 360,589 | 92 | 111 | 139 |
| mid, SLI 8 (worst of k0 / k7) | 300,615 | 110 | 133 | 166 |
| ideal memory, SLI 8 | 260,596 | 127 | 153 | 192 |

Findings:
- The framebuffer channel is the bottleneck; the texture channel barely
  matters (fb-only limit ≈ both limited). Give the fb channel the pins.
- Texture cache 64 → 32 lines costs 17-23%; fb write slots 4 → 2 (64 words)
  cost nothing.
- SLI chips are balanced (k0 vs k3 within 1%). Scaling flattens (ideal memory:
  2.4x at N=4) because every chip does all triangle setup, its own texture
  misses and steps skipped rows.
- Correctness: channel-limited runs are pixel-identical to the baseline; SLI
  chips match the baseline on their rows (bottom-up row numbering) except 4
  pixels on chip 0 (two adjacent pairs, timing-dependent fb hazard, not
  investigated).

Not modelled: scanout reads (≈37/N MB/s per chip), host broadcast link
(~230 KB of commands per frame), multi-frame behaviour, the 4-pixel issue.

## On-chip RAM of a single-TMU ASIC config

`mid` config: ~42 KiB (mini, tex 32: 38.3 KiB). Reducible: HDMI pixel FIFO
8.5 KiB → ~1.3 KiB line buffer, 4 palette copies → 1 (-2.3 KiB), fb reader lane
FIFOs 3 x 3.7 KiB. Target ~25 KiB ≈ 3.9 mm² of 3.3 V macros next to ~9-10 mm²
of single-TMU logic: fits 12.92 mm² only with dense placement. Needs a trial
floorplan before committing.

## Suggested chip

One die design, N copies (SLI): single-TMU core, 2xHyperRAM (16-bit) on the
framebuffer, 1xHyperRAM on textures (~32 memory pins), narrow broadcast input
from the host FPGA (Tang dock: PCIe + HDMI), scan-line output back to it.
4 chips ≈ 70-85 fps GLQuake at 33-40 MHz (dock today: 50.5 fps).

## The SLI chip, built out (2026-10-27)

RTL (port patch, `src/voodoo/asic/`): `AsicChip` die top (`AsicChipGen`) =
core + `AsicUncore`: host link (8 pins DDR), framebuffer arbiter (5 core ports
+ fill-to-burst) into a 16-bit HyperBus controller (2 x8 HyperRAMs), texture
port into an 8-bit one. Runtime SLI (host config write). `AsicConfig`: single
TMU, tex cache 64 lines, fb write slots 2x64, `asicFifos`, scanout line
fetcher, SLI up to 8. Simulation: `--sim-mem-mode asic` (CoreSim keeps the
harness paths; HyperRamModel behind the real controllers, HyperBus at the
core clock). Pin budget 56/56 (clk, reset, link 11, fb HyperBus 20, tex 11,
video 12).

Function: GLQuake frame renders pixel-exact through link + controllers +
models; 2-chip SLI: chip 1 exact, chip 0 2 px (known quirk).

Performance (cycles per frame, fps at 40 MHz):

| run | cycles | fps |
|---|---|---|
| 1 chip, fixed latency 6, dock FIFOs | 3,090,974 | 12.9 |
| + asicFifos (small queues) | 3,135,950 | 12.8 |
| + variable latency 3 | 2,825,951 | 14.2 |
| + 32-word read chunks | 2,770,930 | 14.4 |
| **2 chips (each)** | **1,505,910** | **26.6** |
| 4 chips (chip 0) | 860,944 | 46.5 |

Framebuffer bandwidth bound: ~6.5 MB/frame of framebuffer traffic is ~1.6M
words; the 16-bit HyperRAM at the core clock moves one word per cycle.
Clocking HyperBus at 2x the core clock would be the next big gain.

Die (Yosys + OpenROAD, typical 5 V): logic 4.59 mm² cells (4.84 after
buffering, ~8 mm² placed), meets 40 MHz (core slack +0.02 ns after
repair_timing; slow corner would not). On-chip RAM 26.2 KiB (asicFifos):
~8.8 mm² as 5 V SRAM macros + ~4.4 mm² as flops — does not fit 12.92 mm².
GF180 5 V SRAM is ~51 µm²/bit (512x8; 64x8 is 196) vs ~64 µm²/bit for a flop.
Fitting needs ~10 KiB: single palette copy, plane-reader line buffers → small
windows, HDMI FIFO 256, tex cache 32-64 lines, PCI FIFO 32.

Remaining work to a tape-out: memory diet + SRAM macro wrappers (sync reads);
HyperBus DDR PHY on GF180 pads (no DDR cells; RWDS capture); slow-corner
timing (pipeline the multiplies or ~25-30 MHz); per-chip video streaming
(today every chip scans the whole frame); dock FPGA side (link host, per-chip
addressing, video merge, swap sync) and the board; LibreLane run in the
wafer.space template (pads, power grid, DRC/LVS, antenna); gate-level sim.

## More memory pins: 1x1_max_all pad ring (168 I/O, same 12.92 mm² core)

The wafer.space template's `1x1_max_all` ring has 168 bidirectional pads (+30
power) without shrinking the core, so the dies can have wide parallel
memory. Channel-model runs (fps at 40 MHz, mid config + asicFifos):

| memory per chip | cycles/frame | fps |
|---|---|---|
| calibration: model of HyperRAM at the core clock (real controller: 2,770,930) | 1,940,617 | 21 |
| 32-bit async SRAM fb + x8 HyperRAM tex | 1,465,614 | 27 |
| 64-bit SRAM fb (2 banks) + x8 HyperRAM tex | 1,450,629 | 28 |
| 64-bit SRAM fb + x16 HyperRAM tex | 1,370,619 | 29 |
| 32-bit SRAM fb + 16-bit SRAM tex | 1,430,602 | 28 |
| **2 chips: 32-bit SRAM fb + x16 HyperRAM tex** | **775,641** | **52** |
| 2 chips: 32-bit SRAM fb + 16-bit SRAM tex | 815,590 | 49 |
| 4 chips: 32-bit SRAM fb + 16-bit SRAM tex | 495,581 | 81 |

The model underestimates HyperRAM's cost (1.94M vs the real controller's
2.77M: one transaction at a time, read chunks); an SRAM controller has no
command / latency phase, so the SRAM rows should hold. One 32-bit SRAM bank is
enough: with it the framebuffer stops being the limit (second bank +1%).
Pins: 32-bit SRAM (2 x 16-bit 10 ns parts, shared address) ~59, x16
HyperRAM 20, link 11, video 12, clock/reset 2 = ~104 of 168.
Risks: async SRAM read through GF180 pads in one 25 ns cycle (pad delays
2.4-6.6 ns + 10 ns SRAM: tight at the slow corner, or pipeline), WE# pulse
timing, simultaneous switching on ~60 pins, 3.3 V I/O supply, bond-out of a
200-pad die on chip-on-board.

## SRAM chip and area diet (2026-10-27)

RTL: `SramController` (pipelined: reads overlap the next command, order queue
for BMB responses) + `SramModel`; framebuffer and texture memory each on a
32-bit async SRAM bank (`--asic-fb-sram`, `--asic-tex-sram`), ~59 pins each.
Area options: `Config.paletteSingleRam` (one 4-read palette instead of four
copies, a Quartus workaround), 640x480 max, HDMI FIFO 256, write slots 2x16
words, texture cache 32 lines. PCI FIFO must stay 64: at 32 the trace host
starves (31M cycles). FPGA debug features (frame DMA, scanout tap, OSD, ADC
monitor) are in fpga/src/voodoo/gowin, never in the core; hwDebugRegs perf
counters and debug registers are off / simulation-only in the die.

| run (fps at 40 MHz) | cycles/frame | fps |
|---|---|---|
| SRAM fb (first, non-pipelined controller), 2 chips | 1,115,978 | 36 |
| SRAM fb pipelined, x16 HyperRAM tex, 2 chips | 1,000,952 | 40 |
| + single palette + 640 | 1,030,931 | 39 |
| no fb write buffer / no fb read cache / neither | 1.246M / 1.211M / 1.766M | 32 / 33 / 23 |
| SRAM tex, cache 64 / 32 / 16 / 8 lines, 2 chips | 0.991 / 1.071 / 1.191 / 1.561M | 40 / 37 / 34 / 26 |
| **fit config** (SRAM fb + tex, cache 32, slots 2x16), 1 / 2 / 4 chips | 1.916M / 1.111M / 0.686M | 21 / **36** / 58 |

All pixel-exact (chip 0 of a pair: the known 2-px write-buffer quirk; it
disappears without the write buffer).

Die (fit config): logic 4.56 mm² cells, 4.79 after buffering (incl. ~0.46
mm² of RAM stand-in flops), meets 40 MHz typical. On-chip RAM 15.8 KiB =
7.44 mm² (`memarea.py`: GF180 5 V SRAM macros lose to flip-flops for almost
every memory here). Total ≈ 15-16 mm² vs 12.92: still ~2.5 mm² over. Next
cuts: plane-reader line buffers → 64-lane windows (~1.2 mm²), PCI FIFO
entries 124 → ~56 bits (~0.25), spanVar FIFO 16 → 8 (~0.19), texture cache
16 lines (~1 mm², -11%). 3.3 V cells + 3.3 V SRAM: cells ~20-40% slower,
and the 1024x8 macros only help the line buffers.

## Area cuts round 2 + PCI (2026-10-28)

Cuts: `Config.fbReaderChunkLanes` = 64 (plane readers fetch spans in 64-lane
bursts; lane buffer 128 instead of 641 lanes; also 6% faster), span queue
16 → 4 and TMU sample queue 16 → 8 (free), PCI FIFO stays 64 (Voodoo
software). Texture cache 16 lines is the last lever (-11%).

PCI: `voodoo.asic.Pci` — PciTarget (PCI clock domain; config space with 3dfx
Voodoo Graphics IDs, BAR0 16 MB, posted burst writes, delayed-transaction
reads, parity), PciToLink (packets to every chip; LFB reads to the row's
chip), PciMasterModel (sim host bridge). Die top `AsicChip`: one die is the
SLI master (pciMaster strap) or a slave; straps for chip index / count, busy
in / out folded into the status register. No FPGA needed on a PCI card:
2 dies + SRAM + HyperRAM + an HDMI transmitter on the shared RGB bus.
Pins (1x1_max_all): PCI 47, fb SRAM 59, tex HyperRAM x16 20, link 11, video
20, busy 2, straps 6, core clock 1 = 166 / 168.

Whole-system simulation (`--asic-chips N`, `--asic-pci`): chips share the
memory models, the composed frame is compared with the single-chip image —
exact except the known 2 px, for 2 and 4 chips, link or PCI.

| system (fps at 40 MHz) | cycles/frame | fps |
|---|---|---|
| 2 chips, link, SRAM fb + tex, cache 32 | 1,040,919 | 38 |
| 2 chips, PCI, SRAM fb + tex, cache 32 | 1,071,329 | 37 |
| 2 chips, PCI, SRAM fb + HyperRAM x16 tex, cache 32 | 1,181,395 | 34 |
| 4 chips, PCI, SRAM fb + tex, cache 32 | 691,391 | 58 |
| **fits: 2 chips, PCI, SRAM fb + HyperRAM tex, cache 16** | **1,331,363** | **30** |
| fits: 4 chips | 856,391 | 47 |

PCI die (cache 32): 4.61 mm² cells, 4.86 after buffering (0.47 of RAM
stand-ins → ~4.39 logic; PCI target ~0.06), core 40 MHz +0.23 ns, PCI 33 MHz
+2.68 ns, pixel clock met (typical corner). RAM 13.3 KiB = 6.11 mm².
Total ≈ 13.5 mm² at 70% placement density (14.5 at 60%) vs 12.92; with the
16-line texture cache ≈ 12.2 mm² at 70%: fits, tightly.

## RAM cost correction (2026-10-28)

`memarea.py` charged 64 µm²/bit for flip-flop memories (the dffq_1 cell
alone). Measured with Yosys + GF180 liberty (1R1W, synchronous read):
flip-flop RAM **118 µm²/bit** (flop + write-enable mux + read mux); latch RAM
(registered write data/address, per-word latch enable in the low clock phase,
DFFRAM style) **60 µm²/bit**. With a per-read-port model (flops 97 + 21/port,
latch 44 + 16/port, foundry macros where cheaper) the PCI die's RAM is
**6.0 mm² with latch RAM, 10.0 mm² with flops**: the earlier fit holds only
if every memory is built as latch RAM or a macro (latch timing / DFT work).
By group (latch): tex cache 1.97 (cache 32), pipeline FIFOs 1.57, palette
0.66 (4 read ports), fb readers 0.66, video 0.60, PCI FIFO 0.56.

## Logic by block, and what a board FPGA could take (2026-10-28)

Per-module generic cell counts (Yosys, before mapping, hierarchy kept;
`~/.cache/asic-est/hierq/stat.txt`), share of the die's ~4.4 mm² logic,
±30% (cell mix differs by block): TriangleSetup 22%, Tmu 20% (texel decoder
5.5%, cache control 2.9%), SpanWalker 14%, framebuffer memory subsystem 11%
(plane buffers / readers 5%), RegisterBank 4.8%, FramebufferAccess 3.2%,
ColorCombine 2.3%, Fog 2.1%, HDMI scanout 2.0%, PciFifo 1.6%, uncore ~3%.
GLQuake frame 1923: 2053 triangles, 57418 register writes (~28 / triangle).

Board-FPGA split tiers (placed ≈ logic / 0.7 + latch RAM):
- glue (PCI target + PCI FIFO + video + row merge): ~1.4 mm² off the die,
  47 PCI pins freed.
- + RegisterBank + TriangleSetup (per-triangle work; the FPGA sends the
  setup result, ~100 link cycles per triangle, less than the ~4 x 28
  halfwords of register writes it replaces): ~3.1 mm² total. FPGA ~10k LUT4,
  ~6k FF, 9 multipliers, a few BSRAM (GW2A-18 / GW5A-25 class).
- + TMU (Voodoo 1 FBI / TMU split, texture memory on the FPGA): ~6-7 mm²
  total, but one TMU per die on the FPGA (8.7k LUT4, 17 multipliers each)
  and a per-pixel texel bus (~36 pins per die).
