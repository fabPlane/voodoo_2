# The platform design

Workstream D. Status: 2026-09-17. Read [PORTING.md](PORTING.md) first — this
document assumes its findings and does not repeat their evidence.

Nothing here has run on a T113. What *has* run, on this machine, is the whole
structure with a fake synthesiser under it: `host/` builds and passes
`host/test.sh`. §7 says exactly what that proves and what it does not.

---

## 1. Shape

```
                      core 0                                    core 1
  ┌───────────────────────────────────────────────┐        ┌──────────────┐
  │                                               │        │              │
  │  UART2 RX IRQ ──> stamped byte FIFO           │        │     WFI      │
  │       (320 us/byte)     │  (mtp_midi)         │        │              │
  │                         v                     │        │ (a second    │
  │              mtp_midi_parser  ── running      │        │  engine or   │
  │                         │       status,       │        │  the control │
  │                         │       sysex         │        │  plane, one  │
  │                         v       reassembly    │        │  day)        │
  │              Synth::playMsg / playSysex       │        │              │
  │                         │  (timestamped)      │        └──────────────┘
  │                         v                     │
  │              Synth::render(int16*, 128)       │
  │                         │                     │
  │                         v                     │
  │              audio ring, 3 x 128 frames ──────┼──> DMA ──> I2S ──> PCM5102A
  │                    (mtp_audio)                │        ^
  │                         ^                     │        │ half/full IRQ
  │                         └─────────────────────┼────────┘
  └───────────────────────────────────────────────┘

  microSD ──> FAT ──> ROMs + mt32.cfg, once, at boot (mtp_storage)
```

Two interrupts, one loop, one core. That is the entire concurrency model, and
it is that way on purpose — see §6.

---

## 2. The audio path

### 2.1 Sample rate: 48 kHz, and it is free

`mt32emu` renders internally at 32000 Hz and says so loudly: the constant is
fixed throughout the emulator and "the output from the synth is supposed to be
resampled externally" (`globals.h:89-94`). The naive readings of that are

- **run I²S at 32 kHz** — legal, the PCM5102A will do it, but 32 kHz is an
  unusual rate to derive cleanly and an unusual rate to hand a WaveBlaster
  mixer input; or
- **resample 32 → 48 kHz** — which means `SampleRateConverter`, and with it
  RTTI (`ResamplerModel.cpp:122`), `<iostream>`, a 32 KB stack buffer per call
  (`SampleRateConverter.cpp:107`) and an argument about quality settings.

Both are wrong, because there is a third option the library already contains.
`AnalogOutputMode_ACCURATE` upsamples inside the analogue-circuit emulation —
"upsampled to 48 kHz to allow emulation of audible mirror spectra above 16 kHz"
(`Enumerations.h:128-132`) — and `Synth::getStereoOutputSampleRate` returns
exactly `SAMPLE_RATE * 3 / 2` = **48000** for it (`Synth.cpp:294-298`).

So we get 48 kHz out of `render()` directly, with no resampler, and the
upsampling is not overhead we are paying for a rate conversion: it is the
emulation of the MT-32's own reconstruction filter, which is a feature. The
synthesis engine still runs at 32 kHz regardless — `doRender` asks the analogue
stage how many 32 kHz samples it needs for the requested output length
(`Synth.cpp:2350`) — so the only thing that scales with the output rate is a
16-tap stereo FIR, about 1.5 M MACs/s.

**Decision: `AnalogOutputMode_ACCURATE`, `RendererType_BIT16S`,
`render(Bit16s*, len)`, 48000 Hz, stereo, no resampler anywhere in the system.**

The clock side is ordinary: derive the I²S bit clock from the audio PLL, 48 kHz
× 32 bits × 2 channels = 3.072 MHz BCLK, 12.288 MHz MCLK if the PCM5102A is
configured to want one (it can run MCLK-less with its internal PLL, which is
why mt32-pi's community likes it).

If workstream A comes back with an RTF that makes 48 kHz uncomfortable, the
fallback is `AnalogOutputMode_COARSE` at 32 kHz — cheaper by that same FIR
difference and by one third of the output samples — and then the I²S runs at
32 kHz too. Note what that fallback does *not* require: still no resampler.

### 2.2 Block size and ring depth

| | Value | Why |
|---|---|---|
| Frames per block | **128** | 2.667 ms at 48 kHz. Small enough that a block of latency is inaudible; large enough that per-call overhead in `render()` is amortised over 128 frames |
| Ring depth | **3** blocks | 8 ms of audio in flight. Triple buffering: one playing, one queued, one being filled |
| Target occupancy | **2** blocks | The loop stops filling at 2 queued, leaving a block free so that a DMA interrupt never finds the writer mid-block |
| Buffer memory | 3 × 128 × 2 ch × 2 B = **1536 B** | Non-cacheable |

Double buffering (depth 2) works and halves the latency, but leaves exactly one
block period of slack for a bad render — no margin at all for an SD access, a
long sysex or a cache-miss storm. Quadruple buffering (depth 4) is the knob to
turn if A's RTF comes back above ~0.7. The block size and ring depth are the
only two numbers in this design that should move in response to a measurement,
and the harness in `host/` takes both as command-line arguments so the
experiment is one command.

### 2.3 DMA and cache

The T113's DMAC takes linked descriptors (`boot/vendor/freertos-t113/fw_main/dmac.h`
shows the descriptor layout for exactly this part). Build three descriptors, one
per block, in a circular list — `next` of the last pointing at the first — and
start it once. From then on the DMA engine runs for ever with no CPU
involvement except the per-descriptor completion interrupt, whose only job is
to advance the play cursor and signal the render loop.

Cache: the CPU writes the blocks, the DMA reads them. Map the 1536-byte ring as
**non-cacheable normal memory**. A clean-by-MVA after each block would also
work and would be marginally faster, but 1536 bytes is not worth a class of bug
that only appears under load; take the uncached write. (Write-combining makes
the uncached penalty on sequential `int16` stores small.)

### 2.4 How the render loop stays ahead

The rule, implemented in `src/mtp_render.c`:

```
loop forever:
    drain the MIDI FIFO and hand every complete message to the synth
    while a block is free and queued < target:
        block = audio.acquire()
        synth.render(block, 128)
        audio.commit()
    if no block was free:  wait (WFI / semaphore) for the DMA interrupt
```

Three properties follow from that ordering, and they are the point:

1. **MIDI is drained before every block, not after.** An event that arrives one
   microsecond before we start rendering lands in *that* block. Draining after
   would cost a whole block of latency for no reason.
2. **The loop stops at `target`, not at "ring full".** Filling to the brim adds
   latency without adding safety: the safety comes from the *depth*, and the
   depth is the same either way. Stopping one block short means there is always
   a writable block for the next iteration, so `acquire()` in the common case
   never returns NULL.
3. **The only blocking call is `mtp_audio_wait`.** In a superloop that is a
   `WFI` inside a loop on the queue depth; under an RTOS it is a semaphore the
   DMA ISR gives. Same five-line interface either way (§6).

The safety margin is explicit and measurable: `mtp_render_stats.min_queued` is
the lowest ring occupancy ever seen after a commit. If it never drops below 1,
the loop never came within a block of an underrun. If it touches 0, you are one
bad block from a click. The host harness prints it; the target should log it
every few seconds.

**What the slack actually is.** With a 2.667 ms block period and RTF *r*, one
block costs 2.667·*r* ms to render and the loop has 2.667·(1−*r*) ms spare per
block. But the *instantaneous* margin is larger: a single block that overruns
can eat up to `queued × 2.667 ms` — 5.3 ms at target occupancy 2 — before the
DMA runs dry, because the ring absorbs it and the following blocks catch up.
At r = 0.5 that is four nominal render times of headroom for one bad block.
That is the shock absorber, and it is why the ring depth, not the block size,
is the parameter to raise when things get tight.

### 2.5 Latency budget

MIDI Note On (3 bytes) at 31250 baud to an audible sample, 48 kHz, 128-frame
blocks, ring depth 3, target occupancy 2.

| Stage | Typical | Worst | Fixed? |
|---|---|---|---|
| Wire time of the message's last byte (10 bits @ 31250) | 0.32 ms | 0.32 ms | **fixed** |
| RX interrupt to FIFO | <0.01 ms | 0.02 ms | fixed |
| Wait for the top of the next render block | 1.33 ms | 2.67 ms | = block size |
| `mt32emu` cable-delay emulation, `MIDIDelayMode_IMMEDIATE` | 0 | 0 | see below |
| Event applied at its sample position inside the block | 0 | 0 | — |
| Block waits behind already-queued audio | 2.67 ms (1 block) | 5.33 ms (2 blocks) | = ring depth |
| I²S serialisation of that one frame | 0.02 ms | 0.02 ms | fixed |
| PCM5102A interpolation filter group delay | ~0.4 ms | ~0.4 ms | **fixed**, *verify against the datasheet* |
| **Total, from the last byte on the wire** | **≈4.7 ms** | **≈8.8 ms** | |
| Add the first two bytes if you measure from the start bit | +0.64 ms | +0.64 ms | **fixed** |
| **Total, from the first bit of the message** | **≈5.4 ms** | **≈9.4 ms** | |

**Where the slack is, and where it is not.**

- **Not negotiable: ~0.75 ms.** 0.32 ms of wire time for the last byte (0.96 ms
  for all three), ~0.4 ms in the DAC's reconstruction filter, and the I²S frame
  itself. No software decision touches these.
- **Block size buys 1.3–2.7 ms.** Halving to 64 frames halves it, at the cost of
  twice as many `render()` calls and twice the per-call overhead.
- **Ring depth buys 2.7–5.3 ms and is the same knob as robustness.** This is the
  real trade in the whole design: every block of ring is 2.67 ms of latency and
  2.67 ms of tolerance for a slow render. Set it from A's measured worst-case
  RTF, not from taste.
- **`MIDIDelayMode` is worth 0.77 ms and is free to reclaim.** `mt32emu`'s
  default is `DELAY_SHORT_MESSAGES_ONLY` (`Synth.cpp:323`), which adds
  `len × 8.192` samples at 32 kHz (`Synth.cpp:44,1109-1118`) — 24.6 samples,
  0.77 ms, for a 3-byte message — to emulate the time the message would have
  spent on a MIDI cable. **Our bytes actually spent that time on a real MIDI
  cable.** Counting it twice is not authenticity, it is a bug. Set
  `MIDIDelayMode_IMMEDIATE` and let the wire do the delaying.

**Context.** A real MT-32's own MIDI-to-note latency is commonly cited in the
10–20 ms range (its 8095 scans the MIDI buffer in a main loop); I have not
measured one and neither has anyone on this project, so treat that as folklore
until someone does. If it is even roughly right, this design is *faster than the
instrument it emulates*, and the interesting question becomes whether to add a
deliberate delay for authenticity rather than whether we can afford ours.

### 2.6 One refinement: the look-ahead knob

Events are enqueued at the synth's *current* sample position
(`getInternalRenderedSampleCount()`), because by the time we see them they are
already in the past and `mt32emu` cannot back-date. Within one drained batch
that collapses the true arrival spacing: four notes that arrived 320 µs apart
all land at the same sample, which turns an arpeggio into a chord. `mt32emu`
still separates them by its minimum one sample (`Synth.cpp:2453-2454`), so
nothing is lost, but the *timing* is.

`mtp_render_ctx.lookahead_frames` schedules events one block into the future
instead, which preserves their real relative arrival times at the cost of one
extra block (2.67 ms) of latency. Default 0. It is a knob because I do not know
which sounds better and the answer is a listening test, not an argument.

---

## 3. The MIDI path

### 3.1 Why we write the parser

`mt32emu` ships one — `MidiStreamParserImpl` (`MidiStreamParser.h:58`) — and it
is good: running status, sysex fragmented across calls, the lot. We do not use
it, for three specific reasons:

1. **It reallocates.** Its buffer starts at `SYSEX_BUFFER_SIZE` (1000 bytes,
   `globals.h:127`) and grows with `new Bit8u[]` to 32768 on the first long
   sysex (`MidiStreamParser.cpp:137`). That is a heap operation triggered by
   network— sorry, by *wire* input, which is exactly the class of thing §3 of
   PORTING.md works to eliminate.
2. **It uses `snprintf`/`sprintf`** for its error messages
   (`MidiStreamParser.cpp:193-196`), pulling stdio in behind it.
3. **Timestamps.** It has one timestamp for the whole parser
   (`MidiStreamParser.h:118-119`), set by the caller. We want per-byte arrival
   times so that §2.6's look-ahead is even possible.

Ours (`src/mtp_midi_parser.c`, `include/mtp_midi_parser.h`) is 180 lines, has
one fixed 32 KB buffer supplied by the caller, no libc but `memcpy`, and per-byte
timestamps. It is testable on a laptop, and it is.

### 3.2 What it handles

- **Running status.** A held-down arpeggio is two bytes per note, not three,
  and a game's note stream is mostly running status. After emitting a message
  the parser keeps the status byte and waits for more data bytes. System Common
  (0xF1–0xF6) *clears* running status; channel messages set it. Sysex clears it.
- **System Real Time (0xF8–0xFF) anywhere.** These single bytes are legal in the
  middle of another message, including inside a sysex, and a PC's MPU-401 will
  emit Active Sensing at 0xFE every 300 ms. The parser emits them immediately
  and leaves both the pending message and any open sysex untouched.
- **Sysex reassembly across thousands of blocks.** An MT-32 timbre bank dump is
  about 16 kB. At 31250 baud that is **5.25 seconds** of wire time and roughly
  two thousand audio blocks. The parser is fed incrementally and holds state; it
  does not care how the bytes are chopped up. The test suite exercises exactly
  this: 64 consecutive 254-byte patch sysexes with notes and Active Sensing
  interleaved, reassembled with zero errors while audio keeps flowing.
- **A sysex that never ends.** A new status byte aborts it (counted as
  `stat_sysex_aborted`) and the stream resynchronises on that status byte. This
  is what hardware does.
- **A sysex longer than the buffer.** Refused, counted
  (`stat_sysex_truncated`), and — importantly — **not forwarded**. Half a patch
  dump would fail `mt32emu`'s checksum and show "SysEx error!" on the display,
  which is a worse failure than silence. 32768 is the right cap because it is
  `mt32emu`'s own (`globals.h:119-122`, chosen because "the h/w units have only
  32K of RAM onboard").
- **Data bytes with no status**, because we powered up mid-stream. Counted as
  `stat_dropped_data` and discarded until a status byte appears.

Every one of those cases is asserted in `host/test.sh`, which passes.

### 3.3 The UART side

- **UART2 (or whichever pin group the board settles on) at 31250 baud, 8N1.**
  The T113's UARTs are 16550-compatible with a divisor; 31250 is exact from a
  24 MHz reference (divisor 48 at 16× oversampling).
- **RX interrupt per byte, plus the receive timeout interrupt.** At 3125 bytes/s
  the interrupt rate is trivial; there is no case for DMA on the MIDI input, and
  DMA would cost us the per-byte timestamp that §2.6 wants.
- **The ISR does four things:** read `RBR`, read the timestamp, push
  `{byte, t_us}` into the ring, update the write index with a release barrier.
  Nothing else. No parsing, no logging, no allocation.
- **FIFO size 256 entries** (2 KB). That is 82 ms of wire at full rate, which is
  thirty block periods — if the render loop ever falls that far behind, the
  audio has already broken and the MIDI overrun counter is not the first thing
  you will notice. The counter exists anyway, because a silent drop is the one
  failure mode that makes a MIDI module feel haunted.
- **Barriers are ours to get right.** This is the ring PORTING.md §5 says to
  write ourselves rather than borrow: `DMB ISH` between the payload store and
  the index store on the producer, and between the index load and the payload
  load on the consumer. Both ends are on core 0 in this design, so strictly the
  barriers are belt and braces — but they cost nothing and they are what makes
  the design still correct if someone later moves the ISR to core 1.

### 3.4 Handing events to the synth, without blocking the renderer

Everything happens on the render thread, so there is no synchronisation to get
wrong. The only real question is back-pressure.

`Synth::playMsg` and `playSysex` return `false` when the queue (or the
preallocated sysex ring) is full, and then call
`ReportHandler::onMIDIQueueOverflow()`, retrying in a `do/while` for as long as
it returns true (`Synth.cpp:1138-1141`). **Our handler returns false**, because
returning true would spin inside the library on the render thread with the DMA
draining underneath it. Instead:

- a refused **short message** is counted and dropped — at 1024 queue entries
  (`globals.h:117`) this cannot realistically happen;
- a refused **sysex** is stashed in a retry slot and re-offered at the top of
  the next block, and MIDI draining pauses until it is accepted. Back-pressure
  propagates to the UART FIFO, which has 82 ms of slack, rather than a patch
  dump being silently lost.

This is implemented and exercised: the bank-dump test provokes exactly one
back-pressure event against the fake engine's deliberately small sysex store and
recovers with zero drops.

---

## 4. ROMs and config on SD

### 4.1 Layout

```
/mt32.cfg
/roms/MT32_CONTROL.ROM      64 KB or 128 KB
/roms/MT32_PCM.ROM          512 KB
/roms/CM32L_CONTROL.ROM     64 KB        (optional)
/roms/CM32L_PCM.ROM         1 MB         (optional)
```

`mt32.cfg` is `key = value`, one per line, `#` comments. The keys that exist
because a decision above made them exist:

```
machine        = cm32l      # or mt32; which ROM pair to open
sample_rate    = 48000      # 48000 or 32000; picks ACCURATE or COARSE
block_frames   = 128
ring_blocks    = 3
partials       = 32
reverb         = on
renderer       = int16      # or float, for A/B listening
lookahead      = 0
gain           = 1.0
log_level      = info
```

Boot order: mount, read config (defaults if absent), load ROMs, open the synth,
start I²S, start the UART. The synth is opened before the DAC so that the first
block of audio is real rather than a ramp from silence.

### 4.2 Loading

`mtp_storage_load()` reads the whole file into a caller-owned buffer; the buffer
is wrapped in an `ArrayFile` and handed to `ROMImage::makeROMImage()`, which
identifies it by size and SHA-1 against the library's table
(`ROMInfo.h:52-54`, `ROMInfo.cpp:87-117`). See PORTING.md §4.5 for why there is
no `FileStream` anywhere.

Two honest costs at boot:

- **SHA-1 over 1 MB.** `AbstractFile::getSHA1()` hashes the file the first time
  it is asked (`File.cpp:40-61`), and `makeROMImage` asks. On a 1.2 GHz A7 that
  is tens of milliseconds — irrelevant for a device that also has to read 1 MB
  off an SD card, and worth every cycle: it is what turns "the module is silent
  and I don't know why" into "PCM ROM not recognised".
- **Peak memory during `open()` is double the PCM ROM**, because the file buffer
  and `pcmROMData` both exist (PORTING.md §7). Allocate in that order and the
  arena sizing in PORTING.md §7 already covers it.

### 4.3 When they are missing — which is the normal case

This is the first thing a user will experience, because the ROMs are Roland's
and the repository will never contain them. It must be a good experience.

| Situation | What happens |
|---|---|
| No card, or no FAT | Log it. Play nothing. Flash an LED in a distinctive pattern. **Do not hang** — a UART console still comes up, so `mt32.cfg` can be diagnosed over serial |
| Card present, `mt32.cfg` missing | Use built-in defaults, log at INFO. Not an error: a card with only ROMs on it should work |
| ROM file missing | Name the exact path that was not found, and list what *is* in `/roms`. "no such file" without the path is a bug report waiting to happen |
| ROM present but not recognised | `makeROMImage` returns NULL or a NULL `ROMInfo`. Say so, and say the likely cause: a half image. `mt32emu` knows about `Mux0`/`Mux1` and `FirstHalf`/`SecondHalf` pairs (`ROMInfo.h:37-48`) — a 32 KB control ROM is one half of a pair and needs its partner, which `ROMImage::makeROMImage(File*, File*)` (`ROMInfo.h:108`) will merge. Support that, and say which half you were given |
| `machine = cm32l` but only MT-32 ROMs present | Fall back to MT-32 with a warning rather than failing. Note the trap, **read** at `ROMInfo.h:93-96`: the lower half of the CM-32L PCM ROM *is* the MT-32 PCM ROM, so the two are aliased and `makeROMImage` always prefers the full image |
| Synth opens, ROMs fine | Log the descriptions `mt32emu` gives back (`ROMInfo::description`) so the console says "MT-32 Control v1.07 + MT-32 PCM ROM". That one line answers most support questions |

In every failing case the device still boots, still accepts MIDI, still runs the
I²S at the configured rate and still outputs silence — never a stuck DC level,
never a hang. A module that is quiet and talkative over serial is debuggable; a
module that is bricked is not.

---

## 5. What the platform interface is, and why it is C

`include/` is nine headers and about 40 functions total:

| Header | What |
|---|---|
| `mtp_platform.h` | `mtp_status`, `mtp_strerror` |
| `mtp_audio.h` | open/close/acquire/commit/queued/wait/underruns |
| `mtp_midi.h` | open/close/read/overruns/frame_errors/eof |
| `mtp_storage.h` | mount/open/size/read/close/exists/load |
| `mtp_time.h` | init/us/us64/delay_us |
| `mtp_log.h` | level + one printf-style call |
| `mtp_engine.h` | the synth seam: open/close/timebase/rendered/short/sysex/render |
| `mtp_midi_parser.h` | portable, not a platform service |
| `mtp_render.h` | portable, not a platform service |

**C, not C++.** Three reasons, in order:

1. The implementations *are* interrupt handlers and register pokes. That is the
   one place you least want the C++ runtime — static initialisation order,
   `__cxa_guard`, exception tables in an ISR's call graph.
2. Every driver source we would plausibly borrow is C: RT-Thread's
   `sunxi-hal` (`boot/vendor/rt-thread/bsp/allwinner/libraries/sunxi-hal/`),
   SyterKit's SD/MMC driver, the FreeRTOS T113 tree's DMAC. A C interface glues
   to them directly.
3. The C++ is quarantined. `host/engine_mt32emu.cpp` is the **only** C++ file in
   this port — 200 lines — and it is where every decision from PORTING.md is
   applied and commented. If the whole platform layer were C++ there would be no
   such boundary to point at.

**Small and honest.** Deliberately absent: no audio format negotiation beyond a
config struct that fails loudly, no MIDI output, no file writing, no directory
enumeration, no GPIO, no display, no callback registration, no opaque handles
where a single instance is the truth. Each of those is a real feature that
someone may want later. Adding an abstraction for a feature that does not exist
is how a 40-function interface becomes a 200-function one that nobody can
implement for a new target — which is precisely the trap §2.2 of the parent
document describes Circle falling into for mt32-pi.

The one place I *did* add a seam is `mtp_engine.h`, and it earns it: it is what
lets `host/` build and run today, with no ROMs and no `mt32emu`.

---

## 6. RT-Thread or a superloop?

**Recommendation: a superloop now. Move to RT-Thread when, and only when, a
second concurrent activity exists — and take it for its drivers, not its
scheduler.**

The reasoning, not the assertion.

### 6.1 The workload does not want a scheduler

After U-Boot, awboot or SyterKit has done DDR3, clocks and boot media, what
remains is: one hard-real-time loop feeding a DMA ring, one byte-producing
interrupt, and some SD reads that all happen before the loop starts. There is no
second deadline, nothing to prioritise against anything else, and nothing that
blocks. §2.3 of the parent document makes this argument for mt32-pi and it is
more true here, because we have deliberately dropped USB, networking and FTP —
which are precisely the activities that would have justified tasks.

Preemption in this shape is a cost: context-switch jitter on the render loop,
a stack per task to size (and PORTING.md §4.4 shows `mt32emu` can put 16–96 KB
on a stack depending on configuration), and priority inversion as a new way to
fail.

### 6.2 Neither RTOS gives us the drivers we actually need

This is the decisive part, and it is checkable against what workstream C has
already vendored under `boot/vendor/`.

- **RT-Thread.** Its Allwinner BSP is `bsp/allwinner/{d1,d1s}` — both **RISC-V
  C906**, not the T113's Cortex-A7. There is no A7 T113 BSP in the tree. The
  BSP's own driver set (`bsp/allwinner/libraries/drivers/`) is uart, spi, i2c,
  pwm, rtc, wdt, pin, lcd, sdmmc — **no I²S**.
- **FreeRTOS.** `boot/vendor/freertos-t113` (the `robots/allwinner_t113` project
  §2.6 of the parent document names) *is* a real Cortex-A7 T113 environment:
  GICv2, MMU, CCU, a DMAC driver with linked descriptors, FatFs. But its FAT is
  backed by a compiled-in RAM disk (`fw_main/diskio.c` reads a 2.4 MB FAT16
  image linked into the binary), there is **no SD driver** and **no I²S**. Its
  own README is honest about the state: single core, blocking UART, TWI
  unfinished.
- **SyterKit** has a real T113 SD/MMC driver (`drivers/mmc/sdcard.c`) and is a
  bootloader, so it reads FAT to load a payload.

So the two drivers that stand between us and a working module — **I²S with DMA**
and **SD with FAT** — are supplied by neither RTOS. Choosing RT-Thread today
buys a scheduler we do not need and costs a Cortex-A7 BSP we would have to write
regardless. That is the whole argument.

### 6.3 What RT-Thread *is* worth, and when to take it

One thing in its tree is genuinely valuable and is not a scheduler:
`bsp/allwinner/libraries/sunxi-hal/`, Allwinner's own HAL, which contains

- `hal/source/sound/platform/platforms/daudio-sun8iw20.h` and
  `sunxi-daudio.c` — the **I²S (digital audio) driver for exactly this SoC
  family**; `sun8iw20` is the D1/T113 part number;
- `hal/source/dma/` — the DMA HAL those drivers sit on;
- `hal/source/sdmmc/`, `ccmu/`, `gpio/`, `intc/`.

That is the single most useful thing in any of the vendored trees for our two
missing drivers, and it is usable as a *reference or a source* without adopting
RT-Thread's kernel. Workstream C should look at it either way.

Take the kernel when a second concurrent activity appears and not before:

- an SSD1306 display and a rotary encoder that must not stall the renderer;
- a second synth engine on core 1 (PORTING.md §5);
- SD access *while* rendering — SoundFont streaming, config reload, firmware
  update.

Each of those is a task that blocks, and a task that blocks is what a scheduler
is for.

### 6.4 The decision is cheap to reverse, by construction

That is the real reason to be relaxed about it. The entire RTOS-shaped surface
of this port is one function:

```c
mtp_status mtp_audio_wait(uint32_t timeout_us);
```

In a superloop it is `while (queued == depth) { WFI(); }`. Under RT-Thread it is
`rt_sem_take(sem, timeout)` with the DMA ISR doing `rt_sem_release`. Nothing
else in `include/` changes, `src/` does not change at all, and the render loop
does not know which it is running on. The same is true of `mtp_midi_read`: a
lock-free ring drained by the loop, or a message queue — the caller cannot tell.

Writing the interface first is what makes that true, which is why this
workstream produced `include/` before it produced an opinion.

---

## 7. What the host stub proves, and what it does not

`host/` builds and runs on this machine, today. Verified:

```
$ cd port/host && make && ./test.sh
ok   demo short msgs (8)
ok   demo sysex (1)
ok   demo underruns (0)
ok   demo wav written
ok   bank sysex count (64)
ok   bank short msgs (12)
ok   bank underruns (0)
ok   bank parsed cleanly
ok   bad sysex emitted (0)
ok   bad short msgs (2)
ok   bad underruns (0)
ok   3 orphan data bytes counted
ok   oversize sysex refused
ok   unterminated sysex aborted
ok   realtime underruns (0)
all tests passed
```

Also verified: the CMake build both with and without the real engine
(`-DMT32EMU_SOURCE_DIR=../../bench/vendor/munt/mt32emu` builds `mt32emu` out of
source — nothing under `bench/` is written to — and links
`host/engine_mt32emu.cpp`), and that the mt32emu engine fails cleanly and
informatively with no ROM paths and with ROM files of the right size but the
wrong contents.

**It proves:**

- the platform interface is implementable and is actually sufficient — the
  render loop, the parser and the timing code compile and run against it
  unchanged;
- the MIDI parser is correct on running status, on interleaved real-time bytes,
  on a 16 kB sysex reassembled across ~2000 audio blocks, and on three separate
  ways of being malformed;
- the ring discipline holds: in paced (real-time) mode the loop stays two blocks
  ahead for a second of audio with zero underruns and a minimum occupancy of 1;
- the back-pressure path works, because the fake engine's small sysex store
  provokes it and it recovers with nothing dropped;
- the `mt32emu` binding compiles, links and reports ROM failures the way §4.3
  says it should.

**It does not prove:**

- anything about speed on a Cortex-A7. That is workstream A's number and nothing
  here substitutes for it;
- anything about the T113's I²S, DMAC, UART or SD controllers, none of which
  exist in this build;
- that `mt32emu` produces correct audio here, because I have no ROMs. The engine
  refuses to open without recognised ones, by design;
- that the latency budget in §2.5 is achieved, only that its software components
  are structured to allow it. The two fixed terms — UART wire time and DAC group
  delay — want a scope on a real board.

### Running it

```
cd port/host
make                                   # fake engine, no dependencies
./build/mtp_host --seconds 4           # built-in demo -> out.wav
./build/mtp_host --midi stream.syx --realtime --block 64 --ring 4
./test.sh                              # the assertions above

# with the real engine and real ROMs:
cmake -S . -B build -DMT32EMU_SOURCE_DIR=../../bench/vendor/munt/mt32emu
cmake --build build
./build/mtp_host --engine mt32emu \
    --control-rom roms/MT32_CONTROL.ROM --pcm-rom roms/MT32_PCM.ROM \
    --midi stream.syx --wav out.wav
```

---

## 8. Open questions for the other workstreams

- **A:** the worst-case RTF at 48 kHz with `RendererType_BIT16S` and
  `AnalogOutputMode_ACCURATE` — that exact configuration, because it is the one
  this design uses and it is not the library's default. And please measure on a
  T113 board with its real DDR3: the expanded PCM ROM is 512 KB–1 MB and is
  addressed pseudo-randomly, so a large host cache will flatter the result
  (PORTING.md §7).
- **B:** the I²S pin group and whether the PCM5102A gets an MCLK or runs on its
  internal PLL; and the UART pin group for MIDI RX, given the level-shifting in
  §3 of the parent document.
- **C:** whether the payload is entered with the MMU and caches on or off, and
  who sets up the audio PLL — the bootloader or us. The answer changes §2.3's
  non-cacheable mapping from "we configure it" to "we ask for it". And a look at
  `bsp/allwinner/libraries/sunxi-hal/hal/source/sound/` before writing an I²S
  driver from the datasheet (§6.3).
