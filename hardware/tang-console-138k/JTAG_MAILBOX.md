# Tang Console JTAG mailbox

This diagnostic provides deterministic host-to-FPGA communication without
depending on HDMI, buttons, UART framing, or visually judging an LED. It uses
the GW5AST fabric-visible JTAG ER1 register (JTAG instruction `0x42`) as a
32-bit mailbox. Every scan returns the previous mailbox word while installing
the next one.

The tested adapter is the Sipeed USB Debugger's FT2232H channel A
(`0403:6010`). The GW5AST IDCODE is `0x0001081b`.

## Build and load the open image

Apicula commit `bd7cbbb` describes the GW5AST `GW_JTAG` hard block and emits
the two ER enable fuses.  Build with a nextpnr chip database regenerated from
that Apicula checkout:

```sh
cd fpga/hw/gowin-open
make jtag_mailbox \
  NEXTPNR_CHIPDB=/tmp/chipdb-GW5AST-138C.bin \
  GOWIN_PACK='/home/hyper/projects/fabPlane/apicula/.venv/bin/python -m apycula.gowin_pack'
```

`PYTHONPATH` must include the same Apicula checkout.  Until the small nextpnr
PINCFG constant-wire change is upstream, the database generator must mark
`VSS`/`VCC` PINCFG inputs with their matching `const_value`; otherwise routing
stops on the local `VSS` sink even though `GW_JTAG` itself placed correctly.
The tested change is saved as
`fpga/hw/gowin-open/nextpnr-pincfg-constants.patch`; apply it from the root of
the nextpnr source tree before regenerating the `.bba` and `.bin` chipdb.

Volatile-load the resulting fully open image:

```sh
hardware/tang-console-138k/program_debug_sram.sh \
  fpga/hw/gowin-open/build/jtag_mailbox-open.fs
```

The known-good configured status is `0x00022230`.

The vendor project under `vendor-jtag-mailbox/` remains only as a routing and
fuse oracle.  Both its image and the open image set CFG-tile bits `(20,18)` and
`(20,19)`, corresponding to `JTAG_ER1=USED` and `JTAG_ER2=USED`.

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

## Open-source flow status

The host protocol is open and proven against the vendor oracle.  The open
Yosys/nextpnr/Apicula build now synthesizes one `GW_JTAG`, places it at
`X165Y108/GW_JTAG`, routes all arcs, and packs a 4.1 MiB binary without Gowin
EDA.  Hardware read/write validation of that open image still requires the
FT2232 adapter to be connected; the last attempt could not open USB device
`0403:6010`.
