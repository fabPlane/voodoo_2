# Toward Voodoo 2: a two-TMU core

First step: an SST-1 with two TREX chips, like the two-TMU Voodoo 1 boards.
Glide 2 (SST-1) and the 3dfx MiniGL already drive that configuration, so
GLQuake gets single-pass lightmaps (texture x lightmap in the TMUs) with no
driver work: about half the fill of the two-pass path, which is where the
Voodoo 2's GLQuake speedup came from. Voodoo 2 specific parts (setup engine
registers, CMDFIFO, the CVG Glide) come after.

## Test setup (done)

- `emu/glide/glide2x/sst1/glide/tests/mtex00.c`: TMU1 "lightmap" x TMU0
  base texture with multiply, add, blend, pass-through and perspective.
- Capture: `SIM_DUAL_TMU=1 SST_NODEVICEINFO=1 SST_TMUCFG=0x40
  SST_TMUMEM_SIZE=2 SIM_TRACE_FILE=... ./mtex00.exe -n 1`. fxsim keeps
  register chip-select bits and TMU1 texture writes in this mode.
- Reference: `REF_DUAL_TMUS=1 trace_test mtex00.bin --ref-only`.

## Hardware steps (`Config.tmuCount = 2`, default 1)

1. **Register addressing.** Chip select is address bits 13:10 (0 =
   broadcast, 0x400 FBI, 0x800 TMU0, 0x1000 TMU1); the register bus is 12
   bits today, so TMU1 writes alias onto FBI registers. Decode the chip bits;
   TMU registers (textureMode, tLOD, tDetail, texBaseAddr x4, trexInit0/1,
   NCC tables) and the S/T/W start/gradient registers (fixed and float) get a
   TMU1 copy written by broadcast or TMU1 writes, while TMU0's copy ignores
   TMU1-only writes.
2. **Setup and rasterizer.** Three more iterated channels (S1, T1, W1) in
   triangle setup (sequential per channel: ~3 more cycles per triangle) and
   in the span walker / span rasterizer.
3. **Second TMU.** Another `Tmu` with its own texture cache and DDR read port.
   TMU1 texture memory is a separate DDR region; texture writes with address
   bit 21 set go there.
4. **Texture combine unit** (textureMode bits 12-29, 86Box
   `voodoo_tmu_fetch_and_blend`): TMU1 output = combine(local1, 0), TMU0
   output = combine(local0, TMU1 output); zero/sub-local/mselect
   (zero, c_local, a_other, a_local, detail, LOD fraction)/reverse/add/
   invert, for RGB and alpha. The two TMUs run side by side on the same pixel
   and their outputs are joined before the combine. TMU0 in pass-through
   mode or TMU0 local-only skip TMU1, as 86Box does.
5. **Detection.** tmuConfig bit 6 (TMU1 present) in the TMU "send config"
   output, so Glide counts two TMUs.

## Host side

Stock id GLQuake looks for `GL_SGIS_multitexture` (the 3dfx MiniGL
extension); Mesa 3.4.2 only had the ARB one.
`sw/quake-glide/patches/mesa-sgis-multitexture.patch` adds
`glSelectTextureSGIS` / `glMTexCoord2fSGIS` (aliases of the ARB calls) and
lists the extension only when Glide reports two TMUs, so single-TMU boards
keep GLQuake's own two-pass path. GLQuake itself is unpatched. Until real
detection works, run with `SIM_DUAL_TMU=1 SST_TMUMEM_SIZE=2`.

## Checks

- mtex00 replay matches the dual-TMU reference.
- GLQuake trace: 8891 mismatches with `tmuCount = 1` and `= 2`.
- Board: GLQuake with `SST_TMUCFG` unset (real detection) picks two TMUs;
  timedemo demo1 above 25.3 fps, and the visual check.

## Cost

One TMU today: ~5.8k FF, ~11k LUT, ~2.3k ALU, ~54 BSRAM. After the space
work (B130 61.3k FF / CLS 83% -> ~48.7k FF / CLS 78%) a second one lands near
86% CLS, about where B130 still met timing on half its placements.
