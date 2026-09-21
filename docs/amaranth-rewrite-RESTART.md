# Amaranth rewrite: paused 2026-09-21, how to restart

All workers were stopped deliberately on 2026-09-21 ~12:40 (Asia/Bangkok) with
every result committed and pushed. This file is the hand-off for the session
that resumes. The living plan is `docs/amaranth-rewrite-plan.md`; this file
only says where things are and what to start next.

## 1. Where everything lives

| What | Where |
|---|---|
| Rewrite codebase (Mac) | `/Users/hyper/projects/me/vodoo_2-amaranth`, branch `amaranth/rewrite`, origin `github.com/mattkanwisher/vodoo_2` (pushed, tracking) |
| Rewrite codebase (Linux) | `hyper-ms-lin:~/projects/me/vodoo_2-amaranth`, same branch, `git pull` to sync; venv built; vendor IP copied |
| Spinal reference | submodule `ext/SpinalVoodoo` → `github.com/fabPlane/SpinalVoodoo`, branch `fabplane/console-scanout`, pinned `2749df5` (see `amaranth/spinal-ref/README.md`) |
| Old shared checkout | `/Users/hyper/projects/me/vodoo_2` on `codex/voodoo-console-sdram`: **not part of the rewrite any more**; other sessions use it and its Linux twin. Never rsync the rewrite into `~/projects/me/vodoo_2` on Linux |
| Old worktree | `/Users/hyper/projects/me/vodoo_2/.claude/worktrees/amaranth-rewrite`: fully merged into the clone, safe to delete (`git -C /Users/hyper/projects/me/vodoo_2 worktree remove --force .claude/worktrees/amaranth-rewrite`) |
| Gitignored things a fresh clone needs | `fpga/hw/gowin/ip/*` (Gowin vendor IP Verilog; copy from another checkout or regenerate per `fpga/hw/gowin/ip.md`), Verilator 5.052, scala-cli, Gowin EDA 1.9.11.03, `uv` |
| Diff gate evidence | `amaranth/build/diff/<Module>/report.json` (gitignored, kept locally; every module's result is also written into its plan entry) |
| Parity outputs | `amaranth/test-output/<trace>/` (gitignored); md5s recorded in the plan's Phase 7.3 entry and pinned by `amaranth/tests/test_parity.py` |

Environment check after a fresh clone:

```
cd amaranth && make venv && .venv/bin/python -m pytest tests/test_fixed.py tests/test_core_sim_ports.py -o addopts="" -q
cd ../ext/SpinalVoodoo && git log --oneline -1        # must be 2749df5
cd ../../fpga && scala-cli run . --main-class voodoo.diff.GenOne -- --list | head
```

## 2. State at pause

**Done and diffed clean at 1e6 cycles × 3 seeds against the Spinal Verilog:**
Phases 0–6 in full (library layer, frontend, rasterizer, pixel stages, both
TMU variants incl. the Console's no-cache one, framebuffer memory subsystem),
Phase 7.1 HdmiScanout, 7.2 Core + CoreSim + PixelPipeline, Phase 8.0.

**Phase 7.3 parity gate: PASSED first time.** The Amaranth CoreSim's
screenshots for test04, test_alphabet, test16 and the Tomb Raider frame are
byte-identical (md5) to the Spinal CoreSim's, cycle counts equal (tomb −1,
explained). Table in the plan. This is the milestone that makes the Amaranth
core "the core".

**Hardware checkpoints passed on the Tang Retro Console 138K:** the Gowin
compatibility spike (`amaranth/hw/spike`) and the HDMI test-pattern demo
from the ported scanout (`amaranth/hw/hdmi_demo`, built on the Mac via
`run_pnr_mac.sh`).

**Remaining:** Phase 7.4 (port the last SpinalSim tests), Phase 8 (Gowin
Console port and bitstream), Phase 9 (formal, docs, archival decision).

## 3. Threads the stopped workers left open

1. **Core lockstep diff (`make diff MOD=Core`)** — module and stimulus
   committed (`avoodoo/core/core.py`, `tests/diff/core_stimulus.py`,
   `tests/diff/test_diff_core.py`); the 1e6 gate had NOT completed. The
   agent's traffic stimulus hit a wedge (a degenerate triangle case stalls
   the design; both sides identically as far as known) and it was adding
   post-mortem logging (`CpuTraffic.recent`, `CoreCounters` streak start) to
   decide whether that is a stimulus defect or real. Given parity passed on
   real traces, treat it as a stimulus issue first: skip/avoid degenerate
   triangles in `core_stimulus.py`, then run the three `test_diff_core.py`
   rows short, then the 1e6 × 3 gate + backpressure, and tick nothing until
   `report.json` says `ok: true` with scaling counters. The plan's "core +
   core_sim + dbg" tick stands on parity, not on this gate.
2. **`tests/test_parity.py`** — committed with the expectation table; the
   agent was adding a "stall-signature check" (the harness's MMIO timeout /
   `Shutdown after` line). Verify the test passes as committed
   (`AVOODOO_PARITY=run .venv/bin/python -m pytest tests/test_parity.py`) and
   finish that check if it is half-done.
3. **PixelPipeline** — fully done (gate clean); its plan entry landed in the
   WIP snapshot. Nothing open.

## 4. Restart procedure

Spawn Opus 5 subagents, one task each, all working ONLY in
`/Users/hyper/projects/me/vodoo_2-amaranth`. Every brief must carry the
standing rules (§6 of the plan, restated):

- 1:1 port, Scala path + structure map in the docstring, Spinal names kept;
  Spinal plain `Reg` → `reset_less=True`; large uninitialised memories
  through `strip_meminit`; RAM attributes via `spinal_fifo_ram_attrs`.
- "Done" = pysim tests + `make diff MOD=X` at 1e6 cycles × seeds 1,2,3 plus
  one backpressure run (`DIFF_ARGS="--valid-density 0.9 --ready-density 0.12"`),
  `report.json` `ok: true`, activity counters scaling with cycles (a parked
  run is not a pass; use responders from `tests/diff/*_stimulus.py`), plus
  `tests/diff/test_diff_<x>.py` at 20k × 3. `rm -rf build/diff/<X>/sim` after
  quoting the report (disk is tight). Gates sequential, not parallel.
- Add-only under `fpga/src/voodoo/diff/` (GenOne rows), add rows in
  `amaranth/tools/diff/registry.py`, tick + entry in the plan. Never edit
  `ext/SpinalVoodoo/**` (changes go on the fork branch and move the
  submodule pointer), `avoodoo/lib/**`, `tools/diff/**` internals, `emu/**`
  unless the task is about them.
- Commit only own files via `git commit -- <paths>` (shared index; the
  registry/plan/GenOne files are shared: stage only your hunks, never revert
  others'), trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`,
  push `amaranth/rewrite`. Unique scratchpad file names.
- Gowin PnR: one `gw_sh` per box. Linux first (`hyper-ms-lin`, rsync only
  into `~/projects/me/vodoo_2-amaranth`, or just `git pull` there); if a
  `gw_sh` is already running there, the Mac fallback is
  `amaranth/hw/hdmi_demo/run_pnr_mac.sh`'s pattern. Never program the board;
  hand the user the `.fs` path and the `program_debug_sram.sh` command
  (case buttons released). SD-card core name `voodootang-am.bin`; never
  overwrite `voodootang.bin`.
- The Spinal reference every diff runs against is the submodule at
  `2749df5` plus the fpga/ tree on this branch; both are committed here.

### Task list, in dependency order

**T1 (first, small): finish the Core diff** — thread §3.1 above.

**T2: Phase 7.4, SpinalSim tests not yet covered.** Upstream
`ext/SpinalVoodoo/test/*.scala`: check which intents are already in
`amaranth/tests/` (most are: TriangleSetup, Rasterizer, SpanWalker, Tmu*,
ColorCombine, Write, Dither, AddressRemapper, RegisterBank, PciFifo,
BmbBusInterface, HdmiScanout, StreamWhile, FramebufferPlane*). Port what is
missing (candidates: `TrianglePipelineThroughputTest`, `BmbDriverDebugTest`,
the DE10-independent parts of `de10/*`) as pysim tests. Tick "SpinalSim
tests ported".

**T3: Phase 8.1, `avoodoo/gowin/core_gowin.py`, `axi4_to_ddr_native.py`,
`ddr_native_cdc.py`, `address_map.py`.** Scala: `fpga/src/voodoo/gowin/
{CoreGowin,Axi4ToGowinDdrNative,GowinDdrNativeClockCrossing,GowinAddressMap}.scala`
(this branch's copies). AXI bridges and `Axi4AddressRelocate` are already in
`avoodoo/lib/axi*.py` and `avoodoo/gowin/`. Diff against
`fpga/test/voodoo/gowin/Axi4ToGowinDdrNativeTest.scala` stimulus; the DDR
native side needs a responder. The clock crossing carries
`cross_clock_payload` attributes (see `lib/cdc.py`); Spinal's SDC
exceptions for those registers must stay hand-written in the Console SDC.

**T4 (parallel with T3): Phase 8.2 demo/UI modules**, one agent per 2–3
files: `demo_catalog.py`, `demo_font.py`, `demo_menu.py`, `demo_trace_rom.py`,
`demo_axi_lite_player.py`, `text_osd.py`, `debug_hud.py`, `core_debug_log.py`,
`console_buttons.py`, `snes_pad.py`, `bl616_link.py`, and last `bar_rv32.py`
(not on the demo path). Scala under `fpga/src/voodoo/gowin/`; tests under
`fpga/test/voodoo/gowin/*.scala` give the intents. GenOne rows for these are
add-only (they are fpga/ components, in scope via `project.scala`).

**T5: Phase 8.3, `avoodoo/gowin/tang_console_138k.py` + `amaranth/hw/console/`.**
Exact port names of the Spinal `TangConsole138k` (generate its Verilog with
`make rtl-console` in `fpga/` and read the port list) so
`fpga/hw/gowin/console/board_top.v`, the CST/SDC and `build_console.tcl`
work unchanged; the Console config is `Config.voodoo1().copy(useTexFillCache
= false, ...)` exactly as `CoreGowinGen.scala` builds it (see
`tools/diff/registry.py` `_C_CONSOLE`). Lesson from the HDMI demo: a clock
domain must be *used but not defined* for Amaranth to emit its clk/rst
ports. RAM-style attributes come from Amaranth, so
`force_distributed_fifo_ram.py` is not used; check BSRAM count against the
Spinal build's PnR report (≤ 340). Whole-top diff via a GenOne row if the
harness can hold the DDR native side with a responder; otherwise diff
CoreGowin and rely on the per-module gates for the rest, and say so.

**T6: Phase 8.4, bitstream.** ~31 min PnR on Linux (Mac ~57 min fallback).
Compare utilisation and per-domain Fmax with the Spinal build
(`fpga/hw/gowin/console/impl/` in the old checkout has its reports). SRAM
load first, then `voodootang-am.bin` on the SD card. Expected on hardware:
menu, the four packed SDK traces, HUD, USB pad, CDC log, exactly as the
Spinal core.

**T7: Phase 9.** Re-express the 10 Spinal formal properties with sby
(OSS CAD Suite is at `~/opt/oss-cad-suite-*` on Linux), write
`amaranth/README.md`'s build/test/diff/program sections properly, update
`AGENTS.md`, decide the Spinal tree's fate. Candidates for a v2 list are
already scattered through the plan entries (AOTHER dead factor, cold-miss
pre-capture texel, FramebufferAccess ordering hazard, 17-bit depth-bias
wrap, 4 MiB SimMemBusMemory pending row, FifoCC per-instance vs shared
reset synchroniser).

### Housekeeping to do at restart

- Delete the old worktree (command in §1) once `git -C <worktree> status`
  shows nothing you still want.
- The main `vodoo_2` checkout's submodule still carries the reference edits
  as uncommitted files; when its branch merges this one, `git submodule
  sync && git submodule update` there swaps them for commit `2749df5`.
- `fpga/hw/gowin/ip/` on a fresh clone: copy from another checkout.
