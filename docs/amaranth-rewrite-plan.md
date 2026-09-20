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
      reference) + `amaranth/tests/test_fixed.py` (62 tests: doc tables, shape sweeps over
      every core format pair, pysim sweeps per op). The doc's traps are reproduced and
      tested: 13-bit `ceil(0)`/`roundHalfDown(0)` widths, `UQ(0, 8)` as a zero-bit constant
      zero, truncate-toward-zero `BigInt./`, `fixTo` with `drop < 0` skipping rounding,
      `asUInt` clamping negatives, `sat` flipping to unsigned. `QFormat` grew no
      `fraction <= width` check so `UQ(0, 8)` stays expressible. Diff test against a Spinal
      `AfixOps` micro-module still owed (0.4; wanted port list is in `test_fixed.py`).
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
      `hardware/tang-console-138k/program_debug_sram.sh` while holding MODE.
- [ ] 0.4 diff harness proven on StreamWhile + Fpxx
  - **Spinal side done (2026-09-20).** `fpga/src/voodoo/diff/GenOne.scala`
    (`scala-cli run . --main-class voodoo.diff.GenOne -- <Module> [outDir]`, 44 buildable
    entries, 2 stubs) generates `<Module>_spinal.v`; `DiffWrappers.scala` wraps the
    non-Component `StreamWhile`/`Fpxx`; `AfixOps.scala` is the AFix op-by-op reference for
    0.2; `ports.py` emits the port-name maps committed under `diff/portmaps/`. StreamWhile,
    FpxxDiffWrap, AfixOps, PciFifo, Rasterizer, ColorCombine and CoreSim generate and pass
    `verilator --lint-only` with 0 errors. Conventions and the harness inventory:
    `docs/amaranth/spinal-naming-and-harness.md`. Remaining: the Amaranth side
    (`gen_module.py`, `tools/diff/harness.py`, the stimulus driver).
- [ ] 0.5 harness copy skeleton links

### Phase 1
- [x] stream helpers  - [ ] membus + adapters  - [ ] fpxx  - [ ] cdc/pipeline  - [ ] axi  - [ ] bus_interface  - [ ] pci_fifo
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

### Phase 2
- [ ] regdef  - [ ] register_bank  - [ ] address_remapper  - [ ] swap_buffer  - [ ] lfb  - [ ] cpu_frontdoor  - [ ] core_types

### Phase 3
- [ ] triangle_setup  - [ ] rasterizer  - [ ] span_walker  - [ ] fastfill (3 files)  - [ ] write

### Phase 4
- [ ] color_combine  - [ ] fog  - [ ] dither + tables  - [ ] framebuffer_access  - [ ] pixel_pipeline

### Phase 5
- [ ] tex_layout_tables  - [ ] tmu_texel_decoder  - [ ] tmu_texture_cache  - [ ] tmu  - [ ] texture_mem  - [ ] texture_mem_subsystem

### Phase 6
- [ ] address_math  - [ ] plane_buffer  - [ ] plane_reader  - [ ] plane_direct_reader  - [ ] framebuffer_mem_subsystem

### Phase 7
- [ ] scanout  - [ ] core + core_sim + dbg  - [ ] Glide suite identical  - [ ] trace replays identical  - [ ] SpinalSim tests ported

### Phase 8
- [ ] core_gowin + ddr adapters  - [ ] demo/UI modules  - [ ] tang_console_138k + hw/console  - [ ] bitstream on hardware

### Phase 9
- [ ] formal  - [ ] docs  - [ ] archival decision
