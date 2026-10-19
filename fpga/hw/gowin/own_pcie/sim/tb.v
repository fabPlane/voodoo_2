`timescale 1ps/1ps
// SerDes_Top (generated from Gowin's serdes.v with own_pcie_ctrl and
// own_upar_arbiter) against the behavioural hard blocks.

module tb;
    reg tl_clk = 0;
    always #5000 tl_clk = ~tl_clk;   // 100 MHz
    reg rstn = 0;

    wire         rx_sop, rx_eop, tx_wait, linkup, int_ack;
    wire [255:0] rx_data;
    wire [7:0]   rx_valid, rx_err;
    wire [5:0]   rx_bardec;
    wire [4:0]   ltssm;
    wire [31:0]  cred_p, cred_np, cred_cpl;
    wire [12:0]  busdev;
    wire         drp_clk, drp_resp, drp_rdvld, drp_ready;
    wire [31:0]  drp_rddata;
    reg          rx_wait = 0;
    reg          tx_sop = 0, tx_eop = 0;
    reg  [255:0] tx_data = 0;
    reg  [7:0]   tx_valid = 0;
    reg  [23:0]  drp_addr = 0;
    reg  [31:0]  drp_wrdata = 0;
    reg          drp_wr = 0, drp_rd = 0;

`ifdef PROBE
    wire [3071:0] probe;
`endif
    SerDes_Top dut (
        .PCIE_Controller_Top_pcie_rstn_i(rstn),
        .PCIE_Controller_Top_pcie_tl_clk_i(tl_clk),
        .PCIE_Controller_Top_pcie_tl_rx_sop_o(rx_sop),
        .PCIE_Controller_Top_pcie_tl_rx_eop_o(rx_eop),
        .PCIE_Controller_Top_pcie_tl_rx_data_o(rx_data),
        .PCIE_Controller_Top_pcie_tl_rx_valid_o(rx_valid),
        .PCIE_Controller_Top_pcie_tl_rx_bardec_o(rx_bardec),
        .PCIE_Controller_Top_pcie_tl_rx_err_o(rx_err),
        .PCIE_Controller_Top_pcie_tl_rx_wait_i(rx_wait),
        .PCIE_Controller_Top_pcie_tl_rx_masknp_i(1'b0),
        .PCIE_Controller_Top_pcie_tl_tx_sop_i(tx_sop),
        .PCIE_Controller_Top_pcie_tl_tx_eop_i(tx_eop),
        .PCIE_Controller_Top_pcie_tl_tx_data_i(tx_data),
        .PCIE_Controller_Top_pcie_tl_tx_valid_i(tx_valid),
        .PCIE_Controller_Top_pcie_tl_tx_wait_o(tx_wait),
        .PCIE_Controller_Top_pcie_tl_int_status_i(1'b0),
        .PCIE_Controller_Top_pcie_tl_int_req_i(1'b0),
        .PCIE_Controller_Top_pcie_tl_int_msinum_i(5'd0),
        .PCIE_Controller_Top_pcie_tl_int_ack_o(int_ack),
        .PCIE_Controller_Top_pcie_ltssm_o(ltssm),
        .PCIE_Controller_Top_pcie_tl_tx_creditsp_o(cred_p),
        .PCIE_Controller_Top_pcie_tl_tx_creditsnp_o(cred_np),
        .PCIE_Controller_Top_pcie_tl_tx_creditscpl_o(cred_cpl),
        .PCIE_Controller_Top_pcie_tl_cfg_busdev_o(busdev),
        .PCIE_Controller_Top_pcie_linkup_o(linkup),
        .PCIE_Controller_Top_pcie_tl_drp_clk_o(drp_clk),
        .PCIE_Controller_Top_pcie_tl_drp_rddata_o(drp_rddata),
        .PCIE_Controller_Top_pcie_tl_drp_resp_o(drp_resp),
        .PCIE_Controller_Top_pcie_tl_drp_rd_valid_o(drp_rdvld),
        .PCIE_Controller_Top_pcie_tl_drp_ready_o(drp_ready),
        .PCIE_Controller_Top_pcie_tl_drp_addr_i(drp_addr),
        .PCIE_Controller_Top_pcie_tl_drp_wrdata_i(drp_wrdata),
        .PCIE_Controller_Top_pcie_tl_drp_strb_i(8'h0F),
        .PCIE_Controller_Top_pcie_tl_drp_wr_i(drp_wr),
        .PCIE_Controller_Top_pcie_tl_drp_rd_i(drp_rd)
`ifdef PROBE
        , .own_pcie_probe_o(probe)
`endif
    );
`ifdef PROBE
    // probe page k is JTAG diag page 8+k
    `define PG(k) probe[(k)*32 +: 32]
    `define LO(k) probe[(k)*32 +: 16]
    `define HI(k) probe[(k)*32+16 +: 16]
`endif

    `define PMAC dut.gtr12_pmac_inst

    integer errors = 0;
    task check(input cond, input [8*64-1:0] what);
        if (!cond) begin
            errors = errors + 1;
            $display("%t FAIL %0s", $time, what);
        end
    endtask

    // RX: everything the user side sees, with random rx_wait.
    reg [255:0] rxcap_data [0:63];
    reg [7:0]   rxcap_valid [0:63];
    reg [5:0]   rxcap_bar [0:63];
    reg         rxcap_sop [0:63], rxcap_eop [0:63];
    integer     rxcap_n = 0;
    reg [7:0]   wlfsr = 8'h5A;
    reg         rand_wait = 0;
    always @(posedge tl_clk) begin
        wlfsr   <= {wlfsr[6:0], wlfsr[7] ^ wlfsr[5] ^ wlfsr[4] ^ wlfsr[3]};
        rx_wait <= rand_wait && wlfsr[0];
        if (rx_valid != 0 && !rx_wait) begin
            rxcap_data[rxcap_n % 64] = rx_data; rxcap_valid[rxcap_n % 64] = rx_valid;
            rxcap_bar[rxcap_n % 64]  = rx_bardec;
            rxcap_sop[rxcap_n % 64]  = rx_sop; rxcap_eop[rxcap_n % 64] = rx_eop;
            rxcap_n = rxcap_n + 1;
        end
    end

    // TX: one beat, held while tx_wait.
    task tx_beat(input sop, input eop, input [7:0] valid, input [255:0] data);
        begin
            @(posedge tl_clk);
            tx_sop <= sop; tx_eop <= eop; tx_valid <= valid; tx_data <= data;
            @(posedge tl_clk);
            while (tx_wait) @(posedge tl_clk);
            tx_sop <= 0; tx_eop <= 0; tx_valid <= 0;
        end
    endtask

    reg [31:0] rd_val;
    task drp_write(input [23:0] a, input [31:0] d);
        begin
            @(posedge tl_clk);
            drp_addr <= a; drp_wrdata <= d; drp_wr <= 1;
            @(posedge tl_clk);
            drp_wr <= 0;
            while (!drp_ready) @(posedge tl_clk);
        end
    endtask
    task drp_read(input [23:0] a);
        begin
            @(posedge tl_clk);
            drp_addr <= a; drp_rd <= 1;
            @(posedge tl_clk);
            drp_rd <= 0;
            while (!drp_rdvld) @(posedge tl_clk);
            rd_val = drp_rddata;
        end
    endtask

    integer i, n_rx, t_rel, t_pmac;
    reg [255:0] beat;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb.vcd");
            $dumpvars(0, tb);
        end
        #1_000_000 rstn = 1;
        t_rel = $time;
        wait (`PMAC.FABRIC_TL_NPOR === 1'b1);
        t_pmac = $time;
        $display("PMAC reset released %0d ns after PERST#", (t_pmac - t_rel) / 1000);
        check(`PMAC.FABRIC_PL_NPOR === 1'b1, "PL_NPOR follows TL_NPOR");
        check(`PMAC.PL_LTSSM_ENABLE === 1'b1, "LTSSM enable");
        check(`PMAC.TL_CLKP === tl_clk, "TL_CLKP is tl_clk");

        wait (linkup === 1'b1);
        $display("%t link up, ltssm %h", $time, ltssm);
        check(ltssm == 5'h11, "ltssm L0");
        check(busdev == 13'h0108, "busdev");
        check(cred_p == 32'h0010_0020 && cred_np == 32'h0030_0040 && cred_cpl == 32'h0050_0060,
              "credits split");

        // RX: a 1-DW MWr, a 2-beat MWr and an MRd, with random rx_wait.
        rand_wait = 1;
        `PMAC.rx_beat(1, 1, 8'h0F, {128'd0, 32'h11223344, 32'h00001000, 32'h000000FF, 32'h40000001}, 6'h01);
        `PMAC.rx_beat(1, 0, 8'hFF, {192'hA5A5_0000_0000_0001_0000_0002_0000_0003, 32'h00002000, 32'h000000FF, 32'h40000009}, 6'h01);
        `PMAC.rx_beat(0, 1, 8'h3F, {64'd0, 192'hC0DE_0000_0000_0004_0000_0005_0000_0006}, 6'h01);
        `PMAC.rx_beat(1, 1, 8'h07, {160'd0, 32'h00003000, 32'h000010FF, 32'h00000001}, 6'h01);
        n_rx = 4;
        wait (rxcap_n == n_rx);
        repeat (20) @(posedge tl_clk);
        check(rxcap_n == n_rx, "no extra RX beats");
        for (i = 0; i < n_rx; i = i + 1) begin
            check(rxcap_data[i] == `PMAC.rxq_data[i] && rxcap_valid[i] == `PMAC.rxq_valid[i]
                  && rxcap_bar[i] == `PMAC.rxq_bar[i] && rxcap_sop[i] == `PMAC.rxq_sop[i]
                  && rxcap_eop[i] == `PMAC.rxq_eop[i], "RX beat matches");
        end
        rand_wait = 0;

        // TX: a 3-DW CplD with one data DW, then a 2-beat CplD.
        tx_beat(1, 1, 8'h0F, {128'd0, 32'hFEEDBEEF, 32'h01000000, 32'h01080004, 32'h4A000001});
        tx_beat(1, 0, 8'hFF, {32'h1, 32'h2, 32'h3, 32'h4, 32'h5, 32'h01000000, 32'h01080020, 32'h4A000008});
        tx_beat(0, 1, 8'h1F, {96'd0, 32'h6, 32'h7, 32'h8, 32'h9, 32'hA});
        repeat (10) @(posedge tl_clk);
        check(`PMAC.txcap_n == 3, "3 TX beats");
        check(`PMAC.txcap_data[0][127:96] == 32'hFEEDBEEF && `PMAC.txcap_sop[0] && `PMAC.txcap_eop[0],
              "TX beat 0");
        check(`PMAC.txcap_valid[2] == 8'h1F && `PMAC.txcap_eop[2] && !`PMAC.txcap_sop[2], "TX beat 2");

        // DRP through the arbiter to UPAR.
        drp_write(24'h80_8760, 32'h0041_C010);
        drp_write(24'hB0_0030, 32'h003F_00F0);
        drp_read(24'h80_8760);
        check(rd_val == 32'h0041_C010, "DRP readback 1");
        drp_read(24'hB0_0030);
        check(rd_val == 32'h003F_00F0, "DRP readback 2");
        check(dut.gtr12_upar_inst.upar_writes == 2 && dut.gtr12_upar_inst.upar_reads == 2,
              "UPAR saw 2 writes, 2 reads");

        // PERST# again: link drops and comes back.
        rstn = 0;
        repeat (10) @(posedge tl_clk);
        check(!linkup && `PMAC.FABRIC_TL_NPOR === 1'b0, "reset drops link");
        rstn = 1;
        wait (linkup === 1'b1);

`ifdef PROBE
        // own_pcie_ctrl is a passthrough with registered LTSSM / linkup.
        $display("probe: rx d0 %0d tx d0 %0d ltssm d1 %0d cred %h %h %h  pmac +%0d link +%0d  upar wr %0d rd %0d",
                 `LO(12), `LO(14), `HI(16),
                 `PG(18), `PG(19), `PG(20), `PG(23), `PG(24), `LO(27), `HI(27));
        check(`PG(0) == 32'h0B5E0001, "probe magic");
        check(`LO(12) == 0 && `LO(14) == 0, "probe: TL passthrough d0");
        check(`HI(12) != 0 && `HI(14) != 0, "probe: d1 differs");
        check(`HI(16) == 0, "probe: ltssm d1");
        check(`LO(18) == 0 && `LO(20) == 0 && `LO(22) == 0, "probe: credits on own slice");
        check(`HI(18) != 0 && `HI(19) != 0, "probe: credits off-slice differ");
        check(`PG(23) > 1020 && `PG(23) < 1030, "probe: PMAC reset delay");
        check(`PG(25) == {16'd2, 16'd2}, "probe: 2 PERST# rises, 2 releases");
        check(`PG(27) == {16'd2, 16'd2}, "probe: UPAR 2 writes 2 reads");
        check(`PG(32) == {2'b10, 6'd0, 24'h808760} && `PG(33) == 32'h0041C010, "probe: UPAR log 0");
        check(`PG(36) == {2'b01, 6'd0, 24'h808760} && `PG(37) == 32'h0041C010, "probe: UPAR log read");
        check(probe[64*32 +: 5] == 5'h00 && probe[66*32 +: 5] == 5'h01, "probe: LTSSM log");
        check(`PG(28) > 0, "probe: upar_rst seen");
`endif
        if (errors == 0) $display("PASS");
        else $display("FAILED: %0d errors", errors);
        $finish;
    end

    initial begin
        #200_000_000;
        $display("FAILED: timeout");
        $finish;
    end
endmodule
