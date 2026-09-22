#!/usr/bin/env bash
#
# Smoke-test the in-container Glide path: compile one Glide2 program inside the
# `glide32` container against our sim libglide2x, capture its Voodoo command
# trace, then render it on the host FPGA reference model. The host toolchain is
# never touched -- only the trace render (trace_test) runs on the host.
#
#   ./docker/glide-trace.sh <app.c> [frames]
#   ./docker/glide-trace.sh sw/glide-homebrew/apps/hb02_gouraud.c 1
#
# Output: docker/out/<app>.bin (trace) and docker/out/<app>_ref.png (render).
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SUDO="${SUDO:-sudo}"
IMAGE="${IMAGE:-glide32:latest}"
EXT="$REPO/ext/SpinalVoodoo"
TT="$EXT/emu/test/obj_dir/trace_test"

SRC="${1:?usage: glide-trace.sh <app.c relative to repo root> [frames]}"
FRAMES="${2:-1}"
name="$(basename "${SRC%.*}")"
OUT="$REPO/docker/out"; mkdir -p "$OUT"

"$HERE/setup-docker.sh" >/dev/null

echo "[glide-trace] building + tracing $SRC in $IMAGE ..."
$SUDO docker run --rm -v "$REPO":/workspace -w /workspace/ext/SpinalVoodoo "$IMAGE" bash -c "
  set -e
  make native/trace/build >/dev/null
  G2X=emu/glide/glide2x/sst1
  SW=emu/glide/swlibs
  g++ -m32 -O2 -w -DSST1 -DSIM_BACKEND \
    -I\$G2X/glide/src -I\$G2X/incsrc -I\$G2X/init \
    -I\$SW/fxmisc -I\$SW/newpci/pcilib \
    /workspace/$SRC -o /workspace/docker/out/$name \
    -L\$G2X/lib/sst1 -lglide2x -lm -lstdc++
  LD_LIBRARY_PATH=\$G2X/lib/sst1 SIM_TRACE_FILE=/workspace/docker/out/$name.bin \
    /workspace/docker/out/$name $FRAMES </dev/null
"
echo "[glide-trace] captured docker/out/$name.bin"

if [ -x "$TT" ]; then
  echo "[glide-trace] rendering on host reference model ..."
  "$TT" "$OUT/$name.bin" --ref-only --output-dir "$OUT" >/dev/null 2>&1
  echo "[glide-trace] wrote docker/out/${name}*_ref.png"
else
  echo "[glide-trace] trace_test not built yet; build it with:"
  echo "    bash sw/glide-homebrew/render/render.sh   # builds trace_test + 86Box"
  echo "  then: $TT $OUT/$name.bin --ref-only --output-dir $OUT"
fi
