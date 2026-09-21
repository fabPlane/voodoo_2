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
- `jtag_mailbox`: deterministic 32-bit host-to-fabric reads and writes through
  the GW5AST ER1 JTAG instruction, with the low two bits mirrored on the LEDs.
- `jtag_clock_probe`: exposes a Gray-coded free-running 50 MHz counter through
  ER1 so clocked logic can be checked numerically instead of by eye.
- `jtag_bram_probe`: writes and verifies 1,024 32-bit words in two inferred
  BSRAM blocks and reports the final state and first failing address over ER1.
- `hdmi_colorbars` (work in progress): complete open RTL for 640x480 timing,
  eight color bars, TMDS encoding, and GW5A serializers/differential outputs.
  RESET restarts the counters; holding USER blanks active video.

Build on the Linux machine from the Mac:

```sh
./build-linux.sh blinky
./build-linux.sh bram_test
./build-linux.sh uart_hello
```

The HDMI design now completes synthesis, placement, routing, and open-source
bitstream packing:

```sh
./build-linux.sh hdmi_colorbars
```

The PLL and HCLK database work is in the `fabPlane/apicula` fork. The HDMI PLL
uses independent 125 MHz and 25 MHz outputs, both routed through global clock
resources; OSER10 and the board's true-LVDS outputs also place and route. The
resulting image enters FPGA user mode with status `0x00022230`. HDMI lock and
visible color bars still require confirmation on hardware.

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

Basic combinational GPIO is now hardware-valid with the Apicula fork at commit
`9b385e3`. The fixed packer emits the GW5AST-138C READY/DONE dual-purpose-pin
encoding, and `gpio_probe_key1-fixed-open.fs` entered user mode with status
`0x00022230`; holding RESET changed the status LED color. See
`GW5AST_138C_GPIO_MILESTONE.md` for the image hash and decoded fuses.

Clocked-logic diagnostics now include `button_latch`, `reset_toggle`, and
`reset_shift`. Earlier visual tests proved that a RESET-clocked DFF can capture
a constant but could not establish whether the 50 MHz path ran. The later JTAG
clock probe supersedes that visual result: its counter advances at the expected
50 MHz rate. Any remaining blinky behavior is therefore in the LED/output path
or demo-specific logic, not the board clock itself.

The read-only JTAG boundary sampler removes visual ambiguity from autonomous
output tests:

```sh
python3 ../../../hardware/tang-console-138k/jtag_sample.py --count 20 --interval 0.1
```

Gowin Programmer operation 18 returns 616 live boundary bits. On the failing
blinky, repeated scans changed only the same two unrelated background pins as
a static design, objectively confirming that neither design output toggled.
The GW5AST user-JTAG fabric primitive is hardware-proven with the Apicula fork
at commit `cf6ab5f`. A regenerated chip database places the hard block at
`X166Y108/GW_JTAG` and connects its outputs through the adjacent `X165Y108`
routing tile. The open mailbox entered user mode with status `0x00022230`,
returned its initialized word, and passed six consecutive write/readback
transfers. The open JTAG clock probe advanced by roughly 5,000,000 counts per
100 ms sample, proving the 50 MHz input clock, counter carry/DFF path, and
JTAG readback without relying on visible LEDs. See
`../../../hardware/tang-console-138k/JTAG_MAILBOX.md` for the build, measured
results, and host-side protocol.

The template-backed database and domain-aware fuse application remain required:
ordinary logic tiles carry non-zero blank-state patterns, and configured tiles
both set and clear parts of that baseline. This milestone validates direct
GPIO and basic clocked logic. The JTAG-readable BSRAM probe also passed all
1,024 words on hardware with status `0xb5a5b3ff`, validating two inferred
physical BSRAM blocks without relying on the LEDs. PLL/HDMI and DDR3 remain
separate bring-up steps.

## Porting order

1. Establish logic, GPIO, UART, and inferred BSRAM with the smoke tests above.
   Logic, GPIO, the 50 MHz clock, and inferred BSRAM are now validated.
2. Validate the routed open TMDS color-bar generator on a display. GW5AST PLLA,
   CLKDIV, HCLK, OSER10, and true-LVDS resources are now represented well
   enough for this design to complete the open flow.
3. Add a small soft CPU demo backed entirely by BSRAM.
4. Bring up DDR3 in isolation: initialization, training, walking-bit tests,
   then sustained reads/writes. This requires an open GW5A DDR PHY as well as
   a controller; LiteDRAM can supply controller logic but does not currently
   make the device-specific PHY disappear.
5. Replace the protected USB blocks only after the basic video/memory path is
   stable. The BL616 UART remains the simplest debug/control path meanwhile.

The production console currently depends on encrypted Gowin DDR3 and USB IP.
Those sources are not referenced anywhere in this directory.
