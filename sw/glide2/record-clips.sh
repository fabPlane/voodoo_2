#!/usr/bin/env bash
# Record ~10s clips of the three shareware apps.
# Software / DirectX draws to the Wine/DOSBox window (x11grab).
# Glide draws into the Voodoo sim; those clips are assembled from
# reference-model frame dumps after a capped capture.
set -u
set -m

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DLL=$HERE/build/glide2x.dll
ARCH=$HERE/archives
GAMES=$HERE/games
RUNS=$HERE/runs
ART=${ART:-/opt/cursor/artifacts}
TRACE_TEST=$ROOT/ext/SpinalVoodoo/emu/test/obj_dir/trace_test
export WINEPREFIX=${WINEPREFIX:-/tmp/glide2-wine}
export WINEDEBUG=${WINEDEBUG:--all}
export DISPLAY=${DISPLAY:-:1}
export FX_GLIDE_NO_SPLASH=${FX_GLIDE_NO_SPLASH:-1}

mkdir -p "$ART" "$GAMES" "$RUNS"
if [[ ! -f "$DLL" ]]; then make -C "$HERE"; fi
if [[ ! -x "$HERE/build/watch-swaps" || "$HERE/watch-swaps.c" -nt "$HERE/build/watch-swaps" ]]; then
  gcc -O2 -o "$HERE/build/watch-swaps" "$HERE/watch-swaps.c"
fi
if [[ ! -f "$ARCH/quake2-demo.zip" ]]; then "$HERE/fetch-shareware.sh"; fi

stage_all() {
  if [[ ! -f "$GAMES/quake2/q2test.exe" ]]; then
    mkdir -p "$GAMES/quake2"
    unzip -q "$ARCH/quake2-demo.zip" -d "$GAMES/quake2"
  fi
  cp -f "$DLL" "$GAMES/quake2/glide2x.dll"

  mkdir -p "$GAMES/glquake/id1"
  cp -f "$ARCH/glquake.exe" "$GAMES/glquake/glquake.exe"
  if [[ ! -f "$GAMES/glquake/id1/pak0.pak" ]]; then
    unzip -q -j "$ARCH/quake106-shareware.zip" 'ID1/PAK0.PAK' -d "$GAMES/glquake/id1"
    mv -f "$GAMES/glquake/id1/PAK0.PAK" "$GAMES/glquake/id1/pak0.pak"
  fi
  if [[ ! -f "$GAMES/glquake/3dfxgl.dll" ]]; then
    unzip -q -j "$ARCH/quake2-demo.zip" 3dfxgl.dll -d "$GAMES/glquake"
  fi
  cp -f "$GAMES/glquake/3dfxgl.dll" "$GAMES/glquake/opengl32.dll"
  cp -f "$DLL" "$GAMES/glquake/glide2x.dll"

  if [[ ! -f "$GAMES/mdk/MDKDEM95.EXE" ]]; then
    mkdir -p "$GAMES/mdk"
    7z x -y -o"$GAMES/mdk" "$ARCH/mdk-demo.zip" MDKZIP95.EXE >/dev/null
    7z x -y -o"$GAMES/mdk" "$GAMES/mdk/MDKZIP95.EXE" >/dev/null
  fi

  if [[ ! -f "$GAMES/quake-soft/QUAKE.EXE" ]]; then
    mkdir -p "$GAMES/quake-soft"
    unzip -q "$ARCH/quake106-shareware.zip" -d "$GAMES/quake-soft"
  fi
}

# Grab the full 640x480 desktop for exactly SECONDS.
# Write under /tmp first: direct writes into /opt/cursor/artifacts have
# hung ffmpeg in this environment.
record_x11() {
  local out=$1 seconds=$2
  local tmp=/tmp/clip-$$.mp4
  timeout $((seconds + 15)) ffmpeg -y -f x11grab -video_size 640x480 \
    -framerate 30 -i "${DISPLAY}.0" \
    -t "$seconds" -c:v libx264 -pix_fmt yuv420p -crf 18 -an \
    "$tmp" >/tmp/ffmpeg-record.log 2>&1
  cp -f "$tmp" "$out"
  rm -f "$tmp"
}

wait_window() {
  local pattern=$1
  local tries=${2:-40}
  local i
  for i in $(seq 1 "$tries"); do
    if xwininfo -root -tree -display "$DISPLAY" 2>/dev/null | rg -qi "$pattern"; then
      echo "window '$pattern' at try $i"
      return 0
    fi
    sleep 0.25
  done
  echo "window '$pattern' not seen" >&2
  return 1
}

stop_leader() {
  local leader=$1
  kill -TERM -"$leader" 2>/dev/null || kill -TERM "$leader" 2>/dev/null || true
  sleep 0.5
  kill -KILL -"$leader" 2>/dev/null || kill -KILL "$leader" 2>/dev/null || true
  wait "$leader" 2>/dev/null || true
}

# ---- software / DirectX clips (on-screen) ----

clip_quake2_soft() {
  local out=$RUNS/quake2-soft
  local vid=$ART/quake2_soft_10s.mp4
  rm -rf "$out"; mkdir -p "$out"
  (
    cd "$GAMES/quake2"
    env WINEDLLOVERRIDES='mscoree,mshtml=' \
      wine q2test.exe +set vid_ref soft +set vid_fullscreen 0 +set sw_mode 0 \
      +set s_volume 0 +map demo1 >"$out/run.log" 2>&1
  ) &
  local leader=$!
  wait_window 'Quake 2|q2test' 60 || true
  # Give soft enough time to leave the console and draw the map.
  sleep 8
  echo "recording $vid"
  record_x11 "$vid" 10
  stop_leader "$leader"
  ls -lh "$vid"
}

clip_quake_soft() {
  local out=$RUNS/quake-soft
  local vid=$ART/quake_soft_10s.mp4
  local conf=$out/dosbox.conf
  rm -rf "$out"; mkdir -p "$out"
  cat >"$conf" <<EOF
[sdl]
fullscreen=false
windowresolution=640x480
output=surface
autolock=false
[dosbox]
machine=svga_s3
memsize=32
[cpu]
core=dynamic
cycles=max
[autoexec]
mount c $GAMES/quake-soft
c:
quake.exe -nosound -nocdaudio +map start
EOF
  (
    dosbox -conf "$conf" -exit >"$out/run.log" 2>&1
  ) &
  local leader=$!
  wait_window 'DOSBox|dosbox' 40 || true
  sleep 6
  echo "recording $vid"
  record_x11 "$vid" 10
  stop_leader "$leader"
  ls -lh "$vid"
}

clip_mdk() {
  local out=$RUNS/mdk-clip
  local vid=$ART/mdk_10s.mp4
  rm -rf "$out"; mkdir -p "$out"
  (
    cd "$GAMES/mdk"
    env WINEDLLOVERRIDES='mscoree,mshtml=' \
      wine MDKDEM95.EXE >"$out/run.log" 2>&1
  ) &
  local leader=$!
  wait_window 'MDK|mdkdem' 40 || true
  sleep 3
  echo "recording $vid"
  record_x11 "$vid" 10
  stop_leader "$leader"
  ls -lh "$vid"
}

# Truncate a live Glide trace at swap N (streaming).
truncate_trace() {
  local src=$1 dst=$2 limit=$3
  python3 - "$src" "$dst" "$limit" <<'PY'
import struct, sys
src, dst, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
hdr, ent = 64, 24
fin = open(src, "rb")
head = fin.read(hdr)
magic = struct.unpack_from("<I", head, 0)[0] if len(head) >= 4 else 0
if len(head) < hdr or magic != 0x564F4F44:
    print(f"not a voodoo trace magic={magic:#x}")
    sys.exit(0)
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
PY
}

# Capture Glide until SWAP_LIMIT swaps or CAP bytes, dump frames, make a 10s mp4.
clip_glide() {
  local name=$1
  local dir=$2
  local swap_limit=$3
  local timeout_s=$4
  local cap_mb=$5
  local dll_overrides=$6
  shift 6
  local out=$RUNS/${name}-glide
  local vid=$ART/${name}_glide_10s.mp4
  rm -rf "$out"; mkdir -p "$out/frames"
  rm -f "$dir/trace.bin"
  (
    cd "$dir"
    env WINEDLLOVERRIDES="$dll_overrides" wine "$@" >"$out/run.log" 2>&1
  ) &
  local leader=$!
  "$HERE/build/watch-swaps" "$dir/trace.bin" "$swap_limit" "$timeout_s" "$leader" \
    $((cap_mb * 1024 * 1024)) >"$out/watch.log" 2>&1 || true
  local code=0
  wait "$leader" || code=$?
  echo "$code" >"$out/exitcode"
  sleep 0.5
  if [[ ! -s "$dir/trace.bin" ]]; then
    echo "$name glide: no trace"
    stop_leader "$leader"
    return 1
  fi
  local nbytes
  nbytes=$(wc -c <"$dir/trace.bin")
  truncate_trace "$dir/trace.bin" "$out/trace-open.bin" "$swap_limit" >"$out/truncate.log"
  rm -f "$dir/trace.bin"
  # Dump every swap frame. Cap replay wall time so a huge prefix cannot hang.
  timeout 180 "$TRACE_TEST" "$out/trace-open.bin" --ref-only \
    --dump-frames "$out/frames" --dump-stride 1 \
    >"$out/replay.log" 2>&1 || true
  local nframes
  nframes=$(find "$out/frames" -name 'frame_*.png' | wc -l)
  echo "$name glide exit=$code trace=$nbytes frames=$nframes"
  if [[ "$nframes" -lt 1 ]]; then
    return 1
  fi
  # Build a 10s clip: hold each dumped frame evenly across 10 seconds,
  # nearest-neighbor scale to 640x480.
  local fps
  fps=$(python3 - <<PY
n=$nframes
print(max(n/10.0, 0.5))
PY
)
  ffmpeg -y -framerate "$fps" -i "$out/frames/frame_%05d.png" \
    -t 10 -vf 'scale=640:480:flags=neighbor' \
    -c:v libx264 -pix_fmt yuv420p -crf 18 -an "$vid" \
    >/tmp/ffmpeg-glide.log 2>&1
  ls -lh "$vid"
}

stage_all

echo "===== software / DirectX ====="
clip_quake2_soft
clip_quake_soft
clip_mdk

echo "===== Glide ====="
# Swaps 1-2 init clear, 3 console. Cap high enough that swap 4+ (map) can land.
clip_glide quake2 "$GAMES/quake2" 6 90 2048 "mscoree,mshtml=" \
  q2test.exe +set vid_ref gl +set gl_driver 3dfxgl +set vid_fullscreen 1 \
  +set s_volume 0 +map demo1

clip_glide glquake "$GAMES/glquake" 6 90 2048 "mscoree,mshtml=;opengl32=n,b" \
  glquake.exe -basedir . -window -width 640 -height 480 \
  -nosound -nocdaudio +map start

echo
echo "clips in $ART"
ls -lh "$ART"/*_10s.mp4 2>/dev/null || true
