# Homebrew Glide demos for the SpinalVoodoo core

Ten small, original Glide2x programs written to exercise the Voodoo/Glide path
of the SpinalVoodoo core as it runs on the **Sipeed Tang Retro Console 138K**.
They are the moral equivalent of "homebrew DOS Glide apps": each is a tiny 3dfx
Glide program that draws a scene, and each is turned into a console-replayable
ROM so it can drive the actual Voodoo core.

## How a Glide app "runs on the Gowin"

The Tang Console core does not execute x86. It renders by **replaying a Glide
command stream** (a sequence of Voodoo BAR register/FB/texture writes) through
the core. The on-chip `voodoo.gowin.DemoAxiLitePlayer` walks a packed ROM of
`addr:u32,data:u32` records and feeds them to the core's AXI-Lite BAR — the same
records `voodoo.gowin.DemoTraceRom` loads for the built-in demos.

So the pipeline for each homebrew app is:

```
  hbNN.c ──(gcc -m32, Glide2x sim backend)──▶ hbNN.exe
  hbNN.exe ──(run, SIM_TRACE_FILE)──▶ traces/hbNN.bin   (VOODOO command trace)
  traces/hbNN.bin ──(pack_trace.py)──▶ roms/hbNN.bin     (addr:data player ROM)
```

`roms/*.bin` are the committed artifacts you deploy to the board (see
[Deploying on the Tang Retro Console 138K](#deploying-on-the-tang-retro-console-138k)).

The Glide backend is the in-tree sim library from `ext/SpinalVoodoo` built in
**trace-capture** mode, which records the command stream without needing the
Verilator model — so no FPGA and no `CoreSim` build are required to regenerate
the ROMs.

## The demos

| ROM | Glide features exercised |
|-----|--------------------------|
| `hb00_clear`     | `grBufferClear` fastfill + `grBufferSwap`, colour cycling |
| `hb01_triangle`  | constant-colour combine, single triangle |
| `hb02_gouraud`   | iterated-RGB (gouraud) shading |
| `hb03_quad`      | two-triangle gradient quad |
| `hb04_pinwheel`  | many triangles/frame, per-primitive constant colour, animation |
| `hb05_depth`     | Z-buffer (`grDepthBufferMode`/`Function`/`Mask`, per-vertex `ooz`) |
| `hb06_alpha`     | alpha blending (`grAlphaCombine` + `grAlphaBlendFunction`) |
| `hb07_fog`       | table fog (`grFogMode`/`grFogTable`, per-vertex `oow`) |
| `hb08_texture`   | procedural texture download + textured combine |
| `hb09_starfield` | high primitive count / command throughput |

## Building / regenerating the ROMs

Requires the 32-bit toolchain and the SpinalVoodoo submodule (both provided by
the Cloud Agent environment; see the repo `.cursor/install.sh`):

```
make            # build all demos, capture traces, pack roms/*.bin
make summary    # print the Glide command mix captured from each demo
make FRAMES=3   # override the per-demo frame count
make clean      # remove build/ and traces/ (roms/ are committed)
```

The capture runs under `setarch -R` (ASLR off): a pointer-dependent path in the
sim Glide backend is otherwise non-deterministic, and disabling ASLR makes the
packed ROMs byte-for-byte reproducible.

## Rendering screenshots

```
make render     # renders each demo to sw/glide-homebrew/shots/<demo>_ref.png
```

`render/render.sh` replays each captured trace through the SpinalVoodoo
`trace_test` tool, which renders via the **86Box software Voodoo reference
model** — the golden model the RTL core is verified against — so the images show
exactly what each demo's Glide command stream produces. It automatically:
applies a tiny `CoreSim` HDMI tie-off patch (`render/coresim-hdmi.patch`, needed
only so the sim top elaborates), initializes the 86Box reference submodule over
HTTPS, and builds `trace_test`.

The cycle-accurate Verilator **core** render (`trace_test --sim-replay-only`,
via `RENDER_SIM=1 make render`) drives the exact SpinalVoodoo RTL, but at
~15-20k cycles/s a full 640x480 frame is impractically slow in CI-class
environments; use a small `HB_RES` and expect minutes per frame on fast
hardware.

## Deploying on the Tang Retro Console 138K

These ROMs are meant to be played by the console demo core (the Voodoo
`TangConsole138k` fabric + `DemoAxiLitePlayer`). To surface them in the on-screen
menu, add entries to `voodoo.gowin.DemoCatalog` referencing the packed file
names and rebuild the console bitstream (`make -C fpga rtl-console` +
`gowin-console`, which needs Gowin EDA).

The resulting core `.bin` is loaded by **TangCore** (vendored at `ext/tangcore`)
as `cores/console138k/voodootang.bin` on the board's USB/SD drive; the TangCore
`monitor` menu boots it, and USB gamepad input arrives over the BL616 UART
protocol the fabric already decodes. See `docs/tangcore-integration.md`.

## Licensing

The demo sources are original works, MIT-licensed (`SPDX-License-Identifier: MIT`
in each file). They call the 3dfx Glide2x SDK API (headers/library from
`ext/SpinalVoodoo`) but contain no 3dfx sample code.
