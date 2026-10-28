// DVI (TMDS) transmitter for 7-series: three TMDS encoders and 10:1
// OSERDESE2 master/slave pairs, clock lane sent as 1111100000.
// pix_clk = pixel clock, ser_clk = 5 x pixel clock (DDR serialisation),
// both from the same MMCM, in phase.

module tmds_encoder (
    input  wire       clk,
    input  wire [7:0] d,
    input  wire [1:0] c,
    input  wire       de,
    output reg  [9:0] q
);
    function [3:0] ones8(input [7:0] v);
        ones8 = v[0] + v[1] + v[2] + v[3] + v[4] + v[5] + v[6] + v[7];
    endfunction

    wire [3:0] n1d = ones8(d);
    wire use_xnor = (n1d > 4) || (n1d == 4 && d[0] == 1'b0);
    wire [8:0] qm;
    assign qm[0] = d[0];
    genvar i;
    generate
        for (i = 1; i < 8; i = i + 1) begin : g_qm
            assign qm[i] = use_xnor ? ~(qm[i-1] ^ d[i]) : (qm[i-1] ^ d[i]);
        end
    endgenerate
    assign qm[8] = ~use_xnor;

    wire [3:0] n1q = ones8(qm[7:0]);
    wire [3:0] n0q = 4'd8 - n1q;
    // Running disparity, signed (ones minus zeros sent so far, / 2 units).
    reg signed [4:0] cnt = 0;

    always @(posedge clk) begin
        if (!de) begin
            cnt <= 0;
            case (c)
                2'b00: q <= 10'b1101010100;
                2'b01: q <= 10'b0010101011;
                2'b10: q <= 10'b0101010100;
                default: q <= 10'b1010101011;
            endcase
        end else if (cnt == 0 || n1q == n0q) begin
            q <= {~qm[8], qm[8], qm[8] ? qm[7:0] : ~qm[7:0]};
            if (qm[8]) cnt <= cnt + $signed({1'b0, n1q}) - $signed({1'b0, n0q});
            else       cnt <= cnt + $signed({1'b0, n0q}) - $signed({1'b0, n1q});
        end else if ((cnt > 0 && n1q > n0q) || (cnt < 0 && n0q > n1q)) begin
            q <= {1'b1, qm[8], ~qm[7:0]};
            cnt <= cnt + $signed({3'b0, qm[8], 1'b0}) + $signed({1'b0, n0q}) - $signed({1'b0, n1q});
        end else begin
            q <= {1'b0, qm[8], qm[7:0]};
            cnt <= cnt - $signed({3'b0, ~qm[8], 1'b0}) + $signed({1'b0, n1q}) - $signed({1'b0, n0q});
        end
    end
endmodule

module tmds_serializer (
    input  wire       pix_clk,
    input  wire       ser_clk,
    input  wire       rst,
    input  wire [9:0] d,
    output wire       p,
    output wire       n
);
    wire ser, shift1, shift2;
    // Bit 0 goes out first (D1).
    OSERDESE2 #(
        .DATA_RATE_OQ("DDR"), .DATA_RATE_TQ("SDR"), .DATA_WIDTH(10),
        .SERDES_MODE("MASTER"), .TRISTATE_WIDTH(1)
    ) master (
        .OQ(ser), .OFB(), .TQ(), .TFB(), .SHIFTOUT1(), .SHIFTOUT2(), .TBYTEOUT(),
        .CLK(ser_clk), .CLKDIV(pix_clk),
        .D1(d[0]), .D2(d[1]), .D3(d[2]), .D4(d[3]), .D5(d[4]), .D6(d[5]), .D7(d[6]), .D8(d[7]),
        .OCE(1'b1), .RST(rst),
        .SHIFTIN1(shift1), .SHIFTIN2(shift2),
        .T1(1'b0), .T2(1'b0), .T3(1'b0), .T4(1'b0), .TBYTEIN(1'b0), .TCE(1'b0)
    );
    OSERDESE2 #(
        .DATA_RATE_OQ("DDR"), .DATA_RATE_TQ("SDR"), .DATA_WIDTH(10),
        .SERDES_MODE("SLAVE"), .TRISTATE_WIDTH(1)
    ) slave (
        .OQ(), .OFB(), .TQ(), .TFB(), .SHIFTOUT1(shift1), .SHIFTOUT2(shift2), .TBYTEOUT(),
        .CLK(ser_clk), .CLKDIV(pix_clk),
        .D1(1'b0), .D2(1'b0), .D3(d[8]), .D4(d[9]), .D5(1'b0), .D6(1'b0), .D7(1'b0), .D8(1'b0),
        .OCE(1'b1), .RST(rst),
        .SHIFTIN1(1'b0), .SHIFTIN2(1'b0),
        .T1(1'b0), .T2(1'b0), .T3(1'b0), .T4(1'b0), .TBYTEIN(1'b0), .TCE(1'b0)
    );
    OBUFDS #(.IOSTANDARD("TMDS_33")) obuf (.I(ser), .O(p), .OB(n));
endmodule

module dvi_tx (
    input  wire       pix_clk,
    input  wire       ser_clk,
    input  wire       rst,
    input  wire [7:0] r,
    input  wire [7:0] g,
    input  wire [7:0] b,
    input  wire       hs,
    input  wire       vs,
    input  wire       de,
    output wire       tmds_clk_p,
    output wire       tmds_clk_n,
    output wire [2:0] tmds_d_p,
    output wire [2:0] tmds_d_n
);
    wire [9:0] q_b, q_g, q_r;
    // Sync on the blue channel (C0 = hsync, C1 = vsync).
    tmds_encoder enc_b (.clk(pix_clk), .d(b), .c({vs, hs}), .de(de), .q(q_b));
    tmds_encoder enc_g (.clk(pix_clk), .d(g), .c(2'b00),    .de(de), .q(q_g));
    tmds_encoder enc_r (.clk(pix_clk), .d(r), .c(2'b00),    .de(de), .q(q_r));

    tmds_serializer ser_b (.pix_clk(pix_clk), .ser_clk(ser_clk), .rst(rst), .d(q_b), .p(tmds_d_p[0]), .n(tmds_d_n[0]));
    tmds_serializer ser_g (.pix_clk(pix_clk), .ser_clk(ser_clk), .rst(rst), .d(q_g), .p(tmds_d_p[1]), .n(tmds_d_n[1]));
    tmds_serializer ser_r (.pix_clk(pix_clk), .ser_clk(ser_clk), .rst(rst), .d(q_r), .p(tmds_d_p[2]), .n(tmds_d_n[2]));
    tmds_serializer ser_c (.pix_clk(pix_clk), .ser_clk(ser_clk), .rst(rst), .d(10'b0000011111), .p(tmds_clk_p), .n(tmds_clk_n));
endmodule
