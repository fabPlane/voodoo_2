// ae350_shell: AE350 hard RISC-V + 8 KB boot ROM (AHB, 32-bit) + 16 KB data
// RAM (AHB, 64-bit) in BSRAM + one CPU-written register on the extended AHB
// slave port (exported so synthesis keeps the core). Synthesis-cost test.
module ae350_shell (
    input  wire        clk_cpu,     // up to 800 MHz
    input  wire        clk_bus,     // AHB / APB / DDR-port clock
    input  wire        clk_rtc,
    input  wire        rst_n,
    output wire        uart_txd,
    input  wire        uart_rxd,
    output reg  [31:0] cpu_reg
);
    // Boot ROM: 2048 x 32
    wire [31:0] rom_haddr; wire [1:0] rom_htrans; wire rom_hwrite, rom_hclk, rom_hrstn;
    reg  [31:0] rom [0:2047];
    reg  [31:0] rom_q;
    always @(posedge clk_bus) rom_q <= rom[rom_haddr[12:2]];
    // Data RAM: 2048 x 64 with byte enables, AHB data phase registered
    wire [31:0] d_haddr; wire [2:0] d_hburst, d_hsize; wire [3:0] d_hprot; wire [1:0] d_htrans;
    wire [63:0] d_hwdata; wire d_hwrite, d_hclk, d_hrstn;
    reg  [63:0] ram [0:2047];
    reg  [63:0] ram_q;
    reg  [10:0] d_wa; reg d_we; reg [7:0] d_be;
    wire [7:0] be = (d_hsize == 3'd3) ? 8'hff : (d_hsize == 3'd2) ? (8'h0f << {d_haddr[2], 2'b00}) :
                    (d_hsize == 3'd1) ? (8'h03 << {d_haddr[2:1], 1'b0}) : (8'h01 << d_haddr[2:0]);
    always @(posedge clk_bus) begin
        d_we <= d_htrans[1] & d_hwrite; d_wa <= d_haddr[13:3]; d_be <= be;
        ram_q <= ram[d_haddr[13:3]];
    end
    genvar b;
    generate for (b = 0; b < 8; b = b + 1) begin : wb
        always @(posedge clk_bus) if (d_we && d_be[b]) ram[d_wa][8*b +: 8] <= d_hwdata[8*b +: 8];
    end endgenerate
    // Extended AHB slave: one register (address phase latched, data phase write)
    wire [31:0] s_haddr, s_hwdata; wire [2:0] s_hburst, s_hsize; wire [3:0] s_hprot; wire [1:0] s_htrans;
    wire s_hsel, s_hwrite, s_hclk, s_hrstn;
    reg s_wr;
    always @(posedge clk_bus) begin
        s_wr <= s_hsel & s_htrans[1] & s_hwrite;
        if (s_wr) cpu_reg <= s_hwdata;
    end

    RiscV_AE350_SOC_Top u_ae350 (
        .ROM_HRDATA(rom_q), .ROM_HREADY(1'b1), .ROM_HRESP(1'b0), .ROM_HADDR(rom_haddr),
        .ROM_HTRANS(rom_htrans), .ROM_HWRITE(rom_hwrite), .ROM_HCLK(rom_hclk), .ROM_HRSTN(rom_hrstn),
        .DDR_HRDATA(ram_q), .DDR_HREADY(1'b1), .DDR_HRESP(1'b0), .DDR_HADDR(d_haddr), .DDR_HBURST(d_hburst),
        .DDR_HPROT(d_hprot), .DDR_HSIZE(d_hsize), .DDR_HTRANS(d_htrans), .DDR_HWDATA(d_hwdata),
        .DDR_HWRITE(d_hwrite), .DDR_HCLK(d_hclk), .DDR_HRSTN(d_hrstn),
        .TCK_IN(1'b0), .TMS_IN(1'b1), .TRST_IN(1'b0), .TDI_IN(1'b0), .TDO_OUT(), .TDO_OE(),
        .EXTS_HRDATA(cpu_reg), .EXTS_HREADYIN(1'b1), .EXTS_HRESP(1'b0), .EXTS_HADDR(s_haddr),
        .EXTS_HBURST(s_hburst), .EXTS_HPROT(s_hprot), .EXTS_HSEL(s_hsel), .EXTS_HSIZE(s_hsize),
        .EXTS_HTRANS(s_htrans), .EXTS_HWDATA(s_hwdata), .EXTS_HWRITE(s_hwrite), .EXTS_HCLK(s_hclk),
        .EXTS_HRSTN(s_hrstn),
        .UART2_TXD(uart_txd), .UART2_RTSN(), .UART2_RXD(uart_rxd), .UART2_CTSN(1'b0), .UART2_DCDN(1'b1),
        .UART2_DSRN(1'b1), .UART2_RIN(1'b1), .UART2_DTRN(), .UART2_OUT1N(), .UART2_OUT2N(),
        .CORE0_WFI_MODE(), .RTC_WAKEUP(),
        .CORE_CLK(clk_cpu), .DDR_CLK(clk_bus), .AHB_CLK(clk_bus), .APB_CLK(clk_bus), .RTC_CLK(clk_rtc),
        .POR_RSTN(rst_n), .HW_RSTN(rst_n)
    );
endmodule
