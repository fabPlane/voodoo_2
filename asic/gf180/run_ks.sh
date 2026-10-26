#!/bin/bash
cd /home/hyper/.cache/asic-est/sta
source /home/hyper/opt/oss-cad-suite/environment
if [ ! -s core_gf180_ks.v ] || ! grep -q "^endmodule" <(tail -2 core_gf180_ks.v); then
  rm -f core_gf180_ks.v
  yosys -q -l synth_ks.log synth_ks.ys > /dev/null 2>&1 || { echo SYNTH_FAIL >> ks.status; exit 1; }
fi
echo SYNTH_OK >> ks.status
if [ ! -s placed_ks_rt.odb ]; then
  /home/hyper/opt/eda/bin/openroad -no_init -no_splash -exit flow_ks.tcl > flow_ks.log 2>&1 || { echo FLOW_FAIL >> ks.status; exit 1; }
fi
echo FLOW_OK >> ks.status
/home/hyper/opt/eda/bin/openroad -no_init -no_splash -exit hist_ks.tcl > hist_ks.log 2>&1
echo DONE >> ks.status
