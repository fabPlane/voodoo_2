// Drive every HDMI pair from the known-good 25 MHz pixel clock.
// A capture that leaves solid black means these pads are not toggling.
module top (
    input  wire       clk50,
    output wire [1:0] led_n,
    output wire       tmds_clk_p,
    output wire       tmds_clk_n,
    output wire [2:0] tmds_data_p,
    output wire [2:0] tmds_data_n
);
    wire pixel_clk;
    wire pll_lock;

    open_pll_125 u_pll (
        .clkin(clk50),
        .serial_clk(),
        .pixel_clk(pixel_clk),
        .lock(pll_lock)
    );

    // A fabric sink on the pin clock keeps clkin off the dead right-hand spines.
    reg hb = 1'b0;
    always @(posedge clk50) hb <= ~hb;

    reg blink = 1'b0;
    reg [19:0] div = 20'd0;
    always @(posedge pixel_clk) begin
        div <= div + 1'b1;
        if (div == 20'hfffff)
            blink <= ~blink;
    end

    assign led_n[0] = ~(pll_lock ^ hb);
    assign led_n[1] = ~blink;

    // A LUT copy is ordinary data. The raw pixel clock is a clock net and
    // the router will not finish it onto the pad input.
    wire clk_pad;
    LUT1 u_padclk (.I0(pixel_clk), .F(clk_pad));
    defparam u_padclk.INIT = 2'b10;

    TLVDS_OBUF u_clk (.I(clk_pad), .O(tmds_clk_p), .OB(tmds_clk_n));
    TLVDS_OBUF u_d0  (.I(clk_pad), .O(tmds_data_p[0]), .OB(tmds_data_n[0]));
    TLVDS_OBUF u_d1  (.I(clk_pad), .O(tmds_data_p[1]), .OB(tmds_data_n[1]));
    TLVDS_OBUF u_d2  (.I(clk_pad), .O(tmds_data_p[2]), .OB(tmds_data_n[2]));
endmodule

module open_pll_125 (
    input  wire clkin,
    output wire serial_clk,
    output wire pixel_clk,
    output wire lock
);
    wire clkfbout;
    PLLA u_pll (
        .CLKIN(clkin), .CLKFB(1'b0), .RESET(1'b0), .PLLPWD(1'b0),
        .RESET_I(1'b0), .RESET_O(1'b0),
        .PSSEL(3'b000), .PSDIR(1'b0), .PSPULSE(1'b0),
        .SSCPOL(1'b0), .SSCON(1'b0), .SSCMDSEL(7'b0000000),
        .SSCMDSEL_FRAC(3'b000), .LOCK(lock), .CLKOUT0(serial_clk),
        .CLKOUT1(pixel_clk), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(),
        .CLKOUT6(), .CLKFBOUT(clkfbout)
    );
    defparam u_pll.FCLKIN = "50";
    defparam u_pll.IDIV_SEL = 1;
    defparam u_pll.FBDIV_SEL = 1;
    defparam u_pll.MDIV_SEL = 20;
    defparam u_pll.MDIV_FRAC_SEL = 0;
    defparam u_pll.ODIV0_SEL = 8;
    defparam u_pll.ODIV1_SEL = 40;
    defparam u_pll.CLKOUT0_EN = "TRUE";
    defparam u_pll.CLKOUT1_EN = "TRUE";
    defparam u_pll.CLKOUT2_EN = "FALSE";
    defparam u_pll.CLKOUT3_EN = "FALSE";
    defparam u_pll.CLKOUT4_EN = "FALSE";
    defparam u_pll.CLKOUT5_EN = "FALSE";
    defparam u_pll.CLKOUT6_EN = "FALSE";
    defparam u_pll.CLKFB_SEL = "INTERNAL";
    defparam u_pll.DYN_DPA_EN = "FALSE";
    defparam u_pll.SSC_EN = "FALSE";
endmodule
