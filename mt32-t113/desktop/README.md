# `desktop/` — the port, on a laptop, out of the speakers

This builds the mt32-t113 platform layer against a real sound card and a real
MIDI input, so you can play the thing before the board exists. Everything above
the platform seam is the *same source* that will run on the T113:
`port/src/mtp_midi_parser.c`, `port/src/mtp_render.c` and the `mt32emu` binding
in `port/host/engine_mt32emu.cpp`. What this directory adds is a third
implementation of `port/include` — the first is `port/host` (renders to a WAV
file), the second is `emu/` (bare metal under QEMU), this is the one with
speakers.

That is `docs/PLAN.md` § 0.5 taken literally: if the ring discipline, the sysex
reassembly or the back-pressure handling is wrong, it is wrong here too, and
here you can *hear* it.

**It says nothing about speed.** The real-time factor it prints is an x86 or
Apple-silicon number. The gate in `docs/PLAN.md` § 0 is a Cortex-A7 number and
only `bench/rtf` on real silicon can answer it. See [FINDINGS.md](FINDINGS.md).

---

## Build

### Linux

```sh
sudo apt install build-essential cmake        # Debian/Ubuntu
sudo apt install libasound2-dev               # optional: MIDI in from a keyboard or DAW
cmake -S desktop -B desktop/build
cmake --build desktop/build -j
```

Audio playback needs **no** development packages at all: miniaudio opens
`libasound.so.2` or `libpulse.so.0` at run time. `libasound2-dev` is only needed
for `--midi-seq`, and everything else still works without it.

### macOS

```sh
xcode-select --install                        # if you have not already
brew install cmake
cmake -S desktop -B desktop/build
cmake --build desktop/build -j
```

No other dependencies: CoreAudio, CoreMIDI and CoreFoundation ship with the OS.

> The macOS paths in this directory have **never been compiled or run** — the
> session that wrote them had no Mac. Expect to fix something the first time.
> See [FINDINGS.md](FINDINGS.md).

### What the build picks up

```
-- mt32-desktop: OS MIDI port = ALSA sequencer
-- mt32-desktop: linking mt32emu from .../bench/vendor/munt/mt32emu
```

`mt32emu` is found automatically in `bench/vendor/munt/mt32emu` and built out of
source — nothing under `bench/` is written to. To build without it:

```sh
cmake -S desktop -B desktop/build -DMT32EMU_SOURCE_DIR=
```

---

## Three commands, on a Mac, with a MIDI keyboard

```sh
cmake -S desktop -B desktop/build && cmake --build desktop/build -j
mkdir -p ~/mt32roms && cp /wherever/MT32_CONTROL.ROM /wherever/MT32_PCM.ROM ~/mt32roms/
./desktop/build/mt32-desktop --midi-seq --roms ~/mt32roms
```

`--midi-seq` connects every CoreMIDI source it can find *and* publishes a
virtual destination called `mt32-t113`, so a USB keyboard works with no routing
step and a DAW can target it by name. On Linux the same command creates an ALSA
sequencer port; connect something to it with `aconnect`.

---

## MIDI in: four ways, all at once if you like

Any combination of these can be given on one command line; they all funnel into
the same stamped-byte FIFO and through the same parser.

> **Two live sources at once is a byte-level merge, not a MIDI merge.** The seam
> is one byte FIFO, because the T113 has one UART. If two sources are genuinely
> transmitting at the same moment, one can interleave bytes into the middle of
> the other's message and both will be mangled. Running a keyboard and an SMF
> together is fine in practice (the keyboard is idle most of the time) and is
> useful for auditioning over a backing track; two busy sources is not a
> supported configuration, and a proper merge belongs in `aconnect` or your DAW.

### 1. A serial line at 31250 baud — the highest-fidelity test

This is the one that matters, because it is bit-for-bit what the T113's UART2
will see: 8N1, 31250 baud, one byte every 320 µs, with the timing of the wire.

```sh
# Linux
./desktop/build/mt32-desktop --midi-tty /dev/ttyUSB0 --roms ~/mt32roms
# macOS
./desktop/build/mt32-desktop --midi-tty /dev/tty.usbserial-A50285BI --roms ~/mt32roms
# a non-standard rate, if you are testing something odd
./desktop/build/mt32-desktop --midi-tty /dev/ttyUSB0,38400
```

Linux asks for 31250 with `TCSETS2` + `BOTHER` and reads the rate back; if the
driver rounded it, you get a warning rather than noise. macOS uses `IOSSIOSPEED`.
Most FTDI, CP210x and CH340 adapters do 31250 exactly; some cheap CH340 clones
do not, and the warning will tell you.

`Permission denied` on Linux: `sudo usermod -aG dialout $USER`, then log out and
back in.

**What to plug into it.** Not a class-compliant USB-MIDI adapter — those appear
as ALSA rawmidi or CoreMIDI devices, not as a tty, so use `--midi-seq` for them.
This input is for:

- a **USB-serial adapter** with a MIDI DIN input on its RX line. Wire it exactly
  as the board will be wired: DIN pin 4 → 220 Ω → 6N138 anode, DIN pin 5 →
  6N138 cathode, 6N138 output → adapter RX, with the pull-up to the adapter's
  logic rail. This is the same circuit `hw/` will carry, so it is also a test of
  the circuit.
- a **game port MIDI cable from a real DOS machine.** The game port's MIDI OUT
  (pin 12) is TTL at 5 V, so it goes through the same optocoupler, or through a
  divider into a 5 V-tolerant adapter. A DOS game writing to the MPU-401 at
  0x330 then drives this synth directly, which is about as close to the intended
  use as you can get without the board.
- an **MPU-401 card's MIDI OUT**, same circuit.

### 2. The OS MIDI port — a keyboard or a DAW

```sh
./desktop/build/mt32-desktop --midi-seq --roms ~/mt32roms
```

**Linux (ALSA sequencer).** The program creates a client called `mt32-t113` with
one writable port and prints its number. In another terminal:

```sh
./desktop/build/mt32-desktop --list-midi        # what can send to us
aconnect -l                                     # the same thing, ALSA's way
aconnect 'Keystation 49'  'mt32-t113'           # by name
aconnect 24:0 128:0                             # or by number
```

Or let the program subscribe for you:

```sh
./desktop/build/mt32-desktop --midi-seq 24:0
./desktop/build/mt32-desktop --midi-seq 'Keystation'
```

**macOS (CoreMIDI).** `--midi-seq` connects all sources and creates a virtual
destination named `mt32-t113`, which appears in every DAW's MIDI output list.

```sh
./desktop/build/mt32-desktop --list-midi        # sources CoreMIDI can see
./desktop/build/mt32-desktop --midi-seq 'Keystation'   # just that one
./desktop/build/mt32-desktop --midi-seq none    # virtual destination only
```

### 3. A FIFO or stdin — anything that can write bytes

```sh
# a named pipe, created for you if it is not there
./desktop/build/mt32-desktop --midi-fifo /tmp/mt32.midi --roms ~/mt32roms &
amidi -p hw:2,0,0 -r /tmp/mt32.midi             # raw bytes from a USB keyboard
cat captured_session.syx > /tmp/mt32.midi       # replay a capture
printf '\x90\x3c\x64' > /tmp/mt32.midi          # middle C, by hand

# or straight down a pipe
cat captured_session.syx | ./desktop/build/mt32-desktop --midi-fifo - --ring 8
```

The FIFO is opened read/write, so a writer exiting does not end the run — you
can `cat` into it as many times as you like. stdin, by contrast, ends the run a
couple of seconds after EOF, which is what you want for a scripted test.

Note that a FIFO delivers as fast as the writer writes, which for a file is far
faster than 31250 baud. Use `--midi-tty` when the arrival *timing* is what you
are testing.

### 4. A Standard MIDI File — unattended runs

```sh
./desktop/build/mt32-desktop --midi-smf demo.mid --roms ~/mt32roms
./desktop/build/mt32-desktop --midi-smf demo.mid --midi-loop      # until Ctrl-C
```

Formats 0, 1 and 2, tempo changes, PPQN and SMPTE division, running status
expanded, sysex reconstructed. The file is turned into a byte stream and paced
against the same clock the render loop reads, so it goes through the parser like
anything else. Meta events are dropped; they have no wire representation.

---

## Routing DOSBox into it

**Linux**, DOSBox, DOSBox-X or DOSBox Staging, in `dosbox.conf`:

```ini
[midi]
mididevice = alsa
midiconfig = 128:0        # the client:port mt32-desktop printed at start-up
```

```sh
./desktop/build/mt32-desktop --midi-seq --roms ~/mt32roms &
dosbox -conf dosbox.conf
```

Then set the game to "Roland MT-32" or "Roland LAPC-I", not "General MIDI".

**macOS**:

```ini
[midi]
mididevice = coremidi
midiconfig = mt32-t113
```

**Anywhere, without a sequencer** — DOSBox-X can write raw MIDI to a device
file, which our FIFO is happy to be:

```ini
[midi]
mididevice = none
```
…and instead run DOSBox under a wrapper that pipes its MIDI out into
`--midi-fifo /tmp/mt32.midi`. The sequencer route is much less trouble.

---

## Audio out

```sh
./desktop/build/mt32-desktop --list-devices
./desktop/build/mt32-desktop --midi-seq --device 'USB Audio'
./desktop/build/mt32-desktop --midi-seq --audio jack        # or alsa, pulse, coreaudio
./desktop/build/mt32-desktop --midi-seq --audio null --tap-wav out.wav   # headless
```

`--audio null` plays nothing and paces in real time. It is how this build is
tested on a machine with no sound device; add `--tap-wav` to keep the audio and
listen to it somewhere else.

### Latency

The defaults are the target's numbers from `port/DESIGN.md` § 2.2: 128-frame
blocks (2.67 ms at 48 kHz) and a 3-block ring, for 8 ms of audio in flight.

```sh
--block 64 --ring 3      # 4 ms of ring; lower latency, less tolerance
--block 128 --ring 3     # the default, and the T113's configuration
--block 256 --ring 4     # 21 ms; what a busy or virtualised desktop wants
--periods 2              # device buffering behind our ring; 2 is the minimum
```

**If you get underruns with the defaults, read the summary before assuming the
render loop is slow.** It prints how long the device actually went between
asking for audio. A desktop OS under load, a VM, or PulseAudio's own buffering
can be 10 ms apart where the T113's DMA interrupt is 2.67 ms apart by
construction; a 3-block ring cannot survive that, however fast the synth is. The
summary says so explicitly when it happens, and `--ring 8` fixes it without
changing anything the target has to do.

---

## When there are no ROMs — which is the normal case

MT-32 and CM-32L ROMs are Roland's. They are not in this repository, they never
will be, and `mt32emu` checks them by SHA-1, so there is no way to fake one.

With no ROMs, `mt32-desktop` says so and falls back to the **fake engine**:
eight decaying sine voices, one internal 32 kHz clock, a deliberately small
sysex store. It is not a preview of the sound and is not meant to be. What it
*is* good for is everything except the sound:

```sh
./desktop/build/mt32-desktop --engine fake --midi-seq --ring 8
```

— the ring, the parser, sysex reassembly, back-pressure, timestamps, the audio
device, your MIDI cable and your routing all get exercised, and the counters at
the end tell you whether any of it is wrong. Wire up your keyboard with the fake
engine first; then, when the ROMs arrive, only the synthesis is new.

Once you have dumped your own ROMs:

```
~/mt32roms/MT32_CONTROL.ROM      64 kB or 128 kB
~/mt32roms/MT32_PCM.ROM          512 kB
~/mt32roms/CM32L_CONTROL.ROM     64 kB     (optional)
~/mt32roms/CM32L_PCM.ROM         1 MB      (optional)
```

```sh
./desktop/build/mt32-desktop --roms ~/mt32roms --machine mt32  --midi-seq
./desktop/build/mt32-desktop --roms ~/mt32roms --machine cm32l --midi-seq
./desktop/build/mt32-desktop --control-rom a.rom --pcm-rom b.rom --midi-seq
```

A ROM that `mt32emu` does not recognise is named and refused rather than played
as noise. That is `port/DESIGN.md` § 4.3 working, and it is the same code path
the T113 will take off the SD card.

---

## Reporting

While it runs, one line every half second:

```
  12.3s  rtf 0.081 (desktop, not the A7 gate)  ring 2  under 0  midi 148B 41m 2sx  err 0
```

At the end, the full set: underruns, minimum ring occupancy, worst single-block
render time against the block budget, how the device behaved, MIDI FIFO peak and
overruns, every parser error class separately, engine back-pressure, and the
real-time factor with a paragraph explaining what it is not.

`--status-ms 0` turns the live line off. Logs are on stderr, the summary is on
stdout, so `> run.txt` keeps the summary and leaves the log on the terminal.

The process exits non-zero if there was an underrun, so it is usable in a script.

---

## Tests

```sh
./desktop/test.sh
```

27 assertions, none of which need a sound card, MIDI hardware or ROMs: the loop
against a device that is pulling at it, the demo wire stream through stdin, a
16 kB timbre bank dump reassembled while audio flows, a stream that is malformed
in three separate ways, an SMF, `mt32emu` refusing both missing and wrong ROMs,
`--midi-seq` failing cleanly where there is no sequencer, and the serial path
driven byte-by-byte through a pty.

What they do **not** test is whether anything is audible. Nothing on a machine
with no sound device can.

---

## Licences

- **miniaudio** (`vendor/miniaudio/`) — public domain (Unlicense) or MIT-0, at
  your choice. Vendored whole, with its licence text, so this builds on a clean
  machine with no package manager involved.
- **`mt32emu`** — LGPL 2.1. This program links it statically when it is built
  in, which carries the usual obligations; the boundary is one file,
  `port/host/engine_mt32emu.cpp`, and the library is built from the unmodified
  vendored source in `bench/vendor/munt/`.
- **Everything in `desktop/src`** — 0BSD, like the rest of `port/`.
- **ROMs** — Roland's. Dump your own. Never redistribute them.
