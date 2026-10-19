// Tang Mega 138K Pro Dock DDR3 bring-up, own_ddr3 variant (define
// OWN_DDR3 to use fpga/hw/gowin/own_ddr3 instead of the Gowin IP).
// Copied from ../../ddr3_test/ddr3_jtag_top.v; ops C/D added.
//
//
// Same PLL / DDR3 IP wiring and instance names as ../src/board_top.v, so a
// pass here validates that wrapper's DDR3 path. The host talks to it over the
// on-board USB-JTAG (GW_JTAG ER1, IR 0x42) with ddr3_jtag.py:
//
//   64-bit logical DR = {data[31:0], cmd[31:0]}; each scan returns the
//   response of the previous command (see resp below).
//
//   cmd[31:28] op:
//     0 NOP      -> status
//     1 SETADDR  addr = data (byte address, low 5 bits ignored for bursts)
//     2 WBUF     wbuf[cmd[2:0]] = data
//     3 WRITE    write wbuf (256 bits) at addr; cmd[8] = addr += 32 after
//     4 READ     read 256 bits at addr into rbuf; cmd[8] = addr += 32 after
//     5 RBUF     -> rbuf[cmd[2:0]]
//     6 BIST     fill data bursts from addr with pattern(seed = cmd[15:0]
//                replicated), then read back and compare. cmd[19:16] = idle
//                cycles between commands, cmd[25:20] = max reads in flight - 1
//     7 BISTRES  -> cmd[1:0]: 0 errors, 1 first bad addr, 2 OR of bad bits,
//                3 bursts checked; 4 issued, 5 state, 6 ref_ack count
//     8 ADDR     -> addr
//     9 ECHO     -> data
//     A CYCLES   -> free-running UI clock cycle counter
//     B REFRESH  pulse ref_req every data UI cycles (0 = off) -> ref_ack count
//     C DBGRD    -> own_ddr3 debug register cmd[7:0]
//     D DBGWR    own_ddr3 debug register cmd[7:0] = data
//
//   Response = {payload[31:0], status[31:0]}, status =
//     {8'hD3, seq[7:0], calib, pll_lock, ddr_rst, busy, bist_done, bist_fail,
//      rd_timeout, cmd_ready, wr_data_rdy, 3'b0, last_op[3:0]}
//
// LEDs (active low): 0 UI-clock heartbeat, 1 PLL lock, 2 calibration done,
// 3 BIST pass (solid) / fail (fast blink).
module ddr3_jtag_top (
    input  wire        clk50,
    output wire [3:0]  led_n,
    input  wire        tck_pad_i,
    input  wire        tms_pad_i,
    input  wire        tdi_pad_i,
    output wire        tdo_pad_o,
    output wire [14:0] O_ddr_addr,
    output wire [2:0]  O_ddr_ba,
    output wire        O_ddr_cs_n,
    output wire        O_ddr_ras_n,
    output wire        O_ddr_cas_n,
    output wire        O_ddr_we_n,
    output wire        O_ddr_clk,
    output wire        O_ddr_clk_n,
    output wire        O_ddr_cke,
    output wire        O_ddr_odt,
    output wire        O_ddr_reset_n,
    output wire [3:0]  O_ddr_dqm,
    inout  wire [31:0] IO_ddr_dq,
    inout  wire [3:0]  IO_ddr_dqs,
    inout  wire [3:0]  IO_ddr_dqs_n
);

    // ------------------------------------------------------------------
    // Clocks and DDR3 IP (identical to board_top.v)
    // ------------------------------------------------------------------
    wire pll_sys_lock;
    wire ddr_pll_stop;
    wire clk100;
    wire clk400;

    Gowin_PLL_Sys u_pll_sys (
        .lock    (pll_sys_lock),
        .clkout0 (clk100),
        .clkout1 (clk400),
        .enclk1  (ddr_pll_stop),
        .clkin   (clk50)
    );

    wire         ddr_clk_out;
    wire         ddr_rst;
    wire         init_calib_complete;
    wire         cmd_ready;
    reg  [2:0]   cmd;
    reg          cmd_en;
    reg          wr_data_en;
    wire [28:0]  addr_native;
    wire         wr_data_rdy;
    wire [255:0] wr_data;
    wire [255:0] rd_data;
    wire         rd_data_valid;
    wire         rd_data_end;
    wire         sr_ack, ref_ack;
    reg          ref_req;

    reg  [7:0]   dbg_addr;
    reg  [31:0]  dbg_wdata;
    reg          dbg_we;
    wire [31:0]  dbg_rdata;
`ifdef OWN_DDR3
    own_ddr3 #(
`ifdef OWN_DDR3_RD_PNTR
        .RD_PNTR(`OWN_DDR3_RD_PNTR)
`endif
    ) u_ddr3 (
`else
    assign dbg_rdata = 32'h0;
    DDR3_Memory_Interface_Top u_ddr3 (
`endif
        .clk                 (clk50),
        .pll_stop            (ddr_pll_stop),
        .memory_clk          (clk400),
        .pll_lock            (pll_sys_lock),
        .rst_n               (pll_sys_lock),
        .clk_out             (ddr_clk_out),
        .ddr_rst             (ddr_rst),
        .init_calib_complete (init_calib_complete),
        .cmd_ready           (cmd_ready),
        .cmd                 (cmd),
        .cmd_en              (cmd_en),
        .addr                (addr_native),
        .wr_data_rdy         (wr_data_rdy),
        .wr_data             (wr_data),
        .wr_data_en          (wr_data_en),
        .wr_data_end         (wr_data_en),
        .wr_data_mask        (32'h0),
        .rd_data             (rd_data),
        .rd_data_valid       (rd_data_valid),
        .rd_data_end         (rd_data_end),
        .sr_req              (1'b0),
        .ref_req             (ref_req),
        .sr_ack              (sr_ack),
        .ref_ack             (ref_ack),
        .burst               (1'b0),
`ifdef OWN_DDR3
        .dbg_addr            (dbg_addr),
        .dbg_wdata           (dbg_wdata),
        .dbg_we              (dbg_we),
        .dbg_rdata           (dbg_rdata),
`endif
        .O_ddr_addr          (O_ddr_addr),
        .O_ddr_ba            (O_ddr_ba),
        .O_ddr_cs_n          (O_ddr_cs_n),
        .O_ddr_ras_n         (O_ddr_ras_n),
        .O_ddr_cas_n         (O_ddr_cas_n),
        .O_ddr_we_n          (O_ddr_we_n),
        .O_ddr_clk           (O_ddr_clk),
        .O_ddr_clk_n         (O_ddr_clk_n),
        .O_ddr_cke           (O_ddr_cke),
        .O_ddr_odt           (O_ddr_odt),
        .O_ddr_reset_n       (O_ddr_reset_n),
        .O_ddr_dqm           (O_ddr_dqm),
        .IO_ddr_dq           (IO_ddr_dq),
        .IO_ddr_dqs          (IO_ddr_dqs),
        .IO_ddr_dqs_n        (IO_ddr_dqs_n)
    );

    // ------------------------------------------------------------------
    // JTAG ER1 mailbox (TCK domain)
    // ------------------------------------------------------------------
    wire jtag_tck, jtag_tdi, shift_capture, update_dr, enable_er1;
    reg  [63:0] jtag_sr = 64'h0;

    (* keep *) GW_JTAG u_jtag (
        .tck_pad_i             (tck_pad_i),
        .tms_pad_i             (tms_pad_i),
        .tdi_pad_i             (tdi_pad_i),
        .tdo_pad_o             (tdo_pad_o),
        .tck_o                 (jtag_tck),
        .tdi_o                 (jtag_tdi),
        .test_logic_reset_o    (),
        .run_test_idle_er1_o   (),
        .run_test_idle_er2_o   (),
        .shift_dr_capture_dr_o (shift_capture),
        .pause_dr_o            (),
        .update_dr_o           (update_dr),
        .enable_er1_o          (enable_er1),
        .enable_er2_o          (),
        .tdo_er1_i             (jtag_sr[0]),
        .tdo_er2_i             (1'b0)
    );

    reg  [63:0] jtag_cmd = 64'h0;
    reg         jtag_req = 1'b0;
    reg         shift_capture_d = 1'b0;
    reg         update_dr_d = 1'b0;
    wire [63:0] resp;

    // GW_JTAG launches tdi_o / state outputs on the same TCK rising edge
    // the fabric would sample them on. Sampled directly at posedge, this
    // races on hardware (bit i came in as tdi[i] | tdi[i+1]). Retime them on
    // the falling edge: same values a race-free posedge sample would see.
    reg tdi_n = 1'b0, shift_capture_n = 1'b0, update_dr_n = 1'b0, enable_er1_n = 1'b0;
    always @(negedge jtag_tck) begin
        tdi_n <= jtag_tdi;
        shift_capture_n <= shift_capture;
        update_dr_n <= update_dr;
        enable_er1_n <= enable_er1;
    end

    always @(posedge jtag_tck) begin
        shift_capture_d <= shift_capture_n;
        update_dr_d <= update_dr_n;
        if (enable_er1_n && shift_capture_n) begin
            if (!shift_capture_d)
                jtag_sr <= resp;  // quasi-static, settled long before capture
            else
                jtag_sr <= {tdi_n, jtag_sr[63:1]};
        end
        if (enable_er1_n && update_dr_n && !update_dr_d) begin
            jtag_cmd <= jtag_sr;
            jtag_req <= ~jtag_req;
        end
    end

    // ------------------------------------------------------------------
    // Command executor (DDR3 UI clock domain)
    // ------------------------------------------------------------------
    // Native port handshake: cmd / addr / wr_data are registered and stable;
    // cmd_en / wr_data_en are combinational on cmd_ready (& wr_data_rdy), the
    // same valid/ready scheme as Axi4ToGowinDdrNative.scala.
    wire clk = ddr_clk_out;
    wire rst = ddr_rst;

    reg [2:0] req_sync = 3'b0;
    always @(posedge clk) req_sync <= {req_sync[1:0], jtag_req};
    wire req_pulse = req_sync[2] ^ req_sync[1];

    reg  [31:0] wbuf [0:7];
    reg  [31:0] rbuf [0:7];
    reg  [31:0] addr;
    reg  [31:0] payload;
    reg  [7:0]  seq;
    reg  [3:0]  last_op;
    reg  [31:0] cycles;
    reg  [31:0] ref_period;
    reg  [31:0] ref_cnt;
    reg  [31:0] ref_acks;
    reg         rd_timeout;

    localparam S_IDLE = 3'd0, S_WRITE = 3'd1, S_READ = 3'd2, S_READ_WAIT = 3'd3,
               S_BIST_WR = 3'd4, S_BIST_RD = 3'd5, S_BIST_DRAIN = 3'd6,
               S_DBG = 3'd7;
    reg  [2:0]  state;
    reg         autoinc;
    reg  [15:0] timer;

    reg  [31:0] bist_base;
    reg  [31:0] bist_len;      // bursts
    reg  [31:0] bist_seed;
    reg  [31:0] bist_issued;
    reg  [31:0] bist_checked;
    reg  [31:0] bist_errors;
    reg  [31:0] bist_first_bad;
    reg  [31:0] bist_bad_bits;
    reg         bist_done;
    reg         bist_has_err;
    // bist_issued == bist_len, registered (kept in step below)
    reg         bist_last;
    wire        bist_wr_last = bist_last;
    wire        bist_rd_last = bist_last;
    wire [6:0]  outstanding = bist_issued[6:0] - bist_checked[6:0];
    reg  [3:0]  bist_gap;
    reg  [5:0]  bist_maxout;
    reg  [3:0]  gap_cnt;
    wire        gap_ok = gap_cnt == 4'd0;

    reg  [31:0]  io_addr;      // byte address of the burst being offered
    reg  [255:0] io_wdata;
    assign addr_native = {io_addr[30:5], 3'b000};
    assign wr_data = io_wdata;

    wire wr_fire = (state == S_WRITE || state == S_BIST_WR && !bist_wr_last && gap_ok) &&
                   cmd_ready && wr_data_rdy;
    wire rd_fire = (state == S_READ || state == S_BIST_RD && !bist_rd_last && gap_ok &&
                    outstanding <= {1'b0, bist_maxout}) && cmd_ready;
    always @* begin
        cmd = rd_fire ? 3'd1 : 3'd0;
        cmd_en = wr_fire | rd_fire;
        wr_data_en = wr_fire;
    end

    // Pattern: every 32-bit word is unique per address and seed, and odd
    // lanes are inverted so neighbouring words toggle every DQ bit.
    function [255:0] pattern;
        input [31:0] burst_addr;
        input [31:0] seed;
        integer i;
        reg [31:0] a, w;
        begin
            for (i = 0; i < 8; i = i + 1) begin
                a = {burst_addr[31:5], i[2:0], 2'b00};
                w = a ^ seed ^ {a[15:0], ~a[31:16]};
                pattern[i*32 +: 32] = i[0] ? ~w : w;
            end
        end
    endfunction

    wire [31:0] next_addr = io_addr + 32'd32;

    // Pipelined read check: register data and expected, compare next cycle.
    reg          ck_valid;
    reg  [255:0] ck_got, ck_exp;
    reg  [31:0]  ck_addr;
    wire [31:0]  bist_ck_addr = bist_base + {bist_checked[26:0], 5'b0};
    // Stage 2: per-lane mismatch and OR of failing bits; stage 3: count.
    reg          ck2_valid;
    reg  [7:0]   ck2_lane_bad;
    reg  [31:0]  ck2_bits;
    reg  [31:0]  ck2_addr;
    reg  [255:0] ck2_got, ck2_exp;     // first failing burst, for BISTRES 8..15 / 16..23
    reg  [255:0] bad_got, bad_exp;
    wire [255:0] ck_diff = ck_got ^ ck_exp;
    wire         ck_busy = ck_valid | ck2_valid;

    integer k;
    always @(posedge clk) begin
        cycles <= cycles + 1'b1;
        ck_valid <= 1'b0;
        dbg_we <= 1'b0;
        if (rst) begin
            ck2_valid <= 1'b0;
            state <= S_IDLE;
            addr <= 32'h0;
            payload <= 32'h0;
            seq <= 8'h0;
            last_op <= 4'h0;
            rd_timeout <= 1'b0;
            bist_done <= 1'b0;
            bist_has_err <= 1'b0;
            bist_errors <= 32'h0;
            bist_checked <= 32'h0;
            bist_issued <= 32'h0;
            bist_len <= 32'h0;
            bist_last <= 1'b1;
            bist_gap <= 4'h0;
            bist_maxout <= 6'd31;
            gap_cnt <= 4'h0;
            ref_period <= 32'h0;
            ref_cnt <= 32'h0;
            ref_acks <= 32'h0;
            ref_req <= 1'b0;
            bist_first_bad <= 32'h0;
            bist_bad_bits <= 32'h0;
        end else begin
            case (state)
            S_IDLE: if (req_pulse) begin
                seq <= seq + 1'b1;
                last_op <= jtag_cmd[31:28];
                autoinc <= jtag_cmd[8];
                payload <= 32'h0;
                case (jtag_cmd[31:28])
                4'h1: addr <= jtag_cmd[63:32];
                4'h2: wbuf[jtag_cmd[2:0]] <= jtag_cmd[63:32];
                4'h3: begin
                    io_addr <= addr;
                    io_wdata <= {wbuf[7], wbuf[6], wbuf[5], wbuf[4],
                                 wbuf[3], wbuf[2], wbuf[1], wbuf[0]};
                    state <= S_WRITE;
                end
                4'h4: begin
                    io_addr <= addr;
                    rd_timeout <= 1'b0;
                    state <= S_READ;
                end
                4'h5: payload <= rbuf[jtag_cmd[2:0]];
                4'h6: begin
                    bist_base <= {addr[31:5], 5'b0};
                    bist_len <= jtag_cmd[63:32];
                    bist_seed <= {jtag_cmd[15:0], jtag_cmd[15:0]};
                    bist_gap <= jtag_cmd[19:16];
                    bist_maxout <= jtag_cmd[25:20];
                    io_addr <= {addr[31:5], 5'b0};
                    io_wdata <= pattern({addr[31:5], 5'b0}, {jtag_cmd[15:0], jtag_cmd[15:0]});
                    bist_issued <= 32'h0;
                    bist_last <= jtag_cmd[63:32] == 32'h0;
                    bist_checked <= 32'h0;
                    bist_errors <= 32'h0;
                    bist_first_bad <= 32'hFFFFFFFF;
                    bist_bad_bits <= 32'h0;
                    bist_done <= 1'b0;
                    bist_has_err <= 1'b0;
                    rd_timeout <= 1'b0;
                    state <= S_BIST_WR;
                end
                4'h7: if (jtag_cmd[4]) payload <= jtag_cmd[3] ? bad_exp[jtag_cmd[2:0]*32 +: 32]
                                                             : bad_got[jtag_cmd[2:0]*32 +: 32];
                      else case (jtag_cmd[2:0])
                    3'd0: payload <= bist_errors;
                    3'd1: payload <= bist_first_bad;
                    3'd2: payload <= bist_bad_bits;
                    3'd3: payload <= bist_checked;
                    3'd4: payload <= bist_issued;
                    3'd5: payload <= {29'h0, state};
                    default: payload <= ref_acks;
                endcase
                4'h8: payload <= addr;
                4'h9: payload <= jtag_cmd[63:32];
                4'hA: payload <= cycles;
                4'hB: begin ref_period <= jtag_cmd[63:32]; payload <= ref_acks; end
                4'hC: begin dbg_addr <= jtag_cmd[7:0]; state <= S_DBG; timer <= 16'h0; end
                4'hD: begin
                    dbg_addr <= jtag_cmd[7:0];
                    dbg_wdata <= jtag_cmd[63:32];
                    dbg_we <= 1'b1;
                end
                default: ;
                endcase
            end
            S_DBG: begin
                // dbg_rdata is registered: wait for it to follow dbg_addr
                timer <= timer + 1'b1;
                if (timer == 16'd3) begin
                    payload <= dbg_rdata;
                    state <= S_IDLE;
                end
            end
            S_WRITE: if (wr_fire) begin
                if (autoinc) addr <= addr + 32'd32;
                state <= S_IDLE;
            end
            S_READ: if (rd_fire) begin
                timer <= 16'h0;
                state <= S_READ_WAIT;
            end
            S_READ_WAIT: begin
                timer <= timer + 1'b1;
                if (rd_data_valid) begin
                    for (k = 0; k < 8; k = k + 1)
                        rbuf[k] <= rd_data[k*32 +: 32];
                    if (autoinc) addr <= addr + 32'd32;
                    state <= S_IDLE;
                end else if (&timer) begin
                    rd_timeout <= 1'b1;
                    state <= S_IDLE;
                end
            end
            S_BIST_WR: begin
                if (bist_wr_last) begin
                    timer <= 16'h0;
                    bist_issued <= 32'h0;
                    bist_last <= bist_len == 32'h0;
                    io_addr <= bist_base;
                    state <= S_BIST_RD;
                end else if (wr_fire) begin
                    bist_issued <= bist_issued + 1'b1;
                    bist_last <= bist_issued + 1'b1 == bist_len;
                    io_addr <= next_addr;
                    io_wdata <= pattern(next_addr, bist_seed);
                end
            end
            S_BIST_RD: begin
                timer <= timer + 1'b1;
                if (&timer) begin
                    // Reads went missing: stop instead of hanging busy.
                    rd_timeout <= 1'b1;
                    bist_done <= 1'b1;
                    bist_has_err <= 1'b1;
                    state <= S_IDLE;
                end else if (bist_rd_last) begin
                    timer <= 16'h0;
                    state <= S_BIST_DRAIN;
                end else if (rd_fire) begin
                    bist_issued <= bist_issued + 1'b1;
                    bist_last <= bist_issued + 1'b1 == bist_len;
                    io_addr <= next_addr;
                end
            end
            S_BIST_DRAIN: begin
                timer <= timer + 1'b1;
                if (bist_checked == bist_len && !ck_busy) begin
                    bist_done <= 1'b1;
                    state <= S_IDLE;
                end else if (&timer) begin
                    rd_timeout <= 1'b1;
                    bist_done <= 1'b1;
                    bist_has_err <= 1'b1;
                    state <= S_IDLE;
                end
            end
            default: state <= S_IDLE;
            endcase

            ref_req <= 1'b0;
            if (ref_period != 0) begin
                if (ref_cnt >= ref_period) begin
                    ref_cnt <= 32'h0;
                    ref_req <= 1'b1;
                end else
                    ref_cnt <= ref_cnt + 1'b1;
            end
            if (ref_ack) ref_acks <= ref_acks + 1'b1;

            if (cmd_en) gap_cnt <= bist_gap;
            else if (!gap_ok) gap_cnt <= gap_cnt - 1'b1;

            if (rd_data_valid && (state == S_BIST_RD || state == S_BIST_DRAIN)) begin
                ck_valid <= 1'b1;
                ck_got <= rd_data;
                ck_exp <= pattern(bist_ck_addr, bist_seed);
                ck_addr <= bist_ck_addr;
                bist_checked <= bist_checked + 1'b1;
                timer <= 16'h0;
            end
            ck2_valid <= ck_valid;
            ck2_addr <= ck_addr;
            ck2_got <= ck_got;
            ck2_exp <= ck_exp;
            for (k = 0; k < 8; k = k + 1)
                ck2_lane_bad[k] <= |ck_diff[k*32 +: 32];
            ck2_bits <= ck_diff[31:0]    | ck_diff[63:32]   | ck_diff[95:64]   |
                        ck_diff[127:96]  | ck_diff[159:128] | ck_diff[191:160] |
                        ck_diff[223:192] | ck_diff[255:224];
            if (ck2_valid && |ck2_lane_bad) begin
                bist_errors <= bist_errors + 1'b1;
                bist_has_err <= 1'b1;
                bist_bad_bits <= bist_bad_bits | ck2_bits;
                if (!bist_has_err) begin
                    bist_first_bad <= ck2_addr;
                    bad_got <= ck2_got;
                    bad_exp <= ck2_exp;
                end
            end
        end
    end

    wire busy = state != S_IDLE;
    wire [31:0] status = {8'hD3, seq, init_calib_complete, pll_sys_lock, ddr_rst,
                          busy, bist_done, bist_has_err, rd_timeout, cmd_ready,
                          wr_data_rdy, 3'b000, last_op};
    assign resp = {payload, status};

    // ------------------------------------------------------------------
    // LEDs
    // ------------------------------------------------------------------
    reg [26:0] hb;
    always @(posedge clk) hb <= hb + 1'b1;
    wire bist_led = bist_done && (bist_has_err ? hb[22] : 1'b1);
    assign led_n = ~{bist_led, init_calib_complete, pll_sys_lock, hb[26]};

endmodule
