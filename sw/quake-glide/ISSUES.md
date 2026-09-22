# GLQuake → Voodoo sim: issues log & upstreaming punch-list

Everything that had to be worked around to get a real, textured Quake frame
through the Linux Glide route:

```
id GLQuake (gl_vidlinux.c, fxMesa) -> Mesa 3.4.2 "fx" (GL1.1 on Glide2)
  -> our libglide2x (sim, trace capture) -> trace_test (86Box Voodoo model) -> PNG
```

Each entry has: **Symptom**, **Root cause**, **Workaround** (what `sw/quake-glide/`
does today), and **Proper fix** (what a clean solution / upstream PR should do).
The three items marked **[UPSTREAM]** are general improvements that belong back
in SpinalVoodoo / 86Box; today they live as patches in `sw/quake-glide/patches/`.

---

## A. Environment / Docker

### A1. Docker overlay2 fails in the nested VM
- **Symptom:** `docker build`/`run` fail with `invalid argument` from the
  `overlay2` storage driver inside the Cloud Agent VM.
- **Root cause:** overlay2 can't stack on the nested container's filesystem.
- **Workaround:** force the `vfs` storage driver
  (`/etc/docker/daemon.json = {"storage-driver":"vfs"}`); see
  `docker/setup-docker.sh`.
- **Proper fix:** none needed — vfs is the correct choice for nested VMs; just
  keep it documented (done in `docker/README.md`).

### A2. i386 container missing kernel/asm headers
- **Symptom:** `gl_vidlinux.c` fails on `#include <asm/io.h>`.
- **Root cause:** the `i386/debian:bookworm` base has no kernel headers, and the
  headless build never touches VGA port I/O anyway.
- **Workaround:** an empty `stubs/asm/io.h` on the include path.
- **Proper fix:** same — the include is dead code for the fxMesa/headless path;
  the stub is the right call. (Could instead `#ifndef HEADLESS` it upstream.)

---

## B. Mesa 3.4.2 build (the OpenGL→Glide2 bridge)

### B1. Choosing a Mesa version that speaks Glide2
- **Symptom:** modern Mesa has no Voodoo1/Glide2 path.
- **Root cause:** Mesa's `fx` driver moved to **Glide3** at 4.x; our sim
  `libglide2x` is Glide2 (sst1/Voodoo1).
- **Workaround:** pin **Mesa 3.4.2**, the last release whose `fx` driver targets
  Glide2, built via `make linux-glide`.
- **Proper fix:** none — 3.4.2 is correct for a Voodoo1 target. (A Glide3 core
  sim + Mesa 4.x/6.x would be a much larger, separate effort.)

### B2. `__FUNCTION__` string concatenation breaks on gcc 12
- **Symptom:** `error: expected ')' before string constant` on lines like
  `fprintf(stderr, __FUNCTION__ ":\n")`.
- **Root cause:** pre-gcc-3.4 treated `__FUNCTION__` as a string literal (so it
  concatenated with adjacent string literals); gcc 3.4+ makes it a `const char[]`
  variable, which can't be concatenated.
- **Workaround:** `-D__FUNCTION__=__FILE__` (both become string literals), plus
  `-std=gnu89 -fcommon` and dropping `-ansi -pedantic`. Applied by a `sed` on
  `Make-config` in `build.sh`.
- **Proper fix:** upstream would replace `__FUNCTION__ "..."` with `"%s ...",
  __func__`; too invasive for a vendored 2001 tree, so the `-D` alias is the
  pragmatic route.

### B3. Hardcoded X11R6 paths
- **Symptom:** Mesa's `Make-config` points at `/usr/X11R6/{include,lib}`, which
  don't exist on Debian.
- **Root cause:** 2001-era filesystem layout.
- **Workaround:** `sed` them to `/usr/include` and `/usr/lib/i386-linux-gnu`.
- **Proper fix:** same (or use `pkg-config x11`).

### B4. `widgets-sgi` (Motif GL widget) fails to build
- **Symptom:** `X11/IntrinsicP.h: No such file` after `libGL`/`libGLU` are
  already built.
- **Root cause:** the demo Motif widget needs Xt/Motif headers we don't install.
- **Workaround:** ignore it — `libGL.so`/`libGLU.so` are the only artifacts we
  need; the failing target runs after them.
- **Proper fix:** build only the `lib` subdir (`make -C src` / `-C si-glu`)
  instead of the top-level `linux-glide` target that also walks demos/widgets.

---

## C. GLQuake build

### C1. Modern GLQuake ports assume GL ≥ 1.5
- **Symptom:** tyr-quake fails with `unknown type name 'GLsizeiptr'` /
  `'GLintptr'`.
- **Root cause:** tyr-quake uses VBO typedefs; Mesa 3.4.2's headers are GL 1.2.
- **Workaround:** use the **original id GLQuake** GPL source (GL 1.1), which
  matches Mesa 3.4.2 exactly.
- **Proper fix:** if a modern engine is wanted, compile it against modern GL
  headers but load functions via `glXGetProcAddress` and run against our old
  `libGL` (extended entrypoints resolve to NULL and the engine falls back). More
  work; the id source avoids it.

### C2. Which video backend (X vs Glide-direct)
- **Symptom:** `gl_vidlinuxglx.c` (GLX) needs a real X server with
  `XF86VidMode`/`XF86DGA`; Xvfb lacks the mode-switch/gamma extensions and the
  init path aborts.
- **Root cause:** the GLX backend does fullscreen mode switching + gamma via X
  extensions Xvfb doesn't implement.
- **Workaround:** use `gl_vidlinux.c` — the **fxMesa** backend
  (`fxMesaCreateContext`/`fxMesaSwapBuffers`) renders straight to Glide with **no
  X server at all**.
- **Proper fix:** same — the fxMesa path is the cleanest for headless capture.
  (Alternatively, run a real `Xorg -config dummy` that provides vidmode/RandR,
  but that's heavier.)

### C3. svgalib console-input dependency
- **Symptom:** `gl_vidlinux.c` references `keyboard_*` / `mouse_*` /
  `vga_getmousetype` and `MOUSE_*` / `KEY_EVENTPRESS` constants (svgalib), which
  isn't packaged on modern Debian.
- **Root cause:** the fxMesa backend historically paired with svgalib for
  console keyboard/mouse.
- **Workaround:** no-op stubs (`stubs/vgakeyboard.h`, `stubs/vgamouse.h`) — a
  timedemo needs no input. (`keyboard_update` must return `int`.)
- **Proper fix:** same for headless; for interactive use, port input to
  evdev/SDL.

### C4. x86 assembly modules (id386)
- **Symptom:** link errors for `BoxOnPlaneSide`, `SV_HullPointContents`,
  `Snd_WriteLinearBlastStereo16`, `Sys_SetFPCW`, etc.
- **Root cause:** the container is i386, so `quakedef.h` sets `id386=1` and
  expects `math.s` / `worlda.s` / `snd_mixa.s` / `sys_dosa.s`; we build C-only.
- **Workaround:** force the portable C build (`#if 0` the `__i386__` guard in
  `quakedef.h`) so the C fallbacks in `mathlib.c`/`world.c`/`snd_mix.c` compile,
  plus `_fpcw_stub.c` for the FPU-control helpers that only exist in asm.
- **Proper fix:** same, or build/adapt the `.s` files with NASM (not worth it).

### C5. `S_Init` segfault — no `/dev/dsp`
- **Symptom:** GLQuake crashes in `S_Init` (during `Host_Init`, before the game
  loop) — deterministic; every run produced an identical short trace.
- **Root cause:** the OSS sound path opens `/dev/dsp`, which doesn't exist in the
  container, and the failure path dereferences a null DMA buffer.
- **Workaround:** `-nosound` (and `-nocdaudio`).
- **Proper fix:** same; the SDL/`snd_null` backend would also avoid it.

---

## D. libglide2x (SpinalVoodoo sim backend)

### D1. **[UPSTREAM]** Texture memory reported as 0 MB → fxMesa OOM
- **Symptom:** `fxDriver: out of memory!` at context creation; the process
  balloons and `malloc` fails.
- **Root cause:** the sim can't probe the Voodoo RAM-sizing registers, so
  `sst1InitGetDeviceInfo()` returns `tmuMemSize = 0`. Mesa's `fxMesa` sizes its
  texture heap from `grTexMaxAddress()` = `total_mem - 8`; with `total_mem = 0`
  that underflows to ~4 GB and `fxTMUInit` tries to create millions of 2 KB
  block nodes.
- **Workaround:** `patches/spinalvoodoo-glquake.patch` clamps the reported sizes
  in `init.c` to a standard 2 MB Voodoo1 (2 MB FBI + 2 MB/TMU) when the sim
  reports 0.
- **Proper fix (upstream):** the sim's device-info path should report the
  configured memory sizes directly (a `SIM_*` env or a compile-time constant),
  not 0. Belongs in `ext/SpinalVoodoo/emu/glide/.../init/init.c`.

---

## E. 86Box reference model (`trace_test`)

### E1. **[UPSTREAM]** Rasterizer OOB crash on unclipped off-screen triangles
- **Symptom:** SIGSEGV in `voodoo_half_triangle` at `dat = fb_mem[x]`
  (`vid_voodoo_render.c`), on a Quake triangle.
- **Root cause:** GL clients (fxMesa) emit triangles with **hardware clipping
  disabled** (`fbzMode` bit 0 clear) and vertices projected far off-screen
  (`real_y = -1`, huge `x`). Two gaps: (1) the **colour** framebuffer base isn't
  masked with `voodoo->fb_mask` the way the **aux/depth** base is, so a negative
  scanline yields a pointer *before* the buffer; (2) the per-pixel span `[x, x2)`
  isn't bounded to the framebuffer row when clipping is off.
- **Workaround:** `patches/86box-render-robustness.patch` masks the colour base
  like the aux path, and clamps the span to `row_width/2` pixels (advancing the
  interpolators for a left-edge clamp exactly as the `clipLeft` path does).
- **Proper fix (upstream):** the same masking + span clamp in 86Box's
  `vid_voodoo_render.c` — it's a real bounds-safety bug, not Quake-specific.

### E2. **[UPSTREAM]** Wrong buffer shown for a single static frame (swap parity)
- **Symptom:** `trace_test` writes an all-black PNG even though the scene
  rendered (blackness log: `presented@... 100% black, other@... has content`).
- **Root cause:** a static capture can be stopped with the freshly-drawn frame
  still in the **back** buffer, so the buffer recorded as "presented" is the
  stale/cleared one.
- **Workaround:** `patches/spinalvoodoo-glquake.patch` makes `trace_test.cpp`
  fall back to the other buffer when the presented one is essentially all black
  but the other has content.
- **Proper fix (upstream):** same heuristic in `trace_test`, or teach the trace
  format to record the final front-buffer offset explicitly.

### E3. Per-swap frame dump (gameplay video)
- **Symptom:** `trace_test` only writes the final framebuffer, so a moving
  scene cannot be turned into a video.
- **Root cause:** the tool was built for single-frame compare against the FPGA
  sim, not for playback.
- **Workaround:** `patches/spinalvoodoo-glquake.patch` adds `--dump-frames DIR`
  and `--dump-stride N`. On each `swapbufferCMD` it writes
  `frame_%05d.png` from the buffer that was just drawn, and if that buffer is
  ≥99% black it shows the other ping-pong buffer instead (the same parity
  problem as E2, but per swap). `capture-demo.sh` encodes those PNGs.
- **Proper fix (upstream):** the same flags in `trace_test`.

---

## F. Capture / rendering workflow

### F1. Magenta/wrong texture colours
- **Symptom:** geometry correct but everything renders magenta/green.
- **Root cause:** Quake uses **8-bit paletted** textures
  (`GL_EXT_shared_texture_palette` / `3DFX_set_global_palette`); the reference
  model has no palette loaded, so texels index a garbage palette.
- **Workaround:** `-no8bit` — full RGBA texture uploads, no palette dependency.
- **Proper fix:** implement the shared-palette extension end-to-end (glide
  `grTexDownloadTable` → trace → ref-model palette) if paletted textures are
  wanted for smaller traces; otherwise `-no8bit` is fine.

### F2. Clean exit needed to flush the trace
- **Symptom:** killing the process leaves a truncated/short trace.
- **Root cause:** the trace writer flushes/closes on normal exit (atexit).
- **Workaround:** free-run the map and stop with **SIGTERM**, which Quake's
  `signal_handler` turns into `Sys_Quit` → clean shutdown → flush.
- **Proper fix:** same; or periodic flush in the trace writer.

### F3. Trace size for `-no8bit` full-scene frames
- **Symptom:** ~1 GB trace for 2 s @ 320×240 (RGBA texture uploads are large);
  8 s ≈ 2.2 GB; `trace_test` replay is O(n) and memory-heavy.
- **Root cause:** every visible texture is re-uploaded as RGBA; a full Quake
  level has a lot of texture data.
- **Workaround:** keep runs short (default 2 s); the start-map view is static so
  a couple of seconds settles it. A gameplay clip (`capture-demo.sh`, default
  12 s of `demo1`) is long enough to reach the e1m3 flythrough and still
  replay in minutes. A full minute extrapolates to tens of gigabytes and hours
  of replay, so the one-minute comparison is the software renderer
  (`capture-software.sh`).
- **Proper fix:** dedupe/cache texture uploads in capture, or use the paletted
  path (see F1) to shrink traces. A Voodoo1 TMU is 2–4 MB, so an RGBA cache of
  a whole level still does not fit; dedupe has to drop textures the hardware
  would have evicted.

### F4. Resolution vs framebuffer (letterboxing)
- **Symptom:** the 320×240 scene sits in the lower-left of the 640×480 Voodoo
  framebuffer.
- **Root cause:** GLQuake ran at 320×240 into the Voodoo's 640×480 buffer
  (lower-left origin).
- **Workaround:** crop `320x240+0+240` and upscale for the gallery image.
- **Proper fix:** run GLQuake at 640×480 to fill the buffer (bigger trace), or
  have `trace_test` crop to the active viewport.

---

## G. On-chip (FPGA) constraints

### G1. Only small demos fit on-chip
- **Symptom:** the GLQuake trace (~1 GB) and the env-mapped torus (~4 M entries)
  can't go in the console's BRAM demo player.
- **Root cause:** `DemoAxiLitePlayer` stores demo ROMs in a concatenated BRAM
  `Mem`; only compact captures fit (the homebrew demos, ~2–11 k words each).
- **Workaround:** GLQuake/torus stay **host-side** replays (`trace_test`); only
  the compact homebrew demos were added to the console menu (see the console
  branch PR).
- **Proper fix:** stream large demos from SD/DDR into the player instead of BRAM
  if on-hardware playback of full games is ever wanted.

---

## Suggested new-branch plan

A reasonable order for a follow-up branch that turns the workarounds into real
fixes:

1. **Upstream the three patches** (D1, E1, E2) into SpinalVoodoo / 86Box so
   `trace_test` and `libglide2x` are robust without `sw/quake-glide/patches/`.
2. **Texture-upload dedupe in capture** (F3) so full-scene traces are tractable,
   then render a 640×480 (F4) GLQuake frame and, ideally, a short timedemo clip.
3. **Optional:** paletted-texture path end-to-end (F1) for smaller traces.
4. **Optional:** package the Mesa/GLQuake build artifacts in the image
   (B1–B4, C*) so `build.sh` skips the from-scratch 2001 build each run.
