#!/usr/bin/env bash
#
# Run the Quake II build (./build.sh) live on the Tang dock. Needs root for the
# BAR0 mmap and must hold the board lock. Default: the demo's timedemo.
#
#   fpga/hw/gowin/board-lock -m quake2 -- sudo env SIM_DUAL_TMU=1 SST_TMUMEM_SIZE=2 \
#     SST_TRIPLE_BUFFER=1 timeout 300 sw/quake2-glide/run-pcie.sh
#
# Extra arguments replace the default "+timedemo 1 +demomap q2demo1.dm2".
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QG="$HERE/../quake-glide/work/native"
B="$HERE/work/bin"
[ -x "$B/quake2" ] || { echo "run ./build.sh first" >&2; exit 1; }

export LD_LIBRARY_PATH="$QG/Mesa-3.4.2/lib:$QG/glide-pcie/lib"
export GLIDE_X11_ENABLE="${GLIDE_X11_ENABLE:-0}"
export FX_GLIDE_NO_SPLASH=1
export Q2_REF_DIR="$B"
args=("$@")
[ ${#args[@]} -eq 0 ] && args=(+timedemo 1 +demomap q2demo1.dm2)
cd "$B"
exec ./quake2 +set basedir "$HERE/work/demo" +set vid_ref gl \
  +set gl_driver libGL.so.1 +set gl_mode 3 +set vid_fullscreen 1 \
  +set s_initsound 0 +set cd_nocd 1 +set gl_ext_multitexture 1 +set gl_ext_pointparameters 0 "${args[@]}"
