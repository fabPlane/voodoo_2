#!/bin/bash
cd /home/hyper/.cache/asic-est/chip
source /home/hyper/opt/oss-cad-suite/environment
[ -s chip_gf180.v ] || yosys -q -l synth.log synth.ys > /dev/null 2>&1 || { echo SYNTH_FAIL >> status; exit 1; }
echo SYNTH_OK >> status
/home/hyper/opt/eda/bin/openroad -no_init -no_splash -exit flow.tcl > flow.log 2>&1 || { echo FLOW_FAIL >> status; exit 1; }
echo DONE >> status
