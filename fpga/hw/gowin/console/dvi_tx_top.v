// Parallel RGB888 + HS/VS/DE to TMDS for GW5A. Clock channel is the pixel
// clock; data channels are 10:1 OSER10. Replaces Gowin DVI_TX_Top (Education
// Tcl cannot generate that IP).
module DVI_TX_Top (
    input        I_rst_n,
    input        I_serial_clk,
    input        I_rgb_clk,
    input        I_rgb_vs,
    input        I_rgb_hs,
    input        I_rgb_de,
    input  [7:0] I_rgb_r,
    input  [7:0] I_rgb_g,
    input  [7:0] I_rgb_b,
    output       O_tmds_clk_p,
    output       O_tmds_clk_n,
    output [2:0] O_tmds_data_p,
    output [2:0] O_tmds_data_n
);
    wire reset = ~I_rst_n;
    wire [9:0] tmds_r, tmds_g, tmds_b;
    wire [2:0] tmds_s;

    tmds_encode u_b (
        .clk (I_rgb_clk),
        .rst (reset),
        .de  (I_rgb_de),
        .d   (I_rgb_b),
        .c0  (I_rgb_hs),
        .c1  (I_rgb_vs),
        .q   (tmds_b)
    );
    tmds_encode u_g (
        .clk (I_rgb_clk),
        .rst (reset),
        .de  (I_rgb_de),
        .d   (I_rgb_g),
        .c0  (1'b0),
        .c1  (1'b0),
        .q   (tmds_g)
    );
    tmds_encode u_r (
        .clk (I_rgb_clk),
        .rst (reset),
        .de  (I_rgb_de),
        .d   (I_rgb_r),
        .c0  (1'b0),
        .c1  (1'b0),
        .q   (tmds_r)
    );

    OSER10 u_ser_b (
        .Q(tmds_s[0]),
        .D0(tmds_b[0]), .D1(tmds_b[1]), .D2(tmds_b[2]), .D3(tmds_b[3]), .D4(tmds_b[4]),
        .D5(tmds_b[5]), .D6(tmds_b[6]), .D7(tmds_b[7]), .D8(tmds_b[8]), .D9(tmds_b[9]),
        .PCLK(I_rgb_clk), .FCLK(I_serial_clk), .RESET(reset)
    );
    OSER10 u_ser_g (
        .Q(tmds_s[1]),
        .D0(tmds_g[0]), .D1(tmds_g[1]), .D2(tmds_g[2]), .D3(tmds_g[3]), .D4(tmds_g[4]),
        .D5(tmds_g[5]), .D6(tmds_g[6]), .D7(tmds_g[7]), .D8(tmds_g[8]), .D9(tmds_g[9]),
        .PCLK(I_rgb_clk), .FCLK(I_serial_clk), .RESET(reset)
    );
    OSER10 u_ser_r (
        .Q(tmds_s[2]),
        .D0(tmds_r[0]), .D1(tmds_r[1]), .D2(tmds_r[2]), .D3(tmds_r[3]), .D4(tmds_r[4]),
        .D5(tmds_r[5]), .D6(tmds_r[6]), .D7(tmds_r[7]), .D8(tmds_r[8]), .D9(tmds_r[9]),
        .PCLK(I_rgb_clk), .FCLK(I_serial_clk), .RESET(reset)
    );

    ELVDS_OBUF u_clk  (.O(O_tmds_clk_p),    .OB(O_tmds_clk_n),    .I(I_rgb_clk));
    ELVDS_OBUF u_d0   (.O(O_tmds_data_p[0]), .OB(O_tmds_data_n[0]), .I(tmds_s[0]));
    ELVDS_OBUF u_d1   (.O(O_tmds_data_p[1]), .OB(O_tmds_data_n[1]), .I(tmds_s[1]));
    ELVDS_OBUF u_d2   (.O(O_tmds_data_p[2]), .OB(O_tmds_data_n[2]), .I(tmds_s[2]));
endmodule

module tmds_encode (
    input        clk,
    input        rst,
    input        de,
    input  [7:0] d,
    input        c0,
    input        c1,
    output reg [9:0] q
);
    wire [3:0] n1d = d[0] + d[1] + d[2] + d[3] + d[4] + d[5] + d[6] + d[7];
    wire       xnor_sel = (n1d > 4) || (n1d == 4 && d[0] == 1'b0);
    wire [8:0] q_m = xnor_sel
        ? {1'b0, q_m_xnor}
        : {1'b1, q_m_xor};
    wire [7:0] q_m_xor;
    wire [7:0] q_m_xnor;
    assign q_m_xor[0]  = d[0];
    assign q_m_xnor[0] = d[0];
    assign q_m_xor[1]  = q_m_xor[0]  ^ d[1];
    assign q_m_xnor[1] = q_m_xnor[0] ~^ d[1];
    assign q_m_xor[2]  = q_m_xor[1]  ^ d[2];
    assign q_m_xnor[2] = q_m_xnor[1] ~^ d[2];
    assign q_m_xor[3]  = q_m_xor[2]  ^ d[3];
    assign q_m_xnor[3] = q_m_xnor[2] ~^ d[3];
    assign q_m_xor[4]  = q_m_xor[3]  ^ d[4];
    assign q_m_xnor[4] = q_m_xnor[3] ~^ d[4];
    assign q_m_xor[5]  = q_m_xor[4]  ^ d[5];
    assign q_m_xnor[5] = q_m_xnor[4] ~^ d[5];
    assign q_m_xor[6]  = q_m_xor[5]  ^ d[6];
    assign q_m_xnor[6] = q_m_xnor[5] ~^ d[6];
    assign q_m_xor[7]  = q_m_xor[6]  ^ d[7];
    assign q_m_xnor[7] = q_m_xnor[6] ~^ d[7];

    wire [3:0] n1qm = q_m[0]+q_m[1]+q_m[2]+q_m[3]+q_m[4]+q_m[5]+q_m[6]+q_m[7];
    integer disparity;
    wire [4:0] diff = {1'b0, n1qm} - 5'd4;

    always @(posedge clk) begin
        if (rst) begin
            disparity <= 0;
            q <= 10'b1101010100;
        end else if (!de) begin
            disparity <= 0;
            case ({c1, c0})
                2'b00: q <= 10'b1101010100;
                2'b01: q <= 10'b0010101011;
                2'b10: q <= 10'b0101010100;
                default: q <= 10'b1010101011;
            endcase
        end else begin
            if (disparity == 0 || n1qm == 4) begin
                if (q_m[8] == 1'b0) begin
                    q <= {1'b0, q_m[8], ~q_m[7:0]};
                    disparity <= disparity - $signed({diff[4], diff});
                end else begin
                    q <= {1'b1, q_m[8], q_m[7:0]};
                    disparity <= disparity + $signed({diff[4], diff});
                end
            end else if ((disparity > 0 && n1qm > 4) || (disparity < 0 && n1qm < 4)) begin
                q <= {1'b0, q_m[8], ~q_m[7:0]};
                disparity <= disparity + (q_m[8] ? 2 : 0) - $signed({diff[4], diff});
            end else begin
                q <= {1'b1, q_m[8], q_m[7:0]};
                disparity <= disparity - (q_m[8] ? 0 : 2) + $signed({diff[4], diff});
            end
        end
    end
endmodule
