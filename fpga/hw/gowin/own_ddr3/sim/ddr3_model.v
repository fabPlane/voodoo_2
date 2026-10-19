`timescale 1ns / 1ps
// Behavioural x32 DDR3 (two x16 dies seen as 4 byte lanes) for own_ddr3.
// Checks the timing own_ddr3_ctrl relies on and reports violations as
// "DDR3 ERROR". Memory is 2^20 words ({bank, row[6:0], col}); rows alias.
//
// Read data is driven edge-aligned with DQS (DQS rises with CK at cycle
// RD + CL), per lane delayed by RD_DLY{n} ns of board flight time. Writes
// are captured on DQS edges; the first DQS rising edge must fall within
// tDQSS (+-0.25 tCK) of the CK edge at WR + CWL.
module ddr3_model #(
    parameter real TCK = 2.5,
    parameter real RD_DLY0 = 0.3,
    parameter real RD_DLY1 = 0.5,
    parameter real RD_DLY2 = 0.9,
    parameter real RD_DLY3 = 1.4,
    parameter real TDS = 0.15,
    parameter real TDH = 0.15
) (
    input  wire        ck,
    input  wire        ck_n,
    input  wire        cke,
    input  wire        cs_n,
    input  wire        ras_n,
    input  wire        cas_n,
    input  wire        we_n,
    input  wire [2:0]  ba,
    input  wire [14:0] a,
    input  wire        odt,
    input  wire        reset_n,
    input  wire [3:0]  dm,
    inout  wire [31:0] dq,
    inout  wire [3:0]  dqs,
    inout  wire [3:0]  dqs_n
);

    integer errors = 0;
    task err(input [8*64-1:0] msg);
        begin
            errors = errors + 1;
            if (errors < 400)
                $display("%t DDR3 ERROR: %0s", $realtime, msg);
        end
    endtask

    // Timing in ns
    localparam real tRCD = 13.75, tRP = 13.75, tRAS = 35.0, tRC = 48.75,
                    tRRD = 7.5, tFAW = 40.0, tRFC = 260.0, tWR = 15.0,
                    tWTR = 7.5, tRTP = 7.5, tREFI = 7800.0, tMRD_CK = 4,
                    tMOD = 15.0;

    reg [31:0] mem [0:(1<<20)-1];
    reg [15:0] mr [0:3];
    integer cl = 6, cwl = 5;
    reg     init_done = 0;

    real bank_ready [0:7];
    real bank_act   [0:7];
    reg  bank_open  [0:7];
    reg [14:0] bank_row [0:7];
    real act_hist [0:3];
    integer act_hp = 0;
    real last_act = -1000.0, last_ref = -1e9, last_mrs = -1000.0;
    real last_rd = -1000.0, last_wr_end = -1000.0;
    real max_ref_gap = 0.0;
    integer refs = 0, reads = 0, writes = 0;
    integer i;

    initial begin
        for (i = 0; i < 8; i = i + 1) begin
            bank_ready[i] = 0.0; bank_act[i] = -1000.0; bank_open[i] = 0;
        end
        for (i = 0; i < 4; i = i + 1) act_hist[i] = -1000.0;
        for (i = 0; i < 4; i = i + 1) mr[i] = 16'h0;
    end

    // ------------------------------------------------------------------
    // Cycle bookkeeping for read bursts / write capture
    // ------------------------------------------------------------------
    integer cyc = 0;
    reg        rq_v   [0:63];
    reg        rq_pre [0:63];
    reg        rq_post[0:63];
    reg [19:0] rq_base[0:63];
    reg [1:0]  rq_j   [0:63];
    reg        wq_v   [0:63];
    reg [19:0] wq_base[0:63];
    initial for (i = 0; i < 64; i = i + 1) begin
        rq_v[i] = 0; rq_pre[i] = 0; rq_post[i] = 0; wq_v[i] = 0;
    end

    function [19:0] widx;
        input [2:0] b;
        input [14:0] row;
        input [9:0] col;
        widx = {b, row[6:0], col};
    endfunction

    // ------------------------------------------------------------------
    // Command decode
    // ------------------------------------------------------------------
    real now;
    real pre_start;
    integer s;
    reg [8*64-1:0] m;
    always @(posedge ck) begin
        cyc = cyc + 1;
        now = $realtime;
        if (reset_n === 1'b1 && cke === 1'b1 && cs_n === 1'b0) begin
            case ({ras_n, cas_n, we_n})
            3'b000: begin // MRS
                if (now - last_mrs < tMRD_CK * TCK - 0.01) err("tMRD");
                mr[ba[1:0]] = a;
                last_mrs = now;
                if (ba[1:0] == 2'd0) begin
                    case ({a[6:4], a[2]})
                        4'b0010: cl = 5; 4'b0100: cl = 6; 4'b0110: cl = 7;
                        4'b1000: cl = 8; default: err("unsupported CL");
                    endcase
                    if (a[1:0] != 2'b00) err("BL not fixed 8");
                    init_done = 1;
                end
                if (ba[1:0] == 2'd2) cwl = 5 + a[5:3];
            end
            3'b001: begin // REF
                for (i = 0; i < 8; i = i + 1) begin
                    if (bank_open[i]) err("REF with open bank");
                    if (now < bank_ready[i] - 0.01) err("REF before tRP");
                end
                if (now - last_ref < tRFC - 0.01) err("REF-REF tRFC");
                if (refs > 0 && now - last_ref > max_ref_gap) max_ref_gap = now - last_ref;
                if (refs > 0 && now - last_ref > 9 * tREFI) err("refresh starved");
                last_ref = now;
                refs = refs + 1;
            end
            3'b010: begin // PRE
                if (a[10]) begin
                    for (i = 0; i < 8; i = i + 1) if (bank_open[i]) begin
                        bank_open[i] = 0; bank_ready[i] = now + tRP;
                    end
                end else if (bank_open[ba]) begin
                    bank_open[ba] = 0; bank_ready[ba] = now + tRP;
                end
            end
            3'b011: begin // ACT
                if (!init_done) err("ACT before init");
                if (bank_open[ba]) err("ACT to open bank");
                if (now < bank_ready[ba] - 0.01) err("ACT before tRP");
                if (now - bank_act[ba] < tRC - 0.01) err("tRC");
                if (now - last_act < tRRD - 0.01) err("tRRD");
                if (now - act_hist[act_hp] < tFAW - 0.01) err("tFAW");
                if (now - last_ref < tRFC - 0.01) err("ACT before tRFC");
                act_hist[act_hp] = now; act_hp = (act_hp + 1) % 4;
                last_act = now;
                bank_open[ba] = 1; bank_row[ba] = a; bank_act[ba] = now;
            end
            3'b101, 3'b100: begin // RD / WR
                if (!bank_open[ba]) err("RD/WR to closed bank");
                if (now - bank_act[ba] < tRCD - 0.01) err("tRCD");
                if (a[2:0] != 3'b000) err("unaligned column");
                if (!we_n) begin
                    // write
                    if (now - last_rd < (cl + 4 + 2 - cwl) * TCK - 0.01) err("RD->WR");
                    writes = writes + 1;
                    s = (cyc + cwl - 1) % 64;
                    wq_v[s] = 1;
                    wq_base[s] = widx(ba, bank_row[ba], a[9:0]);
                    last_wr_end = now + (cwl + 4) * TCK;
                    if (a[10]) begin
                        pre_start = last_wr_end + tWR;
                        if (pre_start < bank_act[ba] + tRAS) pre_start = bank_act[ba] + tRAS;
                        bank_open[ba] = 0; bank_ready[ba] = pre_start + tRP;
                    end
                end else begin
                    if (now - last_wr_end < tWTR - 0.01 ||
                        now - last_wr_end < 4 * TCK - 0.01) err("tWTR");
                    reads = reads + 1;
                    last_rd = now;
                    for (i = 0; i < 4; i = i + 1) begin
                        s = (cyc + cl + i) % 64;
                        if (rq_v[s]) err("read burst overlap");
                        rq_v[s] = 1; rq_j[s] = i;
                        rq_base[s] = widx(ba, bank_row[ba], a[9:0]);
                    end
                    rq_pre[(cyc + cl - 1) % 64] = 1;
                    rq_post[(cyc + cl + 4) % 64] = 1;
                    if (a[10]) begin
                        pre_start = now + 4 * TCK;
                        if (pre_start < now + tRTP) pre_start = now + tRTP;
                        if (pre_start < bank_act[ba] + tRAS) pre_start = bank_act[ba] + tRAS;
                        bank_open[ba] = 0; bank_ready[ba] = pre_start + tRP;
                    end
                end
            end
            3'b110: ; // ZQ
            default: ;
            endcase
        end
        drive_pos;
        arm_write;
        check_missing;
    end

    // ------------------------------------------------------------------
    // Read data / DQS drive (edge aligned at CK edges)
    // ------------------------------------------------------------------
    reg [31:0] dq_drv;
    reg        dq_oe = 0;
    reg        dqs_drv = 0;
    reg        dqs_oe = 0;
    integer    rs;
    task drive_pos;
    begin
        rs = cyc % 64;          // cyc already advanced for this edge
        if (rq_v[rs]) begin
            dq_drv <= mem[rq_base[rs] + 2 * rq_j[rs]];
            dq_oe <= 1; dqs_drv <= 1; dqs_oe <= 1;
        end else if (rq_pre[rs]) begin
            dq_oe <= 0; dqs_drv <= 0; dqs_oe <= 1;
        end else if (rq_post[rs]) begin
            dq_oe <= 0; dqs_drv <= 0; dqs_oe <= 1;
        end else begin
            dq_oe <= 0; dqs_oe <= 0;
        end
    end
    endtask
    always @(negedge ck) begin
        rs = cyc % 64;
        if (rq_v[rs]) begin
            dq_drv <= mem[rq_base[rs] + 2 * rq_j[rs] + 1];
            dqs_drv <= 0;
        end else if (rq_pre[rs]) begin
            dqs_drv <= 0;
        end else if (rq_post[rs]) begin
            dqs_oe <= 0;        // postamble is half a tCK
        end
        rq_v[rs] = 0; rq_pre[rs] = 0; rq_post[rs] = 0;
    end

    // Per-lane board delay (transport)
    reg [31:0] dq_pad;
    reg [3:0]  dq_pad_oe = 0, dqs_pad = 0, dqs_pad_oe = 0;
    genvar gl;
    generate
        for (gl = 0; gl < 4; gl = gl + 1) begin : g_lane
            localparam real D = gl == 0 ? RD_DLY0 : gl == 1 ? RD_DLY1 :
                                gl == 2 ? RD_DLY2 : RD_DLY3;
            always @(dq_drv[8*gl +: 8]) dq_pad[8*gl +: 8] <= #(D) dq_drv[8*gl +: 8];
            always @(dq_oe)   dq_pad_oe[gl]  <= #(D) dq_oe;
            always @(dqs_drv) dqs_pad[gl]    <= #(D) dqs_drv;
            always @(dqs_oe)  dqs_pad_oe[gl] <= #(D) dqs_oe;
            assign dq[8*gl +: 8] = dq_pad_oe[gl] ? dq_pad[8*gl +: 8] : 8'bz;
            assign dqs[gl]   = dqs_pad_oe[gl] ? dqs_pad[gl] : 1'bz;
            assign dqs_n[gl] = dqs_pad_oe[gl] ? ~dqs_pad[gl] : 1'bz;
`ifdef DQS_IDLE_LOW
            // Optional weak idle level (as with an FPGA-side pull); without
            // it an undriven DQS reads as X and can hold the gate open.
            pulldown (dqs[gl]);
            pullup (dqs_n[gl]);
`endif
        end
    endgenerate

    // ------------------------------------------------------------------
    // Write capture
    // ------------------------------------------------------------------
    // At the CK edge one cycle before WR + CWL, arm each lane with the
    // expected time of the first DQS rising edge.
    real    w_exp   [0:3];
    integer w_beat  [0:3];
    reg [19:0] w_base [0:3];
    reg     w_armed [0:3];
    reg     w_pend [0:3];
    reg [19:0] w_pbase [0:3];
    real    dq_chg  [0:3];
    real    hold_until [0:3];
    reg     dqs_prev [0:3];
    integer ws;
    initial for (i = 0; i < 4; i = i + 1) begin
        w_armed[i] = 0; w_pend[i] = 0; w_beat[i] = 8; dq_chg[i] = 0.0; hold_until[i] = 0.0;
        dqs_prev[i] = 0;
    end
    task arm_write;
    begin
        ws = cyc % 64;
        if (wq_v[ws]) begin
            wq_v[ws] = 0;
            for (i = 0; i < 4; i = i + 1) begin
                if (w_armed[i] && w_beat[i] < 8) begin
                    // seamless follow-on burst: start when this one ends
                    if (w_pend[i]) err("write burst overlap");
                    w_pend[i] = 1;
                    w_pbase[i] = wq_base[ws];
                end else begin
                    w_armed[i] = 1;
                    w_exp[i] = $realtime + TCK;
                    w_beat[i] = 0;
                    w_base[i] = wq_base[ws];
                end
            end
        end
    end
    endtask

    generate
        for (gl = 0; gl < 4; gl = gl + 1) begin : g_wcap
            always @(dq[8*gl +: 8] or dm[gl]) begin
                dq_chg[gl] = $realtime;
                if ($realtime < hold_until[gl] - 0.001) err("write tDH");
            end
            always @(dqs[gl]) begin : edge_blk
                reg [31:0] w;
                if ((dqs[gl] === 1'b1 && dqs_prev[gl] === 1'b0) ||
                    (dqs[gl] === 1'b0 && dqs_prev[gl] === 1'b1)) begin
                    if (w_armed[gl] && w_beat[gl] < 8) begin
                        if (w_beat[gl] == 0 && w_exp[gl] >= 0.0) begin
                            if (dqs[gl] !== 1'b1) begin
                                // falling edge before the burst: preamble end
                                // or a glitch; only rising edges start it
                            end else begin
                                if ($realtime < w_exp[gl] - 0.25 * TCK ||
                                    $realtime > w_exp[gl] + 0.25 * TCK) begin
                                    $display("%t lane %0d first DQS rise %0.3f ns off",
                                             $realtime, gl, $realtime - w_exp[gl]);
                                    err("tDQSS");
                                end
                            end
                        end
                        if (w_beat[gl] > 0 || dqs[gl] === 1'b1) begin
                            if ($realtime - dq_chg[gl] < TDS - 0.001) err("write tDS");
                            hold_until[gl] = $realtime + TDH;
                            if (^dq[8*gl +: 8] === 1'bx) err("write data X/Z");
                            if (dm[gl] === 1'b0) begin
                                w = mem[w_base[gl] + w_beat[gl]];
                                w[8*gl +: 8] = dq[8*gl +: 8];
                                mem[w_base[gl] + w_beat[gl]] = w;
                            end
                            w_beat[gl] = w_beat[gl] + 1;
                            if (w_beat[gl] == 8) begin
                                w_armed[gl] = 0;
                                if (w_pend[gl]) begin
                                    // next burst continues on the next edge
                                    w_pend[gl] = 0;
                                    w_armed[gl] = 1;
                                    w_beat[gl] = 0;
                                    w_base[gl] = w_pbase[gl];
                                    w_exp[gl] = -1.0;   // no tDQSS check
                                end
                            end
                        end
                    end
                end
                dqs_prev[gl] = dqs[gl];
            end
        end
    endgenerate

    // Armed lane that never saw its burst
    task check_missing;
        for (i = 0; i < 4; i = i + 1)
            if (w_armed[i] && w_beat[i] == 0 && w_exp[i] >= 0.0 && $realtime > w_exp[i] + 2 * TCK) begin
                err("write burst missing DQS");
                w_armed[i] = 0;
            end
    endtask

endmodule
