// DDR3 controller for own_ddr3_phy, Gowin native UI on the user side.
//
// Everything is counted in frames (one pclk = 4 tCK at 1:4). Closed page:
// every access is ACT (slot 0) then, one frame later, RD (slot 2) or WR
// (slot 3) with auto-precharge. Up to one access starts per frame; bank,
// turnaround and refresh timing are small down-counters. The native address
// is split so consecutive 32-byte bursts land in different banks
// (bank = addr[5:3]), which lets a sequential stream start an access every
// frame despite the closed-page policy.
//
// DDR3-800, CL 6, CWL 5, BL8. With the slots above:
//   read  ACT@t: RD at tCK 4t+6, next ACT same bank >= t+6 (tRC 22 tCK),
//                next write ACT >= t+2 (RD->WR 7 tCK)
//   write ACT@t: WR at tCK 4t+7, data at 4t+12, next ACT same bank >= t+7
//                (WR+CWL+4+tWR+tRP = 28 tCK), next read ACT >= t+4 (tWTR)
//   tRRD / tFAW hold trivially with one ACT per frame.
//
// Read calibration (after init, and on request) finds per byte lane the
// DQS gate position, the IDES8_MEM CALIB rotation and the read latency in
// frames, by reading back two training bursts at word addresses 0 and 8
// (both overwritten). See the CAL_* states.
module own_ddr3_ctrl #(
    parameter ROW_WIDTH = 15,
    parameter BANK_WIDTH = 3,
    parameter COL_WIDTH = 10,
    parameter ADDR_WIDTH = 29,
    parameter DQ_WIDTH = 32,
    parameter LANES = DQ_WIDTH / 8,
    parameter WDQ_DEPTH = 2,        // write data FIFO depth (2 or 4)
    parameter SIM = 0,              // shorten init waits
    parameter DBG_MAP = 1,          // keep the per-lane gate pass maps
    parameter [15:0] MR0_INIT = 16'h1520, // PPD fast, WR 6, DLL reset, CL 6, BL8
    parameter [15:0] MR1_INIT = 16'h0004, // DLL on, RZQ/6 drive, RTT_NOM RZQ/4
    parameter [15:0] MR2_INIT = 16'h0000, // CWL 5, RTT_WR off
    parameter [15:0] MR3_INIT = 16'h0000,
    parameter [7:0]  WSTEP_INIT = 8'd0,
    parameter [3:0]  GATE_BASE_INIT = 4'd1,   // frames after RD for gate pos 0
    parameter [7:0]  GATE_LEN_INIT = 8'h0F,   // READ window, 1 bit per tCK
    // Pulse DQS HOLD with every RD (calibration too). The board's DQS FIFO
    // only reads cleanly that way; the GW2A DQS sim model disagrees, so the
    // testbench turns it off.
    parameter        HOLD_RD_INIT = 1,
    // RD in command slot 3 (else 2). On the board, with slot 2 lane 3's
    // read burst sat on the IDES8_MEM frame cut and now and then came out
    // a frame late; slot 3 puts all lanes at latency 8 (1 GiB BIST clean
    // at gaps 0..15).
    parameter        RD_SLOT3_INIT = 1,
    parameter        WR_LAT = 2               // frames from WR to its DQ frame
) (
    input  wire                        pclk,
    input  wire                        rst,

    // Native UI
    output wire                        cmd_ready,
    input  wire [2:0]                  cmd,
    input  wire                        cmd_en,
    input  wire [ADDR_WIDTH-1:0]       addr,
    output wire                        wr_data_rdy,
    input  wire [8*DQ_WIDTH-1:0]       wr_data,
    input  wire                        wr_data_en,
    input  wire                        wr_data_end,
    input  wire [DQ_WIDTH-1:0]         wr_data_mask,
    output reg  [8*DQ_WIDTH-1:0]       rd_data,
    output reg                         rd_data_valid,
    output wire                        rd_data_end,
    output wire                        init_calib_complete,
    output wire                        ref_ack,

    // PHY (all registered here, one frame each)
    output reg  [3:0]                  c_cs_n,
    output reg  [3:0]                  c_ras_n,
    output reg  [3:0]                  c_cas_n,
    output reg  [3:0]                  c_we_n,
    output reg  [4*BANK_WIDTH-1:0]     c_ba,
    output reg  [4*ROW_WIDTH-1:0]      c_a,
    output reg                         p_cke,
    output reg                         p_odt,
    output reg                         p_reset_n,
    output reg  [8*DQ_WIDTH-1:0]       p_wr_dq,
    output reg  [8*LANES-1:0]          p_wr_dm,
    output reg  [3:0]                  p_wr_dq_oen,
    output reg  [7:0]                  p_wr_dqs,
    output reg  [3:0]                  p_wr_dqs_oen,
    output wire [8*LANES-1:0]          p_wstep,
    output reg  [8*LANES-1:0]          p_wdq_dly,
    output reg  [4*LANES-1:0]          p_rd_gate,
    output reg  [3*LANES-1:0]          p_rclksel,
    output reg  [LANES-1:0]            p_rd_calib,
    output reg                         p_dqs_hold,
    input  wire [8*DQ_WIDTH-1:0]       rd_frame,
    // DQS DLLSTEP override (sets the DQ / DQS phase), DDRDLL readback
    output reg                         p_ck_inv,
    output reg                         p_dll_ovr,
    output reg  [7:0]                  p_dll_val,
    input  wire [7:0]                  dll_step,
    input  wire                        dll_lock,

    // Debug registers (pclk domain)
    input  wire [7:0]                  dbg_addr,
    input  wire [31:0]                 dbg_wdata,
    input  wire                        dbg_we,
    output reg  [31:0]                 dbg_rdata
);

    localparam LW = 8 * 8;          // bits per lane per frame
    localparam integer US = SIM ? 1 : 100;   // frames per microsecond

    // DDR3 commands {ras_n, cas_n, we_n}
    localparam [2:0] CMD_MRS = 3'b000, CMD_REF = 3'b001, CMD_PRE = 3'b010,
                     CMD_ACT = 3'b011, CMD_WR  = 3'b100, CMD_RD  = 3'b101,
                     CMD_ZQ  = 3'b110, CMD_NOP = 3'b111;

    localparam integer REFI = 760;  // frames (tREFI 7.8 us)
    localparam integer RFC  = 27;   // frames (tRFC 260 ns)

    // ------------------------------------------------------------------
    // Run-time configuration (debug registers)
    // ------------------------------------------------------------------
    reg [15:0] mr0, mr1, mr2, mr3;
    reg [7:0]  wstep_r [0:LANES-1];
    reg [3:0]  gate_base;
    reg [7:0]  gate_len;        // READ window, one bit per tCK
    reg        opt_hold_rd;     // pulse DQS HOLD on every read
    reg        opt_manual;      // skip the automatic calibration
    reg        req_recal, req_reinit;
    reg        opt_wr_shift;    // send DQ / DM one beat later than DQS
    reg        opt_dq_hold;     // debug: drive DQ whenever no read is near
    reg        opt_rd_slot3;    // RD in command slot 3 instead of 2
    wire [1:0] rd_slot = opt_rd_slot3 ? 2'd3 : 2'd2;
    // previous frame of the undelayed write signals (for wr_dly / shift)
    reg [8*DQ_WIDTH-1:0] a_dq, prev_dq;
    reg [8*LANES-1:0]    a_dm, prev_dm;
    reg [7:0]            a_dqs, prev_dqs;
    reg [3:0]            a_drv, a_dq_drv;
    reg [3:0]            prev_dq_drv, prev_dqs_drv;

    genvar gl;
    generate
        for (gl = 0; gl < LANES; gl = gl + 1) begin : g_wstep
            assign p_wstep[8*gl +: 8] = wstep_r[gl];
        end
    endgenerate

    // ------------------------------------------------------------------
    // Native UI: command FIFO (2) and write data FIFO (WDQ_DEPTH)
    // ------------------------------------------------------------------
    reg                   cq_wr [0:1];
    reg [ADDR_WIDTH-1:0]  cq_addr [0:1];
    reg                   cq_head, cq_tail;
    reg [1:0]             cq_count;

    localparam WDQ_AW = (WDQ_DEPTH > 2) ? 2 : 1;
    reg [8*DQ_WIDTH-1:0]  wq_data [0:WDQ_DEPTH-1];
    reg [DQ_WIDTH-1:0]    wq_mask [0:WDQ_DEPTH-1];
    reg [WDQ_AW-1:0]      wq_head, wq_tail;
    reg [WDQ_AW:0]        wq_count;
    reg [WDQ_AW:0]        wq_claimed;   // writes started, data not yet sent

    reg cal_done;
    // Both readies drop together, so a master that offers a write command
    // and its data in the same cycle (Axi4ToGowinDdrNative, the BIST) can't
    // get one accepted without the other.
    // Registered (computed from the next FIFO counts) to keep it off the
    // user's combinational paths.
    reg  ui_room;
    assign cmd_ready = ui_room;
    assign wr_data_rdy = ui_room;
    assign init_calib_complete = cal_done;
    assign rd_data_end = rd_data_valid;

    wire cq_push = cmd_en && cmd_ready;
    wire wq_push = wr_data_en && wr_data_rdy;
    wire [1:0]      cq_count_n;
    wire [WDQ_AW:0] wq_count_n;

    // ------------------------------------------------------------------
    // Request into the scheduler: native head or calibration
    // ------------------------------------------------------------------
    reg                   cal_active;
    reg                   cal_req;
    reg                   cal_req_wr;
    reg                   cal_req_b;    // burst B (bank 1) instead of A (bank 0)

    wire [ADDR_WIDTH-1:0] hq_addr = cq_addr[cq_head];
    wire                  hq_wr = cq_wr[cq_head];
    wire                  hq_valid = cq_count != 2'd0 &&
                                     (!hq_wr || wq_count > wq_claimed);

    wire                  rq_valid = cal_active ? cal_req : (cal_done && hq_valid);
    wire                  rq_wr    = cal_active ? cal_req_wr : hq_wr;
    wire                  rq_cal   = cal_active;
    wire [BANK_WIDTH-1:0] rq_bank  = cal_active ? {{(BANK_WIDTH-1){1'b0}}, cal_req_b}
                                                : hq_addr[5:3];
    wire [ROW_WIDTH-1:0]  rq_row   = cal_active ? {ROW_WIDTH{1'b0}}
                                                : hq_addr[13 +: ROW_WIDTH];
    wire [COL_WIDTH-1:0]  rq_col   = cal_active ? {COL_WIDTH{1'b0}}
                                                : {hq_addr[12:6], 3'b000};

    // ------------------------------------------------------------------
    // Timing state
    // ------------------------------------------------------------------
    reg [2:0]  bank_cnt [0:7];
    reg [2:0]  rtw_cnt, wtr_cnt;
    reg [4:0]  rfc_cnt;
    reg [9:0]  refi_cnt;
    reg [3:0]  ref_owed;
    reg        ref_ack_r;
    assign ref_ack = ref_ack_r;

    // Registered "counter is zero" flags, set one cycle ahead, so the
    // issue decision doesn't go through the counters.
    reg [7:0]  bank_idle;
    reg        rtw_idle, wtr_idle, rfc_idle;
    wire       all_banks_idle = &bank_idle;

    // Init sequencer state
    localparam [3:0] I_RST = 4'd0, I_CKE = 4'd1, I_XPR = 4'd2, I_MR2 = 4'd3,
                     I_MR3 = 4'd4, I_MR1 = 4'd5, I_MR0 = 4'd6, I_ZQ = 4'd7,
                     I_ZQW = 4'd8, I_RUN = 4'd9;
    reg [3:0]  istate;
    reg [16:0] itimer;
    wire       running = istate == I_RUN;

    wire ref_due = ref_owed != 4'd0;
    wire can_issue = running && rq_valid && rfc_idle && !ref_due &&
                     bank_idle[rq_bank] && (rq_wr ? rtw_idle : wtr_idle);
    wire do_ref = running && ref_due && rfc_idle && all_banks_idle;

    // Stage 1: the RD/WR half of the access started last frame
    reg                   s1_valid, s1_wr, s1_cal, s1_b;
    reg [BANK_WIDTH-1:0]  s1_bank;
    reg [COL_WIDTH-1:0]   s1_col;

    // Shift registers, bit i = command was in the PHY regs i frames ago
    localparam RSR = 32;
    reg [RSR-1:0] rsr;          // reads (all)
    reg [RSR-1:0] rsr_user;     // reads returning to the user
    reg [RSR-1:0] rsr_a;        // calibration reads of burst A
    reg [4:0]     wsr;          // writes
    reg [4:0]     wsr_cal;      // calibration writes
    reg [4:0]     wsr_b;

    wire rd_now = s1_valid && !s1_wr;
    wire wr_now = s1_valid && s1_wr;
    wire [RSR-1:0] rsr_n      = {rsr[RSR-2:0], rd_now};
    wire [RSR-1:0] rsr_user_n = {rsr_user[RSR-2:0], rd_now && !s1_cal};
    wire [RSR-1:0] rsr_a_n    = {rsr_a[RSR-2:0], rd_now && s1_cal};
    wire [4:0]     wsr_n      = {wsr[3:0], wr_now};
    wire [4:0]     wsr_cal_n  = {wsr_cal[3:0], wr_now && s1_cal};
    wire [4:0]     wsr_b_n    = {wsr_b[3:0], wr_now && s1_b};

    // Write burst timing, run-time: wr_lat frames after the WR frame, then
    // wr_dly more tCK, and DQ / DM a further beat with opt_wr_shift.
    reg [1:0] wr_lat;
    reg [1:0] wr_dly;
    // The undelayed burst signals are built one frame ahead (stage A, from
    // wsr_n[wr_lat - 1]) and delayed / aligned in stage B, for timing.
    wire [1:0] wl_a = wr_lat - 1'b1;
    wire wdata_now = wsr_n[wl_a];
    wire wdata_pop = wdata_now && !wsr_cal_n[wl_a];
    assign cq_count_n = cq_count + cq_push - (can_issue && !do_ref && !rq_cal);
    assign wq_count_n = wq_count + wq_push - wdata_pop;

    // ------------------------------------------------------------------
    // Training pattern: pin p of every lane carries tp_pin(p%8) over the
    // 8 beats (bit b = beat b). B is the complement of A.
    // ------------------------------------------------------------------
    function [7:0] tp_pin;
        input [2:0] p;
        case (p)
            // No rotation or reversal of A or B equals another one, and
            // the 8 beat bytes of A are distinct and differ from B's.
            3'd0: tp_pin = 8'hA0; 3'd1: tp_pin = 8'h42;
            3'd2: tp_pin = 8'hBE; 3'd3: tp_pin = 8'h5C;
            3'd4: tp_pin = 8'hCC; 3'd5: tp_pin = 8'hB1;
            3'd6: tp_pin = 8'hF2; default: tp_pin = 8'hD8;
        endcase
    endfunction

    function [8*DQ_WIDTH-1:0] tp_line;
        input dummy;
        integer b, p;
        begin
            for (b = 0; b < 8; b = b + 1)
                for (p = 0; p < DQ_WIDTH; p = p + 1)
                    tp_line[b*DQ_WIDTH + p] = tp_pin(p[2:0]) >> b;
        end
    endfunction
    wire [8*DQ_WIDTH-1:0] tp_a = tp_line(1'b0);

    // Lane view of a frame: [b*8 + i] = beat b, lane pin i
    function [LW-1:0] lane_bits;
        input [8*DQ_WIDTH-1:0] f;
        input integer lane;
        integer b, i;
        begin
            for (b = 0; b < 8; b = b + 1)
                for (i = 0; i < 8; i = i + 1)
                    lane_bits[b*8 + i] = f[b*DQ_WIDTH + 8*lane + i];
        end
    endfunction

    // ------------------------------------------------------------------
    // Calibration state
    // ------------------------------------------------------------------
    localparam [3:0] C_IDLE = 4'd0, C_WRA = 4'd1, C_WRB = 4'd2, C_SETTLE = 4'd3,
                     C_RDB = 4'd4, C_RDA = 4'd5, C_WAIT = 4'd6, C_EVAL = 4'd7,
                     C_PULSE = 4'd8, C_PGAP = 4'd9, C_NEXTG = 4'd10,
                     C_P2 = 4'd11, C_FINISH = 4'd12, C_DONE = 4'd13,
                     C_FAIL = 4'd14, C_HOLD = 4'd15;
    localparam [4:0] C_SCAN = 5'd16, C_SCAN2 = 5'd17;
    localparam WIN = 24;

    reg [4:0]  cstate;
    reg [6:0]  sg;
    reg [LANES-1:0] p2walk;
    reg [6:0]  sc_start [0:LANES-1];
    reg [7:0]  sc_len   [0:LANES-1];
    reg [6:0]  sc_bstart [0:LANES-1];
    reg [7:0]  sc_blen   [0:LANES-1];
    reg        cphase2;
    reg [6:0]  cg;               // phase 1 gate position
    reg [3:0]  ck;               // rotation trial (0..8)
    reg [5:0]  ctimer;
    reg        hold_sent;
    reg        tsel;            // calibration trial reads burst B
    reg        dqs_flush;
    reg [4:0]  afcnt;            // frames since read A
    reg        a_seen;
    reg [6:0]  lg   [0:LANES-1]; // gate position in use per lane
    reg [3:0]  llat [0:LANES-1]; // read latency in frames
    reg [2:0]  lrot [0:LANES-1]; // CALIB pulses applied (mod 8)
    reg [LANES-1:0] lfound;      // this trial matched
    reg [LANES-1:0] lprevb;      // last frame held burst B
    reg [LANES-1:0] lprevb2;     // the frame before that did
    reg [3:0]  lfl  [0:LANES-1]; // frame it matched at
    reg [LANES-1:0] lpass_g;     // any rotation matched at this gate pos
    reg [LANES-1:0] ldone;       // phase 2 locked
    reg [3:0]  rowpass [0:LANES-1]; // RCLKSEL rows with a passing offset
    reg [127:0] pmap [0:LANES-1];
    reg [3:0]  lat_min, lat_max;
    reg [LANES-1:0] lane_late;   // this lane's latency is lat_max
    reg [3:0]  cal_runs;
    reg        cal_fail;

    // ------------------------------------------------------------------
    // Gate generation (for the next frame) per lane
    // ------------------------------------------------------------------
    // Gate position g, in quarter tCK: tCK offset o = g >> 2 from the RD
    // frame (+ gate_base frames), the quarter g[1:0] through RCLKSEL.
    // RCLKSEL delays the gate linearly in quarter tCK (0..7 = 0..1.75 tCK,
    // measured on the DQS model), so 0..3 plus the tCK offset covers all.
    function [2:0] rsel_of;
        input [1:0] q;
        rsel_of = {1'b0, q};
    endfunction

    reg [4*LANES-1:0] gate_n;
    reg [3*LANES-1:0] rsel_n;
    integer li;
    reg [4:0] go;
    reg [5:0] gf;
    reg [7:0] gw;
    always @* begin
        for (li = 0; li < LANES; li = li + 1) begin
            go = lg[li][6:2];
            gf = gate_base + go[4:2];
            gw = gate_len << go[1:0];
            gate_n[4*li +: 4] = (rsr_n[gf] ? gw[3:0] : 4'b0) |
                                (rsr_n[gf + 1] ? gw[7:4] : 4'b0);
            rsel_n[3*li +: 3] = rsel_of(lg[li][1:0]);
        end
    end

    // Lane data alignment: lanes with the shorter latency come from a
    // one-frame delayed copy.
    reg [8*DQ_WIDTH-1:0] rd_frame_d1;
    reg [8*DQ_WIDTH-1:0] rd_aligned;
    integer ab, ai;
    always @* begin
        for (ab = 0; ab < 8; ab = ab + 1)
            for (ai = 0; ai < LANES; ai = ai + 1)
                rd_aligned[ab*DQ_WIDTH + 8*ai +: 8] = lane_late[ai] ?
                    rd_frame[ab*DQ_WIDTH + 8*ai +: 8] :
                    rd_frame_d1[ab*DQ_WIDTH + 8*ai +: 8];
    end

    // ------------------------------------------------------------------
    // Main sequential block
    // ------------------------------------------------------------------
    integer i;
    reg [LW-1:0] lane_got, lane_exp;
    reg [3:0] tmin, tmax;
    reg [8*DQ_WIDTH-1:0] wbeats;
    reg [8*LANES-1:0]    wmask;
    reg [7:0]            rdqs;
    reg [3:0]            rdq_drv, rdqs_drv;
    reg [3:0]            kd, kq;
    integer              b, bq, bd;
    reg                  dd;
    reg       p2more;
    reg [1:0] qsel, qbest, qscore;
    integer   qi;
    always @(posedge pclk) begin
        rd_frame_d1 <= rd_frame;
        rd_data <= rd_aligned;
        rd_data_valid <= rsr_user[lat_max] && cal_done;
        ref_ack_r <= 1'b0;

        // ---------------- defaults for the next frame ----------------
        c_cs_n  <= 4'b1111;
        c_ras_n <= 4'b1111;
        c_cas_n <= 4'b1111;
        c_we_n  <= 4'b1111;
        c_ba    <= {4*BANK_WIDTH{1'b0}};
        c_a     <= {4*ROW_WIDTH{1'b0}};
        p_rd_calib <= {LANES{1'b0}};
        p_dqs_hold <= 1'b0;

        if (rst) begin
            istate <= I_RST;
            itimer <= SIM ? 17'd20 : 17'd20000;      // RESET# low 200 us
            p_reset_n <= 1'b0;
            p_cke <= 1'b0;
            p_odt <= 1'b0;
            mr0 <= MR0_INIT; mr1 <= MR1_INIT; mr2 <= MR2_INIT; mr3 <= MR3_INIT;
            for (i = 0; i < LANES; i = i + 1) begin
                wstep_r[i] <= WSTEP_INIT;
                lg[i] <= 7'd0;
                llat[i] <= 4'd0;
                lrot[i] <= 3'd0;
            end
            gate_base <= GATE_BASE_INIT;
            p_wdq_dly <= {8*LANES{1'b0}};
            gate_len <= GATE_LEN_INIT;
            opt_hold_rd <= HOLD_RD_INIT;
            opt_manual <= 1'b0;
            opt_wr_shift <= 1'b0;
            opt_dq_hold <= 1'b0;
            opt_rd_slot3 <= RD_SLOT3_INIT;
            p_ck_inv <= 1'b0;
            p_dll_ovr <= 1'b0;
            p_dll_val <= 8'd50;
            prev_dq <= 0;
            a_dq <= 0; a_dm <= {8*LANES{1'b1}}; a_dqs <= 8'h00; a_drv <= 4'h0; a_dq_drv <= 4'h0;
            prev_dm <= {8*LANES{1'b1}};
            prev_dqs <= 8'h00;
            prev_dq_drv <= 4'h0;
            prev_dqs_drv <= 4'h0;
            wr_lat <= WR_LAT;
            wr_dly <= 2'd0;
            req_recal <= 1'b0;
            req_reinit <= 1'b0;
            cq_head <= 1'b0; cq_tail <= 1'b0; cq_count <= 2'd0;
            wq_head <= 0; wq_tail <= 0; wq_count <= 0; wq_claimed <= 0;
            for (i = 0; i < 8; i = i + 1) bank_cnt[i] <= 3'd0;
            rtw_cnt <= 3'd0; wtr_cnt <= 3'd0; rfc_cnt <= 5'd0;
            bank_idle <= 8'hFF; rtw_idle <= 1'b1; wtr_idle <= 1'b1; rfc_idle <= 1'b1;
            ui_room <= 1'b0;
            refi_cnt <= 10'd0; ref_owed <= 4'd0;
            s1_valid <= 1'b0;
            rsr <= 0; rsr_user <= 0; rsr_a <= 0;
            wsr <= 0; wsr_cal <= 0; wsr_b <= 0;
            p_wr_dq_oen <= 4'b1111;
            p_wr_dqs_oen <= 4'b1111;
            p_wr_dqs <= 8'h00;
            p_wr_dm <= {8*LANES{1'b1}};
            p_rd_gate <= 0;
            p_rclksel <= 0;
            cal_active <= 1'b0;
            cal_req <= 1'b0;
            cal_done <= 1'b0;
            cal_fail <= 1'b0;
            cal_runs <= 4'd0;
            cstate <= C_IDLE;
            hold_sent <= 1'b0;
            tsel <= 1'b0;
            dqs_flush <= 1'b0;
            lat_min <= 4'd0; lat_max <= 4'd0;
            lane_late <= {LANES{1'b1}};
            rd_data_valid <= 1'b0;
        end else begin
            // ---------------- debug register writes ----------------
            if (dbg_we) begin
                case (dbg_addr)
                    8'h01: for (i = 0; i < LANES; i = i + 1)
                               wstep_r[i] <= dbg_wdata[8*i +: 8];
                    8'h02: begin mr0 <= dbg_wdata[15:0]; mr1 <= dbg_wdata[31:16]; end
                    8'h03: begin mr2 <= dbg_wdata[15:0]; mr3 <= dbg_wdata[31:16]; end
                    8'h04: begin
                        req_recal <= dbg_wdata[0];
                        req_reinit <= dbg_wdata[1];
                        opt_hold_rd <= dbg_wdata[2];
                        opt_manual <= dbg_wdata[3];
                        if (dbg_wdata[4]) p_dqs_hold <= 1'b1;
                    end
                    8'h05: begin gate_base <= dbg_wdata[3:0]; gate_len <= dbg_wdata[15:8]; end
                    8'h06: begin
                        p_dll_val <= dbg_wdata[7:0];
                        p_dll_ovr <= dbg_wdata[8];
                        opt_wr_shift <= dbg_wdata[16];
                        p_ck_inv <= dbg_wdata[20];
                        opt_dq_hold <= dbg_wdata[21];
                        opt_rd_slot3 <= dbg_wdata[22];
                        if (dbg_wdata[24 +: 2] >= 2'd2) wr_lat <= dbg_wdata[24 +: 2];
                        wr_dly <= dbg_wdata[28 +: 2];
                    end
                    // manual per-lane setup: [6:0] gate, [11:8] latency,
                    // [16] pulse CALIB once, [20] latest lane
                    8'h08, 8'h09, 8'h0A, 8'h0B: begin
                        lg[dbg_addr[1:0]] <= dbg_wdata[6:0];
                        llat[dbg_addr[1:0]] <= dbg_wdata[11:8];
                        if (dbg_wdata[16]) begin
                            p_rd_calib[dbg_addr[1:0]] <= 1'b1;
                            lrot[dbg_addr[1:0]] <= lrot[dbg_addr[1:0]] + 1'b1;
                        end
                        lane_late[dbg_addr[1:0]] <= dbg_wdata[20];
                    end
                    8'h0D: p_wdq_dly <= dbg_wdata[8*LANES-1:0];
                    8'h0C: begin
                        lat_max <= dbg_wdata[3:0];
                        cal_done <= dbg_wdata[8];
                    end
                    default: ;
                endcase
            end

            // ---------------- native FIFOs ----------------
            if (cq_push) begin
                cq_wr[cq_tail] <= cmd[2:0] == 3'd0;
                cq_addr[cq_tail] <= addr;
                cq_tail <= ~cq_tail;
            end
            if (wq_push) begin
                wq_data[wq_tail] <= wr_data;
                wq_mask[wq_tail] <= wr_data_mask;
                wq_tail <= wq_tail + 1'b1;
            end

            // ---------------- counters ----------------
            for (i = 0; i < 8; i = i + 1) begin
                if (bank_cnt[i] != 3'd0) bank_cnt[i] <= bank_cnt[i] - 1'b1;
                bank_idle[i] <= bank_cnt[i] <= 3'd1 &&
                                !(can_issue && !do_ref && rq_bank == i);
            end
            if (rtw_cnt != 3'd0) rtw_cnt <= rtw_cnt - 1'b1;
            if (wtr_cnt != 3'd0) wtr_cnt <= wtr_cnt - 1'b1;
            if (rfc_cnt != 5'd0) rfc_cnt <= rfc_cnt - 1'b1;
            rtw_idle <= rtw_cnt <= 3'd1 && !(can_issue && !do_ref && !rq_wr);
            wtr_idle <= wtr_cnt <= 3'd1 && !(can_issue && !do_ref && rq_wr);
            rfc_idle <= rfc_cnt <= 5'd1 && !do_ref;

            // Refresh: owe one every tREFI once running
            if (running) begin
                if (refi_cnt == REFI - 1) begin
                    refi_cnt <= 10'd0;
                    if (!do_ref) ref_owed <= ref_owed + 1'b1;
                end else begin
                    refi_cnt <= refi_cnt + 1'b1;
                    if (do_ref) ref_owed <= ref_owed - 1'b1;
                end
            end

            // ---------------- init sequence ----------------
            if (itimer != 17'd0) itimer <= itimer - 1'b1;
            case (istate)
                I_RST: if (itimer == 0) begin
                    p_reset_n <= 1'b1;
                    itimer <= SIM ? 17'd50 : 17'd50000;  // 500 us to CKE
                    istate <= I_CKE;
                end
                I_CKE: if (itimer == 0) begin
                    p_cke <= 1'b1;
                    itimer <= 17'd40;                    // tXPR
                    istate <= I_XPR;
                end
                I_XPR, I_MR2, I_MR3, I_MR1, I_MR0: if (itimer == 0) begin
                    c_cs_n[0] <= 1'b0;
                    {c_ras_n[0], c_cas_n[0], c_we_n[0]} <= CMD_MRS;
                    case (istate)
                        I_XPR: begin c_ba[2:0] <= 3'd2; c_a[ROW_WIDTH-1:0] <= mr2; end
                        I_MR2: begin c_ba[2:0] <= 3'd3; c_a[ROW_WIDTH-1:0] <= mr3; end
                        I_MR3: begin c_ba[2:0] <= 3'd1; c_a[ROW_WIDTH-1:0] <= mr1; end
                        default: begin c_ba[2:0] <= 3'd0; c_a[ROW_WIDTH-1:0] <= mr0; end
                    endcase
                    itimer <= 17'd4;                     // tMRD / tMOD
                    istate <= istate + 1'b1;
                end
                I_ZQ: if (itimer == 0) begin
                    c_cs_n[0] <= 1'b0;
                    {c_ras_n[0], c_cas_n[0], c_we_n[0]} <= CMD_ZQ;
                    c_a[10] <= 1'b1;                     // ZQCL
                    itimer <= 17'd300;                   // tZQinit, tDLLK
                    istate <= I_ZQW;
                end
                I_ZQW: if (itimer == 0) begin
                    istate <= I_RUN;
                    p_odt <= 1'b1;
                    p_dqs_hold <= 1'b1;
                    cstate <= opt_manual ? C_DONE : C_WRA;
                    cal_active <= !opt_manual;
                    cal_done <= opt_manual;
                end
                default: ;
            endcase

            if (req_reinit && running && all_banks_idle && !s1_valid &&
                    rsr == 0 && wsr == 0) begin
                req_reinit <= 1'b0;
                istate <= I_RST;
                itimer <= SIM ? 17'd20 : 17'd20000;
                p_reset_n <= 1'b0;
                p_cke <= 1'b0;
                p_odt <= 1'b0;
                cal_done <= 1'b0;
                cal_active <= 1'b0;
                cstate <= C_IDLE;
                ref_owed <= 4'd0;
                refi_cnt <= 10'd0;
            end

            // ---------------- scheduler ----------------
            s1_valid <= 1'b0;
            if (do_ref) begin
                c_cs_n[0] <= 1'b0;
                {c_ras_n[0], c_cas_n[0], c_we_n[0]} <= CMD_REF;
                rfc_cnt <= RFC;
                ref_ack_r <= 1'b1;
            end else if (can_issue) begin
                c_cs_n[0] <= 1'b0;
                {c_ras_n[0], c_cas_n[0], c_we_n[0]} <= CMD_ACT;
                c_ba[0 +: BANK_WIDTH] <= rq_bank;
                c_a[0 +: ROW_WIDTH] <= rq_row;
                s1_valid <= 1'b1;
                s1_wr <= rq_wr;
                s1_cal <= rq_cal;
                s1_b <= cal_req_b;
                s1_bank <= rq_bank;
                s1_col <= rq_col;
                bank_cnt[rq_bank] <= rq_wr ? 3'd7 : 3'd6;
                if (rq_wr) wtr_cnt <= 3'd4;
                else       rtw_cnt <= 3'd2;
                if (!rq_cal) begin
                    cq_head <= ~cq_head;
                end
                if (rq_wr && !rq_cal) wq_claimed <= wq_claimed + 1'b1;
            end
            if (s1_valid) begin
                // slot 2 = RD, slot 3 = WR, both with auto-precharge
                if (s1_wr) begin
                    c_cs_n[3] <= 1'b0;
                    {c_ras_n[3], c_cas_n[3], c_we_n[3]} <= CMD_WR;
                    c_ba[3*BANK_WIDTH +: BANK_WIDTH] <= s1_bank;
                    c_a[3*ROW_WIDTH +: ROW_WIDTH] <= {{(ROW_WIDTH-COL_WIDTH){1'b0}}, s1_col} |
                                                     (1 << 10);
                end else begin
                    // RD in slot 2, or 3 (opt_rd_slot3: read data a tCK
                    // later, to move a lane off the IDES frame cut)
                    c_cs_n[rd_slot] <= 1'b0;
                    {c_ras_n[rd_slot], c_cas_n[rd_slot], c_we_n[rd_slot]} <= CMD_RD;
                    c_ba[rd_slot*BANK_WIDTH +: BANK_WIDTH] <= s1_bank;
                    c_a[rd_slot*ROW_WIDTH +: ROW_WIDTH] <= {{(ROW_WIDTH-COL_WIDTH){1'b0}}, s1_col} |
                                                           (1 << 10);
                    // HOLD resets the DQS FIFO pointers: only when no
                    // earlier read's data can still be on its way
                    if (opt_hold_rd && rsr[11:0] == 12'd0) p_dqs_hold <= 1'b1;
                end
            end

            // FIFO counts (pushes above, pops here)
            cq_count <= cq_count_n;
            wq_count <= wq_count_n;
            ui_room <= cal_done && cq_count_n != 2'd2 && wq_count_n != WDQ_DEPTH;
            if (wdata_pop) begin
                wq_head <= wq_head + 1'b1;
                wq_claimed <= wq_claimed + (can_issue && rq_wr && !rq_cal) - 1'b1;
            end

            // ---------------- pipelines / PHY data path ----------------
            rsr <= rsr_n; rsr_user <= rsr_user_n; rsr_a <= rsr_a_n;
            wsr <= wsr_n; wsr_cal <= wsr_cal_n; wsr_b <= wsr_b_n;

            // A gate left open by a late position only closes on a DQS
            // falling edge; drive one ourselves (seen through the IOBUF).
            dqs_flush <= 1'b0;
            // Stage A: undelayed burst signals, one frame ahead
            wbeats = wdata_now ? (wsr_cal_n[wl_a] ? (wsr_b_n[wl_a] ? ~tp_a : tp_a)
                                                  : wq_data[wq_head])
                               : {8*DQ_WIDTH{1'b0}};
            for (i = 0; i < 8*LANES; i = i + 1)
                wmask[i] = !wdata_now ? 1'b1 :
                           wsr_cal_n[wl_a] ? 1'b0 : wq_mask[wq_head][i];
            a_dq <= wbeats;
            a_dm <= wmask;
            a_dqs <= wdata_now ? 8'b1010_1010 : 8'h00;
            // DQS preamble (slot 3 before), postamble (slot 0 after). DQ
            // is driven from two slots before the burst to one after: on
            // the board, with only one slot before, beat 0 went out
            // undriven (the OSER8_MEM tristate control lags its data).
            // DQS too: with one slot the preamble came out ~0.5 tCK on the
            // board (tWPRE >= 0.9 tCK) and beat 0 of back-to-back-ish
            // writes was lost.
            a_drv <= wdata_now ? 4'b1111 :
                     {{2{wsr_n[wl_a - 1'b1]}}, 1'b0, wsr_n[wl_a + 1'b1]};
            a_dq_drv <= wdata_now ? 4'b1111 :
                        {{2{wsr_n[wl_a - 1'b1]}}, 1'b0, wsr_n[wl_a + 1'b1]};
            if (dqs_flush) begin
                p_wr_dq_oen <= 4'b1111;
                p_wr_dm <= {8*LANES{1'b1}};
                p_wr_dqs <= 8'b0000_1111;
                p_wr_dqs_oen <= 4'b0000;
            end else begin
                // Stage B
                prev_dq <= a_dq;
                prev_dm <= a_dm;
                prev_dqs <= a_dqs;
                prev_dq_drv <= a_dq_drv;
                prev_dqs_drv <= a_drv;
                // Delay by kq beats (DQ / DM) and kd beats (DQS), taking the
                // early beats from the previous frame
                kd = {wr_dly, 1'b0};
                kq = kd + opt_wr_shift;
                for (b = 0; b < 8; b = b + 1) begin
                    bq = b - kq;
                    bd = b - kd;
                    p_wr_dq[b*DQ_WIDTH +: DQ_WIDTH] <= bq >= 0 ? a_dq[bq*DQ_WIDTH +: DQ_WIDTH]
                                                              : prev_dq[(bq+8)*DQ_WIDTH +: DQ_WIDTH];
                    p_wr_dm[b*LANES +: LANES] <= bq >= 0 ? a_dm[bq*LANES +: LANES]
                                                        : prev_dm[(bq+8)*LANES +: LANES];
                    p_wr_dqs[b] <= bd >= 0 ? a_dqs[bd] : prev_dqs[bd+8];
                end
                for (b = 0; b < 4; b = b + 1) begin
                    bd = b - wr_dly;
                    bq = b - wr_dly - 1;
                    dd = bd >= 0 ? a_drv[bd] : prev_dqs_drv[bd+4];
                    p_wr_dqs_oen[b] <= ~dd;
                    dd = bd >= 0 ? a_dq_drv[bd] : prev_dq_drv[bd+4];
                    if (opt_wr_shift)
                        dd = dd | (bq >= 0 ? a_dq_drv[bq] : prev_dq_drv[bq+4]);
                    if (opt_dq_hold && rsr_n == 0 && rsr == 0) dd = 1'b1;
                    p_wr_dq_oen[b] <= ~dd;
                end
            end

            p_rd_gate <= gate_n;
            p_rclksel <= rsel_n;

            // ---------------- calibration ----------------
            if (ctimer != 0) ctimer <= ctimer - 1'b1;
            if (rsr_a[0]) begin
                afcnt <= 5'd1;
                a_seen <= 1'b1;
            end else if (afcnt != 0 && afcnt != 5'd31) begin
                afcnt <= afcnt + 1'b1;
            end
            // Compare each frame after read A with the expected lanes
            for (i = 0; i < LANES; i = i + 1) begin
                lane_got = lane_bits(rd_frame, i);
                lane_exp = lane_bits(tsel ? ~tp_a : tp_a, i);
                lprevb[i] <= lane_got == ~lane_exp;
                lprevb2[i] <= lprevb[i];
                if (a_seen && !lfound[i] && afcnt != 0 && afcnt < WIN &&
                        lane_got == lane_exp) begin
                    lfound[i] <= 1'b1;
                    lfl[i] <= afcnt[3:0];
                end
            end

            cal_req <= 1'b0;
            case (cstate)
                C_IDLE: ;
                C_WRA: begin
                    cal_req <= 1'b1; cal_req_wr <= 1'b1; cal_req_b <= 1'b0;
                    if (cal_req && can_issue) begin
                        cal_req <= 1'b0;
                        cstate <= C_WRB;
                    end
                end
                C_WRB: begin
                    cal_req <= 1'b1; cal_req_wr <= 1'b1; cal_req_b <= 1'b1;
                    if (cal_req && can_issue && cal_req_b) begin
                        cal_req <= 1'b0;
                        ctimer <= 6'd16;
                        cstate <= C_SETTLE;
                    end
                end
                C_SETTLE: begin
                    // clear the search state here, off the issue path
                    cphase2 <= 1'b0;
                    cg <= 7'd0;
                    ck <= 4'd0;
                    lpass_g <= 0;
                    for (i = 0; i < LANES; i = i + 1) begin
                        lg[i] <= 7'd0;
                        rowpass[i] <= 4'd0;
                        pmap[i] <= 128'd0;
                    end
                    ldone <= 0;
                    if (ctimer == 0) cstate <= C_HOLD;
                end
                // Reset the DQS FIFO pointers before every trial: a bad gate
                // position leaves them out of step with the IDES8_MEM frames.
                C_HOLD: begin
                    if (ctimer == 0 && !hold_sent) begin
                        // the trial read gets its HOLD with its RD, like
                        // every read in normal operation
                        dqs_flush <= 1'b1;
                        hold_sent <= 1'b1;
                        ctimer <= 6'd4;
                    end else if (ctimer == 0) begin
                        hold_sent <= 1'b0;
                        cstate <= C_RDA;
                    end
                end
                C_RDB: begin
                    cal_req <= 1'b1; cal_req_wr <= 1'b0; cal_req_b <= 1'b1;
                    if (cal_req && can_issue && cal_req_b) begin
                        cal_req <= 1'b0;
                        cstate <= C_RDA;
                    end
                end
                // One read per trial, of burst A or B in turn (so what a
                // previous trial left in the DQS FIFO can't match); HOLD is
                // pulsed with its RD.
                C_RDA: begin
                    cal_req <= 1'b1; cal_req_wr <= 1'b0; cal_req_b <= tsel;
                    if (cal_req && can_issue && cal_req_b == tsel) begin
                        cal_req <= 1'b0;
                        lfound <= 0;
                        a_seen <= 1'b0;
                        afcnt <= 5'd0;
                        cstate <= C_WAIT;
                    end
                end
                C_WAIT: if (a_seen && afcnt == WIN) cstate <= C_EVAL;
                C_EVAL: begin
                    tsel <= ~tsel;
`ifdef OWN_DDR3_SIM
                    if (lfound != 0 || $test$plusargs("caltrace"))
                        $display("%t cal p%0d g=%0d k=%0d found=%b lat0=%0d",
                                 $realtime, cphase2, cg, ck, lfound, lfl[0]);
`endif
                    for (i = 0; i < LANES; i = i + 1)
                        if (lfound[i]) begin
                            lpass_g[i] <= 1'b1;
                            if (cphase2 && !ldone[i]) begin
                                ldone[i] <= 1'b1;
                                llat[i] <= lfl[i];
                            end
                        end
                    cstate <= C_PULSE;
                end
                C_PULSE: begin
                    // phase 1: rotate every lane; phase 2: only unlocked ones
                    for (i = 0; i < LANES; i = i + 1)
                        if (!cphase2 || !ldone[i]) begin
                            p_rd_calib[i] <= 1'b1;
                            lrot[i] <= lrot[i] + 1'b1;
                        end
                    ctimer <= 6'd6;
                    ck <= ck + 1'b1;
                    cstate <= C_PGAP;
                end
                C_PGAP: if (ctimer == 0) begin
                    if (ck != 4'd8)
                        cstate <= C_HOLD;
                    else if (cphase2) begin
                        // Lanes that didn't lock at the centre fall back to
                        // walking the tCK offset in their RCLKSEL row (on the
                        // DQS sim model a live RCLKSEL change can slip the
                        // gate by a tCK; the board doesn't need this).
                        p2more = 1'b0;
                        for (i = 0; i < LANES; i = i + 1)
                            if (!ldone[i] && !(p2walk[i] && lg[i][6:2] == 5'd31)) begin
                                lg[i] <= p2walk[i] ? {lg[i][6:2] + 1'b1, lg[i][1:0]}
                                                   : {5'd0, lg[i][1:0]};
                                p2walk[i] <= 1'b1;
                                p2more = 1'b1;
                            end
                        ck <= 4'd0;
                        cstate <= p2more ? C_HOLD : C_FINISH;
                    end else
                        cstate <= C_NEXTG;
                end
                // Phase 1 order: cg = {RCLKSEL row q, tCK offset o}, o inner,
                // so RCLKSEL changes only once per row (on the DQS model a
                // live RCLKSEL change can slip the gate by a whole tCK).
                C_NEXTG: begin
                    for (i = 0; i < LANES; i = i + 1)
                        if (lpass_g[i]) begin
                            if (DBG_MAP) pmap[i][cg] <= 1'b1;
                            rowpass[i][cg[6:5]] <= 1'b1;
                        end
                    lpass_g <= 0;
                    ck <= 4'd0;
                    if (cg == 7'd127) begin
                        cstate <= C_P2;
                    end else begin
                        cg <= cg + 1'b1;
                        for (i = 0; i < LANES; i = i + 1)
                            lg[i] <= {cg[4:0] + 1'b1, cg[4:0] == 5'd31 ? cg[6:5] + 1'b1 : cg[6:5]};
                        cstate <= C_HOLD;
                    end
                end
                // Pick each lane's gate as the centre of its longest run of
                // passing positions in linear order g = 4*o + q (one map bit
                // per cycle, off the critical path), then lock latency and
                // rotation there in phase 2.
                C_P2: begin
                    sg <= 7'd0;
                    for (i = 0; i < LANES; i = i + 1) begin
                        sc_len[i] <= 8'd0;
                        sc_blen[i] <= 8'd0;
                    end
                    cstate <= C_SCAN;
                end
                C_SCAN: begin
                    for (i = 0; i < LANES; i = i + 1) begin
                        if (pmap[i][{sg[1:0], sg[6:2]}]) begin
                            if (sc_len[i] == 0) sc_start[i] <= sg;
                            sc_len[i] <= sc_len[i] + 1'b1;
                            if (sc_len[i] + 1'b1 > sc_blen[i]) begin
                                sc_blen[i] <= sc_len[i] + 1'b1;
                                sc_bstart[i] <= (sc_len[i] == 0) ? sg : sc_start[i];
                            end
                        end else begin
                            sc_len[i] <= 8'd0;
                        end
                    end
                    sg <= sg + 1'b1;
                    if (sg == 7'd127) cstate <= C_SCAN2;
                end
                C_SCAN2: begin
                    cphase2 <= 1'b1;
                    p2walk <= 0;
                    ck <= 4'd0;
                    ldone <= 0;
                    for (i = 0; i < LANES; i = i + 1)
                        lg[i] <= sc_bstart[i] + sc_blen[i][7:1];
                    cstate <= C_HOLD;
                end
                C_FINISH: begin
                    // all lanes locked, latencies within one frame
                    cstate <= C_DONE;
                    cal_active <= 1'b0;
                    cal_runs <= cal_runs + 1'b1;
                    tmin = 4'hF; tmax = 4'h0;
                    for (i = 0; i < LANES; i = i + 1) begin
                        if (!ldone[i]) cal_fail <= 1'b1;
                        if (llat[i] < tmin) tmin = llat[i];
                        if (llat[i] > tmax) tmax = llat[i];
                    end
                    if (tmax - tmin > 4'd1) cal_fail <= 1'b1;
                    lat_min <= tmin;
                    lat_max <= tmax;
                    for (i = 0; i < LANES; i = i + 1)
                        lane_late[i] <= llat[i] == tmax;
                end
                C_DONE: begin
                    if (!cal_fail) cal_done <= 1'b1;
                    if (req_recal && all_banks_idle && !s1_valid) begin
                        req_recal <= 1'b0;
                        cal_done <= 1'b0;
                        cal_fail <= 1'b0;
                        cal_active <= 1'b1;
                        cstate <= C_WRA;
                    end
                end
                default: ;
            endcase
        end
    end

    // ------------------------------------------------------------------
    // Debug register reads
    // ------------------------------------------------------------------
    always @(posedge pclk) begin
        case (dbg_addr)
            8'h00: dbg_rdata <= {8'h0D, cal_runs, 3'b0, cal_fail, 3'b0, cal_done,
                                 cstate[3:0], istate, ref_owed};
            8'h01: dbg_rdata <= {wstep_r[3], wstep_r[2], wstep_r[1], wstep_r[0]};
            8'h02: dbg_rdata <= {mr1, mr0};
            8'h03: dbg_rdata <= {mr3, mr2};
            8'h04: dbg_rdata <= {28'h0, opt_manual, opt_hold_rd, req_reinit, req_recal};
            8'h05: dbg_rdata <= {16'h0, gate_len, 4'h0, gate_base};
            8'h06: dbg_rdata <= {2'h0, wr_dly, 2'h0, wr_lat, 1'h0, dll_lock, 1'b0, p_ck_inv,
                                 3'h0, opt_wr_shift, dll_step[7:1], p_dll_val};
            8'h08, 8'h09, 8'h0A, 8'h0B:
                dbg_rdata <= {lane_late[dbg_addr[1:0]], 1'b0, lrot[dbg_addr[1:0]],
                              llat[dbg_addr[1:0]], 4'h0, rowpass[dbg_addr[1:0]],
                              8'h0, 1'b0, lg[dbg_addr[1:0]]};
            8'h0C: dbg_rdata <= {lat_min, lat_max};
            8'h0D: dbg_rdata <= p_wdq_dly;
            default:
                if (DBG_MAP && dbg_addr[7:4] != 4'h0)
                    // 0x10 + lane*4 + word: pass map bits [word*32 +: 32]
                    dbg_rdata <= pmap[dbg_addr[3:2]][dbg_addr[1:0]*32 +: 32];
                else
                    dbg_rdata <= 32'h0;
        endcase
    end

endmodule
