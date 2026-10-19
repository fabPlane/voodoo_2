// Drop-in for Gowin's encrypted PCIE_Controller_Top (GW5AST, GTR12_PMAC).
//
// The hard MAC does the PCIe work; the vendor wrapper is glue between the
// user TL ports and GTR12_PMAC: reset, clock, credits split, LTSSM/link
// status and a few constant inputs. Its observe-only inputs (PIPE TX data,
// lane RX data, RX detect, PMA lock) are kept so the port list matches, but
// nothing here uses them yet.
//
// Values marked "unverified" are guesses until the probe build has read the
// vendor wrapper's outputs on the board (see ../PROBE.md).

module own_pcie_ctrl #(
    // unverified: PLDA-style TL clock frequency, assumed kHz
    parameter [21:0] TL_CLK_FREQ = 22'd100000,
    // unverified: L0 encoding on the test bus (board_diag.py sees 0x11)
    parameter [4:0]  LTSSM_L0    = 5'h11,
    // tl_clk cycles PMAC reset is held after PERST# and lane PLL lock
    parameter        RST_DELAY   = 1024
) (
    input          pcie_rstn_i,
    input          pcie_tl_clk_i,
    // user RX (requests to us)
    output         pcie_tl_rx_sop_o,
    output         pcie_tl_rx_eop_o,
    output [255:0] pcie_tl_rx_data_o,
    output [7:0]   pcie_tl_rx_valid_o,
    output [5:0]   pcie_tl_rx_bardec_o,
    output [7:0]   pcie_tl_rx_err_o,
    input          pcie_tl_rx_wait_i,
    input          pcie_tl_rx_masknp_i,
    // user TX
    input          pcie_tl_tx_sop_i,
    input          pcie_tl_tx_eop_i,
    input  [255:0] pcie_tl_tx_data_i,
    input  [7:0]   pcie_tl_tx_valid_i,
    output         pcie_tl_tx_wait_o,
    // interrupts
    input          pcie_tl_int_status_i,
    input          pcie_tl_int_req_i,
    input  [4:0]   pcie_tl_int_msinum_i,
    output         pcie_tl_int_ack_o,
    // user DRP
    output         pcie_tl_drp_clk_o,
    input  [23:0]  pcie_tl_drp_addr_i,
    input  [31:0]  pcie_tl_drp_wrdata_i,
    output [31:0]  pcie_tl_drp_rddata_o,
    input          pcie_tl_drp_wr_i,
    input          pcie_tl_drp_rd_i,
    output         pcie_tl_drp_rd_valid_o,
    output         pcie_tl_drp_ready_o,
    input  [7:0]   pcie_tl_drp_strb_i,
    output         pcie_tl_drp_resp_o,
    // status
    output [4:0]   pcie_ltssm_o,
    output [31:0]  pcie_tl_tx_creditsp_o,
    output [31:0]  pcie_tl_tx_creditsnp_o,
    output [31:0]  pcie_tl_tx_creditscpl_o,
    output [12:0]  pcie_tl_cfg_busdev_o,
    output         pcie_linkup_o,
    // GTR12_PMAC TL
    input          pcie_tl_rx_sop_i,
    input          pcie_tl_rx_eop_i,
    input  [255:0] pcie_tl_rx_data_i,
    input  [7:0]   pcie_tl_rx_valid_i,
    input  [5:0]   pcie_tl_rx_bardec_i,
    input  [7:0]   pcie_tl_rx_err_i,
    output         pcie_tl_rx_wait_o,
    output         pcie_tl_rx_masknp_o,
    output         pcie_tl_tx_sop_o,
    output         pcie_tl_tx_eop_o,
    output [255:0] pcie_tl_tx_data_o,
    output [7:0]   pcie_tl_tx_valid_o,
    input          pcie_tl_tx_wait_i,
    output         pcie_tl_int_status_o,
    output         pcie_tl_int_req_o,
    output [4:0]   pcie_tl_int_msinum_o,
    input          pcie_tl_int_ack_i,
    // DRP towards the UPAR arbiter
    input          pcie_tl_drp_clk_i,
    output [23:0]  pcie_tl_drp_addr_o,
    output [31:0]  pcie_tl_drp_wrdata_o,
    input  [31:0]  pcie_tl_drp_rddata_i,
    output         pcie_tl_drp_wr_o,
    output         pcie_tl_drp_rd_o,
    input          pcie_tl_drp_rd_valid_i,
    input          pcie_tl_drp_ready_i,
    output [7:0]   pcie_tl_drp_strb_o,
    input          pcie_tl_drp_resp_i,
    // GTR12_PMAC status
    input  [95:0]  pcie_tl_tx_credits_i,
    input  [31:0]  pcie_pl_test_out_i,
    input  [12:0]  pcie_tl_cfg_busdev_i,
    // observe-only PHY signals (unused)
    input          fabric_pl_rx_det,
    input          fabric_ln0_pma_rx_lock,
    input  [5:0]   fabric_ln0_astat,
    input  [127:0] fabric_pl_txdata,
    input  [127:0] fabric_pl_txdata_h,
    input  [15:0]  fabric_pl_txdatak,
    input  [15:0]  fabric_pl_txdatak_h,
    input  [7:0]   fabric_pl_txdatavalid,
    input  [7:0]   fabric_pl_txdatavalid_h,
    input  [87:0]  fabric_ln0_rxdata,
    input          fabric_ln0_rxdatavalid,
    input  [87:0]  fabric_ln1_rxdata,
    input  [87:0]  fabric_ln2_rxdata,
    input  [87:0]  fabric_ln3_rxdata,
    input  [1:0]   fabric_pl_rate,
    input  [1:0]   fabric_pl_rate_h,
    input          pcie_half_clk_i,
    // to GTR12_PMAC
    output         pcie_pmac_rstn_o,
    output [21:0]  pcie_tl_clk_freq_o,
    output [31:0]  pcie_tl_tx_prot_o,
    output [7:0]   pcie_tl_brsw_in_o,
    output [3:0]   pcie_tl_pm_obffcontrol_o,
    output         pcie_VCC,
    output         pcie_GND,
    output         pcie_tl_clk_o
);
    assign pcie_tl_clk_o = pcie_tl_clk_i;

    // PERST# release, synchronised to tl_clk, then RST_DELAY cycles.
    reg [2:0] rstn_s = 3'b0;
    always @(posedge pcie_tl_clk_i or negedge pcie_rstn_i)
        if (!pcie_rstn_i) rstn_s <= 3'b0;
        else              rstn_s <= {rstn_s[1:0], 1'b1};

    localparam RW = $clog2(RST_DELAY + 1);
    reg [RW-1:0] rst_cnt = 0;
    reg          pmac_rstn = 1'b0;
    always @(posedge pcie_tl_clk_i)
        if (!rstn_s[2]) begin
            rst_cnt   <= 0;
            pmac_rstn <= 1'b0;
        end else if (rst_cnt != RST_DELAY[RW-1:0]) begin
            rst_cnt <= rst_cnt + 1'b1;
        end else begin
            pmac_rstn <= 1'b1;
        end
    assign pcie_pmac_rstn_o = pmac_rstn;

    // TL passthrough
    assign pcie_tl_rx_sop_o     = pcie_tl_rx_sop_i;
    assign pcie_tl_rx_eop_o     = pcie_tl_rx_eop_i;
    assign pcie_tl_rx_data_o    = pcie_tl_rx_data_i;
    assign pcie_tl_rx_valid_o   = pcie_tl_rx_valid_i;
    assign pcie_tl_rx_bardec_o  = pcie_tl_rx_bardec_i;
    assign pcie_tl_rx_err_o     = pcie_tl_rx_err_i;
    assign pcie_tl_rx_wait_o    = pcie_tl_rx_wait_i;
    assign pcie_tl_rx_masknp_o  = pcie_tl_rx_masknp_i;
    assign pcie_tl_tx_sop_o     = pcie_tl_tx_sop_i;
    assign pcie_tl_tx_eop_o     = pcie_tl_tx_eop_i;
    assign pcie_tl_tx_data_o    = pcie_tl_tx_data_i;
    assign pcie_tl_tx_valid_o   = pcie_tl_tx_valid_i;
    assign pcie_tl_tx_wait_o    = pcie_tl_tx_wait_i;
    assign pcie_tl_int_status_o = pcie_tl_int_status_i;
    assign pcie_tl_int_req_o    = pcie_tl_int_req_i;
    assign pcie_tl_int_msinum_o = pcie_tl_int_msinum_i;
    assign pcie_tl_int_ack_o    = pcie_tl_int_ack_i;

    // DRP passthrough
    assign pcie_tl_drp_clk_o      = pcie_tl_drp_clk_i;
    assign pcie_tl_drp_addr_o     = pcie_tl_drp_addr_i;
    assign pcie_tl_drp_wrdata_o   = pcie_tl_drp_wrdata_i;
    assign pcie_tl_drp_wr_o       = pcie_tl_drp_wr_i;
    assign pcie_tl_drp_rd_o       = pcie_tl_drp_rd_i;
    assign pcie_tl_drp_strb_o     = pcie_tl_drp_strb_i;
    assign pcie_tl_drp_rddata_o   = pcie_tl_drp_rddata_i;
    assign pcie_tl_drp_rd_valid_o = pcie_tl_drp_rd_valid_i;
    assign pcie_tl_drp_ready_o    = pcie_tl_drp_ready_i;
    assign pcie_tl_drp_resp_o     = pcie_tl_drp_resp_i;

    // unverified: posted / non-posted / completion, low to high
    assign pcie_tl_tx_creditsp_o   = pcie_tl_tx_credits_i[31:0];
    assign pcie_tl_tx_creditsnp_o  = pcie_tl_tx_credits_i[63:32];
    assign pcie_tl_tx_creditscpl_o = pcie_tl_tx_credits_i[95:64];
    assign pcie_tl_cfg_busdev_o    = pcie_tl_cfg_busdev_i;

    // unverified: LTSSM state in test bus [4:0]
    reg [4:0] ltssm = 5'd0;
    reg       linkup = 1'b0;
    always @(posedge pcie_tl_clk_i) begin
        ltssm  <= pcie_pl_test_out_i[4:0];
        linkup <= pmac_rstn && pcie_pl_test_out_i[4:0] == LTSSM_L0;
    end
    assign pcie_ltssm_o  = ltssm;
    assign pcie_linkup_o = linkup;

    // unverified constants
    assign pcie_tl_clk_freq_o       = TL_CLK_FREQ;
    assign pcie_tl_tx_prot_o        = 32'd0;
    assign pcie_tl_brsw_in_o        = 8'd0;
    assign pcie_tl_pm_obffcontrol_o = 4'd0;
    assign pcie_VCC                 = 1'b1;
    assign pcie_GND                 = 1'b0;
endmodule
