// Sipeed Tang Retro Console 138K — SpinalVoodoo wrapper.
// Instantiates Gowin IP (see ip.md) and TangConsole138k. After DDR3
// calibration a USB-pad menu offers RISC-V Quake (software or Glide).
// No PCIe. USB HID comes from the BL616 UART (TangCore command 0x09).
// Soft-USB Type-C (S1) is held disconnected in this routable build. Debug
// output remains available through the hardware-debugger UART.

module board_top (
    input  wire        tck_pad_i,
    input  wire        tms_pad_i,
    input  wire        tdi_pad_i,
    output wire        tdo_pad_o,
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
    output wire [3:0]  O_ddr_dqm,
    inout  wire [31:0] IO_ddr_dq,
    inout  wire [3:0]  IO_ddr_dqs,
    inout  wire [3:0]  IO_ddr_dqs_n
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

    Gowin_PLL_Sys u_pll_sys (
        .lock    (pll_sys_lock),
        .clkout0 (clk100),
        .clkout1 (clk60),
        .clkout2 (clk400),
        .enclk2  (ddr_pll_stop),
        .clkin   (clk50)
    );

    // Do not alter the PLL shared with DDR3. Divide its independent
    // 60 MHz output to a 30 MHz renderer/read clock. 20 MHz cannot sustain
    // the framebuffer scanout stream after DDR/AXI command overhead and
    // deterministically underflows a 25 MHz HDMI raster.
    CLKDIV #(
        .DIV_MODE("2")
    ) u_core_div (
        .HCLKIN (clk60),
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
    wire        ref_req;

    // Sipeed's revision-C controller performs refresh internally.
    assign ref_req = 1'b0;

    DDR3_Memory_Interface_Top u_ddr3 (
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
    wire [31:0] jtag_debug [0:15];
    wire [31:0] jtag_framebuffer [0:15];
    wire [31:0] jtag_writes [0:15];

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
        .hid1                (16'd0),
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
        .jtagDebug_0         (jtag_debug[0]),
        .jtagDebug_1         (jtag_debug[1]),
        .jtagDebug_2         (jtag_debug[2]),
        .jtagDebug_3         (jtag_debug[3]),
        .jtagDebug_4         (jtag_debug[4]),
        .jtagDebug_5         (jtag_debug[5]),
        .jtagDebug_6         (jtag_debug[6]),
        .jtagDebug_7         (jtag_debug[7]),
        .jtagDebug_8         (jtag_debug[8]),
        .jtagDebug_9         (jtag_debug[9]),
        .jtagDebug_10        (jtag_debug[10]),
        .jtagDebug_11        (jtag_debug[11]),
        .jtagDebug_12        (jtag_debug[12]),
        .jtagDebug_13        (jtag_debug[13]),
        .jtagDebug_14        (jtag_debug[14]),
        .jtagDebug_15        (jtag_debug[15]),
        .jtagFramebuffer_0   (jtag_framebuffer[0]),
        .jtagFramebuffer_1   (jtag_framebuffer[1]),
        .jtagFramebuffer_2   (jtag_framebuffer[2]),
        .jtagFramebuffer_3   (jtag_framebuffer[3]),
        .jtagFramebuffer_4   (jtag_framebuffer[4]),
        .jtagFramebuffer_5   (jtag_framebuffer[5]),
        .jtagFramebuffer_6   (jtag_framebuffer[6]),
        .jtagFramebuffer_7   (jtag_framebuffer[7]),
        .jtagFramebuffer_8   (jtag_framebuffer[8]),
        .jtagFramebuffer_9   (jtag_framebuffer[9]),
        .jtagFramebuffer_10  (jtag_framebuffer[10]),
        .jtagFramebuffer_11  (jtag_framebuffer[11]),
        .jtagFramebuffer_12  (jtag_framebuffer[12]),
        .jtagFramebuffer_13  (jtag_framebuffer[13]),
        .jtagFramebuffer_14  (jtag_framebuffer[14]),
        .jtagFramebuffer_15  (jtag_framebuffer[15]),
        .jtagWrites_0        (jtag_writes[0]),
        .jtagWrites_1        (jtag_writes[1]),
        .jtagWrites_2        (jtag_writes[2]),
        .jtagWrites_3        (jtag_writes[3]),
        .jtagWrites_4        (jtag_writes[4]),
        .jtagWrites_5        (jtag_writes[5]),
        .jtagWrites_6        (jtag_writes[6]),
        .jtagWrites_7        (jtag_writes[7]),
        .jtagWrites_8        (jtag_writes[8]),
        .jtagWrites_9        (jtag_writes[9]),
        .jtagWrites_10       (jtag_writes[10]),
        .jtagWrites_11       (jtag_writes[11]),
        .jtagWrites_12       (jtag_writes[12]),
        .jtagWrites_13       (jtag_writes[13]),
        .jtagWrites_14       (jtag_writes[14]),
        .jtagWrites_15       (jtag_writes[15]),
        .usbOnline           (1'b0),
        .debugTx_valid       (),
        .debugTx_payload     ()
    );

    jtag_debug_pages u_jtag_debug (
        .tck_pad_i(tck_pad_i), .tms_pad_i(tms_pad_i),
        .tdi_pad_i(tdi_pad_i), .tdo_pad_o(tdo_pad_o),
        .page0(jtag_debug[0]), .page1(jtag_debug[1]),
        .page2(jtag_debug[2]), .page3(jtag_debug[3]),
        .page4(jtag_debug[4]), .page5(jtag_debug[5]),
        .page6(jtag_debug[6]), .page7(jtag_debug[7]),
        .page8(jtag_debug[8]), .page9(jtag_debug[9]),
        .page10(jtag_debug[10]), .page11(jtag_debug[11]),
        .page12(jtag_debug[12]), .page13(jtag_debug[13]),
        .page14(jtag_debug[14]), .page15(jtag_debug[15]),
        .fb0(jtag_framebuffer[0]), .fb1(jtag_framebuffer[1]),
        .fb2(jtag_framebuffer[2]), .fb3(jtag_framebuffer[3]),
        .fb4(jtag_framebuffer[4]), .fb5(jtag_framebuffer[5]),
        .fb6(jtag_framebuffer[6]), .fb7(jtag_framebuffer[7]),
        .fb8(jtag_framebuffer[8]), .fb9(jtag_framebuffer[9]),
        .fb10(jtag_framebuffer[10]), .fb11(jtag_framebuffer[11]),
        .fb12(jtag_framebuffer[12]), .fb13(jtag_framebuffer[13]),
        .fb14(jtag_framebuffer[14]), .fb15(jtag_framebuffer[15]),
        .wr0(jtag_writes[0]), .wr1(jtag_writes[1]),
        .wr2(jtag_writes[2]), .wr3(jtag_writes[3]),
        .wr4(jtag_writes[4]), .wr5(jtag_writes[5]),
        .wr6(jtag_writes[6]), .wr7(jtag_writes[7]),
        .wr8(jtag_writes[8]), .wr9(jtag_writes[9]),
        .wr10(jtag_writes[10]), .wr11(jtag_writes[11]),
        .wr12(jtag_writes[12]), .wr13(jtag_writes[13]),
        .wr14(jtag_writes[14]), .wr15(jtag_writes[15])
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

    wire _unused = &{1'b0, clk100, uart_rx, led_calib, sr_ack, ref_ack,
                     debug_uart_busy};

endmodule
