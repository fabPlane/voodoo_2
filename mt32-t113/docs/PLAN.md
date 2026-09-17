# Plan

Status: 2026-09-17, start of work. Nothing below is measured yet; the first
milestone exists to make one number true or false.

## 0. The gate

**Does `mt32emu` render MT-32 audio faster than real time on one Cortex-A7 at
1.2 GHz?**

Everything else is downstream of that number. The target is a real-time factor
of 0.5 to 0.6 or better on the *busiest* passage — not the average — so that
USB-free I/O, the display and MIDI parsing still fit, and so that a second synth
(FluidSynth, small font) remains conceivable later.

- RTF under ~0.6: proceed to a board.
- RTF 0.6 to 1.0: proceed, but single-synth only, and revisit the sample rate.
- RTF over 1.0: stop. Either the part is wrong or the project is a Pi again.

Measure on real silicon as soon as one is on the desk — a $15 to $25 T113 board
(100ask DongshanPI, MangoPi, a Forlinx eval, whatever is in stock) under Linux
is a perfectly good proxy for this measurement, because the question is cycles,
not the OS.

## 1. What we are building

| | |
|---|---|
| SoC | Allwinner T113-i: 2× Cortex-A7 @ 1.2 GHz, HiFi4 DSP, RISC-V E907, external DDR3 up to 2 GB, LFBGA, industrial temperature |
| DRAM | One x16 4 Gbit DDR3L (512 MB). 256 MB is likely enough — MT-32 alone wants about 16 MB, the rest is SoundFont |
| Audio out | I²S to a PCM5102A, line level, DC-blocked and attenuated for a WaveBlaster mixer input |
| MIDI in | 3.3 V TTL UART at 31 250 baud. DIN via optocoupler on the standalone build; direct from the FPGA's MPU-401 decode on the card build. **No USB**, by choice — that is what keeps a non-Linux platform tractable |
| Storage | microSD: ROMs, SoundFonts, config |
| Software | U-Boot or awboot for DDR and clocks; RT-Thread or bare metal above it; `mt32emu` as the synth |

## 2. Workstreams

Four, run in parallel, each owning its own directory.

### A. `bench/` — the real-time-factor harness
Vendor Munt. Build `mt32emu` as a static library for the host and cross-built for
`armv7-a`, NEON, hard float. Write a headless renderer that takes a SMF or a
captured MIDI stream plus ROM paths and reports RTF, peak partial count, and
per-buffer worst case. Report the static footprint — text, data, bss, heap high
water — because it decides whether the ROMs and state fit where we want them.
No ROMs in the repo; the harness takes paths and skips cleanly without them.

### B. `hw/` — the test board
Minimum viable T113-i board: SoC, one DDR3, PMIC or discrete rails in the right
sequence, microSD, USB for FEL, UART console, I²S header, MIDI in. Read the
reference designs that exist (Forlinx SoM, 100ask, MangoPi) rather than deriving
power sequencing from the datasheet. Answer: layer count, whether JLCPCB will
assemble the LFBGA and at what setup cost, and what the board comes to at qty 5.

### C. `boot/` — bring-up path
What actually boots a T113-i with *external* DDR3: mainline U-Boot (T113 support
landed in v2024.01), awboot, xboot, and the vendor route. Establish where the DRAM
parameters come from for an external-DDR3 part, since awboot's are for the
T113-S3's in-package memory. Then: SD card boot flow and image header, FEL
recovery, toolchain, and how to load a bare-metal or RTOS payload instead of a
kernel.

### D. `port/` — the platform layer
What `mt32emu` needs from a platform: C++ runtime, allocator, float, and nothing
else. Then design the four things around it — an I²S DMA ring that never
underruns, a 31 250-baud UART MIDI parser, FAT on SD for ROMs, and the render
loop and its latency budget. Decide RT-Thread versus a superloop on evidence, not
taste. Produce the header-level platform interface and a stub implementation that
builds against the host, so `port/` can be exercised before silicon exists.

## 3. Sequencing

1. **A alone gates everything.** B, C and D are research and design that can
   proceed in parallel, but no board is ordered until A reports.
2. Buy a cheap T113 dev board early, whatever the paper says.
3. First silicon target is that dev board, not our own: console over UART, then
   audio out of its I²S, then MIDI in. Our board only has to be right after the
   software already works on someone else's.
4. Our board, qty 5, once B and C agree on power, boot and DDR.

## 4. Risks

- **Munt is too slow on an A7.** The gate. Nothing else matters first.
- **DDR3 on a 4-layer board.** Length matching and impedance on a cheap stack-up
  is the classic way to lose a month. Copy a reference layout; do not invent.
- **External-DDR3 T113-i has thinner community ground truth than the T113-S3**,
  whose in-package memory is what every hobbyist board uses. The DRAM parameter
  set is the specific unknown.
- **No USB host means no USB MIDI, ever, on this design.** That is a deliberate
  trade (§ 2.6 of the parent document) and it must stay deliberate.
- **Licensing.** `mt32emu` is LGPL 2.1; static linking carries obligations, so
  keep the boundary clean and publish what the licence requires. ROMs are
  Roland's and never enter this repository.
