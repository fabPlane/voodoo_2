// AMD Artix-7 AC701 (XC7A200T-2FBG676) — SpinalVoodoo board wrapper.
// Instantiates Xilinx IP (generate these in Vivado; see ip.md) and the
// SpinalHDL fabric AmdAc701. Pinout from LiteX xilinx_ac701.

module board_top (
    input  wire        clk200_p,
    input  wire        clk200_n,
    input  wire        clk156_p,
    input  wire        clk156_n,
    input  wire        cpu_reset,
    input  wire        pcie_rst_n,
    input  wire        pcie_clk_p,
    input  wire        pcie_clk_n,
    output wire [3:0]  pci_exp_txp,
    output wire [3:0]  pci_exp_txn,
    input  wire [3:0]  pci_exp_rxp,
    input  wire [3:0]  pci_exp_rxn,
    output wire [3:0]  led,
    output wire [23:0] hdmi_d,
    output wire        hdmi_hs,
    output wire        hdmi_vs,
    output wire        hdmi_de,
    output wire        hdmi_clk,
    inout  wire        i2c_scl,
    inout  wire        i2c_sda,
    output wire        i2c_mux_reset_n,
    inout  wire [31:0] ddr3_dq,
    inout  wire [3:0]  ddr3_dqs_n,
    inout  wire [3:0]  ddr3_dqs_p,
    output wire [15:0] ddr3_addr,
    output wire [2:0]  ddr3_ba,
    output wire        ddr3_ras_n,
    output wire        ddr3_cas_n,
    output wire        ddr3_we_n,
    output wire        ddr3_reset_n,
    output wire        ddr3_ck_p,
    output wire        ddr3_ck_n,
    output wire        ddr3_cke,
    output wire        ddr3_cs_n,
    output wire [3:0]  ddr3_dm,
    output wire        ddr3_odt
);

    wire clk156;
    IBUFDS u_clk156 (
        .I  (clk156_p),
        .IB (clk156_n),
        .O  (clk156)
    );

    wire clk_hdmi;
    wire clk_hdmi_fb;
    wire pll_hdmi_lock;
    MMCME2_BASE #(
        .CLKIN1_PERIOD   (6.4),
        .CLKFBOUT_MULT_F (6.000),
        .CLKOUT0_DIVIDE_F(37.250),
        .DIVCLK_DIVIDE   (1)
    ) u_hdmi_mmcm (
        .CLKIN1   (clk156),
        .CLKFBIN  (clk_hdmi_fb),
        .CLKFBOUT (clk_hdmi_fb),
        .CLKOUT0  (clk_hdmi),
        .LOCKED   (pll_hdmi_lock),
        .RST      (cpu_reset),
        .PWRDWN   (1'b0)
    );
    wire hdmi_rst = ~pll_hdmi_lock;

    wire pcie_clk;
    wire pcie_rst;
    wire pcie_linkup;
    wire [31:0] bar_awaddr, bar_wdata, bar_araddr, bar_rdata;
    wire [3:0]  bar_awid, bar_wid, bar_bid, bar_arid, bar_rid;
    wire [7:0]  bar_awlen, bar_arlen;
    wire [2:0]  bar_awsize, bar_arsize;
    wire [1:0]  bar_awburst, bar_arburst, bar_bresp, bar_rresp;
    wire [3:0]  bar_wstrb;
    wire        bar_awvalid, bar_awready, bar_wvalid, bar_wready, bar_wlast;
    wire        bar_bvalid, bar_bready, bar_arvalid, bar_arready;
    wire        bar_rvalid, bar_rready, bar_rlast;

    axi_pcie_0 u_pcie (
        .pci_exp_txp    (pci_exp_txp),
        .pci_exp_txn    (pci_exp_txn),
        .pci_exp_rxp    (pci_exp_rxp),
        .pci_exp_rxn    (pci_exp_rxn),
        .sys_clk_p      (pcie_clk_p),
        .sys_clk_n      (pcie_clk_n),
        .sys_rst_n      (pcie_rst_n),
        .user_clk_out   (pcie_clk),
        .user_reset_out (pcie_rst),
        .user_link_up   (pcie_linkup),
        .m_axi_awid     (bar_awid),
        .m_axi_awaddr   (bar_awaddr),
        .m_axi_awlen    (bar_awlen),
        .m_axi_awsize   (bar_awsize),
        .m_axi_awburst  (bar_awburst),
        .m_axi_awvalid  (bar_awvalid),
        .m_axi_awready  (bar_awready),
        .m_axi_wdata    (bar_wdata),
        .m_axi_wstrb    (bar_wstrb),
        .m_axi_wlast    (bar_wlast),
        .m_axi_wvalid   (bar_wvalid),
        .m_axi_wready   (bar_wready),
        .m_axi_bid      (bar_bid),
        .m_axi_bresp    (bar_bresp),
        .m_axi_bvalid   (bar_bvalid),
        .m_axi_bready   (bar_bready),
        .m_axi_arid     (bar_arid),
        .m_axi_araddr   (bar_araddr),
        .m_axi_arlen    (bar_arlen),
        .m_axi_arsize   (bar_arsize),
        .m_axi_arburst  (bar_arburst),
        .m_axi_arvalid  (bar_arvalid),
        .m_axi_arready  (bar_arready),
        .m_axi_rid      (bar_rid),
        .m_axi_rdata    (bar_rdata),
        .m_axi_rresp    (bar_rresp),
        .m_axi_rlast    (bar_rlast),
        .m_axi_rvalid   (bar_rvalid),
        .m_axi_rready   (bar_rready)
    );

    wire        ui_clk;
    wire        ui_rst;
    wire        init_calib_complete;

    wire [31:0] fb_awaddr, fb_wdata, fb_araddr, fb_rdata;
    wire [3:0]  fb_wstrb;
    wire        fb_awvalid, fb_awready, fb_wvalid, fb_wready, fb_wlast;
    wire        fb_bvalid, fb_bready, fb_arvalid, fb_arready;
    wire        fb_rvalid, fb_rready, fb_rlast;
    wire [1:0]  fb_bresp, fb_rresp;
    wire [7:0]  fb_awlen, fb_arlen;
    wire [2:0]  fb_awsize, fb_arsize;
    wire [1:0]  fb_awburst, fb_arburst;

    wire [31:0] tex_awaddr, tex_wdata, tex_araddr, tex_rdata;
    wire [3:0]  tex_wstrb;
    wire        tex_awvalid, tex_awready, tex_wvalid, tex_wready, tex_wlast;
    wire        tex_bvalid, tex_bready, tex_arvalid, tex_arready;
    wire        tex_rvalid, tex_rready, tex_rlast;
    wire [1:0]  tex_bresp, tex_rresp;
    wire [7:0]  tex_awlen, tex_arlen;
    wire [2:0]  tex_awsize, tex_arsize;
    wire [1:0]  tex_awburst, tex_arburst;

    wire [31:0] mig_awaddr, mig_wdata, mig_araddr, mig_rdata;
    wire [3:0]  mig_awlen, mig_arlen;
    wire [2:0]  mig_awsize, mig_arsize;
    wire [1:0]  mig_awburst, mig_arburst, mig_bresp, mig_rresp;
    wire [15:0] mig_wstrb;
    wire        mig_awvalid, mig_awready, mig_wvalid, mig_wready, mig_wlast;
    wire        mig_bvalid, mig_bready, mig_arvalid, mig_arready;
    wire        mig_rvalid, mig_rready, mig_rlast;
    wire [127:0] mig_rdata128, mig_wdata128;

    axi_mem_intercon u_mem (
        .ACLK           (ui_clk),
        .ARESETN        (~ui_rst),
        .S00_AXI_awaddr (fb_awaddr),
        .S00_AXI_awlen  (fb_awlen),
        .S00_AXI_awsize (fb_awsize),
        .S00_AXI_awburst(fb_awburst),
        .S00_AXI_awvalid(fb_awvalid),
        .S00_AXI_awready(fb_awready),
        .S00_AXI_wdata  (fb_wdata),
        .S00_AXI_wstrb  (fb_wstrb),
        .S00_AXI_wlast  (fb_wlast),
        .S00_AXI_wvalid (fb_wvalid),
        .S00_AXI_wready (fb_wready),
        .S00_AXI_bresp  (fb_bresp),
        .S00_AXI_bvalid (fb_bvalid),
        .S00_AXI_bready (fb_bready),
        .S00_AXI_araddr (fb_araddr),
        .S00_AXI_arlen  (fb_arlen),
        .S00_AXI_arsize (fb_arsize),
        .S00_AXI_arburst(fb_arburst),
        .S00_AXI_arvalid(fb_arvalid),
        .S00_AXI_arready(fb_arready),
        .S00_AXI_rdata  (fb_rdata),
        .S00_AXI_rresp  (fb_rresp),
        .S00_AXI_rlast  (fb_rlast),
        .S00_AXI_rvalid (fb_rvalid),
        .S00_AXI_rready (fb_rready),
        .S01_AXI_awaddr (tex_awaddr),
        .S01_AXI_awlen  (tex_awlen),
        .S01_AXI_awsize (tex_awsize),
        .S01_AXI_awburst(tex_awburst),
        .S01_AXI_awvalid(tex_awvalid),
        .S01_AXI_awready(tex_awready),
        .S01_AXI_wdata  (tex_wdata),
        .S01_AXI_wstrb  (tex_wstrb),
        .S01_AXI_wlast  (tex_wlast),
        .S01_AXI_wvalid (tex_wvalid),
        .S01_AXI_wready (tex_wready),
        .S01_AXI_bresp  (tex_bresp),
        .S01_AXI_bvalid (tex_bvalid),
        .S01_AXI_bready (tex_bready),
        .S01_AXI_araddr (tex_araddr),
        .S01_AXI_arlen  (tex_arlen),
        .S01_AXI_arsize (tex_arsize),
        .S01_AXI_arburst(tex_arburst),
        .S01_AXI_arvalid(tex_arvalid),
        .S01_AXI_arready(tex_arready),
        .S01_AXI_rdata  (tex_rdata),
        .S01_AXI_rresp  (tex_rresp),
        .S01_AXI_rlast  (tex_rlast),
        .S01_AXI_rvalid (tex_rvalid),
        .S01_AXI_rready (tex_rready)
    );

    mig_7series_0 u_mig (
        .sys_clk_p           (clk200_p),
        .sys_clk_n           (clk200_n),
        .sys_rst             (cpu_reset),
        .ui_clk              (ui_clk),
        .ui_clk_sync_rst     (ui_rst),
        .init_calib_complete (init_calib_complete),
        .ddr3_addr           (ddr3_addr),
        .ddr3_ba             (ddr3_ba),
        .ddr3_cas_n          (ddr3_cas_n),
        .ddr3_ck_n           (ddr3_ck_n),
        .ddr3_ck_p           (ddr3_ck_p),
        .ddr3_cke            (ddr3_cke),
        .ddr3_cs_n           (ddr3_cs_n),
        .ddr3_dm             (ddr3_dm),
        .ddr3_dq             (ddr3_dq),
        .ddr3_dqs_n          (ddr3_dqs_n),
        .ddr3_dqs_p          (ddr3_dqs_p),
        .ddr3_odt            (ddr3_odt),
        .ddr3_ras_n          (ddr3_ras_n),
        .ddr3_reset_n        (ddr3_reset_n),
        .ddr3_we_n           (ddr3_we_n)
    );

    wire [7:0] core_r, core_g, core_b;
    wire       core_hs, core_vs, core_de;
    wire       led_link, led_calib, led_wedge, led_heartbeat;

    AmdAc701 u_voodoo (
        .clk          (ui_clk),
        .reset        (ui_rst | ~init_calib_complete),
        .pcieClk      (pcie_clk),
        .pcieReset    (pcie_rst),
        .pcieLinkup   (pcie_linkup),
        .bar_aw_valid (bar_awvalid),
        .bar_aw_ready (bar_awready),
        .bar_aw_payload_addr (bar_awaddr[23:0]),
        .bar_aw_payload_id   (bar_awid),
        .bar_aw_payload_len (bar_awlen),
        .bar_aw_payload_size(bar_awsize),
        .bar_aw_payload_burst(bar_awburst),
        .bar_aw_payload_prot (3'b0),
        .bar_w_valid  (bar_wvalid),
        .bar_w_ready  (bar_wready),
        .bar_w_payload_data (bar_wdata),
        .bar_w_payload_strb (bar_wstrb),
        .bar_w_payload_last (bar_wlast),
        .bar_b_valid  (bar_bvalid),
        .bar_b_ready  (bar_bready),
        .bar_b_payload_id   (bar_bid),
        .bar_b_payload_resp (bar_bresp),
        .bar_ar_valid (bar_arvalid),
        .bar_ar_ready (bar_arready),
        .bar_ar_payload_addr (bar_araddr[23:0]),
        .bar_ar_payload_id   (bar_arid),
        .bar_ar_payload_len (bar_arlen),
        .bar_ar_payload_size(bar_arsize),
        .bar_ar_payload_burst(bar_arburst),
        .bar_ar_payload_prot (3'b0),
        .bar_r_valid  (bar_rvalid),
        .bar_r_ready  (bar_rready),
        .bar_r_payload_data (bar_rdata),
        .bar_r_payload_id   (bar_rid),
        .bar_r_payload_resp (bar_rresp),
        .bar_r_payload_last (bar_rlast),
        .memFb_aw_valid(fb_awvalid),
        .memFb_aw_ready(fb_awready),
        .memFb_aw_payload_addr(fb_awaddr[25:0]),
        .memFb_aw_payload_len(fb_awlen),
        .memFb_aw_payload_size(fb_awsize),
        .memFb_aw_payload_burst(fb_awburst),
        .memFb_aw_payload_cache(),
        .memFb_aw_payload_prot(),
        .memFb_w_valid(fb_wvalid),
        .memFb_w_ready(fb_wready),
        .memFb_w_payload_data(fb_wdata),
        .memFb_w_payload_strb(fb_wstrb),
        .memFb_w_payload_last(fb_wlast),
        .memFb_b_valid(fb_bvalid),
        .memFb_b_ready(fb_bready),
        .memFb_b_payload_resp(fb_bresp),
        .memFb_ar_valid(fb_arvalid),
        .memFb_ar_ready(fb_arready),
        .memFb_ar_payload_addr(fb_araddr[25:0]),
        .memFb_ar_payload_len(fb_arlen),
        .memFb_ar_payload_size(fb_arsize),
        .memFb_ar_payload_burst(fb_arburst),
        .memFb_ar_payload_cache(),
        .memFb_ar_payload_prot(),
        .memFb_r_valid(fb_rvalid),
        .memFb_r_ready(fb_rready),
        .memFb_r_payload_data(fb_rdata),
        .memFb_r_payload_resp(fb_rresp),
        .memFb_r_payload_last(fb_rlast),
        .memTex_aw_valid(tex_awvalid),
        .memTex_aw_ready(tex_awready),
        .memTex_aw_payload_addr(tex_awaddr[25:0]),
        .memTex_aw_payload_len(tex_awlen),
        .memTex_aw_payload_size(tex_awsize),
        .memTex_aw_payload_burst(tex_awburst),
        .memTex_aw_payload_cache(),
        .memTex_aw_payload_prot(),
        .memTex_w_valid(tex_wvalid),
        .memTex_w_ready(tex_wready),
        .memTex_w_payload_data(tex_wdata),
        .memTex_w_payload_strb(tex_wstrb),
        .memTex_w_payload_last(tex_wlast),
        .memTex_b_valid(tex_bvalid),
        .memTex_b_ready(tex_bready),
        .memTex_b_payload_resp(tex_bresp),
        .memTex_ar_valid(tex_arvalid),
        .memTex_ar_ready(tex_arready),
        .memTex_ar_payload_addr(tex_araddr[25:0]),
        .memTex_ar_payload_len(tex_arlen),
        .memTex_ar_payload_size(tex_arsize),
        .memTex_ar_payload_burst(tex_arburst),
        .memTex_ar_payload_cache(),
        .memTex_ar_payload_prot(),
        .memTex_r_valid(tex_rvalid),
        .memTex_r_ready(tex_rready),
        .memTex_r_payload_data(tex_rdata),
        .memTex_r_payload_resp(tex_rresp),
        .memTex_r_payload_last(tex_rlast),
        .ddrCalib     (init_calib_complete),
        .hdmiClk      (clk_hdmi),
        .hdmiReset    (hdmi_rst),
        .hdmiR        (core_r),
        .hdmiG        (core_g),
        .hdmiB        (core_b),
        .hdmiHs       (core_hs),
        .hdmiVs       (core_vs),
        .hdmiDe       (core_de),
        .ledLink      (led_link),
        .ledCalib     (led_calib),
        .ledWedge     (led_wedge),
        .ledHeartbeat (led_heartbeat)
    );

    assign fb_awaddr[31:26] = 6'b0;
    assign fb_araddr[31:26] = 6'b0;
    assign tex_awaddr[31:26] = 6'b0;
    assign tex_araddr[31:26] = 6'b0;

    // UG952 24-bit map: D[11:4]=B, D[23:16]=G, D[35:28]=R
    assign hdmi_d  = {core_r, core_g, core_b};
    assign hdmi_hs = core_hs;
    assign hdmi_vs = core_vs;
    assign hdmi_de = core_de;
    assign hdmi_clk = clk_hdmi;

    assign i2c_mux_reset_n = ~cpu_reset;

    adv7511_i2c u_hdmi_i2c (
        .clk     (clk156),
        .reset   (cpu_reset),
        .scl     (i2c_scl),
        .sda     (i2c_sda)
    );

    assign led[0] = led_heartbeat;
    assign led[1] = led_link;
    assign led[2] = led_calib;
    assign led[3] = led_wedge;

    wire _unused = &{1'b0, bar_wid, fb_awvalid, tex_awvalid, mig_awvalid,
                     mig_rdata128, mig_wdata128, mig_wstrb};

endmodule
