#!/usr/bin/env bash
#
# Capture a short GLQuake `demo1` clip through the Voodoo reference model and
# encode it to MP4.
#
# A full minute is not replayable on this path. GLQuake with -no8bit re-uploads
# every texture as RGBA into a Voodoo1-class TMU, so the command trace grows
# about half a gigabyte per second and `trace_test` replay is linear in that
# size (see ISSUES.md F3). SECS=12 is long enough for demo1 to finish loading
# and reach the e1m3 flythrough (the "shells" / "Grenade Launcher" pickups).
# capture-software.sh records a full minute of the software renderer.
#
#   ./capture-demo.sh                 # build if needed, capture, encode
#   SECS=12 ./capture-demo.sh         # wall-clock seconds of GLQuake
#   ./capture-demo.sh encode          # re-encode shots/demo1-frames
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
EXT="$REPO/ext/SpinalVoodoo"
WORK="$HERE/work"
TT="$EXT/emu/test/obj_dir/trace_test"
SECS="${SECS:-12}"
FRAMES="${FRAMES:-$HERE/shots/demo1-frames}"
OUT="${OUT:-$HERE/shots/glquake_demo1_voodoo.mp4}"

encode() {
  local n
  n="$(find "$FRAMES" -name 'frame_*.png' | wc -l)"
  if [ "$n" -lt 1 ]; then
    echo "[capture-demo] no frames in $FRAMES" >&2
    exit 1
  fi
  # Play the clip back across the same wall time the trace was captured in.
  local fps
  fps="$(python3 -c "print(max(1.0, $n / float($SECS)))")"
  echo "[capture-demo] encoding $n frames at ${fps} fps -> $OUT"
  # 320x240 view sits in the lower-left of the 640x480 framebuffer (ISSUES F4).
  ffmpeg -y -framerate "$fps" -i "$FRAMES/frame_%05d.png" \
    -vf "crop=320:240:0:240,scale=640:480:flags=neighbor" \
    -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$OUT"
  ls -lh "$OUT"
}

case "${1:-all}" in
  encode) encode ;;
  *)
    if [ ! -x "$WORK/glquake" ] || [ ! -x "$TT" ]; then
      "$HERE/build.sh" build
    fi
    echo "[capture-demo] capturing ${SECS}s of demo1 -> $WORK/glquake.trace"
    QUAKE_CMDS='playdemo demo1' SECS="$SECS" "$HERE/build.sh" run
    ls -lh "$WORK/glquake.trace"
    rm -rf "$FRAMES"
    mkdir -p "$FRAMES"
    echo "[capture-demo] replaying trace to $FRAMES (this is the slow part)"
    timeout 2400 "$TT" "$WORK/glquake.trace" \
      --ref-only --dump-frames "$FRAMES" --output-dir "$FRAMES" \
      >"$FRAMES/render.log" 2>&1 || {
        echo "[capture-demo] trace_test failed; see $FRAMES/render.log" >&2
        tail -30 "$FRAMES/render.log" >&2
        exit 1
      }
    encode
    ;;
esac
