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
`opengl32.dll` imports. Glide3 titles (`glide3x.dll`: later Unreal Tournament,
Quake III) are a different card and are not this DLL.

## Build and smoke test

Requires `i686-w64-mingw32-gcc` and 32-bit Wine.

```bash
make -C sw/glide2
cd sw/glide2/build
wine glide2-clear.exe
```

`glide2-clear.exe` opens a 640×480 SST-1 and clears one frame. The trace is
`trace.bin` in the working directory (`SIM_TRACE_FILE` overrides it; under Wine
use a Windows path such as `Z:\tmp\glide2.trace`).

Replay:

```bash
ext/SpinalVoodoo/emu/test/obj_dir/trace_test \
    sw/glide2/build/trace.bin --ref-only --dump-frames /tmp/glide2-frames
```

## What this is for

The Linux GLQuake path goes through Mesa 3.4.2's `fx` driver. A Windows
`glide2x.dll` is a second Glide2 client, which is the check that the reference
model is not only correct for Mesa's register stream.

First shareware to run once this DLL loads under a real game:

- Windows GLQuake plus the 3dfx MiniGL `opengl32.dll`, using the shareware pak.
- Hexen II demo, same MiniGL.
- Quake II demo, pinned to a MiniGL that imports `glide2x.dll` rather than the later Glide3 ICD.

Native-Glide demos after those: Jedi Knight, Incoming, MDK. Retail games and
Glide3-only demos stay out.
