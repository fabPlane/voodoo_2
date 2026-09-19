# Tang Retro Console 138K

Local backup of the stock SD card, plus the SpinalVoodoo splash demo for this
board.

## SD card backup (2026-09-17)

The card in the USB reader was a 31 GB FAT32 TangCore volume (`NO NAME`,
disk25). A file copy is at `sd-card-backup/` (gitignored) and a restore
tarball is `sd-card-backup-2026-09-17.tar.gz`. SHA-256 sums are in
`sd-card-backup.sha256`.

That card is the factory TangCore image: NES/SNES/GBA/Genesis/SMS/PC cores
under `cores/console138k/` and homebrew ROMs from
[retrobrews](https://github.com/retrobrews). A `voodoo/` staging folder (stills
and packed traces) is on the live card; stock cores were not replaced. There is
still no `cores/console138k/voodootang.bin` because this Mac has no Gowin EDA.

Restore:

```
tar -xzf sd-card-backup-2026-09-17.tar.gz
# then copy sd-card-backup/* onto a FAT32 card
```

## Glide SDK / other demos menu

Packed Glide2x SST-1 traces on the root list, publisher DOS demos under
OTHER DEMOS. USB pad as above. There is no RISC-V Quake on this menu; DOS
Glide games run on the host (`make native/sim/check/tomb`, or DOSBox-X on
Linux with a 32-bit libglide2x). Archives: `sw/glide-demos/`.

| Menu item | Source | Notes |
|---|---|---|
| 3DFX LOGO SPIN | `demo_logo_spin.bin` | compact chevron, loops |
| GLIDE TEST04 | `demo_test04.bin` | gouraud triangle |
| GLIDE TEST16 | `demo_test16.bin` | shameless plug / splash |
| GLIDE ALPHABET | `demo_test_alphabet.bin` | textured glyphs |
| OTHER DEMOS | `sw/glide-demos/` | submenu; info pages, no on-chip traces |

USB pad (SNES layout from BL616 command 0x09): D-pad moves, A/Start runs,
B/Select returns. Onboard USER cycles the highlight, RESET launches.

Rebuild RTL on this machine:

```
cd fpga
make compile
make test
make rtl-console
```

CoreSim already rendered the demo on this Mac:

- `demo/gouraud_coresim.png` — gouraud triangle (test04)
- `demo/splash_coresim.png` — 3dfx shameless-plug screen (test16)

`make native/sim/check/test04` from `ext/SpinalVoodoo` regenerates those PNGs.

### Bitstream (needs Gowin EDA)

This Mac does not have `gw_sh`. On a machine with Gowin EDA 1.9.10+ (educational
or commercial):

1. Generate PLL / DDR3 native / DVI TX IP as in `fpga/hw/gowin/ip.md`.
   Skip the PCIe core. Device is **GW5AST-LV138PG484AC1/I0, version C**.
2. `cd fpga && make gowin-console`
3. Copy `spinalvoodoo_tang_console_138k.bin` onto the SD card as
   `cores/console138k/voodootang.bin` (keep a copy of the stock cores from
   the backup).
4. HDMI to a 640×480-capable monitor. Plug a USB gamepad into the left
   USB-A (P1). LED0 heartbeat, LED1 lights on an info page.
5. HDMI debug: four amber rows at the top of the menu (`DBG/CAL/WED/USB`,
   case-button/HID state, BAR traffic, and BL616 UART counters). USB serial on Soft-USB S1 is optional; the onboard
   microSD is muxed to the BL616 and is not a core log.

Open-source builds are deliberately kept separate. Artifacts from
`fpga/hw/gowin-open` use the `-open` suffix, and the reserved SD-card filename
for a complete open Voodoo core is `cores/console138k/voodootang-open.bin`.
Keep `voodootang.bin` for the Gowin EDA build. Until an explicit TangCore
binary export exists, test the open `.fs` artifacts through SRAM rather than
renaming them to `.bin`.

### Isolated diagnostic mode

Hold the case **MODE** button while the Voodoo core is loaded. The FPGA samples
it for roughly 84 ms after reset and latches `DBG=1` until the next reload. In
this mode:

- MODE and RESET are displayed but cannot generate menu actions.
- The TangCore `iosys_hid` block is held in reset, so the BL616 cannot inject
  HID state or switch the Voodoo menu through that UART.
- The HUD remains visible over a playing demo.
- Both LEDs blink together during DDR calibration, alternate when healthy, and
  stay lit on a core wedge.
- FTDI channel B carries a compact, DDR-independent `V2BOOT ...` health line at
  2 Mbaud, 8N2. Soft-USB carries the fuller `V2DBG ...` snapshots once the main
  DDR/core clock domain is running.

On the Mac, attach the USB debugger port and run:

```
hardware/tang-console-138k/run_debug_uart_monitor.sh
```

Or open the native status window (live decoded fields plus the raw log):

```
hardware/tang-console-138k/run_debug_console_gui.sh
```

The debugger firmware uses its historical 4x divisor convention, so the
monitor requests 500 kbaud from FTDI to sample the 2 Mbaud FPGA wire. Add
`--hex` if the output is not readable; that distinguishes framing/baud trouble
from no FPGA traffic. Normal boots still use the BL616 TangCore protocol, and
the case MODE button remains a local FPGA menu-down pulse after the startup
window. The wrapper permanently ties `joy1` and `joy2` toward the MCU to zero.

TangCore’s menu will only list the core if the BL616 firmware sees that
filename; if it does not, program the bitstream over the MCU USB JTAG
instead (`openFPGALoader` once the Console is in GAO-bridge mode).
