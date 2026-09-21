# Whole-console simulator runbook

This harness runs the generated SpinalHDL `TangConsole138k` top in Verilator,
models its DDR interface, operates the real on-screen menu through the SNES HID
input, and saves the parallel-HDMI output as 640x480 PNG files. Use it to debug
the menu, trace-ROM demos, framebuffer writes, buffer swaps, and HDMI scanout
without building a Gowin bitstream.

For the architecture, design history, and findings behind the harness, see
[`docs/console-sim-plan.md`](../../../docs/console-sim-plan.md).

## Quick start on the Mac

Run commands from this repository checkout. Verilator 5.052 and `libpng`
through `pkg-config` are the known-good dependencies.

```sh
verilator --version
pkg-config --modversion libpng

cd /Users/hyper/projects/me/vodoo_2/fpga
make rtl-console

cd /Users/hyper/projects/me/vodoo_2/amaranth/emu/console
make model
make run-demos
```

`make rtl-console` generates `fpga/rtl/gowin/TangConsole138k.v`. It is a local
RTL-generation step, not synthesis, place-and-route, or bitstream generation.
Do not pass `--main-class` to Scala CLI; the repository Makefile intentionally
uses the shared `voodoo.Gen` entry point so the warm compile cache is reused.

`make run-demos` runs these four scenarios:

| Target | Menu item | Main HDMI capture |
|---|---|---|
| `make run-logo` | Logo spin | `output/logo.png` |
| `make run-test04` | Glide test04 | `output/test04.png` |
| `make run-test16` | Glide test16 | `output/test16.png` |
| `make run-alphabet` | Alphabet | `output/alphabet.png` |

Run `make run` for the boot menu alone; it writes
`output/boot-menu.png`. A demo normally takes about one to two minutes, while
all four take several minutes.

The harness does not jump directly into a demo. It waits for the menu, presses
Down the required number of times through `hid1`, then presses A. This exercises
the same menu, trace player, render, swap, and scanout path used by the board.

## What counts as a pass

A passing command exits with status 0 and prints a line beginning with
`captured 640x480`. For demo scenarios it also verifies both of these
conditions:

- the HDMI capture has at least 100 non-black pixels;
- the HDMI scanout underflow flag stayed clear.

The diagnostic tail should normally include `frame-aligned=1` and
`underflow=0`. Inspect the PNG as well: the numerical checks catch black frames
and starvation, but visual inspection catches wrong colors, repeated tiles,
tearing, misplaced geometry, and stale-buffer problems.

Current expected images are:

- logo: orange chevron/logo on a dark background;
- test04: a smooth rainbow-colored triangle with text;
- test16: a red/blue tiled triangle pattern with instructions;
- alphabet: rows of characters over a light background.

Each demo also writes two raw RGB565 framebuffer snapshots:

```text
output/<scenario>-ddr.png
output/<scenario>-ddr-late.png
```

Use those together with the main HDMI capture to localize a failure:

| Raw DDR image | HDMI image | Likely area |
|---|---|---|
| Correct | Wrong | scanout, CDC, prefetch, unpacking, or HDMI timing |
| Wrong | Wrong | trace player, renderer, DDR writes, or framebuffer layout |
| Black | Black | demo selection, command progress, rendering, or buffer swap |
| Correct | Intermittent/corrupt | bandwidth, FIFO refill, or clock-domain behavior |

The log also reports player progress, DDR read/write counts, buffer addresses,
scanout cache counters, nonzero HDMI pixels, and the sticky underflow state.
Preserve the entire log when reporting a failure.

## Rebuilding after changes

- Scala/Spinal RTL changed: run `make rtl-console` in `fpga/`, then rerun the
  desired simulator target.
- Only `console_tb.cpp` changed: `make model` rebuilds the Verilator executable.
- No source changed: rerun a `make run-*` target; the existing model is reused.
- Use `make clean` here only when a cold rebuild is wanted. It deletes both
  `obj_dir/` and all captured PNGs under `output/`.

The console simulator is intentionally independent of Gowin PnR. Do not start
`make gowin-console` merely to validate a simulator change.

## Related regression tests

After changing the scanout or DDR request logic, run at least the focused Gowin
tests from `fpga/`:

```sh
cd /Users/hyper/projects/me/vodoo_2/fpga
make test TEST_ARGS='-z "boundary-safe prefetch chunks"'
make test TEST_ARGS='-w voodoo.gowin'
```

Use ScalaTest runner arguments as shown. Do not use Scala CLI `--test-only`,
which creates another cold Bloop project cache.

## Adding another demo scenario

The scenario order in `console_tb.cpp` is the menu order. To add a scenario:

1. Add its name to `demo_names` in `console_tb.cpp` at the correct menu index.
2. Add the name to `DEMOS` in this directory's `Makefile`.
3. Run `make run-<name>` and verify the HDMI and raw DDR captures.

If the menu contains non-demo entries before the new item, extend the harness
to express an explicit menu index instead of relying on array position.

## What this does not prove

The harness does not model Gowin PLLs, the vendor DDR3 PHY/calibration, the
TMDS serializer, FPGA timing closure, board wiring, or signal integrity. A
clean simulator result is strong evidence for the digital path through the
generated top, but a hardware release still needs a successfully routed
bitstream and a test on the board.
