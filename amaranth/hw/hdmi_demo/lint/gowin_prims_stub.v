// Empty-bodied stand-ins for the Gowin GW5A primitives the demo instantiates,
// so `verilator --lint-only` can check port names and widths of the board top
// and the vendor PLL wrapper without the (unavailable) Gowin simulation
// library. LINT ONLY -- never handed to gw_sh (build_hdmi_demo.tcl does not
// reference this directory).

/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNDRIVEN */

module CLKDIV #(
    parameter DIV_MODE = "2",
    parameter GSREN = "false"
) (
    input  HCLKIN,
    input  RESETN,
    input  CALIB,
    output CLKOUT
);
endmodule

module OSER10 #(
    parameter GSREN = "false",
    parameter LSREN = "true"
) (
    output Q,
    input  D0, D1, D2, D3, D4, D5, D6, D7, D8, D9,
    input  PCLK,
    input  FCLK,
    input  RESET
);
endmodule

module ELVDS_OBUF (
    output O,
    output OB,
    input  I
);
endmodule

// GW5A PLL, ports as instantiated by Gowin_PLL_Hdmi_mod.v. defparams on the
// instance require the parameters to exist here.
module PLL #(
    parameter FCLKIN = "50",
    parameter IDIV_SEL = 1, FBDIV_SEL = 1, MDIV_SEL = 20, MDIV_FRAC_SEL = 0,
    parameter ODIV0_SEL = 8, ODIV1_SEL = 8, ODIV2_SEL = 8, ODIV3_SEL = 8,
    parameter ODIV4_SEL = 8, ODIV5_SEL = 8, ODIV6_SEL = 8, ODIV0_FRAC_SEL = 0,
    parameter CLKOUT0_EN = "TRUE", CLKOUT1_EN = "FALSE", CLKOUT2_EN = "FALSE",
    parameter CLKOUT3_EN = "FALSE", CLKOUT4_EN = "FALSE", CLKOUT5_EN = "FALSE",
    parameter CLKOUT6_EN = "FALSE", CLKFB_SEL = "INTERNAL",
    parameter CLKOUT0_DT_DIR = 1'b1, CLKOUT1_DT_DIR = 1'b1,
    parameter CLKOUT2_DT_DIR = 1'b1, CLKOUT3_DT_DIR = 1'b1,
    parameter CLKOUT0_DT_STEP = 0, CLKOUT1_DT_STEP = 0,
    parameter CLKOUT2_DT_STEP = 0, CLKOUT3_DT_STEP = 0,
    parameter CLK0_IN_SEL = 1'b0, CLK0_OUT_SEL = 1'b0, CLK1_IN_SEL = 1'b0,
    parameter CLK1_OUT_SEL = 1'b0, CLK2_IN_SEL = 1'b0, CLK2_OUT_SEL = 1'b0,
    parameter CLK3_IN_SEL = 1'b0, CLK3_OUT_SEL = 1'b0, CLK4_IN_SEL = 2'b00,
    parameter CLK4_OUT_SEL = 1'b0, CLK5_IN_SEL = 1'b0, CLK5_OUT_SEL = 1'b0,
    parameter CLK6_IN_SEL = 1'b0, CLK6_OUT_SEL = 1'b0,
    parameter DYN_DPA_EN = "FALSE",
    parameter CLKOUT0_PE_COARSE = 0, CLKOUT0_PE_FINE = 0,
    parameter CLKOUT1_PE_COARSE = 0, CLKOUT1_PE_FINE = 0,
    parameter CLKOUT2_PE_COARSE = 0, CLKOUT2_PE_FINE = 0,
    parameter CLKOUT3_PE_COARSE = 0, CLKOUT3_PE_FINE = 0,
    parameter CLKOUT4_PE_COARSE = 0, CLKOUT4_PE_FINE = 0,
    parameter CLKOUT5_PE_COARSE = 0, CLKOUT5_PE_FINE = 0,
    parameter CLKOUT6_PE_COARSE = 0, CLKOUT6_PE_FINE = 0,
    parameter DYN_PE0_SEL = "FALSE", DYN_PE1_SEL = "FALSE", DYN_PE2_SEL = "FALSE",
    parameter DYN_PE3_SEL = "FALSE", DYN_PE4_SEL = "FALSE", DYN_PE5_SEL = "FALSE",
    parameter DYN_PE6_SEL = "FALSE",
    parameter DE0_EN = "FALSE", DE1_EN = "FALSE", DE2_EN = "FALSE", DE3_EN = "FALSE",
    parameter DE4_EN = "FALSE", DE5_EN = "FALSE", DE6_EN = "FALSE",
    parameter RESET_I_EN = "FALSE", RESET_O_EN = "FALSE",
    parameter ICP_SEL = 6'bXXXXXX, LPF_RES = 3'bXXX, LPF_CAP = 2'b00,
    parameter SSC_EN = "FALSE",
    parameter DYN_IDIV_SEL = "FALSE", DYN_FBDIV_SEL = "FALSE", DYN_MDIV_SEL = "FALSE",
    parameter DYN_ODIV0_SEL = "FALSE", DYN_ODIV1_SEL = "FALSE", DYN_ODIV2_SEL = "FALSE",
    parameter DYN_ODIV3_SEL = "FALSE", DYN_ODIV4_SEL = "FALSE", DYN_ODIV5_SEL = "FALSE",
    parameter DYN_ODIV6_SEL = "FALSE",
    parameter DYN_DT0_SEL = "FALSE", DYN_DT1_SEL = "FALSE", DYN_DT2_SEL = "FALSE",
    parameter DYN_DT3_SEL = "FALSE",
    parameter DYN_ICP_SEL = "TRUE", DYN_LPF_SEL = "TRUE"
) (
    output LOCK,
    output CLKOUT0, CLKOUT1, CLKOUT2, CLKOUT3, CLKOUT4, CLKOUT5, CLKOUT6,
    output CLKFBOUT,
    input  CLKIN, CLKFB, RESET, PLLPWD, RESET_I, RESET_O,
    input  [5:0] FBDSEL, IDSEL,
    input  [6:0] MDSEL,
    input  [2:0] MDSEL_FRAC,
    input  [6:0] ODSEL0, ODSEL1, ODSEL2, ODSEL3, ODSEL4, ODSEL5, ODSEL6,
    input  [2:0] ODSEL0_FRAC,
    input  [3:0] DT0, DT1, DT2, DT3,
    input  [5:0] ICPSEL,
    input  [2:0] LPFRES,
    input  [1:0] LPFCAP,
    input  [2:0] PSSEL,
    input  PSDIR, PSPULSE,
    input  ENCLK0, ENCLK1, ENCLK2, ENCLK3, ENCLK4, ENCLK5, ENCLK6,
    input  SSCPOL, SSCON,
    input  [6:0] SSCMDSEL,
    input  [2:0] SSCMDSEL_FRAC
);
endmodule

/* verilator lint_on UNDRIVEN */
/* verilator lint_on UNUSEDSIGNAL */
