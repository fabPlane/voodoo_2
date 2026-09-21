// USB 2.0 CDC ACM gadget for the Tang Retro Console 138K debug log.
// Console S1 Type-C D+/D- reaches the full-speed GPIO path through
// USB_TERM_RXDP/RXDN (J16/AB16). USB_DX (J19/H19) is the analog-HS path.

module usb_cdc_debug (
    input  wire       clk60,
    input  wire       reset,
    inout  wire       usb_dp,
    inout  wire       usb_dn,
    input  wire       tx_clk,
    input  wire       tx_valid,
    input  wire [7:0] tx_data,
    output wire       online
);
    localparam ENDPT_UART_CONFIG = 4'd0;

    wire [1:0]  PHY_XCVRSELECT;
    wire        PHY_TERMSELECT;
    wire [1:0]  PHY_OPMODE;
    wire [1:0]  PHY_LINESTATE;
    wire        PHY_TXVALID;
    wire        PHY_TXREADY;
    wire        PHY_RXVALID;
    wire        PHY_RXACTIVE;
    wire        PHY_RXERROR;
    wire [7:0]  PHY_DATAIN;
    wire [7:0]  PHY_DATAOUT;
    wire        PHY_RESET;

    wire [15:0] DESCROM_RADDR;
    wire [7:0]  DESCROM_RDAT;
    wire [15:0] DESC_DEV_ADDR, DESC_DEV_LEN;
    wire [15:0] DESC_QUAL_ADDR, DESC_QUAL_LEN;
    wire [15:0] DESC_FSCFG_ADDR, DESC_FSCFG_LEN;
    wire [15:0] DESC_HSCFG_ADDR, DESC_HSCFG_LEN;
    wire [15:0] DESC_OSCFG_ADDR;
    wire [15:0] DESC_STRLANG_ADDR;
    wire [15:0] DESC_STRVENDOR_ADDR, DESC_STRVENDOR_LEN;
    wire [15:0] DESC_STRPRODUCT_ADDR, DESC_STRPRODUCT_LEN;
    wire [15:0] DESC_STRSERIAL_ADDR, DESC_STRSERIAL_LEN;
    wire        DESCROM_HAVE_STRINGS;

    wire [7:0]  usb_txdat;
    wire [11:0] usb_txdat_len;
    wire        usb_txcork;
    wire        usb_txpop;
    wire        usb_txact;
    wire        usb_txpktfin;
    wire [7:0]  usb_rxdat;
    wire        usb_rxval;
    wire        usb_rxpktval;
    wire        usb_rxact;
    wire        usb_rxrdy;
    wire [3:0]  endpt_sel;
    wire        setup_active;
    wire        endpt0_send;
    wire [7:0]  endpt0_dat;
    wire [11:0] uart_config_txdat_len;
    wire        ep_usb_rxrdy;
    wire        ep_usb_txcork;
    wire [11:0] ep_usb_txlen;
    wire [7:0]  ep_usb_txdat;
    wire        usb_busreset;
    wire        usb_highspeed;
    wire        usb_suspend;
    wire [7:0]  inf_alter_i;
    wire [7:0]  inf_alter_o;
    wire [7:0]  inf_sel_o;
    wire        inf_set_o;
    reg  [7:0]  interface0_alter;
    reg  [7:0]  interface1_alter;
    wire [7:0]  ep2_rx_data;
    wire        ep2_rx_dval;

    assign usb_rxrdy     = ep_usb_rxrdy;
    assign usb_txcork    = (endpt_sel == ENDPT_UART_CONFIG) ? 1'b0 : ep_usb_txcork;
    assign usb_txdat     = (endpt_sel == ENDPT_UART_CONFIG) ? endpt0_dat : ep_usb_txdat;
    assign usb_txdat_len = (endpt_sel == ENDPT_UART_CONFIG) ? uart_config_txdat_len : ep_usb_txlen;

    usb_fifo u_usb_fifo (
        .i_clk          (clk60),
        .i_reset        (usb_busreset),
        .i_usb_endpt    (endpt_sel),
        .i_usb_rxact    (usb_rxact),
        .i_usb_rxval    (usb_rxval),
        .i_usb_rxpktval (usb_rxpktval),
        .i_usb_rxdat    (usb_rxdat),
        .o_usb_rxrdy    (ep_usb_rxrdy),
        .i_usb_txact    (usb_txact),
        .i_usb_txpop    (usb_txpop),
        .i_usb_txpktfin (usb_txpktfin),
        .o_usb_txcork   (ep_usb_txcork),
        .o_usb_txlen    (ep_usb_txlen),
        .o_usb_txdat    (ep_usb_txdat),
        .i_ep2_tx_clk   (tx_clk),
        .i_ep2_tx_max   (12'd64),
        .i_ep2_tx_dval  (tx_valid),
        .i_ep2_tx_data  (tx_data),
        .i_ep2_rx_clk   (clk60),
        .i_ep2_rx_rdy   (1'b1),
        .o_ep2_rx_dval  (ep2_rx_dval),
        .o_ep2_rx_data  (ep2_rx_data)
    );

    assign inf_alter_i = (inf_sel_o == 8'd0) ? interface0_alter :
                         (inf_sel_o == 8'd1) ? interface1_alter : 8'd0;
    always @(posedge clk60 or posedge reset) begin
        if (reset) begin
            interface0_alter <= 8'd0;
            interface1_alter <= 8'd0;
        end else if (inf_set_o) begin
            if (inf_sel_o == 8'd0) interface0_alter <= inf_alter_o;
            else if (inf_sel_o == 8'd1) interface1_alter <= inf_alter_o;
        end
    end

    usb_desc #(
        .SERIALSTR      ("Voodoo2"),
        .SERIALSTR_LEN  (7),
        .VENDORSTR      ("Voodoo2"),
        .VENDORSTR_LEN  (7),
        .PRODUCTSTR     ("VoodooDebug"),
        .PRODUCTSTR_LEN (11),
        .VENDORID       (16'h359F),
        .PRODUCTID      (16'h2010),
        .VERSIONBCD     (16'h0100),
        .HSSUPPORT      (0),
        .SELFPOWERED    (0)
    ) u_usb_desc (
        .CLK                    (clk60),
        .RESET                  (reset),
        .i_pid                  (16'd0),
        .i_vid                  (16'd0),
        .i_descrom_raddr        (DESCROM_RADDR),
        .o_descrom_rdat         (DESCROM_RDAT),
        .o_desc_dev_addr        (DESC_DEV_ADDR),
        .o_desc_dev_len         (DESC_DEV_LEN),
        .o_desc_qual_addr       (DESC_QUAL_ADDR),
        .o_desc_qual_len        (DESC_QUAL_LEN),
        .o_desc_fscfg_addr      (DESC_FSCFG_ADDR),
        .o_desc_fscfg_len       (DESC_FSCFG_LEN),
        .o_desc_hscfg_addr      (DESC_HSCFG_ADDR),
        .o_desc_hscfg_len       (DESC_HSCFG_LEN),
        .o_desc_oscfg_addr      (DESC_OSCFG_ADDR),
        .o_desc_strlang_addr    (DESC_STRLANG_ADDR),
        .o_desc_strvendor_addr  (DESC_STRVENDOR_ADDR),
        .o_desc_strvendor_len   (DESC_STRVENDOR_LEN),
        .o_desc_strproduct_addr (DESC_STRPRODUCT_ADDR),
        .o_desc_strproduct_len  (DESC_STRPRODUCT_LEN),
        .o_desc_strserial_addr  (DESC_STRSERIAL_ADDR),
        .o_desc_strserial_len   (DESC_STRSERIAL_LEN),
        .o_descrom_have_strings (DESCROM_HAVE_STRINGS)
    );

    USB_Device_Controller_Top u_usb_device_controller (
        .clk_i                  (clk60),
        .reset_i                (reset),
        .usbrst_o               (usb_busreset),
        .highspeed_o            (usb_highspeed),
        .suspend_o              (usb_suspend),
        .online_o               (online),
        .txdat_i                (usb_txdat),
        .txval_i                (endpt0_send & (endpt_sel == ENDPT_UART_CONFIG)),
        .txdat_len_i            (usb_txdat_len),
        .txcork_i               (usb_txcork),
        .txiso_pid_i            (4'b0011),
        .txpop_o                (usb_txpop),
        .txact_o                (usb_txact),
        .txpktfin_o             (usb_txpktfin),
        .rxdat_o                (usb_rxdat),
        .rxval_o                (usb_rxval),
        .rxact_o                (usb_rxact),
        .rxrdy_i                (usb_rxrdy),
        .rxpktval_o             (usb_rxpktval),
        .setup_o                (setup_active),
        .endpt_o                (endpt_sel),
        .sof_o                  (),
        .inf_alter_i            (inf_alter_i),
        .inf_alter_o            (inf_alter_o),
        .inf_sel_o              (inf_sel_o),
        .inf_set_o              (inf_set_o),
        .descrom_rdata_i        (DESCROM_RDAT),
        .descrom_raddr_o        (DESCROM_RADDR),
        .desc_index_o           (),
        .desc_type_o            (),
        .desc_dev_addr_i        (DESC_DEV_ADDR),
        .desc_dev_len_i         (DESC_DEV_LEN),
        .desc_qual_addr_i       (DESC_QUAL_ADDR),
        .desc_qual_len_i        (DESC_QUAL_LEN),
        .desc_fscfg_addr_i      (DESC_FSCFG_ADDR),
        .desc_fscfg_len_i       (DESC_FSCFG_LEN),
        .desc_hscfg_addr_i      (DESC_HSCFG_ADDR),
        .desc_hscfg_len_i       (DESC_HSCFG_LEN),
        .desc_oscfg_addr_i      (DESC_OSCFG_ADDR),
        .desc_hidrpt_addr_i     (16'd0),
        .desc_hidrpt_len_i      (16'd0),
        .desc_bos_addr_i        (16'd0),
        .desc_bos_len_i         (16'd0),
        .desc_strlang_addr_i    (DESC_STRLANG_ADDR),
        .desc_strvendor_addr_i  (DESC_STRVENDOR_ADDR),
        .desc_strvendor_len_i   (DESC_STRVENDOR_LEN),
        .desc_strproduct_addr_i (DESC_STRPRODUCT_ADDR),
        .desc_strproduct_len_i  (DESC_STRPRODUCT_LEN),
        .desc_strserial_addr_i  (DESC_STRSERIAL_ADDR),
        .desc_strserial_len_i   (DESC_STRSERIAL_LEN),
        .desc_have_strings_i    (DESCROM_HAVE_STRINGS),
        .utmi_dataout_o         (PHY_DATAOUT),
        .utmi_txvalid_o         (PHY_TXVALID),
        .utmi_txready_i         (PHY_TXREADY),
        .utmi_datain_i          (PHY_DATAIN),
        .utmi_rxactive_i        (PHY_RXACTIVE),
        .utmi_rxvalid_i         (PHY_RXVALID),
        .utmi_rxerror_i         (PHY_RXERROR),
        .utmi_linestate_i       (PHY_LINESTATE),
        .utmi_opmode_o          (PHY_OPMODE),
        .utmi_xcvrselect_o      (PHY_XCVRSELECT),
        .utmi_termselect_o      (PHY_TERMSELECT),
        .utmi_reset_o           (PHY_RESET)
    );

    USB_SoftPHY_Top u_usb_softphy (
        .clk_i             (clk60),
        .rst_i             (PHY_RESET),
        .utmi_data_out_i   (PHY_DATAOUT),
        .utmi_txvalid_i    (PHY_TXVALID),
        .utmi_op_mode_i    (PHY_OPMODE),
        .utmi_xcvrselect_i (PHY_XCVRSELECT),
        .utmi_termselect_i (PHY_TERMSELECT),
        .utmi_data_in_o    (PHY_DATAIN),
        .utmi_txready_o    (PHY_TXREADY),
        .utmi_rxvalid_o    (PHY_RXVALID),
        .utmi_rxactive_o   (PHY_RXACTIVE),
        .utmi_rxerror_o    (PHY_RXERROR),
        .utmi_linestate_o  (PHY_LINESTATE),
        .usb_dp_io         (usb_dp),
        .usb_dn_io         (usb_dn)
    );

    usb_uart_config u_usb_uart_config (
        .PHY_CLKOUT          (clk60),
        .RESET_IN            (usb_busreset),
        .setup_active        (setup_active),
        .endpt_sel           (endpt_sel),
        .usb_rxval           (usb_rxval),
        .usb_rxact           (usb_rxact),
        .usb_rxdat           (usb_rxdat),
        .usb_txact           (usb_txact),
        .usb_txpop           (usb_txpop),
        .usb_txdat_len_o     (uart_config_txdat_len),
        .endpt0_dat_o        (endpt0_dat),
        .endpt0_send_o       (endpt0_send),
        .uart1_en_o          (),
        .uart1_BAUD_RATE_o   (),
        .uart1_PARITY_BIT_o  (),
        .uart1_STOP_BIT_o    (),
        .uart1_DATA_BITS_o   ()
    );

    wire _unused = &{1'b0, usb_highspeed, usb_suspend, ep2_rx_dval, ep2_rx_data};
endmodule
