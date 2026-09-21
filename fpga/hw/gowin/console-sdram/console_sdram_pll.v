// Revision-C-safe clocks for the NESTang-style SDRAM interface.
// 50 MHz input -> 1.2 GHz VCO -> 20 MHz Voodoo clock and 60 MHz SDRAM
// controller/pin clocks.  CLKOUT2 retains NESTang's 225-degree phase ratio
// (12.5/20 of a cycle) for the external SDRAM clock.
module console_sdram_pll (
    input  wire clkin,
    output wire core_clk,
    output wire sdram_clk,
    output wire sdram_pin_clk,
    output wire lock
);
    wire gnd = 1'b0;
    wire vcc = 1'b1;

    PLL pll_inst (
        .LOCK(lock),
        .CLKOUT0(core_clk),
        .CLKOUT1(sdram_clk),
        .CLKOUT2(sdram_pin_clk),
        .CLKIN(clkin),
        .CLKFB(gnd),
        .RESET(gnd),
        .PLLPWD(gnd),
        .RESET_I(gnd),
        .RESET_O(gnd),
        .ENCLK0(vcc),
        .ENCLK1(vcc),
        .ENCLK2(vcc)
    );

    defparam pll_inst.FCLKIN = "50";
    defparam pll_inst.IDIV_SEL = 1;
    defparam pll_inst.FBDIV_SEL = 1;
    defparam pll_inst.MDIV_SEL = 24;
    defparam pll_inst.MDIV_FRAC_SEL = 0;
    defparam pll_inst.ODIV0_SEL = 60;
    defparam pll_inst.ODIV1_SEL = 20;
    defparam pll_inst.ODIV2_SEL = 20;
    defparam pll_inst.ODIV0_FRAC_SEL = 0;
    defparam pll_inst.CLKOUT0_EN = "TRUE";
    defparam pll_inst.CLKOUT1_EN = "TRUE";
    defparam pll_inst.CLKOUT2_EN = "TRUE";
    defparam pll_inst.CLKOUT3_EN = "FALSE";
    defparam pll_inst.CLKOUT4_EN = "FALSE";
    defparam pll_inst.CLKOUT5_EN = "FALSE";
    defparam pll_inst.CLKOUT6_EN = "FALSE";
    defparam pll_inst.CLKFB_SEL = "INTERNAL";
    defparam pll_inst.CLKOUT0_DT_DIR = 1'b1;
    defparam pll_inst.CLKOUT1_DT_DIR = 1'b1;
    defparam pll_inst.CLKOUT2_DT_DIR = 1'b1;
    defparam pll_inst.CLKOUT0_DT_STEP = 0;
    defparam pll_inst.CLKOUT1_DT_STEP = 0;
    defparam pll_inst.CLKOUT2_DT_STEP = 0;
    defparam pll_inst.CLKOUT0_PE_COARSE = 0;
    defparam pll_inst.CLKOUT0_PE_FINE = 0;
    defparam pll_inst.CLKOUT1_PE_COARSE = 0;
    defparam pll_inst.CLKOUT1_PE_FINE = 0;
    defparam pll_inst.CLKOUT2_PE_COARSE = 12;
    defparam pll_inst.CLKOUT2_PE_FINE = 4;
    defparam pll_inst.CLK0_IN_SEL = 1'b0;
    defparam pll_inst.CLK0_OUT_SEL = 1'b0;
    defparam pll_inst.CLK1_IN_SEL = 1'b0;
    defparam pll_inst.CLK1_OUT_SEL = 1'b0;
    defparam pll_inst.CLK2_IN_SEL = 1'b0;
    defparam pll_inst.CLK2_OUT_SEL = 1'b0;
    defparam pll_inst.DYN_DPA_EN = "FALSE";
    defparam pll_inst.RESET_I_EN = "FALSE";
    defparam pll_inst.RESET_O_EN = "FALSE";
    defparam pll_inst.SSC_EN = "FALSE";
    defparam pll_inst.DYN_IDIV_SEL = "FALSE";
    defparam pll_inst.DYN_FBDIV_SEL = "FALSE";
    defparam pll_inst.DYN_MDIV_SEL = "FALSE";
    defparam pll_inst.DYN_ODIV0_SEL = "FALSE";
    defparam pll_inst.DYN_ODIV1_SEL = "FALSE";
    defparam pll_inst.DYN_ODIV2_SEL = "FALSE";
    defparam pll_inst.DYN_PE0_SEL = "FALSE";
    defparam pll_inst.DYN_PE1_SEL = "FALSE";
    defparam pll_inst.DYN_PE2_SEL = "FALSE";
    defparam pll_inst.DE0_EN = "FALSE";
    defparam pll_inst.DE1_EN = "FALSE";
    defparam pll_inst.DE2_EN = "FALSE";
endmodule
