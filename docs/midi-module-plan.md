# MIDI module plan — moved

The MIDI sound module work has its own repository:
**https://github.com/mattkanwisher/open_midi_hardware**. Everything that was in this file
— why MT-32 emulation is a CPU problem rather than an FPGA one, what mt32-pi is,
what a Raspberry Pi costs in the 2026 DRAM market, how much RAM a SoundFont
actually needs, and the part shortlist that ended on the Allwinner T113-i — now
lives in its own repository, **mattkanwisher/open_midi_hardware**, as
`docs/background.md`, alongside the plan, the hardware research, the bring-up
notes and the code.

What is there now: the background analysis, the plan and its gate, the Cortex-A7
benchmark harness, the T113-i hardware research and BOM, the bring-up notes, the
platform layer, a bare-metal image that boots under QEMU with mt32emu rendering
on it, and a desktop build you can plug a MIDI keyboard into.

It stays relevant to this card in one place: the Voodoo 2 card decodes MPU-401 at
330h in fabric and has expansion pins budgeted for MIDI, so it can feed such a
module over two wires at 3.3 V TTL with no cable, no DIN and no optocoupler. See
`voodoo2-artix-card-plan.html`, expansion section.
