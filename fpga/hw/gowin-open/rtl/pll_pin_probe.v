// Clock a toggle from each PLL output, then count those edges on clk50.
// A 25 MHz output locked to clk50 is invisible if sampled as data.

module top (
    input  wire       clk50,
    output wire [1:0] led_n
);
`ifdef OPEN_SOURCE
    wire tck_pad_i = 1'b0;
    wire tms_pad_i = 1'b0;
    wire tdi_pad_i = 1'b0;
    wire tdo_pad_o;
`endif

    wire [3:0] clkout;
    wire pll_lock;

    open_pll_125 u_pll (
        .clkin (clk50),
        .clkout (clkout),
        .lock (pll_lock)
    );

    wire buf0;
    (* keep *) LUT1 #(.INIT(2'b10)) u_buf (.I0(clkout[0]), .F(buf0));
    (* keep *) reg tog = 1'b0;
    always @(posedge buf0) tog <= ~tog;

    reg [3:0] d0 = 4'b0;
    reg [3:0] d1 = 4'b0;
    reg [3:0] seen = 4'b0;
    reg tog_s0 = 1'b0;
    reg tog_s1 = 1'b0;
    reg seen_clk = 1'b0;
    reg hb = 1'b0;

    always @(posedge clk50) begin
        hb <= ~hb;
        d0 <= clkout;
        d1 <= d0;
        seen <= seen | (d0 ^ d1);
        tog_s0 <= tog;
        tog_s1 <= tog_s0;
        if (tog_s0 ^ tog_s1)
            seen_clk <= 1'b1;
    end

    assign led_n[0] = ~pll_lock;
    assign led_n[1] = ~seen[0];

    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire update_dr;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] shift_reg = 32'b0;
    reg [1:0] mailbox = 2'b0;
    reg [31:0] word_sync_1 = 32'b0;
    reg [31:0] word_sync_2 = 32'b0;
    reg [1:0] sel_sync_1 = 2'b0;
    reg [1:0] sel_sync_2 = 2'b0;
    reg [31:0] word_mux = 32'b0;

    always @* begin
        case (sel_sync_2)
            2'd1: word_mux = 32'hA5A50000 | {27'b0, seen_clk, seen};
            2'd3: word_mux = {30'b0, hb, pll_lock};
            default: word_mux = 32'h0000A5A5;
        endcase
    end

    (* keep *) GW_JTAG u_jtag (
        .tck_pad_i(tck_pad_i),
        .tms_pad_i(tms_pad_i),
        .tdi_pad_i(tdi_pad_i),
        .tdo_pad_o(tdo_pad_o),
        .tck_o(jtag_tck),
        .tdi_o(jtag_tdi),
        .test_logic_reset_o(),
        .run_test_idle_er1_o(),
        .run_test_idle_er2_o(),
        .shift_dr_capture_dr_o(shift_capture),
        .pause_dr_o(),
        .update_dr_o(update_dr),
        .enable_er1_o(enable_er1),
        .enable_er2_o(),
        .tdo_er1_i(shift_reg[0]),
        .tdo_er2_i(1'b0)
    );

    always @(posedge jtag_tck) begin
        word_sync_1 <= word_mux;
        word_sync_2 <= word_sync_1;
        sel_sync_1 <= mailbox;
        sel_sync_2 <= sel_sync_1;
        shift_capture_d <= shift_capture;
        if (enable_er1 && shift_capture) begin
            if (!shift_capture_d)
                shift_reg <= word_sync_2;
            else
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
        end
        if (enable_er1 && update_dr)
            mailbox <= shift_reg[1:0];
    end
endmodule

module open_pll_125 (
    input  wire       clkin,
    output wire [3:0] clkout,
    output wire       lock
);
    wire clkfbout;

    PLLA u_pll (
        .CLKIN(clkin), .CLKFB(1'b0), .RESET(1'b0), .PLLPWD(1'b0),
        .RESET_I(1'b0), .RESET_O(1'b0),
        .PSSEL(3'b000), .PSDIR(1'b0), .PSPULSE(1'b0),
        .SSCPOL(1'b0), .SSCON(1'b0), .SSCMDSEL(7'b0000000),
        .SSCMDSEL_FRAC(3'b000), .LOCK(lock),
        .CLKOUT0(clkout[0]), .CLKOUT1(clkout[1]),
        .CLKOUT2(clkout[2]), .CLKOUT3(clkout[3]),
        .CLKOUT4(), .CLKOUT5(), .CLKOUT6(), .CLKFBOUT(clkfbout)
    );

    defparam u_pll.FCLKIN = "50";
    defparam u_pll.IDIV_SEL = 1;
    defparam u_pll.FBDIV_SEL = 1;
    defparam u_pll.MDIV_SEL = 20;
    defparam u_pll.MDIV_FRAC_SEL = 0;
    defparam u_pll.ODIV0_SEL = 7;
    defparam u_pll.ODIV0_FRAC_SEL = 0;
    defparam u_pll.ODIV1_SEL = 7;
    defparam u_pll.ODIV2_SEL = 7;
    defparam u_pll.ODIV3_SEL = 7;
    defparam u_pll.CLKOUT0_EN = "TRUE";
    defparam u_pll.CLKOUT1_EN = "TRUE";
    defparam u_pll.CLKOUT2_EN = "TRUE";
    defparam u_pll.CLKOUT3_EN = "TRUE";
    defparam u_pll.CLKOUT4_EN = "FALSE";
    defparam u_pll.CLKOUT5_EN = "FALSE";
    defparam u_pll.CLKOUT6_EN = "FALSE";
    defparam u_pll.CLKFB_SEL = "INTERNAL";
    defparam u_pll.CLKOUT0_DT_DIR = 1'b1;
    defparam u_pll.CLKOUT0_DT_STEP = 0;
    defparam u_pll.CLK0_IN_SEL = 1'b0;
    defparam u_pll.CLK0_OUT_SEL = 1'b0;
    defparam u_pll.DYN_DPA_EN = "FALSE";
    defparam u_pll.DYN_PE0_SEL = "FALSE";
    defparam u_pll.DE0_EN = "FALSE";
    defparam u_pll.RESET_I_EN = "FALSE";
    defparam u_pll.RESET_O_EN = "FALSE";
    defparam u_pll.ICP_SEL = 6'bXXXXXX;
    defparam u_pll.LPF_RES = 3'bXXX;
    defparam u_pll.LPF_CAP = 2'b00;
    defparam u_pll.SSC_EN = "FALSE";
endmodule
