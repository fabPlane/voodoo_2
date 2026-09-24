#!/bin/bash
# Run the Glide2 SDK tests that draw without an external .3df, one frame each,
# then replay the trace through the 86Box reference model.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
BIN=$HERE/build/tests
TRACE_TEST=$ROOT/ext/SpinalVoodoo/emu/test/obj_dir/trace_test
OUT=${OUT:-/tmp/glide2-sdk}
export WINEPREFIX=${WINEPREFIX:-/tmp/glide2-wine}
export WINEDEBUG=${WINEDEBUG:--all}

TESTS=(
  test01 test02 test03 test04 test05 test06 test07 test08
  test10 test13 test14 test15 test16 test28
)

mkdir -p "$OUT"
for t in "${TESTS[@]}"; do
  d=$OUT/$t
  rm -rf "$d"
  mkdir -p "$d/frames"
  cp "$BIN/$t.exe" "$BIN/glide2x.dll" "$d/"
  (
    cd "$d"
    timeout 25 wine "./$t.exe" -n 1 >run.log 2>&1
    echo $? >exitcode
  )
  code=$(cat "$d/exitcode")
  if [ -s "$d/trace.bin" ]; then
    "$TRACE_TEST" "$d/trace.bin" --ref-only --dump-frames "$d/frames" --dump-stride 1 \
      >"$d/replay.log" 2>&1 || true
    last=$(ls "$d/frames"/frame_*.png 2>/dev/null | tail -1)
    echo "$t exit=$code frame=${last:-none}"
  else
    echo "$t exit=$code frame=none"
  fi
done
