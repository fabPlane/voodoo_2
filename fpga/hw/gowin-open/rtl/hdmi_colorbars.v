// 640x480 DVI/HDMI color bars for the Tang Retro Console 138K.
//
// This is intentionally framebuffer-free: counters generate RGB pixels and
// open RTL performs TMDS encoding. Only documented GW5A hardware primitives
// (PLL/PLLA, CLKDIV, OSER10, and ELVDS_OBUF) are instantiated.  The 25 MHz
// pixel clock is divided from the 125 MHz serializer clock so the two clocks
// retain the phase relationship required by OSER10.

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
    wire serial_clk;
    wire pixel_clk;
    wire pll_lock;

    open_pll_125 u_pll (
        .clkin  (clk50),
        .serial_clk (serial_clk),
        .lock   (pll_lock)
    );

`ifdef OPEN_SOURCE
    // The GW5AST CLKDIV output-to-fabric bridge is still being documented in
    // Apicula.  A synchronous divide-by-five keeps a deterministic phase to
    // the serializer clock while using ordinary, already proven routing.
    reg [2:0] pixel_div = 3'd0;
    always @(posedge serial_clk) begin
        if (!pll_lock)
            pixel_div <= 3'd0;
        else if (pixel_div == 3'd4)
            pixel_div <= 3'd0;
        else
            pixel_div <= pixel_div + 1'b1;
    end
    assign pixel_clk = (pixel_div < 3'd2);
`else
    CLKDIV #(.DIV_MODE("5")) u_pixel_div (
        .HCLKIN (serial_clk),
        .RESETN (pll_lock),
        .CALIB  (1'b0),
        .CLKOUT (pixel_clk)
    );
`endif

    reg [9:0] h_count = 10'd0;
    reg [9:0] v_count = 10'd0;
    reg [23:0] frame_count = 24'd0;
    reg [7:0] serial_count = 8'd0;

    always @(posedge serial_clk)
        serial_count <= serial_count + 1'b1;

    always @(posedge pixel_clk) begin
        if (!pll_lock || !key0_n) begin
            h_count <= 10'd0;
            v_count <= 10'd0;
            frame_count <= 24'd0;
        end else if (h_count == 10'd799) begin
            h_count <= 10'd0;
            if (v_count == 10'd524) begin
                v_count <= 10'd0;
                frame_count <= frame_count + 1'b1;
            end else begin
                v_count <= v_count + 1'b1;
            end
        end else begin
            h_count <= h_count + 1'b1;
        end
    end

    wire active_video = (h_count < 10'd640) && (v_count < 10'd480);
    wire hsync = !((h_count >= 10'd656) && (h_count < 10'd752));
    wire vsync = !((v_count >= 10'd490) && (v_count < 10'd492));

    reg [23:0] rgb;
    always @* begin
        if (!active_video) begin
            rgb = 24'h000000;
        end else begin
            if      (h_count < 10'd80)  rgb = 24'hffffff;
            else if (h_count < 10'd160) rgb = 24'hffff00;
            else if (h_count < 10'd240) rgb = 24'h00ffff;
            else if (h_count < 10'd320) rgb = 24'h00ff00;
            else if (h_count < 10'd400) rgb = 24'hff00ff;
            else if (h_count < 10'd480) rgb = 24'hff0000;
            else if (h_count < 10'd560) rgb = 24'h0000ff;
            else                        rgb = 24'h000000;
        end
    end

    open_dvi_tx u_dvi (
        .reset          (!pll_lock),
        .serial_clk     (serial_clk),
        .pixel_clk      (pixel_clk),
        .hsync          (hsync),
        .vsync          (vsync),
        .de             (active_video && key1_n),
        .red            (rgb[23:16]),
        .green          (rgb[15:8]),
        .blue           (rgb[7:0]),
        .tmds_clk_p     (tmds_clk_p),
        .tmds_clk_n     (tmds_clk_n),
        .tmds_data_p    (tmds_data_p),
        .tmds_data_n    (tmds_data_n)
    );

    // LEDs are active-low: one shows PLL lock, the other a slow frame tick.
    assign led_n[0] = ~pll_lock;
    assign led_n[1] = ~frame_count[5];

    // ER1 exposes enough state to diagnose the video clocks without relying
    // on the display or LEDs.  The three counters are Gray-coded because they
    // are sampled asynchronously by JTAG TCK.
    wire [7:0] frame_gray = (frame_count[7:0] >> 1) ^ frame_count[7:0];
    wire [7:0] pixel_gray = (h_count[7:0] >> 1) ^ h_count[7:0];
    wire [7:0] serial_gray = (serial_count >> 1) ^ serial_count;
    wire [31:0] jtag_status = {
        4'hd, pll_lock, key0_n, key1_n, active_video,
        frame_gray, pixel_gray, serial_gray
    };

    hdmi_jtag_probe u_probe (.status(jtag_status));

endmodule

module hdmi_jtag_probe (
    input wire [31:0] status
);
    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] status_sync_1 = 32'b0;
    reg [31:0] status_sync_2 = 32'b0;
    reg [31:0] shift_reg = 32'b0;

    (* keep *) GW_JTAG u_jtag (
        .tck_pad_i(1'b0), .tms_pad_i(1'b0), .tdi_pad_i(1'b0), .tdo_pad_o(),
        .tck_o(jtag_tck), .tdi_o(jtag_tdi), .test_logic_reset_o(),
        .run_test_idle_er1_o(), .run_test_idle_er2_o(),
        .shift_dr_capture_dr_o(shift_capture), .pause_dr_o(), .update_dr_o(),
        .enable_er1_o(enable_er1), .enable_er2_o(),
        .tdo_er1_i(shift_reg[0]), .tdo_er2_i(1'b0)
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
    OSER10 u_ser_green (
        .D0(tmds_green[0]), .D1(tmds_green[1]), .D2(tmds_green[2]),
        .D3(tmds_green[3]), .D4(tmds_green[4]), .D5(tmds_green[5]),
        .D6(tmds_green[6]), .D7(tmds_green[7]), .D8(tmds_green[8]),
        .D9(tmds_green[9]), .FCLK(serial_clk), .PCLK(pixel_clk),
        .RESET(reset), .Q(serialized[1])
    );
    OSER10 u_ser_red (
        .D0(tmds_red[0]), .D1(tmds_red[1]), .D2(tmds_red[2]),
        .D3(tmds_red[3]), .D4(tmds_red[4]), .D5(tmds_red[5]),
        .D6(tmds_red[6]), .D7(tmds_red[7]), .D8(tmds_red[8]),
        .D9(tmds_red[9]), .FCLK(serial_clk), .PCLK(pixel_clk),
        .RESET(reset), .Q(serialized[2])
    );
    ELVDS_OBUF u_clk (.I(pixel_clk),    .O(tmds_clk_p),     .OB(tmds_clk_n));
    ELVDS_OBUF u_d0  (.I(serialized[0]), .O(tmds_data_p[0]), .OB(tmds_data_n[0]));
    ELVDS_OBUF u_d1  (.I(serialized[1]), .O(tmds_data_p[1]), .OB(tmds_data_n[1]));
    ELVDS_OBUF u_d2  (.I(serialized[2]), .O(tmds_data_p[2]), .OB(tmds_data_n[2]));
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
    output wire lock
);
    wire clkfbout;

    // X0Y45 is the hardware-proven TLPLL1 site.  Other currently advertised
    // GW5AST PLL sites can route in nextpnr but still need fuse-map validation.
`ifdef OPEN_SOURCE
    (* BEL = "X0Y45/PLL" *) PLLA u_pll (
        .CLKIN(clkin), .CLKFB(1'b0), .RESET(1'b0), .PLLPWD(1'b0),
        .RESET_I(1'b0), .RESET_O(1'b0),
        .PSSEL(3'b000), .PSDIR(1'b0), .PSPULSE(1'b0),
        .SSCPOL(1'b0), .SSCON(1'b0), .SSCMDSEL(7'b0000000),
        .SSCMDSEL_FRAC(3'b000), .LOCK(lock), .CLKOUT0(serial_clk),
        .CLKOUT1(), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(),
        .CLKOUT6(), .CLKFBOUT(clkfbout)
    );
`else
    // Gowin's vendor tools name the same GW5AST TLPLL hard block PLL.
    // Keeping this branch buildable gives us an exact vendor oracle for the
    // otherwise identical clocking, serializer, and output-buffer RTL.
    PLL u_pll (
        .CLKIN(clkin), .CLKFB(1'b0), .RESET(1'b0), .PLLPWD(1'b0),
        .RESET_I(1'b0), .RESET_O(1'b0),
        .FBDSEL(6'b0), .IDSEL(6'b0), .MDSEL(7'b0),
        .MDSEL_FRAC(3'b0), .ODSEL0(7'b0), .ODSEL0_FRAC(3'b0),
        .ODSEL1(7'b0), .ODSEL2(7'b0), .ODSEL3(7'b0),
        .ODSEL4(7'b0), .ODSEL5(7'b0), .ODSEL6(7'b0),
        .DT0(4'b0), .DT1(4'b0), .DT2(4'b0), .DT3(4'b0),
        .PSSEL(3'b0), .PSDIR(1'b0), .PSPULSE(1'b0),
        .ENCLK0(1'b1), .ENCLK1(1'b1), .ENCLK2(1'b1),
        .ENCLK3(1'b1), .ENCLK4(1'b1), .ENCLK5(1'b1), .ENCLK6(1'b1),
        .SSCPOL(1'b0), .SSCON(1'b0), .SSCMDSEL(7'b0),
        .SSCMDSEL_FRAC(3'b0), .ICPSEL(6'b0), .LPFRES(3'b0),
        .LPFCAP(2'b0), .LOCK(lock), .CLKOUT0(serial_clk),
        .CLKOUT1(), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(),
        .CLKOUT6(), .CLKFBOUT(clkfbout)
    );
`endif

    defparam u_pll.FCLKIN = "50";
    defparam u_pll.IDIV_SEL = 1;
    defparam u_pll.FBDIV_SEL = 1;
    defparam u_pll.MDIV_SEL = 20;
    defparam u_pll.MDIV_FRAC_SEL = 0;
    defparam u_pll.ODIV0_SEL = 8;
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
