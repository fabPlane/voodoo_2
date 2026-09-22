# Getting Glide (and Quake) onto the chip

Three host-to-board methods, after the Wine / MiniGL / `trace_test` path on
the PC. None of those PC traces drive Gowin HDMI by themselves.

| Method | What it does | Live play? | Status |
|---|---|---|---|
| **1. SD card replay** | Copy a packed Glide stream onto the FAT32 card, then run **SD CARD PLAY** | No — canned BAR writes | Menu + player + pack tools |
| **2. JTAG load** | Poke the same image into DDR at `0x11000000` after the bitstream is up | No — same player as (1) | `jtag_load.py` + `load.gdb` |
| **3. Software Quake on the soft CPU** | WinQuake software renderer in a 256 MiB DDR window | Yes, once A25 / larger RV32 is wired | Host probe works; HDMI info page |

USB is **not** a command pipe. Soft-USB S1 is TX-only debug (`VoodooDebug`).
USB-A is HID pads. Streaming a GLQuake capture over USB 1.1 is not viable.

Related: `sw/glide-sd/README.md` (commands), `sw/quake-riscv/README.md`
(engine), `docs/tangcore-integration.md` (TangCore layout),
`fpga/README.md` (console menu).

---

## Memory map (≈1 GiB DDR3)

Voodoo keeps the bottom of the device. The rest is split so a full shareware
pak and a large replay both fit.

| Region | Phys | CPU view | Size | Owner |
|---|---|---|---|---|
| Colour + depth | `0x00000000` | — | 4 MiB | `CoreGowin` FB |
| Textures | `0x00400000` | — | 8 MiB | TMU |
| Pad | `0x00C00000` | — | 4 MiB | — |
| Soft CPU / A25 RAM | `0x01000000` | `0x80000000` | 256 MiB | Quake ELF, heap, stack |
| V2GL replay | `0x11000000` | — | 256 MiB | SD / JTAG image |
| Shareware pak window | (loader) | `0x90000000` | 32 MiB | `pak0.pak` |

Constants live in `fpga/src/voodoo/gowin/GowinAddressMap.scala` and
`sw/quake-riscv/include/tang_mem.h`. The 32 KiB `BarRv32` BRAM is only a
smoke ROM; it cannot hold the game.

---

## Method 1 — SD card Glide playback

The console does not execute x86. HDMI Glide is a **replay**: packed
`addr:u32, data:u32` BAR writes walked by an AXI-Lite master.

On-chip BRAM (`DemoAxiLitePlayer`) only holds the compact SDK traces (logo,
test04, test16, alphabet). Full game captures do not fit. Method 1 puts those
captures in a `V2GL` file on the TangCore FAT32 volume and plays them from DDR.

### Pipeline

```
Glide app (SDK / homebrew / GLQuake)
  → SIM_TRACE_FILE VOODOO trace.bin
  → sw/glide-homebrew/tools/pack_trace.py   (addr:data ROM)
  → sw/glide-sd/pack_v2g.py                 (PLAY.V2G)
  → copy_to_sd.sh                           (<card>/glide/…)
  → JTAG or later DMA into DDR 0x11000000
  → menu SD CARD PLAY → DdrTracePlayer → CoreGowin BAR → HDMI
```

### Card layout

```
<card>/
  cores/console138k/voodootang.bin   # TangCore bitstream (unchanged)
  glide/
    PLAY.V2G                         # image the player walks
    INDEX.TXT                        # human list of slots
    traces/*.bin                     # optional copies of the packed ROMs
```

The onboard microSD is muxed to the BL616. This core does not mount FAT.
Write the tree on a PC (or the TangCore USB mass-storage view). The player
only reads the DDR window.

### V2GL image

Little-endian; see `voodoo.gowin.GlidePlayImage`.

| Offset | Field |
|---|---|
| 0 | `u32` magic `V2GL` (`0x4C473256`) |
| 4 | `u32` version `1` |
| 8 | `u32` slot count (1..16) |
| 12 | reserved |
| 16 | 16 slots of `{off, count, loopAt, flags}` (16 bytes each) |
| 272 | packed BAR writes (`addr:u32, data:u32`, same as `DemoTraceRom`) |

Up to 16 slots. **SD CARD PLAY** runs slot 0.

### Commands

```
python3 sw/glide-sd/pack_v2g.py \
  -o /tmp/glide-sd/PLAY.V2G \
  --fat /tmp/glide-sd/card \
  --slot fpga/hw/gowin/console/demo_test04.bin \
  --slot sw/glide-homebrew/roms/hb00_clear.bin

sw/glide-sd/copy_to_sd.sh "/media/NO NAME" /tmp/glide-sd/card
```

On the pad: D-pad to **SD CARD PLAY**, A/Start to run, B/Select to return.

### What this is not

- Not a live Glide host. MiniGL / Mesa still run on the PC to *capture*.
- Not USB BAR streaming.
- A 10 s GLQuake capture is on the order of hundreds of MiB of packed writes;
  stay inside the 256 MiB play window or shorten the capture.

---

## Method 2 — JTAG load (bonus)

Same `PLAY.V2G`, different transport. After `voodootang` is configured, poke
the image into the play window and use the same menu item.

```
python3 sw/glide-sd/jtag_load.py /tmp/glide-sd/PLAY.V2G --out /tmp/glide-sd/jtag
```

Always written:

| File | Role |
|---|---|
| `payload.bin` | raw V2GL bytes |
| `addresses.txt` | base `0x11000000`, size, sha256 |
| `load.gdb` | `restore payload.bin binary 0x11000000` |

If `openFPGALoader` is on `PATH`:

```
openFPGALoader -c ft2232 <console.fs>
```

If that build advertises an external-RAM write, the script prints a poke at
`--offset 0x11000000`. Otherwise use GDB `restore` on the A25 / soft CPU
once it can see DDR.

The Console debugger port / GAO-bridge flow is the same as
`hardware/tang-console-138k/README.md`. There is no USB RX path that becomes
AXI-Lite writes.

---

## Method 3 — Software Quake on the soft CPU

Port the game, not the Glide stream. One WinQuake-class engine
(`sw/quake-riscv`), software backend first.

| Menu item | Backend | Draw path |
|---|---|---|
| QUAKE RISC-V SW | `vid_sw.c` + `port/tang/display.c` | 320×200 8-bit FB |
| QUAKE RISC-V GLIDE | `vid_glide.c` + `fxhal` | SST-1 MMIO into the BAR |

### Why 256 MiB (of 1 GiB)

The old note that “BarRv32 is too small” is still true for the **32 KiB BRAM**
core. The board DDR is about 1 GiB. This map gives the CPU 256 MiB at
`0x80000000` (ELF, 64 MiB heap, 64 KiB stack) and a 32 MiB pak window at
`0x90000000`. Shareware `pak0.pak` is ~18 MiB.

### Host (works now)

```
git submodule update --init sw/quake-riscv/quake-embedded
cd sw/quake-riscv
make shareware          # official Quake 1.06 → id1/pak0.pak (gitignored)
make test               # host_probe: SW + Glide HAL + pak demos
make engine
make test-engine        # a few frames of demo1.dem; QUAKE_SW_PPM=sw_last.ppm
```

`host_probe` prints the DDR map when the shareware pak is present.

### On-chip (not wired yet)

```
make engine-rv32        # Andes A25 ELF, entry 0x80000000
make install-sd         # → <card>/voodoo/quake/quake-sw.elf
```

Needs the AE350 A25 (or a larger soft RV32) on the AXI bus with that map,
plus HDMI scanout of the 320×200 buffer. Until then the console item
**QUAKE RISC-V SW** is an info page.

USB pads (TangCore `hid1`, SNES layout) are already decoded in `in_usb.c`:
D-pad move, A fire, B jump, X/Y look, Start/Select menu.

Wine / MiniGL / `_grFoo@N` does not come along. That is x86. The Glide
backend on RISC-V is `fxhal` (`sst_wr` / `sst_rd` on the 16 MiB BAR).

---

## Menu (console HDMI)

Root list in `DemoCatalog`:

1. 3DFX LOGO SPIN — BRAM player
2. GLIDE TEST04 — BRAM player
3. GLIDE TEST16 — BRAM player
4. GLIDE ALPHABET — BRAM player
5. **SD CARD PLAY** — `DdrTracePlayer`, slot 0 of the V2GL window
6. **QUAKE RISC-V SW** — info until the CPU is wired
7. OTHER DEMOS — publisher DOS titles (info; still too big for BRAM)

Pad: D-pad move, A/Start run, B/Select back. USER / RESET are the
on-board fallback.

---

## What to use when

- **See a captured Glide frame on HDMI** — method 1 + 2: pack a short
  homebrew or SDK ROM, JTAG-load, **SD CARD PLAY**.
- **See a long GLQuake capture on HDMI** — same, but keep the packed size
  under 256 MiB (or add more slots / a later DMA from the BL616).
- **Play Quake on the board** — method 3, software first; Glide-on-RISC-V
  after the A25 (or bigger soft core) can `sst_wr` the BAR.
- **Do not** expect Wine traces or USB serial to drive the Voodoo core live.
