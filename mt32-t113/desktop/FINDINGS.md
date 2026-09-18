# `desktop/` — findings

Workstream F. Status: 2026-09-18. Written in a Linux container with **no sound
device, no MIDI hardware, no ROMs and no Mac**, which is most of what this
document is about.

---

## 1. What was actually built and run

Everything below was executed in this session, on x86-64 Ubuntu 24.04, GCC 13.3.

| | |
|---|---|
| `cmake -S desktop -B desktop/build && cmake --build desktop/build -j` | **builds clean**, no warnings at `-Wall -Wextra -Wshadow`, linking `mt32emu` built out of source from `bench/vendor/munt/mt32emu` |
| the same with `-DMT32EMU_SOURCE_DIR=` | builds, fake engine only |
| the same with `-DCMAKE_DISABLE_FIND_PACKAGE_ALSA=ON` | builds, `--midi-seq` disabled and says which package to install, everything else works |
| `./desktop/test.sh` | **27 assertions, all pass** |
| miniaudio **null** backend, 2 s and 8 s runs | runs, zero underruns at `--ring 8` |
| miniaudio **ALSA** backend, against ALSA's `null` PCM plugin | opens a real `snd_pcm`, starts, calls our callback with 128-frame periods. That PCM is unpaced, so it is a plumbing test, not a timing test |
| MIDI from **stdin** and from a **named FIFO** | 49-byte demo stream and a 16 kB timbre bank dump: parsed, zero errors |
| MIDI from a **serial tty**, driven byte-by-byte through a pty at 320 us spacing | `TCSETS2`/`BOTHER` set 31250 baud and read it back unchanged; 49 bytes arrived and parsed |
| **SMF** playback, including `--midi-loop` | format 0, tempo meta, running status expanded, sysex reconstructed; run ends by itself 2 s after the file does |
| `--midi-seq` (ALSA sequencer) | compiles and links against libasound; fails cleanly here because this container has no `/dev/snd/seq` |
| `mt32emu` with **no ROM files** | names the missing path, exits 1 |
| `mt32emu` with ROM files of the **right size and wrong contents** (64 kB + 512 kB of `/dev/urandom`) | "control ROM not recognised (wrong file, or a half image)", exits 1 |
| `--engine auto` with no ROMs | warns, explains what the fake engine is, falls back |
| WAV tap (`--tap-wav`) | 48 kHz stereo, peak 23409 from the demo stream — the audio really did reach the device path |
| Ctrl-C | clean shutdown, summary printed, exit 0 |

### What this verifies

1. **The platform seam is implementable a third time, unchanged.** `port/include`
   was written for a DMA ring and a UART. Nothing in it had to be modified to
   sit on top of a callback-driven sound card, a pthread, an ALSA sequencer or a
   Standard MIDI File. `port/src/*.c` and `port/host/engine_*.c*` are compiled
   here byte-for-byte as they are in `port/host`.
2. **The ring discipline survives a consumer that is genuinely concurrent.**
   `port/host`'s virtual play cursor advances when the render loop asks it to;
   this one advances on another thread whether we are ready or not, and the
   `acquire`/`commit`/`queued`/`wait` protocol in `port/src/mtp_render.c` still
   holds. That is a real single-producer/single-consumer test the WAV harness
   cannot do.
3. **The MIDI parser handles bytes that arrive the way bytes really arrive** —
   one at a time from a tty through `poll()`/`read()`, in 512-byte gulps from a
   FIFO, and as decoded sequencer events — rather than out of a `malloc`ed
   buffer. Running status, real-time bytes inside other messages, 16 kB sysex
   reassembly across ~2000 audio blocks, and all three malformed cases behave
   exactly as `port/host/test.sh` says they do.
4. **The `mt32emu` binding links and fails honestly.** Both failure modes
   (missing file, unrecognised image) produce the message `port/DESIGN.md` 4.3
   asks for, and the process exits non-zero. No audio is faked anywhere.

### What this does **not** verify

- **That anything is audible.** This machine has no sound device. Nothing here
  has ever been through a speaker. The ALSA run used ALSA's `null` PCM, which
  discards samples as fast as it is given them.
- **That the macOS build compiles**, let alone works. See section 4.
- **Anything at all about speed on a Cortex-A7.** See section 3.
- **That `mt32emu` produces correct MT-32 audio**, because there are no ROMs and
  the library refuses to open without recognised ones, by design.
- **Real MIDI hardware.** A pty is a tty, so the `termios` path is real, but no
  byte in this session has been through a UART, an optocoupler or a DIN socket.
  The 31250-baud setting was accepted and read back by a pty driver that is not
  clocking anything.
- **Latency.** The numbers in `port/DESIGN.md` 2.5 are unmeasured, and a
  desktop cannot measure the two fixed terms (wire time, DAC group delay) anyway.

---

## 2. The audio library: miniaudio

**Chosen: [miniaudio](https://miniaud.io) v0.11.25, vendored whole at
`vendor/miniaudio/miniaudio.h` (4.1 MB, one header) with its licence text at
`vendor/miniaudio/LICENSE`.**

**Licence: a choice of the Unlicense (public domain) or MIT-0** — MIT with the
attribution clause removed. Either is compatible with this repository and with
`mt32emu`'s LGPL 2.1, and neither adds an obligation to anyone who builds or
ships this. The full text is vendored unmodified; the notice at the end of
`miniaudio.h` is intact.

Why it beats the alternatives here:

- **It needs no development packages, on either OS.** On Linux it `dlopen`s
  `libasound.so.2` and `libpulse.so.0` at run time and declares their entry
  points itself, so `desktop/` builds on a machine with no `libasound2-dev` —
  which this container was, and which is exactly the "a person can actually use
  it" test. Verified: the ALSA backend enumerated and opened a device here with
  only `libasound.so.2` present. On macOS it uses CoreAudio, which ships with
  the OS.
- **One file, no build system of its own.** `port/` is deliberately a repository
  you can build with `cc` and `make`; adding a dependency that needs `pkg-config`
  and a package manager would be the wrong kind of first impression. Compare
  PortAudio (a library to build and install, and a `configure` to argue with),
  RtAudio (C++, and another build), SDL2 (enormous, and drags in a window
  system), JACK (a daemon the user has to be running), libsoundio (unmaintained
  since 2017).
- **It is one seam wide.** We use `ma_context`, `ma_device`, and a callback.
  Decoders, resampling, the node graph and the high-level engine are switched
  off in `vendor/miniaudio/miniaudio_impl.c` — 80% of its compile time and all
  of its file-format handling, gone.
- **It has a null backend**, which is what makes this build testable on a machine
  with no sound card at all, and a JACK backend for anyone who wants to measure
  latency properly later.

The cost is honest: 4.1 MB of vendored third-party C in a repository whose other
vendored thing is an emulator. It compiles in about ten seconds with the
decoders off, is built with `-w` so its style does not pollute our warnings, and
is not linked into anything that will run on the T113 — the target's audio path
is I2S and DMA, and miniaudio never goes near it.

**PulseAudio note.** miniaudio prefers PulseAudio over ALSA when both are
present. That adds Pulse's own buffering on top of ours and is the most likely
source of underruns on a normal Linux desktop; `--audio alsa` bypasses it. This
is untested here (no sound server in this container).

---

## 3. The real-time factor is not the gate, and will be read as if it were

This build prints a live real-time factor because it has a real clock and it
would be perverse not to. It is labelled on every line it appears on:

```
  12.3s  rtf 0.081 (desktop, not the A7 gate)  ring 2  under 0  ...
```

and the end-of-run summary spends six lines saying what it is not. That
verbosity is deliberate. `docs/PLAN.md` section 0 makes the whole project
conditional on an RTF of 0.5-0.6 **on one Cortex-A7 at 1.2 GHz**, and a number
printed next to the words "real-time factor" will be quoted as that number by
someone skimming. The two are not convertible:

- different ISA and different code generation;
- an x86 or Apple-silicon L2 that holds the 512 kB-1 MB PCM ROM, which the A7's
  will not — and `port/PORTING.md` 7 notes the ROM is addressed
  pseudo-randomly, so this flatters the desktop specifically;
- a desktop memory system rather than one DDR3 x16;
- and on Apple silicon, an entirely different microarchitecture again.

What the desktop number *is* good for: relative comparisons on one machine —
reverb on versus off, 32 partials versus 24, `--rate 32000` versus 48000,
`AnalogOutputMode` variants if someone adds the knob. Those ratios are worth
having and are cheap to measure here. The absolute number is worth nothing to
section 0, and `bench/rtf` on real silicon remains the only answer.

---

## 4. macOS is written but unverified

`src/desktop_midi_seq_core.c` (CoreMIDI) and the `__APPLE__` half of
`src/desktop_midi_tty.c` (`IOSSIOSPEED`) **have never been compiled or run.**
There was no Mac in this session and there is no way to cross-check them. The
CoreAudio path is miniaudio's, which is widely used, but our `CMakeLists.txt`
Apple branch — the framework list, `MA_NO_RUNTIME_LINKING` — is equally
unverified.

They are written carefully and guarded so they cannot break the Linux build
(proven: the Linux build is clean, and the `desktop_midi_seq_none.c` fallback
compiles too), but **do not report the macOS build as working until someone has
run it.** Expect to fix something on first contact. The likely candidates:

- `MIDIInputPortCreate` and `MIDIReadProc` are deprecated from macOS 11 in
  favour of `MIDIInputPortCreateWithProtocol`. They still work and still deliver
  MIDI 1.0 bytes, which is what an MT-32 wants, but a strict build may warn.
- `<IOKit/serial/ioss.h>` is header-only for our purposes (we use the
  `IOSSIOSPEED` ioctl constant), so no IOKit framework link should be needed. If
  the linker disagrees, add `-framework IOKit`.
- miniaudio's advice is `-framework AudioUnit` instead of `AudioToolbox` on
  older systems.
- CoreMIDI may need the process to have a run loop for *notifications*; we pass
  a NULL notify proc and only use the read proc, which is delivered on
  CoreMIDI's own thread, so this should be fine. Should.

---

## 5. What this build found out about the desktop, that the target does not have

**Device wake-up granularity is the binding constraint on a desktop, not CPU.**
With the target's own numbers — 128-frame blocks, a 3-block ring, 8 ms of audio
in flight — the null backend in this container produced **203 underruns in 2
seconds** while the render loop used 0.05% of one core. The reason is
measurable and now reported: the device went up to **10.3 ms between requests**
against a nominal 2.67 ms period. A ring holding 8 ms cannot survive a 10 ms
gap, however fast the synth is. `--ring 8` gives 21 ms and the underruns go to
zero.

This is not a defect in `port/DESIGN.md` 2.2's numbers. On the T113 the DMA
completion interrupt arrives every 2.67 ms because the I2S clock says so, and
nothing schedules it away. It *is* a thing every reader of this program's output
needs to know, so the summary now detects the case and says so in full sentences
rather than leaving the reader to conclude the design is broken. Two other
desktop-only consequences:

- The MIDI FIFO here is 64 Ki stamped bytes, not the target's 256
  (`port/DESIGN.md` 3.3). A UART physically cannot deliver faster than 3125
  bytes/s; a redirected file delivers 40 kB in one `read()`. With the target's
  size, a replayed capture overruns the FIFO and the parser reports a *parse*
  error, which blames the wrong component. The overrun counter is still printed
  and still means what it means.
- Underruns are not counted before the first `commit()`. The device starts
  inside `mtp_audio_open()`, microseconds before the render loop exists, and the
  silence it plays until then is the same silence the target's ring holds at
  start-up (`mtp_audio.h`: "opening early is safe"). Counting it put two or
  three phantom underruns on every run, which is exactly how a reader learns to
  ignore the number that matters most.

### 5.0 Several MIDI sources share one FIFO, which is a byte merge

`mtp_midi.h` is one stamped-byte FIFO because the target has one UART, and that
is the right shape for the target. The desktop can have four sources at once,
and they are merged at byte granularity: if two are transmitting simultaneously,
one can interleave into the middle of the other's message. Verified working with
an SMF and a stdin stream together (105 bytes, 17 short + 2 sysex, zero parse
errors) because neither was continuously busy. Two busy live sources would
corrupt each other, and the fix is not in this layer — it is `aconnect`, or a
DAW, or a real MIDI merger. Documented in README.md rather than papered over.

### 5.1 One thing to check before committing: the root `.gitignore`

The repository's root `.gitignore` has `**/vendor/`, which is right for
`bench/vendor` and `boot/vendor` (upstream clones, read and never committed) and
wrong for `desktop/vendor`, whose whole purpose is to be committed so that this
builds on a clean machine. `desktop/.gitignore` negates it:

```
!vendor/
!vendor/**
```

A nested `.gitignore` takes precedence over the root one for paths beneath it,
so this should work — but **no git command was run in this session**, so please
confirm that `desktop/vendor/miniaudio/miniaudio.h` and its `LICENSE` are
actually staged. If the negation does not take, the alternative is a narrower
root rule (`bench/vendor/` and `boot/vendor/` rather than `**/vendor/`), which
is a change to a file this workstream does not own.

---

## 6. Things in `port/` that want changing

Nothing in `port/` was modified. These are for whoever owns it.

### 6.1 `mtp_render_stats.min_queued` is polluted by start-up and reports 1 for ever

`port/src/mtp_render.c` records `min_queued` after every commit, including the
first few while the ring is still filling from empty. The first commit always
leaves occupancy 1, so **`min_queued` is 1 on every run that ever starts**, and
the "real safety margin" `port/DESIGN.md` 2.4 promises is not observable. Runs
in this session show `min ring occupancy 1 of 8` while the live readout shows a
steady occupancy of 4 to 5 and zero underruns — the metric and the reality
disagree by a factor of four.

Fix: ignore commits until occupancy has first reached `target_queued`, or keep a
separate `min_queued_steady`. It is a two-line change and it restores the one
number that is supposed to say "how close did we come".

### 6.2 A refused real-time byte is dropped silently

In `on_message()`, `MTP_MSG_REALTIME` calls `ctx->engine->short_msg(...)` and
ignores the result, with a comment explaining that real-time bytes should not
occupy a queue slot on failure. The intent is right, but nothing counts the
drop, so a synth that is losing MIDI Clock under load looks healthy. One
counter, `stat_realtime_dropped`, would close it.

### 6.3 `DESIGN.md` 4.3 asks for things `port/include` cannot express

The ROM-failure behaviour in 4.3 is good, and two parts of it are not
implementable behind the current seam:

- *"list what **is** in `/roms`"* — `mtp_storage.h` has no directory
  enumeration, deliberately (its header says so). Either 4.3 softens, or the
  seam gains one `readdir`-shaped call.
- *half-image ROM pairs* — `ROMImage::makeROMImage(File*, File*)` merges a
  `FirstHalf`/`SecondHalf` pair, and 4.3 says to support that, but
  `mtp_engine_config` carries exactly one `control_rom_path` and one
  `pcm_rom_path`. Supporting pairs needs two more fields or an array.

Neither is urgent. Both are the sort of thing that is much cheaper to decide now
than after a second implementation exists.

### 6.4 The engine seam has no volume and no panic

`mtp_engine.h` exposes open/close/timebase/rendered/short/sysex/render. A
desktop wants a master volume (`mt32emu` has `Synth::setOutputGain`), and
anything that can be stopped wants an all-notes-off on the way out. This
implementation works around the second by nothing at all: Ctrl-C stops the
device mid-note. On a hardware module with a power switch that is fine; here it
is a small wart, and on any future build with a display and an encoder it will
not be.

### 6.5 `mtp_midi_frame_errors()` cannot mean anything on a POSIX tty

The header describes it as UART framing/parity errors, which is right for the
target. A POSIX tty only reports them via `PARMRK`, which inserts marker bytes
into the stream and would corrupt MIDI data. This implementation returns 0 and
says so in a comment. Not a defect — worth a line in the header saying the
counter is target-only, so nobody later "fixes" a desktop implementation into
corrupting the byte stream.

### 6.6 `mtp_render_ctx` is 64 kB and must not go on a stack

Two `MTP_SYSEX_MAX` buffers inline. `port/host/main.c` makes it `static` with a
comment; so does this one. Given `port/PORTING.md` 4.4 measures `mt32emu`
putting 16-96 kB on a stack depending on configuration, a target task stack that
also had to hold this would be a bad day. Worth an explicit note in
`mtp_render.h` rather than in two `main.c`s.

### 6.7 `mtp_render_run()` is a test harness, not a main loop

It stops at `max_blocks`, and it *breaks out of the loop* if `mtp_audio_wait()`
times out once ("audio sink stalled"). On a desktop a device can legitimately
stall — suspend, a USB interface unplugged, a sample-rate change — and silently
ending the run is the wrong response. `port/DESIGN.md` 6.4 already says the
target uses `mtp_render_pump()` inside its own superloop, and this build does
the same, so nothing is broken. It might be worth saying in `mtp_render.h` that
`mtp_render_run` is for harnesses, since its name suggests otherwise.

---

## 7. What would make this build worth more

In rough order of value per hour:

1. **Run it on a Mac.** Half of section 4 evaporates, and the CoreMIDI path
   either works or is fixed in an afternoon.
2. **Run it on a Linux machine with a sound card and a MIDI keyboard**, with
   ROMs. That is the first time anyone will have heard this project, and it is
   also the first real test of whether 128 frames x 3 blocks is comfortable on a
   normal desktop or whether Pulse's buffering makes it hopeless.
3. **Measure round-trip latency for real**: a loopback cable, JACK, and a scope
   or an audio input. `port/DESIGN.md` 2.5 is arithmetic that has never met a
   measurement, and everything in it above the DAC is testable here.
4. **A/B the `lookahead` knob** (`port/DESIGN.md` 2.6). It exists precisely
   because the answer is a listening test, and this is the build you can listen
   on.
5. **Relative RTF measurements** — reverb on/off, partial limit, 32 kHz versus
   48 kHz. Not the gate, but real information about where the cycles go, and
   cheap.
