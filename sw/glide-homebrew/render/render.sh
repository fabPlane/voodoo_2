#!/usr/bin/env bash
# Render the homebrew Glide demos to PNG screenshots.
#
# Replays each captured demo trace through the SpinalVoodoo trace_test tool,
# which renders via the 86Box software Voodoo reference model (the golden model
# the RTL core is verified against). This is fast and shows exactly what each
# demo's Glide command stream produces.
#
# The cycle-accurate Verilator CORE render is also available via trace_test
# --sim-replay-only, but at ~15-20k cycles/s a full frame is impractically slow
# in CI-class environments; use RENDER_SIM=1 (+ a small HB_RES) if you have time
# and faster hardware. See README.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT="$(cd "$HERE/../../ext/SpinalVoodoo" && pwd)"
TT="$EXT/emu/test/obj_dir/trace_test"
CORESIM="$EXT/src/voodoo/CoreSim.scala"

# 1. Make the sim top elaborate: CoreSim must drive core.io.hdmi (unused for
#    LFB rendering). Applied to the submodule working tree; idempotent.
if ! grep -q 'io.hdmi.clock' "$CORESIM"; then
  echo "[render] Applying CoreSim HDMI tie-off patch..."
  git -C "$EXT" apply "$HERE/render/coresim-hdmi.patch"
fi

# 2. 86Box reference model (nested submodule of SpinalVoodoo; SSH url -> HTTPS).
if [ ! -f "$EXT/emu/86Box/src/video/vid_voodoo_render.c" ]; then
  echo "[render] Initializing 86Box reference submodule..."
  git -C "$EXT" config submodule.emu/86Box.url https://github.com/fayalalebrun/86Box.git
  git -C "$EXT" submodule update --init --depth 1 emu/86Box
fi

# 3. Build trace_test (64-bit). Drop --decorations (unsupported by Verilator 5.020).
VF='--cc --output-split 20000 --output-split-cfuncs 5000 --public-flat-rw --x-assign fast --x-initial fast --noassert -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND -Wno-CMPCONST -Wno-UNSIGNED'
echo "[render] Building trace_test..."
make -C "$EXT/emu/test" VERILATOR_FLAGS="$VF" >/dev/null

# 4. Make sure demo traces exist.
make -C "$HERE" traces >/dev/null

# 5. Render each demo.
mkdir -p "$HERE/shots"
for t in "$HERE"/traces/*.bin; do
  n="$(basename "$t" .bin)"
  if [ "${RENDER_SIM:-0}" = "1" ]; then
    "$TT" "$t" --sim-replay-only --output-dir "$HERE/shots" >"$HERE/shots/$n.log" 2>&1 || true
  else
    "$TT" "$t" --ref-only --output-dir "$HERE/shots" >"$HERE/shots/$n.log" 2>&1
  fi
  echo "[render] shots/${n}_ref.png"
done
echo "[render] Done. Screenshots in sw/glide-homebrew/shots/"
