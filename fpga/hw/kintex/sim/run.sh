#!/bin/bash
# xsim unit tests for the Kintex board_top helpers: ./run.sh [diag|xadc]
set -e
cd "$(dirname "$0")"
source "${VIVADO_SETTINGS:-/home/hyper/projects/tools/Xilinx/2025.2.1/Vivado/settings64.sh}"
G=$XILINX_VIVADO/data/verilog/src/glbl.v
W=$(mktemp -d)
cp xadc_monitor.stim "$W"
trap 'rm -rf "$W"' EXIT
for t in ${@:-diag xadc}; do
    case $t in diag) src=../src/jtag_diag_xilinx.v;; xadc) src=../src/xadc_monitor.v;; esac
    (cd "$W" && xvlog -sv -nolog "$OLDPWD/tb_$t.v" "$OLDPWD/$src" "$G" >/dev/null &&
     xelab -nolog -L unisims_ver tb_$t glbl -s tb_$t >/dev/null &&
     xsim -nolog tb_$t -R | grep -E 'page|samples=|PASS|FAIL|ERROR')
done
