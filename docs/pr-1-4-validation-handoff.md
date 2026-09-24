# PR #1–#4 validation handoff

Last updated: 2026-09-22

This document records the validation performed on pull requests #1 through #4
in `mattkanwisher/vodoo_2`.  It is intended for an agent fixing the remaining
simulator and Tang Console hardware failures.  Do not infer "works" from a
recognizable screenshot alone: use the merge gates at the end of this file.

## Repository layout and test boundaries

- Parent repository: `https://github.com/mattkanwisher/vodoo_2`
- FPGA core submodule: `ext/SpinalVoodoo`
- Core-only renderer test: SpinalVoodoo `emu/test/trace_test` (`CoreSim` versus
  the 86Box reference model)
- Whole-console test: generated `TangConsole138k` RTL plus
  `amaranth/emu/console/console_tb.cpp`
- Hardware target: Sipeed Tang Console/Retro Console 138K,
  `GW5AST-LV138PG484AC1/I0`, device version C
- Gowin build host and JTAG host: `hyper-ms-lin`

The main Mac checkout and the saved Linux checkout both contain unrelated
dirty work.  Use a separate worktree for every PR or prospective merge.  Do
not reset, clean, or copy an entire dirty checkout over a test worktree.

## Current PR status

| PR | Branch relationship | State | Validation decision |
|---|---|---|---|
| [#1](https://github.com/mattkanwisher/vodoo_2/pull/1) | `cursor/setup-dev-environment-1d1c` → `main` | Merged as `5ee7b1a7a7eb07d1f564f9376351ec0bab91efbf` | Accepted; see residual CoreSim discrepancies below |
| [#2](https://github.com/mattkanwisher/vodoo_2/pull/2) | `cursor/opensource-glide-game-1d1c` → PR #1 branch | Open draft | Do not merge: textured torus is visibly wrong |
| [#3](https://github.com/mattkanwisher/vodoo_2/pull/3) | `cursor/glquake-voodoo-sim-1d1c` → PR #1 branch | Open draft | Do not merge: wrong framebuffer/placement and large pixel mismatch |
| [#4](https://github.com/mattkanwisher/vodoo_2/pull/4) | `cursor/console-menu-homebrew-demos-1d1c` → `codex/voodoo-console-sdram` | Open draft | Do not merge: two console demos scan out black; hardware DDR calibration remains low |

## PR #1: environment and homebrew demo foundation

PR #1 was merged.  Ten homebrew traces were built and replayed through
CoreSim at 320×200.  Every trace produced a visible frame.  Simple demos were
exact or nearly exact; depth, alpha, fog, and texture-heavy demos had larger
known pixel differences.  The acceptance decision was therefore based on
usable visual output and the scope of this foundational PR, not a claim that
all renderer parity bugs were solved.

Two follow-up submodule fixes were required during validation:

1. A CoreSim idle-wait deadlock was fixed and merged in SpinalVoodoo.
2. A clean-build regression referencing the removed
   `enableHdmiScanout` setting was fixed.  A clean Scala → Spinal → Verilog →
   Verilator → C/C++ → `trace_test` link completed afterward.

The combined SpinalVoodoo fix is commit
`4135183923493b7342e1a66a7704597379e932c3`.  Ensure a CoreSim test binary is
built from that commit or later.  An older executable at submodule commit
`b4c4070` can spend millions of status reads waiting for a queued swap and is
not a valid regression runner.

## PR #2: textured torus

Observed result:

- The intended environment-mapped textured torus renders mostly thin magenta
  contours instead of the expected shaded/textured solid.
- CoreSim reported 83,248 mismatched pixels.
- A diagnostic build of the same geometry with texturing disabled and
  Gouraud shading enabled rendered correctly, with only 77 edge-pixel
  mismatches.

Conclusion: geometry submission, triangle coverage, and basic interpolation
are working.  The failure is in the texture/color-combine path.  Start with
texture coordinate/Lod setup, texture fetch formatting, and
`fbzColorPath`/`textureMode` combine semantics.  Do not spend the first debug
cycle on torus mesh generation.

Required retest:

1. Rebuild `trace_test` from SpinalVoodoo `4135183` or later.
2. Replay the actual textured trace against CoreSim and 86Box.
3. Inspect reference, simulation, and diff PNGs.
4. Require a solid, correctly colored torus and explain any remaining pixel
   delta before merging.

PR #2 does not alter the Tang Console synthesis inputs, so it does not need a
Gowin bitstream unless its fix changes shared RTL.

## PR #3: GLQuake

Validation built Mesa 3.4.2 plus GLQuake in an i386 container and captured a
one-second trace.  SIGTERM appended a corrupt repeated tail, so the trace was
sanitized to 403,965 valid entries before replay.

Observed result:

- CoreSim produced a recognizable Quake console.
- 132,455 of 307,200 pixels differed from the reference.
- The selected framebuffer and image placement were wrong.
- This is not merely a small rasterization-edge tolerance issue.

Conclusion: PR #3 is useful diagnostic work but not a passing renderer result.
Investigate buffer selection/presented offset, row stride, display dimensions,
and the timing of the final swap before pursuing small color differences.

Trace-capture warning: do not trust an entry count alone.  Validate the final
records and reject/clamp a SIGTERM tail containing repeated commands.  The
later PR #6 clamp only catches a much larger repeat pattern and did not catch
the approximately 375k-repeat corruption seen in shorter captures.

PR #3 does not alter the Tang Console synthesis inputs unless a proposed fix
touches shared RTL.

## PR #4: homebrew demos in the Tang Console menu

PR #4 adds three packed ROM banks and menu entries:

- `demo_hb_pinwheel.bin`
- `demo_hb_depth.bin`
- `demo_hb_alpha.bin`

### Unit and direct CoreSim results

The focused Gowin Scala tests passed: 43 tests, 0 failures.  Direct CoreSim
replay produced recognizable frames for all three demos, but did not pass
pixel parity:

| Demo | CoreSim mismatches | Notable result |
|---|---:|---|
| Pinwheel | 52,722 / 307,200 | Visible geometry; 104 sim-black mismatches; substantial color/parity difference |
| Depth | 15,240 / 307,200 | Visible geometry; zero sim-black mismatches; colors differ from reference |
| Alpha | 43,803 / 307,200 | Visible geometry; 24 sim-black mismatches; alpha/color result differs strongly |

These differences overlap known shared renderer problems from PR #1.  They
should be recorded, but the decisive PR #4 regression is in the whole-console
path.

### Whole-console results

The console harness was extended locally to navigate all seven real menu
items (`logo`, `test04`, `test16`, `alphabet`, `hb-pinwheel`, `hb-depth`, and
`hb-alpha`).  It operates the real menu through `hid1`, not a direct trace
shortcut.

| Demo | Raw DDR framebuffer | HDMI capture | Result |
|---|---|---|---|
| Pinwheel | Correct, nonblack pinwheel | Black | Fail; underflow set, FIFO occupancy zero |
| Depth | Correct, nonblack overlapping triangles | Black | Fail; underflow set, FIFO occupancy zero |
| Alpha | Correct, nonblack blended triangles | Visible | Pass; frame aligned, no underflow |
| Existing `test04` baseline | Correct | Visible | Pass on the same generated RTL |

Important observations:

- Pinwheel and depth wrote substantial nonzero data.  Their failure is after
  rendering, in framebuffer presentation/prefetch/scanout.
- Pinwheel/depth perform their first completed swaps at roughly 185/235 ms.
  Alpha and `test04` do not present until roughly 487/823 ms and work.
- Merely allowing the packed trace player to finish did not recover HDMI.
- Waiting several minutes of wall-clock simulation for nonblack HDMI did not
  recover depth.
- At failure, `prefetchBase` remained on the old framebuffer and the scanout
  cache had zero occupancy.  This is consistent with a persistent scanout
  refill/prefetch stall following the early loaded swap.
- Swap register `0x128` is already marked `syncRequired` by `PciFifo`; do not
  assume the first fix must be an arbitrary delay in `DemoAxiLitePlayer`.

Likely debug area: `ext/SpinalVoodoo/src/voodoo/hdmi/HdmiScanout.scala`, the
framebuffer reader/cache feeding it, and recovery when the FIFO underflows or
the front buffer changes while a source frame is partially prefetched.

### Linux bitstream and physical board result

PR #4 commit `55b0a56e93061d73bbf4d848dbb8a3429e6527b9` with SpinalVoodoo
`b4c4070f941bbbade7c8334936f40de07618b1aa` was built in an isolated Linux
worktree using Gowin EDA 1.9.11.03 Education.

- Synthesis: pass
- Placement: pass
- Routing: pass
- Bitstream generation: pass
- Resource use: 48% logic, 71% CLS, 56% BSRAM, 20% DSP
- Generated `.bin`: 5.0 MiB
- `.bin` SHA-256:
  `ae94ba361d022b494d981ea96694b6e4d923da23248b87358ad90aed53da163b`
- SRAM programming over Linux JTAG cable index 1: pass
- Programmer user code: `0x0000C5C9`
- Programmer status: `0x00022230`

The monitor showed a stable 640×480 debug/menu image, proving that the FPGA,
PLL, TMDS pins, and basic HDMI path were active.  The HUD remained at:

```text
CAL=0  W=0  UF=0
```

DDR calibration never reached ready, so no demo could be launched or accepted
on hardware.  `UF=0` only proves that the idle/debug image did not underflow;
it does not clear the simulated pinwheel/depth failure.

### DDR IP trap

The clean PR #4 wrapper is the older 16-bit DDR design.  Its known matching
encrypted DDR IP file has SHA-256:

```text
687cb7ea5f113aef2df19042c2317ebd4dfdd3794e6a2e3f69fe5709d8a8e13a
```

Copying the dirty Linux checkout's newer 32-bit IP into the clean PR produced
`PA2014` errors for unconnected `dqs[2:3]` differential buffers.  The dirty
checkout also contains uncommitted 32-bit board, CST, 30 MHz core-clock, and
JTAG-debug changes.  Do not mix that IP with the clean 16-bit wrapper and do
not treat those uncommitted experiments as part of PR #4.

The 16-bit IP routes but remains stuck at `CAL=0` on the physical board.  The
next hardware agent must decide whether to repair/regenerate the 16-bit IP or
finish, review, and commit a coherent 32-bit wrapper/IP/CST change.  Do not
silently copy only one side of that interface.

## Recommended fix order

1. Fix PR #2's texture/color-combine defect with the Gouraud diagnostic as a
   control.
2. Fix PR #3's presented framebuffer/stride/placement before color-detail
   work.
3. Fix PR #4 whole-console scanout recovery and add pinwheel/depth/alpha to
   the committed console harness regression targets.
4. Resolve the Tang Console DDR configuration as one coherent change; require
   `CAL=1` on the board.
5. Rebuild the bitstream on Linux, program SRAM, run every menu demo, and
   photograph/capture the resulting display.

## Merge gates

A PR is mergeable only when all applicable gates pass:

1. Its focused unit tests pass.
2. Every new trace completes in CoreSim and produces the intended geometry,
   color, depth, texture, and blending result.  Pixel differences must either
   be fixed or explicitly bounded and explained.
3. If console ROMs/menu/scanout/shared RTL changes, the whole-console model
   must show the correct HDMI frame with `frame-aligned=1`, no black capture,
   and no unexplained underflow.
4. If synthesis inputs change, the Gowin build must route and produce an
   artifact.
5. On physical hardware, require HDMI output, `CAL=1`, `W=0`, and `UF=0`, then
   run and visually inspect every added demo.
6. Merge into the actual stacked base first.  Only advance the stack toward
   `main` after the lower PR's gates pass.

