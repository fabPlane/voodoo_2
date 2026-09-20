# Phase 0.3 — Gowin Verilog compatibility spike: result

**Verdict: Amaranth's Verilog is usable with Gowin EDA as-is. No errors, three
benign warnings, memory inferred, `syn_ramstyle` honoured. Built, not yet
loaded on hardware.**

Built 2026-09-20, Gowin EDA **V1.9.11.03 Education**, `GW5AST-LV138PG484AC1/I0`
device version C, from `rtl/SpikeBlinky.v`
(amaranth 0.5.10 / Amaranth Yosys 0.50, PyPI `0.50.0.0.post128`).

Ran on the **Mac**, not `hyper-ms-lin`: the Linux box was 10 minutes into
`gw_sh build_console_sdram.tcl` for another agent, and `AGENTS.md` makes the
Mac the fallback exactly then ("Linux already in `gw_sh build*.tcl` → Mac
fallback"). Same Gowin version on both boxes. Wall clock: **27 s**
(synth 0.3 s, place 0.7 s, route 4 s, bitgen 7 s).

```
cd amaranth && make rtl-spike
cd hw/spike && mkdir -p build && cd build && gw_sh ../build_spike.tcl
```

Artifacts (gitignored): `build/impl/pnr/amaranth_spike.fs` (34.7 MB, the SRAM
image), `.bin`, and the synth/PnR logs and reports under
`build/impl/{gwsynthesis,pnr}/`.

## What the spike was supposed to prove

| Risk | Result |
|---|---|
| Escaped identifiers (`\$1`, `\$auto$rtlil.cc:2739:Not$2`, `\SpikeBlinky.key0_ff`) | Accepted. They survive into placed instance names (`$auto$rtlil.cc:2739:Not$2_22_s2` appears in the timing report), so waveform/report readability is the only cost. |
| `initial` blocks (register and memory power-up state) | Accepted. One warning, on yosys' own dummy register: `WARN (EX3780) : Using initial value of '$auto$verilog_backend.cc:2355:dump_module$3' since it is never assigned`. Harmless — that signal is yosys' `initial`-block scaffolding, and it is optimised away. |
| Memory inference | `Extracting RAM for identifier 'mem'` — inferred from the generated Verilog, no vendor template needed. |
| `syn_ramstyle = "distributed_ram"` pass-through | **Honoured.** 64x32 landed as `SSRAM 32 x RAM16S4`, and **BSRAM 0 / 340**. |
| `FFSynchronizer` | Elaborated into its own module `SpikeBlinky.key0_ff`, 2 registers, placed and routed. |

Consequence for plan 8.3: BSRAM style can be steered from Python via
`Memory(..., attrs={"syn_ramstyle": ...})`, so the Amaranth tree should not
need the `fpga/hw/gowin/console/force_distributed_fifo_ram.py` post-processing
hack. (Proven for a 64x32 array; the real check is the Console BSRAM count.)

## Resource and timing

```
Logic                 361/138240   <1%   (112 LUT, 57 ALU, 32 SSRAM/RAM16)
Register              100/139095   <1%
CLS                   206/69120    <1%
BSRAM                 0/340         0%
DSP                   1/298        <1%   MULTALU27X18
I/O Port              4/297
PRIMARY clock         1/8
Fmax (clk50)          195.5 MHz, worst slack +4.886 ns against a 10 ns default
```

Pinout as constrained: `clk50` V22, `key0_n` AA13, `led_n[0]` G11 (the DONE
pin), `led_n[1]` U12 (the READY pin) — hence `-use_done_as_gpio` /
`-use_ready_as_gpio` in the tcl, copied from the vendor reference.

## Two things worth carrying into the real builds

1. **A trivial `addr * 0x01010101` inferred a DSP** (`MULTALU27X18`). Gowin
   maps constant multiplies to DSP without being asked. Somewhere in the core
   port this will matter for DSP budget; watch for it in Phase 4/5.
2. **No SDC → no clock constraint.** `WARN (TA1132) : 'clk50' was determined
   to be a clock but was not created` and then `WARN (PR1014) : Generic
   routing resource will be used to clock signal 'clk50_d'`. Expected for a
   spike with no `.sdc`; the Console build has `tang_console_138k.sdc` and
   must keep it.

## Hardware load (not done — the user must do this)

The board is not programmed by any agent. Hold the case **MODE** button, then:

```
hardware/tang-console-138k/program_debug_sram.sh \
  amaranth/hw/spike/build/impl/pnr/amaranth_spike.fs
```

Expected: `led_n[0]` blinks at ~1.5 Hz (50 MHz / 2^25); holding the **USER**
key (`key0_n`) freezes the blink; `led_n[1]` stays lit for as long as the
64x32 RAM read-back matches, and goes dark forever on the first mismatch.
