# `emu/` — the port layer as a bare-metal ARM image

Workstream E. Status: 2026-09-18. **This boots and runs.** Every number in
[FINDINGS.md](FINDINGS.md) was produced by running the commands below in this
container; nothing here is a projection.

This is the second of the two implementations of `port/include` that
[docs/PLAN.md § 0.5](../docs/PLAN.md) calls for. The first is `port/host`,
which runs on a laptop. This one runs on a Cortex-A7 starting at reset, with no
operating system under it, and it passes the same conformance tests. The third
will be the T113's.

```
        port/src, port/include, port/host/engine_fake.c     ← the same code
   ┌──────────────┬──────────────────┬─────────────────┐       everywhere
   │  port/host   │      emu/        │   (T113, later) │
   │  POSIX       │  PL011, GIC-400, │  UART2, GIC-400,│
   │  files, ALSA │  generic timer,  │  I2S+DMAC, SMHC │
   │              │  virtio-sound    │                 │
   └──────────────┴──────────────────┴─────────────────┘
```

## Build and run

Needs `gcc-arm-linux-gnueabihf` and `qemu-system-arm`. Nothing else, and
nothing is installed.

```sh
make                 # build/mt32emu-bare.elf and .bin
make run             # boot it, print the counters, exit
make test            # the conformance suite -- the real deliverable
make wav             # render to build/out.wav over semihosting, then listen
make virtio          # render through a virtio-sound device model
make mt32emu         # also link the real Munt (needs bench/vendor/munt)
make size            # static footprint
make MMU=0 ...       # build with the MMU and caches left off
make uimage          # wrap the .bin for U-Boot `bootm` on a real board
```

`make run` takes arguments the same way `port/host/mtp_host` does:

```sh
make run ARGS="--midi bank --seconds 4 --block 64 --ring 2 --realtime"
```

They reach the image through ARM semihosting `SYS_GET_CMDLINE`, so one image
serves every case and `make test` never rebuilds.

`make test` runs QEMU with `-icount shift=2`, which derives guest time from
instruction count instead of the host's wall clock, so the audio-deadline
assertions are reproducible on a loaded machine. `make run` deliberately does
not, so an interactive run shows real elapsed time. Neither tells you anything
about how fast a Cortex-A7 is — see [FINDINGS.md](FINDINGS.md) § 2.

| Flag | |
|---|---|
| `--midi demo\|bank\|bad` | which compiled-in stream to play |
| `--engine fake\|mt32emu\|mt32emu-fakerom` | which synthesiser |
| `--sink timer\|virtio` | which audio sink (`timer` is the default) |
| `--wav FILE` | also write the rendered PCM to a host file |
| `--seconds S`, `--block N`, `--ring N`, `--rate HZ`, `--lookahead N` | as in `port/host` |
| `--realtime` | pace the MIDI stream at 31 250 baud |
| `--control-rom P`, `--pcm-rom P`, `--root DIR` | ROM paths, read off the host filesystem |
| `-v` | debug logging |

## What is in here

| File | |
|---|---|
| `link.ld` | image at 0x40200000 — the T113's DRAM base plus the conventional 2 MB — plus the heap, the stacks, the page table and a 1 MB uncached window |
| `src/start.S` | reset: park secondaries, record the entry state, stacks for every mode, VBAR, cache invalidate, BSS, VFP/NEON, MMU, `.init_array`, `main` |
| `src/mmu.c` | flat 1:1 mapping, caches on, audio ring mapped Normal Non-cacheable |
| `src/console.c` | PL011. **The one file that does not transfer to the T113** |
| `src/gic.c` | GICv2 |
| `src/timer.c` | ARM architected generic timer: `mtp_time_*`, and the block-rate tick |
| `src/emu_audio.c` | `mtp_audio.h`: the ring, and the three sinks |
| `src/virtio.c`, `src/virtio_snd.c` | virtio-mmio and a virtio-sound playback stream |
| `src/emu_midi.c` | `mtp_midi.h` from a compiled-in stream, paced at 31 250 baud |
| `src/emu_storage.c` | `mtp_storage.h` over semihosting |
| `src/emu_log.c` | `mtp_log.h` |
| `src/printf.c`, `src/retarget.c`, `src/cxxrt.cpp` | the libc and C++ runtime retarget |
| `src/semihost.c` | host files and argv |
| `src/main.c` | the harness; prints the same counters `port/host` prints |
| `src/engine_mt32emu_fake_roms.cpp` | the real Munt, opened on fabricated ROMs |
| `tools/gen_vectors.py` | compiles `port/host`'s MIDI test vectors into `.rodata` |
| `tools/prepare-mt32emu.sh` | stages Munt's headers out of source; `bench/` is never written to |

Nothing outside `emu/` is modified. `port/src`, `port/include` and
`port/host/engine_fake.c` are compiled directly from where they live.

## The three audio sinks

There is no I2S in `-M virt`, so the sink is the interesting part.

1. **`timer` (default).** A ring of blocks in non-cacheable memory, consumed by
   a generic-timer interrupt at exactly the block rate — the place where the
   I2S DMA engine's completion interrupt goes on the board. The interrupt
   checksums the block and drops it. **This is the sink whose underrun count is
   the contract**, because its clock is a real periodic deadline and nothing in
   it can slow down to hide a late render.
2. **`virtio`.** A real device model: a descriptor ring, period buffers, and
   device-driven completion. Structurally the same shape as the T113's DMAC
   with a linked descriptor list. Needs
   `-global virtio-mmio.force-legacy=false` and an `-audiodev`.
3. **The semihosting WAV tap**, `--wav`, which works with either of the above
   and with no device at all. It buffers in RAM and writes once at close, so it
   does not perturb the deadline.

## Licences

`mt32emu` is LGPL 2.1 and statically linking it carries obligations; see the
project README. `tools/prepare-mt32emu.sh` copies Munt's headers into
`build/` and the build compiles its sources from `bench/vendor/munt` — nothing
under `bench/` is ever written to, and no Munt source is modified.
Everything in `emu/` itself is 0BSD.

No MT-32 ROM data is present anywhere in this directory. The fabricated ROMs in
`src/engine_mt32emu_fake_roms.cpp` are zeroes plus a few table entries, derived
from Munt's own `src/test/FakeROMs.cpp` test fixture.
