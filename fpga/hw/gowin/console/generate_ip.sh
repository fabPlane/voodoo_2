#!/bin/sh
# Generate Console 138K vendor-ish IP without Gowin Tcl create_ipc
# (Education's Tcl IP catalog is empty on macOS).
set -euo pipefail

if [ "$(uname)" = Darwin ]; then
  GOWINHOME="${GOWINHOME:-/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA}"
else
  GOWINHOME="${GOWINHOME:-$HOME/opt/gowin}"
fi
export GOWINHOME
export PATH="$GOWINHOME/IDE/bin:$PATH"
export DYLD_LIBRARY_PATH="$GOWINHOME/IDE/lib"
export DYLD_FRAMEWORK_PATH="$GOWINHOME/IDE/lib"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

HERE="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
IPDIR="$(CDPATH= cd -- "$HERE/../ip" && pwd)"
WORKDIR="${TMPDIR:-/tmp}/gowin_console_modgen"
mkdir -p "$WORKDIR" "$IPDIR"

gen_pll() {
  local name="$1"
  local mdiv="$2"
  local odiv0="$3"
  local odiv2="$4"
  local en2="$5"
  local rst="$6"
  local clock_en="$7"
  local odiv1="${8:-8}"
  local en1="${9:-false}"
  local outdir="$WORKDIR/$name"
  mkdir -p "$outdir"
  cat > "$WORKDIR/$name.mod" <<EOF
-series GW5AST
-device GW5AST-138
-device_version C
-package PBGA484A
-part_number GW5AST-LV138PG484AC1/I0
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
-dyn_icp_sel true
-dyn_lpf_sel true
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
-en_clkout1 ${en1}
-clkout1_bypass false
-dyn_odiv1_sel false
-odiv1_sel ${odiv1}
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
  GowinModgen -do "$WORKDIR/$name.mod"
}

# 50 * 24 / 12 = 100 MHz, 50 * 24 / 20 = 60 MHz USB FS PHY,
# 50 * 24 / 3 = 400 MHz for Sipeed's proven revision-C x32 DDR3 core.
gen_pll Gowin_PLL_Sys 24 12 3 true true true 20 true
# 50 * 20 / 8 = 125 MHz (5x 25 MHz 640x480)
gen_pll Gowin_PLL_Hdmi 20 8 8 false true false

mkdir -p "$IPDIR/Gowin_PLL_Sys" "$IPDIR/Gowin_PLL_Hdmi" "$IPDIR/DDR3_Memory_Interface" "$IPDIR/DVI_TX"

cp "$WORKDIR/Gowin_PLL_Sys/Gowin_PLL_Sys_mod.v" "$IPDIR/Gowin_PLL_Sys/"
cp "$WORKDIR/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi_mod.v" "$IPDIR/Gowin_PLL_Hdmi/"
cp /tmp/ddr3_framebuffer_gowin/src/pll_init.v "$IPDIR/Gowin_PLL_Sys/"
cp /tmp/ddr3_framebuffer_gowin/src/ddr3_memory_interface/ddr3_memory_interface.v "$IPDIR/DDR3_Memory_Interface/"

cat > "$IPDIR/Gowin_PLL_Sys/Gowin_PLL_Sys.v" << 'EOF'
module Gowin_PLL_Sys (
    input  clkin,
    input  enclk2,
    output clkout0,
    output clkout1,
    output clkout2,
    output lock
);
    wire [5:0] icpsel;
    wire [2:0] lpfres;
    wire       pll_lock;
    wire       pll_rst;

    Gowin_PLL_Sys_MOD u_pll (
        .lock   (pll_lock),
        .clkout0(clkout0),
        .clkout1(clkout1),
        .clkout2(clkout2),
        .clkin  (clkin),
        .reset  (pll_rst),
        .icpsel (icpsel),
        .lpfres (lpfres),
        .lpfcap (2'b00),
        .enclk0 (1'b1),
        .enclk1 (1'b1),
        .enclk2 (enclk2)
    );

    PLL_INIT u_pll_init (
        .CLKIN  (clkin),
        .I_RST  (1'b0),
        .O_RST  (pll_rst),
        .PLLLOCK(pll_lock),
        .O_LOCK (lock),
        .ICPSEL (icpsel),
        .LPFRES (lpfres)
    );
    defparam u_pll_init.CLK_PERIOD = 20;
    defparam u_pll_init.MULTI_FAC  = 24;
endmodule
EOF

cat > "$IPDIR/Gowin_PLL_Hdmi/Gowin_PLL_Hdmi.v" << 'EOF'
module Gowin_PLL_Hdmi (
    input  clkin,
    output clkout0,
    output lock
);
    wire [5:0] icpsel;
    wire [2:0] lpfres;
    wire       pll_lock;
    wire       pll_rst;

    Gowin_PLL_Hdmi_MOD u_pll (
        .lock   (pll_lock),
        .clkout0(clkout0),
        .clkin  (clkin),
        .reset  (pll_rst),
        .icpsel (icpsel),
        .lpfres (lpfres),
        .lpfcap (2'b00)
    );

    PLL_INIT u_pll_init (
        .CLKIN  (clkin),
        .I_RST  (1'b0),
        .O_RST  (pll_rst),
        .PLLLOCK(pll_lock),
        .O_LOCK (lock),
        .ICPSEL (icpsel),
        .LPFRES (lpfres)
    );
    defparam u_pll_init.CLK_PERIOD = 20;
    defparam u_pll_init.MULTI_FAC  = 20;
endmodule
EOF

# USB analog SoftPHY, same 50 → 48 → 960/60 cascade as Sipeed usb_uart_loopback.
# PFD must stay in 19–81.25 MHz (50/5 = 10 MHz failed PA2078).
# 50 * 24 / 25 = 48 MHz, then 48 * 20 / 1 = 960 and /16 = 60.
gen_pll Gowin_PLL_Usb48 24 25 8 false true false
mkdir -p "$IPDIR/Gowin_PLL_Usb48"
cp "$WORKDIR/Gowin_PLL_Usb48/Gowin_PLL_Usb48_mod.v" "$IPDIR/Gowin_PLL_Usb48/"
cat > "$IPDIR/Gowin_PLL_Usb48/Gowin_PLL_Usb48.v" << 'EOF'
module Gowin_PLL_Usb48 (
    input  clkin,
    output clkout0,
    output lock
);
    wire [5:0] icpsel;
    wire [2:0] lpfres;
    wire       pll_lock;
    wire       pll_rst;

    Gowin_PLL_Usb48_MOD u_pll (
        .lock   (pll_lock),
        .clkout0(clkout0),
        .clkin  (clkin),
        .reset  (pll_rst),
        .icpsel (icpsel),
        .lpfres (lpfres),
        .lpfcap (2'b00)
    );

    PLL_INIT u_pll_init (
        .CLKIN  (clkin),
        .I_RST  (1'b0),
        .O_RST  (pll_rst),
        .PLLLOCK(pll_lock),
        .O_LOCK (lock),
        .ICPSEL (icpsel),
        .LPFRES (lpfres)
    );
    defparam u_pll_init.CLK_PERIOD = 20;
    defparam u_pll_init.MULTI_FAC  = 24;
endmodule
EOF

gen_usb_pll() {
  local name="Gowin_PLL_Usb"
  local outdir="$WORKDIR/$name"
  mkdir -p "$outdir"
  cat > "$WORKDIR/$name.mod" <<EOF
-series GW5AST
-device GW5AST-138
-device_version C
-package PBGA484A
-part_number GW5AST-LV138PG484AC1/I0
-mod_name ${name}_MOD
-file_name ${name}_mod
-path ${outdir}/
-type PLL_ADV
-file_type vlg
-ssc false
-clock_en false
-rst true
-rst_pwd false
-rst_i false
-rst_o false
-fclkin 48
-dyn_idiv_sel false
-idiv_sel 1
-clkfb_sel 0
-dyn_fbdiv_sel false
-fbdiv_sel 1
-dyn_icp_sel true
-dyn_lpf_sel true
-en_lock true
-dyn_dpa_en false
-clkout0_bypass false
-dyn_odiv0_sel false
-odiv0_sel 1
-odiv0_frac_sel 0
-dyn_dt0_sel false
-clkout0_dt_dir 1
-clkout0_dt_step 0
-dyn_pe0_sel false
-clkout0_pe_coarse 0
-clkout0_pe_fine 0
-de0_en false
-en_clkout1 true
-clkout1_bypass false
-dyn_odiv1_sel false
-odiv1_sel 16
-dyn_dt1_sel false
-clkout1_dt_dir 1
-clkout1_dt_step 0
-dyn_pe1_sel false
-clkout1_pe_coarse 0
-clkout1_pe_fine 0
-de1_en false
-en_clkout2 false
-en_clkout3 false
-en_clkout4 false
-en_clkout5 false
-en_clkout6 false
-en_clkfbout false
-dyn_mdiv_sel false
-mdiv_sel 20
-mdiv_frac_sel 0
EOF
  GowinModgen -do "$WORKDIR/$name.mod"
}

gen_usb_pll
mkdir -p "$IPDIR/Gowin_PLL_Usb"
cp "$WORKDIR/Gowin_PLL_Usb/Gowin_PLL_Usb_mod.v" "$IPDIR/Gowin_PLL_Usb/"

cat > "$IPDIR/Gowin_PLL_Usb/Gowin_PLL_Usb.v" << 'EOF'
module Gowin_PLL_Usb (
    input  clkin,
    output clkout0,
    output clkout1,
    output lock
);
    wire [5:0] icpsel;
    wire [2:0] lpfres;
    wire       pll_lock;
    wire       pll_rst;

    Gowin_PLL_Usb_MOD u_pll (
        .lock   (pll_lock),
        .clkout0(clkout0),
        .clkout1(clkout1),
        .clkin  (clkin),
        .reset  (pll_rst),
        .icpsel (icpsel),
        .lpfres (lpfres),
        .lpfcap (2'b00)
    );

    PLL_INIT u_pll_init (
        .CLKIN  (clkin),
        .I_RST  (1'b0),
        .O_RST  (pll_rst),
        .PLLLOCK(pll_lock),
        .O_LOCK (lock),
        .ICPSEL (icpsel),
        .LPFRES (lpfres)
    );
    defparam u_pll_init.CLK_PERIOD = 21;
    defparam u_pll_init.MULTI_FAC  = 20;
endmodule
EOF

echo "IP written under $IPDIR"
ls -l "$IPDIR"/*/*
