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

## Status (2026-10-20)

First two-TMU bitstream on the board: B158e40, 40 MHz core (two-TMU
placements at 48 MHz reach 45-47.7 MHz core so far). Glide's own detection
reads tmuConfig 0xc1; stock GLQuake uses GL_SGIS_multitexture (Mesa patch,
which also lets GLQuake's REPLACE + BLEND combine run in hardware instead of
Mesa's software fallback). timedemo demo1: 27.5 fps, against 25.0 fps for the
single-TMU build at 48 MHz. Getting there took area cuts (per-pixel values
reduced when formed, no triangleCMD register copy, per-pixel config slots,
gamma in BSRAM, ...) and three timing rounds driven by the saved PnR reports.

2026-10-21: B159d, two TMUs at 48 MHz (all clocks met), timedemo demo1
31.4 fps. Also: register reads take two cycles (timing), per-TMU palettes
(mtex00 quad 6), SIM_TRACE_FRAMES=N:M frame-window trace capture in the
trace backend (multitexture GLQuake frames; the 86Box reference still
segfaults replaying them in its blend path - to fix).

2026-10-21/22: B160a 32.5 fps (TMU1 cache 512 lines, paired-bank texture
caches). B161d added a syncWait counter: on the board 34-47% of cycles had a
Sync=Yes write waiting at the FIFO head, almost all fbzMode, and ~205 of
GLQuake's ~220 fbzMode writes a frame rewrite the current value (Mesa
re-sends state per texture bind). B163f skips the drain for those (exact):
syncWait 0.5%, pipeline busy 89 -> 93.5%, timedemo demo1 **33.2 fps**.
Remaining stalls: depth reads blocked ~24%, TMU input ~11%.
Known sim-only difference: without the drains the multitexture frame
differs from the drained run in 11 pixels of one span under GLQuake's
full-screen tint (one dither step); not a blend-over-blend ordering race
(ordering blends did not change it).

2026-10-22: B164e (512-entry PCI FIFO) 34.1 fps. The swap was the next
limit: at swap interval 1 with two buffers nothing behind a swap may run
until retrace (34.1 fps vs 46.2 with vsync off, which tears). B165b adds
Voodoo 2 style triple buffering (fbiInit2 bit 4, SST_TRIPLE_BUFFER=1 in the
init stub): **43.6 fps with vsync on**, no tearing. New perf counters 30/31
split "no triangle" cycles by PCI FIFO state: on B165b 33% of cycles had no
triangle and an empty FIFO, i.e. the host could not deliver register writes
fast enough; the PCIe BAR target waited for every write's response. B166
makes BAR writes posted (reads still wait for all earlier writes).
B166a (posted BAR writes) 47.4 fps; B167c (2048-entry PCI FIFO) 48.2 fps
(52.6 with vsync off). B168f re-enabled the DDR bridge counters: DRAM
commands take only ~6% of DDR cycles, read latency ~40 DDR cycles, no
head-of-line waits, so memory bandwidth is not the limit; a 32-deep span
queue changed nothing. With the PCI FIFO nearly full ~55% of cycles the
card itself is the limit now: TMU path stalls (rasterizer held ~15%,
post-TMU entries waiting for texels ~19%) at a 48 MHz core clock.

## Quake II demo baseline (2026-10-24, B168f)

`sw/quake2-glide`: id's GPL 3.21 source, fxMesa ref_gl, official 3.14 demo
data, `timedemo 1; demomap q2demo1.dm2` (633 frames), 640x480, 2 TMUs,
48 MHz, triple buffering.

| ref_gl extensions | fps |
|---|---|
| stock Linux build (no extensions: two-pass lightmaps) | 22.4 |
| SGIS multitexture (default now) | **30.2-30.5** |
| + GL_EXT_point_parameters | 7.3 (Mesa fx point path, ~20k texture flushes) |
| + paletted textures | 31.2, but world surfaces render black (8-bit path broken) |

The stock Linux ref_gl only grabbed extensions under `#ifdef WIN32`; the
patch looks them up in libGL. run-pcie.sh turns point parameters and
paletted textures off.

Per demo frame (~33 ms): 1940 triangles, 66k register writes, 568k raster
pixels (902k in, 458k out). Host: FIFO stall 15 ms, writes 11 ms, game 5 ms.
Card busy 82%: syncWait 28%, postWait 17%, raster held 12%, TMU in-stall
13%, PCI FIFO nearly full 49%.

Period reference: Voodoo2 Quake II demo1 640x480 ~75-85 fps (PII-333/400),
SLI ~91. demo1.dm2 (full game) and q2demo1.dm2 differ, so this is a rough
comparison.

### B169a (2026-10-24): rasterizer fix for sub-row triangles

SpanWalker no longer emits a row for a triangle whose rounded y range is
empty (Quake II dotted lines). gowin-q33, all clocks TNS 0. Quake II
paletted (Mesa global-palette fix): 31.2 / 30.9 fps, dotted lines gone in
DMA frame grabs. GLQuake timedemo demo1 (play-web, default ztrick): 48.2
fps, unchanged. (With +gl_ztrick 0 GLQuake is 38.6: one extra depth clear
per frame.)

### B170a (2026-10-25): full-rate fastfill

The fastfill word writer passed words through a halfPipe (one per two
cycles); Quake II clears depth every frame, ~27% of the frame's busy time
in replay of a cut demo frame. m2sPipe + s2mPipe: Quake II timedemo
**33.6 fps** (31.1 on B169a, +8%), picture clean; GLQuake 48.2 unchanged
(z-trick, no per-frame clear). gowin-q33, TNS 0 (49.6 / 77.4 / 103.3 MHz).

Replay of the cut frame (busy cycles, latency 40): 1.205M after the fix.
Remaining: TMU input stall ~285k (texture line fills, ~10k first-touch
misses a frame; bigger caches change nothing, 8 fill slots -1.1%), the
clear ~231k (write-buffer stalls behind 2 x 256-word slots), span overhead
58k, setup 40k. With 1-cycle memory the frame still takes ~2.1 cycles per
pixel, so the pipeline itself is the main limit.
