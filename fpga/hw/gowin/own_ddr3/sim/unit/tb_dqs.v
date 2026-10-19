`timescale 1ns / 1ps
// DQS + IDES8_MEM alone: one 8-beat burst per run, print the frames.
// +dly=<ns> burst delay after the RD-equivalent frame edge, +rd=<tck> READ
// window start (tCK from that edge), +len=<tck>, +rsel=<0..7>.
`ifndef IDLE
`define IDLE 1'bz
`endif
module tb_dqs;
    GSR GSR (.GSRI(1'b1));
    reg fclk = 0; always #1.25 fclk = ~fclk;
    reg rst = 1;
    wire pclk;
    CLKDIV #(.DIV_MODE("4")) u_div (.HCLKIN(fclk), .RESETN(~rst), .CALIB(1'b0), .CLKOUT(pclk));
    reg dqs = 1'bz, dq = 1'bz;
    reg hold = 0;
    reg [3:0] read = 0;
    reg [2:0] rsel = 0;
    wire dqsr90, dqsw0, dqsw270, rburst;
    wire [2:0] wp, rp;
    DQS #(.DQS_MODE("X4"), .RD_PNTR(`RDP)) u_dqs (.DQSIN(dqs), .PCLK(pclk), .FCLK(fclk), .RESET(rst),
        .READ(read), .RCLKSEL(rsel), .DLLSTEP(8'd25), .WSTEP(8'd0), .RLOADN(1'b0),
        .RMOVE(1'b0), .RDIR(1'b0), .WLOADN(1'b0), .WMOVE(1'b0), .WDIR(1'b0), .HOLD(hold),
        .DQSR90(dqsr90), .DQSW0(dqsw0), .DQSW270(dqsw270), .RPOINT(rp), .WPOINT(wp),
        .RVALID(), .RBURST(rburst), .RFLAG(), .WFLAG());
    wire [7:0] q;
    IDES8_MEM u_ides (.D(dq), .ICLK(dqsr90), .FCLK(fclk), .PCLK(pclk), .WADDR(wp), .RADDR(rp),
        .CALIB(1'b0), .RESET(rst), .Q0(q[0]), .Q1(q[1]), .Q2(q[2]), .Q3(q[3]),
        .Q4(q[4]), .Q5(q[5]), .Q6(q[6]), .Q7(q[7]));
    real dly; integer rd, len, rs, i, f, nb;
    reg [15:0] win;
    localparam [7:0] PAT1 = 8'b0110_1001;   // burst 1 bits, beat 0 = LSB
    localparam [7:0] PAT2 = 8'b0011_0101;
    task burst(input [7:0] p);
        integer b;
        begin
            dqs = 0; #2.5;                              // preamble
            for (b = 0; b < 8; b = b + 1) begin
                dqs = ~b[0]; dq = p[b]; #1.25;
            end
            dqs = 0; dq = 1'bz; #1.25; dqs = `IDLE;
        end
    endtask
    integer di, hit, hits, k;
    reg [79:0] stream;
    real t_e0;
    reg [7:0] seen;
    task one;   // uses dly, rd, len, rsel
        begin
            rst = 1; dqs = 1'bz; dq = 1'bz; read = 0;
            #20 rst = 0;
            repeat (12) @(posedge pclk);
            hold <= 1; @(posedge pclk); hold <= 0;
            repeat (4) @(posedge pclk);
            hit = -1;
            fork
                begin #(dly); burst(PAT1); end
                begin
                    win = ((16'h1 << len) - 1) << rd;
                    for (f = 0; f < 4; f = f + 1) begin
                        read <= win[4*f +: 4];
                        @(posedge pclk);
                    end
                    read <= 0;
                end
                begin
                    for (i = 0; i < 10; i = i + 1) begin
                        @(posedge pclk); #0.1;
                        stream = {q, stream[79:8]};
                        if (i == 9)
                            for (k = 0; k < 72; k = k + 1)
                                if (stream[k +: 8] == PAT1 && hit < 0) hit = k;
                        if ($test$plusargs("v")) $display("frame %0d q=%b wp=%0d rp=%0d", i, q, wp, rp);
                    end
                end
            join
        end
    endtask
    initial begin
        if ($value$plusargs("dly=%f", dly)) begin
            if (!$value$plusargs("rd=%d", rd)) rd = 3;
            if (!$value$plusargs("len=%d", len)) len = 5;
            if (!$value$plusargs("rsel=%d", rs)) rs = 2;
            rsel = rs;
            one;
            $display("hit %0d", hit);
            $finish;
        end
        if ($test$plusargs("gatemap")) begin
            dly = 100;  // no burst in range
            for (rd = 0; rd < 8; rd = rd + 1)
                for (rs = 0; rs < 8; rs = rs + 1) begin
                    rsel = rs;
                    fork
                        one;
                        begin
                            @(posedge hold); repeat (5) @(posedge pclk); t_e0 = $realtime;
                            @(posedge u_dqs.rd_en);
                            $display("rd=%0d rsel=%0d open at +%0.3f ns", rd, rs, $realtime - t_e0);
                        end
                    join
                end
            $finish;
        end
        // sweep: per burst delay, list (rd,len,rsel) giving a clean frame
        for (di = 0; di < 40; di = di + 1) begin
            dly = 10.0 + di * 0.625;
            hits = 0;
            $write("dly %5.3f:", dly);
            for (len = 4; len <= 6; len = len + 1)
                for (rd = 0; rd < 12; rd = rd + 1)
                    for (rs = 0; rs < 8; rs = rs + 1) begin
                        rsel = rs; one;
                        if (hit >= 0) begin $write(" l%0d/r%0d/s%0d@%0d", len, rd, rs, hit); hits = hits + 1; end
                    end
            $display("  (%0d)", hits);
        end
        $finish;
    end
endmodule
