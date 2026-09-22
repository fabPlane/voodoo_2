#!/usr/bin/env bash
# Quake II Test, deeper clips.
#
# Software runs at playable speed, so it can walk ~60s into demo1 and
# then record 10s. Glide capture is the MiniGL + glide2x.dll path; each
# presented frame re-downloads textures, so "deeper" means more in-level
# swaps after the console, not a full minute of game time.
set -u
set -m

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DLL=$HERE/build/glide2x.dll
GAMES=$HERE/games/quake2
RUNS=$HERE/runs
ART=${ART:-/opt/cursor/artifacts}
TRACE_TEST=$ROOT/ext/SpinalVoodoo/emu/test/obj_dir/trace_test
export WINEPREFIX=${WINEPREFIX:-/tmp/glide2-wine}
export WINEDEBUG=${WINEDEBUG:--all}
export DISPLAY=${DISPLAY:-:1}
export FX_GLIDE_NO_SPLASH=${FX_GLIDE_NO_SPLASH:-1}

mkdir -p "$ART" "$RUNS"
[[ -f "$GAMES/q2test.exe" ]] || { echo "run fetch-shareware.sh first" >&2; exit 1; }
cp -f "$DLL" "$GAMES/glide2x.dll"
[[ -x "$HERE/build/watch-swaps" ]] || gcc -O2 -o "$HERE/build/watch-swaps" "$HERE/watch-swaps.c"

stop_leader() {
  local leader=$1
  kill -TERM -"$leader" 2>/dev/null || kill -TERM "$leader" 2>/dev/null || true
  sleep 0.4
  kill -KILL -"$leader" 2>/dev/null || kill -KILL "$leader" 2>/dev/null || true
  wait "$leader" 2>/dev/null || true
}

find_q2_window() {
  xwininfo -root -tree -display "$DISPLAY" 2>/dev/null \
    | rg '"Quake 2"' | head -1 | awk '{print $1}'
}

# ---- software: 60s into the map, then 10s ----
soft_out=$RUNS/quake2-soft-deep
rm -rf "$soft_out"; mkdir -p "$soft_out"
(
  cd "$GAMES"
  env WINEDLLOVERRIDES='mscoree,mshtml=' \
    wine q2test.exe +set vid_ref soft +set vid_fullscreen 0 +set sw_mode 0 \
    +set s_volume 0 +map demo1 >"$soft_out/run.log" 2>&1
) &
soft_pid=$!
for i in $(seq 1 80); do
  find_q2_window | rg -q . && break
  sleep 0.25
done
echo "soft window up"
# Let the map load, then walk so we are not still at the spawn pad.
sleep 8
WID=$(find_q2_window)
echo "WID=$WID"
if [[ -n "$WID" ]]; then
  xdotool windowactivate --sync "$WID" 2>/dev/null || true
  xdotool key --window "$WID" Escape 2>/dev/null || true
fi
# ~52s of walking / turning (plus the 8s load ≈ a minute in).
end=$((SECONDS + 52))
while [[ $SECONDS -lt $end ]]; do
  if [[ -n "$WID" ]]; then
    xdotool keydown --window "$WID" Up 2>/dev/null || true
    sleep 1.4
    xdotool keyup --window "$WID" Up 2>/dev/null || true
    xdotool key --window "$WID" Left 2>/dev/null || true
  else
    sleep 1
  fi
done
echo "recording software minute-in"
tmp=/tmp/q2-soft-deep.mp4
timeout 25 ffmpeg -y -f x11grab -video_size 640x480 -framerate 30 -i "${DISPLAY}.0" \
  -t 10 -c:v libx264 -pix_fmt yuv420p -crf 18 -an "$tmp" >/tmp/q2-soft-deep-ff.log 2>&1
cp -f "$tmp" "$ART/quake2_soft_1min_in_10s.mp4"
ffmpeg -y -sseof -1 -i "$tmp" -update 1 -frames:v 1 "$ART/quake2_soft_1min_in_still.png" >/dev/null 2>&1
stop_leader "$soft_pid"
echo "soft clip $(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$ART/quake2_soft_1min_in_10s.mp4")"

# ---- Glide: more in-level swaps after the console ----
glide_out=$RUNS/quake2-glide-deep
rm -rf "$glide_out"; mkdir -p "$glide_out/frames"
rm -f "$GAMES/trace.bin"
(
  cd "$GAMES"
  env WINEDLLOVERRIDES='mscoree,mshtml=' \
    wine q2test.exe +set vid_ref gl +set gl_driver 3dfxgl +set vid_fullscreen 1 \
    +set s_volume 0 +map demo1 >"$glide_out/run.log" 2>&1
) &
glide_pid=$!
# Swaps 1-2 clear, 3 console, 4+ in-level. Take 16 so later frames are
# deeper in the same capture. 3GB cap stops a runaway.
"$HERE/build/watch-swaps" "$GAMES/trace.bin" 16 180 "$glide_pid" $((3072 * 1024 * 1024)) \
  >"$glide_out/watch.log" 2>&1 || true
wait "$glide_pid" 2>/dev/null || true
echo "watch:"; cat "$glide_out/watch.log"
if [[ -s "$GAMES/trace.bin" ]]; then
  python3 - "$GAMES/trace.bin" "$glide_out/trace-open.bin" 16 <<'PY'
import struct, sys, os
src, dst, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
hdr, ent = 64, 24
fin = open(src, "rb")
head = fin.read(hdr)
swaps = 0
kept = bytearray()
entries = 0
while True:
    rec = fin.read(ent)
    if len(rec) < ent:
        break
    kept += rec
    entries += 1
    cmd, addr = struct.unpack_from("<B3xI", rec, 8)
    if cmd == 0 and addr == 0x128:
        swaps += 1
        if swaps >= limit:
            break
out = bytearray(head) + kept
struct.pack_into("<I", out, 44, entries)
open(dst, "wb").write(out)
print(f"entries={entries} swaps={swaps} bytes={len(out)}")
fin.close()
os.remove(src)
PY
  timeout 360 "$TRACE_TEST" "$glide_out/trace-open.bin" --ref-only \
    --dump-frames "$glide_out/frames" --dump-stride 1 \
    >"$glide_out/replay.log" 2>&1 || true
  n=$(find "$glide_out/frames" -name 'frame_*.png' | wc -l)
  echo "glide frames=$n"
  # Last 8 presented frames, held across 10s (skip the black/console prefix).
  last=$(ls "$glide_out/frames"/frame_*.png | tail -8 | head -1)
  start=$(basename "$last" | sed 's/frame_//;s/\.png//')
  start=$((10#$start))
  ffmpeg -y -framerate 0.8 -start_number "$start" \
    -i "$glide_out/frames/frame_%05d.png" \
    -t 10 -vf 'scale=640:480:flags=neighbor' \
    -c:v libx264 -pix_fmt yuv420p -crf 18 -an /tmp/q2-glide-deep.mp4 \
    >/tmp/q2-glide-deep-ff.log 2>&1
  cp -f /tmp/q2-glide-deep.mp4 "$ART/quake2_glide_deeper_10s.mp4"
  lastf=$(ls "$glide_out/frames"/frame_*.png | tail -1)
  cp -f "$lastf" "$ART/quake2_glide_deeper_still.png"
  echo "glide clip $(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$ART/quake2_glide_deeper_10s.mp4")"
  rm -f "$glide_out/trace-open.bin"
fi

ls -lh "$ART"/quake2_soft_1min_in_10s.mp4 "$ART"/quake2_glide_deeper_10s.mp4
