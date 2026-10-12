#!/usr/bin/env bash
#
# Play GLQuake on the Tang dock from a browser: the HDMI output is DMA-streamed
# to http://<host>:8090/ and keys/mouse from that page are fed back to the game.
# Hold the board lock for the whole session:
#
#   fpga/hw/gowin/board-lock -m "glquake web" -- sudo sw/quake-glide/play-web.sh [quake args]
#
# PORT (8090) is the web port, INPUT_PORT (27961) the local UDP port for input,
# RECORD=file.mp4 also records the stream. Ctrl-C stops everything.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$HERE/../pcie"
PORT="${PORT:-8090}" INPUT_PORT="${INPUT_PORT:-27961}"
DEV="${DEV:-08:00.0}"

[ -d /sys/module/voodoo_dma ] || insmod "$P/voodoo_dma/voodoo_dma.ko"
setpci -s "$DEV" COMMAND=0006   # memory + bus master for the frame DMA
"$P/voodoo-stream" --setup-only

rec=()
[ -n "${RECORD:-}" ] && rec=(--record "$RECORD")
"$P/voodoo-stream" --attach 2>/dev/null |
  python3 "$P/voodoo-view.py" --port "$PORT" --input-port "$INPUT_PORT" "${rec[@]}" &
view=$!
trap 'kill $view 2>/dev/null; pkill -x voodoo-stream; wait' EXIT

QUAKE_INPUT_UDP="$INPUT_PORT" "$HERE/run-pcie.sh" "$@"
