#!/usr/bin/env bash
# Generate the on-chip ADC wrappers used by AdcMonitor (supply / temperature
# monitor) with GowinModGen: Gowin_ADC_Vcc (ADCULC, voltage mode, vtest =
# VCC / 2) and Gowin_ADC_Temp (ADCLRC, temperature mode), both on the
# internal ADC oscillator. Output: ip/Gowin_ADC_Vcc, ip/Gowin_ADC_Temp.
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/env.sh"
MODGEN="$(command -v GowinModGen || command -v GowinModgen)"
WORK="${TMPDIR:-/tmp}/gowin_adc_modgen"
IPDIR="${1:-$HERE/ip}"
mkdir -p "$WORK"

gen() { # <module> <file> <adc> <mode> <fscal> <offset>
  local out="$IPDIR/$1"
  mkdir -p "$out"
  cat > "$WORK/$2.mod" <<MOD
-series GW5AST
-device GW5AST-138
-device_version B
-package FBG676A
-part_number GW5AST-LV138FPG676AES
-mod_name $1
-file_name $2
-path $out/
-type ADC
-file_type vlg
-adc_select $3
-adc_mode $4
-clk_sel osc
-vsen_ctl_sel 0
-vsen_ctl 0
-fscal_value $5
-offset $6
MOD
  "$MODGEN" -do "$WORK/$2.mod" >/dev/null
  rm -f "$out/${2}_tmp.v"
  echo "generated $out/$2.v"
}

gen Gowin_ADC_Vcc gowin_adc_vcc ADCULC voltage 653 0
# vtest (VSEN_CTL 000) read 0 V on the board (B47); VCC is also on vdd09_2
# (VSEN_CTL 011) behind BUF_VCC_EN, which ModGen offers no option for.
sed -i "s/adculc_inst.BUF_VCC_EN = 1'b0/adculc_inst.BUF_VCC_EN = 1'b1/; s/adculc_inst.VSEN_CTL = 3'b000/adculc_inst.VSEN_CTL = 3'b011/" \
  "$IPDIR/Gowin_ADC_Vcc/gowin_adc_vcc.v"
gen Gowin_ADC_Temp gowin_adc_temp ADCLRC temperature 730 -1180
