# avoodoo — Amaranth port of SpinalVoodoo

Work tracked in [`../docs/amaranth-rewrite-plan.md`](../docs/amaranth-rewrite-plan.md).
This directory is self-contained: nothing here edits the Spinal tree
(`fpga/src/**`) or the `ext/SpinalVoodoo` submodule.

```
make venv                  # uv sync -> .venv
make test                  # pytest
make rtl-module MOD=Counter        # -> rtl/Counter.v
make rtl-module MOD=SpikeBlinky    # (or: make rtl-spike)
make rtl-module MOD=avoodoo.example:Counter PARAMS="width=16"

make rtl-core-sim                  # -> rtl/CoreSim.v   (Mac or Linux)
make sim-model                     # verilate + build the harness  (Linux only)
make sim-build TEST=test00         # link one Glide test against it (Linux only)
make sim-run   TEST=test00         # run it, screenshot -> ext/SpinalVoodoo/output/
```

`make rtl-console` and `gowin-console` are declared but not implemented; each
prints the plan phase that owns it.

## Toolchain (chosen 2026-09-20, Phase 0.1)

| Pin | Version | Why |
|---|---|---|
| Python | **3.14** (`.python-version`, `requires-python = ">=3.13,<3.15"`) | Amaranth 0.5.10 declares `requires-python ~=3.8` and shipped 2026-09-10, i.e. after CPython 3.14. 3.14 was then verified end to end (elaboration, `amaranth-yosys` Verilog emission, pysim, cocotb import) on the Mac and on `hyper-ms-lin`, so it is the newest supported interpreter rather than merely the newest one that installs. `uv` downloads its own 3.14 on both boxes; the Mac's Homebrew 3.14 and the Linux system 3.12 are not used. |
| `amaranth` | **0.5.10** | Latest real release. PyPI also lists `0.6.0`, but **both its artifacts are yanked** ("unintentionally uploaded; contents identical to 0.5.0", 2024-06-14) — do not take it for a newer release. |
| `amaranth-yosys` | **0.50.0.0.post128** | Latest; bundles Yosys 0.50 as a wasmtime module, so Verilog emission needs no system yosys. Neither build machine has to install OSS CAD Suite for `make rtl-*` (the Linux box has it anyway, for `sby` in Phase 9). |
| `cocotb` | **2.1.0** | Latest; `cocotb_tools.runner.Verilator` is present, which is the Phase 0.4 / 4.x differential-harness driver. Both machines have Verilator 5.052. |
| `pytest` | **9.1.1** | Latest. |

Versions are exact-pinned (`==`) rather than ranged: the whole point of this
port is bit-exact reproducibility, and a silent Amaranth minor bump could move
Verilog output from under the diff tests.

## Findings that other Phase 0 tasks depend on

### `amaranth.lib.fixed` does **not** exist in 0.5.10

```
>>> import amaranth.lib.fixed
ModuleNotFoundError: No module named 'amaranth.lib.fixed'
```

It exists only on Amaranth `main` (unreleased), and 0.6.0 on PyPI is the yanked
copy of 0.5.0, so there is no released version to pin. Present and importable
in 0.5.10: `amaranth.lib.memory`, `.cdc`, `.stream`, `.fifo`, `.wiring`,
`.data`, `.enum`, `.crc`, `.io`.

Consequence for plan task 0.2: `avoodoo/lib/fixed.py` must be our own AFix
clone. The "thin wrapper over `amaranth.lib.fixed`" option is off the table
unless we take an unreleased Amaranth, which conflicts with the pinning rule
above. (Task 0.2 owns the decision; this is just the input.)

### `amaranth.vendor.GowinPlatform` does **not** support GW5A / GW5AST

`amaranth/vendor/_gowin.py` parses the device string with

```python
reg_series = r"(GW[12]{1}[AN]{1}[EFNRSZ]{0,3})-"
```

so only GW1N\*/GW2A\* families are accepted; `GW5AST-LV138PG484AC1/I0` raises
`ValueError: Supplied device family name is invalid`. The platform's toolchain
template also drives `gowin_pack`/Apicula or a `gw_sh` project it writes
itself, and its `_dev_osc_mapping` has no GW5A entries.

This costs us nothing: the plan never wanted `GowinPlatform`. Amaranth is used
purely as a Verilog generator, and the existing `fpga/hw/gowin/**` build
scripts (`board_top.v` + CST + SDC + `gw_sh build_*.tcl`) stay the flow. Do not
try to adopt `GowinPlatform` for the Console later without re-checking this.

## Phase 0.3 — Gowin Verilog compatibility spike

`avoodoo/gowin/spike_blinky.py` → `hw/spike/` (build tcl + CST copied from
`fpga/hw/gowin-open/vendor-reference/`). It is a counter on the two active-low
LEDs, a 64x32 `amaranth.lib.memory.Memory` carrying
`syn_ramstyle = "distributed_ram"` that is written and read back forever
(`led_n[1]` lit = passing), and an `FFSynchronizer` on the USER key
(`key0_n`; holding it freezes the blink).

```
make rtl-spike                                  # rtl/SpikeBlinky.v
cd hw/spike && mkdir -p build && cd build && gw_sh ../build_spike.tcl
```

Run the build on `hyper-ms-lin`, one `gw_sh` at a time (`AGENTS.md`).

**Result (2026-09-20): clean.** No errors; memory inferred; `syn_ramstyle`
honoured (32x RAM16S4 SSRAM, **BSRAM 0/340**); escaped identifiers and the
`initial` block accepted; Fmax 195 MHz; 27 s wall. Bitstream built, not yet
loaded on hardware. Full write-up, warnings, and the programming command:
[`hw/spike/RESULTS.md`](hw/spike/RESULTS.md).

## Phase 0.4 — the Spinal-vs-Amaranth differential harness

```
make diff MOD=FpxxDiffWrap            # 1e6 cycles x seeds 1,2,3 — the plan's "done" bar
make diff MOD=Fpxx2AFix DIFF_CYCLES=50000 DIFF_SEEDS=7
make diff MOD=StreamWhileDiffWrap DIFF_ARGS="--ready-density 0.1 -v"
make diff-list                        # what the registry knows about
```

`tools/diff/harness.py` builds one Verilator model containing three modules —
`<Name>_spinal` (from `voodoo.diff.GenOne`), `<Name>_am` (from the registry's
Amaranth factory) and a generated `diff_top` that instantiates both, drives
every input from one wrapper port, and exposes `s_<port>` / `a_<port>`, a
per-output `mm` bit vector and the OR'd `mismatch`. `tools/diff/driver.py`
then applies seeded constrained-random stimulus under cocotb and asserts
`mismatch == 0` every cycle.

It regenerates the Spinal Verilog itself when `fpga/rtl/diff/<Name>_spinal.v`
is missing or older than any `.scala` under `fpga/src` or
`ext/SpinalVoodoo/src`, so `fpga/rtl/` staying gitignored costs nothing.

`make test` runs the same three modules at 20k cycles x 3 seeds through
`tests/diff/`, skipping (not failing) when `verilator` or `scala-cli` is
missing.

**Stimulus rules.** Free inputs are re-randomised every cycle. An input
stream asserts `valid` with probability `--valid-density` and **holds the
payload while `valid && !ready`**, which is the SpinalHDL `Stream` contract;
an output stream's `ready` is random at `--ready-density`. Reset is asserted
for `--reset-cycles` before *every* seed, matching Spinal's SYNC/active-high
default and Amaranth's `rst`. On a mismatch the run stops, prints the
differing port names, and replays that one seed with FST tracing (the
stimulus is a pure function of the seed, so the replay is exact — and a
1e6-cycle trace never gets written).

### Port pairing: the one thing that is not obvious

Spinal emits **one Verilog port per scalar leaf**; Amaranth stops at the
**signature member**. `In(stream.Signature(FpxxLayout(c)))` emits a single
packed `op__payload`, where Spinal emits `io_op_payload_mant`,
`io_op_payload_exp` and `io_op_payload_sign`. Rather than distort every
ported module's signature to line the two up, `tools/diff/amports.py` walks
the Component's signature *and* the `data.Layout` shapes underneath it and
pairs each Spinal leaf with a bit slice of the Amaranth port.

The rest of the mapping is mechanical, driven by the committed port maps in
`fpga/src/voodoo/diff/portmaps/*.json` (see
`../docs/amaranth/spinal-naming-and-harness.md` §1): take the port's path,
camelCase → snake_case each segment, join with `__`; `clk` → `clk`,
`reset` → `rst`. `DiffEntry.overrides` in `tools/diff/registry.py` is the
per-module escape hatch the plan asks for — **it is still empty**, i.e. no
name in the three proven modules needed a special case.

### `Reg` vs `RegInit`: the first thing the harness caught

SpinalHDL's plain `Reg(...)` has **no reset**; only `RegInit(...)` /
`.init(...)` does. Amaranth resets every `Signal` unless told
`reset_less=True`. `StreamWhile`'s `state` is a plain `Reg` and
`output.payload` is combinational from it whatever `running` says, so the two
implementations disagreed for the cycles after a reset. It is invisible on the
first seed (both start at zero) and shows up on the second, which is why the
driver resets before every seed rather than once per run. Five registers
across `lib/stream.py` and `bus/stream_while.py` were fixed this way; expect
the same wherever a Spinal `Reg` is ported.

## Phase 0.5 — the out-of-tree Glide/Verilator harness

`ext/SpinalVoodoo` is a read-only submodule, so `amaranth/emu/sim/` is a copy
of its `emu/sim/` adapted to the Amaranth `CoreSim`. `sim_harness.h` is
**byte-identical** to upstream's (a pytest asserts it), so `libglide2x.so` and
every Glide test binary link against this copy with no source change.

| Path | What |
|---|---|
| `avoodoo/core_sim_ports.py` | the port contract: the 19 Spinal ports + the `dbg` bundle + the memory-backdoor paths. Import this; never retype it. |
| `avoodoo/core_sim.py` | **stub** CoreSim (Phase 7.2 replaces the body, keeps the ports) |
| `gen/gen_core_sim.py` | `rtl/CoreSim.v` |
| `emu/sim/Makefile` | copy of upstream's, with the Spinal-RTL generation rule deleted |
| `emu/sim/harness/sim_harness.{h,cpp}` | the copy; `.h` byte-identical, `.cpp` adapted |
| `emu/sim/harness/sim_smoke.cpp` | standalone check of the whole C API (not upstream) |
| `tests/test_core_sim_ports.py` | cross-checks all of the above without building anything |

### The `dbg` bundle

The upstream harness names 153 hierarchical Verilator signals, but only 14 are
live — the rest sit in two `#if 0` blocks
(`docs/amaranth/spinal-naming-and-harness.md` §2). Hierarchical names are a
SpinalHDL-version artefact, not a contract, so the live ones become real ports:

| Port | Dir | Replaces |
|---|---|---|
| `dbg_pci_cmd_ready` | out 1 | `…core_1__DOT__pciFifo_1_io_cpuSide_cmd_ready` |
| `dbg_frontdoor_cmd_ready` | out 1 | `…core_1__DOT__frontdoor_io_cpuBus_cmd_ready` |
| `dbg_pci_fifo_free` | out 7 | `…core_1__DOT__pciFifo_1_io_pciFifoFree` |
| `dbg_sync_drained` | out 1 | `…core_1__DOT__pciFifo_1_io_syncDrained` |
| `dbg_pipeline_busy` | out 1 | `…pixelPipeline_1_io_debug_pipelineBusy` |
| `dbg_swap_count` | out 2 | `…controlPlane_swapBuffer__DOT__swapCountReg` (read) |
| `dbg_swap_count_set` | in 2 | same register (write) |
| `dbg_swap_count_we` | in 1 | load strobe for the above |

Names are flat, not a nested signature member: Amaranth joins nested paths
with a *double* underscore, so a `dbg` sub-signature would emit
`dbg__pci_cmd_ready`.

`swapCountReg` is the only internal the harness *writes*. A load port costs
`sim_set_swap_count()` one clock, which upstream's poke did not; its only
callers (`emu/test/trace_test.cpp`, `tools/sim_trace_capture.cpp`) call it at
trace-replay boundaries, where a cycle is not observable.

### The memory backdoor: public arrays, not a DPI port

`sim_read_fb` / `sim_write_fb_bulk` / `sim_write_tex_bulk` / `sim_read_tex`
keep reaching straight into the Verilator model, exactly as upstream does, via
`--public-flat-rw`. The alternative — a `dbg_mem_*` port pumped one word at a
time — would turn every texture upload in a trace replay into thousands of
`eval()` calls, so it was rejected.

Two things make this cheaper than upstream rather than merely equal:

* Amaranth 0.5.10 has **no way to attach a `verilator public` attribute to a
  `Memory`** (neither `Memory` nor `MemoryData` takes `attrs=`), so
  `--public-flat-rw` is the mechanism — which is fine, because it is already
  in the upstream `VERILATOR_FLAGS` and the copy keeps them verbatim.
* Where SpinalHDL splits a byte-enabled `Mem` into `ram_symbol0..3`,
  Amaranth + yosys emit **one** `reg [31:0] mem [N-1:0]`, which Verilator
  exposes as `VlUnpacked<IData, N>`. A bulk transfer is one index per word
  instead of four.

The two paths are `CoreSim__DOT__fbWriteRam__DOT__mem` and
`CoreSim__DOT__texRam__DOT__mem` — one `__DOT__` for the `SimRam` submodule,
one for the `Memory` inside it. They are spelled once each, in
`VOODOO_FB_MEM` / `VOODOO_TEX_MEM` at the top of `sim_harness.cpp`, and
`avoodoo/core_sim_ports.py` owns the canonical strings. If Phase 7.2 renames
either submodule, `make -C emu/sim smoke` fails to **compile**, immediately;
it does not produce a black screenshot half an hour into a trace replay.

### Amaranth always initialises a Memory; yosys expands that per word

`Memory(..., init=[])` still emits an all-zero `$meminit_v2` cell, and
`write_verilog` turns it into one `initial` statement **per word** — 3.1
million lines for the 4 MiB + 8 MiB simulation RAMs, which no C++ compiler
will get through. The Spinal `Mem` this replaces has no initialiser at all.

`gen/gen_core_sim.py` therefore emits RTLIL, deletes the `$meminit_v2` cells
(`strip_meminit`), and only then runs yosys. Output is an 11 KB `CoreSim.v`
with bare `reg [31:0] mem [1048575:0]` arrays and no `initial` block;
Verilator's `--x-initial fast` zeroes them at construction. The generator
hard-fails if an `initial` survives.

**Phase 7 must honour this**: any Amaranth `Memory` whose contents are meant
to start undefined has to go through `strip_meminit`; any memory with *real*
contents (dither tables, TMU LUTs) must not. Building the initialiser text is
the slow part — ~35 s on `hyper-ms-lin`, ~55 s on the Mac, for those two RAMs
alone — and it is why `rtl-core-sim` is not instant.

### Driving the upstream Makefiles

Command-line variables beat in-makefile `=` assignments *and* propagate to
sub-makes through `MAKEFLAGS`, so nothing in the submodule is edited:

```
SIM_DIR=<amaranth/emu/sim>  SIM_MODEL=CoreSim
SIM_STAMP=<…>/obj_dir/VCoreSim__ALL.a
SIM_RTL=../../amaranth/rtl/CoreSim.v   # relative: upstream symlinks ../../$(SIM_RTL)
SCALA_SRCS=                            # empty, or the stamp rule re-runs CoreSimGen
```

The copied `emu/sim/Makefile` keeps upstream's `VERILATOR_FLAGS`, `-m32`
`CXX32` build, `OBJ_DIR` layout and object names (`sim_harness.o`,
`verilated.o`, `voodoo_trace_writer.o`, `VCoreSim__ALL.a`) — the Glide
`Makefile.sim` looks for those exact paths. What it drops is the `$(RTL_FILE)`
rule, which would otherwise regenerate the *Spinal* RTL over the Amaranth
one, and the `$readmemb` `sed` that went with it.

### State on `hyper-ms-lin` (2026-09-20)

Verified there, in this order:

* `make rtl-core-sim` → `rtl/CoreSim.v`, 35 s.
* `make sim-model` → Verilator + the whole `-m32` harness, 37 s. Ports and the
  two public memory arrays confirmed in `VCoreSim.h` / `VCoreSim___024root.h`.
* `make -C emu/sim smoke` → **passes**: bus read/write, `sim_idle_wait`,
  fb + texture round-trips including the last word of each (4 MiB−4 and
  8 MiB−4, which is what catches an array one size too small), and the
  `swapCountReg` load path for all four values.

**`make sim-build` does not link yet, and not because of anything here.**
`hyper-ms-lin` has no 32-bit `zlib` / `libX11` / `libXext`, so the final
`libglide2x.so` link fails with `cannot find -lz / -lX11 / -lXext`. Upstream
hits exactly the same wall: SpinalVoodoo gets those from its nix devenv
(`pkgs.pkgsi686Linux.{zlib,xorg.libX11,xorg.libXext}`) and nix is not
installed on that box, so the Glide suite has evidently never been built
there. Everything before the link — all 32-bit Glide objects, `libglide2x.a`,
the harness, the model — builds clean. The fix is three packages:

```
sudo dpkg --add-architecture i386 && sudo apt-get update
sudo apt-get install -y zlib1g-dev:i386 libx11-dev:i386 libxext-dev:i386
```

Until then `make -C emu/sim smoke` is the harness gate, and the
screenshot-parity check against `test-output/` (plan 7.3) is blocked on the
same three packages — for the Spinal build as much as for this one.

### rsync

The `AGENTS.md` Mac→Linux rsync needs three more excludes for this tree:
`amaranth/.venv` (Mac wheels and a Mac interpreter), `amaranth/rtl`
(generated) and `amaranth/emu/sim/obj_dir` (Verilator/`-m32` objects).

## Gotchas

- This directory is called `amaranth/`, the same as the library. It contains no
  `amaranth/` package of its own, so `import amaranth` still resolves to the
  installed library (a namespace-package candidate never wins over a regular
  package found later on `sys.path`). Do not add an `__init__.py` here.
- Run `make` from this directory, not from the repo root.
- `rtl/`, `.venv/`, and `hw/*/build/` are gitignored (see `.gitignore` here).
