// DDR3 PHY for the Tang Mega 138K Pro dock (GW5AST, x32 = 4 byte lanes),
// built straight on the Gowin hard I/O primitives. 1:4 clock ratio: every
// pclk cycle ("frame") carries 4 tCK = 8 DDR bit slots per pin.
//
// Frame bit k of a pin goes out at fclk half-cycle k (D0 first). Command /
// address pins repeat each tCK slot twice (slot s = bits 2s, 2s+1). CK is
// sent as 0,1,0,1,... so it rises in the middle of each command slot.
//
// Byte lane L: DQS primitive (DQSW0 clocks the DQS OSER8_MEM, DQSW270 the
// DQ/DM ones, DQSR90 the IDES8_MEM), 8 DQ IOBUFs, 1 DM, 1 DQS pair.
// Everything handed to this module in frame f reaches the pins a fixed
// number of frames later, the same for command and data pins (checked in
// sim/tb.v), so the controller works purely in frames.
//
// Read data comes back raw: rd_frame[b*32 + pin] is IDES8_MEM output Q<b> (Q0 earliest).
// The controller finds the right frame, CALIB rotation and DQS gate.
module own_ddr3_phy #(
    parameter ROW_WIDTH = 15,
    parameter BANK_WIDTH = 3,
    parameter DQ_WIDTH = 32,
    parameter LANES = DQ_WIDTH / 8
) (
    input  wire                        fclk,      // memory clock (400 MHz)
    input  wire                        pclk,      // fclk / 4
    input  wire                        rst,       // async, held while fclk is stopped
    input  wire [7:0]                  dllstep,

    // Command slots 0..3 of this frame
    input  wire [3:0]                  c_cs_n,
    input  wire [3:0]                  c_ras_n,
    input  wire [3:0]                  c_cas_n,
    input  wire [3:0]                  c_we_n,
    input  wire [4*BANK_WIDTH-1:0]     c_ba,
    input  wire [4*ROW_WIDTH-1:0]      c_a,
    input  wire                        cke,
    input  wire                        odt,
    input  wire                        reset_n,

    // Write data path (all lanes alike)
    input  wire [8*DQ_WIDTH-1:0]       wr_dq,     // [b*32 + pin]
    input  wire [8*LANES-1:0]          wr_dm,     // [b*4 + lane], 1 = masked
    input  wire [3:0]                  wr_dq_oen, // per tCK slot, 1 = tristate
    input  wire [7:0]                  wr_dqs,
    input  wire [3:0]                  wr_dqs_oen,
    input  wire [8*LANES-1:0]          wstep,     // per lane write delay

    // Read path
    input  wire [4*LANES-1:0]          rd_gate,   // DQS READ[3:0] per lane
    input  wire [3*LANES-1:0]          rclksel,
    input  wire [LANES-1:0]            rd_calib,  // IDES8_MEM CALIB per lane
    input  wire                        dqs_hold,
    output wire [8*DQ_WIDTH-1:0]       rd_frame,  // [b*32 + pin]
    output wire [LANES-1:0]            rburst,

    // Pins
    output wire [ROW_WIDTH-1:0]        O_ddr_addr,
    output wire [BANK_WIDTH-1:0]       O_ddr_ba,
    output wire                        O_ddr_cs_n,
    output wire                        O_ddr_ras_n,
    output wire                        O_ddr_cas_n,
    output wire                        O_ddr_we_n,
    output wire                        O_ddr_clk,
    output wire                        O_ddr_clk_n,
    output wire                        O_ddr_cke,
    output wire                        O_ddr_odt,
    output wire                        O_ddr_reset_n,
    output wire [LANES-1:0]            O_ddr_dqm,
    inout  wire [DQ_WIDTH-1:0]         IO_ddr_dq,
    inout  wire [LANES-1:0]            IO_ddr_dqs,
    inout  wire [LANES-1:0]            IO_ddr_dqs_n
);

    // ------------------------------------------------------------------
    // Command / address: OSER8, each slot doubled
    // ------------------------------------------------------------------
    // Slot s drives bits 2s and 2s+1.
    function [7:0] dbl;
        input [3:0] s;
        dbl = {s[3], s[3], s[2], s[2], s[1], s[1], s[0], s[0]};
    endfunction

    wire [7:0] ser_cs  = dbl(c_cs_n);
    wire [7:0] ser_ras = dbl(c_ras_n);
    wire [7:0] ser_cas = dbl(c_cas_n);
    wire [7:0] ser_we  = dbl(c_we_n);
    wire [7:0] ser_cke = {8{cke}};
    wire [7:0] ser_odt = {8{odt}};
    wire [7:0] ser_ck  = 8'b1010_1010;   // D0 = 0: CK rises mid-slot

    `define OWN_OSER8(name, d, q) \
        OSER8 name ( \
            .D0(d[0]), .D1(d[1]), .D2(d[2]), .D3(d[3]), \
            .D4(d[4]), .D5(d[5]), .D6(d[6]), .D7(d[7]), \
            .TX0(1'b0), .TX1(1'b0), .TX2(1'b0), .TX3(1'b0), \
            .PCLK(pclk), .FCLK(fclk), .RESET(rst), .Q0(q), .Q1() \
        )

    `OWN_OSER8(u_oser_cs,  ser_cs,  O_ddr_cs_n);
    `OWN_OSER8(u_oser_ras, ser_ras, O_ddr_ras_n);
    `OWN_OSER8(u_oser_cas, ser_cas, O_ddr_cas_n);
    `OWN_OSER8(u_oser_we,  ser_we,  O_ddr_we_n);
    `OWN_OSER8(u_oser_cke, ser_cke, O_ddr_cke);
    `OWN_OSER8(u_oser_odt, ser_odt, O_ddr_odt);

    wire ck_ser;
    `OWN_OSER8(u_oser_ck, ser_ck, ck_ser);
    ELVDS_OBUF u_ck_obuf (.I(ck_ser), .O(O_ddr_clk), .OB(O_ddr_clk_n));

    genvar gi, gl;
    generate
        for (gi = 0; gi < ROW_WIDTH; gi = gi + 1) begin : g_a
            wire [7:0] d = dbl({c_a[3*ROW_WIDTH+gi], c_a[2*ROW_WIDTH+gi],
                                c_a[ROW_WIDTH+gi], c_a[gi]});
            `OWN_OSER8(u_oser, d, O_ddr_addr[gi]);
        end
        for (gi = 0; gi < BANK_WIDTH; gi = gi + 1) begin : g_ba
            wire [7:0] d = dbl({c_ba[3*BANK_WIDTH+gi], c_ba[2*BANK_WIDTH+gi],
                                c_ba[BANK_WIDTH+gi], c_ba[gi]});
            `OWN_OSER8(u_oser, d, O_ddr_ba[gi]);
        end
    endgenerate
    `undef OWN_OSER8

    // RESET# is slow (us); a plain register is enough.
    reg reset_n_q = 1'b0;
    always @(posedge pclk or posedge rst)
        if (rst) reset_n_q <= 1'b0;
        else     reset_n_q <= reset_n;
    assign O_ddr_reset_n = reset_n_q;

    // ------------------------------------------------------------------
    // Byte lanes
    // ------------------------------------------------------------------
    generate
        for (gl = 0; gl < LANES; gl = gl + 1) begin : g_lane
            wire dqsr90, dqsw0, dqsw270;
            wire [2:0] wpoint, rpoint;
            wire dqs_in, dqs_q0, dqs_q1;

            DQS #(
                .FIFO_MODE_SEL(1'b0),
                .RD_PNTR(3'b000),
                .DQS_MODE("X4"),
                .HWL("false")
            ) u_dqs (
                .DQSIN   (dqs_in),
                .PCLK    (pclk),
                .FCLK    (fclk),
                .RESET   (rst),
                .READ    (rd_gate[4*gl +: 4]),
                .RCLKSEL (rclksel[3*gl +: 3]),
                .DLLSTEP (dllstep),
                .WSTEP   (wstep[8*gl +: 8]),
                .RLOADN  (1'b0),
                .RMOVE   (1'b0),
                .RDIR    (1'b0),
                .WLOADN  (1'b0),
                .WMOVE   (1'b0),
                .WDIR    (1'b0),
                .HOLD    (dqs_hold),
                .DQSR90  (dqsr90),
                .DQSW0   (dqsw0),
                .DQSW270 (dqsw270),
                .RPOINT  (rpoint),
                .WPOINT  (wpoint),
                .RVALID  (),
                .RBURST  (rburst[gl]),
                .RFLAG   (),
                .WFLAG   ()
            );

            OSER8_MEM #(.TCLK_SOURCE("DQSW")) u_oser_dqs (
                .D0(wr_dqs[0]), .D1(wr_dqs[1]), .D2(wr_dqs[2]), .D3(wr_dqs[3]),
                .D4(wr_dqs[4]), .D5(wr_dqs[5]), .D6(wr_dqs[6]), .D7(wr_dqs[7]),
                .TX0(wr_dqs_oen[0]), .TX1(wr_dqs_oen[1]),
                .TX2(wr_dqs_oen[2]), .TX3(wr_dqs_oen[3]),
                .PCLK(pclk), .FCLK(fclk), .TCLK(dqsw0), .RESET(rst),
                .Q0(dqs_q0), .Q1(dqs_q1)
            );
            ELVDS_IOBUF u_dqs_iobuf (
                .O(dqs_in), .IO(IO_ddr_dqs[gl]), .IOB(IO_ddr_dqs_n[gl]),
                .I(dqs_q0), .OEN(dqs_q1)
            );

            OSER8_MEM #(.TCLK_SOURCE("DQSW270")) u_oser_dm (
                .D0(wr_dm[0*LANES+gl]), .D1(wr_dm[1*LANES+gl]),
                .D2(wr_dm[2*LANES+gl]), .D3(wr_dm[3*LANES+gl]),
                .D4(wr_dm[4*LANES+gl]), .D5(wr_dm[5*LANES+gl]),
                .D6(wr_dm[6*LANES+gl]), .D7(wr_dm[7*LANES+gl]),
                .TX0(1'b0), .TX1(1'b0), .TX2(1'b0), .TX3(1'b0),
                .PCLK(pclk), .FCLK(fclk), .TCLK(dqsw270), .RESET(rst),
                .Q0(O_ddr_dqm[gl]), .Q1()
            );

            for (gi = 0; gi < 8; gi = gi + 1) begin : g_dq
                localparam P = 8 * gl + gi;
                wire q0, q1, din;
                OSER8_MEM #(.TCLK_SOURCE("DQSW270")) u_oser (
                    .D0(wr_dq[0*DQ_WIDTH+P]), .D1(wr_dq[1*DQ_WIDTH+P]),
                    .D2(wr_dq[2*DQ_WIDTH+P]), .D3(wr_dq[3*DQ_WIDTH+P]),
                    .D4(wr_dq[4*DQ_WIDTH+P]), .D5(wr_dq[5*DQ_WIDTH+P]),
                    .D6(wr_dq[6*DQ_WIDTH+P]), .D7(wr_dq[7*DQ_WIDTH+P]),
                    .TX0(wr_dq_oen[0]), .TX1(wr_dq_oen[1]),
                    .TX2(wr_dq_oen[2]), .TX3(wr_dq_oen[3]),
                    .PCLK(pclk), .FCLK(fclk), .TCLK(dqsw270), .RESET(rst),
                    .Q0(q0), .Q1(q1)
                );
                IOBUF u_iobuf (.O(din), .IO(IO_ddr_dq[P]), .I(q0), .OEN(q1));
                IDES8_MEM u_ides (
                    .D(din), .ICLK(dqsr90), .FCLK(fclk), .PCLK(pclk),
                    .WADDR(wpoint), .RADDR(rpoint),
                    .CALIB(rd_calib[gl]), .RESET(rst),
                    .Q0(rd_frame[0*DQ_WIDTH+P]), .Q1(rd_frame[1*DQ_WIDTH+P]),
                    .Q2(rd_frame[2*DQ_WIDTH+P]), .Q3(rd_frame[3*DQ_WIDTH+P]),
                    .Q4(rd_frame[4*DQ_WIDTH+P]), .Q5(rd_frame[5*DQ_WIDTH+P]),
                    .Q6(rd_frame[6*DQ_WIDTH+P]), .Q7(rd_frame[7*DQ_WIDTH+P])
                );
            end
        end
    endgenerate

endmodule
