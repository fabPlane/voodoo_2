# What mt32emu actually does per sample

Read of Munt `6e7c01fba7e1d50c8fa705834889fd0eac136075` (mt32emu 2.8.3).
All line numbers are into `bench/vendor/munt/mt32emu/src/`.

**Nothing here is a timing measurement.** No MT-32 ROMs exist in this
environment and no ARM silicon or emulator was available, so the harness has
never been run. This is a source read plus disassembly of the armv7-a build.
Where I make a numeric claim it is either a size from `size(1)`/`sizeof`, an
instruction count from `objdump`, or arithmetic — never a timing.

## 1. The shape of the render call

`Synth::render()` → `RendererImpl<Sample>::doRender()` → `doRenderStreams()` →
`produceStreams()`. The whole of the per-block work is here:

```
Synth.cpp:2648  void RendererImpl<Sample>::produceStreams(...)
Synth.cpp:2656      mute the four accumulation buffers
Synth.cpp:2661      for (i = 0; i < synth.getPartialCount(); i++)
Synth.cpp:2663          partialManager.produceOutput(i, reverbDryL, reverbDryR, len)
Synth.cpp:2665          ... or into nonReverbL/R
Synth.cpp:2669      produceLA32Output()      (a bit re-shuffle over the block)
Synth.cpp:2673      reverbModel.process()
Synth.cpp:2686      convertSamplesToOutput()  (analog LPF / gain)
```

So the structure is: **outer loop over 32 partial slots, inner loop over the
block's samples.** Each partial walks the whole block accumulating into a
shared stereo pair.

`PartialManager::produceOutput` (`PartialManager.cpp:70`) is a one-line
forward to `Partial::produceOutput`, whose body is:

```
Partial.cpp:378  template <class Sample, class LA32PairImpl>
Partial.cpp:379  bool Partial::doProduceOutput(...) {
Partial.cpp:383      for (sampleNum = 0; sampleNum < length; sampleNum++) {
Partial.cpp:384          if (!generateNextSample(la32PairImpl)) break;
Partial.cpp:385          produceAndMixSample(leftBuf, rightBuf, la32PairImpl);
Partial.cpp:386      }
```

and the per-sample step (`Partial.cpp:336-352`) is:

```
Partial.cpp:336      tva->isPlaying() && la32Pair->isActive()     -- two predicate calls
Partial.cpp:340      la32Pair->generateNextSample(MASTER,
                         getAmpValue(),        <- TVA envelope, one LA32Ramp step
                         tvp->nextPitch(),     <- TVP, one timer step
                         getCutoffValue())     <- TVF, one LA32Ramp step
Partial.cpp:341-343  ... and the same again for the ring-modulating slave,
                     if this partial has one
```

That is the unit of work. **Per active partial, per sample**, with a second
full pass when the partial is half of a ring-modulated pair.

## 2. What dominates: integer table lookups and branches, not arithmetic

The default renderer is the integer one, set in the `Synth` constructor:

```
Synth.cpp:331    selectRendererType(RendererType_BIT16S);
```

`Enumerations.h` describes it as "the accurate wave generator model based on
logarithmic fixed-point computations and LUTs. Maximum emulation accuracy and
speed." That is exactly what the code is.

The LA32 works entirely in a **log domain**. Everything — the waveform, the
TVA amplitude, the TVF cutoff contribution — is added as a log value, and the
result is exponentiated once at the end:

```
LA32WaveGenerator.cpp:33-35   // "These two tables are accessed extremely often.
                              //  Keeping the direct pointers here significantly
                              //  improves performance in most cases."
                              static const Bit16u *exp9;
                              static const Bit16u *logsin9;

LA32WaveGenerator.cpp:41-47   interpolateExp()  -- two exp9[] loads + a lerp
LA32WaveGenerator.cpp:49-55   unlog()           -- interpolateExp() then a shift
LA32WaveGenerator.cpp:57-61   addLogSamples()   -- an add and a sign XOR
```

Both tables are `Bit16u[512]` (`Tables.h:50-51`) — 1 KiB each, 2 KiB total,
and they account for most of `Tables.cpp.o`'s 2,620 bytes of bss.

The per-sample generator is a phase-driven state machine over those tables:

```
LA32WaveGenerator.cpp:144-169  generateNextSquareWaveLogSample()
                               switch (phase) over 6 segments, one logsin9[] load,
                               two shifts, two adds, a clamp
LA32WaveGenerator.cpp:171-209  generateNextResonanceWaveLogSample()
                               the expensive one: up to four logsin9[] loads,
                               a multiply, several conditional adds, two clamps
LA32WaveGenerator.cpp:211-...  generateNextSawtoothCosineLogSample()  (sawtooth only)
LA32WaveGenerator.cpp:296-322  generateNextSample() ties them together
```

and the envelopes are a handful of compares and one add each:

```
LA32Ramp.cpp:110-144   nextValue()  -- one add or subtract, two compares,
                                       an interrupt countdown. 80 bytes of Thumb-2.
Partial.cpp:256-272    getAmpValue()     -- ampRamp.nextValue() + interrupt check
Partial.cpp:274-282    getCutoffValue()  -- cutoffModifierRamp.nextValue() + check
TVP.cpp:321-336        nextPitch()  -- usually just a decrement and a return;
                                       does real work only every ~N samples
```

**Answer to "float or integer": integer, overwhelmingly.** The default path
touches no floating point at all in the per-sample loop. Confirmed by
disassembly — `LA32WaveGenerator.cpp.o`, `Partial.cpp.o`, `LA32Ramp.cpp.o`,
`TVA.cpp.o`, `TVF.cpp.o`, `TVP.cpp.o` contain no libm calls and no VFP
double-precision instructions at all.

The character of the work is: **L1-resident table lookups, 16- and 32-bit
shifts and adds, and a lot of unpredictable branches.** That is a profile the
Cortex-A7 is reasonable but not brilliant at — it is a dual-issue in-order
core with a fairly modest branch predictor, so the `switch (phase)` in the
wave generator and the conditional ladders in `generateNextResonanceWaveLogSample`
are where the cycles will actually go.

## 3. The float renderer is disqualified on this platform

Do not use `--renderer float` on the T113. The float wave generator's
per-sample function calls **double-precision libm**:

```
LA32FloatWaveGenerator.cpp:71   generateNextSample(...)
LA32FloatWaveGenerator.cpp:88   EXP2F(ampVal / -1024.0f / 4096.0f)
LA32FloatWaveGenerator.cpp:89   EXP2F(pitch / 4096.0f - 16.0f) * SAMPLE_RATE
LA32FloatWaveGenerator.cpp:115  fmod(newPCMPosition, float(pcmWaveLength))
LA32FloatWaveGenerator.cpp:172  sin(FLOAT_PI * (cutoffVal - MIDDLE_CUTOFF_VALUE) / 32.0f)
LA32FloatWaveGenerator.cpp:179  cos(FLOAT_PI * relWavePos / cosineLen)
LA32FloatWaveGenerator.cpp:258  cos(FLOAT_2PI * wavePos / waveLen)
```

and `EXP2F` is not `exp2f` — off Apple platforms it is `exp()` on a double:

```
mmath.h:41-48   static inline float EXP2F(float x) {
                  #ifdef __APPLE__
                    return exp2f(x);
                  #else
                    return exp(FLOAT_LN_2 * x);     <- double exp()
                  #endif
                }
```

Disassembling the armv7-a build of that one function
(`LA32FloatWaveGenerator::generateNextSample`) finds **9 calls to `exp`, 3 to
`sin`, 3 to `cos` and 1 to `fmod`**, and 40 double-precision VFP instructions.
Branching means not all of them execute on every sample, but even one
double-precision libm call per partial per sample is fatal at 32 kHz × 32
partials on a 1.2 GHz A7. The integer generator, by contrast, has zero calls
out except to its own two helpers.

This is worth measuring once on the board — `rtf --renderer float` exists for
exactly that — but the expected answer is "several times slower", and it is
not a close call.

## 4. Would NEON help? Mostly no, with one specific exception

The armv7-a build was inspected for actual NEON (q-register) instructions per
object. GCC 13.3 at `-O2 -mcpu=cortex-a7 -mfpu=neon-vfpv4` auto-vectorised:

| Object | NEON q-reg insns |
|---|---:|
| `Synth.cpp.o` | 531 |
| `sha1.cpp.o` | 51 |
| `Part.cpp.o` | 42 |
| `MidiStreamParser.cpp.o` | 36 |
| `Analog.cpp.o` | 15 |
| **`Partial.cpp.o`** | **0** |
| **`LA32WaveGenerator.cpp.o`** | **0** |
| **`LA32Ramp.cpp.o`** | **0** |
| **`TVA.cpp.o` / `TVF.cpp.o` / `TVP.cpp.o`** | **0** |
| **`BReverbModel.cpp.o`** | **0** |
| **all of `srchelper/` (the resampler)** | **0** |

And of `Synth.cpp.o`'s 531, the breakdown by symbol is:

| Symbol | NEON insns | in the render path? |
|---|---:|---|
| `Synth::dumpSysexBank` | 198 | no |
| `Synth::loadPCMROM` | 141 | no — one-time ROM decode |
| `SysexBuilder::appendSysex` | 97+11 | no |
| `RendererImpl<short>::produceLA32Output` | 30 | yes, but it is a bit-shuffle over the block |
| `RendererImpl<short>::produceStreams` | 30 | yes — these are the `muteSampleBuffer` memsets |
| `RendererImpl<short>::convertSamplesToOutput` | 5 | yes, trivially |

**So: essentially none of the synthesis work is vectorised today, and hand-
writing NEON for it would not help either.** The reasons are structural, not
a compiler failure:

- The LA32 is a **sequential state machine**. `wavePosition`, `squareWavePosition`,
  `resonanceSinePosition` and the `phase` enum each depend on the previous
  sample (`LA32WaveGenerator::advancePosition`, 472 bytes of Thumb-2). You
  cannot compute sample *n+1* without sample *n*, so there is no vectorising
  *across samples within one partial*.
- Vectorising *across partials* is the theoretically right axis — 32 partials
  are independent — but they are in different `phase` states with different
  branch outcomes, so every lane needs predication, and worse:
- The core operation is a **gather**: `logsin9[(squareWavePosition >> 9) & 511]`
  and `exp9[fract >> 3]` with per-lane indices. armv7 NEON has **no gather
  instruction**. Four lanes means four scalar loads plus `vmov`s, which costs
  more than doing it scalar.
- `BReverbModel`'s comb and allpass filters are **feedback** filters — serial
  by definition.

The one genuine hand-NEON opportunity is **`srchelper/` — the sample rate
converter**. `SincResampler` and `FIRResampler` got **zero** NEON from GCC,
and a FIR is precisely what NEON is good at: contiguous loads, `vmla.f32`,
no gather, no feedback. If the board runs its DAC at 48 kHz (which a PCM5102A
almost certainly will, against mt32emu's native 32 kHz), that FIR is in the
signal path on every output sample and is worth attacking. Measure it first
with `rtf --sample-rate 48000` against `rtf --sample-rate 32000`; if the delta
is small, leave it alone.

Note: `SincResampler.cpp.o` also contains 78 double-precision ops, but they are
all in `KaizerWindow::windowedSinc`, `bessel`, `estimateOrder` and
`createSincResampler` — i.e. **filter construction at open() time**, not the
per-sample path. That is fine.

`Analog.cpp` (the LPF emulation, `--analog coarse` by default) got 15 NEON
insns and is a plausible second target, but it is 5 KB of code total and runs
once per output sample rather than per partial-sample, so it is unlikely to be
where the time is.

## 5. Can the two cores be used?

**Not through the public API, no.**

The partial loop is `Synth.cpp:2661`, inside `RendererImpl::produceStreams`,
which is private. `PartialManager.h` and `Partial.h` are **not public headers** —
the installed set is `mt32emu.h`, `globals.h`, `Enumerations.h`, `Types.h`,
`File.h`, `FileStream.h`, `MidiStreamParser.h`, `ROMInfo.h`,
`SampleRateConverter.h`, `Synth.h` (`mt32emu/CMakeLists.txt:197-212`). There is
no hook to split the partial loop from outside.

`Synth.h` is explicit that there is exactly one rendering thread:

```
Synth.h:379   "Calls from multiple threads must be synchronised, although, no
               synchronisation is required with the rendering thread."
Synth.h:397   "A thread that invokes these methods must be explicitly
               synchronised with the thread performing sample rendering or be
               the same."
```

What is available without patching:

1. **Two independent `Synth` instances on two cores.** Legitimate and useful if
   the plan's "second synth (FluidSynth, small font)" happens — but it does not
   make one MT-32 faster.
2. **Pipelining across blocks**: core 0 renders block *n* while core 1 does the
   I²S/DMA, MIDI parsing, display. This is worth doing regardless and it is
   probably most of what the second core is for. It does not reduce the
   worst-case render RTF, which is the gate.
3. **Split reverb off**, partially. `Synth::renderStreams()` hands out
   `nonReverb*` and `reverbDry*` separately (`Synth.h:573-577`), so with
   `setReverbEnabled(false)` you get the dry signal and could run a reverb on
   the other core. But `BReverbModel` is not a public class, so you would be
   writing your own reverb, which changes the sound. Probably not worth it.

What patching would buy, if the gate comes back marginal (RTF 0.6–1.0):

The partial loop at `Synth.cpp:2661` is **almost** embarrassingly parallel.
Each `Partial::produceOutput` reads its own state and accumulates into a shared
stereo pair. Give each core its own accumulation buffer and sum at the end and
the partials are independent. The catch is the integer mixer:

```
Partial.cpp:354-368  produceAndMixSample(IntSample*, IntSample*, ...)
                     leftOut = ((sample * leftPanValue) >> 13) + *leftBuf;
                     *leftBuf++ = Synth::clipSampleEx(leftOut);
```

— it **saturates at every accumulation step**, so the result depends on the
order partials are summed in. Splitting the loop across two cores changes the
output bit-for-bit whenever any intermediate sum clips. That may or may not be
audible; it is a deliberate emulation-accuracy decision, not a bug. The float
mixer (`Partial.cpp:370-375`) is a plain `+=` and has no such problem, but the
float renderer is disqualified for the reason in § 3.

So: a two-core split of the partial loop is **mechanically straightforward and
semantically lossy**. It is the obvious lever to pull if the single-core number
lands between 0.6 and 1.0, and it is a fork of mt32emu, which the LGPL is fine
with as long as the source is published.

## 6. The cycle budget, as arithmetic

This is not a prediction. It is what the target implies.

- Native rate 32,000 Hz; 32 partials maximum (`globals.h:94`, `globals.h:97`).
- A7 at 1.2 GHz → 37,500 cycles per output sample.
- At the plan's RTF ≤ 0.6 target → **22,500 cycles per output sample** for
  everything: 32 partials, reverb, the analog LPF, the mix, MIDI dispatch.
- Reserve, say, 20 % of that for reverb + analog + mix + MIDI, and 32 fully
  active partials leaves roughly **560 cycles per partial per sample**.

For scale, the code that has to be walked per partial per sample is, on armv7
Thumb-2: `Partial::produceOutput` 624 B (loop body inlined),
`LA32IntPartialPair::generateNextSample` 340 B,
`LA32WaveGenerator::advancePosition` 472 B, `nextOutSample` 300 B,
`unlogAndMixWGOutput` 240 B, `LA32Ramp::nextValue` 80 B × 2,
`TVP::nextPitch` 176 B — call it 2.3 KB of code, of which only a branch-
dependent fraction executes each iteration. At roughly 3 bytes per Thumb-2
instruction that is a few hundred instructions of headroom, on a core that
will manage well under 1 IPC on branchy integer code with L1 table lookups.

**That is neither obviously safe nor obviously doomed.** It is close enough
that the answer genuinely has to be measured, which is the point of the
harness. The two things that most plausibly decide it are (a) how many
partials the busiest real passage actually keeps active — 32 is the ceiling,
not the norm — and (b) whether the 48 kHz resampler is in the path.

## 7. Practical consequences for the port

1. **Use the integer renderer.** It is the default (`Synth.cpp:331`); do not
   change it.
2. **Pin `exp9[512]` and `logsin9[512]`** — 2 KiB, hit on essentially every
   sample of every partial, and the source itself flags them as the hot ones
   (`LA32WaveGenerator.cpp:33`). If anything goes in the T113's on-chip SRAM,
   it is these.
3. **Cut `MT32EMU_MAX_SAMPLES_PER_RUN`** (`globals.h:107`) from 4096 to the
   actual DMA block size. `RendererImpl` holds six inline buffers of that
   length (`Synth.cpp:212-214`); at 256 frames that is 3 KiB instead of 48 KiB.
4. **`rand()` is called in the render path.** `TVP.cpp:330`:
   `counter = NOMINAL_PROCESS_TIMER_PERIOD_SAMPLES + (rand() & 3);`. A bare-metal
   or RTOS port must supply a `rand()`, and it should be a cheap one — glibc's
   is not free and this is called per partial per sample (though it early-outs
   most of the time).
5. **`Synth::preallocateReverbMemory(true)`** before `open()` if the render
   loop must never allocate; the harness already does this. It costs ≈103 KB
   instead of ≈25 KB.
6. **`MidiStreamParser` is not needed** if the UART ISR feeds `playMsg()`
   directly — 4.4 KB of text you can drop. So is most of `ROMInfo.cpp`'s
   7.4 KB SHA-1 table once the ROM set is fixed at build time.
