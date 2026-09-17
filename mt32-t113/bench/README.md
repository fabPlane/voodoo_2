# bench — the real-time-factor gate

The whole project turns on one number: **does `mt32emu` render MT-32 audio
faster than real time on one Cortex-A7 at 1.2 GHz?** `docs/PLAN.md` § 0 sets
the target at a real-time factor of **0.6 or better on the busiest passage**.

This directory holds the harness that answers it. It does not hold the answer.

## Status, honestly

| | |
|---|---|
| `mt32emu` static library, host x86-64 | **builds** |
| `mt32emu` static library, armv7-a Cortex-A7 NEON hard-float | **builds** |
| `rtf` harness, host | **builds and links** |
| `rtf` harness, armv7-a | **builds and links** |
| SMF parser | **tested** against a synthetic multi-track file (running status, sysex, tempo map) |
| Static footprint | **measured** — see below |
| **Real-time factor** | **NOT MEASURED.** No ROMs in this environment, and no ARM hardware or emulator. |

There is no RTF number anywhere in this repository, and there must not be one
until somebody runs the harness on a real T113 (or at minimum on a real
Cortex-A7). A host x86-64 run would produce a number, and that number would be
meaningless for the gate.

## Vendored upstream

Munt is cloned, not submoduled, and not committed.

```
git clone https://github.com/munt/munt.git bench/vendor/munt
```

| | |
|---|---|
| Commit used | `6e7c01fba7e1d50c8fa705834889fd0eac136075` |
| Date | 2026-06-22 |
| Library version | mt32emu 2.8.3 |
| Licence | LGPL 2.1 (`vendor/munt/mt32emu/COPYING.LESSER.txt`) |

To reproduce exactly:

```
git -C bench/vendor/munt checkout 6e7c01fba7e1d50c8fa705834889fd0eac136075
```

`bench/vendor/` is in `.gitignore` at the repo root. Do not add it as a
submodule — the LGPL boundary is cleaner if upstream is fetched, not embedded,
and the port will diverge from upstream anyway.

## Building

### Host

```
cmake -S bench -B bench/build-host -DCMAKE_BUILD_TYPE=Release
cmake --build bench/build-host -j
```

Verified with GCC 13.3.0 / CMake 3.28.3 on Ubuntu 24.04. Zero warnings.

### armv7-a, Cortex-A7, NEON-VFPv4, hard float

```
apt install gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf     # Debian/Ubuntu

cmake -S bench -B bench/build-armv7 -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_TOOLCHAIN_FILE=$PWD/bench/cmake/toolchain-armv7a-neon.cmake
cmake --build bench/build-armv7 -j
```

Verified with `arm-linux-gnueabihf-g++ 13.3.0`. The resulting binary's ELF
attributes confirm the target is right:

```
Tag_CPU_name:            "7-A"
Tag_CPU_arch:            v7
Tag_FP_arch:             VFPv4
Tag_Advanced_SIMD_arch:  NEONv1 with Fused-MAC
Tag_ABI_VFP_args:        VFP registers          (i.e. hard float)
```

The toolchain file defaults to Thumb-2 (`-mthumb`) and `-O2`. Both are
deliberate and both are worth re-measuring on the board: `-marm` and `-O3`
are one `CROSS_PREFIX`-style edit away in
`bench/cmake/toolchain-armv7a-neon.cmake`.

The cross binary has **not been executed** — there is no ARM hardware and no
`qemu-arm` in this environment. It is a build artefact only.

### mt32emu configure flags that worked

Set by `bench/CMakeLists.txt` before `add_subdirectory`, so you do not have to
pass them by hand:

```
-DBUILD_SHARED_LIBS=OFF
-Dlibmt32emu_SHARED=OFF                   # static: what a bare-metal image wants
-DBUILD_TESTING=OFF
-Dlibmt32emu_BUILD_TESTING=OFF            # otherwise it looks for doctest
-Dlibmt32emu_C_INTERFACE=OFF              # we use the C++ classes directly
-Dlibmt32emu_CPP_INTERFACE=ON             # forced ON anyway when SHARED=OFF
-Dlibmt32emu_WITH_INTERNAL_RESAMPLER=ON   # needed for any rate != 32000 Hz
-Dlibmt32emu_WITH_VERSION_TAGGING=OFF
```

`libmt32emu_REQUIRE_ANSI` is left at its default (TRUE): mt32emu targets C++98
and builds clean that way. Do not raise `CMAKE_CXX_STANDARD` without a reason.

## Running the harness

### You need ROMs, and they are not here

MT-32 and CM-32L ROMs are copyrighted by Roland. They are not in this
repository and never will be. Dump them from hardware you own.

A usable set is either:

| Machine | Control ROM | PCM ROM |
|---|---|---|
| MT-32 (old, v1.04–v1.07) | 64 KiB (or two 32 KiB mux halves) | 512 KiB (or two 256 KiB halves) |
| MT-32 (new, v2.03–v2.07) | 128 KiB | 512 KiB |
| CM-32L / CM-64 / LAPC-I | 64 KiB | 1 MiB (or two 512 KiB halves) |

Put them in a directory and point `--rom-dir` at it. The harness identifies
them by SHA-1, so filenames do not matter. Without them it exits **1** with a
message; it never falls back to a default and never prints a number.

```
./bench/build-host/rtf --list-roms       # every dump this build recognises
./bench/build-host/rtf --list-machines   # machine configurations
```

### Host run (sanity only — NOT the gate)

```
./bench/build-host/rtf \
    --rom-dir /path/to/roms \
    --midi    /path/to/busiest-passage.mid \
    --sample-rate 32000 \
    --block   256
```

This tells you the harness works and the MIDI file loads. **It tells you
nothing about the T113.** Do not quote its RTF.

### On a T113 board (this is the gate)

Any cheap T113 board running Linux is a valid proxy — the question is cycles,
not the OS. Copy across the cross-built binary, the ROMs and the MIDI file:

```
scp bench/build-armv7/rtf  root@board:/root/
scp -r roms busiest.mid    root@board:/root/
```

On the board, first pin the clock so you are measuring silicon and not the
governor:

```
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq    # expect ~1200000
echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
```

Then run it pinned to one core, at real-time priority, with a warm-up:

```
taskset -c 0 chrt -f 80 ./rtf \
    --rom-dir ./roms \
    --midi    ./busiest.mid \
    --sample-rate 32000 \
    --block   256 \
    --warmup  2.0 \
    --csv     rtf-blocks.csv
```

`taskset -c 0` is the point: the gate is one core. `--warmup 2.0` discards the
first two seconds so cold caches and a lazy governor do not poison the
worst-case figure. `--csv` gives you the per-block timings, which is where the
real story is — a good mean with a 3× spike at the busiest bar still underruns
a DMA ring.

Sweep the things that actually move the number:

```
for b in 64 128 256 512 1024; do ./rtf ... --block $b; done     # block size
./rtf ... --sample-rate 48000                                    # the resampler is not free
./rtf ... --reverb off                                           # how much reverb costs
./rtf ... --renderer float                                       # the float model, for comparison
./rtf ... --analog digital                                       # drop the LPF emulation
./rtf ... --partials 24                                          # if 32 does not fit
```

`--sample-rate 48000` matters: the PCM5102A will most likely run at 48 kHz, and
mt32emu's native rate is 32 kHz, so the internal resampler is in the real signal
path. Measure it, do not assume it is free. `--src-quality fastest|fast|good|best`
trades passband for cycles.

### Reading the output

```
real-time factor (overall): 0.xxxx    mean cost
worst-case block RTF      : 0.xxxx    <-- this is the one the gate is about
peak active partials      : n of 32   did the passage actually load the synth?
```

If `peak active partials` is well under 32, the MIDI file is not the busiest
passage and the measurement does not answer the question. Pick material that
pegs the partial allocator — a dense Sierra-era MT-32 score, not a piano solo.

## Static footprint (measured, no ROMs needed)

`size(1)` on the armv7-a Release build, and on the host for comparison.

```
cmake --build bench/build-armv7 --target footprint
```

### armv7-a, Cortex-A7, Thumb-2, `-O2`

| | text | data | bss |
|---|---:|---:|---:|
| `libmt32emu.a` (all objects) | 105,863 | 4,553 | 3,040 |
| `rtf` linked (lib + harness + libstdc++ bits) | 145,137 | 5,313 | 3,056 |

Stripped `rtf` on disk: 153,684 bytes.

Biggest objects in the library (text):

| Object | text | note |
|---|---:|---|
| `Synth.cpp.o` | 40,213 | includes the renderer templates, both int and float |
| `BReverbModel.cpp.o` | 8,502 | |
| `ROMInfo.cpp.o` | 7,382 | mostly the SHA-1 table of known dumps — droppable on the port |
| `Part.cpp.o` | 7,206 | |
| `Analog.cpp.o` | 5,024 | LPF emulation |
| `Partial.cpp.o` | 3,788 | |
| `LA32WaveGenerator.cpp.o` | 3,611 | |
| `MidiStreamParser.cpp.o` | 4,385 | not needed if you feed `playMsg` directly |
| `Tables.cpp.o` | 720 text, **2,620 bss** | the exp9/logsin9 LUTs |

Host x86-64 for comparison: library 184,876 text / 9,017 data / 3,472 bss;
`rtf` 263,378 / 10,065 / 3,584.

**So: under 150 KB of code.** That is not the constraint. The constraint is
runtime state.

### What the ROMs and the live state add at runtime

These are computed from the source, not guessed — see `ANALYSIS.md` for the
citations.

| Item | Bytes | Where it comes from |
|---|---:|---|
| Control ROM image, held in `Synth` | 65,536 | `Synth.h:176`, `controlROMData[CONTROL_ROM_SIZE]`, `CONTROL_ROM_SIZE = 64*1024` |
| PCM ROM, **decoded** to 16-bit | 524,288 (MT-32) / 1,048,576 (CM-32L) | the 8-bit log ROM is expanded 1 byte → 1 `Bit16s`, so RAM ≈ the file size. `Synth.cpp:656-673` |
| `sizeof(Synth)` | 65,868 | measured on armv7 — 65,536 of it is the control ROM array |
| `MemParams` × 2 (`mt32ram`, `mt32default`) | 138,070 | 69,035 each, `Synth.cpp:301-302` |
| Renderer temp buffers | 49,152 (int) / 98,304 (float) | 6 × `MAX_SAMPLES_PER_RUN`(4096) × sizeof(Sample), `Synth.cpp:212-214` |
| Reverb delay lines, all 4 modes preallocated | ≈103,000 (int) / ≈206,000 (float) | summed from `BReverbModel.cpp:59-190` |
| 32 × `Partial` | 7,168 | `sizeof(Partial)` = 224 on armv7 |
| 9 × `Part` | 4,140 | `sizeof(Part)` = 460 |

**Rough totals, MT-32 ROM set, int renderer:**

- Read-only-ish, could live in flash or be mapped: PCM 512 KiB + control 64 KiB = **576 KiB**
- Live mutable state: ≈ 138 KB (MemParams) + 49 KB (render buffers) + 103 KB (reverb) + partials/parts ≈ **300 KB**
- Code: **≈ 150 KB**

CM-32L doubles the PCM figure to 1 MiB, so **≈ 1.1 MB read-only + 0.3 MB live**.

The T113-i has 128 KiB of on-chip SRAM (plus the HiFi4's own). **Nothing here
fits in SRAM as a whole.** DDR3 is mandatory, which the plan already assumed.
The interesting question is the other way round: what is small and hot enough to
be worth pinning. The candidates, in order:

1. `exp9[512]` + `logsin9[512]` — 2,048 bytes of `Tables.cpp` bss, and they are
   hit on essentially every sample of every partial (`LA32WaveGenerator.cpp:33-34`
   literally caches pointers to them "because they are accessed extremely often").
   These belong in SRAM or nailed into L2.
2. The renderer temp buffers, if `MT32EMU_MAX_SAMPLES_PER_RUN` is cut from 4096
   to the actual block size. At 256 frames that is 3 KiB instead of 48 KiB.
3. The 32 `Partial` objects plus their TVA/TVF/TVP — about 7 KiB of the hottest
   mutable state in the system.

Three knobs worth pulling on the port, all compile-time in `globals.h`:
`MT32EMU_MAX_SAMPLES_PER_RUN` (4096 → block size), and
`Synth::preallocateReverbMemory(false)` to allocate only the active reverb mode
(≈25 KB instead of ≈103 KB) at the cost of allocating on a mode change — which
you do not want in a render thread, so probably keep it preallocated.

## Harness options

Run `rtf --help`. The ones that matter:

| Flag | Default | Why |
|---|---|---|
| `--block N` | 256 frames | the DMA ring size. Worst-case RTF is a strong function of this. |
| `--sample-rate N` | native (32000) | anything else engages the internal resampler |
| `--partials N` | 32 | the hardware number; lower it only as a fallback |
| `--analog MODE` | `coarse` | `digital` skips the LPF; `accurate`/`oversampled` raise the native rate to 48/96 kHz |
| `--renderer int\|float` | `int` | mt32emu's own default is `int`, and it is the faster model |
| `--reverb on\|off` | `on` | measure both; reverb is a real share |
| `--warmup SEC` | 0 | always use ~2 s on a board |
| `--csv FILE` | — | per-block timings; the worst case is what matters |
| `--machine ID` | — | needed to disambiguate CM-32L half-ROM dumps |

## Files

| Path | What |
|---|---|
| `rtf.cpp` | the harness: SMF parser, ROM discovery, timed render loop |
| `CMakeLists.txt` | builds mt32emu static + `rtf`; `footprint` target |
| `cmake/toolchain-armv7a-neon.cmake` | Cortex-A7 / NEON-VFPv4 / hard float |
| `ANALYSIS.md` | what dominates cost per sample, and whether NEON or the second core can help |
| `vendor/munt/` | the clone (gitignored) |

The SMF parser is written from scratch in `rtf.cpp` (no copyright asserted) so
no third-party licence enters the bench beyond mt32emu's LGPL 2.1. It handles
note on/off, aftertouch, control change, program change, pitch bend, sysex
passthrough, running status, format 0/1/2, PPQN and SMPTE division, and the
set-tempo meta event. Everything else in a file is skipped by design.
