# MIDI module plan — moved

The MIDI sound module work has its own project. Everything that was in this file
— why MT-32 emulation is a CPU problem rather than an FPGA one, what mt32-pi is,
what a Raspberry Pi costs in the 2026 DRAM market, how much RAM a SoundFont
actually needs, and the part shortlist that ended on the Allwinner T113-i — now
lives in `mt32-t113/docs/background.md`, alongside the plan, the hardware
research, the bring-up notes and the code.

It stays relevant to this card in one place: the Voodoo 2 card decodes MPU-401 at
330h in fabric and has expansion pins budgeted for MIDI, so it can feed such a
module over two wires at 3.3 V TTL with no cable, no DIN and no optocoupler. See
`voodoo2-artix-card-plan.html`, expansion section.
