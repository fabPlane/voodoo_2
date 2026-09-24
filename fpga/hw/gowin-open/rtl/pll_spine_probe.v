// Clock a Gray counter from PLLA CLKOUT1 and read it over JTAG.
//
// Unlike pll_local_probe, this uses the PLL output as a clock. A delta near
// 2,500,000 per 100 ms means that output reached the fabric through the
// global spine. Lock is mailbox select 1.

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

    wire pixel_clk;
    wire pll_lock;

    open_pll_125 u_pll (
        .clkin      (clk50),
        .serial_clk (),
        .pixel_clk  (pixel_clk),
        .lock       (pll_lock)
    );

    reg [31:0] pix_count = 32'b0;
    reg [31:0] pix_gray = 32'b0;
    always @(posedge pixel_clk) begin
        pix_count <= pix_count + 1'b1;
        pix_gray <= (pix_count >> 1) ^ pix_count;
    end

    // A fabric sink on clk50 lets the global router accept the input clock
    // when the PLL pin itself is only reachable by ordinary wires.
    reg [7:0] heartbeat = 8'b0;
    always @(posedge clk50)
        heartbeat <= heartbeat + 1'b1;

    assign led_n[0] = ~pll_lock;
    assign led_n[1] = ~heartbeat[7];

    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire update_dr;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] shift_reg = 32'b0;
    reg [1:0] mailbox = 2'b0;
    reg [31:0] gray_sync_1 = 32'b0;
    reg [31:0] gray_sync_2 = 32'b0;
    reg lock_sync_1 = 1'b0;
    reg lock_sync_2 = 1'b0;

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
        gray_sync_1 <= pix_gray;
        gray_sync_2 <= gray_sync_1;
        lock_sync_1 <= pll_lock;
        lock_sync_2 <= lock_sync_1;
        shift_capture_d <= shift_capture;
        if (enable_er1 && shift_capture) begin
            if (!shift_capture_d) begin
                if (mailbox == 2'd1)
                    shift_reg <= {31'b0, lock_sync_2};
                else
                    shift_reg <= gray_sync_2;
            end else begin
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
            end
        end
        if (enable_er1 && update_dr)
            mailbox <= shift_reg[1:0];
    end
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
    defparam u_pll.ODIV0_FRAC_SEL = 0;
    defparam u_pll.ODIV1_SEL = 40;
    defparam u_pll.CLKOUT0_EN = "TRUE";
    defparam u_pll.CLKOUT1_EN = "TRUE";
    defparam u_pll.CLKOUT2_EN = "FALSE";
    defparam u_pll.CLKOUT3_EN = "FALSE";
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
