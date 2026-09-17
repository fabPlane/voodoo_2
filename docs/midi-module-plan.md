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
| **A. Stock mt32-pi** | any supported Pi you can actually buy | yes | yes | no | a weekend, no PCB | $30–60, see § 2.1 |
| **B. Custom carrier** | socketed 40-pin Pi; CM4 as a variant | yes | yes | CM4 / Pi 4 only, marginal | one 4-layer board, stock image | $50–110, mostly the Pi |
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

### 2.1 There is no $25 Pi on sale

List prices and street prices have come apart, and any plan built on the list
prices is fiction. Two separate things are going on.

**The shortage.** As of 11 September 2026 the Zero 2 W was sold out at Adafruit,
CanaKit, Pimoroni and The Pi Hut; PiShop had it at $17.25, one per customer;
Amazon listed it unavailable, with third-party sellers asking over €100.
Raspberry Pi blamed AI-driven substrate demand in May 2026, has qualified a
second substrate vendor, expects improvement in the second half of 2026, and has
committed to producing the Zero 2 W until at least January 2030. The commitment
does not put one on your bench this month.

**The DRAM crisis, which is worse.** LPDDR4 has risen roughly sevenfold since
2025. Raspberry Pi has raised prices three times in four months: February took
$10 off 2 GB parts through to $60 off 16 GB ones, April added another $25 to $100
depending on density, and the 16 GB Pi 5 went from $120 to $305. A new 3 GB Pi 4
exists at $83.75 purely to have something affordable in the line. The CM4 Lite
1 GB, which this document previously recommended at its $25 launch price, now
lists at **$41.25**, and retail asks are far worse — $84.20 at one US shop, with
at least one distributor marking its line discontinued even though Raspberry Pi's
own documentation still says CM4 stays in production until at least January 2034.
This is not a Raspberry Pi problem; DRAM pricing is squeezing the whole hobbyist
SBC market, so the Orange Pi and Radxa alternatives are moving too.

The one useful asymmetry: **the old LPDDR2 parts were spared.** Raspberry Pi has
explicitly held prices on the Zero, Zero 2, Pi 1, Pi 3, 3 B+ and 3 A+ because it
holds substantial inventory of the older memory. Those are exactly the parts
mt32-pi wants. So the cheap compute is old compute — when you can find it.

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
| **CM4 Lite, 1 GB, no wireless** (CM4001000) | yes | sells through industrial distribution, so it exists — but see the price | **$41.25 list, $84 at retail.** Pi 4 silicon, so Nuked-SC55 becomes thinkable later. Two Hirose DF40 100-pin connectors — the same family, and the same 100-pin size, as the module standard in `voodoo2-modular-card.md`, so the footprint and the assembly process are ones this project already uses. Needs a microSD slot on the carrier and a 5 V rail good for about 1 A of peak |
| **Pi 3 A+** | yes | thin, but the price held | $25 list, LPDDR2, spared the hikes. 512 MB, 65 × 56 mm. Several commercial mt32-pi products ship exactly this |
| **Pi 3 B/B+, new or used** | yes | **the realistic one** | Price held (LPDDR2), and millions exist second-hand at well under list. Bigger, hotter, awkward inside a case — and available today, which beats every other row |
| **Pi 4 B** | yes | expensive now | The only supported part that could later carry SC-55 emulation, but you are paying 2026 DRAM prices for the privilege |
| **CM3+ Lite, CM4S** | untested | CM3's published end of life was “no earlier than January 2026”, so treat CM3+ as going away | Pi 3 and Pi 4 silicon in SODIMM form, and the socket is cheap. Circle supports the SoCs; mt32-pi does not list the modules. Boot one *before* laying out a board around it, and do not design a product on the CM3+ |
| **Zero 2 W via other channels** | yes | intermittent | rpilocator alerts, approved resellers, industrial distributors, used. Fine for a one-off, not for a batch |
| Orange Pi Zero 2W, Radxa Zero 3W, Geniatech XPI-3566 | **no** | good | Linux only. Munt plus FluidSynth under ALSA, your own image, your own UI |
| T113-S3, already in the rev A BOM | **no** | good | Same deal as the row above, but at least the supply risk is one you have already accepted elsewhere in this project |

**What I would do, revised for these prices:**

1. **Prove the sound for nothing.** DOSBox-Staging has Munt and FluidSynth built
   in. Point it at your ROM dump on the desktop you already own and decide
   whether you care, before buying any silicon at a shortage price.
2. **Buy used, not scalped.** A second-hand Pi 3 B or 3 B+ is supported, plentiful
   and cheap, and nobody is scalping them. Paying €100 for a $15 Zero 2 W to
   build a $40 project is the one clearly wrong move available here.
3. **Socket, do not solder, and make the socket the 40-pin header.** Every part
   that kept its price — Zero 2 W, 3 A+, 3 B+ — has that header, as do the
   Orange Pi and Radxa boards you would fall back to under Linux. Make the CM4 an
   optional variant of the carrier rather than the baseline: at $41 to $84 it is
   now a larger line item than the entire rest of the board, and that is a bet on
   DRAM prices coming back down.
4. **Re-price before you commit.** Everything in § 7 assumes compute you can buy.
   Check the day's actual asks — not list prices, not this document — at the
   moment you order.

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

### 2.3 Would FreeRTOS be better?

It is the right shape for a from-scratch MCU synth, and it is not a shortcut for
this one. The reason is that **FreeRTOS is a scheduler, not a BSP.**

Look again at the list in § 2.2 and ask which lines FreeRTOS supplies. Tasks,
queues, timers, notifications, and that is the end of it. Startup, MMU and cache,
the interrupt controller, clock trees, I²S DMA, SD/eMMC, FAT, USB host, TCP/IP
— none of that is in FreeRTOS. You get it from the vendor SDK (MCUXpresso,
STM32Cube, pico-sdk) or from TinyUSB and FatFs. That is real leverage and worth
having, but it means swapping Circle for FreeRTOS removes the easy tenth of the
port and leaves the other nine tenths exactly where they were. Circle also
already has a cooperative scheduler, so you are not gaining a concurrency model
you currently lack.

For audio specifically, an RTOS can be a step backwards. A MIDI sound module is
one hard-real-time render loop feeding a DMA ring, plus a UART or USB interrupt
dropping events into a queue. A superloop with interrupts is the lowest-jitter
way to express that; preemption adds context-switch jitter, priority inversion
and a stack per task. Where FreeRTOS earns its keep is the *other* half of
mt32-pi — USB enumeration, the network stack, FTP, the display and encoder UI,
SD access — all of which want to run without stalling the renderer.

What a FreeRTOS port would actually cost, beyond the drivers:

| Piece | Cost on an MCU |
|---|---|
| C and C++ runtime | mt32-pi gets this from `circle-stdlib` (newlib plus the STL on bare metal). Under FreeRTOS you need newlib with FreeRTOS lock hooks and a libstdc++ with a gthread shim |
| Munt | The easy one. Plain C++, no OS dependencies. But the ROMs — roughly 64 KB control plus 512 KB PCM for an MT-32, about 1 MB for a CM-32L — must be resident and randomly addressed, so they want PSRAM or memory-mapped QSPI with a cache you have thought about |
| FluidSynth | Historically needed glib. Modern versions build with `-Dosal=cpp11 -Denable-libinstpatch=0`, and glib is deprecated for removal in 2.6.0 — so the requirement becomes working C++11 threads and mutexes on your RTOS. SoundFont loading can go through custom open/read/seek/tell/close callbacks, so the font can live on SD or in flash — but note those callbacks abstract the I/O, they do not reduce residency; that needs `synth.dynamic-sample-loading` (§ 2.4) |
| USB MIDI | TinyUSB host is the realistic answer, and it is a different stack from the one mt32-pi's USB code is written against |

**But none of that is what decides it.** The decision is cycles and memory, and
the OS question is downstream of it. So settle it with a measurement, not an
architecture:

> Build `mt32emu` (Munt's library) for the candidate part with full optimisation
> and no drivers at all. Feed it a captured busy passage from a real game, render
> to a RAM buffer, and measure the real-time factor at 44.1 kHz. You want
> something like 0.5–0.6 or better on the *worst* passage, not the average, to
> leave room for USB, display and MIDI. Repeat for FluidSynth at the polyphony
> you want, with and without reverb and chorus — the effects are a large share of
> its cost. A day's work, on a dev board, before anyone routes a PCB.

My expectation, stated as an expectation: an i.MX RT1170 (Cortex-M7 at 1 GHz
with external SDRAM or HyperRAM) is the only MCU-class part I would bet on for
Munt; an STM32H7 at 480 MHz is the interesting edge case; an RP2350 at 150 MHz
and an ESP32-S3 are not candidates for LA synthesis, whatever you do to them.
The economics used to settle this argument against the MCU: a $25 Pi ran all of
it today. At 2026 prices (§ 2.1) that is no longer obviously true, and there is a
second-order effect worth noticing. **Munt's working set is small**, though not as
small as this document first claimed. Measured since, by instrumenting
`operator new` on a real build: an MT-32 in the integer renderer with reverb
preallocated is 949 KiB of synth state plus 576 KiB of ROM buffers, **1.49 MiB**;
a CM-32L is **2.49 MiB**. So an i.MX RT1170's 2 MB of on-chip SRAM holds an
MT-32 and does *not* hold a CM-32L, and the T113-i's 128 KB holds neither — that
part needs its DDR3. A synth that needs no DRAM at all is still a strategically
different thing to build in a year when DRAM is what broke everyone's bill of
materials, but it is an MT-32-only proposition on the biggest MCU in the list.
FluidSynth is the part that wants memory, and on an MCU it would have to run with
dynamic sample loading and a small font (§ 2.4) — or not at all, leaving General
MIDI to a wavetable chip.

That is an argument for *running the measurement*, not for starting the port. The
MCU route still has to be justified by something other than money — sub-second
boot, a couple of hundred milliwatts instead of a couple of watts, industrial
supply with a ten-year horizon, no SD card, no dependency on a discontinued
upstream — and it still fails immediately if the real-time factor comes out
above 1.

**Where FreeRTOS does fit this project right now:** the RP2350B already on the
Voodoo card. Give it the MIDI *control plane* — MPU-401 framing, USB MIDI host
and device, routing, configuration, display and encoder — and leave synthesis to
whatever makes the sound. That split is worth building. Porting Munt to it is
not.

### 2.4 It does not need 1 GB

Nothing in this workload wants a gigabyte. The CM4 simply has no smaller SKU —
it ships in 1, 2, 4 and 8 GB, and there is no 512 MB part — so the 1 GB in § 2.1
is a floor imposed by the product line, not a requirement. In 2026 that is
exactly the wrong thing to be forced into buying (§ 2.1): you are paying the DRAM
premium for memory the synth will never touch.

Where the memory actually goes:

| Consumer | Size |
|---|---|
| Munt control ROM | 64 KB, resident |
| Munt PCM ROM | 512 KB for an MT-32, about 1 MB for a CM-32L, resident and randomly addressed |
| Munt internal state and tables | single-digit MB (my estimate, not measured) |
| Circle kernel, heap, stacks | small. No operating system, no page cache, no swap |
| Audio DMA ring | hundreds of KB |
| **The SoundFont** | **everything else.** FluidSynth loads SF2 sample data into RAM, so the file size *is* the requirement |

So an MT-32-only box would be comfortable in about 16 MB. Everything above that
is General MIDI, and it scales with the SoundFont you choose, not with the
emulation:

- GeneralUser GS, the one mt32-pi ships, is about 30 MB.
- UGM and OmegaGMGS2 run fine on a 512 MB Pi.
- Timbres of Heaven 4.0 at 419 MB is the documented failure case: it does *not*
  load on a 512 MB Zero 2 W, because the SoundFont plus the system does not fit.

That is the whole story of the RAM axis. 512 MB — what the Zero 2 W and the
Pi 3 A+ have — covers every SoundFont people actually use bar the giants. A
gigabyte buys you Timbres-of-Heaven-class fonts and nothing else.

**Does the whole font have to be resident?** As mt32-pi ships, yes: FluidSynth
reads the sample chunk in at load time and keeps it. The configuration file
exposes the SoundFont choice, polyphony (default 200 voices), gain, reverb and
chorus — there is no memory-residency knob.

FluidSynth itself can do better, with `synth.dynamic-sample-loading`. With it on,
samples are loaded when a preset is selected on a channel and unloaded when it is
deselected and no other channel uses it, so the resident set becomes the presets
actually in play rather than the whole font. It is off by default, mt32-pi does
not expose it, and there is a decent engineering reason: a program change would
then trigger an SD read mid-performance. On bare metal, with no page cache and a
renderer that must not miss a DMA deadline, that is a dropout waiting for the
worst possible moment — a game switching instruments during a cue. Residency is
how mt32-pi buys predictable latency.

**And a correction to what an earlier revision of this document implied about
SF3:** Vorbis compression shrinks the *file*, not the footprint. FluidSynth
decompresses each sample and caches the decompressed data in memory, so a 400 MB
font stored as a 50 MB SF3 still wants roughly 400 MB of RAM once loaded. SF3
buys SD card space and load time. It only buys RAM in combination with dynamic
sample loading — and then what you are really buying is partial residency, with
the latency risk above attached.

So if you want the RAM number down, the levers in order of honesty are: pick a
smaller SoundFont; or enable dynamic sample loading and accept load-time
glitches on program change; or accept that General MIDI on this architecture
costs what the font costs.

Two consequences for this design:

1. **Buy the 512 MB parts.** They are the ones whose price held (§ 2.1), and they
   are not the compromise they look like. The part this project would actually
   want — Pi 4-class CPU with 256 MB — does not exist in the Pi line.
2. **It sharpens option C.** The T113-S3's 128 MB of in-package DDR3 is poorly
   matched to a big SF2 but perfectly adequate for MT-32 plus a compressed SF3,
   and it is old, cheap DRAM rather than the LPDDR4 that repriced everything. It
   still fails the "runs mt32-pi" test in § 2.1 — but on memory, it is closer to
   right-sized than a CM4 is.

### 2.5 Cheapest ARM that takes 512 MB of external DRAM

If the answer to § 2.1 is "then build the compute too", this is the shopping
list. Everything below is an LCSC quantity-one list price read from search
results in September 2026 — LCSC and JLCPCB are both unreachable from where this
was written, so **none of it is verified on the vendor's own page**. Re-quote
before believing any of it.

| SoC | Cores | Memory | LCSC qty 1 | Notes |
|---|---|---|---:|---|
| **Rockchip RK3308B** | 4× Cortex-A35 @ 1.3 GHz | external DDR3/DDR3L/LPDDR2/3 | **$4.08** | An audio SoC: several I2S ports, on-chip codec on some variants. Radxa's Rock Pi S is a published reference design with 512 MB on it |
| **Allwinner T113-i** | 2× Cortex-A7 @ 1.2 GHz, HiFi4 DSP, RISC-V core | external DDR3, up to 2 GB | **$4.84** | Same family as the T113-S3 already in the rev A BOM, so the sunxi work, the power sequencing and the reference designs carry over. LFBGA, industrial temperature |
| RK3308B-S / RK3308H | as above | as above | $4.43 / $5.64 | Variants worth a look when checking stock |
| **Allwinner H616** | 4× Cortex-A53 @ 1.5 GHz | external DDR3 | $6.73 | The most CPU per dollar in the list |
| **Allwinner H3** | 4× Cortex-A7 @ 1.2 GHz | external DDR3 | $7.08 | The best-trodden mainline path (Orange Pi Zero), if documentation matters more than price |
| Rockchip RK3328 | 4× A53 @ 1.5 GHz | DDR3/DDR4 | $7.38 | |
| NXP i.MX6ULL | 1× A7 | external DDR3 | ~$10.80–16.30 | Western support and documentation, single core, and the price of two Allwinners |
| *for contrast:* Allwinner T113-S3 | 2× A7 @ 1.2 GHz | **128 MB in package, fixed** | $18.39 | |

The DRAM, 512 MB being one 4 Gbit x16 DDR3L part:

| Part | LCSC qty 1 |
|---|---:|
| Micron MT41K256M16HA-125 IT:E | **$6.53** |
| Micron MT41K256M16TW-107:P | $17.55 |
| Nanya NT5CC256M16ER-EK / EKI | $19.56–$30.12 |

So the cheapest ARM-plus-512 MB pairing on this evidence is **RK3308B plus one
Micron 4 Gbit DDR3L, about $10.60 of silicon**, with **T113-i at about $11.40**
the one I would actually pick here, because its family is already in this
project's BOM.

**The finding that matters beyond this document:** the T113-S3 costs $18.39 with
128 MB soldered inside it, while the T113-i costs $4.84 and takes 512 MB beside
it for $6.53. You are paying roughly $13.50 for 128 MB of in-package DRAM, and
getting four times the memory for less by putting it outside. That is worth
re-checking against the rev A BOM's T113-S3 line for the Voodoo card itself, not
just for this side quest.

Five caveats, and they are not small:

1. **None of these run mt32-pi.** This is option C in § 2, with all of its
   software work: Buildroot, Munt, FluidSynth, ALSA, RT tuning, and your own
   control surface.
2. **Chips are not a board.** DDR3 means length-matched, impedance-controlled
   routing, six layers or a very careful four, a PMIC or three sequenced rails,
   eMMC or SD, and BGA assembly. That is a month and a respin risk, against
   soldering a 40-pin header and plugging in a Pi.
3. **Being on LCSC is not being assemblable at JLCPCB.** Check that the part is
   in the assembly library, and what the extended-part and feeder fees are,
   before designing around it.
4. **Nobody has shown Munt running well on any of these.** A quad A35 at 1.3 GHz
   and a dual A7 at 1.2 GHz are in the neighbourhood of a Pi 3, not obviously
   past it. Run the real-time-factor measurement from § 2.3 on a $20 Rock Pi S or
   a T113 board first.
5. **512 MB may be more than the workload needs** (§ 2.4) — MT-32 alone wants
   about 16 MB and 256 MB covers a decent General MIDI font — but buy it anyway.
   Checked September 2026: the 2 Gbit part (MT41K128M16JT-125, 256 MB) is
   **$10.46–12.19**, against $6.53 for the 4 Gbit part. Half the memory costs
   more than twice the memory. Do not design for 256 MB to save money.

### 2.6 RTOS support on those parts, and what actually bootstraps you

Yes, all of them have an RTOS story — but separate three layers first, because
only one of them is the hard part and it is not the kernel.

**Layer 1, silicon bring-up: you do not write this, and it is free either way.**
DDR training, clocks, PLLs, PMIC sequencing and boot media are the chip-specific
work, and on both candidate families it already exists as something you consume:

- **Rockchip RK3308.** U-Boot's TPL stage loads a prebuilt DDR init binary from
  Rockchip's `rkbin` repository (`rk3308_ddr_589MHz_*.bin`) plus an ATF BL31.
  Closed blobs, but they mean DDR comes up without you understanding it.
- **Allwinner T113.** Mainline U-Boot has supported it since v2024.01, and there
  are small purpose-built loaders — `awboot` and `xboot` — that do DRAM init and
  load a payload off SD or SPI flash in a fraction of a second.

**The pattern that follows:** let U-Boot or awboot bring the chip up, then have
it load *your* payload — RTOS or plain bare metal — instead of a kernel. You
inherit the worst of § 2.2's list for free and still do not run Linux. That is
the real answer to "give us some of the bootstrapping".

**Layer 2, the kernel.** FreeRTOS, RT-Thread or Zephyr. Worth little on its own,
for the reasons in § 2.3.

**Layer 3, drivers and middleware — where the actual work is:**

| Stack | On these parts | Verdict |
|---|---|---|
| **RT-Thread** | The strongest fit. Allwinner's own **Melis** RTOS is built on it, there are D1 and T113 ports, and its package ecosystem covers filesystems, audio and USB | The one I would start from on a T113 |
| **Melis** (Allwinner) | Vendor RTOS with a HAL for Allwinner peripherals, RT-Thread underneath | Useful as a driver source; documentation is mostly Chinese |
| **FreeRTOS** | A community bare-metal T113 port exists (`robots/allwinner_t113`) | A starting point, not a BSP. You would be writing the drivers |
| **Zephyr** | Good driver model, but Cortex-A support is thin and neither of these SoCs is a supported target | Do not plan on it |
| **Rockchip RTOS SDK** | Exists for the audio parts, vendor-gated | The public RK3308 path is U-Boot plus Linux |

**The decision rule, which is sharper than it first looks.** After U-Boot has
bootstrapped you, what remains is: I²S with DMA, SD and FAT, a UART, and — only
if you want it — **a USB host stack**. USB is the expensive one, and Linux is the
only place you get it for free.

- **Drop USB MIDI and the RTOS path is genuinely tractable.** A WaveBlaster
  daughterboard fed by this project's own MPU-401 decode over TTL serial (§ 3)
  does not need USB at all. I²S plus DMA, FAT on SD, one UART, and a render loop
  is a weekend-scale driver list on top of RT-Thread, not a month.
- **Keep USB MIDI, RTP-MIDI or the FTP upload, and take Linux.** A stripped
  Buildroot image on a T113 boots in a second or two, and you spend your time on
  audio instead of on enumeration.

Two part-specific notes worth having before either path:

- The **RK3308 has an embedded audio codec** — 8× ADC, 2× DAC, two 8-channel
  I²S/TDM ports, PDM and S/PDIF in and out. That can remove the external DAC
  from the BOM entirely, or keep a PCM5102A for the line output and use the
  on-chip path for monitoring. Its DDR controller is 16-bit DDR2/DDR3/DDR3L or
  LPDDR2 at 1066, so one x16 4 Gbit part is exactly the right shape (§ 2.5).
- The **T113-i carries a RISC-V core and a HiFi4 DSP** beside the two A7s. The
  tidy split is the control plane (MIDI parsing, UI, display) on the small core
  under RT-Thread while the A7s render — tidy, and almost certainly more
  complexity than this project needs on day one.

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

Compute is now the dominant line item and the one that moves weekly (§ 2.1), so
it is priced separately from the board it sits on.

| Build | Board parts, qty 5 | Plus compute | Est. total |
|---|---|---|---|
| Stock mt32-pi, off the shelf | DAC module, opto board, SD: ~$15 | used Pi 3 B/B+ $15–25, or a Zero 2 W at whatever it costs that day | $30–60 |
| Daughterboard, 40-pin socket | PCB and assembly ~$12, DAC $3, header, level shifter, passives ~$7 | any supported Pi | $37–60 |
| Bracket module | above plus DIN, jacks, OLED, encoder, USB-C, bracket: ~$35 | any supported Pi | $50–75 |
| CM4 variant of the carrier | above plus 2× DF40 ~$5, microSD, extra layers | **CM4 Lite $41–84** | $80–130 |

The last row is the whole argument of § 2.1 in one line: at today's prices the
CM4 costs more than everything else on the board put together.

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
- **Pi supply and price.** The Zero 2 W is chronically out of stock, the DRAM
  crisis has repriced everything with modern memory on it, and nothing outside
  mt32-pi's short supported list is a drop-in (§ 2.1). Socket the compute, make
  the 40-pin header the primary footprint, treat the CM4 as a variant, and
  re-quote on the day you order rather than trusting any price in this document.
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
bumps; mt32-pi's submodule set (circle-stdlib, munt, fluidsynth, inih) and
FluidSynth's `osal=cpp11` escape from glib; the 2026 DRAM-driven price rises,
the CM4 Lite's $41.25 list and $84 retail asks, the exemption of the older
LPDDR2 models, CM4 production running to at least January 2034 and its 1 GB
memory floor, and the SoundFont-in-RAM behaviour including the 419 MB font that
will not load on a 512 MB Pi; FluidSynth's `dynamic-sample-loading` semantics and
its caching of decompressed SF3 samples; mt32-pi's FluidSynth configuration
surface; September 2026 LCSC list prices for the RK3308/T113/H3/H616/RK3328/
i.MX6ULL families and for 4 Gbit DDR3L parts, read from search results rather
than from LCSC itself, which is unreachable here; the RTOS and bootloader
situation on both families — Melis on RT-Thread, the community FreeRTOS T113
port, mainline U-Boot since v2024.01, awboot and xboot, and Rockchip's rkbin DDR
blob and BL31 requirement — and the RK3308's on-chip codec and memory support; the September 2026 Zero 2 W stock picture and Raspberry Pi's supply
statements and production commitment.

Not verified, and to be checked before any layout: the WaveBlaster pinout above
(against a real card, not a web table), the exact audio level a period sound
card's wavetable input wants, the 5 V headroom on the host card's rail, the
today's actual asks for whichever Pi you buy (every price here has a shelf life
of about a week), whether mt32-pi boots on a CM3+ Lite or a CM4S, and
whether Munt is comfortable on a single
Cortex-A7 at 1.2 GHz if option C is ever revisited.
