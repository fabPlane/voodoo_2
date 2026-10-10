#!/usr/bin/env bash
# Generate the Tang Mega 138K Pro Dock PLLs with GowinModgen.
# DDR3 (32-bit native) and PCIE_Controller_Top still come from the GUI
# IP Core Generator. See ip.md.
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/env.sh"

MODGEN="$(command -v GowinModGen || command -v GowinModgen || true)"
if [ -z "${GOWINHOME:-}" ] || [ -z "$MODGEN" ]; then
  echo "GowinModGen not on PATH. Set GOWINHOME or source hw/gowin/env.sh." >&2
  echo "Standard 1.9.12.04 lives in \$HOME/gowin/standard-1.9.12.04" >&2
  exit 1
fi

IPDIR="$HERE/ip"
WORKDIR="${TMPDIR:-/tmp}/gowin_pro_dock_modgen"
mkdir -p "$WORKDIR" "$IPDIR"

gen_pll() {
  local name="$1"
  local mdiv="$2"
  local odiv0="$3"
  local odiv2="$4"
  local en2="$5"
  local rst="$6"
  local clock_en="$7"
  local outdir="$WORKDIR/$name"
  mkdir -p "$outdir"
  cat > "$WORKDIR/$name.mod" <<EOF
-series GW5AST
-device GW5AST-138
-device_version B
-package FBG676A
-part_number GW5AST-LV138FPG676AES
-mod_name ${name}_MOD
-file_name ${name}_mod
-path ${outdir}/
-type PLL_ADV
-file_type vlg
-ssc false
-clock_en ${clock_en}
-rst ${rst}
-rst_pwd false
-rst_i false
-rst_o false
-fclkin 50
-dyn_idiv_sel false
-idiv_sel 1
-clkfb_sel 0
-dyn_fbdiv_sel false
-fbdiv_sel 1
-dyn_icp_sel false
-dyn_lpf_sel false
-en_lock true
-dyn_dpa_en false
-clkout0_bypass false
-dyn_odiv0_sel false
-odiv0_sel ${odiv0}
-odiv0_frac_sel 0
-dyn_dt0_sel false
-clkout0_dt_dir 1
-clkout0_dt_step 0
-dyn_pe0_sel false
-clkout0_pe_coarse 0
-clkout0_pe_fine 0
-de0_en false
-en_clkout1 ${en1:-false}
-clkout1_bypass false
-dyn_odiv1_sel false
-odiv1_sel ${odiv1:-8}
-dyn_dt1_sel false
-clkout1_dt_dir 1
-clkout1_dt_step 0
-dyn_pe1_sel false
-clkout1_pe_coarse 0
-clkout1_pe_fine 0
-de1_en false
-en_clkout2 ${en2}
-clkout2_bypass false
-dyn_odiv2_sel false
-odiv2_sel ${odiv2}
-dyn_dt2_sel false
-clkout2_dt_dir 1
-clkout2_dt_step 0
-dyn_pe2_sel false
-clkout2_pe_coarse 0
-clkout2_pe_fine 0
-de2_en false
-en_clkout3 false
-en_clkout4 false
-en_clkout5 false
-en_clkout6 false
-en_clkfbout false
-dyn_mdiv_sel false
-mdiv_sel ${mdiv}
-mdiv_frac_sel 0
EOF
  "$MODGEN" -do "$WORKDIR/$name.mod"
}

# GW5AST PLL VCO must be 650–1300 MHz (VCO = 50 * mdiv with idiv=1).
# 50 * 24 / 16 = 75 MHz TLP (was /12 = 100: vendor upar_arbiter missed timing), 50 * 24 / 3 = 400 MHz DDR3 memory_clk
# clkout1: 50 * 24 / 26 = 46.15 MHz Voodoo core clock (24 -> 50 MHz once timing closes).
en1=true odiv1=26 gen_pll Gowin_PLL_Sys 24 16 3 true true true
# 50 * 25 / 10 = 125 MHz HDMI serial (~5x 25.0 MHz VGA). 126 MHz needs VCO 3150.
gen_pll Gowin_PLL_Hdmi 25 10 8 false true false

mkdir -p "$IPDIR/Gowin_PLL_Sys" "$IPDIR/Gowin_PLL_Hdmi"
cp "$WORKDIR/Gowin_PLL_Sys/Gowin_PLL_Sys_mod.v" "$IPDIR/Gowin_PLL_Sys/"
cp "$WORKDIR/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi_mod.v" "$IPDIR/Gowin_PLL_Hdmi/"

cat > "$IPDIR/Gowin_PLL_Sys/Gowin_PLL_Sys.v" << 'EOF'
module Gowin_PLL_Sys (
    input  clkin,
    output clkout0,
    output clkout1,
    output clkout_core,
    output lock,
    // clkout1 (DDR3 memory_clk) enable, driven by the DDR3 IP's pll_stop.
    input  enclk1
);
    // VCO 1200 MHz: clkout0 = /16 = 75 MHz (PCIe TLP; 100 MHz left the vendor
    // SerDes upar_arbiter 3.2 ns short, Gen2 x4 @ 256 bit needs only 62.5 MHz), clkout1 = /3 =
    // 400 MHz (DDR3 memory_clk), clkout_core = /26 = 46.15 MHz (Voodoo core;
    // /24 = 50 MHz is the SST-1 graphics clock, once the core closes timing).
    Gowin_PLL_Sys_MOD u_pll (
        .lock   (lock),
        .clkout0(clkout0),
        .clkout1(clkout_core),
        .clkout2(clkout1),
        .clkin  (clkin),
        .reset  (1'b0),
        .enclk0 (1'b1),
        .enclk2 (enclk1)
    );
endmodule
EOF

cat > "$IPDIR/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v" << 'EOF'
module Gowin_PLL_Hdmi (
    input  clkin,
    output clkout0,
    output lock
);
    Gowin_PLL_Hdmi_MOD u_pll (
        .lock   (lock),
        .clkout0(clkout0),
        .clkin  (clkin),
        .reset  (1'b0)
    );
endmodule
EOF

echo "PLLs written under $IPDIR"
echo "Still needed from Gowin IP Core Generator (ip.md):"
echo "  PCIE_Controller_Top   endpoint Gen2 x4, TLP 256-bit, BAR0 1 MiB, IDs 10ee:0007"
echo "  DDR3_Memory_Interface_Top  native UI, 32-bit, 256-bit / 29-bit, memory_clk 400 MHz"
echo "  DVI_TX_Top            RGB888, serial=5x pixel"
ls -l "$IPDIR"/*/* 2>/dev/null || true
