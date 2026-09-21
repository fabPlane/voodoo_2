#!/bin/bash
# Mac fallback for run_pnr.sh (AGENTS.md: use only when hyper-ms-lin is
# already in a gw_sh build). Same tcl, same outputs under build/impl/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
G=/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA
if pgrep -fl 'gw_sh build' >/dev/null; then
    echo "run_pnr_mac.sh: a gw_sh build is already running on the Mac" >&2
    exit 2
fi
mkdir -p "$here/build"
cd "$here/build"
rm -rf impl
export GOWINHOME="$G" DYLD_LIBRARY_PATH="$G/IDE/lib" DYLD_FRAMEWORK_PATH="$G/IDE/lib"
"$G/IDE/bin/gw_sh" ../build_hdmi_demo.tcl 2>&1 | tee gw_sh_mac.log
grep -q "Bitstream generation completed" gw_sh_mac.log
ls -la impl/pnr/*.fs
