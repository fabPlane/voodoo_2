// SpinalVoodoo core (Spinal TangMega138kPro) for the open flow, fabric fit only.
//
// The vendor wrapper (fpga/hw/gowin/src/board_top.v) puts PLLs, the PCIe
// Gen2 hard controller, the encrypted DDR3 IP and DVI TX around the Spinal
// top. None of those exist on apicula/nextpnr for the GW5AST yet, so here:
//
//   - one clock (clk50) drives every Spinal domain (core, PCIe, HDMI, DDR UI);
//   - the PCIe TLP RX bus is fed from a 272-bit shift register clocked from
//     the pcie_rstn pin, so yosys cannot constant-fold the BAR/AXI-Lite path;
//   - the DDR3 native UI is answered by a 256-bit x 256-entry BSRAM window
//     (always ready, one read beat per read command, byte-masked writes);
//   - TLP TX, HDMI pixels and the status LEDs are XOR-reduced onto the four
//     LED pins so the scanout and TX logic stays alive.
//
// None of this is meant to run on the board; it is for yosys/nextpnr fit.
module voodoo_core_open (
    input  wire       clk50,
    input  wire       pcie_rstn,
    input  wire       tmds_hpd,
    output wire [3:0] led_n
);
    // Power-on reset, held for 256 cycles.
    reg [7:0] por_cnt = 8'd0;
    reg       rst = 1'b1;
    always @(posedge clk50) begin
        if (por_cnt != 8'hff)
            por_cnt <= por_cnt + 8'd1;
        rst <= (por_cnt != 8'hff);
    end

    // Stimulus: two synchronised pins into a shift register.
    reg [1:0]   sin_sync = 2'b00;
    reg [1:0]   hpd_sync = 2'b00;
    reg [271:0] stim = 272'd0;
    always @(posedge clk50) begin
        sin_sync <= {sin_sync[0], pcie_rstn};
        hpd_sync <= {hpd_sync[0], tmds_hpd};
        stim     <= {stim[270:0], sin_sync[1]};
    end

    // Spinal top ports.
    wire         tl_rx_wait;
    wire         tl_tx_sop, tl_tx_eop;
    wire [7:0]   tl_tx_valid;
    wire [255:0] tl_tx_data;

    wire [2:0]   ddr_cmd;
    wire         ddr_cmd_en;
    wire [28:0]  ddr_addr;
    wire         ddr_burst;
    wire [255:0] ddr_wr_data;
    wire         ddr_wr_data_en, ddr_wr_data_end;
    wire [31:0]  ddr_wr_data_mask;
    reg  [255:0] ddr_rd_data;
    reg          ddr_rd_valid = 1'b0;
    reg          ddr_calib = 1'b0;

    wire [7:0]   hdmi_r, hdmi_g, hdmi_b;
    wire         hdmi_hs, hdmi_vs, hdmi_de;
    wire         led_link, led_calib, led_wedge, led_heartbeat;

    TangMega138kPro u_core (
        .coreClk               (clk50),
        .coreReset             (rst),
        .pcieClk               (clk50),
        .pcieReset             (rst),
        .pcieLinkup            (hpd_sync[1]),
        .tlRxSop               (stim[0]),
        .tlRxEop               (stim[1]),
        .tlRxValid             (stim[9:2]),
        .tlRxData              (stim[271:16]),
        .tlRxBardec            (stim[15:10]),
        .tlRxWait              (tl_rx_wait),
        .tlTxSop               (tl_tx_sop),
        .tlTxEop               (tl_tx_eop),
        .tlTxValid             (tl_tx_valid),
        .tlTxData              (tl_tx_data),
        .tlTxWait              (hpd_sync[1] & stim[100]),
        .tlTxCreditsCpl        (stim[47:16]),
        .tlTxCreditsP          (stim[79:48]),
        .tlCfgBusdev           (stim[92:80]),
        .ddr_cmdReady          (1'b1),
        .ddr_cmd               (ddr_cmd),
        .ddr_cmdEn             (ddr_cmd_en),
        .ddr_addr              (ddr_addr),
        .ddr_burst             (ddr_burst),
        .ddr_wrDataRdy         (1'b1),
        .ddr_wrData            (ddr_wr_data),
        .ddr_wrDataEn          (ddr_wr_data_en),
        .ddr_wrDataEnd         (ddr_wr_data_end),
        .ddr_wrDataMask        (ddr_wr_data_mask),
        .ddr_rdData            (ddr_rd_data),
        .ddr_rdDataValid       (ddr_rd_valid),
        .ddr_rdDataEnd         (ddr_rd_valid),
        .ddr_initCalibComplete (ddr_calib),
        .hdmiClk               (clk50),
        .hdmiReset             (rst),
        .hdmiR                 (hdmi_r),
        .hdmiG                 (hdmi_g),
        .hdmiB                 (hdmi_b),
        .hdmiHs                (hdmi_hs),
        .hdmiVs                (hdmi_vs),
        .hdmiDe                (hdmi_de),
        .ledLink               (led_link),
        .ledCalib              (led_calib),
        .ledWedge              (led_wedge),
        .ledHeartbeat          (led_heartbeat),
        .clk                   (clk50),
        .reset                 (rst)
    );

    // DDR3 native UI stub: 256 x 256-bit window, folded address.
    reg  [255:0] ddr_mem [0:255];
    wire [7:0]   ddr_idx = ddr_addr[10:3] ^ ddr_addr[18:11] ^ ddr_addr[26:19];
    wire         ddr_wr = ddr_cmd_en & (ddr_cmd == 3'd0) & ddr_wr_data_en;
    wire         ddr_rd = ddr_cmd_en & (ddr_cmd == 3'd1);
    integer i;
    always @(posedge clk50) begin
        for (i = 0; i < 32; i = i + 1)
            if (ddr_wr && !ddr_wr_data_mask[i])
                ddr_mem[ddr_idx][i*8 +: 8] <= ddr_wr_data[i*8 +: 8];
        ddr_rd_data <= ddr_mem[ddr_idx];
    end
    always @(posedge clk50) begin
        ddr_rd_valid <= ddr_rd & ~rst;
        ddr_calib    <= ~rst;
    end

    // Keep TX, HDMI and status outputs observable.
    reg tx_x = 1'b0, hdmi_x = 1'b0, misc_x = 1'b0;
    always @(posedge clk50) begin
        tx_x   <= ^{tl_rx_wait, tl_tx_sop, tl_tx_eop, tl_tx_valid, tl_tx_data};
        hdmi_x <= ^{hdmi_r, hdmi_g, hdmi_b, hdmi_hs, hdmi_vs, hdmi_de};
        misc_x <= led_link ^ led_calib ^ led_wedge ^ ddr_burst ^ ddr_wr_data_end;
    end
    assign led_n = {misc_x, hdmi_x, tx_x, led_heartbeat};
endmodule
