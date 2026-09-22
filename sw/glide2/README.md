# Windows Glide2 capture

Shareware that shipped a Glide2 renderer loads `glide2x.dll`. This directory
cross-builds the same trace-capture runtime used by the Linux Glide tests as
that DLL, so a Win32 program can be recorded and replayed through the 86Box
Voodoo reference model.

```
glide2-clear.exe  --(Wine, glide2x.dll)-->  trace.bin
trace.bin         --(trace_test --ref-only)-->  PNG
```

The DLL is Glide2 / SST-1 (one TMU). It is the runtime a 3dfx MiniGL
`opengl32.dll` imports. A cleared frame only proves the DLL loads, so the
3dfx SDK tests are what get run first. Glide3 titles (`glide3x.dll`) are a
different card and are not this DLL.

## Build and smoke test

Requires `i686-w64-mingw32-gcc` and 32-bit Wine.

```bash
make -C sw/glide2
cd sw/glide2/build
wine glide2-clear.exe
```

`glide2-clear.exe` only checks that the DLL loads: it opens a 640×480 SST-1
and clears one frame, so the replay is a solid colour. The trace is `trace.bin`
in the working directory (`SIM_TRACE_FILE` overrides it; under Wine use a
Windows path such as `Z:\tmp\glide2.trace`).

## SDK tests

The programs that exercise Glide2 are the 3dfx SDK tests. `make tests` builds
`test00`–`test29` against this DLL. `./run-sdk.sh` runs the ones that draw
without an external texture, one frame each (`-n 1`), and replays the trace.

```bash
make -C sw/glide2 tests
./sw/glide2/run-sdk.sh
```

That set is points, lines, a gouraud triangle, depth, alpha, fog, backface
cull, iterated alpha, the clip window, and a polygon. `test17`–`test22` and
`test29` load `miro.3df` / `decal1.3df`, which are not in the tree, so they
are built but not run. Each run writes `trace.bin` and `frames/frame_00000.png`
under `/tmp/glide2-sdk/<test>/`.

The clear-screen trace replays with:

```bash
ext/SpinalVoodoo/emu/test/obj_dir/trace_test \
    sw/glide2/build/trace.bin --ref-only --dump-frames /tmp/glide2-frames
```

## What this is for

The Linux GLQuake path goes through Mesa 3.4.2's `fx` driver. A Windows
`glide2x.dll` is a second Glide2 client. The SDK tests are the check that
triangles, depth, alpha, and fog survive that client before a game does.

Shareware, after the SDK tests:

- Windows GLQuake plus the 3dfx MiniGL `opengl32.dll`, using the shareware pak.
- Hexen II demo, same MiniGL.
- Quake II demo, pinned to a MiniGL that imports `glide2x.dll` rather than the later Glide3 ICD.

Native-Glide demos after those: Jedi Knight, Incoming, MDK. Retail games and
Glide3-only demos stay out.
