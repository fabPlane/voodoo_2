# A dedicated MIDI sound module, mt32-pi class

Status: exploration, 2026-09-17. No commitment, no BOM ordered. Written as a
companion to the Voodoo 2 card work because the card already decodes MPU-401 at
330h and already carries a game/MIDI header (`voodoo2-artix-card-plan.html`,
expansion section), so a MIDI module is either a separate small board or a
daughtercard on the same family. Prices below are rough order-of-magnitude
figures from memory of distributor pricing, **not** the researched LCSC/JLCPCB
numbers the rev A BOM uses. Treat them as estimates until quoted.

## 1. What mt32-pi actually is, and what that implies

mt32-pi is a bare-metal Raspberry Pi image (built on the Circle framework, no
Linux) that turns a Pi into a MIDI sound module. Two synthesis engines run
inside it:

- **Munt**, a software emulation of the Roland MT-32 / CM-32L. It needs the
  original control and PCM ROM images, which the project does not ship; you dump
  them from hardware or supply them yourself.
- **FluidSynth**, a SoundFont sampler for General MIDI / GS. GeneralUser GS
  ships with the image.

Inputs: USB MIDI interfaces, a GPIO UART MIDI circuit, plain serial, and network
MIDI (RTP-MIDI and raw UDP). Outputs: I²S DAC (recommended) or the PWM headphone
jack (audibly worse, known aliasing). It drives an LCD/OLED for MT-32 display
messages and part levels, takes buttons and a rotary encoder, and carries an FTP
server for dropping SoundFonts in. Supported silicon: Pi Zero 2 W, Pi 3 A+/B/B+,
Pi 4 B, CM4. Pi 2 runs with reduced quality; Pi 1 and the original Zero cannot.
Licence: GPLv3.

Three consequences fall out of that, and they decide the whole design:

1. **This is a CPU problem, not an FPGA problem.** Munt is a behavioural model
   of the LA32 plus the MT-32's firmware ROM, not a register-level netlist you
   can drop into fabric. A cycle-level MT-32 in an FPGA would mean an MCS-96
   (8095) core plus the LA32 gate array plus the same ROMs; I am not aware of a
   finished open core for it. Every shipping MT-32 emulator is Munt on a CPU.
2. **The hard work is done and is GPLv3.** The value you add by building a board
   is packaging, connectors and integration — not synthesis. Design so that a
   stock mt32-pi image boots on it, and treat every deviation as a cost.
3. **Prior art is commercial, and it is all the same software.** Serdaco's WP32
   "McCake" already puts mt32-pi on a WaveBlaster daughterboard, their MP32L and
   TM32 ship mt32-pi configs, and half a dozen other boards do the same thing in
   different boxes (§ 10). Build this because you want it in *this* project's
   shape, not because nothing exists. Note also that upstream mt32-pi is no
   longer actively developed — the live line is the `metaneutrons` fork (§ 10).

## 2. The option ladder

| | Compute | MT-32 (Munt) | GM (FluidSynth) | SC-55 (Nuked-SC55) | Effort | Est. cost |
|---|---|---|---|---|---|---|
| **A. Stock mt32-pi** | Pi 3 A+ or Zero 2 W | yes | yes | no | a weekend, no PCB | ~$40 |
| **B. Custom carrier** | CM4 Lite, or a socketed Zero-format board | yes | yes | CM4 only, marginal | one 4-layer board, stock image | ~$55–80 |
| **C. T113-S3 board** | 2× A7 1.2 GHz, Linux | likely, unproven here | RAM-limited | no | you write the software platform | ~$45 |
| **D. RP2350** | 2× M33 150 MHz | no | small SF2, reduced polyphony | no | firmware project | ~$15 |
| **E. FPGA fabric** | Artix/Gowin | no | sample engine possible | no | large | — |

Notes behind the table:

- **Nuked-SC55** is cycle-accurate SC-55 emulation and costs roughly one whole
  Raspberry Pi 4 core at stock 1.8 GHz (reports put the work thread at 85–90 %
  with the UI off). If Roland SC-55 matters to you, that requirement, not MT-32,
  sets the CPU. A Zero 2 W will not do it.
- **Option C** is tempting because the T113-S3 is already qualified in the rev A
  BOM at $18, has an in-package 128 MB of DDR3, native I²S and an audio codec.
  The catch: mt32-pi is bare-metal Pi code, so on a T113 you are running Linux
  with Munt and FluidSynth under ALSA instead — a different project, with boot
  time, RT scheduling and xrun tuning as your problem. And 128 MB caps the
  SoundFont: FluidSynth loads samples into RAM, so GeneralUser GS (~30 MB) fits
  and FluidR3 or Arachno (~150 MB) do not. A Zero 2 W's 512 MB does not have
  that problem.
- **Option D** is the PicoGUS shape: an MCU can do a credible GM sampler and
  OPL, and cannot do MT-32. Useful as a cheap second product, not as the answer.
- **Option E** is where fabric genuinely earns its keep: the host-bus side (the
  MPU-401 UART, SB/Adlib port decode), OPL3 — open cores exist (gtaylormb's
  `opl3_fpga`, jotego's JT cores) — a sample-playback engine and a
  sample-accurate mixer and I²S master. Keep LA synthesis on a CPU.

**Recommendation: A, then B.** Get a supported Pi, an I²S DAC and an
optocoupler this week and prove the thing you actually want to hear. Then spend a
board spin turning that pile into the connector set below. Do not start at C or E.
Which Pi, given that the Zero 2 W is hard to buy, is the next section.

### 2.1 When the Zero 2 W is out of stock

It usually is. As of 11 September 2026 Adafruit, CanaKit, Pimoroni and The Pi Hut
were all sold out; PiShop had it at $17.25, one per customer. Raspberry Pi blamed
AI-driven substrate demand in May 2026, has qualified a second substrate vendor,
expects supply to improve in the second half of 2026, and has committed to
producing the Zero 2 W until at least January 2030. So this is a shortage, not an
end of life — which is cold comfort if you want to build something this month.

The rule that decides every substitution: **mt32-pi boots on Zero 2 W, Pi 3 A+,
Pi 3 B/B+, Pi 4 B and the CM4 series, and nothing else.** Step off that list and
you are not running mt32-pi at all — you are building your own Linux appliance
around Munt and FluidSynth, and inheriting boot time, RT scheduling, xruns, and a
control and display UI that mt32-pi otherwise gives you for free. Pi 5 and CM5 are
not on the official list either; Circle reportedly runs on a Pi 5, and the
actively maintained `metaneutrons/mt32-pi` fork is where to check whether that has
turned into real support.

| Substitute | Runs mt32-pi | Availability | Notes |
|---|---|---|---|
| **CM4 Lite, 1 GB, no wireless** (CM4001000) | yes | best in the range — it sells through industrial distribution, not the hobby channel | $25. Pi 4 silicon, so Nuked-SC55 becomes thinkable later. Two Hirose DF40 100-pin connectors — the same family, and the same 100-pin size, as the module standard in `voodoo2-modular-card.md`, so the footprint and the assembly process are ones this project already uses. Needs a microSD slot on the carrier and a 5 V rail good for about 1 A of peak |
| **Pi 3 A+** | yes | usually stocked | $25, 512 MB, 65 × 56 mm. Several commercial mt32-pi products ship exactly this. The quickest unblock for phase 1 |
| **Pi 3 B+ / Pi 4 B** | yes | good | Bigger, hotter, more power draw; awkward as an internal daughterboard. The Pi 4 is the only one on the list that could later carry SC-55 emulation |
| **CM3+ Lite, CM4S** | untested | good, and the DDR2 SODIMM socket is cheap | Pi 3 and Pi 4 silicon in SODIMM form. Circle supports the SoCs; mt32-pi does not list the modules. Buy one and boot it *before* you lay out a board around it |
| **Zero 2 W via other channels** | yes | intermittent | rpilocator alerts, approved resellers, industrial distributors, used. Fine for a one-off, not for a batch |
| Orange Pi Zero 2W, Radxa Zero 3W, Geniatech XPI-3566 | **no** | good | Linux only. Munt plus FluidSynth under ALSA, your own image, your own UI |
| T113-S3, already in the rev A BOM | **no** | good | Same deal as the row above, but at least the supply risk is one you have already accepted elsewhere in this project |

**What I would do:** use a **Pi 3 A+** to get sound out of it this week, and
design the board around a **CM4 Lite** — it is the most reliably purchasable part
on the supported list, it is Pi 4 class, and its connector is one this project is
already committed to. Then hedge cheaply: a 40-pin GPIO header footprint costs
nothing but board area, and it keeps the design alive for any Zero-format board,
Pi or otherwise, if the CM4 ever goes the way of the Zero 2 W.

### 2.2 Why not port mt32-pi to a different ARM chip?

Because the part that is Pi-specific is not the part you would think.

mt32-pi is three things: **Munt**, **FluidSynth** and **Circle**. The two synth
engines are ordinary portable C++ — they already build for x86, ARM Linux and
macOS, and nothing about LA synthesis cares what silicon it runs on. Circle is a
complete bare-metal environment for the Raspberry Pi, and mt32-pi calls its
classes directly. There is no vendor-neutral HAL seam in the middle to
re-implement.

So "port it to another ARM chip" means writing, for the new SoC, all of:

- reset and startup, MMU and cache configuration, exception vectors;
- the interrupt controller, timers, and the clock/PLL setup that gets you an
  exact 22.05/44.1/48 kHz I²S bit clock;
- a DMA-driven I²S audio backend that never underruns;
- SD/eMMC plus a FAT stack, because the ROMs, SoundFonts and `mt32-pi.cfg` live
  on a card;
- **a full USB host stack**, for USB MIDI — this is the big one;
- an Ethernet driver and TCP/IP, if you want the RTP-MIDI and FTP features;
- GPIO, I²C and SPI for the display, buttons and encoder.

That is a board support package, and it is months, not a weekend. For scale:
the community fork's jump from Circle 45.1 to 50.1 is what it takes merely to
*follow* the Pi family, and Pi 5 support within that same family is still marked
experimental.

The cheaper move, if you are set on different silicon, is not to port mt32-pi
but to replace it: a Buildroot image with `munt` and `fluidsynth` on ALSA. The
engines come across for free. What you give up is what mt32-pi's bare-metal
design buys — cold boot in seconds, no Linux audio configuration, predictable
latency — and what you take on is RT scheduling, xruns, and writing your own
control-surface and display layer. That is exactly option C in § 2, priced
honestly: it is not a port, it is a new product with borrowed synth engines.

The one case where a real port is defensible: a chip that already has a mature
bare-metal BSP with a working USB host stack and I²S DMA (an i.MX RT or an
STM32MP, say), where you are gluing rather than writing drivers. Even then you
would be maintaining a second port of a project whose upstream has stopped
(§ 10), on your own, forever.

## 3. How it talks to the host

Five paths, and the last one is the interesting one for this project.

| Path | Electrical | Notes |
|---|---|---|
| MIDI DIN in | current loop, opto-isolated | The universal one. Feed it from a game-port MIDI cable or a real MPU-401 |
| Game port MIDI cable | same, via DB15 | What a 1998 PC with a Sound Blaster actually has |
| WaveBlaster header | 5 V TTL serial at 31 250 baud + analog stereo back into the host card's mixer | The neat one: module lives inside the PC, no cables, one 2×13 0.1" header |
| USB MIDI | class-compliant device or host | For a modern PC driving it |
| Network | RTP-MIDI / raw UDP | Free with mt32-pi if there is an Ethernet PHY |
| **Direct FPGA link** | 3.3 V TTL serial | The Voodoo card already decodes MPU-401 at 330h in fabric. Hand the module the 31.25 kbaud stream on two expansion pins — no DIN, no opto, no cable |

The WaveBlaster pinout, as commonly published (verify against a known-good board
before you lay out a footprint; the Elektor-era drawings mirror the numbering):
odd pins 1–25 ground except 13; MIDI IN on 4; +5 V on 6, 10, 14; +12 V on 18;
−12 V on 22; right audio out 20, left audio out 24; reset on 26; 2, 8, 12, 13,
16 unused.

Three electrical details that bite on that header:

- **MIDI is 5 V TTL, not a current loop.** No optocoupler, but you must level
  shift to the Pi's 3.3 V RX — a 74LVC1G17 or a divider, not a bare wire.
- **The audio is expected at the host mixer's line input.** A PCM5102A puts out
  about 2.1 V RMS full scale, which is hot for that input; plan roughly 10–12 dB
  of attenuation and DC blocking, and set the mixer's "wavetable" level from
  DOS before you conclude the board distorts.
- **Power comes off the sound card's 5 V, i.e. straight off the slot.** A Zero
  2 W idles a couple of hundred mA and peaks around half an amp at boot. That is
  within what the rail gives, but put bulk capacitance and a soft-start load
  switch on it so the inrush does not brown out a 1995 sound card.

## 4. Board sketch

```
  MIDI DIN in ──[H11L1]──┐
  WaveBlaster MIDI ──[LVC1G17]──┤
  FPGA expansion TTL ────────────┼──> UART RX ┌──────────────┐   I2S   ┌──────────┐
                                 │            │   CM4 Lite   │ ──────> │ PCM5102A │
  USB MIDI ──────────────────────┼──> USB     │ (or Pi 3 A+) │         └────┬─────┘
                                              │  mt32-pi     │              │
  microSD ────────────────────────────────────┤  bare metal  │      RC + DC block
                                              └──┬────────┬──┘         ├─> 3.5 mm TRS
  4 buttons + encoder ───────────────> GPIO ─────┘        └── I2C ──> SSD1306 OLED
                                                                      └─> WaveBlaster
                                                                          audio pins
```

Parts worth naming now:

| Block | Part | Why |
|---|---|---|
| Opto MIDI in | H11L1 (Schmitt output) | 6N138 is the classic and is in the card's BOM, but it is slow and asymmetric at 31.25 kbaud; H11L1 gives clean edges for free |
| DAC | PCM5102A | What the mt32-pi community uses, needs no software configuration, ~$1.5–3 |
| Display | SSD1306 128×64 I²C | mt32-pi renders MT-32 LCD text and part-level bars on it |
| Controls | 4 buttons + EC11 encoder | Synth select, SoundFont cycle, volume |
| Power | 5 V in from USB-C **or** the WaveBlaster header, load switch, 3 A-rated bulk | Both inputs diode-OR'd or switched, never both driving |

## 5. Form factors

The same board, three builds, in order of increasing cost:

1. **Daughterboard.** 2×13 WaveBlaster header pointing down, module sits over a
   sound card. No DIN, no jacks, no display. This is the "it disappears into the
   PC" build.
2. **Bracket module.** A slot-cover-width board with DIN in/thru, 3.5 mm line
   out and the OLED facing the back of the case, fed from a spare Molex or the
   card's 5 V.
3. **Desk box.** The bracket build plus a case, USB-C power and a real volume
   pot. This is what you use with a laptop and a USB MIDI keyboard.

## 6. Where it meets this project

The honest answer is that it does not *need* the Voodoo card, and the Voodoo
card does not need it. What is real:

- The card's expansion group is already budgeted for "serial, MIDI, Pmods". Two
  pins of it carry the MPU-401 stream straight to this module. That removes the
  opto, the DIN and the cable from the retro-PC path, which is the single
  biggest usability win available here.
- The combo-card plan already contemplates PicoGUS-style Adlib/SB/MPU-401 behind
  the FPGA's legacy decode. OPL3 in fabric plus MT-32 on a small CPU beside it
  is a complete DOS audio story from one slot — but it is a *later* phase, and
  the audio path (mixing, codec, output filtering, shielding next to a PCI
  graphics card) is a whole engineering problem of its own. Do not fold it into
  the Voodoo rev A/B schedule.
- The T113-S3 on the full card could in principle run Munt and FluidSynth under
  Linux. Treat that as an experiment to try once the T113 boots at all, not as a
  reason to design the MIDI module around it.

## 7. Rough cost

| Build | Parts | Est. |
|---|---|---|
| Stock mt32-pi, off the shelf | Pi 3 A+ (or Zero 2 W) + DAC module + opto board + SD | ~$40 |
| Daughterboard, qty 5 | CM4 Lite $25 + DF40 pair $5, PCB+assembly ~$12, DAC $3, passives $5 | ~$50 |
| Bracket module, qty 5 | above + DIN, jacks, OLED, encoder, USB-C, bracket | ~$55–65 |
| Socketed 40-pin variant | Pi 3 A+ or any Zero-format board $15–25, header and standoffs $2 | ~$45–60 |

## 8. Phases

1. **Prove the sound.** Whatever supported Pi you can actually buy (§ 2.1),
   mt32-pi, your own MT-32 ROM dump, a game-port MIDI cable into a real DOS
   machine. — *exit: Sierra and LucasArts titles sound right, and you know the
   latency you can live with.*
2. **Daughterboard spin.** 4-layer, WaveBlaster header, TTL level shift, DAC and
   output attenuator, no display. Stock mt32-pi image, unmodified. — *exit: it
   plays through an SB16's mixer with the lid on.*
3. **Bracket build.** Add DIN in/thru, line out, OLED, encoder, USB-C power,
   config via `mt32-pi.cfg`. — *exit: usable without a host sound card at all.*
4. **Card integration.** Two expansion pins from the Voodoo card's MPU-401
   decode into the module's UART; drop the opto on that path. — *exit: one PCI
   card and one small board, no MIDI cable in the case.*
5. **Optional, later.** OPL3 core in fabric and a mixer, so the FPGA covers
   Adlib/SB while this board covers MT-32 and GM.

## 9. Risks and rules

- **ROMs.** MT-32 control and PCM ROMs are Roland's. Dump your own; never ship
  them on an SD card with a board you sell or give away. Same care with
  SoundFonts whose licences forbid redistribution.
- **GPLv3.** mt32-pi is GPLv3. If you modify it for this board — a different
  GPIO map, a different display — publish the changes with the board.
- **Pi supply.** The Zero 2 W is chronically out of stock (§ 2.1) and nothing
  outside mt32-pi's short supported list is a drop-in. Design around the CM4
  Lite, keep the compute socketed rather than soldered, and add the 40-pin header
  footprint as a second escape route.
- **Analog next to digital.** Inside a PC, on a card's 5 V, beside a graphics
  card, a clean line out is work: star ground, keep the DAC's ground return away
  from the Pi's, and expect to iterate the output filter.
- **Scope.** This is a side quest to the Voodoo card. Phase 1 costs $40 and a
  weekend and answers most of the question. Everything after that is packaging.

## 10. The field: what else exists

Worth knowing before you build, because most of what looks like competition is
not.

**Almost every "MT-32 emulator" product is mt32-pi in a different box.** Serdaco's
WP32 McCake (a CM4 on a WaveBlaster board — note that it reached the same
conclusion about the CM4 that § 2.1 does), their MP32L and TM32, tebl's
open-source BulkyMIDI-32, Bits und Bolts' MIDI FORGE Maestro (Zero 2 W,
WaveBlaster, onboard screen and four buttons, files on PCBWay), the MiSTer minis
from Ultimate MiSTer and MiSTer Addons, and a shelf of Tindie HATs. They differ
in connectors, case and price, not in synthesis. That is also the honest read on
what this project would be adding.

**Upstream mt32-pi is discontinued.** The author has said he is unlikely to
release further updates, citing sustained harassment, code theft and design
plagiarism by people in the community, and the effect that had on his health.
The software is stable and very usable; it is just not moving. The live fork is
`metaneutrons/mt32-pi`: Circle 45.1 → 50.1, FluidSynth 2.3.1 → 2.5.3, Munt
2.7.3, GCC 14.3, SF3 (Vorbis-compressed) SoundFonts, MIDI Thru, NEON
vectorisation, and Pi 5 support marked experimental. Build against that fork,
and budget for the possibility that you end up maintaining your own.

Genuinely different approaches:

| | What it is | Where it fits |
|---|---|---|
| **DreamBlaster S2 / X2 / X16** | Real wavetable silicon (SAM2695 and successors) on a WaveBlaster board | Instant on, no ROMs, no legality question, cheap. GM/GS — its "MT-32 mode" is an approximation, not emulation |
| **Nuked-SC55** | Cycle-accurate Roland SC-55 family: SC-55mk1/mk2/st, CM-300, SCC-1, SCB-55, SC-155, JV-880 | The best Sound Canvas there is, at about one Pi 4 core. **Upstream archived 12 September 2026**, relicensed GPL2, development stopped; the README points at forks, jcmoyer's in particular. If SC-55 is part of your plan, plan on a fork |
| **EmuSC** | SC-55 emulation driven from the ROM sample data rather than chip-level | Cheaper on CPU, less exact. The pragmatic SC-55 option for weak silicon |
| **PicoGUS** | RP2040 ISA card: GUS, SB16/SB Pro 2 OPL3, AdLib, CMS/Game Blaster, Tandy 3-voice, and MPU-401 *intelligent mode* with real MIDI out | The complement, not the competitor — it is the host-interface half. Combo products literally pair a PicoGUS with an mt32-pi |
| **Host-side software** | DOSBox-Staging with Munt and FluidSynth built in, Nuked-SC55 as a CLAP plugin, VirtualMIDISynth or the Munt VSTi on a modern PC, SoftMPU on the DOS machine for intelligent-mode MPU-401 | Free, and it answers "do I like this sound?" before any hardware exists |
| **The real thing** | An MT-32, CM-32L or SC-55 on eBay | You need a ROM dump from somewhere anyway, and nothing emulates the analog output stage |

And the negative result that matters for this project: **there is still no
finished FPGA MT-32.** The MiSTer community's answer to MT-32 is to bolt a
Raspberry Pi onto the MiSTer. That is a fair summary of the state of the art,
and it is why § 1 says to keep LA synthesis on a CPU.

## 11. What is checked, and what is not

Verified from source while writing: mt32-pi's supported Pi models, engines,
input/output options, display and control support, ROM policy and licence, from
its README; the Nuked-SC55 CPU figures from its optimisation discussions, and
its archival, relicensing and end of development in September 2026; Serdaco's
WP32 McCake as existing CM4-based WaveBlaster mt32-pi hardware; upstream
mt32-pi's discontinuation and the `metaneutrons` fork's changes and version
bumps; the September 2026 Zero 2 W stock picture and Raspberry Pi's supply
statements and production commitment.

Not verified, and to be checked before any layout: the WaveBlaster pinout above
(against a real card, not a web table), the exact audio level a period sound
card's wavetable input wants, the 5 V headroom on the host card's rail, the
current Pi prices and stock, whether mt32-pi boots on a CM3+ Lite or a CM4S, and
whether Munt is comfortable on a single
Cortex-A7 at 1.2 GHz if option C is ever revisited.
