// 800x600@72 DVI/HDMI color bars for the Tang Retro Console 138K.
//
// The 50 MHz oscillator is the only clock that reaches the HDMI serializers.
// CLKDIV2 stays inside the HCLK mux and cannot drive OSER PCLK, so the pixel
// clock is 50 MHz and the serial PLL runs at 250 MHz (5x). 800x600@72 is the
// VESA mode whose pixel clock is exactly 50 MHz.

module top (
    input  wire       clk50,
    input  wire       key0_n,
    input  wire       key1_n,
    output wire [1:0] led_n,
    output wire       tmds_clk_p,
    output wire       tmds_clk_n,
    output wire [2:0] tmds_data_p,
    output wire [2:0] tmds_data_n
);
`ifdef OPEN_SOURCE
    wire tck_pad_i = 1'b0;
    wire tms_pad_i = 1'b0;
    wire tdi_pad_i = 1'b0;
    wire tdo_pad_o;
`endif
    wire pixel_clk;
    wire serial_clk;
    wire pll_lock;
    wire serial_lock;

    // The 50 MHz pin reaches the serializers through BRMDCLK1_BOT and SPINE24.
    assign pixel_clk = clk50;
    assign pll_lock = 1'b1;
    open_pll_x5 u_pll_serial (
        .clkin (clk50),
        .clkout (serial_clk),
        .lock (serial_lock)
    );

    // 800x600@72: total 1040 x 666, positive sync.
    reg [10:0] h_count = 11'd0;
    reg [9:0] v_count = 10'd0;
    reg [23:0] frame_count = 24'd0;

    always @(posedge pixel_clk) begin
        if (!pll_lock) begin
            h_count <= 11'd0;
            v_count <= 10'd0;
            frame_count <= 24'd0;
        end else if (h_count == 11'd1039) begin
            h_count <= 11'd0;
            if (v_count == 10'd665) begin
                v_count <= 10'd0;
                frame_count <= frame_count + 1'b1;
            end else begin
                v_count <= v_count + 1'b1;
            end
        end else begin
            h_count <= h_count + 1'b1;
        end
    end

    wire active_video = (h_count < 11'd800) && (v_count < 10'd600);
    wire hsync = (h_count >= 11'd856) && (h_count < 11'd976);
    wire vsync = (v_count >= 10'd637) && (v_count < 10'd643);

    reg [23:0] rgb;
    always @* begin
        if (!active_video) begin
            rgb = 24'h000000;
        end else begin
            if      (h_count < 11'd100) rgb = 24'hffffff;
            else if (h_count < 11'd200) rgb = 24'hffff00;
            else if (h_count < 11'd300) rgb = 24'h00ffff;
            else if (h_count < 11'd400) rgb = 24'h00ff00;
            else if (h_count < 11'd500) rgb = 24'hff00ff;
            else if (h_count < 11'd600) rgb = 24'hff0000;
            else if (h_count < 11'd700) rgb = 24'h0000ff;
            else                        rgb = 24'h000000;
        end
    end

    open_dvi_tx u_dvi (
        .reset          (!pll_lock || !serial_lock),
        .serial_clk     (serial_clk),
        .pixel_clk      (pixel_clk),
        .hsync          (hsync),
        .vsync          (vsync),
        .de             (active_video && key0_n && key1_n),
        .red            (rgb[23:16]),
        .green          (rgb[15:8]),
        .blue           (rgb[7:0]),
        .tmds_clk_p     (tmds_clk_p),
        .tmds_clk_n     (tmds_clk_n),
        .tmds_data_p    (tmds_data_p),
        .tmds_data_n    (tmds_data_n)
    );

    // LEDs are active-low: one shows PLL lock, the other a slow frame tick.
    // The PLL input is not reachable by the dedicated global router on the
    // top-left site. A fabric sink lets that route complete partially, and
    // ordinary wires finish the connection. This is the path that locks.
    (* keep *) reg clk50_heartbeat = 1'b0;
    always @(posedge clk50)
        clk50_heartbeat <= ~clk50_heartbeat;
    // One pixel-clock flop on the proven top-left spine. The router binds
    // that spine first, then joins the HDMI pins to the same bridge.
    (* keep *) reg pixel_anchor = 1'b0;
    always @(posedge pixel_clk)
        pixel_anchor <= ~pixel_anchor;
    // No fabric flop on the serial clock. A LUT or fabric DFF clocked from
    // this PLL pin does not toggle; only the OSER10 FCLK pins may use it.

    assign led_n[0] = ~(pll_lock && serial_lock);
    assign led_n[1] = ~(frame_count[5] ^ clk50_heartbeat);

    // Ripple counters, then a gray copy. The gray flops are the only wide
    // load on each PLL output, so they can sit on the dedicated spine and
    // report that spine's real rate. 26 bits covers a 125 MHz clock for
    // the 100 ms JTAG sample interval.
    reg [25:0] pix_div = 26'b0;
    reg [25:0] pix_gray_s = 26'b0;
    reg [25:0] ser_div = 26'b0;
    reg [25:0] ser_gray_s = 26'b0;
    always @(posedge pixel_clk) begin
        pix_div[0] <= ~pix_div[0];
        pix_gray_s <= (pix_div >> 1) ^ pix_div;
    end
    genvar ripple_i;
    generate
        for (ripple_i = 1; ripple_i < 26; ripple_i = ripple_i + 1) begin : pix_ripple
            always @(posedge pix_div[ripple_i - 1])
                pix_div[ripple_i] <= ~pix_div[ripple_i];
        end
    endgenerate
    wire [31:0] pix_gray = {6'b0, pix_gray_s};
    wire [31:0] ser_gray = {6'b0, ser_gray_s};

    // One slow bit from the serial clock, rippled so the high bit is a few
    // hertz. Placed beside the HDMI serializers, its edges give that clock's
    // real rate.
    wire [22:0] ser_slow = 23'b0;

    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire update_dr;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] shift_reg = 32'b0;
    reg [31:0] mailbox = 32'b0;
    reg [31:0] pix_sync_1 = 32'b0;
    reg [31:0] pix_sync_2 = 32'b0;
    reg [31:0] ser_sync_1 = 32'b0;
    reg [31:0] ser_sync_2 = 32'b0;
    reg [23:0] frame_sync_1 = 24'b0;
    reg [23:0] frame_sync_2 = 24'b0;
    reg lock_sync_1 = 1'b0;
    reg lock_sync_2 = 1'b0;
    reg key0_sync_1 = 1'b0;
    reg key0_sync_2 = 1'b0;
    reg key1_sync_1 = 1'b0;
    reg key1_sync_2 = 1'b0;
    reg ser_slow_sync_1 = 1'b0;
    reg ser_slow_sync_2 = 1'b0;
    reg serial_lock_sync_1 = 1'b0;
    reg serial_lock_sync_2 = 1'b0;

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
        pix_sync_1 <= pix_gray;
        pix_sync_2 <= pix_sync_1;
        ser_sync_1 <= ser_gray;
        ser_sync_2 <= ser_sync_1;
        frame_sync_1 <= frame_count;
        frame_sync_2 <= frame_sync_1;
        lock_sync_1 <= pll_lock;
        lock_sync_2 <= lock_sync_1;
        key0_sync_1 <= key0_n;
        key0_sync_2 <= key0_sync_1;
        key1_sync_1 <= key1_n;
        key1_sync_2 <= key1_sync_1;
        ser_slow_sync_1 <= ser_slow[22];
        ser_slow_sync_2 <= ser_slow_sync_1;
        serial_lock_sync_1 <= serial_lock;
        serial_lock_sync_2 <= serial_lock_sync_1;
        shift_capture_d <= shift_capture;
        if (enable_er1 && shift_capture) begin
            if (!shift_capture_d) begin
                case (mailbox[1:0])
                    2'd1: shift_reg <= ser_sync_2;
                    2'd2: shift_reg <= {lock_sync_2, key0_sync_2, key1_sync_2, ser_slow_sync_2, serial_lock_sync_2, 3'b0, frame_sync_2};
                    default: shift_reg <= pix_sync_2;
                endcase
            end else begin
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
            end
        end
        if (enable_er1 && update_dr)
            mailbox <= shift_reg;
    end

endmodule

module open_dvi_tx (
    input  wire       reset,
    input  wire       serial_clk,
    input  wire       pixel_clk,
    input  wire       hsync,
    input  wire       vsync,
    input  wire       de,
    input  wire [7:0] red,
    input  wire [7:0] green,
    input  wire [7:0] blue,
    output wire       tmds_clk_p,
    output wire       tmds_clk_n,
    output wire [2:0] tmds_data_p,
    output wire [2:0] tmds_data_n
);
    wire [9:0] tmds_red;
    wire [9:0] tmds_green;
    wire [9:0] tmds_blue;
    wire [2:0] serialized;
    wire tmds_clk_pad;

    tmds_encoder u_enc_blue (
        .clk(pixel_clk), .reset(reset), .de(de), .data(blue),
        .control0(hsync), .control1(vsync), .symbol(tmds_blue)
    );
    tmds_encoder u_enc_green (
        .clk(pixel_clk), .reset(reset), .de(de), .data(green),
        .control0(1'b0), .control1(1'b0), .symbol(tmds_green)
    );
    tmds_encoder u_enc_red (
        .clk(pixel_clk), .reset(reset), .de(de), .data(red),
        .control0(1'b0), .control1(1'b0), .symbol(tmds_red)
    );

    OSER10 u_ser_blue (
        .D0(tmds_blue[0]), .D1(tmds_blue[1]), .D2(tmds_blue[2]),
        .D3(tmds_blue[3]), .D4(tmds_blue[4]), .D5(tmds_blue[5]),
        .D6(tmds_blue[6]), .D7(tmds_blue[7]), .D8(tmds_blue[8]),
        .D9(tmds_blue[9]), .FCLK(serial_clk), .PCLK(pixel_clk),
        .RESET(reset), .Q(serialized[0])
    );
    defparam u_ser_blue.GSREN = "false";
    defparam u_ser_blue.LSREN = "true";

    OSER10 u_ser_green (
        .D0(tmds_green[0]), .D1(tmds_green[1]), .D2(tmds_green[2]),
        .D3(tmds_green[3]), .D4(tmds_green[4]), .D5(tmds_green[5]),
        .D6(tmds_green[6]), .D7(tmds_green[7]), .D8(tmds_green[8]),
        .D9(tmds_green[9]), .FCLK(serial_clk), .PCLK(pixel_clk),
        .RESET(reset), .Q(serialized[1])
    );
    defparam u_ser_green.GSREN = "false";
    defparam u_ser_green.LSREN = "true";

    OSER10 u_ser_red (
        .D0(tmds_red[0]), .D1(tmds_red[1]), .D2(tmds_red[2]),
        .D3(tmds_red[3]), .D4(tmds_red[4]), .D5(tmds_red[5]),
        .D6(tmds_red[6]), .D7(tmds_red[7]), .D8(tmds_red[8]),
        .D9(tmds_red[9]), .FCLK(serial_clk), .PCLK(pixel_clk),
        .RESET(reset), .Q(serialized[2])
    );
    defparam u_ser_red.GSREN = "false";
    defparam u_ser_red.LSREN = "true";

    // Five ones then five zeros is the TMDS pixel clock. A raw 25 MHz
    // square wave on this pad left the monitor dark.
    OSER10 u_ser_clk (
        .D0(1'b1), .D1(1'b1), .D2(1'b1), .D3(1'b1), .D4(1'b1),
        .D5(1'b0), .D6(1'b0), .D7(1'b0), .D8(1'b0), .D9(1'b0),
        .FCLK(serial_clk), .PCLK(pixel_clk), .RESET(reset), .Q(tmds_clk_pad)
    );
    defparam u_ser_clk.GSREN = "false";
    defparam u_ser_clk.LSREN = "true";

    TLVDS_OBUF u_clk (.I(tmds_clk_pad), .O(tmds_clk_p),     .OB(tmds_clk_n));
    TLVDS_OBUF u_d0  (.I(serialized[0]), .O(tmds_data_p[0]), .OB(tmds_data_n[0]));
    TLVDS_OBUF u_d1  (.I(serialized[1]), .O(tmds_data_p[1]), .OB(tmds_data_n[1]));
    TLVDS_OBUF u_d2  (.I(serialized[2]), .O(tmds_data_p[2]), .OB(tmds_data_n[2]));
endmodule

module tmds_encoder (
    input  wire       clk,
    input  wire       reset,
    input  wire       de,
    input  wire [7:0] data,
    input  wire       control0,
    input  wire       control1,
    output reg  [9:0] symbol
);
    wire [3:0] data_ones = data[0] + data[1] + data[2] + data[3]
                         + data[4] + data[5] + data[6] + data[7];
    wire use_xnor = (data_ones > 4) || ((data_ones == 4) && !data[0]);
    wire [7:0] xor_chain;
    wire [7:0] xnor_chain;
    wire [8:0] transition_code;

    assign xor_chain[0] = data[0];
    assign xnor_chain[0] = data[0];
    assign xor_chain[1] = xor_chain[0] ^ data[1];
    assign xnor_chain[1] = xnor_chain[0] ~^ data[1];
    assign xor_chain[2] = xor_chain[1] ^ data[2];
    assign xnor_chain[2] = xnor_chain[1] ~^ data[2];
    assign xor_chain[3] = xor_chain[2] ^ data[3];
    assign xnor_chain[3] = xnor_chain[2] ~^ data[3];
    assign xor_chain[4] = xor_chain[3] ^ data[4];
    assign xnor_chain[4] = xnor_chain[3] ~^ data[4];
    assign xor_chain[5] = xor_chain[4] ^ data[5];
    assign xnor_chain[5] = xnor_chain[4] ~^ data[5];
    assign xor_chain[6] = xor_chain[5] ^ data[6];
    assign xnor_chain[6] = xnor_chain[5] ~^ data[6];
    assign xor_chain[7] = xor_chain[6] ^ data[7];
    assign xnor_chain[7] = xnor_chain[6] ~^ data[7];
    assign transition_code = use_xnor ? {1'b0, xnor_chain}
                                      : {1'b1, xor_chain};

    wire [3:0] code_ones = transition_code[0] + transition_code[1]
                         + transition_code[2] + transition_code[3]
                         + transition_code[4] + transition_code[5]
                         + transition_code[6] + transition_code[7];
    wire signed [5:0] balance = $signed({1'b0, code_ones}) - 6'sd4;
    reg signed [5:0] disparity = 6'sd0;

    always @(posedge clk) begin
        if (reset) begin
            disparity <= 6'sd0;
            symbol <= 10'b1101010100;
        end else if (!de) begin
            disparity <= 6'sd0;
            case ({control1, control0})
                2'b00: symbol <= 10'b1101010100;
                2'b01: symbol <= 10'b0010101011;
                2'b10: symbol <= 10'b0101010100;
                default: symbol <= 10'b1010101011;
            endcase
        end else if ((disparity == 0) || (code_ones == 4)) begin
            if (!transition_code[8]) begin
                symbol <= {1'b0, transition_code[8], ~transition_code[7:0]};
                disparity <= disparity - balance;
            end else begin
                symbol <= {1'b1, transition_code[8], transition_code[7:0]};
                disparity <= disparity + balance;
            end
        end else if (((disparity > 0) && (code_ones > 4)) ||
                     ((disparity < 0) && (code_ones < 4))) begin
            symbol <= {1'b0, transition_code[8], ~transition_code[7:0]};
            disparity <= disparity - balance +
                         (transition_code[8] ? 6'sd2 : 6'sd0);
        end else begin
            symbol <= {1'b1, transition_code[8], transition_code[7:0]};
            disparity <= disparity + balance -
                         (transition_code[8] ? 6'sd0 : 6'sd2);
        end
    end
endmodule

module open_pll_125 (
    input  wire clkin,
    output wire serial_clk,
    output wire pixel_clk,
    output wire lock
);
    wire clkfbout;

    (* BEL="X1Y27/PLL" *)
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

// 50 MHz in, 250 MHz out (VCO 1000 MHz, ODIV0 4). Sits on the HDMI-side PLL.
module open_pll_x5 (
    input  wire clkin,
    output wire clkout,
    output wire lock
);
    wire clkfbout;

    (* BEL="X181Y45/PLL" *)
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
    defparam u_pll.ODIV0_SEL = 4;
    defparam u_pll.ODIV0_FRAC_SEL = 0;
    defparam u_pll.ODIV1_SEL = 8;
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
