# Console-in-a-box: simulating the whole Tang Console top, HDMI included

Status: first end-to-end simulator working, 2026-09-21. Companion to
`amaranth-rewrite-plan.md` §4.
Goal: run **`TangConsole138k` unmodified** under Verilator, drive it the way the
board does (buttons, HID, trace player), and get **PNG frames off the HDMI
port** — so a logic change is judged in seconds to minutes instead of a
31-minute PnR plus an SD-card walk.

## 0. What exists and what is missing

| Piece | Today | Gap |
|---|---|---|
| Core (rasterizer → framebuffer) | `CoreSim` + Glide harness, screenshots from `sim_read_fb()`, 86Box trace diff | HDMI scanout is **built out** (`enableHdmiScanout = false`, hdmi domain held in reset) |
| Scanout | `HdmiScanoutTest` counts `active`/`newLine`/`newFrame` on an 8×4 timing | Never run at 640×480, never fed by a real framebuffer |
| OSD / HUD / menu / player / buttons | Per-module SpinalSim tests sampling a few `(x,y)` | Nothing assembles a frame; nothing runs them together |
| DDR path (`Axi4ToGowinDdrNative`, CDC) | Unit test with hand-written stimulus | No behavioural DDR native-port model, so the top never ran in sim |
| `TangConsole138k` | Only ever seen a Gowin PnR | Zero simulation |

So the demo path — power-up → menu → select → `DemoAxiLitePlayer` replays a
baked trace ROM → core renders → scanout → HDMI with OSD/HUD — has **never
been simulated end to end**. That is the path every bring-up bug has been in.

Key observation: the console **needs no Glide and no C++ Glide harness** to
show a picture. The trace ROMs (`hw/gowin/console/demo_*.bin`) are baked into
the design; pressing "select" in sim renders test04 / test16 / alphabet / the
logo spin exactly as on hardware. The whole thing can be driven from the
board-level pins.

## 1. Architecture

```
                 ┌─────────────────────────── Verilator ───────────────────────────┐
  C++/cocotb     │  TangConsole138k  (the real generated Verilog, no sim variant)  │
  testbench ───▶ │   keyUserN keyResetN hid1 hid2 uartLink… usbOnline              │
                 │   ctrlClk  clk(core)  hdmiClk  ddrClk                           │
                 │   io.ddr ◀──────────────▶  C++ GowinDdrNative model (12 MB)     │
                 │   hdmiR/G/B/Hs/Vs/De ─────▶ frame grabber ─▶ frame_NNN.png      │
                 │   debugTx (Flow[8]) ──────▶ cdc_log.txt                         │
                 │   jtag{Debug,Framebuffer,Writes}[16] ─▶ polled each frame       │
                 │   ledCalib/Done/Wedge/Heartbeat ──▶ asserted / logged           │
                 └─────────────────────────────────────────────────────────────────┘
```

Decisions:

1. **DUT is the unmodified top.** No `ConsoleSim` Scala/Amaranth wrapper. The
   DDR native port, HDMI parallel bus and buttons are already at the boundary;
   everything Gowin-specific (PLLs, `dvi_tx_top.v`, DDR3 IP, `GW_JTAG`,
   `iosys_hid.v`) lives in `board_top.v`, outside the DUT. That is exactly the
   piece Verilator should not model.
2. **Models in C++ (or cocotb), not HDL.** The DDR model, frame grabber, HID
   and CDC-log sink attach to ports by name. Because plan §8.3 keeps the
   Amaranth `tang_console_138k.py` **port-for-port identical** to the Spinal
   top, one testbench serves both cores — build it once on Spinal today, point
   it at the Amaranth Verilog in Phase 8 and diff the frames.
3. **Four clocks, integer periods.** Verilator is cycle-based; pick periods in
   ps that keep the real ratios close and the LCM small:

   | Domain | Board | Sim period | Note |
   |---|---|---|---|
   | `clk` (core) | 30 MHz | 33 334 ps | 20 MHz starved scanout; board uses 60 MHz / 2 |
   | `hdmiClk` | 25.175 MHz | 40 000 ps (25 MHz) | 640×480 DMT; 0.7 % fast is irrelevant to logic |
   | `ctrlClk` | 50 MHz | 20 000 ps | |
   | `ddrClk` | DDR3 IP user clock (read it from `generate_ddr3_300.tcl`) | matching integer | make it a CLI flag; sweep it |

   The CDC FIFOs make *correctness* independent of ratios, but the scanout
   underflow bug class depends on them, so the harness must let you shift
   `ddrClk` and DDR latency without rebuilding.
4. **Time is not free.** 640×480 at 25 MHz is 420 000 pixel clocks per frame,
   plus 2.1 M ctrl clocks and 840 k core clocks in the same window. A
   138K-class netlist under Verilator runs on the order of 0.5–2 M
   cycles/s of the fastest clock, so **roughly 1–5 s per frame, idle**, and
   more while the core is rendering. Budget: menu screenshot ≈ 10 s, one
   demo rendered and displayed ≈ 1–3 min. That is the iteration loop.

## 2. Work items

### 2.1 DDR native-port model (blocks everything)

`amaranth/emu/console/ddr_model.{h,cpp}` — behavioural `GowinDdrNative`
slave, bound to `io.ddr` ports:

- `cmdReady`/`cmdEn`/`cmd`/`addr`/`burst`, `wrDataRdy`/`wrDataEn`/`wrDataEnd`/
  `wrDataMask`, `rdData`/`rdDataValid`/`rdDataEnd` — read the Gowin DDR3 UI
  semantics off `Axi4ToGowinDdrNative.scala` and the IP user guide; the
  bridge is "one outstanding", so the model only has to be right for that.
- Backing store: flat 12 MB `std::vector<uint8_t>` (4 MB fb + 8 MB tex map
  per `GowinAddressMap`), sparse/mmap'd if it ever grows.
- Knobs (CLI/env): `initCalibComplete` delay, command-accept latency, read
  latency (fixed or uniform random in `[lo,hi]`), `wrDataRdy` stall
  probability, and a strict mode that aborts on protocol violations
  (`cmdEn` without `cmdReady`, `wrDataEn` past `wrDataEnd`, mask width).
- Peek/poke API so the testbench can pre-load a framebuffer image
  (scanout-only tests) and dump memory on failure.
- Unit-test it against the existing `Axi4ToGowinDdrNativeTest` stimulus
  before trusting it: the two "unaligned" tests must produce the same lane and
  mask behaviour against the model as against the hand-driven signals.

### 2.2 Frame grabber

`amaranth/emu/console/hdmi_grab.{h,cpp}`:

- Sample `hdmiR/G/B` on every `hdmiClk` posedge where `hdmiDe`; new line on
  `hdmiHs` falling, new frame on `hdmiVs` falling (check polarity against
  `VideoTiming.dmt640x480`).
- Assert geometry: 640 active per line, 480 lines per frame, H/V totals 800 ×
  525. Any deviation is a test failure — this alone catches half the timing
  bugs without looking at a picture.
- Write `frame_%04d.png` (stb_image_write, one header, no deps) into
  `output/console/<test>/`. Also keep the last frame in memory for pixel
  assertions.
- `grab_every_n` and `grab_after_ms` options so a 3-minute run isn't 180
  PNGs; `--live` optionally pipes raw RGB to a FIFO for
  `ffplay -f rawvideo -pixel_format rgb24 -video_size 640x480 -i pipe`.
  Novelty, not the workflow — PNG + `open` is faster in practice.

### 2.3 Board-level stimulus

`amaranth/emu/console/console_tb.cpp` (or a cocotb `test_console.py` with
`cocotb_tools.runner.Verilator`, already pinned in the venv — pick one; C++
is 3–5× faster per cycle for a testbench this size, cocotb is nicer for
writing the twenty scenarios. Recommendation: **C++ models + cocotb driver**,
models exposed via DPI so cocotb only steps time and reads pins).

Stimulus map (all real-board semantics, see `ConsoleButtons.scala`,
`SnesPad.scala`, `TangConsole138k.scala`):

| Board input | Sim | Purpose |
|---|---|---|
| `keyUserN`/`keyResetN` | press/hold with ms durations | menu down/select; MODE-at-boot → `debugMode` |
| `hid1`/`hid2` | SnesPad bitmask | USB pad path through `BufferCC` |
| `uartLink`, `uartLastCmd`, `uartRxCount`, `uartHidCount` | constants or scripted | HUD line content |
| `usbOnline` | 0/1 | CDC log gating |
| `ddrReset`/`hdmiReset`/`ctrlReset`/core reset | staged like `board_top.v` (DDR calib first) | reset ordering bugs |

Sinks: `debugTx` Flow → `cdc_log.txt`; `jtag*` pages sampled at each `Vs`
and written as CSV; LEDs asserted per scenario (`ledWedge` must stay low —
make that a global check).

### 2.4 Scenarios (each a `make console-sim TEST=…` target, each with a golden)

1. **`boot`** — reset, wait for `ledCalib`, one frame. Golden: the menu OSD,
   navy background, cursor on entry 0. Assert HUD `CAL=1`, `ledHeartbeat`
   toggling, geometry OK. *(~10 s)*
2. **`hud-debug`** — hold MODE through reset; assert `debugMode`, HUD lines
   2–3 show the counters (`scanoutPrefetchCount` etc. monotonic).
3. **`menu-nav`** — down ×3, capture; pad Down via `hid1`, capture; compare
   cursor row. Also `other` submenu.
4. **`demo-<name>`** for `test04`, `test16`, `alphabet`, `logo_spin` — select,
   wait for `player.done` (poll a jtag page or just `ledDone`), grab the next
   two frames. Golden A: byte-identical to the previous run (regression).
   Golden B: **matches `CoreSim`'s `output/<test>/screenshot.png`** for the
   same trace, modulo gamma/OSD — this is the test that proves the scanout
   reads the buffer the pipeline wrote, and it's the one that has never
   existed.
5. **`loop-logo`** — logo spin with `loop`; 10 frames; assert no `ledWedge`,
   no scanout underflow bit, frame N ≠ frame N+1 (it spins).
6. **`ddr-stress`** — `demo-test16` with DDR read latency random 8–64 and
   `wrDataRdy` stalls 20 %; frames still identical to scenario 4. Then sweep
   `ddrClk` ±20 %. This is the underflow / CDC bug reproducer.
7. **`scanout-only`** — pre-load the DDR model with a known PNG at the front
   buffer address, enable scanout, grab one frame, expect the PNG back. Fast
   (no rendering) and isolates scanout + CDC + address math.
8. **`wedge`** — force a bridge timeout (DDR model stops answering); assert
   `ledWedge`, `player.enable` drops, HUD shows it, CDC log has the line.
9. **`glide-live`** *(optional, later)* — an extra sim-only AXI-Lite/BMB
   injection so the existing Glide `libglide2x` harness drives the console
   top instead of `CoreSim`, giving DOSBox-X → HDMI. Needs a testbench-side
   mux onto `core.io.bar` and therefore *does* need a wrapper top. Defer
   until 1–8 are in; the trace player already covers the demo path.

### 2.5 Build plumbing

- `amaranth/Makefile`: `console-rtl` (Spinal today: `make -C ../fpga
  rtl-console`; Amaranth later: `gen.gen_console`), `console-model`
  (verilate `TangConsole138k.v` with `--timing`-free multiclock, `-O3`,
  `--threads 4`, `-Wno-fatal`, `--trace-fst` only in `TRACE=1` builds),
  `console-sim TEST=`, `console-golden TEST=` (bless), `console-all`.
- Verilate on the Mac; it's one big C++ compile, not a 32-thread job (same
  finding as CoreSim in `AGENTS.md`). Keep `obj_dir` out of rsync.
- `force_distributed_fifo_ram.py` runs on the Gowin path only; the sim uses
  the raw Verilog.
- Goldens under `amaranth/test/console/golden/<test>/frame_0001.png`, small
  enough to commit. Compare exact; print a diff PNG (abs diff × 8) on
  mismatch so you can *see* where it went wrong.
- `TRACE=1 make console-sim TEST=demo-test04 TRACE_FROM_FRAME=2` — FST only
  from frame N, because a full-run FST of this design is tens of GB.

### 2.6 Amaranth hookup (Phase 8)

Nothing new: `console-rtl` points at `rtl/TangConsole138k.v` from
`tang_console_138k.py`, and `console-all` must produce **byte-identical
frames** to the Spinal goldens. That becomes the Phase 8 exit gate before any
PnR is spent, and turns 8.4's "compare on hardware" into "compare on hardware
what already matched in sim".

## 3. What this proves, and what it cannot

Proves: register/menu/player logic, framebuffer address math, scanout →
CDC → pixel timing, OSD/HUD placement, reset ordering, DDR protocol use,
wedge handling, and CDC-FIFO behaviour under adversarial latency. It also
gives Phase 8 a bit-exact acceptance test that costs minutes, not an
afternoon.

Cannot prove: DDR3 calibration (`initCalibComplete` is a model flag), PLL
lock, TMDS serialiser/`dvi_tx_top.v`, BL616 UART parsing in `iosys_hid.v`,
`GW_JTAG`, timing closure, and BSRAM/DSP inference. Those stay on the
bitstream path — but after this, that path runs to confirm, not to discover.

## 4. Order and rough effort

| Step | Depends on | Effort |
|---|---|---|
| 2.1 DDR model + protocol check against the unit-test stimulus | — | 1 day |
| 2.2 grabber + geometry asserts | — | ½ day |
| 2.3 testbench skeleton, clocks, resets, `boot` scenario | 2.1, 2.2 | 1 day |
| 2.4 scenarios 1–5, goldens | 2.3 | 1–2 days (expect to find bugs; that is the point) |
| 2.4 scenario 7 (`scanout-only`) and 4-B (CoreSim PNG parity) | 2.3 | ½ day |
| 2.4 scenarios 6, 8 | 2.3 | ½ day |
| 2.5 Makefile, FST-from-frame, diff PNG | 2.3 | ½ day |
| 2.6 Amaranth switch | Phase 8.3 | hours |

First useful picture (`boot` showing the menu) is reachable at the end of day
two. Everything after that is turning hardware-only debugging into
`make console-sim`.

## 5. First finding: disappearing logo

The initial `logo` scenario reproduced the hardware symptom: the first raw
framebuffer dump contained the orange chevron, but the delayed HDMI capture
was black. The DDR model, framebuffer addresses, gamma LUT, and scanout were
all healthy; at 30 MHz the scanout completed with no FIFO underflow.

The fault was in `gen_logo_spin.py`. Every animation frame selected and
fast-cleared both the front and back buffers. Immediately after a swap, the
next frame therefore erased the buffer that HDMI had just begun displaying.
The stream now clears only the current back buffer. The end-to-end `run-logo`
scenario captures 5,012 non-black output pixels after the delay and fails if
the image is effectively black or the HDMI FIFO underflows.
