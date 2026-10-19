// Watches Gowin's PCIE_Controller_Top / upar_arbiter inside SerDes_Top
// (probe build, own_pcie/tools/gen_serdes.py --probe) so own_pcie_ctrl can
// copy what they drive. Read over JTAG with own_pcie/tools/pcie_probe.py.
//
// pages[k*32 +: 32] is JTAG diag page 8+k:
//   0       magic 0x0B5E0001
//   1       pl_test_out (PMAC FABRIC_TEST_BUS_MON) live
//   2       flags (see pcie_probe.py)
//   3       tl_clk_freq_o           4  tl_tx_prot_o
//   5       {brsw_in, obffcontrol, 7'b0, cfg_busdev_o}
//   6..8    PMAC tl_tx_credits_i[31:0], [63:32], [95:64]
//   9..11   controller creditsp_o, creditsnp_o, creditscpl_o
//   12..17  mismatch counters, 16 bit each, low half first:
//           rx d0 d1 | rx d2 d3 | tx d0 d1 | tx d2 d3 | ltssm d0 d1 | ltssm d2, rx_wait d0
//   18..22  credits: output o vs PMAC slice s, counters (o,s) in order
//           (p,0)(p,1)|(p,2)(np,0)|(np,1)(np,2)|(cpl,0)(cpl,1)|(cpl,2) linkup-vs-L0
//   23      PERST# rise -> PMAC reset release, tl_clk cycles (last time)
//   24      PERST# rise -> linkup_o, tl_clk cycles (last time)
//   25      {PMAC reset releases [15:0], PERST# rises [31:16]}
//   26      controller DRP {reads, writes}    27  UPAR bus {reads, writes}
//   28      upar_rst_o high cycles since configuration
//   29..31  0
//   32..63  UPAR log, 16 entries: {wr, rd, 6'b0, addr} then data
//           (write data, or read data when it returns)
//   64..95  LTSSM log, 16 entries: pl_test_out then cycles since PERST# rise,
//           each time controller ltssm_o changes
module own_pcie_probe (
    input          clk,
    input          rstn_i,
    // PMAC side into the controller
    input  [31:0]  pl_test_out_i,
    input  [95:0]  credits_i,
    input  [12:0]  busdev_i,
    input          prim_rx_sop, prim_rx_eop,
    input  [255:0] prim_rx_data,
    input  [7:0]   prim_rx_valid, prim_rx_err,
    input  [5:0]   prim_rx_bardec,
    input          prim_tx_wait,
    // user side into the controller
    input          user_rx_wait, user_rx_masknp,
    input          user_tx_sop, user_tx_eop,
    input  [255:0] user_tx_data,
    input  [7:0]   user_tx_valid,
    // controller outputs, user side
    input          v_rx_sop, v_rx_eop,
    input  [255:0] v_rx_data,
    input  [7:0]   v_rx_valid, v_rx_err,
    input  [5:0]   v_rx_bardec,
    input          v_tx_wait,
    input  [4:0]   v_ltssm,
    input          v_linkup,
    input  [31:0]  v_cred_p, v_cred_np, v_cred_cpl,
    input  [12:0]  v_busdev,
    // controller outputs, PMAC side
    input          v_rx_wait, v_rx_masknp,
    input          v_tx_sop, v_tx_eop,
    input  [255:0] v_tx_data,
    input  [7:0]   v_tx_valid,
    input          v_pmac_rstn,
    input  [21:0]  v_clk_freq,
    input  [31:0]  v_tx_prot,
    input  [7:0]   v_brsw_in,
    input  [3:0]   v_obff,
    input          v_vcc, v_gnd,
    input          v_drp_wr, v_drp_rd,
    // UPAR bus
    input          upar_rst,
    input  [23:0]  upar_addr,
    input          upar_wren, upar_rden,
    input  [31:0]  upar_wrdata,
    input  [7:0]   upar_strb,
    input          upar_bus_width,
    input          upar_ready, upar_rdvld,
    input  [31:0]  upar_rddata,
    input          upar_clk,
    output [96*32-1:0] pages
);
    // --- delayed copies of the controller inputs for latency checks ---
    localparam RXW = 1 + 1 + 256 + 8 + 8 + 6;
    localparam TXW = 1 + 1 + 256 + 8;
    wire [RXW-1:0] rx_in  = {prim_rx_sop, prim_rx_eop, prim_rx_data, prim_rx_valid, prim_rx_err, prim_rx_bardec};
    wire [RXW-1:0] rx_out = {v_rx_sop, v_rx_eop, v_rx_data, v_rx_valid, v_rx_err, v_rx_bardec};
    wire [TXW-1:0] tx_in  = {user_tx_sop, user_tx_eop, user_tx_data, user_tx_valid};
    wire [TXW-1:0] tx_out = {v_tx_sop, v_tx_eop, v_tx_data, v_tx_valid};
    reg  [RXW-1:0] rx_d1 = 0, rx_d2 = 0, rx_d3 = 0;
    reg  [TXW-1:0] tx_d1 = 0, tx_d2 = 0, tx_d3 = 0;
    reg  [4:0]     lt_d1 = 0, lt_d2 = 0;
    reg            rw_d1 = 0;
    always @(posedge clk) begin
        rx_d1 <= rx_in;  rx_d2 <= rx_d1; rx_d3 <= rx_d2;
        tx_d1 <= tx_in;  tx_d2 <= tx_d1; tx_d3 <= tx_d2;
        lt_d1 <= pl_test_out_i[4:0]; lt_d2 <= lt_d1;
        rw_d1 <= user_rx_wait;
    end

    // Comparisons are registered before counting to keep the 256-bit
    // compares off the counter paths.
    localparam NC = 23;
    reg [NC-1:0] ne = 0;
    always @(posedge clk) begin
        ne[0]  <= rx_out != rx_in;
        ne[1]  <= rx_out != rx_d1;
        ne[2]  <= rx_out != rx_d2;
        ne[3]  <= rx_out != rx_d3;
        ne[4]  <= tx_out != tx_in;
        ne[5]  <= tx_out != tx_d1;
        ne[6]  <= tx_out != tx_d2;
        ne[7]  <= tx_out != tx_d3;
        ne[8]  <= v_ltssm != pl_test_out_i[4:0];
        ne[9]  <= v_ltssm != lt_d1;
        ne[10] <= v_ltssm != lt_d2;
        ne[11] <= v_rx_wait != user_rx_wait;
        ne[12] <= v_cred_p   != credits_i[31:0];
        ne[13] <= v_cred_p   != credits_i[63:32];
        ne[14] <= v_cred_p   != credits_i[95:64];
        ne[15] <= v_cred_np  != credits_i[31:0];
        ne[16] <= v_cred_np  != credits_i[63:32];
        ne[17] <= v_cred_np  != credits_i[95:64];
        ne[18] <= v_cred_cpl != credits_i[31:0];
        ne[19] <= v_cred_cpl != credits_i[63:32];
        ne[20] <= v_cred_cpl != credits_i[95:64];
        ne[21] <= v_linkup != (v_ltssm == 5'h11);
        ne[22] <= 1'b0;
    end
    reg [15:0] cnt [0:NC-1];
    integer i;
    initial for (i = 0; i < NC; i = i + 1) cnt[i] = 0;
    always @(posedge clk)
        for (i = 0; i < NC; i = i + 1)
            if (ne[i] && cnt[i] != 16'hFFFF) cnt[i] <= cnt[i] + 1'b1;

    // --- reset / link timing ---
    reg [1:0]  rstn_s = 0;
    reg        pmac_d = 0, link_d = 0;
    reg [31:0] since = 0, t_pmac = 0, t_link = 0;
    reg [15:0] n_pmac = 0, n_perst = 0;
    always @(posedge clk) begin
        rstn_s <= {rstn_s[0], rstn_i};
        pmac_d <= v_pmac_rstn;
        link_d <= v_linkup;
        if (rstn_s == 2'b01) begin
            since   <= 0;
            n_perst <= n_perst + 1'b1;
        end else if (since != 32'hFFFFFFFF) begin
            since <= since + 1'b1;
        end
        if (v_pmac_rstn && !pmac_d) begin
            t_pmac <= since;
            n_pmac <= n_pmac + 1'b1;
        end
        if (v_linkup && !link_d) t_link <= since;
    end

    // --- DRP / UPAR activity ---
    reg [15:0] drp_wr_n = 0, drp_rd_n = 0;
    reg        dwr_d = 0, drd_d = 0;
    always @(posedge clk) begin
        dwr_d <= v_drp_wr; drd_d <= v_drp_rd;
        if (v_drp_wr && !dwr_d) drp_wr_n <= drp_wr_n + 1'b1;
        if (v_drp_rd && !drd_d) drp_rd_n <= drp_rd_n + 1'b1;
    end

    reg [15:0] up_wr_n = 0, up_rd_n = 0;
    reg [31:0] up_rst_cycles = 0;
    reg [31:0] ulog [0:31];
    reg [4:0]  ulog_n = 0;       // entries written, saturates at 16
    reg [3:0]  rd_slot = 0;
    reg        rd_wait = 0;
    initial for (i = 0; i < 32; i = i + 1) ulog[i] = 0;
    always @(posedge upar_clk) begin
        if (upar_rst && up_rst_cycles != 32'hFFFFFFFF) up_rst_cycles <= up_rst_cycles + 1'b1;
        // A request counts in the cycle it is taken (ready high).
        if ((upar_wren || upar_rden) && upar_ready) begin
            if (upar_wren) up_wr_n <= up_wr_n + 1'b1;
            else           up_rd_n <= up_rd_n + 1'b1;
            if (!ulog_n[4]) begin
                ulog[{ulog_n[3:0], 1'b0}] <= {upar_wren, upar_rden, 6'b0, upar_addr};
                ulog[{ulog_n[3:0], 1'b1}] <= upar_wren ? upar_wrdata : 32'hFFFFFFFF;
                ulog_n <= ulog_n + 1'b1;
                if (upar_rden) begin
                    rd_slot <= ulog_n[3:0];
                    rd_wait <= 1'b1;
                end
            end
        end
        if (upar_rdvld && rd_wait) begin
            ulog[{rd_slot, 1'b1}] <= upar_rddata;
            rd_wait <= 1'b0;
        end
    end

    // --- LTSSM history ---
    reg [31:0] llog [0:31];
    reg [4:0]  llog_n = 0;
    reg [4:0]  lt_prev = 5'h1F;
    initial for (i = 0; i < 32; i = i + 1) llog[i] = 0;
    always @(posedge clk) begin
        lt_prev <= v_ltssm;
        if (v_ltssm != lt_prev && !llog_n[4]) begin
            llog[{llog_n[3:0], 1'b0}] <= pl_test_out_i;
            llog[{llog_n[3:0], 1'b1}] <= since;
            llog_n <= llog_n + 1'b1;
        end
    end

    wire [31:0] flags = {
        v_ltssm, v_linkup, v_pmac_rstn, v_vcc,                 // [31:24]
        v_gnd, v_rx_masknp, v_rx_wait, user_rx_wait,           // [23:20]
        user_rx_masknp, v_tx_wait, prim_tx_wait, upar_rst,     // [19:16]
        upar_bus_width, rstn_i, upar_ready, 1'b0,              // [15:12]
        upar_strb, 4'b0                                        // [11:0]
    };

    genvar g;
    generate
        for (g = 0; g < 16; g = g + 1) begin : gl
            assign pages[(32 + 2*g) * 32 +: 32] = ulog[2*g];
            assign pages[(33 + 2*g) * 32 +: 32] = ulog[2*g + 1];
            assign pages[(64 + 2*g) * 32 +: 32] = llog[2*g];
            assign pages[(65 + 2*g) * 32 +: 32] = llog[2*g + 1];
        end
    endgenerate
    assign pages[0*32 +: 32]  = 32'h0B5E0001;
    assign pages[1*32 +: 32]  = pl_test_out_i;
    assign pages[2*32 +: 32]  = flags;
    assign pages[3*32 +: 32]  = {10'd0, v_clk_freq};
    assign pages[4*32 +: 32]  = v_tx_prot;
    assign pages[5*32 +: 32]  = {v_brsw_in, v_obff, 7'd0, v_busdev};
    assign pages[6*32 +: 32]  = credits_i[31:0];
    assign pages[7*32 +: 32]  = credits_i[63:32];
    assign pages[8*32 +: 32]  = credits_i[95:64];
    assign pages[9*32 +: 32]  = v_cred_p;
    assign pages[10*32 +: 32] = v_cred_np;
    assign pages[11*32 +: 32] = v_cred_cpl;
    generate
        for (g = 0; g < 11; g = g + 1) begin : gc
            assign pages[(12 + g) * 32 +: 32] = {cnt[2*g + 1], cnt[2*g]};
        end
    endgenerate
    assign pages[23*32 +: 32] = t_pmac;
    assign pages[24*32 +: 32] = t_link;
    assign pages[25*32 +: 32] = {n_perst, n_pmac};
    assign pages[26*32 +: 32] = {drp_rd_n, drp_wr_n};
    assign pages[27*32 +: 32] = {up_rd_n, up_wr_n};
    assign pages[28*32 +: 32] = up_rst_cycles;
    assign pages[29*32 +: 96] = 96'd0;
    wire _unused = &{1'b0, busdev_i, user_tx_valid, rw_d1};
endmodule
