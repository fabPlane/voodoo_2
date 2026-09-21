// Configuration-only diagnostic for Tang Console 138K.
// Claims the same external pins as the SDRAM Voodoo target while avoiding
// PLLs, BSRAM, DSPs, and the large generated core.
module config_smoke_top (
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
    output wire [12:0] O_sdram_addr,
    output wire [1:0]  O_sdram_ba,
    output wire        O_sdram_cs_n,
    output wire        O_sdram_ras_n,
    output wire        O_sdram_cas_n,
    output wire        O_sdram_wen_n,
    output wire        O_sdram_cke,
    output wire        O_sdram_clk,
    output wire [1:0]  O_sdram_dqm,
    inout  wire [15:0] IO_sdram_dq
);
    reg [25:0] counter = 26'd0;
    always @(posedge clk50)
        counter <= counter + 1'b1;

    assign led_n = ~counter[25:24];
    assign uart_tx = uart_rx;
    assign usb_soft_dp = 1'bz;
    assign usb_soft_dn = 1'bz;
    assign usb_pullup_en = 1'b0;
    assign tmds_clk_p = 1'b0;
    assign tmds_clk_n = 1'b1;
    assign tmds_data_p = 3'b000;
    assign tmds_data_n = 3'b111;

    // Put the SDRAM in an inactive, non-driving state.
    assign O_sdram_addr = 13'd0;
    assign O_sdram_ba = 2'd0;
    assign O_sdram_cs_n = 1'b1;
    assign O_sdram_ras_n = 1'b1;
    assign O_sdram_cas_n = 1'b1;
    assign O_sdram_wen_n = 1'b1;
    assign O_sdram_cke = 1'b1;
    assign O_sdram_clk = 1'b0;
    assign O_sdram_dqm = 2'b11;
    assign IO_sdram_dq = 16'hzzzz;

    wire _unused = &{1'b0, key0_n, key1_n};
endmodule
