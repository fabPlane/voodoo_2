#!/bin/sh
# Bring up external DDR3 on a T113-i over USB FEL, then optionally load and run
# a bare-metal payload from DRAM.
#
# Why this is not just `xfel ddr t113-i`:
#   xfel only knows t113-s3, t113-s4 and r528-s3, all of which are the
#   CO-PACKAGED memory parts.  Its DDR init works by uploading an eGON.BT0
#   image to SRAM at 0x28000, overwriting the dram_para_t structure at offset
#   0x38 of that image, and executing it.  We do the same three steps by hand
#   with our own parameters.  See ../BRINGUP.md section 2.
#
# Usage:
#   fel-ddr-t113i.sh                       # DRAM init only
#   fel-ddr-t113i.sh payload.bin           # DRAM init, then load+run at 0x40000000
#   PRESET=t113i-tronlong fel-ddr-t113i.sh # use the other published T113-i set
#   XFEL_SRC=/path/to/xfel fel-ddr-t113i.sh
#
# Requires: xfel on PATH (https://github.com/xboot/xfel, MIT), python3, and an
# xfel source checkout to lift the DDR payload from (or a prebuilt payload in
# payloads/t113-ddr.bin).

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
BOOT=$(cd "$HERE/.." && pwd)
PRESET=${PRESET:-mt32-t113}
LOAD_ADDR=${LOAD_ADDR:-0x40000000}
PAYLOAD_DIR="$BOOT/payloads"
BASE="$PAYLOAD_DIR/t113-ddr.bin"
OUT="$PAYLOAD_DIR/${PRESET}-ddr.bin"
XFEL_SRC=${XFEL_SRC:-$BOOT/vendor/xfel}

mkdir -p "$PAYLOAD_DIR"

if [ ! -f "$BASE" ]; then
	src="$XFEL_SRC/chips/r528_t113.c"
	[ -f "$src" ] || {
		echo "no $BASE and no xfel source at $src" >&2
		echo "clone it:  git clone https://github.com/xboot/xfel $XFEL_SRC" >&2
		exit 1
	}
	echo "==> extracting DDR payload from $src"
	python3 "$HERE/ddrpara.py" extract "$src" -o "$BASE" >/dev/null
fi

echo "==> stamping preset '$PRESET' into the payload"
python3 "$HERE/ddrpara.py" patch "$BASE" --preset "$PRESET" -o "$OUT"

echo "==> checking the board is in FEL mode"
xfel version

echo "==> uploading DDR init payload to SRAM 0x28000 and running it"
xfel write 0x00028000 "$OUT"
xfel exec 0x00028000

echo "==> DRAM should now be live; probing 0x40000000"
xfel hexdump 0x40000000 0x20 || true

if [ $# -ge 1 ]; then
	echo "==> loading $1 to $LOAD_ADDR"
	xfel write "$LOAD_ADDR" "$1"
	echo "==> exec $LOAD_ADDR"
	xfel exec "$LOAD_ADDR"
fi

echo "done."
