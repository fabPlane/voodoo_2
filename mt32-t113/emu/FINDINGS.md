# What the bare-metal image proves, and what it does not

Workstream E, 2026-09-18. Everything below was run in this container. Where a
number appears, the command that produced it is given, and where something was
*not* run, it says so.

---

## 1. The headline

**It boots.** `qemu-system-arm -M virt -cpu cortex-a7` starts the image at reset
with no operating system, and it brings up a console, a timer, an interrupt
controller, an audio sink and a MIDI source, runs `port/src`'s render loop
against a real periodic deadline for a second of audio, prints its counters and
exits with a status a script can assert on.

**It passes the same conformance suite as `port/host`.** All fifteen assertions
from `port/host/test.sh`, against the same MIDI vectors, plus nine more that
only a bare-metal run can make:

```
$ cd emu && make mt32emu && make test
ok   boots into SVC with MMU off, caches off, interrupts masked
ok   generic timer is present and running
ok   demo short msgs (8)             ok   bank sysex count (64)
ok   demo sysex (1)                  ok   bank short msgs (12)
ok   demo underruns (0)              ok   bank parsed cleanly
ok   demo wav written                ok   bank underruns (ring 3) (0)
ok   bad sysex emitted (0)           ok   3 orphan data bytes counted
ok   bad short msgs (2)              ok   oversize sysex refused
ok   bad underruns (0)               ok   unterminated sysex aborted
ok   realtime underruns (0)          ok   no spurious interrupts
ok   audio ring is in the non-cacheable window
ok   sink actually read every block it played
ok   deadline-driven sink consumed blocks
ok   heap high water is reported (PORTING.md 7)
ok   virtio-sound stream starts
ok   virtio periods complete
ok   virtio and timer sinks carried identical PCM (192000 bytes)
ok   QEMU wrote the device's audio to a host wav
ok   missing ROMs are named and the image still exits cleanly
ok   mt32emu opens a Synth bare metal
ok   mt32emu bank sysex (64)         ok   mt32emu bank short (12)
ok   mt32emu underruns (0)           ok   mt32emu render allocates nothing (0 B)
all tests passed
```

Thirty-three assertions, six consecutive clean runs. The suite runs under
`-icount shift=2`, which derives guest time from instruction count rather than
from the host's wall clock; without it a busy container reads as a missed audio
deadline. See § 8.7 for the one place where that is still not enough, and what
the suite does about it.

**The real `mt32emu` links, opens a `Synth`, and renders — bare metal.** Not
"links and fails cleanly on missing ROMs", which is what the brief asked for as
a success condition; the actual result is stronger. See § 6.

**Two independent cross-checks that the port layer is platform-blind.** The
1-second demo render produces **byte-identical PCM** on x86-64 (`port/host`) and
on bare-metal Cortex-A7 — 192 000 bytes, `cmp`-identical — and the same again
whether the blocks are consumed by the timer sink or by a virtio-sound device.

---

## 2. What this cannot prove, stated plainly

`boot/BRINGUP.md` § 6.4 already says it and it is worth repeating with the
emphasis the results above might otherwise erode:

- **No timing evidence comes out of this at all.** QEMU's TCG models neither
  the A7 pipeline nor its caches. The "worst render 2413 us" line the image
  prints is a property of this container's host CPU and of QEMU's translation
  cache. It is not a real-time factor and it must never be quoted as one.
  `docs/PLAN.md` § 0's gate still needs a board and real ROMs.
- **Nothing about the T113's peripherals.** There is no DRAM controller here,
  no I2S, no DMAC, no SMHC, no CCU, no pinmux. `-M virt` is a generic ARMv7-A
  machine that happens to put its RAM at 0x40000000, which is also the T113's
  DRAM base — that coincidence is what lets one linker script serve both, and
  it is the only thing the two machines have in common below the CPU.
- **Nothing about the audio being right.** The PCM the real engine produces
  here comes from a ROM image of zeroes. It proves the emulator runs; it says
  nothing about what an MT-32 sounds like.
- **The PL011 console does not transfer.** `src/console.c` is the only file in
  `emu/` that is throwaway. The T113 needs a 16550 driver at 0x02500000 + n·0x400
  (`boot/BRINGUP.md` § 6.2), which is about forty lines behind the same three
  functions.
- **The GIC base and interrupt numbering do not transfer**, though the
  programming model does: `-M virt` puts its GIC-400 at 0x08000000/0x08010000,
  the T113 at 0x03020000 (`boot/BRINGUP.md` § 4.3).

---

## 3. The entry state, measured — and it answers a question BRINGUP left open

`boot/BRINGUP.md` § 4.4 asks for "a 10-line `mrs`/`mrc` dump in the first
payload" to settle secure-versus-non-secure on real silicon. That dump is
`src/start.S` step 2 plus `print_entry_state()` in `src/main.c`, it runs before
anything is reconfigured, and it is the *same code* that will run on the board.
Under QEMU today:

```
entry cpsr          0x400001d3  mode SVC  I=1 F=1 A=1 T=0
entry sctlr         0x00c50078  MMU=0 Dcache=0 Icache=0 align=0
entry actlr/vbar    0x00000000 / 0x00000000
midr                0x410fc075  part 0xc07 r0p5
id_pfr1             0x00010001  security=0 virt=0 genTimer=1
cntfrq at entry     62500000 Hz
loader r0/r1/r2     0x00000000 / 0x00000000 / 0x00000000
running at          0x40200020
```

Three things follow.

1. **QEMU's `-kernel` loader hands over in exactly the state `bootm` does** —
   SVC, IRQ and FIQ masked, MMU off, D-cache off, I-cache off. That is
   `cleanup_before_linux()`'s row in `boot/BRINGUP.md` § 4.1. So the reset path
   this image exercises is the one the board will use, and the recommendation
   there (wrap with `mkimage`, start with `bootm`, never `go`) is what
   `make uimage` produces.
2. **`ID_PFR1.Security = 0` under QEMU**: `-M virt` with `secure=off` has no
   Security Extensions at all, so the secure/non-secure question is moot here
   and the answer QEMU gives is not the answer the T113 will give. On the board
   expect `security=1`, and this dump is what will say whether U-Boot dropped
   us to non-secure. **Do not skip this step on hardware.**
3. **`CNTFRQ` is 62.5 MHz here and will be 24 MHz on the T113.** Nothing in
   `src/timer.c` has either number compiled in; both are derived from CNTFRQ at
   init, with a loud fallback to 24 MHz if a loader left it at zero.

One deviation from `port/include/mtp_time.h` worth recording: that header says
CNTVCT. `src/timer.c` uses **CNTPCT and the PL1 physical timer** (PPI 30)
instead. In a bare-metal PL1 payload with no hypervisor, CNTVOFF is zero and
the two counters are the same value; the physical timer is the one whose
comparator and PPI a PL1 payload can actually own. The header's argument for
the generic timer over a SoC peripheral is unaffected and was correct.

---

## 4. The MMU decision, and a measurement that argues both ways

**Decision: the image sets up a flat 1:1 mapping and turns the caches on, and
`make MMU=0` builds the other way.** Both configurations pass the whole test
suite.

The mapping is 1 MB sections (`src/mmu.c`): DRAM as Normal write-back
write-allocate shareable, everything below 0x40000000 as Device-XN, everything
above unmapped so a wild pointer faults into the abort handler rather than
wandering, and one 1 MB window — the `.dma` section from `link.ld` — retyped as
**Normal Non-cacheable**, which is where the audio ring and the virtqueues live.
That window exists because `port/DESIGN.md` § 2.3 requires it for the I2S DMA
ring on the board. Nothing here can prove the attribute is *sufficient*, because
TCG models no caches; what it proves is that the descriptor is built, the
linker puts the ring in it, and uncached `int16` stores at 48 kHz cost nothing
measurable.

The measurement, real engine, `--midi bank --seconds 2`:

| | worst render | underruns |
|---|---|---|
| `make mt32emu` (MMU + caches on) | 3262 µs | 0 |
| `make MMU=0 mt32emu` | 4807 µs | 0 |

**Read that with care.** The 47 % difference is QEMU's soft-MMU bookkeeping
changing, not caches: with the MMU off, TCG takes a different address-translation
path. It is *not* a preview of what caches-off costs on an A7, where it would be
far worse — an order of magnitude is the usual figure, because every load goes
to DDR3. The honest summary is that **this experiment cannot measure the thing
it looks like it measures**, and that is itself the reason the image says
`all timings are meaningless` on its own banner when built `MMU=0`.

What the MMU-on build *does* earn: it proves the payload can go from the `bootm`
handover state to a live MMU with caches on, from reset, without faulting — which
is a real bring-up step and one that is much easier to debug here than on a board.

---

## 5. Toolchain: what a bare-metal link actually costs

`arm-none-eabi-gcc` is **not** in this container, so there is no newlib and no
newlib libstdc++. The image is built with `arm-linux-gnueabihf-gcc 13.3` used as
a bare-metal compiler (`-nostdlib -nostartfiles`), with `libm.a` and `libgcc.a`
pulled in by hand and everything else written here. That is a deliberate choice
— it keeps the ABI identical to `bench/`'s cross build, which
`boot/BRINGUP.md` § 6.1 insists on — but it cost five specific things that are
worth writing down, because four of them will bite the T113 build too.

1. **glibc's `libm.a` declares `errno` as `__thread`.** Define a plain `int
   errno` and the link fails with `TLS reference in libm.a(math_err.o)
   mismatches non-TLS definition ... error adding symbols: bad value`, which is
   not a helpful message. The fix is a real TLS segment: `.tdata`/`.tbss` in
   `link.ld` with an explicit size reservation (`.tbss` does not advance the
   location counter), and `__aeabi_read_tp` in assembly returning
   `__tls_start - 8` — ARM variant 1 puts an 8-byte TCB in front of the block.
   Twenty lines in total, and `__aeabi_read_tp` must be assembly because AEABI
   requires it to preserve r1–r3.
2. **Ubuntu's GCC enables `_FORTIFY_SOURCE=2` at `-O2`.** Every `printf`,
   `sprintf`, `vsnprintf` and `vfprintf` call in `mt32emu` is rewritten to
   `__printf_chk`, `__sprintf_chk`, `__vsnprintf_chk`, `__vfprintf_chk` — glibc-only
   symbols, four undefined references with no hint as to the cause. Build with
   `-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0`.
3. **`-ffreestanding` must not be passed to the C++ translation units.**
   libstdc++'s `<cstdlib>` is guarded by `_GLIBCXX_HOSTED`, and `-ffreestanding`
   makes it withhold `std::div` and `std::div_t`, which `mt32emu`'s
   `Display.cpp:227` uses. The error (`'div_t' is not a member of 'std'`) points
   nowhere near the flag. `emu/Makefile` keeps `-ffreestanding` on the C side
   only.
4. **`-fno-use-cxa-atexit` is a trap**, because it makes static destructors
   register with `atexit` instead of `__cxa_atexit`, and `atexit` is one more
   symbol to invent for no benefit. Drop it and implement `__cxa_atexit` as the
   one-line stub `port/PORTING.md` § 4.1 already specifies.
5. **`-fno-threadsafe-statics` really does remove `__cxa_guard_*`.** Confirmed:
   neither symbol appears in the link. `src/cxxrt.cpp` deliberately does not
   define them, so a build that forgets the flag fails loudly instead of
   silently acquiring a guard variable in `Tables::getInstance()`.

**Total retarget surface**, in `src/retarget.c`, `src/printf.c` and
`src/cxxrt.cpp`: `_sbrk`, `_write`, five `-ENOSYS` fd stubs, a bump allocator
(`malloc`/`calloc`/`realloc`/`free`), fifteen `string.h` functions, `rand`,
`abs`, `labs`, `div`, `errno`/`__errno_location`, `raise`, `abort`, `exit`,
`__assert_fail`, `__stack_chk_*`, `stdout`/`stderr`/`stdin` as data symbols, a
printf family, and six C++ runtime symbols. About 600 lines including comments.
`port/PORTING.md` § 8's closing claim — "twenty external symbols, better than a
bare-metal port has any right to expect" — survives contact with reality.

---

## 6. `mt32emu` bare metal: it links, it opens, it renders

`make mt32emu` builds Munt 2.8.3 out of source (nothing under `bench/` is
written to) with `port/PORTING.md` § 4.2's flags, and links it into the image.

### Static footprint

| | text | data | bss | raw `.bin` |
|---|---|---|---|---|
| Image without `mt32emu` | 97 824 | 436 | 13 798 284 | **98 260 B** |
| Image with `mt32emu` | 190 672 | 3 840 | 13 801 420 | **194 512 B** |
| `mt32emu`'s contribution | **+92 848** | **+3 404** | +3 136 | +96 252 B |

(`bss` is dominated by reservations, not code: the 12 MB heap, the 81 KB of
stacks, the 16 KB page table and the 1 MB uncached window are all `NOLOAD`
sections the loader never has to carry. The number that matters for an SD-booted
payload is the raw `.bin`: **190 KiB with the synthesiser in it.**)

### Runtime footprint, measured on ARM

```
$ make mt32emu && make run ARGS="--engine mt32emu-fakerom --midi bank --seconds 2"
heap after setup    1608728 B
heap high water     1608728 B of 12582912 B
heap grown by run   0 B
```

**1 608 728 bytes = 1 571 KiB** for MT-32, `RendererType_BIT16S`,
`AnalogOutputMode_ACCURATE`, 32 partials, reverb preallocated, 64 KB sysex ring.
`port/PORTING.md` § 7 predicted **1 525 KiB** for that configuration from an
x86-64 measurement, and expected ARM to come in slightly *under* it. It came in
3 % over, and the difference is this image's bump allocator: it rounds every
request to 8 bytes and never reuses anything, so the 589 824-byte PCM staging
buffer is not recovered. The prediction was good; the 4 MB arena § 7 recommends
is right with room to spare.

**`heap grown by run` is 0 bytes**, across a run that delivered 64 sysex patch
dumps into the synth. That is `port/PORTING.md` § 3's central measured claim —
zero heap operations on the render path, given `preallocateReverbMemory(true)`
and `configureMIDIEventQueueSysexStorage()` — independently confirmed on ARM,
bare metal, with a different allocator underneath. It is now a `make test`
assertion, so it cannot silently regress.

### How it is opened without ROMs

`port/host/engine_mt32emu.cpp` loads ROMs through `mtp_storage` and lets
`makeROMImage()` identify them by size and SHA-1. With no ROMs that path can
only ever reach "no such file" — which `emu/` does test, and which behaves as
`port/DESIGN.md` § 4.3 specifies:

```
[E] control ROM 'roms/MT32_CONTROL.ROM': no such file
[E] engine open: I/O error
--- run ---
engine              mt32emu (FAILED TO OPEN)
--- end ---
```

The console stays up, nothing hangs, the image exits cleanly. But that leaves
the C++ runtime, the arena, the static constructors and the LA32 render path
untested, so `emu/` also carries `src/engine_mt32emu_fake_roms.cpp`: Munt's own
`src/test/FakeROMs.cpp` fixture, reduced and rewritten against the public API,
which fabricates ROM images the library accepts by handing `ArrayFile` the
*expected* SHA-1 (`File.h:60`) instead of hashing the fabrication. It needs the
library compiled with `-DMT32EMU_WITH_TESTING` for `Test::getControlROMMap`
(`Synth.cpp:2912-2922`), which `emu/Makefile` does.

With it, the real emulator opens and renders:

```
[I] fake ROMs: 'ctrl_mt32_1_07' (65536 B) + 'pcm_mt32' (524288 B)
[I] ROMs: MT-32 Control v1.07 + MT-32 PCM ROM
[I] engine: mt32emu 2.8.3, 32 partials, 48000 Hz out
blocks committed    376  (1.002 s of audio)
sysex messages      64
underruns           0
```

The audio is meaningless — the PCM ROM is zeroes. Everything else is real: the
C++ runtime, `operator new` against the bump arena, `.init_array`,
`Tables::getInstance()`, 1.5 MiB of allocations, `Synth::playSysex` on 64
16 kB-total patch dumps, and `Synth::render(Bit16s*, 128)` 376 times without
missing a deadline.

---

## 7. The virtio-sound sink, and one transferable result

The default sink is a timer interrupt pretending to be a DMA engine. The second
is a real device model — `src/virtio.c` (virtio-mmio transport and split
virtqueues, 297 lines) and `src/virtio_snd.c` (a playback stream). It
works:

```
$ make virtio
[I] virtio-snd: transport slot 31 at 0xa003e00, irq 79
[I] virtio-snd: 0 jacks, 2 streams, 0 chmaps
[I] virtio-snd: stream 0 is output, 1..2 channels
[I] virtio-snd: stream 0 started, period 512 B, buffer 3072 B
```

and QEMU's `-audiodev wav` writes the device's output to a host file. (That run
is `make virtio`, ring depth 6: 370 periods completed, `max 6 completed per irq`
— the batching § 7's table is about — and 2 device-idle events.)

**Practical note that cost an hour:** QEMU's virtio-mmio transport defaults to
the **legacy** interface and reports Version 1, whose queue setup is QueuePFN
plus a guest page-size register rather than the split
descriptor/driver/device addresses a modern driver writes. Pass
`-global virtio-mmio.force-legacy=false`. The driver detects the legacy case
and prints that flag rather than failing obscurely.

**The transferable result.** The number of device-idle events — the sink running
out of data, the virtio analogue of an I2S ring going dry — against ring depth,
demo stream, one second, 128-frame blocks:

| ring depth | audio in flight | device-idle events |
|---|---|---|
| 3 (`port/DESIGN.md` § 2.2's choice) | 8.0 ms | 124 |
| 4 | 10.7 ms | 83 |
| 6 | 16.0 ms | 2 |
| 8 | 21.3 ms | 1 |

`min ring occupancy` stayed at 1 in every one of those runs: **the renderer was
never behind.** The starvation is entirely on the consumer side, and its cause
is visible in the image's own `max N completed per irq` counter — QEMU's audio
subsystem services its backends on a ~10 ms timer and takes several 2.67 ms
periods in one go, so a ring holding 8 ms of audio cannot cover one service
interval.

That is a constraint `port/DESIGN.md` § 2.2 does not state. Its ring depth is
derived from the *renderer's* slack — how long a bad `render()` can overrun
before the DMA runs dry — and that derivation is right as far as it goes. The
second, independent constraint is the **consumer's service granularity**: the
ring must hold at least one service interval of the thing draining it, or it
starves no matter how far ahead the renderer is. On the T113 this constraint is
satisfied trivially, because the DMAC raises a completion interrupt per
descriptor and its granularity is one block — which is exactly why depth 3 is
defensible there and is *not* defensible against QEMU's audio backend. It is
worth a sentence in DESIGN.md anyway, because the next consumer might not be a
DMAC (an RTOS driver with a work queue, say, or a USB isochronous endpoint).

---

## 8. Things in `port/` and `boot/` that want changing

Nothing outside `emu/` was modified — with one caveat worth stating: running
`port/host`'s own suite for the PCM comparison in § 1 regenerated
`port/host/build/`, which is build output and is already in `.gitignore`. No
source file outside `emu/` was touched.

These are the changes this work says are needed.

### 8.1 `port/include/mtp_audio.h`: the start-of-stream underrun

The header says "Opens the sink and starts the DMA. Until the first block is
committed the ring holds silence, so opening early is safe." An implementation
that takes that literally — start the consumer in `open()` — counts an underrun
for every consumer period between `open()` and the first `commit()`, because the
ring genuinely is empty. That is an artefact of the start-up order, not a
rendering failure, and it makes the counter that the whole design hangs on
non-zero on a perfectly healthy boot. (`emu/` hit exactly this: 1 underrun on a
clean run.)

The fix here was to start the consumer clock on the **first commit**, which is
also what a real driver should do: prime the ring, then start the DMA. **Proposed
change:** say so in `mtp_audio.h` — "the implementation must not begin consuming,
or must not count underruns, before the first `commit()`" — so that the T113
driver does not have to rediscover it. One sentence, no API change.

### 8.2 `port/host/engine_fake.c` is in the wrong directory

It is not host-specific in any way: it has no POSIX dependency, it allocates
once at open, and `emu/` compiles it unmodified for bare metal. It is the
portable reference engine that lets *any* platform implementation be exercised
before a synthesiser exists, which is precisely the role `docs/PLAN.md` § 0.5
puts above the seam. **Proposed change:** move it to `port/src/engine_fake.c`.
`emu/Makefile` compiles it from `port/host/` today and will need one path edited.

### 8.3 `port/PORTING.md` § 4.1's retarget table is missing entries

Measured from the undefined-symbol dump of an actual bare-metal link against
Munt 2.8.3. Add:

| Symbol | Where from | Note |
|---|---|---|
| `abs` | `TVP.cpp:88` | the table lists `div` but not this |
| `stdout` | `Synth.cpp:391-398`, as a **data** symbol | needed even though the default `ReportHandler` is overridden, exactly as § 4.7 predicts |
| `vfprintf` | same | § 4.7 mentions `vprintf`; the call is `vfprintf(stdout, …)` |
| `sprintf` | `Part.cpp` constructor | § 4.7 mentions `MidiStreamParser.cpp` only |
| `errno` as **TLS** | glibc `libm.a` | with `__aeabi_read_tp`; see § 5.1 above. Not needed with newlib |
| `__printf_chk` family | `_FORTIFY_SOURCE` | see § 5.2 above — or disable it, which is the right answer |

And a note on § 4.2's flags: **do not add `-ffreestanding`** to the C++ flags
(§ 5.3 above), and drop `-fno-use-cxa-atexit` if it is ever considered.

### 8.4 `port/PORTING.md` § 4.2 asks for a patch to `bench/`

It says to remove `src/FileStream.cpp` from `libmt32emu_CPP_SOURCES`
(`CMakeLists.txt:154`). Upstream offers no option for that, so following the
instruction means editing the vendored tree — which conflicts with every
workstream's rule that `bench/vendor` is read-only. `emu/` solves it by owning
the file list instead (`tools/prepare-mt32emu.sh` stages the headers, the
Makefile names the nineteen translation units). **Proposed change:** say that in
§ 4.5, because "patch upstream" and "never modify the vendored clone" cannot
both be the policy. Worth noting that including `FileStream.h` is harmless — it
is only compiling `FileStream.cpp` that drags iostream in.

### 8.5 `port/src/mtp_render.c`: a stalled sink ends the run

`mtp_render_run()` breaks out of its loop when `mtp_audio_wait()` times out,
logging "audio sink stalled". On a host harness that is right. On the device it
means a transient sink problem silently ends audio for ever, with one line on a
console nobody is watching — against the spirit of `port/DESIGN.md` § 4.3's "never
hang, always be debuggable". It did not fire in any `emu/` run; flagging it as a
design question, not a bug. `worst_block_us` is also collected and never printed
by any harness.

### 8.6 `port/host`'s underrun assertions are structurally vacuous

`port/host/host_audio_wav.c:107`: outside `--realtime`, `advance()` sets
`g_queued = 0` unconditionally, so the ring can never be found empty and
`mtp_audio_underruns()` can never be non-zero. Three of `port/host/test.sh`'s
four "underruns (0)" assertions — cases 1, 2 and 3 — are therefore checking
nothing. Only case 4, with `--realtime`, tests anything, and even there the
"clock" is `clock_gettime` polled by the same thread that renders, so a late
render cannot be observed by an independent observer.

That is not a criticism of the harness: it was built to exercise structure
before silicon existed, and it does. But it means **`emu/` is the first place
the underrun counter is a measurement**, because here the consumer is an
interrupt from a free-running timer that fires whether or not the renderer is
ready. `emu/test.sh` says so in its own comments so that nobody reads the two
suites' matching "underruns (0)" lines as equally strong.

**Proposed change:** either make the host's non-realtime sink advance the
virtual cursor at the block rate against `mtp_time_us()` (a few lines, and it
would make the assertions mean something), or say in `test.sh` that those three
lines are structural checks rather than timing ones.

### 8.7 A real deadline test needs a way to tell jitter from failure

Running `emu/test.sh` repeatedly on a loaded container fails perhaps one run in
five, on whichever case has the least audio in flight. The cause is always the
same: the host descheduled QEMU for longer than the ring covers, the guest's
generic timer interrupt arrived late, and the sink correctly reported that it
had nothing to play. The render loop did nothing wrong.

Two things were needed to make the suite deterministic, and both are worth
carrying into whatever tests the T113 build one day has.

1. **`-icount shift=2`**, so that guest time is derived from instruction count.
   This removes most of the jitter and makes the numbers repeatable. It also
   makes the reported render times obviously fictional, which is a feature: at
   shift 0, 1 and 2 the real engine's worst render came out as 838, 1676 and
   3352 µs — exactly doubling — which is as clear a demonstration as one could
   want that these are instruction counts and not microseconds.
2. **A test that distinguishes the two failures.** `expect_no_underruns()` in
   `emu/test.sh` fails on an underrun only when the sink interrupt was *on
   time*; if the interrupt itself was later than `(ring − 1) × block_period`,
   it says so and passes, because the deadline was moved rather than missed.
   The image prints the worst tick-to-service latency for exactly this purpose.

The bank case is also split in two as a result: the parser contract is asserted
at `port/host`'s own `--block 64 --ring 2` (2.67 ms of audio in flight), and the
underrun contract at the 128-frame, ring-3 numbers `port/DESIGN.md` § 2.2
actually specifies (5.3 ms). Asserting a 2.67 ms deadline inside a container is
asserting how busy the container is.

### 8.8 `boot/BRINGUP.md` § 4.4's open question is now half-answered

The § 3 dump above is the "10-line test" that section asks for, written and
working. It answers the question for QEMU (no Security Extensions at all) and
is ready to answer it on silicon unchanged. § 4.4 can be updated to point at
`emu/src/start.S` and `emu/src/main.c:print_entry_state()` rather than
describing a test to be written.

---

## 9. What was not run

- **`make uimage`.** `mkimage` is not installed in this container (it is in
  `u-boot-tools`) and no packages were installed. The target is written and the
  addresses match `boot/BRINGUP.md` § 4.2, but it has never been executed.
- **Anything with real ROMs**, for the obvious reason.
- **`-audiodev alsa`/`oss`.** `make virtio` uses the `wav` backend; the others
  are listed by this QEMU but there is no sound card in the container.
- **A second core.** `src/start.S` parks every core but MPIDR.Aff0 == 0, and
  `-M virt` is started with one CPU, so the parking loop has never actually
  executed. It is four instructions.
- **`-icount shift=N,sleep=off`**, which would have made guest time fully
  independent of the host, **hangs**: with the render loop in `WFI` no
  instructions retire, so the virtual clock stops and the timer deadline never
  arrives. Killed after two minutes with no output. `sleep=on` (the default)
  works and is what `make test` uses.

---

## 10. The first thing to try on real hardware

In this order, and the first step is worth its own session.

1. **Build `make uimage`, load it with `xfel` or over U-Boot, and start it with
   `bootm` — and read the entry-state dump.** Change two constants first:
   `PL011_BASE` in `src/console.c` becomes the T113's 16550 at 0x02500000 (and
   the driver becomes a 16550 — about forty lines behind the same three
   functions), and `GICD_BASE`/`GICC_BASE` in `src/gic.c` become 0x03020000 and
   0x03021000. Nothing else should need to change to get a console.

   That single boot answers, in one screen: whether U-Boot handed over secure or
   non-secure (`id_pfr1`, `cpsr`), whether `bootm` really left the MMU and caches
   off as `boot/BRINGUP.md` § 4.1 says, whether `CNTFRQ` is the 24 MHz everyone
   assumes, and whether the image runs at the address it was linked for. Those
   are four assumptions the whole port rests on, and they cost one boot to check.

2. **`--engine fake --midi bank --sink timer`, with the tick driven by the
   generic timer and nothing else connected.** If that reports zero underruns on
   silicon, the render loop, the parser, the ring discipline and the interrupt
   path are all working on the real part, before a single audio peripheral has
   been touched. If it does not, the counters say which of them broke — and
   because the identical assertions pass here and under `qemu-user` and on the
   host, the difference is a driver.

3. **Then, and only then, the I2S DMA ring**, replacing `emu_audio.c`'s timer
   tick with the DMAC's per-descriptor completion interrupt. The ring
   discipline, the non-cacheable mapping, the acquire/commit protocol and the
   underrun accounting above it are already written and already tested; what is
   new is the descriptor list and the clock tree. Keep `emu/`'s counters and
   compare the numbers directly.

4. **`--engine mt32emu` with real ROM dumps**, which is `docs/PLAN.md` § 0's gate.
   Everything needed to run it is in place: the link works, the arena is sized,
   the render path allocates nothing, and the ROM failure path is friendly. The
   only missing inputs are Roland's ROMs and a board.
