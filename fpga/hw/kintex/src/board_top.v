// Kintex-7 (XC7K160T / XC7K325T, FFG676) PCIe card top: vendor IP around
// rtl/kintex/Kintex7Voodoo.v.
//
//   pcie_7x_0       7-series Integrated Block for PCI Express, endpoint,
//                   Gen1 x4 (or Gen2 x2), 64-bit AXI-Stream at 125 MHz,
//                   BAR0 32-bit memory 1 MiB (same host map as the dock).
//   mig_7series_0   DDR3, 32-bit data (two x16 chips), 400 MHz memory
//                   clock, 4:1, native UI (256-bit), ui_clk 100 MHz,
//                   ORDERING = STRICT.
//   MMCM            core clock and HDMI pixel / serial clocks from the
//                   200 MHz board oscillator.
//
// The pcie_7x_0 port list matches the instantiation template Vivado 2025.2.1
// generates with build.tcl's settings (checked with pcie_ip_check.tcl). MIG
// ports follow UG586; check them against mig_7series_0.veo once mig.prj exists. Pin locations are in kintex.xdc and are
// board specific.

module board_top #(
    // MIG app_addr width: rank (1 bit even for one rank) + bank + row +
    // column. 2 x MT41K256M16 (512 MiB each, 32-bit bus): 1 + 3 + 15 + 10.
    parameter MIG_ADDR_WIDTH = 29,
    // Core clock = 1000 MHz VCO / CORE_DIV (10 = 100 MHz, 12.5 = 80 MHz).
    parameter real CORE_DIV = 10.0
) (
    input  wire        sys_clk_p,    // 200 MHz differential oscillator
    input  wire        sys_clk_n,
    input  wire        sys_rst_n,    // board reset button, active low

    // PCIe
    input  wire        pcie_clk_p,   // 100 MHz reference from the slot
    input  wire        pcie_clk_n,
    input  wire        pcie_perst_n,
    output wire [3:0]  pci_exp_txp,
    output wire [3:0]  pci_exp_txn,
    input  wire [3:0]  pci_exp_rxp,
    input  wire [3:0]  pci_exp_rxn,

    // DDR3 (MIG pins)
    inout  wire [31:0] ddr3_dq,
    inout  wire [3:0]  ddr3_dqs_p,
    inout  wire [3:0]  ddr3_dqs_n,
    output wire [14:0] ddr3_addr,
    output wire [2:0]  ddr3_ba,
    output wire        ddr3_ras_n,
    output wire        ddr3_cas_n,
    output wire        ddr3_we_n,
    output wire        ddr3_reset_n,
    output wire [0:0]  ddr3_ck_p,
    output wire [0:0]  ddr3_ck_n,
    output wire [0:0]  ddr3_cke,
    output wire [0:0]  ddr3_cs_n,
    output wire [3:0]  ddr3_dm,
    output wire [0:0]  ddr3_odt,

    // HDMI (direct TMDS from the FPGA)
    output wire        tmds_clk_p,
    output wire        tmds_clk_n,
    output wire [2:0]  tmds_d_p,
    output wire [2:0]  tmds_d_n,

    output wire [3:0]  led
);

    // ---------------------------------------------------------- clocks
    wire sys_clk_ibuf, sys_clk;
    IBUFDS sys_clk_buf (.I(sys_clk_p), .IB(sys_clk_n), .O(sys_clk_ibuf));
    BUFG sys_clk_bufg (.I(sys_clk_ibuf), .O(sys_clk));

    // VCO 1000 MHz: core, pixel 25 MHz (640x480; 25.175 nominal), serial 125 MHz.
    wire mmcm_fb, mmcm_locked;
    wire core_clk_u, pix_clk_u, ser_clk_u;
    wire core_clk, pix_clk, ser_clk;
    MMCME2_BASE #(
        .CLKIN1_PERIOD(5.0),
        .DIVCLK_DIVIDE(1),
        .CLKFBOUT_MULT_F(5.0),
        .CLKOUT0_DIVIDE_F(CORE_DIV),
        .CLKOUT1_DIVIDE(40),
        .CLKOUT2_DIVIDE(8)
    ) mmcm (
        .CLKIN1(sys_clk), .CLKFBIN(mmcm_fb), .CLKFBOUT(mmcm_fb), .CLKFBOUTB(),
        .CLKOUT0(core_clk_u), .CLKOUT0B(), .CLKOUT1(pix_clk_u), .CLKOUT1B(),
        .CLKOUT2(ser_clk_u), .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(),
        .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .LOCKED(mmcm_locked), .PWRDWN(1'b0), .RST(!sys_rst_n)
    );
    BUFG core_bufg (.I(core_clk_u), .O(core_clk));
    BUFG pix_bufg (.I(pix_clk_u), .O(pix_clk));
    BUFG ser_bufg (.I(ser_clk_u), .O(ser_clk));

    // Synchronous resets, released a few cycles after lock.
    reg [3:0] core_rst_sr = 4'hf;
    reg [3:0] pix_rst_sr = 4'hf;
    always @(posedge core_clk or negedge mmcm_locked)
        if (!mmcm_locked) core_rst_sr <= 4'hf; else core_rst_sr <= {core_rst_sr[2:0], 1'b0};
    always @(posedge pix_clk or negedge mmcm_locked)
        if (!mmcm_locked) pix_rst_sr <= 4'hf; else pix_rst_sr <= {pix_rst_sr[2:0], 1'b0};
    wire core_rst = core_rst_sr[3];
    wire pix_rst = pix_rst_sr[3];

    // ---------------------------------------------------------- PCIe
    wire pcie_refclk;
    IBUFDS_GTE2 pcie_refclk_buf (.I(pcie_clk_p), .IB(pcie_clk_n), .O(pcie_refclk), .ODIV2(), .CEB(1'b0));
    wire pcie_perst_n_c;
    IBUF perst_buf (.I(pcie_perst_n), .O(pcie_perst_n_c));

    wire        user_clk, user_reset, user_lnk_up;
    wire [63:0] rx_tdata, tx_tdata;
    wire [7:0]  rx_tkeep, tx_tkeep;
    wire        rx_tlast, rx_tvalid, rx_tready, tx_tlast, tx_tvalid, tx_tready;
    wire [21:0] rx_tuser;
    wire [7:0]  cfg_bus_number;
    wire [4:0]  cfg_device_number;
    wire [2:0]  cfg_function_number;

    pcie_7x_0 pcie (
        .pci_exp_txp(pci_exp_txp), .pci_exp_txn(pci_exp_txn),
        .pci_exp_rxp(pci_exp_rxp), .pci_exp_rxn(pci_exp_rxn),

        .user_clk_out(user_clk), .user_reset_out(user_reset), .user_lnk_up(user_lnk_up),
        .user_app_rdy(),

        .tx_buf_av(), .tx_cfg_req(), .tx_err_drop(),
        .s_axis_tx_tready(tx_tready), .s_axis_tx_tdata(tx_tdata), .s_axis_tx_tkeep(tx_tkeep),
        .s_axis_tx_tlast(tx_tlast), .s_axis_tx_tvalid(tx_tvalid), .s_axis_tx_tuser(4'b0),
        .tx_cfg_gnt(1'b1),

        .m_axis_rx_tdata(rx_tdata), .m_axis_rx_tkeep(rx_tkeep), .m_axis_rx_tlast(rx_tlast),
        .m_axis_rx_tvalid(rx_tvalid), .m_axis_rx_tready(rx_tready), .m_axis_rx_tuser(rx_tuser),
        .rx_np_ok(1'b1), .rx_np_req(1'b1),

        .fc_cpld(), .fc_cplh(), .fc_npd(), .fc_nph(), .fc_pd(), .fc_ph(), .fc_sel(3'b0),

        .cfg_status(), .cfg_command(), .cfg_dstatus(), .cfg_dcommand(), .cfg_lstatus(),
        .cfg_lcommand(), .cfg_dcommand2(), .cfg_pcie_link_state(),
        .cfg_pmcsr_pme_en(), .cfg_pmcsr_powerstate(), .cfg_pmcsr_pme_status(),
        .cfg_received_func_lvl_rst(),
        .cfg_bus_number(cfg_bus_number), .cfg_device_number(cfg_device_number),
        .cfg_function_number(cfg_function_number),

        // Management / error / interrupt / power interfaces unused: tie-offs
        // as in the PG054 PIO example (pcie_app_7x).
        .cfg_mgmt_do(), .cfg_mgmt_rd_wr_done(), .cfg_mgmt_di(32'b0), .cfg_mgmt_byte_en(4'b0),
        .cfg_mgmt_dwaddr(10'b0), .cfg_mgmt_wr_en(1'b0), .cfg_mgmt_rd_en(1'b0),
        .cfg_mgmt_wr_readonly(1'b0), .cfg_mgmt_wr_rw1c_as_rw(1'b0),
        .cfg_err_ecrc(1'b0), .cfg_err_ur(1'b0), .cfg_err_cpl_timeout(1'b0),
        .cfg_err_cpl_unexpect(1'b0), .cfg_err_cpl_abort(1'b0), .cfg_err_posted(1'b0),
        .cfg_err_cor(1'b0), .cfg_err_atomic_egress_blocked(1'b0), .cfg_err_internal_cor(1'b0),
        .cfg_err_malformed(1'b0), .cfg_err_mc_blocked(1'b0), .cfg_err_poisoned(1'b0),
        .cfg_err_norecovery(1'b0), .cfg_err_tlp_cpl_header(48'b0), .cfg_err_cpl_rdy(),
        .cfg_err_locked(1'b0), .cfg_err_acs(1'b0), .cfg_err_internal_uncor(1'b0),
        .cfg_trn_pending(1'b0), .cfg_pm_halt_aspm_l0s(1'b0), .cfg_pm_halt_aspm_l1(1'b0),
        .cfg_pm_force_state_en(1'b0), .cfg_pm_force_state(2'b0), .cfg_dsn(64'b0),
        .cfg_interrupt(1'b0), .cfg_interrupt_rdy(), .cfg_interrupt_assert(1'b0),
        .cfg_interrupt_di(8'b0), .cfg_interrupt_do(), .cfg_interrupt_mmenable(),
        .cfg_interrupt_msienable(), .cfg_interrupt_msixenable(), .cfg_interrupt_msixfm(),
        .cfg_interrupt_stat(1'b0), .cfg_pciecap_interrupt_msgnum(5'b0),
        .cfg_to_turnoff(), .cfg_turnoff_ok(1'b0), .cfg_pm_wake(1'b0),
        .cfg_pm_send_pme_to(1'b0), .cfg_ds_bus_number(8'b0), .cfg_ds_device_number(5'b0),
        .cfg_ds_function_number(3'b0),
        .cfg_bridge_serr_en(), .cfg_slot_control_electromech_il_ctl_pulse(),
        .cfg_root_control_syserr_corr_err_en(), .cfg_root_control_syserr_non_fatal_err_en(),
        .cfg_root_control_syserr_fatal_err_en(), .cfg_root_control_pme_int_en(),
        .cfg_aer_rooterr_corr_err_reporting_en(), .cfg_aer_rooterr_non_fatal_err_reporting_en(),
        .cfg_aer_rooterr_fatal_err_reporting_en(), .cfg_aer_rooterr_corr_err_received(),
        .cfg_aer_rooterr_non_fatal_err_received(), .cfg_aer_rooterr_fatal_err_received(),
        .cfg_aer_ecrc_check_en(), .cfg_aer_ecrc_gen_en(),
        .cfg_aer_interrupt_msgnum(5'b0),
        .cfg_err_aer_headerlog(128'b0), .cfg_err_aer_headerlog_set(),
        .pcie_drp_clk(1'b1), .pcie_drp_en(1'b0), .pcie_drp_we(1'b0), .pcie_drp_addr(9'b0),
        .pcie_drp_di(16'b0), .pcie_drp_do(), .pcie_drp_rdy(),
        .cfg_vc_tcvc_map(), .cfg_msg_received(), .cfg_msg_data(),
        .cfg_msg_received_err_cor(), .cfg_msg_received_err_non_fatal(), .cfg_msg_received_err_fatal(),
        .cfg_msg_received_pm_as_nak(), .cfg_msg_received_pme_to_ack(),
        .cfg_msg_received_assert_int_a(), .cfg_msg_received_assert_int_b(),
        .cfg_msg_received_assert_int_c(), .cfg_msg_received_assert_int_d(),
        .cfg_msg_received_deassert_int_a(), .cfg_msg_received_deassert_int_b(),
        .cfg_msg_received_deassert_int_c(), .cfg_msg_received_deassert_int_d(),
        .cfg_msg_received_pm_pme(), .cfg_msg_received_setslotpowerlimit(),

        .pl_directed_link_change(2'b0), .pl_directed_link_width(2'b0),
        .pl_directed_link_speed(1'b0), .pl_directed_link_auton(1'b0),
        .pl_upstream_prefer_deemph(1'b1), .pl_sel_lnk_rate(), .pl_sel_lnk_width(),
        .pl_ltssm_state(), .pl_lane_reversal_mode(), .pl_phy_lnk_up(), .pl_tx_pm_state(),
        .pl_rx_pm_state(), .pl_link_upcfg_cap(), .pl_link_gen2_cap(),
        .pl_link_partner_gen2_supported(), .pl_initial_link_width(),
        .pl_directed_change_done(), .pl_received_hot_rst(), .pl_transmit_hot_rst(1'b0),
        .pl_downstream_deemph_source(1'b0),

        .sys_clk(pcie_refclk), .sys_rst_n(pcie_perst_n_c)
    );

    // ---------------------------------------------------------- DDR3
    wire         ui_clk, ui_rst, init_calib_complete;
    wire         app_rdy, app_wdf_rdy, app_rd_data_valid, app_rd_data_end;
    wire [255:0] app_rd_data;
    wire [2:0]   ddr_cmd;
    wire         ddr_cmd_en, ddr_wr_en, ddr_wr_end, ddr_burst;
    wire [28:0]  ddr_addr;
    wire [255:0] ddr_wr_data;
    wire [31:0]  ddr_wr_mask;

    mig_7series_0 mig (
        .ddr3_dq(ddr3_dq), .ddr3_dqs_p(ddr3_dqs_p), .ddr3_dqs_n(ddr3_dqs_n),
        .ddr3_addr(ddr3_addr), .ddr3_ba(ddr3_ba), .ddr3_ras_n(ddr3_ras_n),
        .ddr3_cas_n(ddr3_cas_n), .ddr3_we_n(ddr3_we_n), .ddr3_reset_n(ddr3_reset_n),
        .ddr3_ck_p(ddr3_ck_p), .ddr3_ck_n(ddr3_ck_n), .ddr3_cke(ddr3_cke),
        .ddr3_cs_n(ddr3_cs_n), .ddr3_dm(ddr3_dm), .ddr3_odt(ddr3_odt),

        // System clock "No Buffer" and reference clock "Use System Clock"
        // in the MIG GUI: the 200 MHz BUFG output feeds both.
        .sys_clk_i(sys_clk),
        .sys_rst(sys_rst_n),

        // Gowin native address counts 32-byte words; MIG counts DQ (32-bit) words.
        .app_addr({ddr_addr[MIG_ADDR_WIDTH-4:0], 3'b000}),
        .app_cmd(ddr_cmd), .app_en(ddr_cmd_en), .app_rdy(app_rdy),
        .app_wdf_data(ddr_wr_data), .app_wdf_end(ddr_wr_end), .app_wdf_mask(ddr_wr_mask),
        .app_wdf_wren(ddr_wr_en), .app_wdf_rdy(app_wdf_rdy),
        .app_rd_data(app_rd_data), .app_rd_data_end(app_rd_data_end),
        .app_rd_data_valid(app_rd_data_valid),
        .app_sr_req(1'b0), .app_ref_req(1'b0), .app_zq_req(1'b0),
        .app_sr_active(), .app_ref_ack(), .app_zq_ack(),
        .ui_clk(ui_clk), .ui_clk_sync_rst(ui_rst),
        .init_calib_complete(init_calib_complete),
        .device_temp()
    );

    // ---------------------------------------------------------- Voodoo
    wire [7:0] hdmi_r, hdmi_g, hdmi_b;
    wire       hdmi_hs, hdmi_vs, hdmi_de;
    wire       led_link, led_calib, led_wedge, led_heartbeat;

    Kintex7Voodoo voodoo (
        .clk(ui_clk), .reset(ui_rst),
        .coreClk(core_clk), .coreReset(core_rst),

        .pcieClk(user_clk), .pcieReset(user_reset), .pcieLinkup(user_lnk_up),
        .rxData(rx_tdata), .rxKeep(rx_tkeep), .rxLast(rx_tlast), .rxValid(rx_tvalid),
        .rxReady(rx_tready), .rxBarHit(rx_tuser[8:2]),
        .txData(tx_tdata), .txKeep(tx_tkeep), .txLast(tx_tlast), .txValid(tx_tvalid),
        .txReady(tx_tready),
        .cfgBusNumber(cfg_bus_number), .cfgDeviceNumber(cfg_device_number),

        .ddr_cmdReady(app_rdy), .ddr_cmd(ddr_cmd), .ddr_cmdEn(ddr_cmd_en), .ddr_addr(ddr_addr),
        .ddr_burst(ddr_burst), .ddr_wrDataRdy(app_wdf_rdy), .ddr_wrData(ddr_wr_data),
        .ddr_wrDataEn(ddr_wr_en), .ddr_wrDataEnd(ddr_wr_end), .ddr_wrDataMask(ddr_wr_mask),
        .ddr_rdData(app_rd_data), .ddr_rdDataValid(app_rd_data_valid),
        .ddr_rdDataEnd(app_rd_data_end), .ddr_initCalibComplete(init_calib_complete),

        .hdmiClk(pix_clk), .hdmiReset(pix_rst),
        .hdmiR(hdmi_r), .hdmiG(hdmi_g), .hdmiB(hdmi_b),
        .hdmiHs(hdmi_hs), .hdmiVs(hdmi_vs), .hdmiDe(hdmi_de),

        .ledLink(led_link), .ledCalib(led_calib), .ledWedge(led_wedge),
        .ledHeartbeat(led_heartbeat)
    );

    dvi_tx dvi (
        .pix_clk(pix_clk), .ser_clk(ser_clk), .rst(pix_rst),
        .r(hdmi_r), .g(hdmi_g), .b(hdmi_b), .hs(hdmi_hs), .vs(hdmi_vs), .de(hdmi_de),
        .tmds_clk_p(tmds_clk_p), .tmds_clk_n(tmds_clk_n), .tmds_d_p(tmds_d_p), .tmds_d_n(tmds_d_n)
    );

    // Active-low like the dock: heartbeat, PCIe link, DDR calibration, host bridge wedged.
    assign led = {led_wedge, led_calib, led_link, led_heartbeat};
endmodule
