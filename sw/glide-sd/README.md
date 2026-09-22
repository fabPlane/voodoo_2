# Glide playback on SD / JTAG

Overview of this path, JTAG, and software Quake: [`docs/glide-on-chip-methods.md`](../../docs/glide-on-chip-methods.md).

Pack Glide `addr:data` traces into a `V2GL` image, copy that image onto the
Tang Console FAT32 card, and (bonus) poke the same bytes into DDR over JTAG.
The console menu item **SD CARD PLAY** walks the image through `DdrTracePlayer`.

```
VOODOO trace.bin ── pack_trace.py ──▶ packed.bin (addr:u32,data:u32)
packed.bin        ── pack_v2g.py   ──▶ glide/PLAY.V2G
PLAY.V2G          ── copy_to_sd.sh ──▶ <card>/glide/PLAY.V2G
PLAY.V2G          ── jtag_load.py  ──▶ DDR @ 0x11000000
```

The onboard microSD is muxed to the BL616, so the usual path is: write the
card on a PC (or the TangCore USB mass-storage view), then JTAG-load
`PLAY.V2G` into the reserved DDR window after the bitstream is up. The
player reads that window; it does not speak FAT itself.

## Image format

Little-endian `V2GL` (see `voodoo.gowin.GlidePlayImage`):

| Offset | Field |
|---|---|
| 0 | `u32` magic `V2GL` (`0x4C473256`) |
| 4 | `u32` version `1` |
| 8 | `u32` slot count (1..16) |
| 12 | reserved |
| 16 | 16 slots of `{off, count, loopAt, flags}` (16 bytes each) |
| 272 | packed BAR writes |

DDR map (1 GiB device): Voodoo FB/tex in the first 12 MiB, soft-CPU heap at
phys `0x01000000` (256 MiB), **this image at phys `0x11000000`** (256 MiB).

## Commands

```
# from packed DemoTraceRom / homebrew roms
python3 sw/glide-sd/pack_v2g.py \
  -o /tmp/glide-sd/PLAY.V2G \
  --slot sw/glide-homebrew/roms/hb00_clear.bin \
  --slot fpga/hw/gowin/console/demo_test04.bin

# FAT32 tree (INDEX.TXT + PLAY.V2G + copies of the packed slots)
python3 sw/glide-sd/pack_v2g.py --fat /tmp/glide-sd/card \
  --slot fpga/hw/gowin/console/demo_logo_spin.bin \
  --slot fpga/hw/gowin/console/demo_test04.bin

# copy onto a mounted card
sw/glide-sd/copy_to_sd.sh /media/NO\ NAME /tmp/glide-sd/card

# JTAG / GDB payload for DDR 0x11000000
python3 sw/glide-sd/jtag_load.py /tmp/glide-sd/PLAY.V2G --out /tmp/glide-sd/jtag
```

`jtag_load.py` always writes `payload.bin` and `load.gdb` (`restore
payload.bin binary 0x11000000`). If `openFPGALoader` is on `PATH` it prints
the bitstream command and, when the probe supports an external-RAM write,
the follow-up poke. There is no USB command stream; JTAG is the host poke.

On the pad: D-pad to **SD CARD PLAY**, A/Start to run slot 0.
