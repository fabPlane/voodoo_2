# Tang Mega 138K Pro — current status

Written 2026-10-09. This is where the PCIe dock port actually is, what landed
on this machine, and what is blocking a bitstream. Longer background is in
`HANDOFF.md` and `ip.md`.

**Not done.** Synthesis of `board_top` completed. Place-and-route died on
BSRAM. Nothing SpinalVoodoo-related has been flashed. `lspci` has never seen
this core.

Do not commit unless asked. Do not touch the dirty `ext/SpinalVoodoo` tree or
the untracked `hw/gowin-open/vendor-jtag-mailbox` tree.

---

## Where we are

Target is the Sipeed Tang Mega 138K Pro Dock in a Linux AMD/ASUS + NVIDIA
host: `GW5AST-LV138FPG676AES` (`GW5AST-138B`, JTAG idcode `0x1081b`).

| Step | State |
|---|---|
| JTAG / USB Debugger | Works. Sipeed `led` example SRAM-loaded; six LEDs chase. |
| Gowin Education 1.9.11.03 | Installed onboard; **cannot** target the 676-pin part. |
| Gowin Standard 1.9.12.04 | Installed at `$HOME/gowin/standard-1.9.12.04` (IDE + Programmer). |
| Sipeed float license | `106.55.34.119:10559` in `IDE/bin/gwlicense.ini`. Flaky; retry. |
| Spinal Verilog | `fpga/rtl/gowin/TangMega138kPro.v` generated (~9.6 MB). |
| Vendor IP in `ip/` | Sipeed prebuilts: DDR3, DVI TX, `SerDes_Top` PCIe. PLLs generated here. |
| `gw_sh` synthesis | Completed 2026-10-08. |
| `gw_sh` PnR | **Failed: 473 BSRAM used vs 340 on the device.** |
| Flash / cold-plug / `lspci` | Not reached. |
| Host `sw/pcie` tools | Built. Window test passed against whatever was already in the slot (not this core). |

Next concrete work: fit BSRAM (need about 133 tiles back), rerun `gw_sh`,
flash (not SRAM-only), cold-plug, look for `22c2:1100` (Sipeed SerDes) or
`10ee:0007` if PCIe IP is regenerated.

### Update, later 2026-10-09: BSRAM fixed, now routing-bound

BSRAM is solved; the blockers behind it were constraints and then
routing congestion.

| Run | Change | Result |
|---|---|---|
| 1 | `GowinMemStylePhase`: ≤4-deep → registers, ≤32-deep → `distributed_ram` | BSRAM 260 → 114 (synth). PnR: I/O bank VCCIO errors (CST). |
| 2 | CST rewritten from Sipeed's Pro examples; SDC one-line | Placed. **55 858 unrouted nets**: CLS 100 %, FF 105k. Gowin ignored `distributed_ram` (0 RAM16) → ~40k FFs. |
| 3 | Registers only for ≤4 deep, or ≤32 deep and ≤4 kbit; rest stays BSRAM | LUT 71 %, FF 53 %, CLS 96 %, BSRAM 285/340. **10 147 unrouted.** |
| 4 | `useTexFillCache = false` (Gowin only) + `-place_option 1` | LUT 60 %, FF 49 %, CLS 94 %, BSRAM 215/340. **600 unrouted.** Routing 2 h 22 m. |
| 5 | Span-walker FIFO (4 x 1439) back to BSRAM | FF 46 %, BSRAM 289. **892 unrouted** — worse. Reverted. |
| 6A | Run 4 + `-opt_goal area` | Bit-for-bit same utilisation as run 4, 600 unrouted. No effect; dropped. |
| 6B | Run 4 + `-max_fanout 100` | **495 unrouted.** Kept in `build.tcl`. |

Tool knobs are exhausted (this Gowin exposes only place/route algorithm,
fanout, opt_goal). The router gives up after ~2.5 h of phase 1. CLS sits at
~94 % with LUT 60 % / FF 49 %: the core needs roughly another 10 % of logic
removed, which means either Config feature cuts or upstream (`ext/`) area
work (e.g. the 2713-bit rasterizer → span-walker payload).

What changed and why:

- `CoreGowinGen.scala`: `GowinMemStylePhase` (SpinalHDL transformation
  phase) adds `syn_ramstyle = "registers"` to shallow / small non-ROM mems.
  Kills the rasterizer (54) and span-walker (37) BSRAM blow-up without
  touching `ext/`. `GowinGenSupport.config` is the one Gowin `Config`.
- `useTexFillCache = false`: saves ~9.7k LUT/ALU, 4.4k FF, 27 BSRAM.
  CoreSim A/B on the logo trace: cached vs uncached frames are
  **bit-identical** (0 differing pixels).
- CST: every pin location already matched Sipeed's `pro_ddr_test` /
  `cam2hdmi_alt` / `pcie_dma_demo`. Missing were per-bit `IO_TYPE=SSTL15`
  + `BANK_VCCIO=1.5` (only bit 0 had them), `p,n` pair LOCs for CK/DQS/TMDS,
  invalid `LVCMOS33D` on TMDS, and the DDR PHY `INS_LOC` / PLL `PLL_L[0]` /
  DLL `CLKIN_SEL` lines.
- DDR3 `pll_stop` now drives the sys-PLL `enclk` for `memory_clk` (Sipeed
  wiring). Was left unconnected; likely calibration failure on hardware.
- SDC: no `\` continuations (Gowin parse error); `ddr_clk_out` is on
  `fclkdiv/CLKOUT`; HDMI periods match the 125 MHz PLL (8 / 40 ns).
- `build.tcl`: `-place_option 1`; `GOWIN_RUN=pnr` env var (note: gw_sh
  still re-runs synthesis).
- Long builds: run gw_sh detached (`setsid nohup`), routing can exceed 2 h.

Remaining levers, none tried yet:

- Config cuts in `GowinGenSupport.config`: `enableFog`, `enableChromaKey`,
  `enableAlphaTest`, `enableDither`, `maxFbDims = (640, 480)`. Each is a
  few k LUT at most; may change logo pixels.
- `-rw_check_on_ram 0`: drops the read-during-write bypass on every
  BSRAM (wide FIFOs included). Spinal's `readSync` Verilog is read-first in
  sim, so this risks sim/hardware mismatch; needs a careful audit first.
- Upstream area work in `ext/SpinalVoodoo` (rasterizer / span walker /
  triangle setup are 21k + 16k LUT).

Sim note: `ext/SpinalVoodoo/traces/*` here are Git LFS pointers. Real logo
trace is in `~/projects/voodoo-benchmark/src/main/ext/SpinalVoodoo/traces/logo/`,
but its `state.bin` is v1 (harness wants v2), so the 86Box reference
renders black without state. Re-capture the logo trace before using it as
the board acceptance reference.

---

## What was added

FPGA / board glue (this repo, `fpga/` and `sw/pcie/`):

- `src/voodoo/gowin/` — `TangMega138kPro`, `CoreGowin`, TLP BAR target, AXI-Lite
  CDC, AXI4 → Gowin DDR3 native UI, address map (fb at 0, textures at 4 MiB).
- `hw/gowin/src/board_top.v` — PLLs, DDR3, DVI TX, `SerDes_Top` with
  `PCIE_Controller_Top_*` port names (not a bare `PCIE_Controller_Top`).
- `hw/gowin/build.tcl` — top is `board_top`; adds `TangMega138kPro.v` +
  `board_top` + `ip/*/*.v` + CST/SDC; `set_csr` for `serdes.csr`. No `rtl/`
  glob (that pulled in `TangConsole138k.v` as a second top).
- `hw/gowin/env.sh` — prefers Standard 1.9.12.04; `LD_PRELOAD` distro
  freetype + libz; `QT_QPA_PLATFORM=offscreen` if there is no display. Do
  **not** put Programmer/bin on `LD_LIBRARY_PATH`.
- `hw/gowin/generate_ip.sh` — sys/HDMI PLLs. VCO now in 650–1300 MHz
  (`mdiv` 24 / `odiv` 12+3 → 100/400 MHz; `mdiv` 25 / `odiv` 10 → 125 MHz
  HDMI serial).
- `hw/gowin/generate_vendor_ip.tcl` — CLI `read_ipc` / `generate_target`.
  Not proven; first bitstream uses Sipeed encrypted IP instead.
- `ip/*.ipc` kept in git. Generated `.v` / `.vo` / `.csr` gitignored
  (except `serdes.csr`, which PnR requires).
- `sw/pcie/` — `pci-bar`, `pci-trace-replay`, window test. Also matches
  Sipeed IDs `22c2:1100` as well as planned `10ee:0007`.

Gowin `Config` for this top (in `CoreGowinGen.scala` /
`TangMega138kProGen`):

```
Config.voodoo1().copy(
  useFbWriteBuffer = true,
  fbWriteBufferLineWords = 256,
  useFbReadCache = true
)
```

That 256-word write buffer is **not** the BSRAM hog (one 18K tile per slot).

---

## Problems

### 1. PnR will not fit — 473 BSRAM > 340 (the blocker)

Synthesis hierarchy XML reported `T_Bsram=260`. PnR then asked for **473**.
GW5AST-138 has **340** 18K BSRAMs. Need ~133 tiles (~28%) back.

This is packing waste, not real storage. Gowin maps each ~36-bit slice of a
Spinal `StreamFifo` to a full 18K tile, even when the FIFO is two entries
deep.

| Memory | Depth × width | Tiles if packed | Tiles seen |
|---|---|---|---|
| Rasterizer `i.queue(2)` (`StreamFifo_2`) | 2 × 2713 | 1, or just FFs | 54 |
| SpanWalker `o.queue(4)` (`StreamFifo_3`) | 4 × 1439 | 1, or just FFs | 37 |
| TMU request FIFOs (`queue(16)` / `texFillRequestWindow`) | 16 × ~684 × 2 | ~2 | ~38 if sliced |
| Pixel pipeline `queue(64)` | 64 × 408 | 2 | up to 12 |
| Dither / recip / log tables | tiny ROMs | ROM16 / LUT | 27 |
| `useFbReadCache` | real cache | ~22 | ~22 |
| DDR3 IP | fixed | 24 | 24 |
| HDMI CDC `fifoDepth` 4096 | — | 4 | 4 |
| FB write buffer, 256 words/slot | — | 1 each | 1 each |

Those two raster FIFOs alone are ~91 tiles for ~11 kbit of state. Mapping
them to registers costs ~11k FFs, which this part can absorb.

**Do first (should clear 340 without gutting the core):**

1. Stop inferring BSRAM for shallow/wide FIFOs — register pipes
   (`s2mPipe` / `m2sPipe`) or `(* syn_ramstyle = "distributed" *)` /
   `"registers"` on every `logic_ram` with depth ≤ 16. Prefer a Verilog
   post-pass in `build.tcl` so the dirty `ext/SpinalVoodoo` tree stays
   untouched.
2. `useFbReadCache = false` in the Gowin generators only (~22 real tiles).
3. Force dither / recip / log tables off BSRAM (`enableDither = false` for
   bring-up, or the same style attribute).

**Then, if still over:** `texFillRequestWindow = 4`; shrink PixelPipeline
`.queue(64)` and the TMU `.queue(16)`s.

**Do not cut** `fbWriteBufferLineWords`, the TMU (logo needs textures), the
HDMI FIFO, or the DDR3 IP.

`HANDOFF.md` still says “smaller raster FIFOs”. Depths are already 2 and 4.
The win is remapping them off BSRAM, not shrinking them.

### 2. Sipeed float license drops seats

`gw_sh` often dies with “Server not responding”. Probe-then-exec still
flakes (check is per-process). Retry the same command. Do not put
Programmer/bin on `LD_LIBRARY_PATH`.

### 3. Education edition cannot build this board

Sipeed documents **Standard** for every Tang except this 676-pin Pro.
Education 1.9.11.03 refuses the device. Standard 1.9.12.04 is the path.

### 4. Vendor IP generation from CLI is not proven

`create_project` + `read_ipc` + `generate_target` did not produce usable
objects here. First synthesis used Sipeed prebuilts (EDA 1.9.9–1.9.11
encrypted IP). 1.9.12 synthesized them. That PCIe IP enumerates as
`22c2:1100`, not the planned `10ee:0007`. Host tools accept both.

### 5. Tooling landmines (fixed, do not regress)

- Gowin `libfreetype` vs system fontconfig → `FT_Done_MM_Var`. `env.sh`
  `LD_PRELOAD`s distro freetype + libz.
- Qt xcb with no display → `QT_QPA_PLATFORM=offscreen`.
- `add_file -type csr` is unknown; use `set_csr`. First PnR died on “csr
  file should be specified” before BSRAM.
- Sys/HDMI PLL VCO was outside 650–1300 MHz. Generators now use `mdiv`
  24/25. Not re-proven through a successful PnR.

### 6. Bring-up constraints once a bitstream exists

- Cold-plug only. Gowin PCIe IP does not hot-plug. Flash, then power-cycle
  the host with the card seated. SRAM load is dropped on power cycle.
- BAR0 is 1 MiB. Window at `0x250`, never-hang status at `0x254`.
- `Axi4ToGowinDdrNative` is one-outstanding. Fine for pixels, slow.
- Heavy traces (Quake / Screamer) can wedge upstream `Core`. Logo is the
  first acceptance trace.
- CST pin table is transcribed, not probed. DDR3 DQS swap fails calib
  silently. Some docks have dead DVI TX; LiteX used DVI RX pins instead.

---

## What we are not changing

- Dirty files under `ext/SpinalVoodoo` (Lfb, Fog, SpanWalker, TriangleSetup,
  Tmu, TmuTextureCache, emu harness / tests).
- Untracked `fpga/hw/gowin-open/vendor-jtag-mailbox/`.
- No git commit unless asked.

---

## Acceptance (unchanged, not reached)

On the dock, after flash + cold-plug:

1. LEDs: heartbeat, PCIe link, DDR calib, not wedged.
2. `lspci` shows the endpoint (`22c2:1100` with current SerDes, or
   `10ee:0007` if regenerated).
3. `pci-trace-replay traces/logo` completes with `0x254` bit 0 clear.
4. Framebuffer readback matches the 86Box-verified logo reference.
5. Monitor on DVI shows the same image.

### DDR3 bring-up on hardware (2026-10-09)

`ddr3_test/` is a standalone DDR3 + USB-JTAG bitstream with the same
PLL/DDR3 wiring as `src/board_top.v` (see `ddr3_test/README.md`). On the
board: calibration completes, UI clock 100 MHz, all 1 GiB passes an
address-unique BIST, and files can be pushed/verified over JTAG
(`ddr3_jtag.py write/verify`). Two load-dependent problems: back-to-back
writes at ≥ ~1 GB/s for > ~1 ms kill the user JTAG until reload, and
gap-4 streaming over ≥ 512 MiB gives ~0.1 % bad bursts; gap ≥ 8 is clean.
The production AXI→native adapter stays in the gap ≥ 8 regime. Also: GW_JTAG
outputs must be sampled on the falling TCK edge on this board.

### Update, night 2026-10-09: core shrunk in `ext/SpinalVoodoo`, PnR routes, bitstream built

`ext/SpinalVoodoo` edited (uncommitted, on top of the user's WIP
scanX / Lfb / Fog / Tmu changes; pre-edit diff saved in
`/tmp/ext_spinalvoodoo_baseline.patch`). No Voodoo features removed.

What changed:

- `raster/TriangleSetup.scala`: the three combinational 48/32-bit edge-slope
  dividers are now three radix-4 sequential dividers (16 cycles). The 11
  parallel 66-bit start-correction accumulators are now one shared,
  3-stage pipelined multiply-add, one channel per cycle:
  `start + ((d/dx * (dxPix*16 + sub) + d/dy * (dyPix*16 + sub)) >> 4) mod 2^w`,
  which is the old "paramAdjust, then 8 x 2-bit steps" result. Pass-through
  fields come from `pending`; the duplicate `outputReg` is gone. Setup is
  ~20 cycles instead of ~10. New `busy` output (state != Idle).
- `raster/Rasterizer.scala`: dropped the triangle input `queue(2)` + `<-<`
  (3 x ~2.7 kbit). The span queue (4 deep) now holds only per-span fields
  + a 1-bit triangle tag (~364 bits instead of 1439); per-triangle constants
  (x steps, TMU / pixel config) come from the walker's live
  `triangleConst` or one shadow copy of the previous triangle. The walker
  waits to start a new triangle while spans of the shadow triangle are
  still queued.
- Sync=Yes hazard fix (needed once setup got slower): `pipelineBusy`
  now also covers setup Prep/Work (`triangleSetup.busy`), triangle
  commands waiting in the reg-bank command pipe, `rasterYPipe` and the span
  pipe stage into `SpanRasterizer`; `PciFifo` holds a Sync=Yes write while
  the previous write is still in its issue stage. Before this, a
  `color0/1` write right after `triangleCMD` could overtake the triangle.

Gowin synthesis, `board_top` (whole design):

| | Logic (LUT+ALU) | LUT | ALU | FF | DSP (27x36) | BSRAM (syn) |
|---|---|---|---|---|---|---|
| before (run 6B) | 81 134 | 64 593 | 16 541 | 68 127 | 15 | 131 |
| after | 61 228 | 51 255 | 9 973 | 56 739 | 19 | 131 |

Per module (FF / ALU / LUT): `triangleSetup` 4028 / 7044 / 14092 ->
2669 / 473 / 1748; rasterizer `i_fifo` 5369 / 0 / 1798 -> gone; span
queue 6189 / 3 / 2509 -> 1665 / 3 / 678; `rasterizer_1` (pipes + shadow)
3024 / 0 / 2 -> 2162 / 0 / 1054. The rest is unchanged within noise.

PnR (scratch copy, same `build.tcl`, ~28 min): **routed, bitstream
generated.** Logic 61 749 / 138 240 (45 %), FF 56 739 (41 %), **CLS
55 501 / 69 120 (81 %, was 94 %)**, BSRAM 215 / 340, DSP 59 / 298.
Bitstream: `/tmp/spinalvoodoo_shrink.fs` (reports in `/tmp/shrink/pnr1/`).
`rtl/gowin/TangMega138kPro.v` is regenerated from the new sources;
`hw/gowin/impl` still holds the old run-6B reports.

**Timing is not met**: `ddr_clk_out` (core clock) Fmax 19.6 MHz, TNS
-494 056 ns over 40 865 endpoints; `clk100` 68.9 MHz (166 endpoints).
Every reported worst path starts at `regBank/sim_reg_214_f4` (`fbiInit1`
field 4, framebuffer row width) and runs ~36 levels through framebuffer
address math into the fb readers / AXI aligner. That register is static
after init: a `set_false_path` / multicycle from `fbiInit1`, or registering
the derived row stride, is the next step before trusting the board.

Pixel checks (CoreSim `trace_test`, Verilator 5.052, real traces from
`~/projects/voodoo-benchmark/src/main/ext/SpinalVoodoo/traces`, stateless):

- Randomised old-vs-new `TriangleSetup` harness: 6000 triangles (half
  with coincident coordinates / zero dy): **0 output bit differences**.
- logo, test02, test04, test28, test_alphabet, tower, qatest00_filter,
  qatest00_mipmap: final frames **byte-identical** before vs after.
- test16 / test18 are timing-dependent in the baseline (a Sync=Yes
  `color` / texture write races in-flight triangles). Every buffering
  variant gives a different frame, including base-only variants. With the
  hazard fix the new core matches the 86Box reference nearly exactly
  (207 / 113 of 307 200 pixels mismatched, baseline 102 482 / 102 823).
- quake / tomb / screamer2 / valley_of_ra / test20 hang at ~5.2 M cycles
  in **both** baseline and new builds (stateless replay), so they were not
  compared.
- Upstream scalatest suites can't run here: they need `iverilog`, or
  have stale harnesses (`TrianglePipelineThroughputTest`: NO DRIVER
  errors). Same result on baseline and new.

Earlier Tang Console build options (`-rw_check_on_ram 0`,
`-route_option 0`, `-clock_route_order 1`, `-route_maxfan 100`,
device_version C): routing doesn't need them now. `-rw_check_on_ram 0`
still has the sim / hardware read-during-write risk. Check device_version
against the actual chip with the Programmer before flashing; synthesis
currently reports version B.

### Update 2026-10-10: Quake / Screamer 2 / Valley of Ra / Tower replay hangs fixed (`ext/SpinalVoodoo`)

The CPU-side symptom (`bus_write(0x2000e0) cmd timeout`) was the PCI FIFO
filling up behind a wedged framebuffer memory port. Two bugs, both older
than the recent raster changes:

1. **Fb plane readers issued reads with no space reserved for the data**
   (`framebuffer/FramebufferPlaneReader.scala`). The cached colour/aux
   reader could have up to 4 span bursts in flight, but `laneFifo` holds
   only 801 lanes. On Quake (~cycle 523k) two ~460 px rows overfilled it.
   The response at the head of the shared, in-order fb memory then stalled.
   That blocked the colour/aux write buffers, so the pixel pipeline stopped
   draining lanes: a deadlock. Fix: a `laneCredits` counter (lanes issued
   but not yet popped). A prefetch burst issues only when its `laneCount`
   fits (or nothing is outstanding). A direct-miss read issues only when
   `readRspFifo` has room for it and every other outstanding direct miss.
2. **The 5-way fb arbiter dropped source bits** (`CoreSim.scala`). The
   output used `fbMemBmbParams` (4 source bits), but `BmbArbiter` needs
   input source (4) + log2Up(5) = 7 bits. Only bit 0 of each port's source
   survived, so responses came back misrouted. LFB reads (colour-read
   arbiter index 2) went to the scanout reader, which wedged Tower at its
   first 16-bit LFB write (`0x4c4c28`). Direct-miss responses came back
   tagged as prefetch data. Fix: new `Core.fbMemSharedBmbParams(c)` (+3
   source bits) for the arbiter output and the sim RAM.
   **The same bug is in `fpga/src/voodoo/gowin/CoreGowin.scala` and
   `artix/CoreArtix.scala`**: their `fbArbiter` output, `fbAxi` and
   `fbAxiConfig` all use `Core.fbMemBmbParams(c)`. They should use
   `Core.fbMemSharedBmbParams(c)`, which widens the fb AXI ID by 3 bits.
   I left the board wrappers unchanged.

Results (CoreSim `trace_test`, both fixes, with v2 state):

| trace | result | mismatched px vs 86Box | cycles |
|---|---|---|---|
| quake | completes (hung before) | 8 891 / 307 200 | 3 420 443 |
| tomb | completes | 5 617 | 3 180 467 |
| screamer2 | completes (hung before) | 28 804 | 2 612 355 |
| valley_of_ra | completes (hung before) | 7 797 | 3 015 284 |
| tower | completes (hung at 1st LFB write before) | 95 | 1 914 253 |

Stateless regression set compared with the previous build (`/tmp/shrink/out_new2`):
- **Byte-identical frames:** logo, test02, test04, test28, test_alphabet,
  qatest00_filter, qatest00_mipmap, tower. Cycle counts are within 0.2 %
  (logo +0.17 %).
- **Changed frames:** test16 and test18, which are timing-dependent (see
  above). They now give 207 and 113 mismatches (were 151 153 / 151 855).
- **Now complete instead of hanging:** quake (0 mismatches), screamer2,
  valley_of_ra and test20.
- **Stateless tomb:** gets past the hang, then segfaults inside the 86Box
  reference model (`voodoo_triangle`), not in the RTL.

### Bring-up state, 2026-10-10 (before first flash + reboot)

Design changes since the shrink (all uncommitted):

- Voodoo core on its own clock `clk_core` from the sys PLL (`clkout1`,
  VCO 1200 / ODIV1). **30 MHz for bring-up** (ODIV1 = 40); 50 MHz (= SST-1
  graphics clock, ODIV1 = 24) once the core closes timing. DDR3 adapter stays
  on the 100 MHz UI clock; fb/tex AXI cross via `Axi4CC`
  (`TangMega138kPro.scala`), BMB hops registered in `CoreGowin.scala`,
  scanout requests `m2sPipe`d in ext `Core.scala`.
- Deadlock fix (ext): `FramebufferPlaneReader` lane credits + 5-way fb arbiter
  needs `Core.fbMemSharedBmbParams` (also applied in `CoreGowin` /
  `CoreArtix`). Quake / Tomb / Screamer2 / Valley / Tower state traces now
  complete in CoreSim.
- `PcieTlpBarTarget`: window register = byte write at 0x250 from any window,
  or any access at 0x250 in register space (windows 0-3). Host side
  `sw/pcie/pci_bar.h` uses the byte write. Timeout uses counter MSB.
- `src/jtag_diag.v` + `board_diag.py`: read LTSSM / link / calib / PLL /
  core reset / wedge / clock rates / TLP counts over the USB JTAG
  (`sudo python3 board_diag.py --watch 3`).
- `build.tcl`: Sipeed PCIe demo PnR options (clock_route_order 1,
  route_option 1) + replicate_resources.

Timing at the B2 build (50 MHz core target): core Fmax 32.1 MHz (TMU /
span walker / pciFifo->regBank paths ~31 ns), DDR UI 75 MHz, TLP 76.9 MHz.
B3/B4 add the fixes above; check `impl/pnr/*_tr_content.html`.

PCIe on this host (ASUS ProArt X870E-Creator, dock in **PCIEX16(G4)**,
chipset x4): the BIOS disables an empty slot's port at boot, so JTAG/SRAM
hot-loading can never link (tried with Sipeed's known-good PCIe demo,
retrain + secondary bus reset). The Voodoo bitstream must be in the dock's
SPI flash at power-on. Factory flash backup (W25Q128, 16 MiB, Sipeed LED
demo): `~/tang-mega-138k-pro-backup/sipeed_factory_w25q128.bin`
(sha256 1ca34c7f...). Restore with
`sudo openFPGALoader -c ft2232 -f --file-type bin <backup>` (raw image).

After reboot: `lspci -nn | grep -i "22c2\|10ee"`, then
`sudo python3 fpga/hw/gowin/board_diag.py`, `sudo sw/pcie/pci-bar status`,
`sudo sw/pcie/pci-trace-replay <logo trace dir>`, and the live Glide demo
`sudo sw/glide-homebrew/build/pcie/hb04_pinwheel 0` (`make -C
sw/glide-homebrew pcie`).

**Flashed 2026-10-10 03:4x: build B5** (`hw/gowin-c`, RTL snapshot
`/tmp/TangMega138kPro_v4.v`; copy of the .fs in
`~/tang-mega-138k-pro-backup/voodoo_b5_flashed.fs`). Routed, bitstream OK.
Timing not fully clean: core 28.9 MHz vs 30 (10 endpoints), DDR UI 78 MHz
(31; adapter + tex R CDC -> adapter), TLP 77 MHz (70; mostly the vendor
SerDes upar_arbiter, 4 in PcieTlpBarTarget). On SRAM and after a
reconfigure-from-flash: PLLs locked, DDR3 calibrated, core out of reset,
not wedged, clk_core 30.0 / DDR 100.0 / TLP 100.0 / pixel 25.0 MHz, HDMI
frames counting, LTSSM 0 (slot port disabled until reboot). Next: reboot,
check `lspci`, then `board_diag.py`, `pci-bar status`, logo replay,
`hb04_pinwheel 0`. B3 note: Sipeed's route_option 1 / clock_route_order 1
/ replicate_resources gave 12k unrouted nets; left off.

### Update 2026-10-10: clk_core closes at 50 MHz (ext pipelining, pixels unchanged)

Scratch build dirs `hw/gowin-{t,v,x}` (copies of `hw/gowin` with ODIV1 = 24 and
`clk_core` period 20; TLP 75 MHz as in `hw/gowin`). Agent log:
`~/.cache/timing50/NOTES.md`; helper `~/.cache/timing50/mkbuild.sh`.

| build | RTL snapshot | clk_core Fmax | clk_core TNS / endpoints | TLP (75) | DDR UI |
|---|---|---|---|---|---|
| B2 (before, 50 MHz target) | - | 32.1 MHz | ~14k endpoints | - | 75 MHz |
| t2 (iter 1+2) | `~/.cache/gowin-builds/rtl_t2` | 38.7 MHz | -4632 ns / 2434 | 70.4 (vendor upar_arbiter) | 102.0 |
| **t5 (iter 1-5)** | `~/.cache/gowin-builds/rtl_t5` | **50.02 MHz** | **0 / 0** | 76.1, TNS 0 | 101.5, TNS 0 |

t5: every clock setup + hold TNS 0, bitstream
`hw/gowin-x/impl/pnr/spinalvoodoo_tang_mega_138k_pro.fs` (copy + reports in
`~/.cache/timing50/best_t5/`). Logic 49 %, FF 47 %, CLS 86 %, BSRAM 227, DSP 59.
Worst core slack is only +0.01 ns (span walker probe step, TMU stageA -> stageGrad,
regBank tex config -> textureMem drain tables, triangle setup).

Changes (all in `ext/SpinalVoodoo`, uncommitted, on top of the earlier edits;
pre-change diff in `~/.cache/ext_before_timing50.patch`):

- `pixel/FramebufferAccess.scala`: `m2sPipe` after the colour/aux plane address math
  (inside `inFlightCount`, so `fbAccess.busy` covers it).
- `core/FramebufferMemSubsystem.scala`: colour/aux pixel read requests
  `s2mPipe().m2sPipe()` into the cached readers.
- `core/PixelPipeline.scala`: span prefetch requests `m2sPipe`d (y-origin swap + row
  multiply -> reader span queues).
- `texture/Tmu.scala`: new stages `stageClz` (abs + clz64 before normalise/ROM),
  `stageMul` (the two 48x18 perspective products), `sampleRequestMid` (packed-layout
  LOD range match / base select, then offset subtract + bank bits). The LOD select is
  flattened (parallel end compares, one-hot "highest LOD wins"), line base + byte is a
  concat. `s2mPipe` after `stageMul` and `stageLod` to cut the ready chain. All new
  stages are in `io.busy`.
- `raster/SpanWalker.scala`: new `RowPrep` state per row: registers the row's edge
  bounds (`xi`/`x2i`, so the per-pixel inside test is two compares) and the
  per-triangle clip/visible bounds; `AdvanceRow` registers the vertical step
  (`rowBase`), `RowPrep` does the optional left step. One or two extra cycles per row.
- `bus/PciFifo.scala`: CPU ingress skid buffer (registered ready to the host bridge)
  + register stage holding the command and its registered address decode; one register
  stage between the drain/passthrough mux and the register bank (`regCmdStage`),
  `floatShadow` / `syncDrained` delayed to match. Sync=Yes drains also wait for
  `regCmdStage`; a command-register write is held while the same command is still in
  the issue / regBank stages; `fifoEmpty` is false while a write sits in any of the new
  stages (reads excluded so status polling still sees empty), so LFB accesses keep
  their ordering.
- `texture/TexLayoutTables.scala`: per-LOD offsets are constant prefix-sum tables
  indexed by (aspect, 16-bit) instead of a 9-deep adder chain (same values mod 2^22).
  Not in t5: it is in t6 (`~/.cache/gowin-builds/rtl_t6`, `hw/gowin-t`, = current
  ext). t6 was still in routing phase 0 after 40 min when this was written (t5 routed
  in ~6 min), so it may miss timing by a little because of PnR variance. Check
  `hw/gowin-t/impl/pnr/*_tr_content.html`; if it isn't clean, use t5.

Pixel check (CoreSim `trace_test`, Verilator 5.052, baseline = ext tree before these
changes; copies under `~/.cache/voodoo-sim/`): logo (stateless), test02, test04,
test28, test_alphabet, qatest00_filter, qatest00_mipmap, quake / tomb / tower (v2
state): final `*_sim.png` **byte-identical** at every iteration (t1..t6). Cycle counts
+0.0..+1.1 % (quake 3 420 443 -> 3 450 481).

### Overnight 2026-10-10: PCIe up, rendering correct, board resets under DDR3 load

What works now (bitstream B16, in flash; copy `~/.cache/overnight/candidates/voodoo_b16.fs`):

- **PCIe enumerates** as `08:00.0 [22c2:1100]` behind chipset port 07:00.0 (PCIEX16(G4)), x4.
  Requires a **cold boot** (board programmed while the PC is fully off, per Sipeed's README);
  warm reboots never link. Re-enumerate after an FPGA reload while the slot clock is live:
  `echo 1 > /sys/bus/pci/devices/0000:08:00.0/remove; echo 1 > /sys/bus/pci/rescan;
  setpci -s 08:00.0 COMMAND=0006`.
- **Live Glide over PCIe**: `sudo sw/glide-homebrew/build/pcie/hb04_pinwheel 0` renders correctly
  at 18.5 frames/s (28.6 MHz core). Logo trace replays in ~1 s. Readback:
  `sudo sw/pcie/lfb-capture out.ppm [front|back]`.

Bugs found and fixed tonight:

1. **Warm reboot** (above).
2. **TLP payload byte order**: PCIe payload dwords have the first byte in bits 31:24;
   `PcieTlpBarTarget` now swaps write data and completion data (`pcieSwap`).
3. **Window register** stole LFB/texture offset 0x250 in every megabyte: now a byte write to 0x250
   (any window) or any access in register space; host `pci_bar.h` uses the byte store.
4. **PcieTlpBarTarget abandoned AXI transactions** on timeout (dropped valid, late response
   desynced everything): AXI valids now held until handshake, orphaned B/R counted and dropped,
   write timeout ~1.8 s, read timeout ~7 ms returning all-ones; host treats all-ones as "busy".
5. **Host tools**: bridge status (0x254) only exists in window 0 - `Bar::status()` selects it.
6. **Framebuffer write-buffer drain** (ext `FramebufferPlaneBuffer`) advanced the BMB command
   address every beat; `BmbLengthFixer` splits 1 KiB lines into 256 B AXI bursts from it, so only
   alternate 256 B chunks were written (half-rendered frames on HW, CoreSim's RAM model hid it).
   Fixed (hold the burst start address); `fpga/test/voodoo/gowin/FbMemPathTest.scala` covers the
   path (BMB -> BmbToAxi4Mem -> Axi4CC -> adapter) incl. arbiter contention.
7. Vendor SerDes `upar_arbiter` missed timing at 100 MHz: TLP clock now 75 MHz (enough for Gen2 x4).
8. `GowinMemStylePhase` no longer forces dual-clock (CDC FIFO) memories to registers.

**Open blocker: the FPGA reloads itself (from flash) under sustained DDR3 traffic**, typically
within seconds of heavy rendering, minutes when idle with the PCIe link up. Not timing (all clocks
clean), not logic (a probe build with DDR3/core/DVI held in reset stays up; the DDR3 stress test
kills it within ~1 ms at gap 0 even at 300 MHz memory clock, RTT_NOM 120 ohm, 8 mA drive).
The dock is currently powered from the PCIe slot only; Sipeed specifies **12 V via the DC jack**.
Next: run the dock from a 12 V supply (and a fan), then re-run the stress test
(`ddr3_test`: `bist --bursts 0x40000 --gap 0`) - it should survive.

Full speed: the core meets 50 MHz with the timing agent's pipelining (`~/.cache/timing50/`);
B17 (50 MHz + every fix above) is building in `hw/gowin-f2` (B14b at 50 MHz was 4 ns short).

**Full-speed candidate (timing clean): B18b** — `~/tang-mega-138k-pro-backup/voodoo_b18b_46mhz.fs`
(build dir `hw/gowin-g3`, RTL `~/.cache/gowin-builds/rtl_b17`): core 46.15 MHz (PLL ODIV1=26; 92 % of the
SST-1's 50 MHz), TLP 75 MHz, DDR UI 100 MHz, all setup/hold TNS 0, with every fix above plus the 50 MHz
pipelining. Not flashed: on slot power a 50 MHz build (B17) went straight into the reload loop.
Flash it once the dock runs from 12 V: `sudo openFPGALoader -c ft2232 -f --verify <fs>`, then power-cycle.
B16 (28.6 MHz, `voodoo_b16.fs`) stays in flash as the known-good image.

### 2026-10-10 afternoon: demo running on 12 V

With the dock on its **12 V DC jack** (not slot power alone) the resets stopped for real workloads:
52 min idle with the link up, minutes of continuous rendering, no reload. The synthetic DDR3 BIST at
command gap 0-4 still reloads the FPGA; gap >= 8 is clean (the Voodoo's adapter stays there).

**Flashed: B18b** (`~/tang-mega-138k-pro-backup/voodoo_b18b_46mhz.fs`), core 46.15 MHz, timing clean.
`sudo sw/glide-homebrew/build/pcie/hb04_pinwheel 0` → **27.8 frames/s** live over PCIe (B16 @ 28.6 MHz:
18.5). After any FPGA reload: `echo 1 > /sys/bus/pci/devices/0000:08:00.0/remove; echo 1 >
/sys/bus/pci/rescan; setpci -s 08:00.0 COMMAND=0006` (or cold boot).

Known issue: a few stray pixel runs (fixed screen positions, a handful of rows near blade edges and
at x < 100) appear while frames render back to back; a lone clear is pixel-exact and repeated readbacks
are identical, so they are real writes from the triangle path. HW-only (CoreSim pinwheel is exact).
Next suspects: write-buffer run/rotate edge cases under real DDR3 latency.

### Sharing the board between agents

Anything that touches the dock (JTAG/openocd, `openFPGALoader` load or flash, PCIe tests, reboots)
must hold the board lock; builds don't need it and can run in parallel:

    fpga/hw/gowin/board-lock -m "why" -- sudo openFPGALoader -c ft2232 -m bitstream.fs
    fpga/hw/gowin/board-lock -m "pinwheel test" -- sudo sw/glide-homebrew/build/pcie/hb04_pinwheel 0
    fpga/hw/gowin/board-lock status        # who has it, since when, why

`ddr3_jtag.py` / `board_diag.py` take the lock themselves (and skip it when run under board-lock).
The lock is an flock on `~/.cache/tang-board/lock` (holder info in `lock.holder`); it is released
automatically if the holder dies. `run_detached.sh` also flocks each build directory, so two agents
can't build in the same `hw/<dir>` at once (use separate dirs).
