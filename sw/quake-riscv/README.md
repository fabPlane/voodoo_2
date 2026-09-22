# RISC-V Quake (software + Glide)

How this sits next to SD / JTAG Glide replay: [`docs/glide-on-chip-methods.md`](../../docs/glide-on-chip-methods.md).

One WinQuake-class engine, two video backends. That is the Glide port: add
SST-1 to the RISC-V build rather than maintaining a second game.

| Menu item | Backend | Draw path |
|---|---|---|
| QUAKE RISC-V SW | `quake-sw` | 320×200 8-bit FB, HDMI scanout |
| QUAKE RISC-V GLIDE | `vid_glide.c` | SST-1 MMIO into `CoreGowin` BAR |

USB pads (TangCore `hid1`, SNES layout) map in `in_usb.c`: D-pad move, A fire,
B jump, X/Y look, Start/Select menu.

## Engine

Software mode is [sysprog21/quake-embedded](https://github.com/sysprog21/quake-embedded)
(WinQuake, GPL-2.0) plus a Tang board under `port/tang/`. Their `rv32emu` sound
path uses emulator `scall`s; we link `snd_null.c` instead. Display is the same
320×200 8-bit buffer HDMI will sample (`qembd_fillrect` → `VID_SwFramebuffer`).

```
git submodule update --init sw/quake-riscv/quake-embedded
cd sw/quake-riscv && make shareware && make test && make test-engine
make engine-rv32          # Andes A25 ELF (rv32imafdc / ilp32d)
make install-sd           # copy ELF onto the Console FAT32 card
```

`make engine` is a host `quake-sw`. `make test-engine` runs a few frames of
shareware `demo1.dem`. `make engine-rv32` uses the xPack `riscv-none-elf-gcc`
under `.toolchains/` if it is not on PATH. Entry is `0x80000000` with **256 MiB**
of DDR (phys 16 MiB); shareware `pak0.pak` is a 32 MiB window at `0x90000000`.
The 32 KiB `BarRv32` BRAM is only a smoke ROM. The board has ~1 GiB of DDR3 —
this map spends 256 MiB on the software renderer and 256 MiB on V2GL replay
(`0x11000000`). Live HDMI play still needs the AE350 A25 (or a bigger soft
RV32) wired onto that DDR window in the bitstream.

## Layout

```
quake-embedded/         submodule: WinQuake + quakembd port API
port/tang/              320×200 SW, USB pad, A25 crt0/syscalls
include/voodoo_mmio.h   SST-1 16 MiB map + write log
include/fxhal.h         Glide2x ISET/IGET → sst_wr / sst_rd
include/sst1_regs.h     SST-1 register offsets
include/in_usb.h        BL616 HID → Quake buttons
include/vid_riscv.h     VID_Init(VID_SW | VID_GLIDE) host_probe stubs
include/pak.h           shareware PACK reader
src/                    backends + host_probe
id1/                    make shareware → pak0.pak + demo1/2/3.dem
build-rv32/quake-sw     A25 ELF + .bin
```

Game data is the official Quake 1.06 shareware (`id1/pak0.pak`): Episode 1
plus the recorded WinQuake demos `demo1.dem`, `demo2.dem`, `demo3.dem`.
`make shareware` fetches it from the id FTP mirror. Do not vendor registered
`pak1.pak`.

Glide2x lives in `ext/SpinalVoodoo/emu/glide/glide2x/sst1`. Hardware access is
`fxhal` (`sst_wr` / `sst_rd` on the 16 MiB BAR, not PCI). Bind Glide2x with
`ISET` → `fxHalPoke` and `IGET` → `fxHalPeek`. `VID_GLIDE` already goes through
that HAL (FBI init, gouraud `ftriangleCMD`, `swapbufferCMD` at 0x128).

The Console fabric does not instantiate the Andes A25 yet, so the FPGA menu
item **QUAKE RISC-V SW** opens an info page. The TangCore bitstream is
`cores/console138k/voodootang.bin`; the A25 image sits next to it at
`voodoo/quake/quake-sw.elf`. Packed Glide replays use **SD CARD PLAY** and
`sw/glide-sd/`.
