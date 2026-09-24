#!/usr/bin/env bash
# Stage three Windows shareware/demo apps and try each one against glide2x.dll.
#
# 1. Quake II Test (October 1997) with its bundled 3dfx MiniGL.
# 2. Windows GLQuake plus the Quake 1.06 shareware pak, using that MiniGL
#    renamed to opengl32.dll.
# 3. MDK Windows demo. It is a DirectX title and does not import Glide;
#    the run records whether the exe starts.
#
# Archives come from ./fetch-shareware.sh. Unpacked trees and traces stay
# under games/ and runs/, which are gitignored.
set -u
# Job control gives each background Wine launch its own process group,
# which the swap watcher signals when the trace prefix is long enough.
set -m

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
DLL=$HERE/build/glide2x.dll
ARCH=$HERE/archives
GAMES=$HERE/games
RUNS=$HERE/runs
TRACE_TEST=$ROOT/ext/SpinalVoodoo/emu/test/obj_dir/trace_test
export WINEPREFIX=${WINEPREFIX:-/tmp/glide2-wine}
export WINEDEBUG=${WINEDEBUG:--all}
export DISPLAY=${DISPLAY:-:1}
export FX_GLIDE_NO_SPLASH=${FX_GLIDE_NO_SPLASH:-1}

if [[ ! -f "$DLL" ]]; then
  make -C "$HERE"
fi
if [[ ! -f "$ARCH/quake2-demo.zip" || ! -f "$ARCH/glquake.exe" || ! -f "$ARCH/quake106-shareware.zip" || ! -f "$ARCH/mdk-demo.zip" ]]; then
  "$HERE/fetch-shareware.sh"
fi
if [[ ! -x "$TRACE_TEST" ]]; then
  echo "missing $TRACE_TEST" >&2
  exit 1
fi

mkdir -p "$GAMES" "$RUNS" "$WINEPREFIX"
if [[ ! -f "$WINEPREFIX/system.reg" ]]; then
  wineboot -i >/dev/null 2>&1 || true
fi

stage_quake2() {
  local d=$GAMES/quake2
  if [[ ! -f "$d/q2test.exe" ]]; then
    rm -rf "$d"
    mkdir -p "$d"
    unzip -q "$ARCH/quake2-demo.zip" -d "$d"
  fi
  cp -f "$DLL" "$d/glide2x.dll"
}

stage_glquake() {
  local d=$GAMES/glquake
  mkdir -p "$d/id1"
  cp -f "$ARCH/glquake.exe" "$d/glquake.exe"
  if [[ ! -f "$d/id1/pak0.pak" ]]; then
    unzip -q -j "$ARCH/quake106-shareware.zip" 'ID1/PAK0.PAK' -d "$d/id1"
    mv -f "$d/id1/PAK0.PAK" "$d/id1/pak0.pak"
  fi
  if [[ ! -f "$d/3dfxgl.dll" ]]; then
    unzip -q -j "$ARCH/quake2-demo.zip" 3dfxgl.dll -d "$d"
  fi
  # GLQuake loads OPENGL32.dll. Wine's builtin wins unless the native
  # MiniGL is forced. The MiniGL then LoadLibrary's glide2x.dll.
  cp -f "$d/3dfxgl.dll" "$d/opengl32.dll"
  cp -f "$DLL" "$d/glide2x.dll"
}

stage_mdk() {
  local d=$GAMES/mdk
  if [[ ! -f "$d/MDKDEM95.EXE" ]]; then
    rm -rf "$d"
    mkdir -p "$d"
    7z x -y -o"$d" "$ARCH/mdk-demo.zip" MDKZIP95.EXE >/dev/null
    7z x -y -o"$d" "$d/MDKZIP95.EXE" >/dev/null
  fi
  cp -f "$DLL" "$d/glide2x.dll"
}

# A rendered frame is a register write to SST_swapbufferCMD (0x128).
# MiniGL re-downloads textures on the way there, so a full run is many
# gigabytes. Keep only the prefix through the Nth swap, then delete the
# source trace.
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
    print(f"not a voodoo trace magic={magic:#x} bytes={len(head)}")
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

# Stop the game once the trace holds enough swaps, or at the time / size
# cap. A C reader keeps up with MiniGL's texture downloads; Python does not.
watch_trace() {
  local trace=$1 swaps=$2 limit=$3 leader=$4
  local watcher=$HERE/build/watch-swaps
  if [[ ! -x "$watcher" || "$HERE/watch-swaps.c" -nt "$watcher" ]]; then
    gcc -O2 -o "$watcher" "$HERE/watch-swaps.c"
  fi
  # 768MB is enough for the init clears plus the first rendered frames,
  # and it stops a runaway before the trace fills the disk.
  "$watcher" "$trace" "$swaps" "$limit" "$leader" $((768 * 1024 * 1024))
}

run_one() {
  local name=$1 dir=$2 timeout_s=$3 swap_limit=$4 dll_overrides=$5
  shift 5
  local out=$RUNS/$name
  rm -rf "$out"
  mkdir -p "$out/frames"
  rm -f "$dir/trace.bin" "$dir/qconsole.log"
  echo "---- $name ----"
  (
    cd "$dir"
    env WINEDLLOVERRIDES="$dll_overrides" \
      wine "$@" >"$out/run.log" 2>&1
  ) &
  local leader=$!
  echo "$leader" >"$out/wine.pid"
  watch_trace "$dir/trace.bin" "$swap_limit" "$timeout_s" "$leader" \
    >"$out/watch.log" 2>&1 || true
  local code=0
  wait "$leader" || code=$?
  echo "$code" >"$out/exitcode"
  # Give Wine a moment to release the trace file.
  sleep 0.5
  if [[ -f "$dir/qconsole.log" ]]; then
    cp -f "$dir/qconsole.log" "$out/qconsole.log"
  fi
  if [[ -s "$dir/trace.bin" ]]; then
    local nbytes
    nbytes=$(wc -c <"$dir/trace.bin")
    truncate_trace "$dir/trace.bin" "$out/trace-open.bin" "$swap_limit" \
      >"$out/truncate.log" || true
    rm -f "$dir/trace.bin"
    if [[ -s "$out/trace-open.bin" ]]; then
      "$TRACE_TEST" "$out/trace-open.bin" --ref-only \
        --dump-frames "$out/frames" --dump-stride 1 \
        >"$out/replay.log" 2>&1 || true
    fi
    local frames
    frames=$(find "$out/frames" -name 'frame_*.png' 2>/dev/null | wc -l)
    echo "$name exit=$code trace=$nbytes frames=$frames"
  else
    echo "$name exit=$code trace=none"
  fi
}

stage_quake2
stage_glquake
stage_mdk

# Quake II Test selects the bundled MiniGL with gl_driver 3dfxgl.
# demo1.bsp and demos/demo1.dm2 are in pak0.
# Swaps 1 and 2 are the Glide init clear. Swap 3 is the first frame the
# client presents (Quake II's console, before the rest of the level upload).
run_one quake2 "$GAMES/quake2" 45 3 "mscoree,mshtml=" \
  q2test.exe +set vid_ref gl +set gl_driver 3dfxgl +set vid_fullscreen 1 \
  +set s_volume 0 +map demo1

# GLQuake's import is OPENGL32.dll. Force the game-dir MiniGL copy.
run_one glquake "$GAMES/glquake" 40 3 "mscoree,mshtml=;opengl32=n,b" \
  glquake.exe -basedir . -window -width 640 -height 480 \
  -nosound -nocdaudio +map start

# DirectX demo. No Glide import; swap_limit 1 just lets the time limit win.
run_one mdk "$GAMES/mdk" 20 1 "mscoree,mshtml=" \
  MDKDEM95.EXE

echo
echo "logs and frames are under $RUNS"
