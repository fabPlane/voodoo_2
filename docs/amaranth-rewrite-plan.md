# SpinalVoodoo → Amaranth rewrite plan (Gowin first)

Status: **draft v1, 2026-09-20**. Living document; agents update the checklists
at the bottom of each phase as work lands.

## 1. Goal

Re-implement the SpinalVoodoo 3dfx Voodoo 1 core and this repo's Gowin port in
[Amaranth](https://github.com/amaranth-lang/amaranth) (Python HDL), producing a
Tang Retro Console 138K bitstream that is functionally identical to today's
SpinalHDL build. "Identical" is measured, not asserted: same Glide test
screenshots, same trace-replay cycle counts within a stated tolerance, same
hardware behaviour on the Console.

Non-goals for v1:

- Improving the architecture. Port first, refactor later. Every module is a 1:1
  translation with the same interface, pipeline depth, and arithmetic widths.
- Open-source Gowin PnR. Apicula's GW5AST-138C database is not hardware-valid
  yet (see `fpga/hw/gowin-open/README.md`). Amaranth emits Verilog; Gowin EDA
  1.9.11.03 (`gw_sh`) stays the synthesis/PnR tool, on `hyper-ms-lin` as per
  `AGENTS.md`.
- Replacing the encrypted Gowin DDR3 / USB / DVI / PCIe IP. Vendor Verilog in
  `fpga/hw/gowin/**` is reused unchanged.
- The DE10-Nano, Artix AC701, and Tang Mega Pro (PCIe) targets. They stay on
  SpinalHDL until the Console build is at parity. The port must not make them
  harder later (keep the AXI boundary contracts).

## 2. What exists today (ground truth)

| Piece | Where | Size | Notes |
|---|---|---|---|
| Upstream core | `ext/SpinalVoodoo/src/voodoo/**` (git submodule, **read-only**) | ~17 k lines Scala, 57 files | DE10 wrapper included (skip) |
| Gowin/Artix ports | `fpga/src/voodoo/{gowin,artix}` | ~3.8 k lines, 25 files | Console top `TangConsole138k`, Dock top `TangMega138kPro` |
| Vendor glue | `fpga/hw/gowin/console/board_top.v` + `*.v`, `build_console.tcl`, CST/SDC | 268-line board top | PLLs, DDR3 native IF, DVI_TX, `iosys_hid`/`uart_fixed`, CDC USB |
| Glide test rig | `ext/SpinalVoodoo/emu/sim/harness/sim_harness.cpp` + Glide2x SDK tests | C API `sim_*` | Verilator on `CoreSim.v`; reaches into ~150 internal Spinal signal names |
| Golden outputs | `ext/SpinalVoodoo/test-output/{logo,quake,screamer2,test04,test16,test_alphabet,tomb,valley_of_ra}` | PNGs | Regression reference |
| SpinalSim/formal tests | `ext/SpinalVoodoo/test/*.scala`, `fpga/test/voodoo/gowin/*.scala` | 32 + 12 | Port the intent, not the framework |

SpinalHDL library features the core leans on (counted uses):

| Feature | Uses | Amaranth answer |
|---|---|---|
| `AFix` fixed point (`SQ`/`UQ`, `.fixTo` ×52, `.floor` ×16, `.sat` ×8, `.roundHalfDown` ×4, `.ceil`, `.round`, `.truncated`) | 196 | `avoodoo.lib.fixed` — own implementation mirroring AFix width/rounding rules exactly (or thin wrapper over `amaranth.lib.fixed` if the installed release has it **and** the semantics match; Phase 0 decides) |
| `Fpxx` custom float + `Fpxx2AFix` | 25 + 13 | `avoodoo.lib.fpxx`, ported 1:1 |
| `Bmb` bus + `BmbArbiter`/`SourceRemover`/`LengthFixer`/`Aligner`/`AlignedSpliter`/`ContextRemover`/`BmbRouting` | 122 | `avoodoo.lib.membus` — Bmb-equivalent `wiring.Signature` (cmd/rsp streams) plus the adapters actually used |
| `Stream`, `StreamFifo`, `StreamFifoCC`, `StreamFork`, `StreamJoin`, `StreamDemux`, `StreamArbiterFactory`, `StreamWhile` | ~110 | `amaranth.lib.stream` + `amaranth.lib.fifo` + `avoodoo.lib.stream` helpers with Spinal semantics (fork = synchronous by default, join, while) |
| `BufferCC`, `ClockDomain`, `ClockingArea` | 56 + 34 | `amaranth.lib.cdc.FFSynchronizer`, `DomainRenamer`, explicit domains |
| `Mem` | 15 (+ dither/tex tables) | `amaranth.lib.memory.Memory`; RAM-style attributes set in Amaranth (replaces `force_distributed_fifo_ram.py`) |
| `regif` register bank (`BmbBusInterface`, `RegisterCategory`) | RegisterBank 1280 lines | table-driven register file in Python (`avoodoo.frontend.regdef`); categories, float aliases, command streams as data |
| `spinal.lib.misc.pipeline` | ColorCombine, Fpxx2AFix, RegisterBank, BmbBusInterface | hand-written pipeline stages (small helper for valid/ready staging) |
| AXI4 / AXI4-Lite bridges | Gowin/Artix boundary only | `avoodoo.lib.axi` signatures + ports of `AxiLite4ToBmbBridge`, `BmbToAxi4Mem`, `Axi4ToGowinDdrNative`, `AxiLite4ClockCrossing` |
| `spinal.core.formal` | 10 source files | drop from RTL; re-express as SymbiYosys checks in Phase 8 |

Toolchain facts (checked 2026-09-20):

- Mac: Python 3.14 via Homebrew, `uv` available, Verilator 5.052. No yosys, no Amaranth.
- Linux `hyper-ms-lin`: `~/opt/oss-cad-suite-20260918` (Yosys 0.69, nextpnr-himbaechel 0.11, sby), Verilator 5.052, Gowin EDA. No Amaranth.
- Neither box has Amaranth installed. Install into a `uv`-managed venv under `amaranth/`; use the `amaranth-yosys` wheel for Verilog emission so the Mac needs no OSS CAD Suite.

## 3. Architecture decisions

1. **Amaranth emits Verilog; nothing else changes downstream.** The Console top
   `TangConsole138k` keeps *exactly* the port names the Spinal top has today so
   `board_top.v`, the CST/SDC and `build_console.tcl` work unchanged. Same for
   `CoreSim` so the Verilator Glide harness links unchanged (see §4).
2. **1:1 module map.** One Python module per Scala file, same names (snake_case
   file, CamelCase class), same ports, same register-stage structure. A diff
   between the Scala and the Python should read as a transliteration.
   Refactors go in a later "v2" and only with the equivalence tests green.
3. **Bit-exact arithmetic.** `avoodoo.lib.fixed` reproduces AFix's result-width
   rules (add/sub grow by one integer bit, mul sums widths, `fixTo` with the
   named rounding mode then saturation). Every arithmetic helper has a pytest
   that sweeps random operands against a Python reference *and* the Spinal
   Verilog (§4.2).
4. **Bus.** Internal bus is `MemBus` (Bmb clone). The Gowin boundary keeps
   AXI4-Lite (BAR) and AXI4 (framebuffer, texture) exactly as `CoreGowin`
   does, so `Axi4ToGowinDdrNative` and its tests port directly and the Dock/
   Artix targets can follow later. Direct MemBus→DDR-native is a v2 option.
5. **Simulation.** Verilator is the workhorse (Amaranth's pysim is far too slow
   for trace replay). pysim only for tiny unit tests. CXXRTL optional.
6. **Clocking.** Same clock domains as today: core, DDR native (`ddrClk`), HDMI
   pixel, ctrl. Amaranth `DomainRenamer` + explicit `ClockSignal/ResetSignal`
   inputs on the top. Reset polarity/sync must match the current Verilog.
7. **Repo layout** (new top-level dir, Spinal tree untouched):

```
amaranth/
  pyproject.toml            # uv project; amaranth pinned; amaranth-yosys; pytest; cocotb
  README.md
  Makefile                  # venv, rtl, sim, test, diff, gowin-console
  avoodoo/
    __init__.py
    config.py               # Config.voodoo1() dataclass, QFormat
    lib/   fixed.py fpxx.py stream.py membus.py axi.py cdc.py pipeline.py
    bus/   register_category.py bus_interface.py pci_fifo.py stream_while.py
    frontend/ regdef.py register_bank.py address_remapper.py lfb.py swap_buffer.py
    raster/  triangle_setup.py rasterizer.py span_walker.py fastfill*.py
    pixel/   color_combine.py fog.py dither.py dither_tables.py framebuffer_access.py write.py
    texture/ tex_layout_tables.py tmu_texel_decoder.py tmu_texture_cache.py tmu.py texture_mem.py
    framebuffer/ address_math.py plane_buffer.py plane_reader.py plane_direct_reader.py
    hdmi/    scanout.py
    core/    core_types.py cpu_frontdoor.py framebuffer_mem_subsystem.py texture_mem_subsystem.py pixel_pipeline.py
    core.py  core_sim.py sim_membus_memory.py
    gowin/   address_map.py core_gowin.py axi4_to_ddr_native.py ddr_native_cdc.py
             console_buttons.py bl616_link.py demo_*.py text_osd.py debug_hud.py
             core_debug_log.py bar_rv32.py tang_console_138k.py
  gen/                      # CLI entry points: gen_core_sim.py, gen_tang_console.py, gen_module.py
  tests/                    # pytest; unit (pysim) + differential (Verilator/cocotb)
  tools/diff/               # Spinal-vs-Amaranth lockstep harness (§4.2)
  emu/sim/                  # copy of the C++ Glide harness adapted to the Amaranth CoreSim
  hw/console/               # build dir: symlinks/refs to ../../../fpga/hw/gowin/console/*.v, own build tcl
  rtl/                      # generated Verilog (gitignored)
```

Naming rule: the SD-card core produced by this tree is
`cores/console138k/voodootang-am.bin`. Never overwrite `voodootang.bin`
(the Spinal build) or the reserved `voodootang-open.bin`.

## 4. Verification strategy (this is most of the work)

### 4.1 Levels

| Level | Tool | Proves |
|---|---|---|
| L0 unit | pytest + Amaranth pysim | helper libraries (fixed, fpxx, stream, membus) vs Python reference models |
| L1 differential | Verilator lockstep, Spinal module vs Amaranth module | cycle-exact port of each module |
| L2 system | existing Glide2x test suite on `CoreSim` via Verilator (`make native/sim/check-all`) | screenshots bit-identical to `test-output/*`; cycle counts within 0.5 % |
| L3 trace | Tomb / Quake / Screamer 2 / Valley of Ra trace replay | same, on real workloads |
| L4 hardware | Console SD-card load `voodootang-am.bin` | menu, four SDK traces, HUD, USB pad, CDC log |
| L5 formal | SymbiYosys (`sby` in OSS CAD Suite) | re-port of the 10 Spinal formal properties (after L4) |

### 4.2 Differential harness (`amaranth/tools/diff/`)

For a module `X`:

1. Spinal side: generate Verilog for the single Spinal component (a scala-cli
   main under `fpga/` that elaborates `X` with the same parameters — e.g.
   `voodoo.diff.GenOne X`), or reuse the module from a full `CoreSim.v` with
   `verilator --top-module X`. Rename the module `X_spinal`.
2. Amaranth side: `gen_module.py X` → `X_am`.
3. `tools/diff/harness.py X` emits an Amaranth wrapper that instantiates both,
   feeds identical inputs, and exposes both output vectors plus a `mismatch`
   flag. Verilator builds it; a cocotb (or plain C++) driver applies
   constrained-random + directed stimulus honouring stream handshakes and
   asserts `mismatch == 0` for N cycles. Same seed → same stimulus, so
   failures are reproducible; dump FST on mismatch.
4. Spinal port names (`io_x_valid`, `io_x_payload_a_b`, `_1` suffixes) are
   matched by an explicit mapping table per module, generated once from the
   Spinal Verilog port list and committed.

Every ported module ships with its diff test. A module is "done" only when
its diff test passes for ≥1e6 cycles across ≥3 seeds.

### 4.3 System harness

The upstream harness (`ext/SpinalVoodoo/emu/sim/harness/sim_harness.cpp`)
pokes ~150 internal Verilator signals for debug stats. Do not modify the
submodule. Instead:

- `amaranth/emu/sim/harness/` holds a copy adapted to the Amaranth `CoreSim`.
  The C API in `sim_harness.h` (`sim_init`, `sim_write`, `sim_read`,
  `sim_idle_wait`, `sim_read_fb`, `sim_get_*` …) stays identical so the Glide
  tests and Makefiles link without change.
- The Amaranth `CoreSim` exposes those stats as a dedicated `dbg` output
  bundle instead of relying on hierarchical names. The harness reads the ports.
- The upstream Makefile takes `SIM_RTL=` / `SIM_DIR=` overrides on the
  command line; `amaranth/Makefile` drives it with those pointed at the
  Amaranth outputs.

## 5. Phases

Each phase lists tasks, files, and an exit criterion. Tasks inside a phase are
independent unless noted, so several agents can run in parallel (one module per
agent, separate worktrees, rebase before merge). Line counts are the Scala
source size, as a rough effort guide.

### Phase 0 — Toolchain, skeleton, and the two risky spikes

0.1 **Skeleton.** `amaranth/pyproject.toml` (uv, Python 3.12 or 3.13 — check
    Amaranth's supported range; 3.14 on the Mac is likely too new), pinned
    latest Amaranth release, `amaranth-yosys`, pytest, cocotb, `amaranth-soc`
    only if used. `Makefile` targets: `venv`, `rtl-core-sim`, `rtl-console`,
    `test`, `diff MOD=…`, `gowin-console` (runs on Linux via a copy of
    `run_pnr.sh` logic). `avoodoo/config.py` with `Config.voodoo1()`.
    Verify on both machines (`uv sync` on Linux too).
0.2 **Fixed-point spike.** Inventory every AFix expression in the core (formats,
    ops, rounding), write `avoodoo/lib/fixed.py`, unit tests against a Python
    model of AFix semantics, and a diff test against a Spinal micro-module that
    exercises each op. Decide `amaranth.lib.fixed` vs own (document why).
0.3 **Gowin Verilog compatibility spike.** Amaranth blinky-plus (a counter, a
    `Memory` with `syn_ramstyle` attribute, an `FFSynchronizer`) emitted with
    `amaranth-yosys`, wrapped by the existing `board_top.v` conventions, built
    by `gw_sh` on Linux, SRAM-loaded on the Console. Proves: escaped
    identifiers, `initial` blocks, memory inference, attribute pass-through.
0.4 **Diff harness.** `tools/diff/` from §4.2, proven on `StreamWhile` (95
    lines) and `Fpxx` (259 lines) end to end, including the port-name mapping
    generator and the `fpga/` scala-cli `GenOne` entry point.
0.5 **Harness copy.** `amaranth/emu/sim/harness/` skeleton that compiles
    against a stub `CoreSim` and links the Glide tests (no RTL yet).

Exit: `make test` green on Mac and Linux; blinky-plus runs on hardware;
`make diff MOD=StreamWhile` and `MOD=Fpxx` pass.

### Phase 1 — Library layer (`avoodoo/lib`, `avoodoo/bus`)

1.1 `stream.py`: fork (sync), join, demux, arbiter (Spinal `StreamArbiterFactory`
    modes used), `stage`/`m2s`/`s2m` pipes, `StreamWhile` (95). Diff-tested.
1.2 `membus.py`: `MemBus` signature (opcode, address, length, data, mask,
    source, last / rsp opcode, data, source, last) + `Arbiter`,
    `SourceRemover`, `LengthFixer`, `Aligner`, `AlignedSplitter`,
    `ContextRemover`, `Routing` (268). Diff-tested one by one.
1.3 `fpxx.py` + `fpxx2afix.py` (259 + ~100).
1.4 `cdc.py`, `pipeline.py` helpers; `sim_membus_memory.py` (146).
1.5 `axi.py`: AXI4/AXI4-Lite signatures; ports of `AxiLite4ToBmbBridge`,
    `BmbToAxi4Mem`, `Axi4ToAxiLite4` (135), `AxiLite4ClockCrossing` (37).
1.6 `bus/register_category.py`, `bus/bus_interface.py` (268),
    `bus/pci_fifo.py` (654) — `PciFifo` is the first "real" module and the
    first big diff test.

Exit: every module above has a passing diff test.

### Phase 2 — Frontend

2.1 `frontend/regdef.py`: the register table (address, name, category, float
    alias, command stream, reset value) as data extracted from
    `RegisterBank.scala` (1280). Generate a markdown map and cross-check it
    against `docs/sst-1.md`.
2.2 `frontend/register_bank.py` — depends on 2.1 and 1.6.
2.3 `frontend/address_remapper.py` (107), `swap_buffer.py` (116), `lfb.py`
    (496), `core/cpu_frontdoor.py` (~200), `core/core_types.py` (407, mostly
    layouts/records).

Exit: `RegisterBank` diff test replays the register-write sequence of every
Glide SDK trace with identical bus responses and command-stream outputs.

### Phase 3 — Rasterizer

`raster/triangle_setup.py` (498), `rasterizer.py` (234), `span_walker.py`
(445), `fastfill.py` + `fastfill_word_writer.py` + `fastfill_write.py` (~480),
`pixel/write.py` (108).

Exit: diff tests; plus the ported intent of `TriangleSetupTest`,
`RasterizerTest`, `SpanWalkerClipDisabledRegressionTest` as pytest.

### Phase 4 — Pixel pipeline

`pixel/color_combine.py` (571), `fog.py` (290), `dither.py` + tables (118 +
931 data → generated from 86Box LUT source, not hand-copied),
`framebuffer_access.py` (493), `core/pixel_pipeline.py` (714, assembly).

Exit: diff test of `PixelPipeline` as a whole with the TMU stubbed, then with
Phase 5.

### Phase 5 — Texture unit

`texture/tex_layout_tables.py` (106), `tmu_texel_decoder.py` (235),
`tmu_texture_cache.py` (996), `tmu.py` (1180), `texture_mem.py` (194),
`core/texture_mem_subsystem.py` (117). `TmuTextureCache` and `Tmu` are the two
largest modules in the design; split each across two agents by pipeline stage
only if the stage boundary is a registered stream.

Exit: diff tests; `TmuTest`, `TmuTexelDecoderTest`, `TmuTextureCacheReuseTest`
intent as pytest.

### Phase 6 — Framebuffer memory subsystem

`framebuffer/address_math.py`, `plane_buffer.py` (592), `plane_reader.py`
(461), `plane_direct_reader.py`, `core/framebuffer_mem_subsystem.py` (417).

Exit: diff tests; `FramebufferPlaneBufferThroughputTest` intent as pytest.

### Phase 7 — Scanout, Core, CoreSim, system parity

7.1 `hdmi/scanout.py` (671, two clock domains, CDC FIFO).
7.2 `core.py` (215) and `core_sim.py` with the `dbg` bundle; finish the
    harness copy (§4.3).
7.3 Run `make native/sim/check-all` and the four trace replays. Compare PNGs
    byte-for-byte and cycle counts. Fix until identical.
7.4 Port the DE10-independent SpinalSim tests that are not yet covered
    (`HdmiScanoutTest`, `WriteTest`, `ColorCombineTest`, `DitherTest`,
    `AddressRemapperTest`, `RegisterBankTest`, `PciFifoTest`,
    `BmbBusInterfaceTest`).

Exit: L2 and L3 identical to the Spinal build. This is the milestone that
makes the Amaranth core "the core".

### Phase 8 — Gowin Console port

8.1 `gowin/address_map.py`, `core_gowin.py` (103), `axi4_to_ddr_native.py`
    (254), `ddr_native_cdc.py` (125), and the `GowinDdrNative` signature.
    Diff tests against `Axi4ToGowinDdrNativeTest` stimulus.
8.2 Demo/UI: `demo_catalog.py` (240), `demo_font.py`, `demo_menu.py` (144),
    `demo_trace_rom.py`, `demo_axi_lite_player.py` (142), `text_osd.py` (97),
    `debug_hud.py` (174), `core_debug_log.py` (205), `console_buttons.py`
    (76), `snes_pad.py`, `bl616_link.py` (323), `bar_rv32.py` (392, hand
    assembled RV32I BRAM smoke test — port last, it is not on the demo path).
8.3 `tang_console_138k.py` (290) with the exact Spinal port list;
    `hw/console/` build dir reusing `board_top.v`, `dvi_tx_top.v`,
    `iosys_hid.v`, `uart_fixed.v`, `usb_cdc/`, IP, CST, SDC; RAM-style
    attributes set in Amaranth so `force_distributed_fifo_ram.py` is not
    needed (verify BSRAM count in the PnR report ≤ Spinal build's).
8.4 Bitstream on Linux (~31 min PnR; follow `AGENTS.md`), SRAM load first,
    then `voodootang-am.bin` on the SD card. Compare timing report (Fmax per
    domain) and utilisation with the Spinal build; both recorded in
    `amaranth/README.md`.

Exit: L4 — menu, four SDK traces, HUD, USB pad, and CDC log behave as the
Spinal core does.

### Phase 9 — Formal, cleanup, docs

9.1 Re-express the 10 Spinal formal properties with `amaranth.asserts` + sby.
9.2 `amaranth/README.md` (how to build, test, diff, program), update
    `fpga/README.md`, `hardware/tang-console-138k/README.md`, `AGENTS.md`
    (Amaranth venv on both machines, PnR rules unchanged).
9.3 Decide when the Spinal `fpga/` tree becomes archival.

### Later (not scheduled)

- Tang Mega 138K Pro: `pcie_tlp_bar_target.py` (240), `tang_mega_138k_pro.py`.
- Artix AC701 and the `hardware/rough-card` Artix-100T card.
- v2 refactors: MemBus→DDR-native without AXI, `amaranth-soc` CSR, wider
  memory data path.
- Open toolchain once Apicula's GW5AST-138C database is hardware-valid.

## 6. Working rules for agents

- Never edit `ext/SpinalVoodoo/**` (submodule) or the Spinal `fpga/src/**`.
  Add scala-cli *entry points* under `fpga/src/voodoo/diff/` only.
- One module per task. Read the Scala file fully first. Keep the Scala file
  path in the Python module docstring. Preserve signal names where Amaranth
  allows so waveforms line up.
- Test before "done": unit test (pysim), diff test (Verilator), both wired
  into `make test` / `make diff`.
- Spinal `Reg(...)` without `init` has no reset; an Amaranth `Signal` resets
  to 0. Declare such registers `reset_less=True` or the lockstep diff diverges
  right after reset (the harness resets before every seed).
- Amaranth attaches an initialiser to every `Memory`; large uninitialised RAMs
  must go through `gen/gen_core_sim.py`'s `strip_meminit` or Verilog emission
  explodes to one `initial` per word.
- Keep `avoodoo/` free of simulation-only code paths except `core_sim.py` and
  `sim_membus_memory.py`.
- Gowin PnR only on `hyper-ms-lin`, one `gw_sh` at a time (see `AGENTS.md`).
  Never program the SD card automatically; SRAM load first.
- Record decisions and measurements in this file's checklists, not in chat.
- Commit per module on branch `amaranth/<phase>-<module>`; PR into
  `codex/voodoo-console-sdram` (or `main` once that branch merges).

## 7. Risks

| Risk | Mitigation |
|---|---|
| AFix rounding/width semantics subtly differ → off-by-one LSB colours | Phase 0.2 spike + per-op diff tests; L2 byte-compare screenshots |
| Amaranth Verilog trips Gowin synth (escaped names, `initial` on BSRAM, attributes) | Phase 0.3 spike before any core work |
| Verilator harness depends on Spinal hierarchical names | `dbg` bundle + harness copy (§4.3) |
| pysim too slow | Verilator everywhere except tiny unit tests |
| BSRAM budget (340 blocks on 138K) blows up without the `force_distributed_fifo_ram.py` hack | attributes set in Amaranth `Memory`; check PnR report in 8.3 |
| Python 3.14 unsupported by Amaranth | uv pins 3.12/3.13 per project |
| Two big modules (`Tmu`, `TmuTextureCache`) exceed one agent's context | split by registered stream boundary; diff test each half |
| Timing regression on GW5AST | compare Fmax per domain; keep register structure identical |

## 8. Progress checklists

### Phase 0
- [x] 0.1 skeleton (`amaranth/pyproject.toml`, Makefile, config) — 2026-09-20.
      Pinned Python **3.14**, `amaranth==0.5.10`, `amaranth-yosys==0.50.0.0.post128`,
      `cocotb==2.1.0`, `pytest==9.1.1`; `make venv && make test` green on the Mac
      and on `hyper-ms-lin` (17 tests). Rationale in `amaranth/README.md`.
      Two findings for 0.2 / Phase 8: **`amaranth.lib.fixed` does not exist in any
      released Amaranth** (0.5.10 has no such module; PyPI's 0.6.0 is a yanked
      duplicate of 0.5.0), so `avoodoo/lib/fixed.py` must be our own AFix clone;
      and **`amaranth.vendor.GowinPlatform` rejects GW5A/GW5AST** (its device regex
      only matches GW1N\*/GW2A\*), which the plan already assumed.
- [x] 0.2 fixed-point spike — **decision: own implementation; `amaranth.lib.fixed` absent**
      (not in any released Amaranth, and its proposed `(width, fraction)` shape cannot
      express AFix's `(maxRaw, minRaw)` bounds — see `docs/amaranth/afix-semantics.md`
      §10.1). `amaranth/avoodoo/lib/fixed.py` (range-based `AFixShape`/`AFixValue`,
      mirroring SpinalHDL 1.15.0 `AFix.scala`) + `fixed_model.py` (independent pure-Python
      reference) + `amaranth/tests/test_fixed.py` (71 tests: doc tables, shape sweeps over
      every core format pair, pysim sweeps per op). The doc's traps are reproduced and
      tested: 13-bit `ceil(0)`/`roundHalfDown(0)` widths, `UQ(0, 8)` as a zero-bit constant
      zero, truncate-toward-zero `BigInt./`, `fixTo` with `drop < 0` skipping rounding,
      `asUInt` clamping negatives, `sat` flipping to unsigned. `QFormat` grew no
      `fraction <= width` check so `UQ(0, 8)` stays expressible.
      **Diff-verified against SpinalHDL 1.15.0**: `avoodoo/lib/afix_ops.py` mirrors
      `fpga/src/voodoo/diff/AfixOps.scala` and `make diff MOD=AfixOps` is clean over
      282 ports x 1e6 cycles x 3 seeds (~2 min), with `tests/diff/test_diff_afix_ops.py`
      as the short version in `make test`. Every bound-derived output width matched the
      committed port map on the first run. Two semantics the doc left open were settled
      from `AfixOps_spinal.v` and are now in `fixed.py`: `.round(exp, align)` is the
      **global default rounding, i.e. plain floor** (`round0_vertex` is a bare `>>> 4`,
      12 bits, not ceil's 13), and `AFix.sat(m: Int)` **keeps the format** and only
      clamps the value to what `bit_width - m` bits could hold. `floorToZero`/`ceilToInf`
      are implemented; `ROUNDTOEVEN`/`SCRAP` raise rather than guess (AfixOps only
      reaches them at `drop == 0`, where every mode is the identity).
      One bug escaped all of that and was caught downstream by the SpanRasterizer diff
      (p3-rasterizer): an AFix read out of an `amaranth.lib.data` layout did **unsigned**
      arithmetic, because `data.View` hands a field's shape an unsigned slice and
      `AFixValue` kept the Value's signedness instead of the shape's. Fixed in
      `AFixValue.__init__`, three regression tests, and AfixOps now routes the vertex /
      coefficient / colorMul operands through a `data.StructLayout` so the lockstep diff
      covers the way the core actually stores an AFix — in a Bundle. Reverting the fix
      now fails the diff on 33 ports at cycle 0. Lesson for the other phases: a helper
      library's diff module must exercise the *storage* path, not just the operators.
- [x] 0.3 Gowin compatibility spike — **built, awaiting hardware load**.
      `avoodoo/gowin/spike_blinky.py` + `amaranth/hw/spike/`; Gowin EDA 1.9.11.03
      on GW5AST-138C, 27 s, bitstream generated. No errors on escaped identifiers
      or `initial` blocks; `Extracting RAM for identifier 'mem'`; `syn_ramstyle =
      "distributed_ram"` honoured (32x RAM16S4 SSRAM, **BSRAM 0/340**), so
      `force_distributed_fifo_ram.py` should not be needed for the Amaranth tree
      (plan 8.3). Fmax 195 MHz, 1 DSP inferred from a constant multiply.
      Ran on the Mac, not Linux: `hyper-ms-lin` was mid `gw_sh
      build_console_sdram.tcl`, which `AGENTS.md` makes a Mac-fallback case.
      Details in `amaranth/hw/spike/RESULTS.md`; the user must load it with
      `hardware/tang-console-138k/program_debug_sram.sh` with both case buttons
      released. Holding MODE blocks FPGA startup after programming.
- [x] 0.4 diff harness proven on StreamWhile + Fpxx — **2026-09-20**.
  - **Amaranth side done.** `amaranth/tools/diff/` + `make diff MOD=<Name>`
    (`--seeds`, `--cycles`, `--valid-density`, `--ready-density`, `--reset-cycles`,
    `--list`). It runs `GenOne` when `fpga/rtl/diff/<Name>_spinal.v` is missing or
    older than any `.scala` under `fpga/src` / `ext/SpinalVoodoo/src`, elaborates the
    registry's Amaranth factory to `<Name>_am`, emits an Amaranth `diff_top` that
    instantiates both as `Instance()`s with shared inputs and an OR'd `mismatch`,
    and drives it from a generic cocotb/Verilator stimulus driver.
    **Measured clean at 1e6 cycles x 3 seeds each** (the plan's "done" bar):
    `FpxxDiffWrap` (21 ports), `Fpxx2AFix` (9 ports, streams + a pipeline stage),
    `StreamWhileDiffWrap` (7 ports); plus 1e6 x 3 more at
    `--valid-density 0.9 --ready-density 0.12` for the two handshaked ones.
    `make test` runs the same three at 20k x 3 via `amaranth/tests/diff/`.
    Three things worth knowing before writing the next port:
    (a) **Amaranth stops flattening at the signature member, Spinal does not.**
    `In(stream.Signature(FpxxLayout(c)))` emits one packed `op__payload`; Spinal
    emits `io_op_payload_{mant,exp,sign}`. `tools/diff/amports.py` walks the
    signature *and* the `data.Layout` underneath it and pairs Spinal leaves with
    bit slices, so ports do **not** have to be reshaped to match — and the
    per-module override table in `tools/diff/registry.py` stays empty so far.
    (b) The rest of the naming rule is mechanical and needed no exceptions:
    Spinal path segments (from the committed `portmaps/*.json`) camelCase ->
    snake_case, joined with `__`; `clk`->`clk`, `reset`->`rst`.
    (c) **A plain Spinal `Reg(...)` has no reset; Amaranth's `Signal` does.** The
    harness found this immediately (`StreamWhile.state`, then four more registers
    in `lib/stream.py`) — it only shows up on a seed that resets a *running*
    design, which is why the driver resets before every seed rather than once.
    Every such register must be `reset_less=True`.
  - **Spinal side done (2026-09-20).** `fpga/src/voodoo/diff/GenOne.scala`
    (`scala-cli run . --main-class voodoo.diff.GenOne -- <Module> [outDir]`, 44 buildable
    entries, 2 stubs) generates `<Module>_spinal.v`; `DiffWrappers.scala` wraps the
    non-Component `StreamWhile`/`Fpxx`; `AfixOps.scala` is the AFix op-by-op reference for
    0.2; `ports.py` emits the port-name maps committed under `diff/portmaps/`. StreamWhile,
    FpxxDiffWrap, AfixOps, PciFifo, Rasterizer, ColorCombine and CoreSim generate and pass
    `verilator --lint-only` with 0 errors. Conventions and the harness inventory:
    `docs/amaranth/spinal-naming-and-harness.md`. Remaining: the Amaranth side
    (`gen_module.py`, `tools/diff/harness.py`, the stimulus driver).
- [x] 0.5 harness copy skeleton links — 2026-09-20. `amaranth/emu/sim/`
      (Makefile + `harness/sim_harness.{h,cpp}` + `harness/sim_smoke.cpp`),
      `avoodoo/core_sim_ports.py` (port contract, reusable by 7.2),
      `avoodoo/core_sim.py` (**stub**), `gen/gen_core_sim.py`, Makefile targets
      `rtl-core-sim` / `sim-model` / `sim-build` / `sim-run`. `sim_harness.h` is
      byte-identical to upstream's, so the Glide tests link unchanged; the 56
      zero-returning `sim_get_*` are untouched; the ~139 dead `#if 0` taps are
      dropped (inventory stays in `docs/amaranth/spinal-naming-and-harness.md` §2.3).
      **dbg bundle**: the 5 live debug scalars become outputs
      (`dbg_pci_cmd_ready`, `dbg_frontdoor_cmd_ready`, `dbg_pci_fifo_free[6:0]`,
      `dbg_sync_drained`, `dbg_pipeline_busy`), `swapCountReg` becomes
      `dbg_swap_count` out + `dbg_swap_count_set`/`dbg_swap_count_we` in (costs
      `sim_set_swap_count()` one clock; only trace-replay boundaries call it).
      Names are flat because Amaranth joins nested signature paths with `__`.
      **Memory backdoor**: option (a), Verilator `--public-flat-rw` public
      arrays — Amaranth 0.5.10 cannot attach a `verilator public` attribute to
      a `Memory` at all (no `attrs=` on `Memory`/`MemoryData`), and a port/DPI
      path would cost an `eval()` per word on texture uploads. Amaranth+yosys
      emit ONE `reg [31:0] mem [N-1:0]` where Spinal splits into
      `ram_symbol0..3`, so bulk transfers get *faster*: paths are
      `CoreSim__DOT__{fbWriteRam,texRam}__DOT__mem`, spelled once each in
      `VOODOO_FB_MEM`/`VOODOO_TEX_MEM`, owned by `core_sim_ports.py`, and a
      rename breaks `make -C emu/sim smoke` at compile time.
      **Trap for Phase 7**: Amaranth always attaches an initialiser to a
      `Memory` (even `init=[]`) and yosys expands it to one `initial` statement
      per word — 3.1 M lines for the 4 MiB + 8 MiB sim RAMs. `gen_core_sim.py`
      strips the `$meminit_v2` cells from the RTLIL before yosys; memories with
      *real* contents (dither/TMU tables) must not be stripped.
      On `hyper-ms-lin`: `rtl-core-sim` 35 s, `sim-model` 37 s, `make -C
      emu/sim smoke` passes (bus, fb+tex round-trip to the last word of each,
      swap-count load path). `sim-build TEST=test00` links `libglide2x.so.2`
      and `test00.exe`; running it, Glide completes the whole SST-1 init
      sequence against the stub, `fbiMemSize`'s LFB probe writes
      `0xdead`/`0x55aa` and reads them back through the stub's framebuffer RAM
      and correctly detects 4 MB, then it spins on a register the stub does
      not model. Needed `zlib1g-dev:i386 libx11-dev:i386 libxext-dev:i386`
      plus the dpkg i386 arch, now installed and recorded in `AGENTS.md` —
      before that **no** Glide binary could be linked on that box, upstream's
      own `make native/sim/build` included.
- **Two pipelines, and 7.3 wants the 64-bit one.** The golden images in
  `test-output/` come from `native/sim/check/%` → `emu/test/obj_dir/trace_test`,
  a *separate 64-bit* build that replays a captured trace against the 86Box
  reference model and only borrows `emu/sim`'s `harness/` and `rtl/` through
  plain `=` variables. It therefore takes the Amaranth copy with three
  overrides and **needs no 32-bit libraries at all**; the `-m32` Glide stack
  is only for live runs and for *capturing* traces. New target:
  `make sim-trace-check TEST=test04`. It runs the full pipeline against the
  stub — 3024 entries, 35 triangles, 2107 texture uploads, all four output
  files — and **`test04_ref.png` comes out byte-identical (md5) to the
  committed reference**, which is the harness copy validated end to end. It
  then fails as it should: 36354/307200 pixels mismatch, all `SIM-BLACK`,
  because the stub draws nothing. `-o $(RTL_DIR)/CoreSim.v` in that recipe is
  load-bearing: `emu/test/Makefile` has the same `$(VERILOG): $(SCALA_SRCS)`
  trap, and `SCALA_SRCS=` alone does not disarm it because the rule's other
  prerequisites are `$(shell …)` sentinels recreated in `OBJ_DIR` at parse
  time, so it fires a `voodoo.CoreSimGen` Scala build inside the submodule.
- **`native/sim/run/%` does not complete against the *Spinal* core either.**
  Driving the unmodified upstream harness and the submodule's own `CoreSim.v`
  through the copied `emu/sim/Makefile`, test04 renders the fastfill stage
  (5022 pixels) and then hangs on the `fbiMemSize` LFB probe —
  `bus_read(0x432100)` times out after 5 M cycles, and so does the next. So
  there is no live-run reference to reproduce for either core; 7.3 should
  compare through `trace_test`. That run did, however, validate the copied
  `emu/sim/Makefile` independently of the Amaranth core: it verilated the
  9.2 MB Spinal `CoreSim.v` with upstream's exact flags, compiled the
  *upstream* `sim_harness.cpp` against it, and the Glide `Makefile.sim` linked
  `libglide2x.so` entirely out of `amaranth/emu/sim/obj_dir/` from the single
  `SIM_DIR=` override.

### Phase 1
- [x] stream helpers  - [x] membus + adapters  - [x] fpxx  - [x] cdc/pipeline  - [x] axi  - [x] bus_interface  - [x] pci_fifo
- [x] **axi** (2026-09-21). `avoodoo/lib/axi.py` (AXI4/AXI4-Lite signatures in Scala
      field order), `lib/axi4_to_axilite4.py`, `lib/axilite4_to_membus.py`
      (AxiLite4ToBmbBridge incl. the requestTimeoutSeen/responseTimeoutSeen outputs the
      console-SDRAM CoreArtix added, timeoutLog2 = 30), `lib/membus_to_axi4_mem.py`
      (BmbToAxi4Mem + BmbToAxi4SharedBridge), `gowin/axilite4_clock_crossing.py`,
      `gowin/axi4_address_relocate.py`. 22 pysim tests. Eight GenOne rows; all eight
      diff clean at 1e6 x seeds 1,2,3 AND at --valid-density 0.9 --ready-density 0.12;
      the clock crossing also seeds 4-7 x 2e5. Findings: (a) Spinal's
      `withBufferedResetFrom` = `ResetCtrl.asyncAssertSyncDeassertCreateCd` keeps the pop
      domain's SYNC resetKind (only the 2-flop bridge is async); `lib/stream.py:FifoCC`
      declares that domain async_reset=True, which the 1e6 gate caught at seed 3 cycle 0
      via stale RAM-read state across seeds. The crossing builds the sync-reset domain
      itself and passes with_pop_buffered_reset=False; **FifoCC's default must be fixed
      before HdmiScanout** (Phase 7.1). (b) The first failing diff was only the two new
      timeout ports missing from the signature.
- [x] **pci_fifo** (2026-09-20/21). `avoodoo/bus/pci_fifo.py`; 26 pysim tests; diff
      against the real 158-entry decode table clean at 1e6 x 3 (commit f14fb0d).
- [x] 1.1 stream helpers — 2026-09-20. `amaranth/avoodoo/lib/stream.py` (+ `lib/fragment.py`,
      `bus/stream_while.py`), transliterated from the SpinalHDL 1.15.0 `spinal/lib/Stream.scala`,
      `Flow.scala`, `Counter.scala` and `Utils.scala` sources. 66 pysim tests in
      `amaranth/tests/test_stream.py` + `test_stream_while.py`; `make test` 145 green.
      Ported (everything the core instantiates, census in the module docstring): `Stream`/`Flow`,
      `m2sPipe`/`s2mPipe`/`halfPipe`/`stage`/`combStage`, `translateWith`/`map`,
      `throwWhen`/`haltWhen`/`takeWhen`/`continueWhen`/`clearValidWhen`, `StreamFork`
      (sync + async), `StreamJoin`, `StreamDemux`, `StreamMux`, `StreamArbiter` with all four
      arbitration and three lock policies, `StreamFifo` (latency 0/1/2, `flush`, `occupancy`,
      `availability`), `StreamFifoCC`, `Counter`, `OHMasking`/`MuxOH`/`OHToUInt`. Deliberately
      skipped: `StreamFifoLowLatency`, `StreamCCByToggle`, `StreamWidthAdapter`,
      `StreamTransactionExtender`, `StreamUnpacker`/`Packer`, `repeat`/`slowdown`/`delay`/
      `validPipe`/`forkSerial`/`toStream`/`queueOfReg`, `forFMax`, `useVec`.
      **Findings that bind later phases** (full text in `stream.py:SPINAL_VS_AMARANTH`):
      (a) `amaranth.lib.fifo.SyncFIFOBuffered` is *not* substitutable for `StreamFifo` — no
      `flush` (the two `FramebufferPlaneBuffer` slot FIFOs drive it), no `availability`
      (`PciFifo` exports it as `io.pciFifoFree`), and its `level` disagrees with Spinal's
      `occupancy` on the cycle a pop fires, because Spinal computes `pushPtr - popOnIo` with
      `popOnIo = RegNextWhen(popPtr, readArbitration.fire)`; the core's `busy` flags and the
      HUD debug word read that, so `avoodoo.lib.stream.Fifo` is a transliteration.
      A cycle-accurate Python model of the Scala is in `tests/test_stream.py` and every output
      is compared every cycle for depths 2/3/4/5/8/16. Usable capacity is `depth`, not
      `depth + 1`: `full` compares against `popOnIo`, so the output register buys no slot.
      (b) `amaranth.lib.stream.Signature` is `@final`, so the ~100 Spinal `Stream` methods are
      module-level functions taking the `Module` first. (c) `StreamFork(synchronous = true)`
      drives `outputs.valid := i.valid && i.ready` — valid depends on ready, illegal in AXI,
      relied on in six places; kept verbatim. (d) Several helpers depend on Spinal's
      last-`when`-wins override order (`s2mPipe`, `halfPipe`, `StreamForkArea`, `StreamFifo`
      flush); statement order is preserved for that reason. (e) `StreamFifoCC` derives the pop
      domain reset from the push domain by default
      (`ClockDomain.crossClockBufferPushToPopResetGen` is **true**); reproduced with a local
      async-reset domain + `amaranth.lib.cdc.ResetSynchronizer`, and it needs checking against
      the Console Verilog in Phase 8 (decision 6).
      `avoodoo.bus.stream_while:StreamWhileDiffWrap` is the elaboratable counterpart of
      `fpga/src/voodoo/diff/DiffWrappers.scala`, ready for the 0.4 harness; it is not in
      `gen/gen_module.py`'s `REGISTRY` yet (use the `module:Class` spec).
- [x] 1.2 membus + adapters (and 1.4's `sim_membus_memory.py`) — 2026-09-20.
      `amaranth/avoodoo/lib/membus.py` (parameters, layouts, signature, six adapters),
      `avoodoo/bus/routing.py` (the core-local `BmbRouting.scala`),
      `avoodoo/core/membus_params.py` (every `*BmbParams` in the design, one place) and
      `avoodoo/sim_membus_memory.py` (`SimBmbMemory.scala` + `SimMemoryTiming`).
      53 pysim tests in `tests/test_membus.py` + 15 short lockstep runs in
      `tests/diff/test_diff_membus.py`.
      **Diff results** (`make diff`, 3 seeds x 1e6 cycles, Mac, Verilator 5.052), each against
      the parameters its Scala call site uses: `BmbArbiter` (5 x fbMemBmbParams, lowerFirst)
      PASS, `BmbArbiterSingle` (PlaneBuffer -> fbMem, the portCount == 1 bypass) PASS,
      `BmbSourceRemover` (fbMemBmbParams) PASS, `BmbAligner` (fbMemBmbParams, burstLog2 8)
      PASS, `BmbLengthFixer` (aligner.op, 8) PASS, `BmbAlignedSpliter`
      (PlaneReader.internalBmbParams, lengthMax 2048) PASS, `BmbContextRemover`
      (splitterBmbParams, pendingMax 64) PASS, `SimBmbMemorySmall` PASS. New `GenOne` rows
      for all of them, with the committed port maps.
      The two arbiter rows needed a harness fix first (`tools/diff/amports.py` dropped the
      index of an `In(sig).array(n)` member, 1a96914) and then caught a real bug: Spinal's
      `Vec.read(rspSel)` emits a `case` whose `default` arm is the *last* element, so a
      route index >= portCount (5..7 with five ports, which the driver's random
      `rsp.source` does present) selects input 4's `ready`; the first port used Amaranth's
      `Array`, which returns 0 there. Fixed with a `Switch` + `Default`, and pinned by a
      pysim test. `SimBmbMemory` at its real 4 MiB is marked `pending`: 1 048 576 words
      makes `amaranth_yosys` `write_verilog` run for minutes and the Spinal side is four
      1 Mi-entry byte arrays on top, so `SimBmbMemorySmall` (64 KiB, identical parameters
      and timing, only `wordAddrWidth` differs) is the diffable stand-in.
      **Ported exactly what is used** (census in the module docstring): `BmbParameter` /
      `BmbAccessParameter` / `BmbSourceParameter` with the derived widths and the
      `aggregate` fold, `BmbCmd` / `BmbRsp` / `Fragment` layouts, `weakAssignFrom`,
      `Bmb.incr` / `addToAddress` / `transferBeatCountMinusOne`, and the six adapters.
      Not ported, and they raise rather than silently differ: the `inv`/`ack`/`sync`
      channels (no parameter set in the core enables them), `canExclusive`, `BmbDecoder`,
      `BmbUnburstify`, the up/down sizers, and a `BmbArbiter` with heterogeneous input
      parameters.
      **Findings that bind later phases:**
      (a) **`sourceWidth` is derived, `log2Up(max(id) + 1)`, so a single source is a
      ZERO-bit field and no Verilog port at all.** Every `withSingleSource` output
      parameter has it, as does `contextWidth = 0` on all of the core's own buses. Fields
      of width 0 are omitted from the Amaranth layout, matching Spinal, and the code guards
      on presence rather than assuming.
      (b) **The arbiter truncates the source.** `cmd.source := (source @@ chosen).resized`
      writes into a field that is *not* widened, and CoreSim's five-way framebuffer arbiter
      has `sourceWidth 4` with `routeWidth 3` — so master 2's source 2 goes out as 2, and
      master 3's 3 goes out as 11. Only the route bits are meaningful; no master can rely on
      getting its own source back, and the core does not. And on the way back, a route
      index with no owner goes to the last input (`Vec.read`'s `default`), see above.
      (c) **Bundle assignment in Spinal resolves by NAME, not bit position.** The first
      version of `ContextRemover` copied `io.output.rsp.payload` as a packed vector and
      shifted `opcode`/`data` into the wrong fields, because the input and output payloads
      have different shapes (the output has neither `source` nor `context`). Caught by a
      pysim test before the diff ran. Any adapter that connects two differently
      parameterised buses must use the by-name copy.
      (d) **`Fragment` declares `last` FIRST**, so a packed cmd/rsp payload has `last` at
      bit 0. `avoodoo/lib/fragment.py` had it reversed; reported and fixed by 1.1 (12905ae).
      (e) **`last` retiming.** `LengthFixer` and `AlignedSplitter` re-derive `last` per
      *sub*-burst and carry the input's own `last` in `context.last`; non-final *write*
      responses are swallowed entirely (`takeWhen(!write || last && rsp.last)`). Getting
      this wrong deadlocks the arbiter, whose `fragmentLock` holds the grant until a `last`
      beat fires.
      (f) **`mask` is write-only** (`canWrite && canMask`); reads ignore it. `Aligner` pads
      a write burst by forcing `mask` to 0 on the padding beats rather than suppressing
      them, so a slave must treat a zero mask as "write nothing" — `SimBmbMemory` does.
      (g) **Write bursts carry a fresh address on every beat in this design.**
      `FramebufferPlaneBuffer` increments `cmd.address` per beat and `SimBmbMemory` writes to
      the current beat's address. `Aligner`/`LengthFixer` emit a *constant* address across a
      burst instead, matching the AXI bridge that follows them, so `SimBmbMemory` must not
      be wired straight behind either. The `tests/test_membus.py` model slave implements the
      AXI convention for exactly that reason.
      (h) **`needBurstSplitter(Config.voodoo1())` is FALSE** (`((800+1)/2)*4 = 1600 <= 2048`),
      so the shipping core contains neither `BmbAlignedSpliter` nor `BmbContextRemover`.
      Both are ported and diffed anyway with the parameters `FramebufferPlaneReader` would
      give them; Phase 6 should not be surprised to find no instance.
      (i) Registers: every Spinal `Reg` in these six adapters has an explicit `init`, so
      none needed `reset_less=True` (0.4's finding). The two simulation memories do start
      all-zero on both sides, which is what makes the `SimBmbMemory` diff meaningful.
      `avoodoo/core/membus_params.py` duplicates the two register-bank parameter sets that
      `avoodoo/frontend/address_remapper.py` also declares (`bmb_params` /
      `external_bmb_params`); Phase 2 should collapse them onto one.
- [x] 1.3 fpxx — 2026-09-20. `amaranth/avoodoo/lib/fpxx.py` (`Fpxx.scala`, 259 lines)
      and `fpxx2afix.py` (`Fpxx2AFix.scala`, 71). Both diff-clean against the Spinal
      Verilog at **1e6 cycles x 3 seeds** (`make diff MOD=FpxxDiffWrap`,
      `MOD=Fpxx2AFix`), `Fpxx2AFix` also at `--ready-density 0.12`; 23 pysim tests in
      `tests/test_fpxx.py` pin the semantics against an independent Python model.
      `FpxxLayout` is a `data.StructLayout` in the Scala's declaration order
      (`mant`, `exp`, `sign`), which gives the IEEE packing for free;
      `FpxxView` carries the Bundle's methods, with `set_nan`/`set_inf`/`set_zero`
      returning statement lists so a caller writes `m.d.comb += v.set_nan()`.
      Three upstream oddities are reproduced deliberately and covered by tests:
      `is_subnormal()` is only `exp == 0` and so is **true for zero**;
      `full_mant()` forces the hidden bit even for a denormal; and `Fpxx2AFix`
      has **no NaN/Inf handling at all** — `exp = 255` drives `shift = -109`, so
      both saturate by sign — while underflow converts to zero with `overflow`
      *clear*. The pipeline payload registers are `reset_less=True`, matching
      Spinal's plain `Reg` (0.4's finding); `StageLink` at `pipeStages = 1` is
      a plain m2s stage, `n0_ready = n1_ready | !n1_valid`, read off
      `Fpxx2AFix_spinal.v` rather than guessed.
      Only the *output type* `AFix.SQ(20 bits, 30 bits)` touches
      `avoodoo/lib/fixed.py` — there is no AFix arithmetic in this module, so
      nothing here depends on the rounding/saturation surface (§9).
- [x] 1.4 cdc + pipeline — 2026-09-20. `amaranth/avoodoo/lib/pipeline.py` and
      `lib/cdc.py`, transliterated from the SpinalHDL 1.15.0
      `spinal/lib/misc/pipeline/*.scala`, `spinal/lib/CrossClock.scala`,
      `spinal/lib/Utils.scala` and `spinal/core/ClockDomain.scala` sources.
      37 + 22 pysim tests in `tests/test_pipeline.py` / `tests/test_cdc.py`;
      `make test` 598 green.
      **pipeline.** Only two files in the tree import `spinal.lib.misc.pipeline`:
      `pixel/ColorCombine.scala` (34 `Payload`s, `n0..n8`, eight default
      `StageLink`s, `driveFrom`/`driveTo`, `Builder(links)`) and
      `math/Fpxx2AFix.scala` (`arbitrateFrom`/`arbitrateTo`, seven `insert`s,
      `StageLink` or `DirectLink` on `pipeStages`, `Builder(c01)`).
      `RegisterBank.scala` and `BmbBusInterface.scala` were on the brief as
      pipeline users but are **not** — what they use is
      `spinal.lib.bus.regif`'s `newCommandRegWithPayload`, which Phase 2.2/1.6
      owns. Ported: `Payload`, `Node` (lazy valid/ready/cancel + the `is_*`
      status accessors, `insert`, stream and flow arbitration), `StageLink`
      (`collapseBubble`, `holdPayload`), `DirectLink`, `CtrlLink`
      (`haltWhen`/`duplicateWhen`/`terminateWhen`/`forgetOneWhen`/
      `ignoreReadyWhen`/`throwWhen`/`bypass`), `S2MLink`, and `Builder` with
      Spinal's two propagation passes. Skipped: `ForkLink`, `JoinLink`,
      `NodesBuilder`, `StagePipeline`, `StageCtrlPipeline`, `forkStream`/
      `forkFlow`.
      **Structure matched.** A `StageLink` whose upstream node has a `ready`
      is bit-for-bit `Stream.m2sPipe(collapsBubble = true)` per live payload
      (`up.ready = down.ready | !down.valid`, valid reg with sync reset,
      payload regs with **no** reset); with no `ready` anywhere it takes its
      other branch and emits an unconditional Flow-style load, so adding an
      `arbitrateTo` at the tail silently rewrites every register in the chain.
      Payload registers only span a payload's first to last *touched* node
      (`matches = down.fromUp ∩ up.fromDown`, and a read counts as a touch):
      `ColorCombine` is 8 valid registers, latency 8, and **150** of the naive
      272 (34 payloads x 8 links) payload stage registers —
      `test_color_combine_skeleton_registers` rebuilds the skeleton and its
      liveness map and asserts both. The same pruning reproduces
      `Fpxx2AFix`'s hand-written stage exactly (3 of 6 inserted payloads
      registered), so a module written with the builder and
      `avoodoo/lib/fpxx2afix.py` are the same netlist.
      **halt/throw.** `haltWhen` clears `down.valid` and `up.ready`, a
      combinational path back to the pipeline's input `ready` through every
      collapse-bubble link. `throwWhen` is terminate + cancel + forgetOne, and
      the forgetOne is the load-bearing half: it propagates into the
      *upstream* `StageLink` as `down.valid clearWhen(forgetOne)` emitted
      before the `when(up.isReady)` reload, so a thrown transaction leaves a
      *stalled* chain instead of reappearing when the condition drops (a bare
      `terminateWhen` would jam). `cancel` does **not** cross a `StageLink`
      upward, so a throw removes one transaction, it is not a flush. A
      4-`CtrlLink`/3-`StageLink` chain with a halt and a throw is compared
      every cycle against a Python model transcribed from the Scala equations,
      over 4 seeds x 900 cycles.
      **cdc.** All 56 `BufferCC` call sites are the plain two-flop form — no
      `bufferDepth` argument anywhere and no `CrossClockBufferDepth` tag on any
      clock domain, so `finalBufferDepth` is 2 throughout, matching
      `amaranth.lib.cdc.FFSynchronizer`'s default. 53 pass an `init`, 3 do not
      (`TangConsole138k.scala` :172/:275/:280), and the difference is
      structural: `Reg(t, init)` under the design's SYNC/HIGH
      `ClockDomainConfig` is a *resettable* flop pair, `Reg(t)` has no reset at
      all, so `buffer_cc(init=None)` maps to `reset_less = True` and those
      three come out with a defined 0 at power-on where Spinal has X — the
      diff harness needs to know about exactly those three.
      `ClockDomain(clock =, reset =, config = ClockDomain.current.config)` is
      the only construction form used (6 sites) and
      `CoreGowinGen.scala`'s `SpinalConfig` makes that
      `ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)`, which is
      an Amaranth `ClockDomain` at its defaults — so `clock_domain()` needs no
      polarity juggling. `ClockingArea` is `DomainRenamer({"sync": name})`.
      `ResetCtrl` is never called by name but is reached implicitly:
      `crossClockBufferPushToPopResetGen` defaults to **true**, so all six
      `StreamFifoCC`s (HdmiScanout pixel FIFO + the five
      `AxiLite4ClockCrossing` channel FIFOs) and both `PulseCCByToggle`s build
      an async-assert/sync-deassert reset synchroniser on their output domain
      from the *input* domain's reset — `buffered_reset_domain()`, the same
      three lines `stream.FifoCC` already open-codes.
      **Phase 8 note (timing).** Amaranth's `ResetSynchronizer` is the same
      circuit with the same 2-cycle release, but Spinal instantiates a
      separate `BufferCC` module carrying `keep_hierarchy = "TRUE"` and hands
      back a `Bool`, while Amaranth inlines the flops and drives
      `ResetSignal(domain)` itself — so the Console netlist comparison has to
      be at flop level, not module level. More importantly Spinal's
      `crossClockDomain` / `crossClockBuffer` / `crossClockFalsePath` tags
      become SDC through its constraint generator and Amaranth emits nothing
      of the kind: the false-path and max-delay exceptions (including the six
      `GowinDdrNativeClockCrossing` bundled-data payload registers) must stay
      hand-written in `fpga/hw/gowin/console/*.sdc`, and 8.3 has to re-check
      that the flop names the SDC matches are the ones Amaranth emits.
      `cross_clock_payload()` and `SYNC_FF_ATTRS` put an `async_reg` attribute
      on those flops so they are at least findable.
      **Undetermined from source:** whether Spinal's
      `ContextSwapper.outsideCondScopeData` placement of the lazily-created
      valid/ready/status signals can move emitted Verilog in a way a
      statement-order-faithful port cannot match (it hoists the declaration,
      not the assignments, so it should not — only the Phase 4 `ColorCombine`
      lockstep diff will prove it).

- [x] 1.6 register_category + bus_interface — 2026-09-21. `amaranth/avoodoo/bus/register_category.py`
      (the four factories as frozen-dataclass constants; `regdef.py` now re-exports them instead
      of carrying its own copy) and `bus/bus_interface.py` (`BmbBusInterface.scala`, 268 lines,
      plus the slice of `spinal.lib.bus.regif` that `RegisterBank` reaches: `newRegAt`,
      `field`/`fieldAt` for `RO`/`WO`/`RW`, `hitDoWrite`, the byte-strobe `wmask`/`wmaskn` merge,
      the registered read mux with `rderr`, and the `s2mPipe().m2sPipe()` command-stream buffer
      that exports `src.ready` per address). Like the Scala it is not a Component: the owner
      passes its `Module` and bus port, declares registers, then calls `finalize()` for the read
      mux. Not a separate diff row — `GenOne` says "diff it through RegisterBank" and 2.2 does.

### Phase 2
- [x] regdef  - [x] register_bank  - [x] address_remapper  - [x] swap_buffer  - [x] lfb  - [x] cpu_frontdoor  - [ ] core_types
- [x] 2.1 regdef — 2026-09-20. `amaranth/avoodoo/frontend/regdef.py`: 136 registers (2 of them
      simulation-only), 279 fields, 30 float aliases, 5 command streams, 158 PciFifo decode
      entries (143 FIFO=Yes, 71 Sync=Yes, 15 FIFO=No), all as frozen dataclasses with no
      Amaranth import. Ground truth is the real elaboration: `GenOne` gained a
      `--dump-regmeta` option that writes `busif.slices` + `getCategories` /
      `getFloatAliases` / `getCommandStreamReady` as JSON, committed at
      `amaranth/tests/data/registerbank_regmeta.json` (verified identical to the decode
      constants in the generated `PciFifo_spinal.v`), and `amaranth/tests/test_regdef.py`
      asserts the table matches it address for address, section for section, reset for reset.
      `docs/amaranth/register-map.md` is generated by `gen/gen_register_map.py` and its
      cross-check against `ext/SpinalVoodoo/docs/sst-1.md` finds exactly one datasheet
      register the core does not implement (`vRetrace`, 0x204) and five it adds
      (`fbiInit5/6/7` plus the two sim-only debug words). The write side effects the table
      cannot express — gammaLut via `clutData`, the YIQ palette path on NCC I/Q writes with
      data[31] set, the twelve 50-bit float shadow registers, the triangleCMD payload
      assembly — are recorded per register in `Register.effects` for 2.2 to implement.
- [x] 2.2 register_bank — 2026-09-21. `amaranth/avoodoo/frontend/register_bank.py` (1280 Scala
      lines): the 136 registers come from the `regdef` table through `BusInterface`, the twelve
      pieces the table cannot express (`regdef.py` footer) are hand-written against their Scala
      lines. Through the full §4.2 gate on the Mac: `make diff MOD=RegisterBank` clean at 1e6
      cycles x seeds 1,2,3 (588 s sim), plus 1e6 x 3 at `--valid-density 0.9 --ready-density
      0.12`; 657 Spinal ports paired (109 in / 548 out), no pinned inputs, no responder.
      `tests/diff/test_diff_register_bank.py` runs 20k x 3 + a stalled case in `make test`;
      `tests/test_register_bank.py` (29 pysim tests) includes a cycle-accurate Python model that
      replays the register-write prefix of `demo_test04.bin` (930 writes, 31 ftriangleCMD + 12
      nopCMD) and `demo_logo_spin.bin` with a read-back after every write, under random
      command-stream back-pressure, and compares the bus responses, the five streams'
      valid/payload and `commandReady` every cycle, then every stored field and the gammaLut.
      The 231 `.asOutput()` register fields, which upstream renames `sim_reg_%03x_f%d` for the
      Verilator harness, are ordinary ports here grouped by Scala Area
      (`render_config__fbz_mode__enable_clipping`); `spinal_port_overrides()` derives the
      pairing from `regdef`, counting regif's auto-inserted `NA` padding, and the test pins the
      indices against the committed `--dump-regmeta` JSON. The AFix fields pair mechanically as
      `triangleGeometry_<area>_<reg>`. No regdef entry needed correcting. Two things worth knowing:
      (a) `BmbBusInterface` answers **every** command, writes included (`rsp.valid :=
      RegNext(cmd.fire)`, BmbBusInterface.scala:135) — a replay test written as "one response
      per read" counted 2N-1 and was the only thing wrong with the port as handed over.
      (b) The reset split: `triangleConfigReg` and `triangleTexTablesReg` are plain `Reg` ->
      `reset_less`, as are the command streams' payload registers and `RegNext(cmd.source)`;
      everything else (`reg_rdata`, `rsp.valid`, the float shadows, gammaLut, syncPulse, every
      field) is `init`ed. `TexLayoutTables.compute` is the shared
      `texture/tex_layout_tables.compute_tables`, registered once (`triangleTexTablesReg`), so a
      triangle command sees the texture tables as they were two cycles before the write and the
      render config as it was one cycle before — both as upstream.
- [x] 2.3 address_remapper + swap_buffer — 2026-09-20. Both 1:1, both through the full
      §4.2 gate on the Mac: `make diff MOD=SwapBuffer` and `MOD=AddressRemapper`, 3 seeds
      x 1e6 cycles, no mismatch; `tests/diff/` runs them at 20k in `make test`.
      `swap_buffer.py` adds one thing the Scala has not got: an optional
      `dbg_load_port=True` that exposes `swap_count_set`/`swap_count_we`, the real-port
      replacement for the Verilator harness's poke at
      `…controlPlane_swapBuffer__DOT__swapCountReg` (`core_sim_ports.DBG_PORTS`). It is off
      by default, so the diffed port list is identical to the Scala's. `Stream(NoData)`
      is `stream.Signature(unsigned(0))`: yosys emits the zero-width payload as
      `input [-1:0] cmd__payload`, which the harness now treats as no port (1a96914 drops
      zero-width leaves; Verilator gets `-Wno-ASCRANGE`), so it stays a real
      `amaranth.lib.stream` signature and composes with RegisterBank's command streams
      (`fastfillCMD` is the other `NoData` one). `RegisterBank.bmbParams` / `externalBmbParams` live in
      `address_remapper.py` until 2.2 lands. Two findings: the Scala `AddressRemapper`'s
      bit-21-set-but-out-of-range branch (`inputAddr & ~(1 << 21)`, then `.resized` to 12
      bits) is indistinguishable from plain truncation, so the only real translation is the
      remap table; and `SwapBufferFormalTest`'s first two `cover` statements are
      unreachable as written (`cover(io.cmd.ready && state === Sampling)` contradicts the
      asserted `cmd.ready === (state === Idle)`) — they have never failed only because
      `FormalConfig.withBMC` runs sby in `bmc` mode, which ignores `cover`.
- [x] 2.3 lfb + cpu_frontdoor — 2026-09-20. `amaranth/avoodoo/frontend/lfb.py` (496 Scala
      lines) and `amaranth/avoodoo/core/cpu_frontdoor.py` (92), both 1:1, both through the
      full §4.2 gate on the Mac: `make diff MOD=Lfb` and `MOD=CoreCpuFrontdoor` clean at
      1e6 cycles x seeds 1,2,3, plus 1e6 x 3 each at `--valid-density 0.9 --ready-density
      0.12`; `tests/diff/test_diff_{lfb,cpu_frontdoor}.py` run 20k x 3 + a stalled case in
      `make test`. No `DiffEntry.overrides`: `io_regs_*` (66 leaves) pairs against one
      packed 266-bit `regs` port (`core_types.LfbRegs`) through `amports.py`, and the
      `io_regBus_*` / `io_regs` names were the trigger for the `tools/diff/vlog.py`
      word-boundary fix in 1a96914 (`input reg_bus__cmd__ready` parsed as type `reg`).
      pysim: `tests/test_lfb.py` (42 tests) drives the real MemBus port and checks every
      emitted pixel against a Python converter over all 16 `writeFormat`s x 4 `rgbaLanes`
      x word-swap x byte-swizzle x {full, lo-half, hi-half} mask x both
      `pixelPipelineEnable` routings under random stalls, plus the two-fetch read path
      against a Python framebuffer with the `pciFifoEmpty && !pipelineBusy` gate;
      `tests/test_cpu_frontdoor.py` (7) pins the `DECODE_QUIRKS` table (chip-field zeroing,
      `texBaseAddr[18:0] << 3` read relocation, raw offset on writes) and the fixed-priority
      response chain. Appended to `core_types.py`: `LfbMode`, `LfbRegs`, `TexWritePayload`
      (PciFifo.scala:12), `expand_rgb565/555`, `word_swap_and_swizzle`.
      Four things worth knowing:
      (a) **The LFB does not dither.** The Scala comment says it and the Verilog confirms it:
      bypass mode emits `Write.PreDither` and the shared `Dither` in `Core` does the work;
      `enableDithering`/`ditherAlgorithm` are only copied from `fbzMode` into the payload.
      (b) `writeFormat` 3 and 6-11 have no `is()` in the decode `switch`: they emit
      RGB 0 / `zaColor[15:0]` depth / `rgbWrite` — format 3 as *two* pixels (it is in
      `isDualPixel`), the others as one. Reproduced, and the pysim sweep covers them.
      (c) `CoreCpuFrontdoor`'s texture-write acknowledgement is a one-cycle `rsp.valid`
      that ignores `rsp.ready`, and a second texture write accepted on the cycle the first
      is being answered clears `texWriteRspPending` (last assignment wins) — one ack for
      two writes. Both kept; `test_cpu_frontdoor.py` pins them as documented behaviour.
      (d) The reset split (plain `Reg` -> `reset_less`): `capturedAddr`, `capturedData`,
      `capturedRead1/2`, `fbReadAddr`, `capturedSource` and `texWriteRspSource`. Every
      other register is `RegInit`. Register writes are kept in the Scala's statement
      order — the `Lfb` sync block relies on last-wins for `state` (`stateRresp` exit vs
      the `Rwait` entry) and for `fbReadCmdPending` (cmd.fire clears, rsp.fire re-arms).

### Phase 3
- [x] triangle_setup  - [ ] rasterizer  - [x] span_walker  - [ ] fastfill (3 files)  - [x] write
  - span_walker — **2026-09-20**. `avoodoo/raster/span_walker.py` (+ its `object SpanWalker` layouts; the TriangleSetup/Rasterizer record types it consumes are in `core/core_types.py`). `make diff MOD=SpanWalker` **clean at 1e6 cycles x seeds 1,2,3** (1090 s on the Mac); `tests/diff/test_diff_span_walker.py` (20k x 3, plus a ready-density 0.1 run) and `tests/test_span_walker.py` (8 tests: cycle-accurate Python reference model on `fixed_model`, the ClipDisabled regression literal `(0, 1, 0)`, the clip-left clamp, the in-module formal span invariants, the FormalDut sequencing invariants, a `Reg`-vs-`RegInit` reset_less audit, and a port-name/width cross-check against `SpanWalker_spinal.v`). First diff run caught the `AFixValue` layout-member signedness bug in `lib/fixed.py` (fixed by p1-fixed). Not ported: `SpanWalkerFormalTest`'s two literal `xEnd` constants — the RTL (Spinal and this port alike) emits `visibleEndPix - 1`, which the upstream *simulation* regression agrees with; see the test module docstring.
- [x] **triangle_setup** (2026-09-20). `amaranth/avoodoo/raster/triangle_setup.py` (498
      Scala lines), plus `avoodoo/vertex.py` (the `package object voodoo` vertex helpers
      from `Bundles.scala`) and `triangle_setup_input_layout` appended to
      `avoodoo/core/core_types.py` (the `Output` half and the shared config bundles are
      the rasterizer agent's). **`make diff MOD=TriangleSetup` clean at 1e6 cycles x
      seeds 1,2,3**, plus 20k x 3 at `--valid-density 0.95 --ready-density 0.12` in
      `tests/diff/`; 34 pysim tests in `tests/test_triangle_setup.py` compare **every**
      output bit against a `fixed_model.py` reference model over directed corner cases
      and three random seeds, and re-assert the `GenerationFlags.formal` bounding-box
      invariants every cycle `o.valid` is high. No `DiffEntry.overrides` needed: Spinal's
      258 scalar leaf ports pair against two packed Amaranth ports (`i__payload` 2129
      bits, `o__payload` 2504) through `amports.py` alone.
      Five things worth knowing before the next raster module:
      (a) **`reset_less` is not optional here.** `pending` and `outputReg` are plain
      Spinal `Reg`s and must not reset; `state`, `iterIndex`, `dxMagWork`, `dyMagWork`
      and the 33 `IterChannel` registers are `RegInit`/`.init(0)` and must. The split was
      read off the `if(reset)` block of `fpga/rtl/diff/TriangleSetup_spinal.v`, and
      `test_triangle_setup.py::test_reset_less_registers_match_spinal` pins it.
      (b) **A signal Spinal declares inside a `when` is emitted unconditionally.**
      `coeffsVec`, `edgeStartVec` and `scaleByChunk`'s `scaled` all look conditional in
      the Scala but are plain `assign` / `always @(*)` in the Verilog, because Spinal
      treats a signal declared in a conditional scope as complete within it. The port
      builds all of the `Prep` and `Iterate` combinational math outside the `m.If`, with
      only the register writes inside — which is also the only way Amaranth takes it.
      (c) **Three Spinal width rules have no Amaranth equivalent** and got named helpers:
      `SInt + SInt` keeps the operand width (Amaranth grows), `|<<` is a width-preserving
      shift, and `SInt >> n` with a constant *narrows* by `n`. The trap is
      `dxPix = (startXPix - aXPix).resize(16 bits)`: the subtract wraps at **13** bits
      (max of 12 and 13) and only then sign-extends to 16.
      (d) **`AFixValue` read out of an `amaranth.lib.data` layout did unsigned
      arithmetic** — `data.View` hands a ShapeCastable member an unsigned slice and
      `AFixValue.__init__` kept it, so every add, compare and saturation on a
      record-resident AFix was unsigned. Found here, fixed in `avoodoo/lib/fixed.py` by
      the fixed-point agent (the shape decides now, not the incoming Value). Anything
      ported before that fix and not diff-tested against negative values is suspect; the
      same trap bites test code, where `ctx.get(afix.raw)` now returns a *signed* int.
      (e) `TriangleSetupTest.scala`'s "scale factor is 2^4 = 16" does **not** convert to
      fixed point: SpinalSim's `AFix #= Int` assigns the *value*, so the test's triangles
      are sixteen times bigger than they read and its expected bounding boxes (`96.0` for
      `x = 6.0`) are values, not raw words. The pytest port reproduces that literally so
      the two files diff line for line.
- [x] **write** (2026-09-20). `amaranth/avoodoo/pixel/write.py`; the
      `PixelCoords`/`FbRouting`/`FramebufferPlaneBuffer.WriteReq` layouts and the
      `FramebufferAddressMath` / `packWordLane` helpers went into
      `avoodoo/core/core_types.py`. `make diff MOD=Write` clean at 3 seeds x 1e6
      cycles (combinational, no clk/reset on either side);
      `tests/test_write.py` ports `WriteTest.scala`'s payload claims (the Bmb
      opcode/length/last assertions have no counterpart now that the output is a
      `WriteReq` stream) and adds a 400-case stride sweep.

### Phase 4
- [ ] color_combine  - [x] fog  - [x] dither tables  - [x] dither module  - [ ] framebuffer_access  - [ ] pixel_pipeline
- [x] **fog** (2026-09-20). `amaranth/avoodoo/pixel/fog.py`, three `m2sPipe`
      stages, `ColorCombine.Output` in / `Fog.Output` out. **The fog table is
      not a `Mem`**: `io.fogTable` is `in Vec(Bits(16 bits), 64)`, 64 flat input
      ports read combinationally in stage 1, so the Amaranth side is
      `In(data.ArrayLayout(unsigned(16), 64))` and no `amaranth.lib.memory` is
      involved; the 64 registers live in `RegisterBank`. `make diff MOD=Fog`
      clean at 3 seeds x 1e6 cycles, 161 ports paired with **zero overrides**
      (`io_fogTable_<N>` -> `fog_table__<N>` and the `AFix` leaf
      `io_input_payload_depth` -> `input__payload__depth` both fall out of the
      mechanical rule). `tests/test_fog.py` is a pysim run against a Python
      model of `voodooFls` / the 86Box `wDepth` reconstruction / the table
      interpolation / the four blend modes; the three places Spinal's
      non-growing `+`/`-` make the result wrap (`wFogFactorRaw`, `diff`,
      `finalColor`, all SInt(10)) and the `wBlend.asSInt`-before-sign-extend
      reinterpretation are each pinned by their own case.
- [x] **dither tables — formula-generated, no data pasted** (2026-09-20).
      `amaranth/avoodoo/pixel/dither_tables.py` builds all eight 86Box tables
      (`rb4x4`/`g4x4`/`rb2x2`/`g2x2` + the four `dithersub` ones, 10 240 entries,
      931 Scala lines) from two Bayer rank matrices and a shift-and-add level
      count; `tests/test_dither_tables.py` (40 tests) asserts **every entry**
      against the Scala literals in `tests/data/dither_tables_spinal.json`
      (extracted by `amaranth/tools/extract_spinal_tables.py`) and again against
      `emu/86Box/src/include/86box/vid_voodoo_dither.h`, which is byte-identical
      to the Scala. 86Box itself has no generator — the tables are literal arrays
      there too — so the generator was recovered from the data: per cell they are
      provably *not* `floor((a*v+b)/c)` for any integers (LP over the thresholds),
      but `levels(v) = 2v - (v>>4) + (v>>7)` (5-bit) / `4v - (v>>4) + (v>>6)`
      (6-bit), `divmod` by the tile size, bump the cells ranked below the
      remainder, reproduces them exactly. The API matches the Scala object
      (eight `lookup*`, four `*_packed` ROM images, `packed_index` =
      `Dither.packedAddr`) so `pixel/dither.py` can be a 1:1 port.
- [x] **dither module** (2026-09-20). `amaranth/avoodoo/pixel/dither.py`
      (`Dither.scala`, 118 lines): three `amaranth.lib.memory.Memory` ROMs of
      5120 x 5/6/5 initialised from `dither_tables.rb_packed`/`g_packed`, read
      latency 1, same register stages and signal names. **Diff-clean at 1e6
      cycles x 3 seeds** (`make diff MOD=Dither`, 14 ports paired) and again at
      `--valid-density 0.9 --ready-density 0.12`; `tests/diff/test_diff_dither.py`
      runs both at 20k in `make test`. `tests/test_dither.py` (7 tests, 6 s)
      ports `DitherTest.scala`'s three cases — 4x4 and 2x2 exhaustive over all
      4·4·256 / 2·2·256 cells, and the disabled-bypass truncation — plus two the
      Scala does not have: a full-rate streaming pass over every combination
      (pins "one result per cycle, latency 1") and a randomised handshake run
      compared every cycle against a cycle-accurate model of the expanded
      `streamReadSync`.
      Three things worth carrying into `framebuffer_access.py`, which has the
      same ROM idiom (`ditherAddr`, `dsubRbRom`/`dsubGRom`):
      (a) **the two clock enables differ**, and the Scala source hides it —
      `Mem.streamReadSync` expands to ROM read registers enabled by
      `addrStream.fire` but `retValid` + the four `linked` registers enabled by
      `addrStream.ready` (`Dither_spinal.v` is the ground truth; the Scala reads
      as if the R channel used `ready` for both). Unobservable at full rate,
      which is why the backpressure diff run is the one that matters.
      (b) `retValid` is a `RegInit(False)` but the four `linked` registers are
      plain `RegNextWhen`, so only the latter are `reset_less=True` (plan §6).
      The ROM read-data registers have no reset on either side either; the
      outputs still match from reset because the enables do.
      (c) `from __future__ import annotations` and `wiring.Component`'s
      class-level member annotations are mutually exclusive — Amaranth sees the
      annotations as strings and raises "does not have signature member
      annotations" — so the members go to `super().__init__()`, as in
      `frontend/swap_buffer.py`.

### Phase 5
- [x] tex_layout_tables  - [x] tmu_texel_decoder  - [x] tmu_texture_cache  - [ ] tmu  - [x] texture_mem  - [x] texture_mem_subsystem
- [x] **tmu_texture_cache** (2026-09-21). `avoodoo/texture/tmu_texture_cache.py`, 1:1 with
      `TmuTextureCache.scala` (16 slots x 2 ways x 8-word lines, 16-deep request window);
      structure map in the docstring. 13 pysim tests (ReuseTest cases, FormalTest asserts
      per cycle, a request-level model predicting every fill and texel). Diff clean at
      1e6 x seeds 1,2,3 and at 0.9/0.12 backpressure, 102 ports, one override
      (`dbgDataValid -> sample_fetch__valid`: Spinal's setName renames the port). Needs the
      responder `tests/diff/tmu_texture_cache_stimulus.py`: a tap decoding outside every
      LOD livelocks the cache on both sides, so random stimulus compares two stuck designs.
      All 13 RAMs `init=[]` + distributed_ram. Findings reproduced 1:1: a cold miss answers
      with the pre-capture texel (0) for the tap resolved on the emitting beat (confirmed
      through the whole Tmu; the Console builds without the cache, CoreSim with it); the
      16-bit bank formula lets bilinear taps alias (formal `noTapBankAlias` assumption);
      `io.busy` drops one cycle after a push into the empty request FIFO.
- [x] **tex_layout_tables** (2026-09-20). `amaranth/avoodoo/texture/tex_layout_tables.py`
      is the pure-Python transliteration of the Scala object (`TexConfig`/`Tables`
      dataclasses, `compute`, 22-bit wrapping kept); `tests/test_tex_layout_tables.py`
      (171 tests) checks every non-multibase configuration LOD by LOD against an
      independent reimplementation of 86Box `voodoo_recalc_tex12`
      (`vid_voodoo_texture.c:59-144`) written from the C's width/height/shift loop.
      Noted for Phase 5: **the Scala deliberately differs from 86Box under
      `tLOD_multibase`** — 86Box adds its running offset to the replaced base
      (`tex_base[1] = texBaseAddr1 + size(LOD0)`), the Scala uses the register bare
      for LOD 1/2 and a tail offset restarting at LOD 3. The port follows the Scala;
      the test pins the divergence so it is not "fixed" by accident.
- [x] **tmu_texel_decoder** (2026-09-20). `amaranth/avoodoo/texture/tmu_texel_decoder.py`
      is the 1:1 port of `TmuTexelDecoder.scala` (all 13 formats + the 3 unassigned codes
      that fall through to RGB565, NCC/YIQ decode with the 11-bit wrapping adds and
      `clampToU8`, P8/AP88 palette, `bilinearWeights`/`blendChannel`, `sendConfig`
      override, three `m2sPipe` stages). The `object Tmu` bundles it needs
      (`PaletteWrite`, `TmuPassthrough`, `SampleFetch`, `BilinearWeights`, `NccTableData`,
      `NccTables`, `Output`, `TextureFormat`) live in `texture/tmu_types.py` for `tmu.py`
      / `tmu_texture_cache.py` to extend; `expandTo8` / `clampToU8` / `Color` are the
      shared `core_types.py` ones. **Diff-clean at 1e6 cycles x 3 seeds**
      (`make diff MOD=TmuTexelDecoder`, 70 ports, no overrides) and again at 1e6 x 3
      with `--valid-density 0.9 --ready-density 0.12`; `tests/diff/test_diff_tmu_texel_decoder.py`
      runs both shapes at 20k/10k. No directed stimulus was needed: `paletteWrite` is a
      Flow, so the generic driver randomises it every asserted cycle and all 256 entries of
      the four replicated RAMs are written within a few thousand cycles, interleaved with
      the P8/AP88 lookups; `nccTables` is 688 bits of free input re-randomised every cycle.
      `tests/test_tmu_texel_decoder.py` (37 tests) ports the two Scala cases exactly (the
      Scala test itself is stale -- it drives `passthrough.ncc`/`readIdx`/`requestId` fields
      that no longer exist) and adds an independent Python model per format, all 256 NCC
      indices on both tables, palette write-then-read, the 3-cycle latency, back-pressure
      and a 400-request random sweep.
      Two things worth recording: (a) the palette RAM is **read-before-write** -- the
      Spinal Verilog puts the write and the `readSync` in separate `always` blocks -- which
      is Amaranth's default `read_port(domain="sync")` (`transparent_for=()`), and the
      diff would have caught the other choice ~1400 times per 1e6 cycles; a unit test pins
      it. (b) The 256 x 24-bit RAMs have no `init` in Spinal but Amaranth must attach one;
      at this size the resulting `initial` block is harmless (no `strip_meminit` needed)
      and both sides read zero before the first write, so the early cycles diff too.
      No AFix anywhere in this module, so the layout-read signedness fix in
      `lib/fixed.py` does not touch it.
- [x] **tmu constant ROMs** (2026-09-20, ahead of `tmu`).
      `amaranth/avoodoo/texture/tmu_tables.py` = the `lookupTables` area of
      `Tmu.scala:53-80` only (no pipeline): the 512-entry 17-bit reciprocal LUT
      (formula, exact-integer `round`) and the 256-entry log2 LUT, whose Scala
      literal is exactly `floor(log2(1 + i/256) * 256)` and identical to 86Box's
      `logtable` (`vid_voodoo_render.c:111`). `tests/test_tmu_tables.py` (8 tests)
      pins the log table against the extracted literal. No other pure constant
      tables exist under `ext/SpinalVoodoo/src/voodoo`: the NCC, fog and gamma
      tables are all register-loaded at runtime, and texel expansion is a
      bit-replication helper, not a LUT.
- [x] **texture_mem + texture_mem_subsystem** (2026-09-20).
      `amaranth/avoodoo/texture/texture_mem.py` is `TextureMem.scala` (an object, no
      Component): `DownloadConfig` (+ `from_register_bank(m, c, reg_bank.tmu_config)`),
      `WriteCmd`, the two private `*CpuWritePrep` layouts, `write_cmd_to_membus`
      (`toBmb`) and `write_cmd_from_pci_drain` (`fromPciDrain`, both the packed and the
      linear branch). `amaranth/avoodoo/core/texture_mem_subsystem.py` is the Component:
      `s2mPipe` on the TMU command, `queue(2)` on the CPU read, fixed TMU > CPU-write >
      CPU-read priority re-evaluated per cycle, `routedSource` with the 2-bit route tag
      in the low `source` bits, rsp demux on `rsp.source[1:0]`. Supporting additions:
      `membus_params.tmu_bmb_params(c)` (`Tmu.bmbParams`, canWrite = false so the
      command has no data/mask) and a hardware `TexLayoutTables.compute` --
      `tex_layout_tables.TexConfigLayout` + `compute_tables(m, cfg)` -- next to the
      Python model (checked against it, 3 x 68 random configs). `TexWritePayload`
      comes from `core_types.py` (added by the Lfb/frontdoor port).
      Tests: `tests/test_texture_mem_subsystem.py` (13; a Python model of the packed
      address decode incl. the LOD 9..15 / non-TMU0 drops, directed 8/16-bit/seq8/
      multibase cases, 2-cycle latency + 1/cycle throughput, arbitration order with
      all three pending, TMU starvation, queue depth 2, rsp routing for all 64
      source values x slave readies, write rsp swallowed) and
      `tests/diff/test_diff_texture_mem_subsystem.py` (20k x 3 + 10k back-pressured).
      Diff: `make diff MOD=TextureMemSubsystem` **clean, 3 seeds x 1e6 cycles**
      (256 s on the Mac, 54 ports paired: 32 in / 22 out, no overrides), plus
      3 x 200k at `--valid-density 0.9 --ready-density 0.1` clean. The generic
      driver's random `texMem.rsp.source` does not echo the command source, but both
      sides route purely on `rsp.source[1:0]`, so this is strictly wider coverage
      than the system contract, not a gap; no responder needed. Two Spinal details
      reproduced on purpose: the 9-entry `Vec` read with a 4-bit `lod` falls to
      entry 8 for 9..15 (Spinal's `case` default), and the second `m2sPipe` loads
      its payload register on `ready`, not `fire`, so thrown-away writes still
      land in it. `register_bank.py` (in flight) carries its own copy of the
      hardware table compute; dedupe onto `compute_tables` later.

### Phase 6
- [x] address_math  - [x] plane_buffer  - [x] plane_reader  - [x] plane_direct_reader  - [ ] framebuffer_mem_subsystem
- [x] **address_math** (2026-09-20). `amaranth/avoodoo/framebuffer/address_math.py`
      is the canonical port of `FramebufferAddressMath.scala` (`mul_by_small_unsigned`,
      `pixel_byte_offset`, `plane_address`) plus exact-integer models;
      `tests/test_address_math.py` sweeps the core widths (26/12/11) and a narrow
      configuration where `resize` zero-extends. `core/core_types.py` carries an
      identical private copy that `pixel/write.py` picked up earlier; fold it into an
      import from here when `core_types` is next touched.
- [x] **plane_buffer** (2026-09-20). `amaranth/avoodoo/framebuffer/plane_buffer.py`,
      both branches of `FramebufferPlaneBuffer.scala`. Findings:
      * `Config.voodoo1()` has `useFbWriteBuffer = false`, so the existing GenOne
        `FramebufferPlaneBuffer` entry (and CoreSim / TangConsole138k) is the 8-deep
        direct queue only. Two GenOne entries were added (`fpga/src/voodoo/diff/GenOne.scala`,
        flagged parameter addition): `FramebufferPlaneBufferWb` = the Dock/Mega/Artix
        combiner (`useFbWriteBuffer = true, fbWriteBufferLineWords = 256`) and
        `FramebufferPlaneBufferWbSmall` = the ThroughputTest config (12-bit address,
        4-bit burst length, 2-word lines) so random stimulus can fill a line.
      * Random addresses rotate on almost every write, so a fourth and fifth row
        (`FramebufferPlaneBufferWbLocal`, `…WbSmallLocal`; GenOne aliases) attach a
        `responder` (`tests/diff/plane_buffer_stimulus.py`) that drives `io_writeReq`
        in sequential runs of 1 .. 2*lineWords+3 words with byte merges mixed in, so
        appends, full-line drains and 256-beat bursts happen in lockstep.
      * Diff: all five rows clean at 1e6 cycles x seeds 1,2,3 and at 2e5 cycles
        with `--valid-density 0.9 --ready-density 0.1` (seed 7).
        `tests/diff/test_diff_plane_buffer.py` runs nine short versions. No
        `overrides` needed.
      * `directWriteQueue` is instantiated in the combiner branch too (never pushed;
        Spinal keeps it as `streamFifo_3`), and `DrainState.Rsp` is unreachable
        (`Send` -> `Idle`); both ported as-is.
      * The Scala's full-drain and flush-drain `when`s are independent, so a full
        byte-complete line seen under `io.flush` steps *both* reason counters for
        one drain; the test model reproduces that.
      * `syn_ramstyle`: every RAM here is a `lib/stream.py:Fifo`, each built with
        `mem_attrs=spinal_fifo_ram_attrs(width, depth)` (the
        `force_distributed_fifo_ram.py` rule, landed in e487ead). That stamps
        `directWriteQueue` (8 x 62 bits, both branches) `distributed_ram` and
        leaves the 255-deep slot FIFOs in BSRAM, as the Spinal bitstream has them;
        at `fbWriteBufferLineWords = 2` the depth-1 slot FIFOs are one `m2sPipe`
        register each and hold no RAM.
      * `tests/test_plane_buffer.py` (20 tests): the two ThroughputTest scenarios,
        the FormalTest asserts as per-cycle checks (slot invariants, `drainState`
        implies valid+draining, the `writeReqPipe.ready` promise, WRITE-only, no
        `readRsp`), its cover point as a hard check, and locality-biased random
        traffic against a scoreboard (dirty-word merge tracking, burst shape,
        drain reasons incl. single-word drains and rotate classification) with all
        20 statistic outputs compared at the end; plus the direct branch.
- [x] **plane_reader + plane_direct_reader** (2026-09-21).
      `amaranth/avoodoo/framebuffer/plane_reader.py` (`FramebufferPlaneReader.scala`,
      incl. the `needBurstSplitter` chain, which `Config.voodoo1()` does not build --
      `lib/membus.py` note 9) and `plane_direct_reader.py`. Findings:
      * **The generic driver cannot drive this module.** `io_mem_rsp` is only accepted
        while a matching miss is outstanding, so a held random `source = 1` beat parks
        the stream ~30 cycles after reset, and a random 26-bit `io_readReq` address is
        never the next prefetched lane. This is what `DiffEntry.responder` was added for
        (`tools/diff/responders.py`, landed with plane_buffer's `*Local` rows).
        `tests/diff/plane_reader_stimulus.py` composes an in-order `MemSlave` on
        `io_mem_rsp` (answers every `io_mem_cmd` beat: `((addr & 3) + length) // 4 + 1`
        words from a lazily random image, `source` echoed) with `SpanTraffic`: spans of
        1..16 lanes plus 10 % long ones (up to 600 lanes), odd start/end bytes, and the
        reads for a span released three cycles after its prefetch fires -- in order with
        20 % of lanes dropped (forward skips), 10 % repeats of an earlier lane (direct
        misses, "behind") and 0.2 % random addresses. The prefetcher is throttled to
        48 queued reads, because an in-order memory deadlocks the reader when the
        801-lane `laneFifo` fills behind an outstanding direct miss (the real pipeline
        never runs that far ahead). Five registry rows: `FramebufferPlaneReader` (slave +
        traffic), `*MemOnly` (slave only: random, half-inverted spans -- parks within a
        few hundred cycles because a wrapped `wordCount` wants more beats than the 11-bit
        `length` asks for, identically on both sides), `*Random` (bare generator, kept so
        the parking stays reproducible), `FramebufferPlaneDirectReader` (bare generator
        is fine: it ignores `rsp.source`) and `*Mem` (behind the slave). The three alias
        rows are GenOne aliases, as the plane_buffer `*Local` rows are (the harness names
        the Spinal module after the registry key).
      * **What the diff found.** No RTL mismatch on either module at any point. It did
        find a stimulus bug: with `_Prefetches` ordered before `_Reads` in the composite,
        the prefetcher re-checked its throttle in the cycle a prefetch fired, before that
        span's reads were staged, so two long spans went out back to back (1200 lanes)
        and every seed parked around cycle 20k -- a clean 1e6-cycle "pass" whose seed-1
        counters equalled its 20k-cycle counters. Reads now run first; the pytest asserts
        `reqCount > cycles / 10` and a majority of hits so a parked run fails.
      * **Gate.** `make diff` clean at 1e6 cycles x seeds 1,2,3 for all five rows
        (main row 127 s on the Mac; per seed ~333k reads, 74 % cache hits, ~88k direct
        misses, ~9.5k spans of which ~940 long, `singleBeat`/`multiBeat` bursts ~800/9000,
        52 % of cycles `fillStallCycles` = waiting on memory) and at 2e5 cycles with
        `--valid-density 0.9 --ready-density 0.1` (seed 7; 19k reads).
        `tests/diff/test_diff_plane_reader.py` runs seven short versions and checks the
        responder stats. `tests/test_plane_reader.py` (18 tests) ports the five
        `FramebufferPlaneReaderTest` scenarios, checks the four `FormalTest` invariants
        every cycle from `cacheDebug`, runs random traffic against a Python model on the
        Scala test's 12-bit config, builds the `mkLengthFixerConfig` splitter chain, and
        audits that **no** register is `reset_less` (every Scala `Reg` here has `init`).
      * `syn_ramstyle`: `spanIssueQueue`/`consumeQueue` (124 x 64) and `issuedSpanQueue`
        (124 x 4) get `distributed_ram`; `laneFifo` (16 x 801), `readRspFifo` (16 x 16),
        `directMissLaneQueue` (1 x 8) and the direct reader's two FIFOs stay untagged --
        the `force_distributed_fifo_ram.py` rule via `spinal_fifo_ram_attrs`. The 801-word
        `initial` block is ~24 KB standalone (harmless); CoreSim's `strip_meminit` removes
        it with the rest.
      * Ported as-is: `reqSameLineCount := reqSameLineCount` (never advances),
        `maxOccupancy`'s `resize(8)` truncating the 10-bit occupancy before the compare,
        and the fill retiring on `wordCount` rather than `rsp.last`.
      * **`allow_direct_miss`** (2026-09-21, for p6-fbmemsub). The reference Scala's local
        patch adds `FramebufferPlaneReader(c, allowDirectMiss: Boolean = true)`; the mem
        subsystem builds `fbScanoutReader` with `false`, which ties `directMissNeeded` to
        `False` (a scanout read is always preceded by its line prefetch; an early direct
        miss in the one-cycle prefetch/consume startup window would hold the ordered
        response path). Ported as the keyword-only `allow_direct_miss=True`. Sixth row
        `FramebufferPlaneReaderNoDirectMiss` (GenOne entry, not an alias) diffs it under
        `reader_traffic_800_in_order` -- the same spans with repeats and random addresses
        off, because a read *behind* the pointer now waits forever (the first run with the
        normal traffic parked at ~10 reads per seed; both sides agreed). Clean at 1e6 x
        seeds 1,2,3 and under back-pressure; ~11.7k reads / 20k cycles, zero `source = 1`
        commands, asserted by the pytest. Unit test
        `test_no_direct_miss_waits_for_the_prefetched_span`.

### Phase 7
- [ ] scanout  - [ ] core + core_sim + dbg  - [ ] Glide suite identical  - [ ] trace replays identical  - [ ] SpinalSim tests ported

### Phase 8
- [ ] core_gowin + ddr adapters  - [ ] demo/UI modules  - [ ] tang_console_138k + hw/console  - [ ] bitstream on hardware

### Phase 9
- [ ] formal  - [ ] docs  - [ ] archival decision
