// Sipeed Tang Retro Console 138K -- Amaranth HDMI test-pattern demo.
//
// Board wrapper for HdmiDemoTop (avoodoo/gowin/hdmi_demo_top.py), modelled on
// fpga/hw/gowin/console/board_top.v with everything but the HDMI path removed:
//
//   clk50 --> Gowin_PLL_Hdmi --> clk_hdmi_serial (125 MHz; the IP was asked for 126)
//                            --> CLKDIV /5     --> clk_hdmi_pixel (25 MHz)
//   HdmiDemoTop: `sync` = clk50 (heartbeat), `hdmi` = clk_hdmi_pixel (scanout)
//   DVI_TX_Top (dvi_tx_top.v, the Console's hand-written TMDS + OSER10 encoder)
//
// Both Amaranth domains are reset from the PLL lock, as the Console resets
// its hdmi domain. No DDR3, no UART, no soft-USB: those pins are not in
// hdmi_demo.cst either (a CST line for a missing port is an error).

module hdmi_demo_board_top (
    input  wire        clk50,
    input  wire        key0_n,
    output wire [1:0]  led_n,
    output wire        tmds_clk_p,
    output wire        tmds_clk_n,
    output wire [2:0]  tmds_data_p,
    output wire [2:0]  tmds_data_n
);

    wire pll_hdmi_lock;
    wire clk_hdmi_serial;
    wire clk_hdmi_pixel;
    wire hdmi_rst = ~pll_hdmi_lock;

    Gowin_PLL_Hdmi u_pll_hdmi (
        .lock    (pll_hdmi_lock),
        .clkout0 (clk_hdmi_serial),
        .clkin   (clk50)
    );

    CLKDIV #(
        .DIV_MODE("5")
    ) u_hdmi_div (
        .HCLKIN (clk_hdmi_serial),
        .RESETN (pll_hdmi_lock),
        .CALIB  (1'b0),
        .CLKOUT (clk_hdmi_pixel)
    );

    wire [7:0] hdmi_r, hdmi_g, hdmi_b;
    wire       hdmi_hs, hdmi_vs, hdmi_de;

    HdmiDemoTop u_demo (
        .clk      (clk50),
        .rst      (hdmi_rst),
        .hdmi_clk (clk_hdmi_pixel),
        .hdmi_rst (hdmi_rst),
        .key0_n   (key0_n),
        .hdmi_r   (hdmi_r),
        .hdmi_g   (hdmi_g),
        .hdmi_b   (hdmi_b),
        .hdmi_hs  (hdmi_hs),
        .hdmi_vs  (hdmi_vs),
        .hdmi_de  (hdmi_de),
        .led_n    (led_n)
    );

    DVI_TX_Top u_dvi (
        .I_rst_n      (~hdmi_rst),
        .I_serial_clk (clk_hdmi_serial),
        .I_rgb_clk    (clk_hdmi_pixel),
        .I_rgb_vs     (hdmi_vs),
        .I_rgb_hs     (hdmi_hs),
        .I_rgb_de     (hdmi_de),
        .I_rgb_r      (hdmi_r),
        .I_rgb_g      (hdmi_g),
        .I_rgb_b      (hdmi_b),
        .O_tmds_clk_p (tmds_clk_p),
        .O_tmds_clk_n (tmds_clk_n),
        .O_tmds_data_p(tmds_data_p),
        .O_tmds_data_n(tmds_data_n)
    );

endmodule
