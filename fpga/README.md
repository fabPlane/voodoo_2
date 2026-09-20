# SpinalVoodoo FPGA ports

Board-specific packages for SpinalVoodoo. The Voodoo core itself is the upstream
[SpinalVoodoo](https://github.com/fayalalebrun/SpinalVoodoo) source, pulled in from
`ext/SpinalVoodoo`; nothing upstream is modified.

| Target | Package | Host | Memory | Video |
|---|---|---|---|---|
| AMD Artix-7 AC701 (XC7A200T) | `voodoo.artix` | AXI PCIe BAR0 → AXI4-Lite | two AXI4 masters into MIG | parallel RGB → ADV7511 |
| Sipeed Tang Mega 138K Pro Dock | `voodoo.gowin` | Gowin PCIe TLP BAR0 → AXI4-Lite | AXI4 → Gowin DDR3 native UI | parallel RGB → DVI_TX_Top |
| Sipeed Tang Retro Console 138K | `voodoo.gowin` | USB-pad Glide SDK tests + Tomb info | AXI4 → Gowin DDR3 native UI | parallel RGB → DVI_TX_Top |

An Amaranth (Python HDL) re-implementation of this tree is in progress under
`../amaranth/`; see `../docs/amaranth-rewrite-plan.md`. It does not touch this
tree, and the Spinal build stays authoritative until Phase 7 parity.

Upstream SpinalVoodoo only has the DE10-Nano (`CoreDe10`). `CoreArtix` /
`CoreGowin` are the ports of that wrapper. The Artix board top (`AmdAc701`)
is LiteX `xilinx_ac701`: 16 MiB BAR0, textures relocated to 4 MiB in the same
DDR3. The Gowin board top (`TangMega138kPro`) adds a TLP BAR target and a 1 MiB
window at 0x250 (Gowin BARs max out at 1 MiB). Vendor IP is instantiated in
`hw/*/src/board_top.v`, not in SpinalHDL.

## Tools

- JDK 17 (fetched by scala-cli from `//> using jvm`), scala-cli, Verilator 5.x.
- SpinalHDL 1.15.0.
- Gowin EDA (`gw_sh`) plus the IP cores in `hw/gowin/ip.md` for a Gowin bitstream.
- Vivado plus the IP cores in `hw/artix/ip.md` for an AC701 bitstream.

```
make compile      # type-check both ports
make rtl          # Artix + Gowin Verilog
make rtl-artix    # rtl/artix/AmdAc701.v
make rtl-gowin    # rtl/gowin/TangMega138kPro.v
make rtl-console  # rtl/gowin/TangConsole138k.v
make test         # SpinalSim, including the TLP BAR target
make artix        # Vivado bitstream (after generating IP)
make gowin        # Gowin EDA bitstream (after generating IP)
make gowin-console # Retro Console 138K Glide-test bitstream
```

Program the AC701 with Vivado or `openFPGALoader`. Program the dock with the
Gowin programmer or `openFPGALoader -c ft2232`. The Console demo needs Gowin
EDA; copy the resulting `.bin` to `cores/console138k/voodootang.bin` on the
SD card. Open-source experiments from `hw/gowin-open` use distinct `*-open`
build artifacts; a future complete open core is reserved
`cores/console138k/voodootang-open.bin` and must not overwrite the vendor-built
core. See `hardware/tang-console-138k/README.md`.

## Gowin / Tang Mega 138K Pro

Device: `GW5AST-LV138FPG676AES` (`GW5AST-138B`). Cold-plug the PCIe finger;
the Sipeed demo does not hot-plug.

Colour, depth and textures all live in the module DDR3 (the dock has no
HyperRAM): 4 MiB framebuffer at 0, 8 MiB textures at 4 MiB.

Host map: BAR0 is 1 MiB. Write the megabyte index to byte 0x250, then access
the 1 MiB window. 0x254 is the same never-hang status word as DE10/Artix.

HDMI uses the dock DVI TX pins. LiteX found DVI TX dead on some boards and
drove DVI RX instead; if the monitor stays dark, swap the TMDS locations in
`hw/gowin/tang_mega_138k_pro.cst`.

LEDs (active low): heartbeat, PCIe link, DDR calib, host-bridge wedged.

## Gowin / Tang Retro Console 138K

Device: `GW5AST-LV138PG484AC1/I0` (`GW5AST-138C`). HDMI pins from working
Console cores. USB gamepads come from the BL616 (TangCore UART command 0x09,
SNES-layout `hid1`/`hid2`); onboard USER/RESET are a fallback. After DDR3
calibration a text menu offers packed Glide2x SDK traces (logo spin, test04
gouraud, test16 splash, alphabet) plus an OTHER DEMOS submenu of publisher
DOS Glide titles. Those captures are too big for on-chip ROM; archives are
in `sw/glide-demos/`. Replay Tomb with `make native/sim/check/tomb` from
`ext/SpinalVoodoo`. DOSBox-X live Tomb needs the game directory and a 32-bit
`libglide2x` host (Linux).

```
make rtl-console
make gowin-console   # needs gw_sh and hw/gowin/ip as in ip.md (no PCIe IP)
```

USB pad: D-pad moves, A/Start runs, B/Select back. USER cycles, RESET runs.

LEDs (active low): heartbeat, info page open.

HDMI debug: the menu has two amber status rows at the top (`CAL= WED= USB=`
and `PC= BAR= N=`). That is the bring-up log when S1 does not enumerate.
The onboard microSD is muxed to the BL616; this core does not write it.

USB serial debug: the Soft-USB Type-C (silkscreen **S1**) enumerates as a
full-speed CDC ACM gadget (`VoodooDebug`) on the analog `USB_DX` pair. Plug
that jack into the Mac, then:

```
ls /dev/cu.usbmodem*
screen /dev/cu.usbmodem* 115200
```

Lines look like:

```
t=00A1B2C3 cal=1 wed=0 ov=1 pl=0 inf=0 cur=0 pc=80000000 bar=0000021C:00110000 n=0002 hid=0000 usb=1
```

`cal` is DDR3 calibration, `wed` is the host BAR wedge, `pc` is the BRAM
RV32, `bar` is the last AXI-Lite write, `n` is write count.

The gadget is Gowin USB 1.1 SoftPHY + Device Controller, glued like Sipeed's
`usb_uart_loopback` (Apache-2.0), on Console S1 `USB_DX` (J19/H19) with the
1.5 kΩ D+ pull-up enable on M17. The analog HS SoftPHY did not route on this
fabric. USB-A P1/P2 stay pad hosts.

## Artix-7 200T / AMD AC701

Device: `xc7a200t-fbg676-2`. Pinout from LiteX `xilinx_ac701` (PCIe x4, 32-bit
slice of the 64-bit DDR3 SODIMM, ADV7511 HDMI). Cold-plug the PCIe card.

Colour, depth and textures all live in the board DDR3: 4 MiB framebuffer at 0,
8 MiB textures at 4 MiB.

Host map: BAR0 is 16 MiB, so the Voodoo map is 1:1. 0x254 is the same never-hang
status word as DE10.

HDMI is 24-bit RGB into the ADV7511 (UG952 D4–D11 / D16–D23 / D28–D35 map:
B, G, R). `adv7511_i2c.v` selects PCA9548 channel 5 then plays the Analog
power-up. Pixel clock is 25.175 MHz from the 156.25 MHz oscillator (640x480).

LEDs: heartbeat, PCIe link, DDR calib, host-bridge wedged.

- `io.memFb` and `io.memTex` are AXI4 masters without ID signals (one source after the burst
  fixer). Route both through an AXI interconnect into MIG (`hw/artix/ip.md`).
- Every memory burst is aligned and extended to 256 bytes (`CoreArtix.axiBurstLog2`). Correct,
  simple, and wasteful for single-pixel writes; a boundary-splitting bridge is the follow-up.
- `ArtixGenSupport` emits synchronous active-high resets for the whole design, which is what
  MIG and the PCIe block provide.
- Untouched from upstream: `Config`, all of `Core`, the Verilator harness and Glide tests.
