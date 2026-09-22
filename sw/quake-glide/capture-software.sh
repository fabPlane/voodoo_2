#!/usr/bin/env bash
#
# One minute of the shareware demo, software-rendered, for comparison with
# the Voodoo/Glide clip from capture-demo.sh.
#
# The id GPL drop's Linux port is the GL/fxMesa backend only. This builds
# tyrquake (a direct port of id's WinQuake software engine) with a headless
# video driver that writes one PPM per frame. Fake DMA sound is required:
# tyrquake's null sound driver otherwise leaves shm NULL and S_Init faults.
# demo1, then demo2 and demo3, so the minute continues past the first demo.
#
#   ./capture-software.sh
#   SECS=60 ./capture-software.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$HERE/work"
SRC="$WORK/tyrquake"
SECS="${SECS:-60}"
FRAMES="${FRAMES:-$WORK/software-frames}"
OUT="${OUT:-$HERE/shots/quake_demo_software.mp4}"

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

if ! grep -q 'QUAKE_FRAME_DIR' "$SRC/common/vid_null.c" 2>/dev/null; then
  echo "[capture-software] applying frame-dump patch..."
  git -C "$SRC" apply "$HERE/patches/tyrquake-frame-dump.patch"
fi

if [ ! -x "$SRC/bin/tyr-quake" ] || ! grep -q 'null_shm' "$SRC/common/snd_null.c"; then
  echo "[capture-software] building tyr-quake (software, headless)..."
  make -C "$SRC" -j"$(nproc)" \
    VID_TARGET=null IN_TARGET=null SND_TARGET=null CD_TARGET=null bin/tyr-quake
fi

rm -rf "$FRAMES"
mkdir -p "$FRAMES" "$(dirname "$OUT")"
echo "[capture-software] recording ${SECS}s of game time -> $FRAMES"
set +e
QUAKE_FRAME_DIR="$FRAMES" QUAKE_MAX_SECONDS="$SECS" \
  "$SRC/bin/tyr-quake" -basedir "$WORK/q" \
  +playdemo demo1 +playdemo demo2 +playdemo demo3 \
  >/tmp/quake-software.log 2>&1
set -e
echo "[capture-software] quake log tail:"
tail -15 /tmp/quake-software.log || true

n="$(find "$FRAMES" -name 'frame_*.ppm' | wc -l)"
if [ "$n" -lt 1 ]; then
  echo "[capture-software] no frames written" >&2
  exit 1
fi
fps="$(python3 -c "print(max(1.0, $n / float($SECS)))")"
echo "[capture-software] encoding $n frames at ${fps} fps -> $OUT"
ffmpeg -y -framerate "$fps" -i "$FRAMES/frame_%05d.ppm" \
  -vf "scale=640:400:flags=neighbor" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$OUT"
ls -lh "$OUT"
