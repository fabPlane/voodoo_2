// Drop-in for Gowin's encrypted upar_arbiter_wrap: DRP_NUM DRP ports onto
// the single GTR12_UPAR register bus, round robin, one access at a time.
//
// UPAR handshake as assumed here (unverified, see ../PROBE.md): the master
// holds wren/rden with address and data until it sees upar_ready_i, and a
// read returns later with upar_rdvld_i. A DRP port gets drp_ready_o when
// its write is taken, or drp_rdvld_o with the data when its read returns.

module own_upar_arbiter #(
    parameter DRP_NUM   = 8,
    parameter RST_CYCLES = 64
) (
    output [DRP_NUM-1:0]    drp_clk_o,
    input  [DRP_NUM*24-1:0] drp_addr_i,
    input  [DRP_NUM-1:0]    drp_wren_i,
    input  [DRP_NUM*32-1:0] drp_wrdata_i,
    input  [DRP_NUM*8-1:0]  drp_strb_i,
    output [DRP_NUM-1:0]    drp_ready_o,
    input  [DRP_NUM-1:0]    drp_rden_i,
    output [DRP_NUM-1:0]    drp_rdvld_o,
    output [DRP_NUM*32-1:0] drp_rddata_o,
    output [DRP_NUM-1:0]    drp_resp_o,

    input                   upar_clk_i,
    output                  upar_rst_o,
    output reg [23:0]       upar_addr_o = 24'd0,
    output reg              upar_wren_o = 1'b0,
    output reg [31:0]       upar_wrdata_o = 32'd0,
    output reg [7:0]        upar_strb_o = 8'd0,
    input                   upar_ready_i,
    output reg              upar_rden_o = 1'b0,
    output                  upar_bus_width_o,
    input                   upar_rdvld_i,
    input  [31:0]           upar_rddata_i
);
    localparam IW = DRP_NUM > 1 ? $clog2(DRP_NUM) : 1;

    assign drp_clk_o        = {DRP_NUM{upar_clk_i}};
    assign upar_bus_width_o = 1'b0;   // unverified: 0 = 32-bit

    // Power-up reset for the UPAR block.
    reg [$clog2(RST_CYCLES+1)-1:0] rst_cnt = 0;
    reg rst = 1'b1;
    always @(posedge upar_clk_i)
        if (rst_cnt != RST_CYCLES) rst_cnt <= rst_cnt + 1'b1;
        else                       rst <= 1'b0;
    assign upar_rst_o = rst;

    // Requests are pulses; remember them until served.
    reg [DRP_NUM-1:0] pend_wr = 0, pend_rd = 0;
    reg [DRP_NUM*24-1:0] addr_q = 0;
    reg [DRP_NUM*32-1:0] data_q = 0;
    reg [DRP_NUM*8-1:0]  strb_q = 0;

    localparam S_IDLE = 2'd0, S_REQ = 2'd1, S_RDWAIT = 2'd2;
    reg [1:0]    state = S_IDLE;
    reg [IW-1:0] cur = 0, last = 0;
    reg          cur_rd = 1'b0;

    reg [DRP_NUM-1:0]    ready_q = 0, rdvld_q = 0;
    reg [DRP_NUM*32-1:0] rddata_q = 0;
    assign drp_ready_o  = ready_q;
    assign drp_rdvld_o  = rdvld_q;
    assign drp_rddata_o = rddata_q;
    assign drp_resp_o   = {DRP_NUM{1'b0}};

    // Next port after `last` with a pending request.
    wire [DRP_NUM-1:0] pend = pend_wr | pend_rd;
    reg [IW-1:0] pick;
    reg          pick_ok;
    integer k;
    always @* begin
        pick = 0; pick_ok = 1'b0;
        for (k = DRP_NUM; k >= 1; k = k - 1)
            if (pend[(last + k) % DRP_NUM]) begin
                pick = (last + k) % DRP_NUM; pick_ok = 1'b1;
            end
    end

    integer i;
    always @(posedge upar_clk_i) begin
        ready_q <= 0;
        rdvld_q <= 0;
        for (i = 0; i < DRP_NUM; i = i + 1)
            if (drp_wren_i[i] | drp_rden_i[i]) begin
                pend_wr[i] <= drp_wren_i[i];
                pend_rd[i] <= drp_rden_i[i];
                addr_q[i*24 +: 24] <= drp_addr_i[i*24 +: 24];
                data_q[i*32 +: 32] <= drp_wrdata_i[i*32 +: 32];
                strb_q[i*8 +: 8]   <= drp_strb_i[i*8 +: 8];
            end
        if (rst) begin
            state <= S_IDLE;
            upar_wren_o <= 1'b0;
            upar_rden_o <= 1'b0;
        end else case (state)
        S_IDLE: if (pick_ok) begin
            cur    <= pick;
            last   <= pick;
            cur_rd <= pend_rd[pick];
            upar_addr_o   <= addr_q[pick*24 +: 24];
            upar_wrdata_o <= data_q[pick*32 +: 32];
            upar_strb_o   <= strb_q[pick*8 +: 8];
            upar_wren_o   <= pend_wr[pick];
            upar_rden_o   <= pend_rd[pick];
            pend_wr[pick] <= 1'b0;
            pend_rd[pick] <= 1'b0;
            state <= S_REQ;
        end
        S_REQ: if (upar_ready_i) begin
            upar_wren_o <= 1'b0;
            upar_rden_o <= 1'b0;
            if (cur_rd) begin
                state <= S_RDWAIT;
            end else begin
                ready_q[cur] <= 1'b1;
                state <= S_IDLE;
            end
        end
        S_RDWAIT: if (upar_rdvld_i) begin
            rdvld_q[cur] <= 1'b1;
            ready_q[cur] <= 1'b1;
            rddata_q[cur*32 +: 32] <= upar_rddata_i;
            state <= S_IDLE;
        end
        default: state <= S_IDLE;
        endcase
    end
endmodule
