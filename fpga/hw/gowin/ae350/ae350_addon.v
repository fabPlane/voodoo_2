// ae350_addon: the GW5AST's hard Andes A25 RISC-V (AE350_SOC primitive,
// declared in $GOWINHOME/IDE/data/hardware_core/gw5at/prim_syn.v), used
// directly without Gowin's encrypted RiscV_AE350_SOC wrapper. Optional:
// board_top instantiates it under `ifdef USE_AE350.
//
//   0x80000000  boot ROM (reset vector), ROM AHB port: 8 KB BSRAM loaded
//               from AE350_ROM_HEX ($readmemh, 32-bit words)
//   0x00000000  data RAM, DDR AHB port: 16 KB BSRAM (64-bit, byte writes)
//   0xE8000000  mailbox, extended AHB slave window: +0 mailbox register
//               (cpu_reg, read back), +4 free-running bus-clock counter
//   0xF0300000  UART2 (ATCUART100, 16550 registers at +0x20)
//
// Tie-offs follow Gowin's own post-PnR netlist of the AE350 demo and LiteX
// (litex/soc/cores/cpu/gowin_ae350). CORE_CLK must come straight from the
// PLL at site PLL_R[0] (INS_LOC in the .cst).
`ifndef AE350_ROM_HEX
`define AE350_ROM_HEX "ae350_rom.hex"
`endif
module ae350_addon (
    input  wire        clk_cpu,     // from the PLL at PLL_R[0], <= 800 MHz
    input  wire        clk_bus,     // AHB / APB / DDR-port / RTC clock
    input  wire        rst_n,       // bus-clock domain
    output wire        uart_txd,
    input  wire        uart_rxd,
    output reg  [31:0] cpu_reg = 32'd0
);
    // ---- boot ROM: 2048 x 32, word reads, one-cycle data phase ---------
    wire [31:0] rom_haddr; wire [1:0] rom_htrans; wire rom_hwrite;
    reg  [31:0] rom [0:2047];
    initial $readmemh(`AE350_ROM_HEX, rom);
    reg  [31:0] rom_q;
    always @(posedge clk_bus) rom_q <= rom[rom_haddr[12:2]];

    // ---- data RAM: 2048 x 64 -------------------------------------------
    wire [31:0] d_haddr; wire [2:0] d_hburst, d_hsize; wire [3:0] d_hprot;
    wire [1:0] d_htrans; wire [63:0] d_hwdata; wire d_hwrite;
    reg  [63:0] ram [0:2047];
    reg  [63:0] ram_q;
    reg  [10:0] d_wa = 0; reg d_we = 1'b0; reg [7:0] d_be = 0;
    wire [7:0] be = (d_hsize == 3'd3) ? 8'hff :
                    (d_hsize == 3'd2) ? (8'h0f << {d_haddr[2], 2'b00}) :
                    (d_hsize == 3'd1) ? (8'h03 << {d_haddr[2:1], 1'b0}) :
                                        (8'h01 << d_haddr[2:0]);
    wire d_rd = d_htrans[1] & ~d_hwrite;
    // A read whose address phase is a write's data phase gets the old word
    // from the BSRAM: forward the written bytes.
    reg        fwd = 1'b0; reg [63:0] fwd_data = 0; reg [7:0] fwd_be = 0;
    always @(posedge clk_bus) begin
        d_we <= d_htrans[1] & d_hwrite;
        d_wa <= d_haddr[13:3];
        d_be <= be;
        ram_q <= ram[d_haddr[13:3]];
        fwd <= d_we & d_rd & (d_haddr[13:3] == d_wa);
        fwd_data <= d_hwdata;
        fwd_be <= d_be;
    end
    genvar b;
    generate for (b = 0; b < 8; b = b + 1) begin : wb
        always @(posedge clk_bus) if (d_we && d_be[b]) ram[d_wa][8*b +: 8] <= d_hwdata[8*b +: 8];
    end endgenerate
    wire [63:0] ram_rdata;
    generate for (b = 0; b < 8; b = b + 1) begin : rf
        assign ram_rdata[8*b +: 8] = (fwd && fwd_be[b]) ? fwd_data[8*b +: 8] : ram_q[8*b +: 8];
    end endgenerate

    // ---- mailbox on the extended AHB slave -----------------------------
    wire [31:0] s_haddr, s_hwdata; wire [2:0] s_hburst, s_hsize; wire [3:0] s_hprot;
    wire [1:0] s_htrans; wire s_hsel, s_hwrite;
    reg  s_wr = 1'b0; reg s_a = 1'b0;
    reg  [31:0] cnt = 0;
    always @(posedge clk_bus) begin
        cnt <= cnt + 1'b1;
        s_wr <= s_hsel & s_htrans[1] & s_hwrite & ~s_haddr[2];
        s_a <= s_haddr[2];
        if (s_wr) cpu_reg <= s_hwdata;
    end
    wire [31:0] s_hrdata = s_a ? cnt : cpu_reg;

    AE350_SOC u_soc (
        .POR_N(1'b1), .HW_RSTN(rst_n),
        .CORE_CLK(clk_cpu), .DDR_CLK(clk_bus), .AHB_CLK(clk_bus), .APB_CLK(clk_bus),
        .DBG_TCK(1'b1), .RTC_CLK(clk_bus),
        .CORE_CE(1'b1), .AXI_CE(1'b1), .DDR_CE(1'b1), .AHB_CE(1'b1),
        // APB peripheral enables: bit0 APB, 2 UART2, 5 PIT.
        .APB_CE(8'b0010_0101), .APB2AHB_CE(1'b1),
        .SCAN_TEST(1'b0), .SCAN_EN(1'b0),
        .PRESETN(), .HRESETN(), .DDR_RSTN(),
        .GP_INT(16'd0), .DMA_REQ(8'd0), .DMA_ACK(),
        .CORE0_WFI_MODE(), .WAKEUP_IN(1'b0), .RTC_WAKEUP(),
        .TEST_CLK(1'b0), .TEST_MODE(1'b0), .TEST_RSTN(1'b1),
        .ROM_HADDR(rom_haddr), .ROM_HRDATA(rom_q), .ROM_HREADY(1'b1), .ROM_HRESP(1'b0),
        .ROM_HTRANS(rom_htrans), .ROM_HWRITE(rom_hwrite),
        .APB_PADDR(), .APB_PENABLE(), .APB_PRDATA(32'd0), .APB_PREADY(1'b0), .APB_PSEL(),
        .APB_PWDATA(), .APB_PWRITE(), .APB_PSLVERR(1'b0), .APB_PPROT(), .APB_PSTRB(),
        .EXTS_HRDATA(s_hrdata), .EXTS_HREADYIN(1'b1), .EXTS_HRESP(1'b0), .EXTS_HADDR(s_haddr),
        .EXTS_HBURST(s_hburst), .EXTS_HPROT(s_hprot), .EXTS_HSEL(s_hsel), .EXTS_HSIZE(s_hsize),
        .EXTS_HTRANS(s_htrans), .EXTS_HWDATA(s_hwdata), .EXTS_HWRITE(s_hwrite),
        .EXTM_HADDR(32'd0), .EXTM_HBURST(3'd0), .EXTM_HPROT(4'd0), .EXTM_HRDATA(),
        .EXTM_HREADY(1'b0), .EXTM_HREADYOUT(), .EXTM_HRESP(), .EXTM_HSEL(1'b0),
        .EXTM_HSIZE(3'd0), .EXTM_HTRANS(2'd0), .EXTM_HWDATA(64'd0), .EXTM_HWRITE(1'b0),
        .DDR_HADDR(d_haddr), .DDR_HBURST(d_hburst), .DDR_HPROT(d_hprot), .DDR_HRDATA(ram_rdata),
        .DDR_HREADY(1'b1), .DDR_HRESP(1'b0), .DDR_HSIZE(d_hsize), .DDR_HTRANS(d_htrans),
        .DDR_HWDATA(d_hwdata), .DDR_HWRITE(d_hwrite),
        // A25 debug JTAG unused (it needs its own fabric pins and adapter).
        .TMS_IN(1'b1), .TRST_IN(1'b0), .TDI_IN(1'b1), .TDO_OUT(), .TDO_OE(),
        .SPI2_HOLDN_IN(1'b0), .SPI2_WPN_IN(1'b0), .SPI2_CLK_IN(1'b0), .SPI2_CSN_IN(1'b0),
        .SPI2_MISO_IN(1'b0), .SPI2_MOSI_IN(1'b0),
        .SPI2_HOLDN_OUT(), .SPI2_HOLDN_OE(), .SPI2_WPN_OUT(), .SPI2_WPN_OE(),
        .SPI2_CLK_OUT(), .SPI2_CLK_OE(), .SPI2_CSN_OUT(), .SPI2_CSN_OE(),
        .SPI2_MISO_OUT(), .SPI2_MISO_OE(), .SPI2_MOSI_OUT(), .SPI2_MOSI_OE(),
        .I2C_SCL_IN(1'b1), .I2C_SDA_IN(1'b1), .I2C_SCL(), .I2C_SDA(),
        .UART1_TXD(), .UART1_RTSN(), .UART1_RXD(1'b1), .UART1_CTSN(1'b0), .UART1_DSRN(1'b1),
        .UART1_DCDN(1'b1), .UART1_RIN(1'b1), .UART1_DTRN(), .UART1_OUT1N(), .UART1_OUT2N(),
        .UART2_TXD(uart_txd), .UART2_RTSN(), .UART2_RXD(uart_rxd), .UART2_CTSN(1'b0),
        .UART2_DCDN(1'b1), .UART2_DSRN(1'b1), .UART2_RIN(1'b1), .UART2_DTRN(),
        .UART2_OUT1N(), .UART2_OUT2N(),
        .CH0_PWM(), .CH0_PWMOE(), .CH1_PWM(), .CH1_PWMOE(), .CH2_PWM(), .CH2_PWMOE(),
        .CH3_PWM(), .CH3_PWMOE(),
        .GPIO_IN(32'd0), .GPIO_OE(), .GPIO_OUT()
    );
endmodule
