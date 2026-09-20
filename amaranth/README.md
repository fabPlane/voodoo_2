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
```

`make rtl-core-sim`, `rtl-console`, `diff`, `gowin-console` are declared but
not implemented; each prints the plan phase that owns it.

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

## Gotchas

- This directory is called `amaranth/`, the same as the library. It contains no
  `amaranth/` package of its own, so `import amaranth` still resolves to the
  installed library (a namespace-package candidate never wins over a regular
  package found later on `sys.path`). Do not add an `__init__.py` here.
- Run `make` from this directory, not from the repo root.
- `rtl/`, `.venv/`, and `hw/*/build/` are gitignored (see `.gitignore` here).
