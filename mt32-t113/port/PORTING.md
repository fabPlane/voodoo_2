# What `mt32emu` needs from a platform

Workstream D. Status: 2026-09-17. This document is the *analysis*; the design
that follows from it is in [DESIGN.md](DESIGN.md).

Every claim below is one of three things and is labelled as such:

- **Read** — taken from the source, cited `file:line`.
- **Measured** — a number I produced on this machine; the method is stated so
  you can redo it.
- **Inferred** — my reasoning from the above. Argue with these.

Source read: `../bench/vendor/munt` at `6e7c01f` (workstream A's clone; read
only, never modified). Library version 2.8.x. All `file:line` citations are
relative to `bench/vendor/munt/mt32emu/`.

---

## 1. The short answer

`mt32emu` is the most portable large C++ library I have read in a while. It is
C++98, it uses no STL containers, no strings, no threads, no exceptions and no
RTTI, and — once configured correctly — it does not allocate a single byte
while rendering. The awkward parts are not in the language; they are four
specific things:

1. **`<fstream>`.** One file, `FileStream.cpp/h`, pulls in the whole iostream
   and locale machinery. It is avoidable (§4.5) and avoiding it is the single
   biggest win in a bare-metal link.
2. **Allocation on a reverb mode change.** Not on the render path *unless* a
   game sends a reverb sysex, which games do — and then it allocates on the
   render thread. One API call fixes it. Measured, §3.
3. **The MIDI event queue is `volatile`-only, with no memory barriers.** Fine
   on x86, **not** fine between two Cortex-A7 cores. §5.
4. **Stack.** The renderer can put 16 KB on the stack in one configuration and
   96 KB in another. The configuration we want puts zero there, but you have to
   choose it deliberately. §4.4.

None of these is a blocker. All four are decided by build flags and three lines
of setup code.

---

## 2. Language and library surface

### 2.1 C++ revision: C++98 is enough

**Read.** Upstream compiles the library with `-ansi -pedantic` by default when
the compiler is GCC or Clang (`CMakeLists.txt:81`, `CMakeLists.txt:355-357`).
`-ansi` on a C++ compiler means `-std=c++98`.

**Measured.** Every source file of the synthesis core compiles clean under
`g++ -std=c++98 -pedantic-errors -fno-exceptions -fno-rtti -fno-stack-protector -O2`
— nineteen translation units, zero errors, zero warnings escalated.

That matters because it means **any** C++ toolchain works. You are not waiting
on a libstdc++ that supports C++17 on an arm-none-eabi cross-compiler; C++98
support has been universal since about 2005.

### 2.2 The STL surface: there isn't one

**Read.** The complete set of system headers included by the library, outside
`src/test/`:

| Header | Included by |
|---|---|
| `<cstddef>` | 13 files — for `size_t`, `NULL` |
| `<cstring>` | 10 files — `memcpy`, `memset`, `strcmp` |
| `<cmath>` | `mmath.h:21` and three resampler files |
| `<cstdio>` | `Synth.cpp:18`, `Part.cpp:18`, `MidiStreamParser.cpp:18` |
| `<cstdlib>` | `TVP.cpp:18` (`rand`), `Display.cpp:18` (`div`) |
| `<cstdarg>` | `Synth.h:21` — `printDebug(const char*, va_list)` |
| `<clocale>` | `FileStream.cpp:19` — **avoidable, see §4.5** |
| `<fstream>` | `FileStream.h:94` — **avoidable, see §4.5** |
| `<iostream>` | `srchelper/srctools/src/SincResampler.cpp:20` — **avoidable, §4.6** |

No `<vector>`, no `<string>`, no `<map>`, no `<memory>`, no `<algorithm>`, no
`<atomic>`, no `<thread>`, no `<mutex>`. The library hand-rolls its containers:
the MIDI queue is a plain array ring (`MidiEventQueue.h:65`), the partial table
is `new Partial*[n]` (`PartialManager.cpp:39`).

**Measured** — the definitive version of this claim. Linking the static library
and asking for its undefined symbols, with `-fno-exceptions -fno-rtti` and the
resampler and `FileStream` excluded, the *entire* external surface is:

```
C++ ABI : operator new, operator new[], operator delete, operator delete[],
          __cxa_atexit, __dso_handle,
          __cxa_guard_acquire, __cxa_guard_release
libm    : sin, cos, exp, log, log10, fmod          (all double precision)
libc    : memcpy, memset, strcmp, rand, div,
          stdout, printf, vfprintf, sprintf
```

Twenty symbols. That is the whole platform contract. Anything a bare-metal or
RT-Thread target has to provide is on that list.

(Method: `nm -u` over an `ar` of the 19 core objects built with the flags in
§2.1, demangled, with `MT32Emu::`/`SRCTools::` internal references filtered.
`__memcpy_chk`/`__printf_chk` appear instead of the plain names on Ubuntu
because of `_FORTIFY_SOURCE`; they are the same functions.)

### 2.3 Exceptions: can be disabled

**Read.** Grepping the library for `throw`, `try {` and `catch (` outside
`src/test/` returns exactly one hit — `struct PCMWaveEntry {` at
`Structures.h:239`, which matches only because `try {` is a substring of
`Entry {`. There is no `throw`, no `try` and no `catch` anywhere in `mt32emu`.

**Measured.** With `-fno-exceptions`, `_Unwind_Resume` and
`__gxx_personality_v0` vanish from the undefined-symbol list (§2.2), which is
the proof that no unwinding machinery is left behind.

**Consequence.** `operator new` must then not be libstdc++'s throwing one. On
bare metal you supply your own (§4.3) and it cannot fail, because nothing
allocates after `open()` (§3).

### 2.4 RTTI: can be disabled, if you drop the resampler

**Read.** One `dynamic_cast` in the whole library:
`srchelper/srctools/src/ResamplerModel.cpp:122`. It is in the *internal sample
rate converter*, which is an optional component
(`CMakeLists.txt:78`, `libmt32emu_WITH_INTERNAL_RESAMPLER`).

**Measured.** `-fno-rtti` compiles the entire synthesis core and fails on
exactly that one file:

```
ResamplerModel.cpp:122:46: error: 'dynamic_cast' not permitted with '-fno-rtti'
```

**Consequence.** We do not need the resampler at all, because
`AnalogOutputMode_ACCURATE` already outputs exactly 48 kHz (§6, and DESIGN.md
§2). So: `-fno-rtti` is on, `srchelper/` is out of the build, and
`__dynamic_cast`, `__cxxabiv1::__si_class_type_info` and friends never enter
the link.

### 2.5 Threads: none

**Read.** No `<thread>`, no pthread symbols, no thread creation anywhere. The
library's only concurrency *affordance* is documented, not used:

- `MidiEventQueue.h:32-34`: "safe to use either in a single thread environment
  or when there are only two threads — one performs only reading and one
  performs only writing."
- `Synth.h:379`: enqueueing MIDI needs "no synchronisation … with the rendering
  thread".
- `Synth.h:394-397`: the `*Now()` variants **do** need explicit synchronisation
  with the renderer, and warn they "may have no effect while the synth is
  aborting a poly". Do not use them from a second context.

**Inferred.** The library is single-threaded; concurrency is entirely the
host's business. See §5 for why that SPSC queue is not safe across two A7
cores.

---

## 3. Does it allocate while rendering?

This is the question that decides whether a bare-metal heap is acceptable, so I
measured it rather than reasoning about it.

**Method.** Global `operator new`/`delete` replaced with a counting allocator,
linked against the library built with its own test fixtures
(`src/test/FakeROMs.cpp`, which fabricates ROM images the library accepts), a
`Synth` opened at 32 partials and `AnalogOutputMode_ACCURATE`, then two seconds
of audio rendered in 256-frame blocks with notes on and off throughout and
**three reverb-mode changes** injected as system-area sysex mid-render.

**Measured**, bytes, MT-32 v1.07 ROM set, host x86-64:

| Renderer | `preallocateReverbMemory` | allocs during `open()` | allocs during 2 s render | peak growth |
|---|---|---|---|---|
| BIT16S | off (default) | 250 | **35** | +10,202 B |
| BIT16S | **on** | 285 | **0** | 0 |
| FLOAT | off (default) | 250 | **35** | +20,612 B |
| FLOAT | **on** | 285 | **0** | 0 |

Identical pattern for the CM-32L set.

**Read**, which explains it. `Synth::refreshSystemReverbParameters`
(`Synth.cpp:2010-2023`) switches `BReverbModel` when a sysex changes the reverb
mode, and unless `extensions.preallocatedReverbMemory` is set it calls
`close()` on the old model and `open()` on the new one — which `delete[]`s and
`new[]`s every comb and allpass delay line (`BReverbModel.cpp:287`, `:465-476`).
That code runs inside `render()`, because `render()` is where queued MIDI
events are processed (`Synth.cpp:2464-2474`).

The second allocation source is the MIDI queue's sysex storage. By default each
sysex event gets `new Bit8u[len]` (`Synth.cpp:2162-2163`), freed lazily when a
later event displaces it — i.e. on the *producer* side, but still a heap
operation tied to MIDI traffic. `configureMIDIEventQueueSysexStorage(n)`
switches it to a preallocated ring (`Synth.cpp:2178-2236`, and the header says
so at `Synth.h:359-368`).

### The rule

```cpp
synth.preallocateReverbMemory(true);                     // Synth.h:455
synth.configureMIDIEventQueueSysexStorage(64 * 1024);    // Synth.h:368
```

With those two calls, **the render path performs zero heap operations, ever**.
That is measured, not hoped for. It also means a bump allocator with no `free`
is a legitimate implementation of `operator new` on this target (§4.3).

---

## 4. Consequences for newlib + libstdc++ on bare metal or RT-Thread

### 4.1 What you must retarget

| Symbol | Why | What to do |
|---|---|---|
| `_sbrk` | newlib `malloc` | Hand it a fixed DDR3 arena. Sized from §7; nothing grows it after boot |
| `__malloc_lock` / `__malloc_unlock` | newlib thread safety | No-op in a superloop; a mutex under RT-Thread. Nothing allocates in an ISR |
| `operator new/new[]/delete/delete[]` | §2.2 | Map to the arena. With `-fno-exceptions` they must not throw — see §4.3 |
| `__cxa_atexit`, `__dso_handle` | static destructors | One-line stubs. The device never exits |
| `__cxa_guard_acquire/release` | function-local static | Needed by `Tables::getInstance()` (`Tables.h:29`). Single-threaded no-ops are correct *provided* the first call happens before a second core is started |
| `_write` | `printf`/`vfprintf` reaching `stdout` | Route to the debug UART. See §4.7 |
| `_read`,`_close`,`_lseek`,`_fstat`,`_isatty` | newlib stdio link | Return `-ENOSYS` stubs |
| `sin cos exp log log10 fmod` | `mmath.h`, `Tables.cpp` | newlib libm. All **double**; see §4.8 |
| `rand` | `TVP.cpp:330`, on the render path | Supply your own. See §4.9 |
| `div` | `Display.cpp:227` | Not on the hot path; newlib's is fine |

### 4.2 Build flags for the library

```
-std=c++98 -fno-exceptions -fno-rtti -fno-threadsafe-statics
-ffunction-sections -fdata-sections -O2
-marm -mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard
```

`-fno-threadsafe-statics` removes `__cxa_guard_*` outright, which is correct
here: the only function-local static is `Tables::getInstance()`, and the header
itself says it "should be avoided on the critical path" (`Tables.h:28`), so
touch it once at init.

And in the library's own CMake configuration:

```
-DBUILD_SHARED_LIBS=OFF
-Dlibmt32emu_C_INTERFACE=OFF       # drops c_interface.cpp, which uses FileStream
-Dlibmt32emu_WITH_INTERNAL_RESAMPLER=OFF
-DBUILD_TESTING=OFF
```

plus removing `src/FileStream.cpp` from `libmt32emu_CPP_SOURCES`
(`CMakeLists.txt:154`). See §4.5.

### 4.3 `operator new` with exceptions off

```cpp
// The arena is sized at build time from §7 and never grows.
void *operator new(size_t n)      { return mtp_arena_alloc(n); }  // panics on exhaustion
void *operator new[](size_t n)    { return mtp_arena_alloc(n); }
void  operator delete(void *)     {}   // nothing is freed before reset
void  operator delete[](void *)   {}
void  operator delete(void *, size_t)   {}
void  operator delete[](void *, size_t) {}
```

A bump allocator is defensible *because* of §3: all 285 allocations happen
during `Synth::open()`, none during rendering, and the only thing that ever
frees them is `Synth::close()` — which on this device means "reboot".
`close()` + re-`open()` (to switch between MT-32 and CM-32L ROM sets at
runtime, say) would leak the whole arena, so if you want that feature, use a
real allocator or two arenas and reset one. Say which you chose.

### 4.4 Stack

**Read.** Three places put large arrays on the stack:

| Site | Size (BIT16S) | Size (float) | When |
|---|---|---|---|
| `Synth.cpp:2364` `renderingBuffer[MAX_SAMPLES_PER_RUN<<1]` | 16 KB | 32 KB | **only** when the output type differs from the renderer type |
| `Synth.cpp:2486-2488` six `cnv*` buffers | 48 KB | 96 KB | `renderStreams()` with a mismatched output type |
| `SampleRateConverter.cpp:107` `floatBuffer[2*4096]` | — | 32 KB | only with the sample rate converter |

`MAX_SAMPLES_PER_RUN` is 4096 (`globals.h:107`) and is a compile-time constant,
not a function of the length you pass to `render()`.

**Inferred, and it is the reason for a specific configuration choice.** If you
select `RendererType_BIT16S` and call `render(Bit16s*, len)`, you take
`RendererImpl<IntSample>::render` → `doRender` (`Synth.cpp:2375-2377`) and
**none** of those buffers is instantiated. Mix the types — a float renderer
with an int16 sink, say — and you silently acquire 16 KB of stack inside the
audio callback. Choose deliberately and put a comment next to the choice.

Budget 32 KB of stack for the render context anyway: it costs nothing in DDR3
and it covers the deep `Synth → Renderer → PartialManager → Partial → TVA/TVF/TVP
→ LA32*` call chain with margin.

The renderer's *working* buffers are not on the stack — they are members of the
heap-allocated `RendererImpl` (`Synth.cpp:212-214`), 48 KB for int16 and 96 KB
for float, allocated once at `open()`. That is counted in §7.

### 4.5 `<fstream>` must go

**Read.** `FileStream.h:94` includes `<fstream>` and `FileStream.h:112` holds a
`std::ifstream&`; `FileStream.cpp:19` includes `<clocale>` and the shared-library
build even has an option to install a system locale so that localised ROM
pathnames open (`CMakeLists.txt:138-140`). On a desktop this is invisible. In a
newlib link it drags in `std::basic_filebuf`, `std::ios_base`, locale facets and
their static initialisers — measured in the undefined-symbol dump of the stock
build as ~14 `std::` symbols and three vtables, none of which appear once the
file is excluded.

**The replacement is in the library already.** `File.h:57` defines `ArrayFile`:
a `File` backed by a `const Bit8u*` plus a size. We read the ROM off SD into our
own buffer through `mtp_storage` and wrap it:

```cpp
MT32Emu::ArrayFile ctrl(ctrl_buf, ctrl_len);
const MT32Emu::ROMImage *img = MT32Emu::ROMImage::makeROMImage(&ctrl);
```

That is exactly what `host/engine_mt32emu.cpp` does, and it is the whole of the
file I/O story. `mt32emu` never opens a file.

Caveat, **read**: `ROMInfo.h:110` says `freeROMImage` "must only be done after
all Synths using the ROMImage are deleted", so the documented contract is that
the `ROMImage` and its `File` stay alive for the synth's lifetime. That is
1.1 MB of DDR3 for a CM-32L set that, as far as I can tell, is dead weight —
`Synth` copies the control ROM into its own `controlROMData[65536]`
(`Synth.cpp:622`) and expands the PCM ROM into its own `pcmROMData`
(`Synth.cpp:656-673`). **Measured**: freeing the `ROMImage` and its buffer
immediately after `open()` and rendering two seconds produced a bit-identical
output hash to keeping it. I still would not do it on the strength of one test
against fabricated ROMs — budget for keeping them (§7) and treat freeing them as
a 1.1 MB optimisation to re-validate with real ROMs if you ever need the space.
On a board with 512 MB of DDR3 you never will.

### 4.6 The resampler is not needed

`AnalogOutputMode_ACCURATE` outputs 48000 Hz on the nose
(`Synth.cpp:294-298`: `SAMPLE_RATES[] = {32000, 32000, 48000, 96000}`), which is
the rate we want at the PCM5102A. So `SampleRateConverter`, `srchelper/` and
their `<iostream>`, `dynamic_cast` and per-call 32 KB stack buffer are all
excluded. Full argument in DESIGN.md §2.

### 4.7 `printDebug` and the libc surface

**Read.** `ReportHandler::printDebug` calls `vprintf` and `ReportHandler::
showLCDMessage` calls `printf("WRITE-LCD: %s\n", …)` (`Synth.cpp:391-398`).
Subclassing `ReportHandler` and overriding both is the supported route
(`Synth.h:89-118`, and the constructor takes one: `Synth.h:325`), and that is
what `host/engine_mt32emu.cpp` does.

But: the default implementations are compiled into `Synth.cpp` whether you use
them or not, so `stdout`, `printf` and `vfprintf` stay in the link and drag
newlib's full stdio (~10–20 KB of text plus a `_reent`). If that matters, link
with `-Wl,--gc-sections` and check; if it still matters, the honest fix is a
two-line local patch to `Synth.cpp:391-398` and a note in the LGPL-compliance
file. `MidiStreamParser.cpp:193-196` also uses `snprintf`/`sprintf`, but we do
not use that class (§ DESIGN.md §3.1).

### 4.8 Double-precision libm is on the render path — if you pick the float renderer

**Read.** `mmath.h:33-64` defines `POWF`, `EXPF`, `EXP2F`, `LOGF` etc. as calls
to the **double** `pow`, `exp`, `log`. `LA32FloatWaveGenerator.cpp` calls
`EXP2F` at lines 42, 88, 89, 123, 144, 157, 200, 226 — i.e. several times per
partial per sample.

**Inferred.** A Cortex-A7 has hardware double-precision VFP, but `exp()` from
newlib is still a polynomial evaluation with branches, not an instruction. The
integer renderer avoids all of it: it is described as "the accurate wave
generator model based on logarithmic fixed-point computations and LUTs"
(`Enumerations.h:157`). This is a second, independent reason to pick
`RendererType_BIT16S` beyond the one in §4.4 and the accuracy claim in §6.

Note also that `AnalogOutputMode_ACCURATE`'s low-pass filter is float **even in
the integer renderer** — `AccurateLowPassFilter` derives from both
`AbstractLowPassFilter<IntSampleEx>` and `<FloatSample>` and converts
(`Analog.cpp:169`, `:404-406`). That is a 16-tap FIR at 48 kHz on two channels,
about 1.5 M multiply-accumulates per second: noise on a 1.2 GHz A7 with NEON,
but it does mean the FPU must be enabled and its context handled.

### 4.9 `rand()` on the render path

**Read.** `TVP.cpp:330`: `counter = NOMINAL_PROCESS_TIMER_PERIOD_SAMPLES +
(rand() & 3);` — the pitch envelope's timer jitter, evaluated per partial per
timer tick.

**Inferred.** newlib's `rand()` goes through `_REENT` and, in the reentrant
build, takes a lock. Supply your own three-line xorshift or LCG and let the
linker prefer it; it is a legitimate substitution because the only property the
code needs is "two low bits that vary".

### 4.10 Packed structs and unaligned access

**Read.** `Structures.h:34` defines `MT32EMU_ALIGN_PACKED` as
`__attribute__((packed))` and applies it throughout `MemParams`
(`Structures.h:141,148,158,168`), because sysex writes land directly in those
structures as byte ranges.

**Inferred.** GCC on ARMv7 generates byte-wise access for packed member reads,
so this is correct but not free. It is all in the sysex write path, not the
render path, so it does not matter. It *would* matter if you ever mapped those
structures over a device that faults on unaligned access — do not, and keep
`SCTLR.A` (alignment fault) off, as Linux does.

---

## 5. Can rendering be split across the two A7 cores?

**No, not for one synth. Read, from the API shape:**

`Synth::render()` is a single serial pass over the sample timeline. Inside it,
`doRenderStreams` (`Synth.cpp:2448-2481`) walks forward in time, stopping at
every queued MIDI event, and calls `produceStreams` for each run. Partial state
lives in `PartialManager`, polys are allocated and stolen across parts, and the
reverb is a recursive filter over the mixed signal. There is no API that renders
a subrange, no API that renders a subset of partials, and the renderer object is
one instance with one set of temp buffers (`Synth.cpp:212-214`).

`renderStreams()` (`Synth.h:573-577`) looks like it might help — it hands you
non-reverb, reverb-dry and reverb-wet as six separate streams — but the reverb
has *already been computed* by the time you see the wet stream. It is a routing
convenience, not a parallelism seam.

**What two cores actually buy you.** Three options, in order of how much I
believe in them:

1. **A second engine.** A second `Synth` (a CM-32L alongside an MT-32), or
   FluidSynth for General MIDI, rendering into its own buffer on core 1, mixed
   on core 0. Two instances are independent; the library has no global mutable
   state on the render path apart from `Tables`, which is read-only after its
   one-time construction (`Tables.h:26-59`).
2. **A control plane.** Display, encoder, buttons, config, SD access on core 1;
   render and MIDI on core 0. This is the split §2.6 of the parent document
   proposes for the RISC-V companion core, and it applies equally to A7 #1.
3. **Nothing.** Park core 1 in WFI. If workstream A reports RTF ≤ 0.6, one core
   is enough and a second one is a source of cache contention on the shared L2
   and DDR3 controller, not a source of speed.

**Start with (3).** Take (1) when a second engine exists.

### The barrier problem, which is the real finding here

**Read.** `MidiEventQueue` synchronises producer and consumer with nothing but
`volatile`:

```cpp
volatile Bit32u startPosition;     // MidiEventQueue.h:67-68
volatile Bit32u endPosition;

bool MidiEventQueue::pushShortMessage(...) {          // Synth.cpp:2270-2281
    ...
    newEvent.timestamp = timestamp;
    endPosition = newEndPosition;                     // <- no barrier
    return true;
}
```

`volatile` in C++ means "do not optimise away the access". It says nothing about
*ordering as observed by another core*. On x86, stores are ordered anyway and
this works. On ARMv7-A, which has a weakly ordered memory model, core 1 can
observe the updated `endPosition` before it observes the event payload written
just above it, and the renderer will then read a half-written `MidiEvent`.
`BufferedSysexDataStorage::allocate` has the same shape and even carries a
comment reasoning about non-atomic writes on the assumption of a single
timeline (`Synth.cpp:2206-2208`).

I want to be fair to upstream: the header's threading note
(`MidiEventQueue.h:32-34`) is a statement about *data-race-freedom by
construction on a sequentially consistent machine*, and on every platform Munt
ships to it is correct in practice. It is simply not a guarantee you can carry
to a weakly ordered dual-core SoC without adding `DMB`.

**Consequence for the design, and it is a good one:** put the MIDI producer and
the renderer on the *same* core. The UART ISR writes raw bytes into *our* ring
(`mtp_midi_read`), which we make correct with explicit barriers because we wrote
it; the render loop drains it, parses it and calls `playMsg` from the render
context. Munt's queue is then used single-threaded, which is unambiguously safe,
and we never have to patch a vendored library. DESIGN.md §3 does exactly this.

---

## 6. Accuracy modes, and which are cheaper

Two independent knobs, often confused.

### 6.1 `RendererType` — integer versus float

**Read**, `Enumerations.h:156-161`:

- `RendererType_BIT16S`: "16-bit signed samples in the renderer and the
  accurate wave generator model based on logarithmic fixed-point computations
  and LUTs. **Maximum emulation accuracy and speed.**"
- `RendererType_FLOAT`: "float samples … and **simplified** wave generator
  model. Maximum output quality and minimum noise."

This is the opposite of the usual intuition. The integer path is the *more*
accurate model (it reproduces the LA32's own log-domain arithmetic, quantisation
noise included) and it is the faster one. The float path is cleaner-sounding but
is a simplification of the hardware and, per §4.8, calls `exp()` several times
per partial per sample.

**Pick `BIT16S`.** More accurate, faster, no libm on the hot path, no stack
buffers (§4.4). The only reason to build the float path is A/B listening.

**Measured**, its other cost: the float renderer's heap footprint is 67 KB
larger for MT-32 and 149 KB larger for CM-32L with preallocated reverb, because
every sample buffer and every reverb delay line doubles in width (§7).

### 6.2 `AnalogOutputMode` — how much of the analogue circuit to emulate

**Read**, `Enumerations.h:123-139` and `Synth.cpp:294-298`:

| Mode | Output rate | What it does | Relative cost |
|---|---|---|---|
| `DIGITAL_ONLY` | 32000 | nothing; samples at the DAC pins | cheapest |
| `COARSE` | 32000 | 8-tap FIR approximating the LPF (`Analog.cpp:96`) | cheap |
| `ACCURATE` | **48000** | 16-tap ×3-phase polyphase FIR, ×3/÷2 (`Analog.cpp:97-100`) | moderate |
| `OVERSAMPLED` | 96000 | same filter, ×3/÷1 | 2× ACCURATE |

The important structural fact, **read** at `Synth.cpp:2350`: `doRender` calls
`doRenderStreams(tmpBuffers, getAnalog().getDACStreamsLength(thisPassLen))`.
The LA32 and the partials always run at 32 kHz; the analogue stage is what
changes rate. So going from `COARSE` at 32 kHz to `ACCURATE` at 48 kHz does
**not** make synthesis 1.5× more expensive. It makes only the 16-tap stereo FIR
run at 48 kHz instead of an 8-tap one at 32 kHz — about 1.5 M MACs/s against
0.5 M.

**Inferred, and this is the design's keystone:** `ACCURATE` gives us a 48 kHz
output for almost nothing and removes the resampler entirely — which removes
RTTI, `<iostream>`, a 32 KB stack buffer, a per-call allocation path and a
whole quality-versus-speed argument. See DESIGN.md §2.

### 6.3 Other cheapening knobs, in order of effect

| Knob | Where | Effect |
|---|---|---|
| `setReverbEnabled(false)` | `Synth.h:434` | Removes the reverb entirely. Big, and audibly wrong for MT-32 material |
| `MT32EMU_BOSS_REVERB_PRECISE_MODE 0` | `internals.h:86-88` | Already the default: "Maximum speed at the cost of a bit lower emulation accuracy" |
| `usePartialCount < 32` | `Synth.h:340` | Caps polyphony. A real MT-32 has 32 partials; fewer is a different instrument, not a faster one |
| `setNicePanningEnabled` / `NicePartialMixing` | `Synth.h:532,544` | Quality options, negligible cost either way |

---

## 7. Memory budget

**Measured.** Global `operator new` accounting, `Synth` opened at 32 partials
and `AnalogOutputMode_ACCURATE`, on x86-64. Figures in KiB. "Synth" is
everything the library allocates; "ROM buffers" is the file data an `ArrayFile`
points at, which §4.5 says to keep resident.

| Configuration | Synth | ROM buffers | **Total** |
|---|---|---|---|
| MT-32, int16, reverb not preallocated | 869 | 576 | 1 445 |
| **MT-32, int16, reverb preallocated** | **949** | **576** | **1 525** |
| MT-32, float, reverb preallocated | 1 094 | 576 | 1 670 |
| CM-32L, int16, reverb not preallocated | 1 385 | 1 088 | 2 473 |
| **CM-32L, int16, reverb preallocated** | **1 465** | **1 088** | **2 553** |
| CM-32L, float, reverb preallocated | 1 611 | 1 088 | 2 699 |

The bold rows are the recommended configuration.

### Where it goes

**Measured** by `sizeof` against the real headers, plus **read** allocation
sites:

| Item | Bytes | Source |
|---|---|---|
| `controlROMData[CONTROL_ROM_SIZE]` | 65 536 | `Synth.h:75,176` — a 128 KB v2.x ROM is **truncated to 64 KB** at `Synth.cpp:622` |
| `pcmROMData`, expanded 16-bit | 524 288 (MT-32) / 1 048 576 (CM-32L) | `Synth.cpp:797-798`; one ROM byte pair → one `Bit16s` |
| `MemParams` × 2 (`mt32ram`, `mt32default`) | 138 070 | `sizeof(MemParams)` = 69 035, `Synth.cpp:301-302` |
| `RendererImpl` temp buffers, 6 × 4096 | 49 152 (int16) / 98 304 (float) | `Synth.cpp:212-214` |
| Reverb delay lines, all four modes | ~101 000 (int16) / ~202 000 (float) | sizes at `BReverbModel.cpp:61-110`; 51 808 samples total |
| MIDI event ring, 1024 × 16 | 16 384 | `globals.h:117`, `Synth.cpp:2248` |
| Sysex storage ring (our choice) | 65 536 | `Synth.h:368` |
| 32 × `Partial` + `TVA`/`TVF`/`TVP` + LA32 pair | ~23 000 | `sizeof`: 280 + 72 + 48 + 88 + 208 each |
| `Synth` object itself | 66 024 | dominated by `controlROMData` |
| ROM file buffers (kept resident, §4.5) | 589 824 / 1 114 112 | our buffers, not the library's |

Plus this port's own: audio ring 3 × 128 × 2 × 2 B = 1.5 KB, MIDI byte FIFO
2 KB, parser sysex buffer 32 KB, retry buffer 32 KB, stack 32 KB. Call it
100 KB.

**On ARM these numbers shrink slightly** — the measurement is on a 64-bit host,
and the pointer-heavy structures (`Partial`, `PartialManager` tables, the
`MidiEvent` ring) roughly halve their pointer content. The arrays that dominate
— ROM, PCM, `MemParams`, renderer buffers, reverb lines — contain no pointers
and do not change at all. Expect within a few per cent of the table above.

### Can it live in on-chip SRAM? No.

The T113 family's on-chip SRAM is a few hundred kilobytes at most across its
A1/A2/C banks, and part of that is spoken for by the boot ROM, the RISC-V
companion core and the HiFi4 DSP — **get the exact figure from the T113-i
datasheet before quoting it**, because the argument does not depend on it. The
smallest defensible configuration — MT-32, int16, reverb not preallocated, ROM
buffers freed after `open()` — is **869 KiB**, and the expanded PCM ROM alone is
512 KiB. No plausible reading of the SRAM map gets you there.

This contradicts a hope expressed in the parent document (§2.3, "Munt's working
set is small — on the order of 1 to 2 MB including the ROMs — which is inside
the on-chip SRAM of parts like the i.MX RT1170"). The working-set figure is
right; the conclusion is not transferable to the T113, which has two orders of
magnitude less SRAM than an RT1170's 2 MB. **The T113 build needs the DDR3.**

That is fine — the DDR3 is in the BOM for other reasons — but it has a
performance consequence worth handing to workstream A: the PCM ROM is 512 KiB
to 1 MiB and is addressed pseudo-randomly by whichever partials are playing
PCM waveforms. It will not fit in the A7 cluster's L2. **Any RTF measurement
taken on a machine with a large cache will flatter the T113.** Measure on a
T113 dev board with its real DDR3, as PLAN.md §0 already says.

### Sizing the arena

Round the CM-32L int16 figure up and add the port's own: **a 4 MB arena** covers
every configuration in the table with room to spare, and there is no reason to
be tighter on a 512 MB part. Set the arena's high-water mark to be logged at
boot so a regression is visible.

---

## 8. Summary of the awkward parts

In the order I would worry about them:

1. **The unbarriered MIDI queue (§5).** Only a problem if you cross cores.
   Design around it: MIDI and render on one core.
2. **Allocation on a reverb-mode sysex (§3).** One API call. Measured, so you
   can prove it is gone.
3. **`<fstream>` (§4.5).** One file to exclude, one `ArrayFile` to construct.
4. **Stack buffers on the type-mismatched render path (§4.4).** Not a problem
   in the recommended configuration, and a silent 16 KB if you drift out of it.
5. **`stdout` reaching the link through `printDebug` (§4.7).** Cosmetic unless
   flash is tight, which on an SD-booted DDR3 system it is not.

Everything else — C++98, no STL, no threads, no exceptions, no RTTI, 20 external
symbols — is better than a bare-metal port has any right to expect.
