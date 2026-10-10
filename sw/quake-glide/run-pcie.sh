#!/usr/bin/env bash
#
# Run the PCIe GLQuake build (./build.sh pcie) live on the Tang dock.
# Needs root for the BAR0 mmap, and must hold the board lock:
#
#   fpga/hw/gowin/board-lock -m "glquake" -- sudo sw/quake-glide/run-pcie.sh +map start
#   ... -- sudo timeout 60 sw/quake-glide/run-pcie.sh +timedemo demo1
#
# Extra arguments go to GLQuake after the defaults below (640x480, no sound,
# full RGBA textures). VOODOO_PCI_STATS=1 prints the MMIO counts at exit.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
N="$HERE/work/native"
[ -x "$N/glquake-pcie" ] || { echo "run ./build.sh pcie first" >&2; exit 1; }

export LD_LIBRARY_PATH="$N/Mesa-3.4.2/lib:$N/glide-pcie/lib"
export GLIDE_X11_ENABLE="${GLIDE_X11_ENABLE:-0}"   # no X11 LFB-readback preview
export FX_GLIDE_NO_SPLASH=1
cd "$N"
exec ./glquake-pcie -basedir "$N/q" -nosound -nocdaudio -no8bit \
  -width 640 -height 480 "$@"
