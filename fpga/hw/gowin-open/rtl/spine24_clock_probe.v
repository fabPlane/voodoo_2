// 25 MHz PLL clock measured on the HDMI-side spine.
// Every counter flop is locked into the SPINE24_BOT region (row 82,
// columns 150-158). SPINE8 does not reach these columns, so a counting
// result means TLPLL0CLK1 is driving SPINE24.
module top (
    input wire clk50
);
    wire clk25;
    wire pll_lock;
    open_pll_25 u_pll (.clkin(clk50), .clkout(clk25), .lock(pll_lock));

    (* BEL="X150Y82/DFF1", keep *) reg c0 = 1'b0;
    (* BEL="X150Y82/DFF2", keep *) reg c1 = 1'b0;
    (* BEL="X150Y82/DFF3", keep *) reg c2 = 1'b0;
    (* BEL="X150Y82/DFF4", keep *) reg c3 = 1'b0;
    (* BEL="X150Y82/DFF5", keep *) reg c4 = 1'b0;
    (* BEL="X150Y82/DFF6", keep *) reg c5 = 1'b0;
    (* BEL="X150Y82/DFF7", keep *) reg c6 = 1'b0;
    (* BEL="X152Y82/DFF1", keep *) reg c7 = 1'b0;
    (* BEL="X152Y82/DFF2", keep *) reg c8 = 1'b0;
    (* BEL="X152Y82/DFF3", keep *) reg c9 = 1'b0;
    (* BEL="X152Y82/DFF4", keep *) reg c10 = 1'b0;
    (* BEL="X152Y82/DFF5", keep *) reg c11 = 1'b0;
    (* BEL="X152Y82/DFF6", keep *) reg c12 = 1'b0;
    (* BEL="X152Y82/DFF7", keep *) reg c13 = 1'b0;
    (* BEL="X154Y82/DFF1", keep *) reg c14 = 1'b0;
    (* BEL="X154Y82/DFF2", keep *) reg c15 = 1'b0;
    (* BEL="X154Y82/DFF3", keep *) reg c16 = 1'b0;
    (* BEL="X154Y82/DFF4", keep *) reg c17 = 1'b0;
    (* BEL="X154Y82/DFF5", keep *) reg c18 = 1'b0;
    (* BEL="X154Y82/DFF6", keep *) reg c19 = 1'b0;
    (* BEL="X154Y82/DFF7", keep *) reg c20 = 1'b0;
    (* BEL="X156Y82/DFF1", keep *) reg c21 = 1'b0;
    (* BEL="X156Y82/DFF2", keep *) reg c22 = 1'b0;
    (* BEL="X156Y82/DFF3", keep *) reg c23 = 1'b0;
    (* BEL="X156Y82/DFF4", keep *) reg c24 = 1'b0;
    (* BEL="X156Y82/DFF5", keep *) reg c25 = 1'b0;
    (* BEL="X156Y82/DFF6", keep *) reg c26 = 1'b0;
    (* BEL="X156Y82/DFF7", keep *) reg c27 = 1'b0;
    (* BEL="X158Y82/DFF1", keep *) reg c28 = 1'b0;
    (* BEL="X158Y82/DFF2", keep *) reg c29 = 1'b0;
    (* BEL="X158Y82/DFF3", keep *) reg c30 = 1'b0;
    (* BEL="X158Y82/DFF4", keep *) reg c31 = 1'b0;

    wire [31:0] counter = {c31, c30, c29, c28, c27, c26, c25, c24, c23, c22, c21, c20, c19, c18, c17, c16, c15, c14, c13, c12, c11, c10, c9, c8, c7, c6, c5, c4, c3, c2, c1, c0};
    wire [31:0] counter_next = counter + 1'b1;
    always @(posedge clk25) begin
        c0 <= counter_next[0];
        c1 <= counter_next[1];
        c2 <= counter_next[2];
        c3 <= counter_next[3];
        c4 <= counter_next[4];
        c5 <= counter_next[5];
        c6 <= counter_next[6];
        c7 <= counter_next[7];
        c8 <= counter_next[8];
        c9 <= counter_next[9];
        c10 <= counter_next[10];
        c11 <= counter_next[11];
        c12 <= counter_next[12];
        c13 <= counter_next[13];
        c14 <= counter_next[14];
        c15 <= counter_next[15];
        c16 <= counter_next[16];
        c17 <= counter_next[17];
        c18 <= counter_next[18];
        c19 <= counter_next[19];
        c20 <= counter_next[20];
        c21 <= counter_next[21];
        c22 <= counter_next[22];
        c23 <= counter_next[23];
        c24 <= counter_next[24];
        c25 <= counter_next[25];
        c26 <= counter_next[26];
        c27 <= counter_next[27];
        c28 <= counter_next[28];
        c29 <= counter_next[29];
        c30 <= counter_next[30];
        c31 <= counter_next[31];
    end

    wire [31:0] counter_gray = (counter >> 1) ^ counter;

    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] status_sync_1 = 32'b0;
    reg [31:0] status_sync_2 = 32'b0;
    reg [31:0] shift_reg = 32'b0;
    wire [31:0] status = {pll_lock, counter_gray[30:0]};

    (* keep *) GW_JTAG u_jtag (
        .tck_pad_i(1'b0),
        .tms_pad_i(1'b0),
        .tdi_pad_i(1'b0),
        .tdo_pad_o(),
        .tck_o(jtag_tck),
        .tdi_o(jtag_tdi),
        .test_logic_reset_o(),
        .run_test_idle_er1_o(),
        .run_test_idle_er2_o(),
        .shift_dr_capture_dr_o(shift_capture),
        .pause_dr_o(),
        .update_dr_o(),
        .enable_er1_o(enable_er1),
        .enable_er2_o(),
        .tdo_er1_i(shift_reg[0]),
        .tdo_er2_i(1'b0)
    );

    always @(posedge jtag_tck) begin
        status_sync_1 <= status;
        status_sync_2 <= status_sync_1;
        shift_capture_d <= shift_capture;
        if (enable_er1 && shift_capture) begin
            if (!shift_capture_d)
                shift_reg <= status_sync_2;
            else
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
        end
    end
endmodule
module open_pll_25 (
    input  wire clkin,
    output wire clkout,
    output wire lock
);
    wire clkfbout;

    // BRPLL0 sits on the HDMI column in the bottom half. FSE selects
    // CLKOUT0 onto SPINE24 with bits (20,113), (20,115) and (20,117).
    (* BEL="X181Y63/PLL" *)
    PLLA u_pll (
        .CLKIN(clkin), .CLKFB(1'b0), .RESET(1'b0), .PLLPWD(1'b0),
        .RESET_I(1'b0), .RESET_O(1'b0),
        .PSSEL(3'b000), .PSDIR(1'b0), .PSPULSE(1'b0),
        .SSCPOL(1'b0), .SSCON(1'b0), .SSCMDSEL(7'b0000000),
        .SSCMDSEL_FRAC(3'b000), .LOCK(lock), .CLKOUT0(clkout),
        .CLKOUT1(), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(),
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
    defparam u_pll.CLKOUT1_EN = "FALSE";
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
