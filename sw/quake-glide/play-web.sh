#!/usr/bin/env bash
#
# Play GLQuake on the Tang dock from a browser: the HDMI output is DMA-streamed
# to http://<host>:8090/ and keys/mouse from that page are fed back to the game.
# Hold the board lock for the whole session:
#
#   fpga/hw/gowin/board-lock -m "glquake web" -- sudo sw/quake-glide/play-web.sh [quake args]
#
# PORT (8090) is the web port, INPUT_PORT (27961) the local UDP port for input,
# RECORD=file.mp4 also records the stream, THROTTLE= the DDR3 throttle (see
# below). Ctrl-C stops everything.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$HERE/../pcie"
PORT="${PORT:-8090}" INPUT_PORT="${INPUT_PORT:-27961}"
DEV="${DEV:-$(lspci -D -d 22c2: | cut -d" " -f1 | head -1)}"

[ -d /sys/module/voodoo_dma ] || insmod "$P/voodoo_dma/voodoo_dma.ko"
setpci -s "$DEV" COMMAND=0006   # memory + bus master for the frame DMA
"$P/voodoo-stream" --setup-only
# DDR3 throttle (window 31 0x64, B43+): [7:0] idle cycles after every DDR3
# command, [11:8] max reads in flight. Faster settings reload the FPGA under
# GLQuake on this dock; 0x160 (gap 96, 1 read) survived a 15-min soak.
THROTTLE="${THROTTLE:-0x160}"
python3 - "$DEV" "$THROTTLE" <<'PY'
import mmap, os, sys
fd = os.open('/sys/bus/pci/devices/%s/resource0' % sys.argv[1], os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 1 << 20, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
mv = memoryview(m).cast('I')
m[0x250] = 31                      # byte write: select the board-local window
mv[0x64 >> 2] = int(sys.argv[2], 0)
print('ddr throttle %08x' % mv[0x64 >> 2])
m[0x250] = 0
PY

rec=()
[ -n "${RECORD:-}" ] && rec=(--record "$RECORD")
"$P/voodoo-stream" --attach 2>/dev/null |
  python3 "$P/voodoo-view.py" --port "$PORT" --input-port "$INPUT_PORT" "${rec[@]}" &
view=$!
trap 'kill $view 2>/dev/null; pkill -x voodoo-stream; wait' EXIT

QUAKE_INPUT_UDP="$INPUT_PORT" "$HERE/run-pcie.sh" "$@"
