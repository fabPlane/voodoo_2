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

# Keep the trace through the Nth buffer swap so the reference replay stays
# on the opening frames instead of a full timedemo.
truncate_trace() {
  local src=$1 dst=$2 limit=$3
  python3 - "$src" "$dst" "$limit" <<'PY'
import struct, sys
src, dst, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
data = open(src, "rb").read()
hdr, ent = 64, 24
if len(data) < hdr or data[:4] != b"VOOD":
    print("not a voodoo trace")
    sys.exit(0)
n = (len(data) - hdr) // ent
keep, swaps = n, 0
for i in range(n):
    off = hdr + i * ent
    cmd, addr = struct.unpack_from("<B3xI", data, off + 8)
    if cmd == 11 or (cmd == 0 and addr == 0x128):
        swaps += 1
        if swaps >= limit:
            keep = i + 1
            break
out = bytearray(data[: hdr + keep * ent])
struct.pack_into("<I", out, 44, keep)
open(dst, "wb").write(out)
print(f"entries={n} keep={keep} swaps={swaps} bytes={len(out)}")
PY
}

run_one() {
  local name=$1 dir=$2 timeout_s=$3 dll_overrides=$4
  shift 4
  local out=$RUNS/$name
  rm -rf "$out"
  mkdir -p "$out/frames"
  rm -f "$dir/trace.bin" "$dir/qconsole.log"
  echo "---- $name ----"
  (
    cd "$dir"
    # shellcheck disable=SC2086
    timeout --signal=TERM --kill-after=5 "$timeout_s" \
      env WINEDLLOVERRIDES="$dll_overrides" \
      wine "$@" >"$out/run.log" 2>&1
    echo $? >"$out/exitcode"
  )
  local code
  code=$(cat "$out/exitcode")
  if [[ -f "$dir/qconsole.log" ]]; then
    cp -f "$dir/qconsole.log" "$out/qconsole.log"
  fi
  if [[ -s "$dir/trace.bin" ]]; then
    cp -f "$dir/trace.bin" "$out/trace.bin"
    truncate_trace "$out/trace.bin" "$out/trace-open.bin" 4 >"$out/truncate.log"
    "$TRACE_TEST" "$out/trace-open.bin" --ref-only \
      --dump-frames "$out/frames" --dump-stride 1 \
      >"$out/replay.log" 2>&1 || true
    local frames
    frames=$(find "$out/frames" -name 'frame_*.png' | wc -l)
    echo "$name exit=$code trace=$(wc -c <"$out/trace.bin") frames=$frames"
  else
    echo "$name exit=$code trace=none"
  fi
}

stage_quake2
stage_glquake
stage_mdk

# Quake II Test selects the bundled MiniGL with gl_driver 3dfxgl.
# demo1.bsp and demos/demo1.dm2 are in pak0.
run_one quake2 "$GAMES/quake2" 40 "mscoree,mshtml=" \
  q2test.exe +set vid_ref gl +set gl_driver 3dfxgl +set vid_fullscreen 1 \
  +set s_volume 0 +map demo1

# GLQuake's import is OPENGL32.dll. Force the game-dir MiniGL copy.
run_one glquake "$GAMES/glquake" 30 "mscoree,mshtml=;opengl32=n,b" \
  glquake.exe -basedir . -window -width 640 -height 480 \
  -nosound -nocdaudio +map start

# DirectX demo. No Glide import; still launched so the log is kept.
run_one mdk "$GAMES/mdk" 20 "mscoree,mshtml=" \
  MDKDEM95.EXE

echo
echo "logs and frames are under $RUNS"
