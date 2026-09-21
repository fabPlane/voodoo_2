#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
BITSTREAM=${1:-"$REPO_ROOT/fpga/hw/gowin/console/impl/pnr/spinalvoodoo_tang_console_138k.fs"}
PROGRAMMER=/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA/Programmer/bin/programmer_cli

if [ ! -x "$PROGRAMMER" ]; then
    echo "Gowin Programmer CLI not found at $PROGRAMMER" >&2
    exit 1
fi
if [ ! -f "$BITSTREAM" ]; then
    echo "Bitstream not found: $BITSTREAM" >&2
    exit 1
fi
BITSTREAM_DIR=$(CDPATH= cd -- "$(dirname -- "$BITSTREAM")" && pwd)
BITSTREAM="$BITSTREAM_DIR/$(basename -- "$BITSTREAM")"

echo "Keep both case buttons RELEASED while SRAM programming completes." >&2
echo "Holding MODE prevents the GW5AST from entering user mode (status 0x00004000)." >&2
exec "$PROGRAMMER" \
    --device GW5AST-138C \
    --operation_index 2 \
    --cable-index 4 \
    --frequency 2.5MHz \
    -f "$BITSTREAM"
