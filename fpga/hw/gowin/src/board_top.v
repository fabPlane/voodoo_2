// Sipeed Tang Mega 138K Pro Dock — SpinalVoodoo board wrapper.
// Instantiates Gowin IP (generate these in the IP Core Generator; see ip.md)
// and the SpinalHDL fabric TangMega138kPro.

module board_top #(
    // DRAM on-die termination. Sipeed's DDR3 core sets RTT_NOM = RZQ/12
    // (20 ohm) with dynamic ODT off, so every write burst pulls ~0.5 A
    // through the DQ terminations; the dock reloads itself under DDR3
    // traffic. 0 holds the DRAM ODT pin low (no termination) to test that.
    parameter DDR_ODT_ENABLE = 1
) (
    input  wire        clk50,
    input  wire        pcie_rstn,
    output wire [3:0]  led_n,
    output wire        tmds_clk_p,
    output wire        tmds_clk_n,
    output wire [2:0]  tmds_data_p,
    output wire [2:0]  tmds_data_n,
    input  wire        tmds_hpd,
    // GW_JTAG pads (dedicated JTAG pins, no CST entry) for jtag_diag.
    input  wire        tck_pad_i,
    input  wire        tms_pad_i,
    input  wire        tdi_pad_i,
    output wire        tdo_pad_o,
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
    output wire [3:0]  O_ddr_dqm,
    inout  wire [31:0] IO_ddr_dq,
    inout  wire [3:0]  IO_ddr_dqs,
    inout  wire [3:0]  IO_ddr_dqs_n
);

    wire pll_sys_lock;
    wire ddr_pll_stop;  // DDR3 IP gates memory_clk during calibration
    wire clk100;
    wire clk400;
    wire clk_core;      // Voodoo core clock (46.15 MHz; 50 MHz = SST-1)
    wire pll_hdmi_lock;
    wire clk_hdmi_serial;
    wire clk_hdmi_pixel;
    wire sys_rst = ~pll_sys_lock;
    wire hdmi_rst = ~pll_hdmi_lock;

    Gowin_PLL_Sys u_pll_sys (
        .lock    (pll_sys_lock),
        .clkout0 (clk100),
        .clkout1 (clk400),
        .clkout_core (clk_core),
        .enclk1  (ddr_pll_stop),
        .clkin   (clk50)
    );

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

    wire tlp_clk = clk100;
    wire tlp_rst = sys_rst | ~pcie_rstn;

    wire        tl_rx_sop, tl_rx_eop, tl_rx_wait;
    wire [7:0]  tl_rx_valid;
    wire [255:0] tl_rx_data;
    wire [5:0]  tl_rx_bardec;
    wire        tl_tx_sop, tl_tx_eop, tl_tx_wait;
    wire [7:0]  tl_tx_valid;
    wire [255:0] tl_tx_data;
    wire [31:0] tl_tx_p_credits, tl_tx_np_credits, tl_tx_cpl_credits;
    wire [12:0] tl_cfg_busdev;
    wire        pcie_linkup;
    wire [4:0]  pcie_ltssm;
    wire [7:0]  tl_rx_err;

    // Gowin wraps the controller as SerDes_Top with prefixed port names.
    SerDes_Top u_pcie (
        .PCIE_Controller_Top_pcie_rstn_i           (pcie_rstn),
        .PCIE_Controller_Top_pcie_tl_clk_i         (tlp_clk),
        .PCIE_Controller_Top_pcie_linkup_o         (pcie_linkup),
        .PCIE_Controller_Top_pcie_ltssm_o          (pcie_ltssm),
        .PCIE_Controller_Top_pcie_tl_cfg_busdev_o  (tl_cfg_busdev),
        .PCIE_Controller_Top_pcie_tl_rx_sop_o      (tl_rx_sop),
        .PCIE_Controller_Top_pcie_tl_rx_eop_o      (tl_rx_eop),
        .PCIE_Controller_Top_pcie_tl_rx_valid_o    (tl_rx_valid),
        .PCIE_Controller_Top_pcie_tl_rx_data_o     (tl_rx_data),
        .PCIE_Controller_Top_pcie_tl_rx_bardec_o   (tl_rx_bardec),
        .PCIE_Controller_Top_pcie_tl_rx_err_o      (tl_rx_err),
        .PCIE_Controller_Top_pcie_tl_rx_wait_i     (tl_rx_wait),
        .PCIE_Controller_Top_pcie_tl_rx_masknp_i   (1'b0),
        .PCIE_Controller_Top_pcie_tl_tx_sop_i      (tl_tx_sop),
        .PCIE_Controller_Top_pcie_tl_tx_eop_i      (tl_tx_eop),
        .PCIE_Controller_Top_pcie_tl_tx_valid_i    (tl_tx_valid),
        .PCIE_Controller_Top_pcie_tl_tx_data_i     (tl_tx_data),
        .PCIE_Controller_Top_pcie_tl_tx_wait_o     (tl_tx_wait),
        .PCIE_Controller_Top_pcie_tl_tx_creditsp_o (tl_tx_p_credits),
        .PCIE_Controller_Top_pcie_tl_tx_creditsnp_o(tl_tx_np_credits),
        .PCIE_Controller_Top_pcie_tl_tx_creditscpl_o(tl_tx_cpl_credits),
        .PCIE_Controller_Top_pcie_tl_int_status_i  (1'b0),
        .PCIE_Controller_Top_pcie_tl_int_req_i     (1'b0),
        .PCIE_Controller_Top_pcie_tl_int_msinum_i  (5'b0),
        .PCIE_Controller_Top_pcie_tl_int_ack_o     (),
        .PCIE_Controller_Top_pcie_tl_drp_clk_o     (),
        .PCIE_Controller_Top_pcie_tl_drp_rddata_o  (),
        .PCIE_Controller_Top_pcie_tl_drp_resp_o    (),
        .PCIE_Controller_Top_pcie_tl_drp_rd_valid_o(),
        .PCIE_Controller_Top_pcie_tl_drp_ready_o   (),
        .PCIE_Controller_Top_pcie_tl_drp_addr_i    (24'b0),
        .PCIE_Controller_Top_pcie_tl_drp_wrdata_i  (32'b0),
        .PCIE_Controller_Top_pcie_tl_drp_strb_i    (8'b0),
        .PCIE_Controller_Top_pcie_tl_drp_wr_i      (1'b0),
        .PCIE_Controller_Top_pcie_tl_drp_rd_i      (1'b0)
    );

    wire        ddr_clk_out;
    wire        ddr_rst;
    wire        init_calib_complete;
    wire        cmd_ready;
    wire [2:0]  cmd;
    wire        cmd_en;
    wire [28:0] addr;
    wire        wr_data_rdy;
    wire [255:0] wr_data;
    wire        wr_data_en;
    wire        wr_data_end;
    wire [31:0] wr_data_mask;
    wire [255:0] rd_data;
    wire        rd_data_valid;
    wire        rd_data_end;
    wire        sr_ack, ref_ack;

    wire ddr_odt_ip;
    assign O_ddr_odt = DDR_ODT_ENABLE ? ddr_odt_ip : 1'b0;

    // DDR3 controller: fpga/hw/gowin/own_ddr3 by default (Gowin IP's native
    // UI, no BSRAM, lower read latency; B128: GLQuake 27.6 ms/frame). Define
    // USE_GOWIN_DDR3_IP (wrapper file) for the Gowin IP; it also needs the IP's
    // CST / SDC names and files (see own_ddr3/INTEGRATION.md).
`ifndef USE_GOWIN_DDR3_IP
    wire [31:0] own_ddr3_dbg;
    own_ddr3 u_ddr3 (
        .dbg_addr            (8'h00),
        .dbg_wdata           (32'h0),
        .dbg_we              (1'b0),
        .dbg_rdata           (own_ddr3_dbg),
`else
    DDR3_Memory_Interface_Top u_ddr3 (
`endif
        .clk                 (clk50),
        .pll_stop            (ddr_pll_stop),
        .memory_clk          (clk400),
        .pll_lock            (pll_sys_lock),
        .rst_n               (~sys_rst),
        .clk_out             (ddr_clk_out),
        .ddr_rst             (ddr_rst),
        .init_calib_complete (init_calib_complete),
        .cmd_ready           (cmd_ready),
        .cmd                 (cmd),
        .cmd_en              (cmd_en),
        .addr                (addr),
        .wr_data_rdy         (wr_data_rdy),
        .wr_data             (wr_data),
        .wr_data_en          (wr_data_en),
        .wr_data_end         (wr_data_end),
        .wr_data_mask        (wr_data_mask),
        .rd_data             (rd_data),
        .rd_data_valid       (rd_data_valid),
        .rd_data_end         (rd_data_end),
        .sr_req              (1'b0),
        .ref_req             (1'b0),
        .sr_ack              (sr_ack),
        .ref_ack             (ref_ack),
        .burst               (1'b0),
        .O_ddr_addr          (O_ddr_addr),
        .O_ddr_ba            (O_ddr_ba),
        .O_ddr_cs_n          (O_ddr_cs_n),
        .O_ddr_ras_n         (O_ddr_ras_n),
        .O_ddr_cas_n         (O_ddr_cas_n),
        .O_ddr_we_n          (O_ddr_we_n),
        .O_ddr_clk           (O_ddr_clk),
        .O_ddr_clk_n         (O_ddr_clk_n),
        .O_ddr_cke           (O_ddr_cke),
        .O_ddr_odt           (ddr_odt_ip),
        .O_ddr_reset_n       (O_ddr_reset_n),
        .O_ddr_dqm           (O_ddr_dqm),
        .IO_ddr_dq           (IO_ddr_dq),
        .IO_ddr_dqs          (IO_ddr_dqs),
        .IO_ddr_dqs_n        (IO_ddr_dqs_n)
    );

    wire [7:0] hdmi_r, hdmi_g, hdmi_b;
    wire       hdmi_hs, hdmi_vs, hdmi_de;
    wire       led_link, led_calib, led_wedge, led_heartbeat;

    // Core held in reset until DDR3 is calibrated; synchronised into clk_core.
    reg [2:0] core_rst_sync = 3'b111;
    wire      core_rst_async = ~pll_sys_lock | ddr_rst | ~init_calib_complete;
    always @(posedge clk_core or posedge core_rst_async)
        if (core_rst_async) core_rst_sync <= 3'b111;
        else                core_rst_sync <= {core_rst_sync[1:0], 1'b0};

    // DDR-UI-domain reset, registered twice: driven straight from the PHY's
    // init_calib_complete it fanned out to ~150 synchronous resets and missed
    // 100 MHz (B62).
    reg [1:0] ddr_dom_rst = 2'b11;
    always @(posedge ddr_clk_out)
        ddr_dom_rst <= {ddr_dom_rst[0], ddr_rst | ~init_calib_complete};

    // Implicit domain = DDR3 UI clock (native-port adapter only); the Voodoo
    // core runs on clk_core and crosses into it through Axi4CC.
    TangMega138kPro u_voodoo (
        .clk                 (ddr_clk_out),
        .reset               (ddr_dom_rst[1]),
        .coreClk             (clk_core),
        .coreReset           (core_rst_sync[2]),
        .pcieClk             (clk100),
        .pcieReset           (tlp_rst),
        .pcieLinkup          (pcie_linkup),
        .tlRxSop             (tl_rx_sop),
        .tlRxEop             (tl_rx_eop),
        .tlRxValid           (tl_rx_valid),
        .tlRxData            (tl_rx_data),
        .tlRxBardec          (tl_rx_bardec),
        .tlRxWait            (tl_rx_wait),
        .tlTxSop             (tl_tx_sop),
        .tlTxEop             (tl_tx_eop),
        .tlTxValid           (tl_tx_valid),
        .tlTxData            (tl_tx_data),
        .tlTxWait            (tl_tx_wait),
        .tlTxCreditsCpl      (tl_tx_cpl_credits),
        .tlTxCreditsP        (tl_tx_p_credits),
        .tlCfgBusdev         (tl_cfg_busdev),
        .ddr_cmdReady        (cmd_ready),
        .ddr_cmd             (cmd),
        .ddr_cmdEn           (cmd_en),
        .ddr_addr            (addr),
        .ddr_burst           (),
        .ddr_wrDataRdy       (wr_data_rdy),
        .ddr_wrData          (wr_data),
        .ddr_wrDataEn        (wr_data_en),
        .ddr_wrDataEnd       (wr_data_end),
        .ddr_wrDataMask      (wr_data_mask),
        .ddr_rdData          (rd_data),
        .ddr_rdDataValid     (rd_data_valid),
        .ddr_rdDataEnd       (rd_data_end),
        .ddr_initCalibComplete(init_calib_complete),
        .hdmiClk             (clk_hdmi_pixel),
        .hdmiReset           (hdmi_rst),
        .hdmiR               (hdmi_r),
        .hdmiG               (hdmi_g),
        .hdmiB               (hdmi_b),
        .hdmiHs              (hdmi_hs),
        .hdmiVs              (hdmi_vs),
        .hdmiDe              (hdmi_de),
        .ledLink             (led_link),
        .ledCalib            (led_calib),
        .ledWedge            (led_wedge),
        .ledHeartbeat        (led_heartbeat)
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

    assign led_n[0] = led_heartbeat;
    assign led_n[1] = led_link;
    assign led_n[2] = led_calib;
    assign led_n[3] = led_wedge;

    // ------------------------------------------------------------------
    // JTAG diagnostics (fpga/hw/gowin/board_diag.py): link / clocks / TLPs.
    // ------------------------------------------------------------------
    reg [31:0] cnt_core = 0, cnt_ddr = 0, cnt_tlp = 0, cnt_pix = 0;
    reg [31:0] rx_tlps = 0, tx_tlps = 0, hdmi_frames = 0;
    reg        hdmi_vs_d = 1'b0;
    always @(posedge clk_core)       cnt_core <= cnt_core + 1'b1;
    always @(posedge ddr_clk_out)    cnt_ddr  <= cnt_ddr + 1'b1;
    always @(posedge clk_hdmi_pixel) begin
        cnt_pix <= cnt_pix + 1'b1;
        hdmi_vs_d <= hdmi_vs;
        if (hdmi_vs && !hdmi_vs_d) hdmi_frames <= hdmi_frames + 1'b1;
    end
    always @(posedge tlp_clk) begin
        cnt_tlp <= cnt_tlp + 1'b1;
        if (|tl_rx_valid && tl_rx_sop) rx_tlps <= rx_tlps + 1'b1;
        if (|tl_tx_valid && tl_tx_sop && !tl_tx_wait) tx_tlps <= tx_tlps + 1'b1;
    end

    wire [31:0] diag_status = {
        8'hD1,                    // [31:24] magic
        3'b0, pcie_ltssm,         // [23:16]
        tl_cfg_busdev[12:5],      // [15:8]  PCI bus number
        pcie_rstn,                // [7]
        pcie_linkup,              // [6]
        init_calib_complete,      // [5]
        ddr_rst,                  // [4]
        pll_sys_lock,             // [3]
        pll_hdmi_lock,            // [2]
        core_rst_sync[2],         // [1]
        ~led_wedge                // [0]  host bridge wedged
    };

    jtag_diag u_diag (
        .tck_pad_i (tck_pad_i),
        .tms_pad_i (tms_pad_i),
        .tdi_pad_i (tdi_pad_i),
        .tdo_pad_o (tdo_pad_o),
        .page0     (diag_status),
        .page1     (cnt_core),
        .page2     (cnt_ddr),
        .page3     (cnt_tlp),
        .page4     (cnt_pix),
        .page5     (rx_tlps),
        .page6     (tx_tlps),
        .page7     (hdmi_frames)
    );

    wire _unused = &{1'b0, tmds_hpd, tlp_rst, tl_tx_p_credits, tl_tx_np_credits,
                     pcie_ltssm, tl_rx_err, sr_ack, ref_ack};

endmodule
