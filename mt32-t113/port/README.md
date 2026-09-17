# `port/` — the platform layer

Workstream D. What `mt32emu` needs from below, the design of everything around
it, and a host implementation you can run today.

| | |
|---|---|
| **[PORTING.md](PORTING.md)** | What `mt32emu` actually requires of a platform — C++ revision, STL surface, allocator behaviour, float versus fixed point, threads, file I/O — with the consequences for newlib + libstdc++, whether the two A7 cores help, and a measured memory budget |
| **[DESIGN.md](DESIGN.md)** | The audio path and its latency budget, the 31250-baud MIDI parser, ROM and config loading, and the RT-Thread-versus-superloop call with the reasoning |
| `include/` | The platform interface: audio sink, MIDI source, storage, timebase, log, plus the synth seam and the portable parser/render-loop headers |
| `src/` | Portable port code — the MIDI parser and the render loop. The same files build for the host and for the T113 |
| `host/` | A host implementation of the interface: the audio sink writes a WAV, the MIDI source reads a file, storage is the filesystem. Plus a fake synthesiser, so the whole structure runs with no ROMs and no `mt32emu` |

## Run it

```sh
cd host
make            # fake engine; needs only a C compiler
./test.sh       # asserts on running status, sysex reassembly, ring discipline
./build/mtp_host --seconds 4        # built-in demo stream -> out.wav
```

With the real engine, built out of workstream A's clone (nothing under `bench/`
is written to):

```sh
cmake -S . -B build -DMT32EMU_SOURCE_DIR=../../bench/vendor/munt/mt32emu
cmake --build build
./build/mtp_host --engine mt32emu \
    --control-rom roms/MT32_CONTROL.ROM --pcm-rom roms/MT32_PCM.ROM
```

ROMs are Roland's. They are not here and never will be; the engine refuses to
open without ones it recognises, and says why.

## The findings, in one paragraph

`mt32emu` is C++98 with no STL containers, no threads, no exceptions and no
RTTI, and with two setup calls it performs **zero heap operations while
rendering** — measured, not assumed. The awkward parts are `<fstream>` (one
avoidable file), a reverb-mode sysex that allocates on the render thread unless
you ask it not to, and a MIDI event queue synchronised with `volatile` and no
memory barriers, which is fine on x86 and not fine between two Cortex-A7 cores.
Rendering one synth is inherently single-threaded. A CM-32L configuration needs
about **2.5 MB** resident, so it lives in the DDR3 and not in on-chip SRAM.
`AnalogOutputMode_ACCURATE` outputs exactly 48 kHz, which removes the resampler
— and with it RTTI, `<iostream>` and a 32 KB stack buffer — from the system
entirely. MIDI byte to audible sample comes to **≈5 ms typical, ≈9 ms worst**,
of which only about 0.75 ms is not a block-size trade. Start with a superloop:
neither RT-Thread nor FreeRTOS supplies the two drivers that actually stand in
the way (I²S-over-DMA and SD+FAT on a Cortex-A7 T113), and the entire
RTOS-shaped surface of this port is one function, so the decision is cheap to
reverse.
