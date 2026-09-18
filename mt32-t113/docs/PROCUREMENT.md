# Things to buy

Nothing here is ordered. This is the list, with what each purchase unblocks, so
it can be acted on in one sitting. Prices are September 2026 readings from
`hw/HARDWARE.md`; re-quote before ordering, and note that DRAM-bearing parts are
moving 10–20 % a month.

## 1. The gate: any T113 board, today

The real-time-factor measurement does not care which T113 variant it runs on —
the cores are the same dual Cortex-A7 at 1.2 GHz. **Do not wait for a T113-i
part to close the gate.** The cheapest board that boots Linux answers it.

| Item | Why | Est. |
|---|---|---|
| A T113-S3 board — DongshanPI, MangoPi MQ-R, 100ask | Run `bench/rtf` on real A7 silicon. This is the whole project's go/no-go | $15–25 |
| microSD, 8 GB+ | Boot media | $5 |
| USB-C cable, and a 3.3 V USB-serial adapter | Console, and FEL recovery | $10 |

## 2. The T113-i path: SoM plus its dev board

Workstream B's recommendation is a vendor module on our own carrier rather than
our own BGA layout, because the module vendor has already answered the two
questions we cannot read: the power-up sequence and the DDR3 address/command
routing (`hw/HARDWARE.md` §§ 2.1, 5.4).

| Item | Why | Est. |
|---|---|---|
| **Forlinx FET113i-S SoM** (T113-i, 256 MB DDR3, 256 MB NAND) | The module our carrier would host | $15–23 |
| **Forlinx OK113i-S dev board** | Proves the module and ships a working U-Boot with a known-good DRAM parameter set and AC remapping table | $62–75 |
| Alternative: MYIR MYC-YT113i SoM (512 MB or 1 GB) and MYD-YT113i board | Second source; MYIR's own SoM is 6-layer, which is evidence for our carrier | $19.80–31.80 / board TBD |

Buy the SoM **and** its dev board together. The board is what makes the module
useful before our carrier exists, and it is the artefact that settles the
remapping question — one `xfel read32 0x03006228` on arrival.

## 3. Audio and MIDI front end, for the bench

| Item | Why | Est. |
|---|---|---|
| PCM5102A module (or the bare IC, C107671) | I²S DAC, the output path | $2–4 |
| H11L1S optocoupler (C78589) ×2 | MIDI in. Schmitt output, in spec at 3.3 V | $0.50 |
| MIDI DIN breakout or a game-port MIDI cable | Feed it from a real DOS machine | $10 |

## 4. Not purchasable

**MT-32 or CM-32L ROM dumps.** Roland's, and the gate cannot be closed without
them — `bench/rtf` refuses to run otherwise, and mt32emu checks them by SHA-1, so
an approximation will not do. Dump your own from hardware you own. These never
enter this repository.

## On arrival, in order

1. `xfel read32 0x03006228` — bits [11:8] are the AC remapping selector. 13 or 14
   means stock mainline works with no patch (`hw/HARDWARE.md` § 5.4).
2. `bench/rtf` on the busiest MT-32 score you have, pinned to one core, governor
   at performance, at 32 kHz and again at 48 kHz.
3. The four hardware PDFs (`hw/HARDWARE.md` § 7 items 1 and 7) from a network
   that can reach whycan, 100ask and Forlinx — this session cannot.
