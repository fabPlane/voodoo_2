# mt32-t113

An MT-32 and General MIDI sound module built on an Allwinner **T113-i**: two
Cortex-A7 cores at 1.2 GHz, a HiFi4 DSP, a RISC-V companion core, and external
DDR3 — about $4.84 of SoC plus about $6.53 of DRAM (LCSC, September 2026).

The synthesis is **Munt** (`mt32emu`), the same engine mt32-pi uses. What is new
here is the platform: not a Raspberry Pi, not Linux, but U-Boot or awboot for
silicon bring-up and an RTOS or bare-metal payload above it.

**Status: nothing is proven yet.** The gating question is whether Munt renders
faster than real time on a 1.2 GHz Cortex-A7. `bench/` answers that before any
board is designed. See `docs/PLAN.md`.

## Why this exists

Background and the full option analysis live in the parent project's
`docs/midi-module-plan.md`: why MT-32 emulation is a CPU problem rather than an
FPGA one, what mt32-pi is and why its upstream is discontinued, what a Pi costs
in the 2026 DRAM market, how much RAM a SoundFont actually needs, and the
shortlist that ended on this part.

## Layout

| Path | What |
|---|---|
| `bench/` | Munt real-time-factor harness: host build and armv7 cross build, plus footprint numbers. The go/no-go gate |
| `hw/` | Test board: minimum BOM, DDR3 and power design, boot media, JLCPCB assembly feasibility |
| `boot/` | Bring-up: U-Boot, awboot, SD boot flow, toolchain, loading a non-Linux payload |
| `port/` | The port itself: RTOS choice, I2S and DMA, MIDI UART, ROM loading, the platform layer under `mt32emu` |
| `docs/` | Plan and findings |

## Licences

`mt32emu` is LGPL 2.1; anything statically linked against it inherits obligations.
MT-32 and CM-32L ROMs are Roland's — dump your own, never redistribute them.
