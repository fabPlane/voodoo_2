# Tang Console JTAG mailbox

This diagnostic provides deterministic host-to-FPGA communication without
depending on HDMI, buttons, UART framing, or visually judging an LED. It uses
the GW5AST fabric-visible JTAG ER1 register (JTAG instruction `0x42`) as a
32-bit mailbox. Every scan returns the previous mailbox word while installing
the next one.

The tested adapter is the Sipeed USB Debugger's FT2232H channel A
(`0403:6010`). The GW5AST IDCODE is `0x0001081b`.

## Load the oracle image

The current mailbox image is built by Gowin EDA because Apicula does not yet
emit the GW5AST `GW_JTAG` hard block. Build it on the Linux build machine:

```sh
cd fpga/hw/gowin-open/vendor-jtag-mailbox
gw_sh build_jtag_mailbox.tcl
```

Volatile-load the resulting image:

```sh
hardware/tang-console-138k/program_debug_sram.sh \
  fpga/hw/gowin-open/vendor-jtag-mailbox/impl/pnr/vendor_jtag_mailbox.fs
```

The known-good configured status is `0x00022230`.

## Read, write, and run timed sequences

```sh
# Read without changing the final value
python3 hardware/tang-console-138k/jtag_mailbox.py --read

# Write and immediately verify by readback
python3 hardware/tang-console-138k/jtag_mailbox.py --write 0xa5c35a7e

# Alternate mailbox bits 1:0 four times at 150 ms per step
python3 hardware/tang-console-138k/jtag_mailbox.py \
  --sequence 0,3 --count 4 --interval 0.15
```

The sequence command checks that every scan returns the value written by the
preceding scan and exits nonzero on a mismatch. The mailbox's low two bits
also drive the two active-low LED outputs, but visible LED behavior is not
part of the pass/fail decision.

## Scan framing

Gowin's `GW_JTAG` primitive registers the serial path. A logical 32-bit
payload therefore uses a 34-bit OpenOCD data scan with one low padding bit:

```text
IR = 0x42
wire_value = payload << 1
logical_readback = (wire_readback >> 1) & 0xffffffff
```

The OpenOCD adapter/TAP definition is in `openocd-gowin-ft2232.cfg`. The FPGA
side is `fpga/hw/gowin-open/rtl/jtag_mailbox.v`.

## Open-source flow work remaining

The host protocol is fully open and proven. The remaining Apicula task is to
describe the GW5AST `GW_JTAG` hard-block location, internal wires, and enable
fuses so nextpnr can route this same RTL without the Gowin vendor tools. The
vendor image above is the oracle for differential bitstream and routing
analysis.
