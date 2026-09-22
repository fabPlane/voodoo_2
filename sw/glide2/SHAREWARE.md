# Windows shareware against glide2x.dll

Three freely distributed Windows apps, downloaded from archive.org and
launched under Wine with the trace-capture `glide2x.dll` from this
directory. Unpacked games, zips, and traces are gitignored (`archives/`,
`games/`, `runs/`). The download and run scripts are the pieces that stay
in the tree.

`glide2x.dll` exports the MSVC stdcall aliases MiniGL imports
(`_grGlideInit@0`, `_grDrawTriangle@12`, and the rest). `gen-exports.sh`
writes those aliases. Without them the DLL loads for MinGW callers and the
3dfx SDK tests, and a MiniGL `LoadLibrary` fails.

## Download

```bash
./sw/glide2/fetch-shareware.sh
```

The script skips any archive that is already present and non-empty.

| Local file | archive.org item | Remote file | What it is |
| --- | --- | --- | --- |
| `archives/quake2-demo.zip` | `QuakeII_1020` | `q2_test.zip` | id Quake II Test (October 1997). Includes `q2test.exe`, `ref_gl.dll`, `3dfxgl.dll`, and `baseq2/pak0.pak` (`demo1`–`demo3`). |
| `archives/glquake.exe` | `glquake` | `glquake.exe` | Windows GLQuake. A renderer patch, not a full game. |
| `archives/quake106-shareware.zip` | `msdos_Quake106_shareware_201702` | `msdos_Quake106_shareware.zip` | Official Quake 1.06 shareware. `ID1/PAK0.PAK` is the data GLQuake loads. |
| `archives/mdk-demo.zip` | `MDK_1020` | `MDK.zip` | Shiny MDK Windows demo. `MDKZIP95.EXE` inside the zip is the PKZIP self-extractor that holds `MDKDEM95.EXE`. |

`HexenII_1020` on archive.org is Heroes of Might and Magic II (`H2DEMOW.EXE`,
`HEROES2.AGG`), not Raven Hexen II. It is not fetched. A Raven Hexen II
Windows demo was not on the identifiers that were probed. Forsaken's
archive.org demo is an InstallShield tree, not a loose exe, so it is not
one of the three.

The registered Quake 1.06 item (`quake106`) is not downloaded.

## Run

Build the DLL first (`make -C sw/glide2`), then:

```bash
./sw/glide2/run-shareware.sh
```

The script unpacks into `sw/glide2/games/`, copies `build/glide2x.dll` beside
each exe, and runs them under 32-bit Wine. `watch-swaps` (built from
`watch-swaps.c`) reads the trace as it grows and signals the process group
on the third swap, or after the time limit when the exe never opens
`glide2x.dll`. MiniGL writes texture downloads faster than a Python scan
can read them, and a full level load is tens of gigabytes, so the replay
keeps only that prefix. `trace_test --ref-only` then dumps one PNG per swap
under `sw/glide2/runs/<name>/frames/`.

Wine uses `WINEPREFIX` (default `/tmp/glide2-wine`), `DISPLAY` (default
`:1`), and `FX_GLIDE_NO_SPLASH=1`. `WINEDEBUG` defaults to `-all`. Exit code
143 is the watcher stopping the process with SIGTERM. That is the expected
end of a run that is still drawing.

### Quake II Test

`games/quake2/` is the unzipped test. Their `3dfxgl.dll` stays as shipped.
The readme says Glide 2.4x is required for the 3dfx path. The menu label is
`[3Dfx OpenGL   ]`, and `q2test.exe` sets `gl_driver` to `3dfxgl` for that
choice. `ref_gl.dll` loads that name.

```text
q2test.exe +set vid_ref gl +set gl_driver 3dfxgl +set vid_fullscreen 1
           +set s_volume 0 +map demo1
```

`pak0` contains `maps/demo1.bsp` and `demos/demo1.dm2`. The shipped
`baseq2/config.cfg` has `vid_ref "soft"`; the command line overrides it.
Timeout is 40 seconds. Exit 124 means the timeout stopped a process that
was still running.

### GLQuake

`games/glquake/` gets `glquake.exe`, `id1/pak0.pak` from the shareware zip,
`3dfxgl.dll` copied to `opengl32.dll`, and `glide2x.dll`. GLQuake imports
`OPENGL32.dll`. Wine prefers its builtin `opengl32`, so the run sets
`WINEDLLOVERRIDES=opengl32=n,b` and the game-dir MiniGL is the one that
loads. GLQuake's own error string for a failed load is
`Couldn't load opengl32.dll`. Windowed mode is what its 16-bit-color
message tells you to try:

```text
glquake.exe -basedir . -window -width 640 -height 480
            -nosound -nocdaudio +map start
```

Timeout is 30 seconds. Console text, when the game writes it, is
`qconsole.log`.

### MDK demo

`games/mdk/` is the inner zip extracted from `MDKZIP95.EXE`. The launcher
is `MDKDEM95.EXE`. The readme says Windows 95 needs DirectX 3, and neither
`MDKDEM95.EXE` nor `MDKDEMO.EXE` references `glide2x`, `3dfx`, `voodoo`, or
`opengl`. The run still copies `glide2x.dll` beside the exe and launches
it, so the log shows whether the demo starts. No trace is expected.

## Results

Software mode first (desktop X11 capture), then Glide.

### Software / DirectX

| App | How | Result |
| --- | --- | --- |
| Quake II Test | Wine, `+set vid_ref soft +set vid_fullscreen 0 +map demo1` | Windowed 320×240. Loads `ref_soft.dll`. After ~60s of walking, health 104 in a later room. |
| Quake 1.06 shareware | DOSBox, `QUAKE.EXE -nosound -nocdaudio +map start` | Start map with shotgun HUD (25 shells, 100 health, 25 armor). |
| MDK demo | DOSBox, `MDKDEMO.EXE` (Win95 DirectX path stays black under Wine) | Shiny Entertainment splash, then HMI Sound Setup. No Glide. |

### Glide (trace → reference replay)

| App | Exit | Trace cut | Replay |
| --- | --- | --- | --- |
| Quake II Test (`3dfxgl.dll`) | 143 | 16 swaps (~2.1GB raw) | Swaps 4–15 stay in-level. Later frames are a different room (crate + blaster). Green tint remains. MiniGL re-downloads textures every present, so a wall-clock minute of Glide capture is tens of gigabytes; 16 swaps is as deep as that prefix stays. |
| GLQuake (MiniGL as `opengl32.dll`) | 143 | 12–16 swaps | Stays on the 3Dfx Voodoo console (`GL_VENDOR: 3Dfx Interactive Inc.`). Map/demo commands under Wine have not left the console yet. |
| MDK | — | none | Not a Glide client. |

### Ten-second clips

`./sw/glide2/record-clips.sh` builds these under `/opt/cursor/artifacts` (encode to `/tmp` then copy):

| Clip | Contents |
| --- | --- |
| `quake2_soft_10s.mp4` | Quake II software, demo1 gameplay |
| `quake_soft_10s.mp4` | DOS Quake shareware, start map |
| `mdk_demo_10s.mp4` | MDK DOS demo splash / sound setup |
| `quake2_glide_gameplay_10s.mp4` | Quake II Glide, first in-level frames |
| `quake2_soft_1min_in_10s.mp4` | Quake II software, ~60s into demo1 then 10s |
| `quake2_glide_deeper_10s.mp4` | Quake II Glide, later of 16 in-level swaps |
| `glquake_glide_console_10s.mp4` | GLQuake Glide MiniGL console |

`./sw/glide2/record-q2-deeper.sh` builds the two deeper Quake II clips.

Logs: `runs/quake2-soft/`, `runs/quake-soft/`, `runs/quake2-glide/`, `runs/quake2-glide-deep/`, `runs/quake2-soft-deep2/`, `runs/glquake-glide2/`, `runs/mdk-splash/`.

## Is the Glide2 port finished?

The Windows capture path is in place and proven on one real MiniGL client:

- `glide2x.dll` is a Win32 SST-1 / Glide2 build of the existing trace harness.
- MiniGL's `_grFoo@N` imports resolve.
- The 3dfx SDK tests draw through it.
- Quake II Test's `3dfxgl.dll` loads it and presents in-level frames (green tint is a remaining render bug, not a load failure).

That is not a finished port of every Glide2 title. GLQuake initializes the same MiniGL and has not left the console under Wine in the capped runs. MDK is DirectX, not Glide. Texture downloads make long Glide captures huge, so later-level Glide clips are more swaps of the same session, not a full minute of game time. Software Quake II is what actually walks a minute into the map.
