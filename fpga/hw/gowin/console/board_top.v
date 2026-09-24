// Sipeed Tang Retro Console 138K — SpinalVoodoo wrapper.
// Instantiates Gowin IP (see ip.md) and TangConsole138k. After DDR3
// calibration a USB-pad menu offers RISC-V Quake (software or Glide).
// No PCIe. USB HID comes from the BL616 UART (TangCore command 0x09).
// Soft-USB Type-C (S1) is held disconnected in this routable build. Debug
// output remains available through the hardware-debugger UART.

module board_top (
    input  wire        clk50,
    input  wire        uart_rx,
    output wire        uart_tx,
    input  wire        key0_n,
    input  wire        key1_n,
    inout  wire        usb_soft_dp,
    inout  wire        usb_soft_dn,
    output wire        usb_pullup_en,
    output wire [1:0]  led_n,
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

    wire pll_sys_lock;
    wire clk100;
    wire clk_core;
    wire clk400;
    wire pll_hdmi_lock;
    wire clk_hdmi_serial;
    wire clk_hdmi_pixel;
    wire ddr_pll_stop;
    wire sys_rst = ~pll_sys_lock;
    wire hdmi_rst = ~pll_hdmi_lock;

    wire clk60;
    wire clk27;
    wire pll_lock_27;
    wire pll_ddr_lock;
    wire clk_mem;
    wire clk_mem_unused;

    // The 300 MHz output of this PLL never trains the SOM DDR3. Keep it for
    // the 60 MHz core clock only, and gate the unused memory output off.
    Gowin_PLL_Sys u_pll_sys (
        .lock    (pll_sys_lock),
        .clkout0 (clk100),
        .clkout1 (clk60),
        .clkout2 (clk400),
        .enclk2  (1'b0),
        .clkin   (clk50)
    );

    // 50 -> 27 -> 297 MHz. This is the clock the Tang Console 138K DDR3 trains
    // with; a standalone HDMI check turns green only with this PLL.
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
        .lock     (pll_ddr_lock),
        .reset    (~pll_lock_27)
    );

    // 30 MHz does not close (Fmax ~24 MHz, hundreds of failing endpoints), so
    // the renderer misbehaves and the demo stays black. 100/5 = 20 MHz is
    // under that Fmax. Scanout can underflow; a late picture is still a picture.
    CLKDIV #(
        .DIV_MODE("5")
    ) u_core_div (
        .HCLKIN (clk100),
        .RESETN (pll_sys_lock),
        .CALIB  (1'b0),
        .CLKOUT (clk_core)
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

    wire        ddr_clk_out;
    wire        ddr_rst;
    wire        init_calib_complete;
    wire        cmd_ready;
    wire [2:0]  cmd;
    wire        cmd_en;
    wire [27:0] addr;
    wire        wr_data_rdy;
    wire [127:0] wr_data;
    wire        wr_data_en;
    wire        wr_data_end;
    wire [15:0] wr_data_mask;
    wire [127:0] rd_data;
    wire        rd_data_valid;
    wire        rd_data_end;
    wire        sr_ack, ref_ack;
    // Hold the counter in reset until training finishes. A request latched
    // earlier never receives ref_ack and then blocks every read. A refresh
    // that overlaps a real command returns the wrong word, which shows up as
    // broken red lines. Leave refresh off unless MODE is held; tying the pin
    // itself to 0 failed placement.
    reg [9:0] ref_div;
    reg       ref_req_r;
    reg       key0_meta, key0_sync;
    always @(posedge ddr_clk_out) begin
        if (ddr_rst || !init_calib_complete) begin
            ref_div <= 10'd0;
            ref_req_r <= 1'b0;
            key0_meta <= 1'b1;
            key0_sync <= 1'b1;
        end else begin
            key0_meta <= key0_n;
            key0_sync <= key0_meta;
            if (ref_ack) begin
                ref_req_r <= 1'b0;
            end else if (!ref_req_r && !key0_sync && ref_div == 10'd580) begin
                ref_div <= 10'd0;
                ref_req_r <= 1'b1;
            end else if (!ref_req_r) begin
                ref_div <= ref_div + 1'b1;
            end
        end
    end
    wire ref_req = ref_req_r;

    DDR3_Memory_Interface_Top u_ddr3 (
        .clk                 (clk50),
        .pll_stop            (ddr_pll_stop),
        .memory_clk          (clk_mem),
        .pll_lock            (pll_ddr_lock),
        .rst_n               (1'b1),
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
        .ref_req             (ref_req),
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
        .O_ddr_odt           (O_ddr_odt),
        .O_ddr_reset_n       (O_ddr_reset_n),
        .O_ddr_dqm           (O_ddr_dqm),
        .IO_ddr_dq           (IO_ddr_dq),
        .IO_ddr_dqs          (IO_ddr_dqs),
        .IO_ddr_dqs_n        (IO_ddr_dqs_n)
    );

    wire [7:0] hdmi_r, hdmi_g, hdmi_b;
    wire       hdmi_hs, hdmi_vs, hdmi_de;
    wire       led_calib, led_done, led_wedge, led_heartbeat;
    wire       debug_mode;

    // The bit-banged USB CDC device was the largest avoidable routing load in
    // this already-congested image. Leave S1 electrically disconnected.
    assign usb_soft_dp = 1'bz;
    assign usb_soft_dn = 1'bz;
    assign usb_pullup_en = 1'b0;

    // In diagnostic mode channel B of the Sipeed USB debugger becomes a
    // plain 2 Mbaud, 8N2 ASCII console. This compact logger is independent
    // of DDR reset and the congested 75 MHz core clock.
    wire debug_uart_tx;
    wire debug_uart_busy;
    diagnostic_uart u_debug_uart (
        .clk           (clk50),
        .enable        (debug_mode),
        .calib         (init_calib_complete),
        .wedged        (~led_wedge),
        .mode_n        (key0_n),
        .reset_n       (key1_n),
        .uart_link     (1'b0),
        .uart_cmd      (8'd0),
        .uart_rx_count (16'd0),
        .uart_hid_count(16'd0),
        .tx            (debug_uart_tx),
        .busy          (debug_uart_busy)
    );
    assign uart_tx = debug_uart_tx;

    // Keep a free-running counter in the netlist (the console route closes
    // with it). Eight seconds after lock, press A once so the first menu
    // item starts on its own. Onboard keys still work before that.
    reg [31:0] menu_hold;
    always @(posedge clk50) begin
        if (sys_rst)
            menu_hold <= 32'd0;
        else if (menu_hold != 32'hffffffff)
            menu_hold <= menu_hold + 32'd1;
    end
    wire select_pulse = (menu_hold >= 32'd400_000_000) && (menu_hold < 32'd420_000_000);
    wire [15:0] hid_hold = select_pulse ? 16'h0100 : 16'h0000;

    TangConsole138k u_voodoo (
        .clk                 (clk_core),
        .reset               (sys_rst),
        .ddrClk              (ddr_clk_out),
        .ddrReset            (ddr_rst),
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
        .hid1                (hid_hold),
        .hid2                (16'd0),
        .keyUserN            (key0_n),
        .keyResetN           (key1_n),
        .uartLink            (1'b0),
        .uartLastCmd         (8'd0),
        .uartRxCount         (16'd0),
        .uartHidCount        (16'd0),
        .debugMode           (debug_mode),
        .hdmiClk             (clk_hdmi_pixel),
        .hdmiReset           (hdmi_rst),
        .hdmiR               (hdmi_r),
        .hdmiG               (hdmi_g),
        .hdmiB               (hdmi_b),
        .hdmiHs              (hdmi_hs),
        .hdmiVs              (hdmi_vs),
        .hdmiDe              (hdmi_de),
        .ctrlClk             (clk50),
        .ctrlReset           (sys_rst),
        .ledCalib            (led_calib),
        .ledDone             (led_done),
        .ledWedge            (led_wedge),
        .ledHeartbeat        (led_heartbeat),
        .usbOnline           (1'b0),
        .debugTx_valid       (),
        .debugTx_payload     ()
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

    reg [24:0] diagnostic_led_counter;
    always @(posedge clk50) begin
        if (sys_rst)
            diagnostic_led_counter <= 25'd0;
        else
            diagnostic_led_counter <= diagnostic_led_counter + 1'd1;
    end

    // Normal: heartbeat/menu status. Diagnostic: LEDs alternate after DDR
    // calibration, blink together while calibrating, and stay on if wedged.
    assign led_n[0] = debug_mode
        ? ((~led_wedge) ? 1'b0
                        : (init_calib_complete ? diagnostic_led_counter[23]
                                               : diagnostic_led_counter[20]))
        : led_heartbeat;
    assign led_n[1] = debug_mode
        ? ((~led_wedge) ? 1'b0
                        : (init_calib_complete ? ~diagnostic_led_counter[23]
                                               : diagnostic_led_counter[20]))
        : led_done;

    wire _unused = &{1'b0, clk400, clk27, clk_mem_unused, uart_rx, led_calib, sr_ack, ref_ack,
                     debug_uart_busy};

endmodule
