// Standalone DDR3 training check for the Tang Console 138K.
// HDMI is red until init_calib_complete, then green. Same PLL, IP, and
// pins as the console bitstream, with no Voodoo core on the memory bus.
module ddr_cal_top (
    input  wire        clk50,
    output wire        tmds_clk_p,
    output wire        tmds_clk_n,
    output wire [2:0]  tmds_data_p,
    output wire [2:0]  tmds_data_n,
    output wire [14:0] O_ddr_addr,
    output wire [2:0]  O_ddr_ba,
    output wire        O_ddr_cs_n,
    output wire        O_ddr_ras_n,
    output wire        O_ddr_cas_n,
    output wire        O_ddr_we_n,
    output wire        O_ddr_clk,
    output wire        O_ddr_clk_n,
    output wire        O_ddr_cke,
    output wire        O_ddr_odt,
    output wire        O_ddr_reset_n,
    output wire [1:0]  O_ddr_dqm,
    inout  wire [15:0] IO_ddr_dq,
    inout  wire [1:0]  IO_ddr_dqs,
    inout  wire [1:0]  IO_ddr_dqs_n
);
    wire clk27;
    wire pll_lock_27;
    wire pll_lock;
    wire clk_mem;
    wire ddr_pll_stop;
    wire clk_mem_unused;
    wire pll_hdmi_lock;
    wire clk_hdmi_serial;
    wire clk_hdmi_pixel;

    pll_27 u_pll_27 (
        .clkin    (clk50),
        .init_clk (clk50),
        .clkout0  (clk27),
        .lock     (pll_lock_27)
    );

    pll_ddr3 u_pll_ddr (
        .clkin    (clk27),
        .init_clk (clk50),
        .enclk0   (1'b1),
        .enclk2   (ddr_pll_stop),
        .clkout0  (clk_mem_unused),
        .clkout2  (clk_mem),
        .lock     (pll_lock),
        .reset    (~pll_lock_27)
    );

    Gowin_PLL_Hdmi u_pll_hdmi (
        .lock    (pll_hdmi_lock),
        .clkout0 (clk_hdmi_serial),
        .clkin   (clk50)
    );

    CLKDIV #(.DIV_MODE("5")) u_hdmi_div (
        .HCLKIN (clk_hdmi_serial),
        .RESETN (pll_hdmi_lock),
        .CALIB  (1'b0),
        .CLKOUT (clk_hdmi_pixel)
    );

    wire        init_calib_complete;
    wire        ddr_clk_out;
    wire        ddr_rst;
    wire        cmd_ready;
    wire        wr_data_rdy;
    wire [127:0] rd_data;
    wire        rd_data_valid;
    wire        rd_data_end;
    wire        sr_ack;
    wire        ref_ack;

    DDR3_Memory_Interface_Top u_ddr3 (
        .clk                 (clk50),
        .pll_stop            (ddr_pll_stop),
        .memory_clk          (clk_mem),
        .pll_lock            (pll_lock),
        .rst_n               (1'b1),
        .clk_out             (ddr_clk_out),
        .ddr_rst             (ddr_rst),
        .init_calib_complete (init_calib_complete),
        .cmd_ready           (cmd_ready),
        .cmd                 (3'b0),
        .cmd_en              (1'b0),
        .addr                (28'b0),
        .wr_data_rdy         (wr_data_rdy),
        .wr_data             (128'b0),
        .wr_data_en          (1'b0),
        .wr_data_end         (1'b0),
        .wr_data_mask        (16'b0),
        .rd_data             (rd_data),
        .rd_data_valid       (rd_data_valid),
        .rd_data_end         (rd_data_end),
        .sr_req              (1'b0),
        .ref_req             (1'b0),
        .sr_ack              (sr_ack),
        .ref_ack             (ref_ack),
        .burst               (1'b1),
        .O_ddr_addr          (O_ddr_addr),
        .O_ddr_ba            (O_ddr_ba),
        .O_ddr_cs_n          (O_ddr_cs_n),
        .O_ddr_ras_n         (O_ddr_ras_n),
        .O_ddr_cas_n         (O_ddr_cas_n),
        .O_ddr_we_n          (O_ddr_we_n),
        .O_ddr_clk           (O_ddr_clk),
        .O_ddr_clk_n         (O_ddr_clk_n),
        .O_ddr_cke           (O_ddr_cke),
        .O_ddr_odt           (O_ddr_odt),
        .O_ddr_reset_n       (O_ddr_reset_n),
        .O_ddr_dqm           (O_ddr_dqm),
        .IO_ddr_dq           (IO_ddr_dq),
        .IO_ddr_dqs          (IO_ddr_dqs),
        .IO_ddr_dqs_n        (IO_ddr_dqs_n)
    );

    // 640x480 @ ~25.2 MHz. Red until training finishes, then green.
    localparam H_ACTIVE = 16'd640;
    localparam H_FP     = 16'd16;
    localparam H_SYNC   = 16'd96;
    localparam H_BP     = 16'd48;
    localparam H_TOTAL  = H_ACTIVE + H_FP + H_SYNC + H_BP;
    localparam V_ACTIVE = 16'd480;
    localparam V_FP     = 16'd10;
    localparam V_SYNC   = 16'd2;
    localparam V_BP     = 16'd33;
    localparam V_TOTAL  = V_ACTIVE + V_FP + V_SYNC + V_BP;

    reg [15:0] hcount;
    reg [15:0] vcount;
    wire hlast = hcount == H_TOTAL - 1;
    wire vlast = vcount == V_TOTAL - 1;

    always @(posedge clk_hdmi_pixel) begin
        if (!pll_hdmi_lock) begin
            hcount <= 16'd0;
            vcount <= 16'd0;
        end else if (hlast) begin
            hcount <= 16'd0;
            vcount <= vlast ? 16'd0 : vcount + 16'd1;
        end else begin
            hcount <= hcount + 16'd1;
        end
    end

    wire de    = (hcount < H_ACTIVE) && (vcount < V_ACTIVE);
    wire hsync = (hcount >= H_ACTIVE + H_FP) && (hcount < H_ACTIVE + H_FP + H_SYNC);
    wire vsync = (vcount >= V_ACTIVE + V_FP) && (vcount < V_ACTIVE + V_FP + V_SYNC);
    wire [7:0] red   = init_calib_complete ? 8'h00 : 8'hff;
    wire [7:0] green = init_calib_complete ? 8'hff : 8'h00;

    DVI_TX_Top u_dvi (
        .I_rst_n      (pll_hdmi_lock),
        .I_serial_clk (clk_hdmi_serial),
        .I_rgb_clk    (clk_hdmi_pixel),
        .I_rgb_vs     (vsync),
        .I_rgb_hs     (hsync),
        .I_rgb_de     (de),
        .I_rgb_r      (red),
        .I_rgb_g      (green),
        .I_rgb_b      (8'h00),
        .O_tmds_clk_p (tmds_clk_p),
        .O_tmds_clk_n (tmds_clk_n),
        .O_tmds_data_p(tmds_data_p),
        .O_tmds_data_n(tmds_data_n)
    );

    wire _unused = &{1'b0, clk_mem_unused, ddr_clk_out, ddr_rst, cmd_ready,
                     wr_data_rdy, rd_data, rd_data_valid, rd_data_end, sr_ack, ref_ack};
endmodule
