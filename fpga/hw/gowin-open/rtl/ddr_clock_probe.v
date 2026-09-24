// DDR3 clock and reset probe for the Tang Retro Console 138K.
//
// Step 1 and 2 of the open PHY bring-up. A 25 MHz PLL output clocks a JTAG
// counter and the DDR CK pins. reset_n is low for 200 us, then CKE is low
// for 400 us. After CKE rises the command bus issues MRS2, MRS3, MRS1,
// MRS0 (DLL reset), and ZQCL, then returns to deselect. DQ and DQS stay
// inputs, so this image never drives the data bus. 25 MHz is below the
// DDR3 minimum clock; this proves the command sequence, not training.
//
// ER1 is {pll_lock, init_step[2:0], gray[27:0]}. The low 28 bits are a
// Gray counter of the DDR clock, about 2,500,000 counts per 100 ms.
// init_step is 0 during reset, 1 while CKE is low, 2 at NOP, then 3..7
// across MRS2, MRS3, MRS1, MRS0, and ZQCL. It stays at 7 when finished.

module top (
    input  wire        clk50,
    output wire [1:0]  led_n,
    output wire        ddr_clk_p,
    output wire        ddr_clk_n,
    output wire        ddr_cke,
    output wire        ddr_odt,
    output wire        ddr_reset_n,
    output wire        ddr_cs_n,
    output wire        ddr_ras_n,
    output wire        ddr_cas_n,
    output wire        ddr_we_n,
    output wire [14:0] ddr_a,
    output wire [2:0]  ddr_ba,
    output wire [1:0]  ddr_dm,
    input  wire [15:0] ddr_dq,
    input  wire [1:0]  ddr_dqs_p,
    input  wire [1:0]  ddr_dqs_n
);
`ifdef OPEN_SOURCE
    wire tck_pad_i = 1'b0;
    wire tms_pad_i = 1'b0;
    wire tdi_pad_i = 1'b0;
    wire tdo_pad_o;
`endif

    wire ddr_clk;
    wire pll_lock;

    open_pll_25 u_pll (
        .clkin  (clk50),
        .clkout (ddr_clk),
        .lock   (pll_lock)
    );

    (* keep *) reg clk50_heartbeat = 1'b0;
    always @(posedge clk50)
        clk50_heartbeat <= ~clk50_heartbeat;

    localparam integer RESET_CYCLES = 5000;
    localparam integer CKE_CYCLES = 15000;

    reg [31:0] counter = 32'b0;
    reg [27:0] counter_gray = 28'b0;
    reg        reset_n_q = 1'b0;
    reg        cke_q = 1'b0;
    reg [2:0]  init_step = 3'd0;
    reg [2:0]  cmd_hold = 3'd0;
    reg        ddr_cs_n_q = 1'b1;
    reg        ddr_ras_n_q = 1'b1;
    reg        ddr_cas_n_q = 1'b1;
    reg        ddr_we_n_q = 1'b1;
    reg [14:0] ddr_a_q = 15'b0;
    reg [2:0]  ddr_ba_q = 3'b0;
    (* keep *) reg [15:0] dq_keep = 16'b0;
    (* keep *) reg [1:0]  dqs_p_keep = 2'b0;
    (* keep *) reg [1:0]  dqs_n_keep = 2'b0;

    // One command every 8 clocks: 4 clocks asserted, 4 clocks deselect.
    always @(posedge ddr_clk) begin
        if (!pll_lock) begin
            counter <= 32'b0;
            counter_gray <= 28'b0;
            reset_n_q <= 1'b0;
            cke_q <= 1'b0;
            init_step <= 3'd0;
            cmd_hold <= 3'd0;
            ddr_cs_n_q <= 1'b1;
            ddr_ras_n_q <= 1'b1;
            ddr_cas_n_q <= 1'b1;
            ddr_we_n_q <= 1'b1;
            ddr_a_q <= 15'b0;
            ddr_ba_q <= 3'b0;
        end else begin
            counter <= counter + 1'b1;
            counter_gray <= (counter[27:0] >> 1) ^ counter[27:0];
            reset_n_q <= counter >= RESET_CYCLES;
            cke_q <= counter >= CKE_CYCLES;
            if (counter < RESET_CYCLES) begin
                init_step <= 3'd0;
            end else if (counter < CKE_CYCLES) begin
                init_step <= 3'd1;
            end else if (!(init_step == 3'd7 && cmd_hold == 3'd7)) begin
                if (cmd_hold == 3'd7) begin
                    cmd_hold <= 3'd0;
                    init_step <= init_step + 3'd1;
                end else begin
                    cmd_hold <= cmd_hold + 3'd1;
                end
            end
            dq_keep <= ddr_dq;
            dqs_p_keep <= ddr_dqs_p;
            dqs_n_keep <= ddr_dqs_n;
            // Deselect unless this is the assert half of an MRS/ZQCL step.
            ddr_cs_n_q <= 1'b1;
            ddr_ras_n_q <= 1'b1;
            ddr_cas_n_q <= 1'b1;
            ddr_we_n_q <= 1'b1;
            ddr_a_q <= 15'b0;
            ddr_ba_q <= 3'b0;
            if (init_step >= 3'd3 && init_step <= 3'd7 && cmd_hold < 3'd4) begin
                ddr_cs_n_q <= 1'b0;
                case (init_step)
                    3'd3: begin // MRS2, CWL 5
                        ddr_ras_n_q <= 1'b0; ddr_cas_n_q <= 1'b0; ddr_we_n_q <= 1'b0;
                        ddr_ba_q <= 3'b010;
                    end
                    3'd4: begin // MRS3
                        ddr_ras_n_q <= 1'b0; ddr_cas_n_q <= 1'b0; ddr_we_n_q <= 1'b0;
                        ddr_ba_q <= 3'b011;
                    end
                    3'd5: begin // MRS1, DLL on, RTT off
                        ddr_ras_n_q <= 1'b0; ddr_cas_n_q <= 1'b0; ddr_we_n_q <= 1'b0;
                        ddr_ba_q <= 3'b001;
                    end
                    3'd6: begin // MRS0, BL8, CL5, DLL reset
                        ddr_ras_n_q <= 1'b0; ddr_cas_n_q <= 1'b0; ddr_we_n_q <= 1'b0;
                        ddr_ba_q <= 3'b000;
                        ddr_a_q <= 15'h0110;
                    end
                    default: begin // ZQCL
                        ddr_we_n_q <= 1'b0;
                        ddr_a_q <= 15'h0400;
                    end
                endcase
            end
        end
    end

    assign ddr_reset_n = reset_n_q;
    assign ddr_cke = cke_q;
    assign ddr_odt = 1'b0;
    assign ddr_cs_n = ddr_cs_n_q;
    assign ddr_ras_n = ddr_ras_n_q;
    assign ddr_cas_n = ddr_cas_n_q;
    assign ddr_we_n = ddr_we_n_q;
    assign ddr_a = ddr_a_q;
    assign ddr_ba = ddr_ba_q;
    assign ddr_dm = 2'b0;

    wire ck_p_unused_oen;
    wire ck_n_unused_oen;

    ODDR u_ck_p (
        .D0(1'b0),
        .D1(1'b1),
        .CLK(ddr_clk),
        .Q0(ddr_clk_p),
        .Q1(ck_p_unused_oen),
        .TX(1'b1)
    );
    ODDR u_ck_n (
        .D0(1'b1),
        .D1(1'b0),
        .CLK(ddr_clk),
        .Q0(ddr_clk_n),
        .Q1(ck_n_unused_oen),
        .TX(1'b1)
    );

    assign led_n[0] = ~pll_lock ^ clk50_heartbeat;
    assign led_n[1] = ~cke_q;

    wire [31:0] status = {pll_lock, init_step, counter_gray};

    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] status_sync_1 = 32'b0;
    reg [31:0] status_sync_2 = 32'b0;
    reg [31:0] shift_reg = 32'b0;

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

    // The right-side PLL locks, but its outputs do not clock fabric.
    // The top-left PLL's CLKOUT1 does, on SPINE8.
    (* BEL="X1Y27/PLL" *)
    PLLA u_pll (
        .CLKIN(clkin), .CLKFB(1'b0), .RESET(1'b0), .PLLPWD(1'b0),
        .RESET_I(1'b0), .RESET_O(1'b0),
        .PSSEL(3'b000), .PSDIR(1'b0), .PSPULSE(1'b0),
        .SSCPOL(1'b0), .SSCON(1'b0), .SSCMDSEL(7'b0000000),
        .SSCMDSEL_FRAC(3'b000), .LOCK(lock), .CLKOUT0(),
        .CLKOUT1(clkout), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(),
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
    defparam u_pll.CLKOUT0_EN = "FALSE";
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
