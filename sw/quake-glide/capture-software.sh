#!/usr/bin/env bash
#
# One minute of the same shareware demo, software-rendered, for comparison
# with the Voodoo/Glide clip from capture-demo.sh.
#
# The id GPL drop's Linux port is the GL/fxMesa backend only. The software
# renderer here is tyrquake (a direct port of id's WinQuake software engine)
# playing pak0's demo1, then demo2 and demo3 so the recording covers a full
# minute. Frames are grabbed from a headless Xvfb.
#
#   ./capture-software.sh            # build tyrquake if needed, record 60s
#   SECS=60 ./capture-software.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HERE/work"
SRC="$WORK/tyrquake"
SECS="${SECS:-60}"
OUT="${OUT:-$HERE/shots/quake_demo_software.mp4}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"

if ! command -v Xvfb >/dev/null 2>&1; then
  echo "[capture-software] Xvfb is required" >&2
  exit 1
fi

mkdir -p "$WORK/q/id1"
if [ ! -f "$WORK/q/id1/pak0.pak" ]; then
  if [ -f "$WORK/id1/pak0.pak" ]; then
    cp -f "$WORK/id1/pak0.pak" "$WORK/q/id1/pak0.pak"
  else
    echo "[capture-software] put shareware pak0.pak at $WORK/id1/pak0.pak" >&2
    exit 1
  fi
fi

if [ ! -d "$SRC/.git" ]; then
  echo "[capture-software] cloning tyrquake..."
  git clone --depth 1 https://github.com/sezero/tyrquake.git "$SRC"
fi

if [ ! -x "$SRC/tyr-quake" ]; then
  echo "[capture-software] building tyr-quake (software renderer)..."
  sudo apt-get install -y --no-install-recommends libsdl2-dev >/dev/null
  make -C "$SRC" tyr-quake
fi

# One X server for the recording. Reuse it if this display is already up.
if ! xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1; then
  Xvfb ":$DISPLAY_NUM" -screen 0 640x480x24 >/tmp/xvfb-quake.log 2>&1 &
  for _ in $(seq 1 50); do
    xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1 && break
    sleep 0.1
  done
fi

echo "[capture-software] recording ${SECS}s -> $OUT"
mkdir -p "$(dirname "$OUT")"
# Start the grabber first so the opening frames are not missed.
ffmpeg -y -f x11grab -video_size 640x480 -framerate 20 -i ":$DISPLAY_NUM" \
  -t "$SECS" -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$OUT" \
  >/tmp/quake-software-ffmpeg.log 2>&1 &
FFPID=$!
sleep 1
set +e
DISPLAY=":$DISPLAY_NUM" SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy \
  "$SRC/tyr-quake" -basedir "$WORK/q" -width 320 -height 240 -window \
  +playdemo demo1 +playdemo demo2 +playdemo demo3 \
  >/tmp/quake-software.log 2>&1 &
QPID=$!
set -e
# Let ffmpeg own the duration. If Quake exits early the last frame stays up.
wait "$FFPID" || true
if kill -0 "$QPID" 2>/dev/null; then
  kill "$QPID" 2>/dev/null || true
  wait "$QPID" 2>/dev/null || true
fi
echo "[capture-software] quake log tail:"
tail -15 /tmp/quake-software.log || true
ls -lh "$OUT"
