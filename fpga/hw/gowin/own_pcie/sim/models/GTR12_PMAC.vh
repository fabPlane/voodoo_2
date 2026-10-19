// PMAC model: a root complex at the TL ports.
//  - LTSSM on FABRIC_TEST_BUS_MON[4:0]: walks to L0 (0x11) once both NPORs
//    are high and the lane PLLs are locked; back to 0 on reset.
//  - RX: tasks rx_beat() push beats into a queue; a beat is presented until
//    the cycle after TL_RX_WAIT0 is low (no beats while wait is high).
//  - TX: logs beats taken (valid, !wait); TL_TX_WAIT0 is pseudo-random.
reg pclk_r = 0;
always #4000 pclk_r = ~pclk_r;       // 125 MHz
assign PCIE_HALF_CLK = pclk_r;
assign PCIE_CLK      = pclk_r;

localparam [31:0] CRED_P = 32'h0010_0020, CRED_NP = 32'h0030_0040, CRED_CPL = 32'h0050_0060;
assign TL_TX_CREDITS0 = {CRED_CPL, CRED_NP, CRED_P};
assign TLCFG_BUSDEV   = 13'h0108;

wire pll_ok = Q0_CPLL0_OK_I & Q0_CPLL1_OK_I & Q0_CPLL2_OK_I & Q0_CPLL3_OK_I;
reg [4:0]  ltssm_r = 0;
reg [15:0] lt_cnt = 0;
always @(posedge TL_CLKP)
    if (!(FABRIC_PL_NPOR && FABRIC_TL_NPOR && pll_ok && PL_LTSSM_ENABLE)) begin
        ltssm_r <= 0; lt_cnt <= 0;
    end else if (ltssm_r != 5'h11) begin
        lt_cnt <= lt_cnt + 1'b1;
        if (lt_cnt == 16'd200) begin
            lt_cnt  <= 0;
            ltssm_r <= ltssm_r == 5'h07 ? 5'h11 : ltssm_r + 1'b1;
        end
    end
assign FABRIC_TEST_BUS_MON = {27'd0, ltssm_r};

// RX queue
reg [255:0] rxq_data [0:63];
reg [7:0]   rxq_valid [0:63];
reg [5:0]   rxq_bar [0:63];
reg         rxq_sop [0:63], rxq_eop [0:63];
integer     rxq_wr = 0, rxq_rd = 0;
task rx_beat(input sop, input eop, input [7:0] valid, input [255:0] data, input [5:0] bar);
    begin
        rxq_data[rxq_wr % 64] = data; rxq_valid[rxq_wr % 64] = valid;
        rxq_bar[rxq_wr % 64] = bar;   rxq_sop[rxq_wr % 64] = sop;
        rxq_eop[rxq_wr % 64] = eop;   rxq_wr = rxq_wr + 1;
    end
endtask
reg         rx_on = 0;
reg [255:0] rx_data_r = 0;
reg [7:0]   rx_valid_r = 0;
reg [5:0]   rx_bar_r = 0;
reg         rx_sop_r = 0, rx_eop_r = 0;
always @(posedge TL_CLKP) begin
    if (!rx_on || !TL_RX_WAIT0) begin
        if (ltssm_r == 5'h11 && rxq_rd != rxq_wr && !TL_RX_WAIT0) begin
            rx_on <= 1;
            rx_data_r <= rxq_data[rxq_rd % 64]; rx_valid_r <= rxq_valid[rxq_rd % 64];
            rx_bar_r  <= rxq_bar[rxq_rd % 64];  rx_sop_r   <= rxq_sop[rxq_rd % 64];
            rx_eop_r  <= rxq_eop[rxq_rd % 64];  rxq_rd = rxq_rd + 1;
        end else begin
            rx_on <= 0;
        end
    end
end
assign TL_RX_SOP0    = rx_on & rx_sop_r;
assign TL_RX_EOP0    = rx_on & rx_eop_r;
assign TL_RX_VALID0  = rx_on ? rx_valid_r : 8'd0;
assign TL_RX_DATA0   = rx_data_r;
assign TL_RX_BARDEC0 = rx_on ? {14'd0, rx_bar_r} : 20'd0;
assign TL_RX_ERR0    = 8'd0;

// TX capture
reg [15:0] lfsr = 16'hACE1;
reg        tx_wait_r = 1;
always @(posedge TL_CLKP) begin
    lfsr      <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
    tx_wait_r <= ltssm_r != 5'h11 || lfsr[1:0] == 2'b00;
end
assign TL_TX_WAIT0 = tx_wait_r;
reg [255:0] txcap_data [0:63];
reg [7:0]   txcap_valid [0:63];
reg         txcap_sop [0:63], txcap_eop [0:63];
integer     txcap_n = 0;
always @(posedge TL_CLKP)
    if (TL_TX_VALID0 != 0 && !tx_wait_r) begin
        txcap_data[txcap_n % 64] = TL_TX_DATA0; txcap_valid[txcap_n % 64] = TL_TX_VALID0;
        txcap_sop[txcap_n % 64] = TL_TX_SOP0;   txcap_eop[txcap_n % 64] = TL_TX_EOP0;
        txcap_n = txcap_n + 1;
    end
assign TL_INT_ACK0 = TL_INT_REQ0;
