# Open-source GW5AST-138C experiments

This directory is deliberately independent from `../gowin`. It explores a
Yosys + nextpnr-himbaechel + Apicula flow for the Tang Retro Console 138K
without changing the known-good Gowin EDA build.

## Open-core naming

Every bitstream produced here carries an `-open` suffix, for example
`blinky-open.fs` and `hdmi_colorbars-open.fs`. A future complete Voodoo core
from this flow is reserved the SD-card name
`cores/console138k/voodootang-open.bin`. The Gowin EDA core keeps the existing
name `voodootang.bin`; do not overwrite it with an open-flow experiment.

Use SRAM loading for the first hardware check. `make bin` then packs each
textual Apicula `.fs` bitstream into the binary representation used by the
TangCore SD loader. The demo filenames remain distinct (`blinky-open.bin`,
`bram_test-open.bin`, and `uart_hello-open.bin`); none claims the reserved
complete-core name.

## Current demos

- `blinky`: counter, reset key, user key, and the two active-low LEDs.
- `bram_test`: inferred 1024x32 memory, destructive write/read self-test, and
  pass/fail LEDs. This is the first check that GW5AST BSRAM mapping works.
- `uart_hello`: sends `OPEN GOWIN 138K OK` once per second at 115200 baud on
  the FPGA-to-BL616 UART. Press USER to send immediately.
- `hdmi_colorbars` (work in progress): complete open RTL for 640x480 timing,
  eight color bars, TMDS encoding, and GW5A serializers/differential outputs.
  RESET restarts the counters; holding USER blanks active video.

Build on the Linux machine from the Mac:

```sh
./build-linux.sh blinky
./build-linux.sh bram_test
./build-linux.sh uart_hello
```

The HDMI design currently passes Yosys synthesis:

```sh
./build-linux.sh hdmi_colorbars-synth
```

The PLL database work is now in the `fabPlane/apicula` fork. All twelve PLLA
BELs are visible and the HDMI PLL places successfully. Full HDMI placement is
currently stopped at the next missing device resource:

```text
ERROR: Unable to place cell 'u_pixel_div', no BELs remaining to implement cell type 'CLKDIV'
```

`make all` therefore keeps the three buildable smoke-test bitstreams green
while the HDMI source and synthesis checkpoint remain available for continued
PLL chip-database work. A real 640x480 TMDS output needs 125 MHz serial and
25 MHz pixel clocks, so bypassing the PLL with the board's 50 MHz oscillator
is not a usable hardware workaround.

Or, with a recent OSS CAD Suite already on `PATH`:

```sh
make -j3
```

Open-flow outputs are written to `build/*-open.fs`. Programming is intentionally not
automatic: these are experimental bitstreams and should first be loaded into
SRAM using the board's MODE-button procedure.

After SRAM testing, generate separately named SD-card images with:

```sh
make bin
```

With the Console connected to the Mac, hold its MODE button and run, for
example:

```sh
../../../../hardware/tang-console-138k/program_debug_sram.sh build/blinky-open.fs
```

This currently uses Gowin's programmer only as the transport. The generated
bitstream itself comes entirely from Yosys, nextpnr, and Apicula. A future
openFPGALoader target can replace the transport after its Console cable path is
verified.

## Hardware status and regression reference

The minimal vendor reference in `vendor-reference/` builds the same `blinky.v`
and pin constraints with Gowin EDA. It is deliberately kept separate from the
open outputs and is useful only for bitstream-delta and hardware transport
checks. On a GW5AST-138C it SRAM-loads successfully and alternates the two
status-light channels.

The open image is not hardware-valid yet. The header, SSPI/MSPI GPIO options,
and first-frame CRC now match the working reference, but the FPGA still reports
status `0x20` after loading. Comparing an empty vendor image showed the next
chip-database blocker: every ordinary logic tile carries a 280-bit blank-state
pattern that the current 138C database does not model. Configured tiles both
set and clear portions of that baseline, so simply ORing or XORing a blank
template is insufficient. Apicula needs domain-aware fuse application for the
routing, slice, I/O-bank, and configuration tables before these demos should be
copied to an SD card or described as hardware-tested.

## Porting order

1. Establish logic, GPIO, UART, and inferred BSRAM with the smoke tests above.
2. Finish GW5AST-138C PLL placement/packing upstream, then place the existing
   open TMDS color-bar generator. CLKDIV/OSER10/ELVDS already synthesize.
3. Add a small soft CPU demo backed entirely by BSRAM.
4. Bring up DDR3 in isolation: initialization, training, walking-bit tests,
   then sustained reads/writes. This requires an open GW5A DDR PHY as well as
   a controller; LiteDRAM can supply controller logic but does not currently
   make the device-specific PHY disappear.
5. Replace the protected USB blocks only after the basic video/memory path is
   stable. The BL616 UART remains the simplest debug/control path meanwhile.

The production console currently depends on encrypted Gowin DDR3 and USB IP.
Those sources are not referenced anywhere in this directory.
