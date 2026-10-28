// Drop-in replacement for the Gowin DDR3_Memory_Interface_Top (native UI,
// 1:4, x32, BL8) on the Tang Mega 138K Pro dock. Same ports, plus a debug
// register port (tie dbg_we = 0 when unused).
//
// Clocking: memory_clk (400 MHz, from the PLL output gated by pll_stop,
// 1 = running) drives every IOLOGIC FCLK, the DDRDLL and a CLKDIV /4 that
// makes the UI clock clk_out. The OSER8 / IDES8_MEM / DQS dividers each
// count FCLK on their own, so the IOLOGIC resets are released while
// memory_clk is stopped and all of them start on the same edge.
module own_ddr3 #(
    parameter SIM = 0,
    // Debug-port readout of the per-lane gate pass maps (ddrdbg.py dump).
    // 0 drops only the readout mux: the maps stay, the B183b gate centring
    // picks the middle of each lane's widest pass window from them
    // (DBG_MAP=0 once removed the maps too: every gate landed on 27 and
    // odd-pixel dashes came back, s197_2).
    parameter DBG_MAP = 0,
    parameter HOLD_RD = 1,
    parameter [11:0] RD_PNTR = 12'h000
) (
    input  wire          clk,          // 50 MHz reference, sequencing only
    output wire          pll_stop,     // PLL ENCLK for memory_clk (1 = run)
    input  wire          memory_clk,
    input  wire          pll_lock,
    input  wire          rst_n,
    output wire          clk_out,
    output wire          ddr_rst,
    output wire          init_calib_complete,
    output wire          cmd_ready,
    input  wire [2:0]    cmd,
    input  wire          cmd_en,
    input  wire [28:0]   addr,
    output wire          wr_data_rdy,
    input  wire [255:0]  wr_data,
    input  wire          wr_data_en,
    input  wire          wr_data_end,
    input  wire [31:0]   wr_data_mask,
    output wire [255:0]  rd_data,
    output wire          rd_data_valid,
    output wire          rd_data_end,
    input  wire          sr_req,
    input  wire          ref_req,
    output wire          sr_ack,
    output wire          ref_ack,
    input  wire          burst,

    input  wire [7:0]    dbg_addr,
    input  wire [31:0]   dbg_wdata,
    input  wire          dbg_we,
    output wire [31:0]   dbg_rdata,

    output wire [14:0]   O_ddr_addr,
    output wire [2:0]    O_ddr_ba,
    output wire          O_ddr_cs_n,
    output wire          O_ddr_ras_n,
    output wire          O_ddr_cas_n,
    output wire          O_ddr_we_n,
    output wire          O_ddr_clk,
    output wire          O_ddr_clk_n,
    output wire          O_ddr_cke,
    output wire          O_ddr_odt,
    output wire          O_ddr_reset_n,
    output wire [3:0]    O_ddr_dqm,
    inout  wire [31:0]   IO_ddr_dq,
    inout  wire [3:0]    IO_ddr_dqs,
    inout  wire [3:0]    IO_ddr_dqs_n
);

    assign sr_ack = 1'b0;

    // ------------------------------------------------------------------
    // Start-up sequencing (clk domain)
    // ------------------------------------------------------------------
    reg [2:0]  rs_sync = 3'b0;
    always @(posedge clk) rs_sync <= {rs_sync[1:0], rst_n & pll_lock};
    wire go = rs_sync[2];

    localparam [2:0] Q_WAIT = 3'd0, Q_STOP = 3'd1, Q_REL = 3'd2, Q_START = 3'd3,
                     Q_DLL = 3'd4, Q_RUN = 3'd5;
    reg [2:0]  qstate = Q_WAIT;
    reg [7:0]  qtimer = 8'd0;
    reg        clk_en = 1'b1;
    reg        io_rst = 1'b1;
    reg        dll_rst = 1'b1;
    reg        ui_rst_req = 1'b1;
    wire       dll_lock;
    reg [1:0]  dll_lock_s = 2'b0;
    always @(posedge clk) dll_lock_s <= {dll_lock_s[0], dll_lock};

    always @(posedge clk) begin
        if (qtimer != 0) qtimer <= qtimer - 1'b1;
        if (!go) begin
            qstate <= Q_WAIT;
            qtimer <= 8'd64;
            clk_en <= 1'b1;
            io_rst <= 1'b1;
            dll_rst <= 1'b1;
            ui_rst_req <= 1'b1;
        end else case (qstate)
            Q_WAIT:  if (qtimer == 0) begin clk_en <= 1'b0; qtimer <= 8'd16; qstate <= Q_STOP; end
            Q_STOP:  if (qtimer == 0) begin io_rst <= 1'b0; qtimer <= 8'd16; qstate <= Q_REL; end
            Q_REL:   if (qtimer == 0) begin clk_en <= 1'b1; qtimer <= 8'd16; qstate <= Q_START; end
            Q_START: if (qtimer == 0) begin dll_rst <= 1'b0; qtimer <= 8'd255; qstate <= Q_DLL; end
            Q_DLL:   if (qtimer == 0 && dll_lock_s[1]) begin ui_rst_req <= 1'b0; qstate <= Q_RUN; end
            default: ;
        endcase
    end
    assign pll_stop = clk_en;

    // ------------------------------------------------------------------
    // Clocks
    // ------------------------------------------------------------------
    wire fclk = memory_clk;
    wire pclk;
    CLKDIV #(.DIV_MODE("4")) u_clkdiv (
        .HCLKIN(fclk), .RESETN(~io_rst), .CALIB(1'b0), .CLKOUT(pclk)
    );
    assign clk_out = pclk;

    wire [7:0] dllstep;
    wire       p_dll_ovr;
    wire [7:0] p_dll_val;
    // DQS DLLSTEP must come straight from the DDRDLL (PR0015), so the
    // override registers are only kept for readback.
`ifdef OWN_DDR3_SIM
    // The GW2A DQS model used in simulation delays 25 ps per step.
    assign dllstep = 8'd25;
    assign dll_lock = ~dll_rst;
`else
    DDRDLL #(
        .DLL_FORCE("FALSE"),
        .CODESCAL("000"),
        .SCAL_EN("FALSE"),      // 90 degrees
        .DIV_SEL(1'b0)
    ) u_dll (
        .CLKIN(fclk), .STOP(1'b0), .RESET(dll_rst), .UPDNCNTL(1'b0),
        .STEP(dllstep), .LOCK(dll_lock)
    );
`endif

`ifdef OWN_DDR3_DLL_PROBE
    // Extra DDRDLLs clocked from the 50 MHz reference and the UI clock, to
    // check that STEP can be read from fabric at all (debug only).
    wire [7:0] probe_step0, probe_step1;
    wire       probe_lock0, probe_lock1;
    DDRDLL #(.DLL_FORCE("FALSE"), .CODESCAL("000"), .SCAL_EN("FALSE"), .DIV_SEL(1'b0)) u_dll_p0 (
        .CLKIN(clk), .STOP(1'b0), .RESET(dll_rst), .UPDNCNTL(1'b0),
        .STEP(probe_step0), .LOCK(probe_lock0));
    DDRDLL #(.DLL_FORCE("FALSE"), .CODESCAL("000"), .SCAL_EN("FALSE"), .DIV_SEL(1'b0)) u_dll_p1 (
        .CLKIN(pclk), .STOP(1'b0), .RESET(dll_rst), .UPDNCNTL(1'b0),
        .STEP(probe_step1), .LOCK(probe_lock1));
    reg [31:0] probe_r;
    always @(posedge pclk) probe_r <= {6'h0, probe_lock1, probe_lock0, probe_step1, probe_step0, dllstep};
    assign dbg_rdata = (dbg_addr == 8'h07) ? probe_r : dbg_rdata_c;
`else
    assign dbg_rdata = dbg_rdata_c;
`endif

    reg [2:0] ui_rst_s = 3'b111;
    always @(posedge pclk or posedge ui_rst_req)
        if (ui_rst_req) ui_rst_s <= 3'b111;
        else            ui_rst_s <= {ui_rst_s[1:0], 1'b0};
    assign ddr_rst = ui_rst_s[2];

    // ------------------------------------------------------------------
    // Controller + PHY
    // ------------------------------------------------------------------
    wire [3:0]   c_cs_n, c_ras_n, c_cas_n, c_we_n;
    wire [11:0]  c_ba;
    wire [59:0]  c_a;
    wire         p_cke, p_odt, p_reset_n, p_ck_inv;
    wire [255:0] p_wr_dq;
    wire [31:0]  p_wr_dm;
    wire [3:0]   p_wr_dq_oen;
    wire [7:0]   p_wr_dqs;
    wire [3:0]   p_wr_dqs_oen;
    wire [31:0]  p_wstep;
    wire [31:0]  p_wdq_dly;
    wire [15:0]  p_rd_gate;
    wire [11:0]  p_rclksel;
    wire [3:0]   p_rd_calib;
    wire         p_dqs_hold;
    wire [255:0] rd_frame;
    wire [31:0]  dbg_rdata_c;

    own_ddr3_ctrl #(.SIM(SIM), .DBG_MAP(DBG_MAP), .HOLD_RD_INIT(HOLD_RD)) u_ctrl (
        .pclk(pclk), .rst(ddr_rst),
        .cmd_ready(cmd_ready), .cmd(cmd), .cmd_en(cmd_en), .addr(addr),
        .wr_data_rdy(wr_data_rdy), .wr_data(wr_data), .wr_data_en(wr_data_en),
        .wr_data_end(wr_data_end), .wr_data_mask(wr_data_mask),
        .rd_data(rd_data), .rd_data_valid(rd_data_valid), .rd_data_end(rd_data_end),
        .init_calib_complete(init_calib_complete), .ref_ack(ref_ack),
        .c_cs_n(c_cs_n), .c_ras_n(c_ras_n), .c_cas_n(c_cas_n), .c_we_n(c_we_n),
        .c_ba(c_ba), .c_a(c_a), .p_cke(p_cke), .p_odt(p_odt), .p_reset_n(p_reset_n),
        .p_wr_dq(p_wr_dq), .p_wr_dm(p_wr_dm), .p_wr_dq_oen(p_wr_dq_oen),
        .p_wr_dqs(p_wr_dqs), .p_wr_dqs_oen(p_wr_dqs_oen), .p_wstep(p_wstep),
        .p_wdq_dly(p_wdq_dly),
        .p_rd_gate(p_rd_gate), .p_rclksel(p_rclksel), .p_rd_calib(p_rd_calib),
        .p_dqs_hold(p_dqs_hold), .rd_frame(rd_frame),
        .p_ck_inv(p_ck_inv), .p_dll_ovr(p_dll_ovr), .p_dll_val(p_dll_val),
        .dll_step(dllstep), .dll_lock(dll_lock),
        .dbg_addr(dbg_addr), .dbg_wdata(dbg_wdata), .dbg_we(dbg_we),
        .dbg_rdata(dbg_rdata_c)
    );

    own_ddr3_phy #(.RD_PNTR(RD_PNTR)) u_phy (
        .fclk(fclk), .pclk(pclk), .rst(io_rst), .dllstep(dllstep),
        .c_cs_n(c_cs_n), .c_ras_n(c_ras_n), .c_cas_n(c_cas_n), .c_we_n(c_we_n),
        .c_ba(c_ba), .c_a(c_a), .cke(p_cke), .odt(p_odt), .reset_n(p_reset_n),
        .ck_inv(p_ck_inv),
        .wr_dq(p_wr_dq), .wr_dm(p_wr_dm), .wr_dq_oen(p_wr_dq_oen),
        .wr_dqs(p_wr_dqs), .wr_dqs_oen(p_wr_dqs_oen), .wstep(p_wstep),
        .wdq_dly(p_wdq_dly),
        .rd_gate(p_rd_gate), .rclksel(p_rclksel), .rd_calib(p_rd_calib),
        .dqs_hold(p_dqs_hold), .rd_frame(rd_frame), .rburst(),
        .O_ddr_addr(O_ddr_addr), .O_ddr_ba(O_ddr_ba), .O_ddr_cs_n(O_ddr_cs_n),
        .O_ddr_ras_n(O_ddr_ras_n), .O_ddr_cas_n(O_ddr_cas_n), .O_ddr_we_n(O_ddr_we_n),
        .O_ddr_clk(O_ddr_clk), .O_ddr_clk_n(O_ddr_clk_n), .O_ddr_cke(O_ddr_cke),
        .O_ddr_odt(O_ddr_odt), .O_ddr_reset_n(O_ddr_reset_n), .O_ddr_dqm(O_ddr_dqm),
        .IO_ddr_dq(IO_ddr_dq), .IO_ddr_dqs(IO_ddr_dqs), .IO_ddr_dqs_n(IO_ddr_dqs_n)
    );

endmodule
