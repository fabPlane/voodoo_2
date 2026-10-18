`timescale 1ns / 1ps
// own_ddr3 + Gowin primitive models + ddr3_model.
// Build/run: make -C fpga/hw/gowin/own_ddr3/sim
//
// Calibrates, then runs a write phase, a read-back phase and a random
// mixed phase (masked writes, back-to-back commands) against a shadow copy.
module tb;
    GSR GSR (.GSRI(1'b1));

    reg clk50 = 0;
    always #10 clk50 = ~clk50;
    reg clk400 = 0;
    always #1.25 clk400 = ~clk400;

    // PLL ENCLK gating, glitch-free on the low phase
    wire pll_stop;
    reg  en_l = 1'b1;
    always @(negedge clk400) en_l <= pll_stop;
    wire memory_clk = clk400 & en_l;

    reg rst_n = 0;
    initial #200 rst_n = 1;

    wire         clk, ddr_rst, calib;
    wire         cmd_ready, wr_data_rdy, rd_data_valid, rd_data_end;
    reg  [2:0]   cmd = 0;
    reg          cmd_en = 0;
    reg  [28:0]  addr = 0;
    reg  [255:0] wr_data = 0;
    reg          wr_data_en = 0;
    reg  [31:0]  wr_data_mask = 0;
    wire [255:0] rd_data;
    reg  [7:0]   dbg_addr = 0;
    wire [31:0]  dbg_rdata;

    wire [14:0] a; wire [2:0] ba;
    wire cs_n, ras_n, cas_n, we_n, ck, ck_n, cke, odt, reset_n;
    wire [3:0] dm;
    wire [31:0] dq; wire [3:0] dqs, dqs_n;

    own_ddr3 #(.SIM(1)) dut (
        .clk(clk50), .pll_stop(pll_stop), .memory_clk(memory_clk),
        .pll_lock(1'b1), .rst_n(rst_n), .clk_out(clk), .ddr_rst(ddr_rst),
        .init_calib_complete(calib), .cmd_ready(cmd_ready), .cmd(cmd),
        .cmd_en(cmd_en), .addr(addr), .wr_data_rdy(wr_data_rdy),
        .wr_data(wr_data), .wr_data_en(wr_data_en), .wr_data_end(wr_data_en),
        .wr_data_mask(wr_data_mask), .rd_data(rd_data),
        .rd_data_valid(rd_data_valid), .rd_data_end(rd_data_end),
        .sr_req(1'b0), .ref_req(1'b0), .sr_ack(), .ref_ack(), .burst(1'b0),
        .dbg_addr(dbg_addr), .dbg_wdata(32'h0), .dbg_we(1'b0), .dbg_rdata(dbg_rdata),
        .O_ddr_addr(a), .O_ddr_ba(ba), .O_ddr_cs_n(cs_n), .O_ddr_ras_n(ras_n),
        .O_ddr_cas_n(cas_n), .O_ddr_we_n(we_n), .O_ddr_clk(ck), .O_ddr_clk_n(ck_n),
        .O_ddr_cke(cke), .O_ddr_odt(odt), .O_ddr_reset_n(reset_n), .O_ddr_dqm(dm),
        .IO_ddr_dq(dq), .IO_ddr_dqs(dqs), .IO_ddr_dqs_n(dqs_n)
    );

    ddr3_model #(
        .RD_DLY0(`RD_DLY0), .RD_DLY1(`RD_DLY1), .RD_DLY2(`RD_DLY2), .RD_DLY3(`RD_DLY3)
    ) mem (
        .ck(ck), .ck_n(ck_n), .cke(cke), .cs_n(cs_n), .ras_n(ras_n), .cas_n(cas_n),
        .we_n(we_n), .ba(ba), .a(a), .odt(odt), .reset_n(reset_n), .dm(dm),
        .dq(dq), .dqs(dqs), .dqs_n(dqs_n)
    );

    // ------------------------------------------------------------------
    // Traffic
    // ------------------------------------------------------------------
    localparam NB = 64;                       // bursts under test
    reg [255:0] shadow [0:NB-1];
    reg [28:0]  expq_addr [0:255];
    reg [7:0]   expq_idx [0:255];
    reg [255:0] expq_data [0:255];   // shadow at issue time
    integer     exp_head = 0, exp_tail = 0;
    integer     errors = 0, checked = 0;

    function [28:0] burst_addr;
        input integer n;
        // spread over banks, columns and a few rows
        burst_addr = {n[5:0], 3'b000} ^ {n[7:6], 19'h0, 6'h0, 3'b000};
    endfunction

    function [255:0] pat;
        input integer n;
        input integer salt;
        integer k;
        begin
            for (k = 0; k < 8; k = k + 1)
                pat[k*32 +: 32] = {n[7:0], salt[7:0], k[7:0], 8'hA5} ^ {32{k[0]}};
        end
    endfunction

    task issue_write(input integer n, input [255:0] d, input [31:0] mask);
        integer k;
        begin
            @(posedge clk);
            while (!(cmd_ready && wr_data_rdy)) @(posedge clk);
            cmd <= 3'd0; cmd_en <= 1; addr <= burst_addr(n);
            wr_data <= d; wr_data_en <= 1; wr_data_mask <= mask;
            for (k = 0; k < 32; k = k + 1)
                if (!mask[k]) shadow[n][k*8 +: 8] = d[k*8 +: 8];
            @(posedge clk);
            cmd_en <= 0; wr_data_en <= 0;
        end
    endtask

    task issue_read(input integer n);
        begin
            @(posedge clk);
            while (!cmd_ready) @(posedge clk);
            cmd <= 3'd1; cmd_en <= 1; addr <= burst_addr(n);
            expq_idx[exp_tail % 256] = n;
            expq_data[exp_tail % 256] = shadow[n];
            expq_addr[exp_tail % 256] = burst_addr(n);
            exp_tail = exp_tail + 1;
            @(posedge clk);
            cmd_en <= 0;
        end
    endtask

    // Back-to-back variants: hold cmd_en across cycles
    integer n, r, seed;
    always @(posedge clk) begin
        if (rd_data_valid) begin
            if (exp_head == exp_tail) begin
                $display("%t TB ERROR: unexpected read data", $realtime);
                errors = errors + 1;
            end else begin
                if (rd_data !== expq_data[exp_head % 256]) begin
                    errors = errors + 1;
                    if (errors < 10)
                        $display("%t TB ERROR: burst %0d got %h exp %h", $realtime,
                                 expq_idx[exp_head % 256], rd_data,
                                 expq_data[exp_head % 256]);
                end
                checked = checked + 1;
                exp_head = exp_head + 1;
            end
        end
    end

    task show_dbg;
        integer l;
        begin
            dbg_addr = 8'h00; repeat (2) @(posedge clk);
            $display("dbg status %h", dbg_rdata);
            for (l = 0; l < 4; l = l + 1) begin
                dbg_addr = 8'h08 + l; repeat (2) @(posedge clk);
                $display("lane %0d: gate o=%0d q=%0d rows %b lat %0d rot %0d late %0d",
                         l, dbg_rdata[6:2], dbg_rdata[1:0], dbg_rdata[19:16],
                         dbg_rdata[27:24], dbg_rdata[30:28], dbg_rdata[31]);
                dbg_addr = 8'h10 + 4*l; repeat (2) @(posedge clk);
                $write("  map %h", dbg_rdata);
                dbg_addr = 8'h11 + 4*l; repeat (2) @(posedge clk);
                $write(" %h", dbg_rdata);
                dbg_addr = 8'h12 + 4*l; repeat (2) @(posedge clk);
                $write(" %h", dbg_rdata);
                dbg_addr = 8'h13 + 4*l; repeat (2) @(posedge clk);
                $display(" %h", dbg_rdata);
            end
            dbg_addr = 8'h0C; repeat (2) @(posedge clk);
            $display("lat min/max %h", dbg_rdata[7:0]);
        end
    endtask

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb.vcd");
            $dumpvars(0, tb);
        end
        for (n = 0; n < NB; n = n + 1) shadow[n] = 256'h0;
        seed = 1;
        wait (!ddr_rst);
        $display("%t UI reset released", $realtime);
        fork
            begin wait (calib); end
            begin
                wait (dut.u_ctrl.cstate == 4'd13);
                repeat (10) @(posedge clk);
            end
        join_any
        repeat (10) @(posedge clk);
        show_dbg;
        if (!calib) begin
            $display("TB FAIL: calibration failed");
            $finish;
        end
        $display("%t calibrated", $realtime);

        // 1. write all, read all (serial handshakes)
        for (n = 0; n < NB; n = n + 1) issue_write(n, pat(n, 1), 32'h0);
        for (n = 0; n < NB; n = n + 1) issue_read(n);
        repeat (100) @(posedge clk);
        $display("%t phase 1: checked %0d errors %0d", $realtime, checked, errors);

        // 2. back-to-back writes then reads (cmd_en held)
        @(posedge clk);
        n = 0;
        while (n < NB) begin
            cmd <= 3'd0; cmd_en <= 1; addr <= burst_addr(n);
            wr_data <= pat(n, 2); wr_data_en <= 1; wr_data_mask <= 0;
            @(posedge clk);
            if (cmd_ready && wr_data_rdy) begin
                shadow[n] = pat(n, 2);
                n = n + 1;
            end
        end
        cmd_en <= 0; wr_data_en <= 0;
        @(posedge clk);
        n = 0;
        while (n < NB) begin
            cmd <= 3'd1; cmd_en <= 1; addr <= burst_addr(n);
            @(posedge clk);
            if (cmd_ready) begin
                expq_idx[exp_tail % 256] = n;
                expq_data[exp_tail % 256] = shadow[n];
                exp_tail = exp_tail + 1;
                n = n + 1;
            end
        end
        cmd_en <= 0;
        repeat (100) @(posedge clk);
        $display("%t phase 2: checked %0d errors %0d", $realtime, checked, errors);

        // 3. random mixed with masks
        for (r = 0; r < 400; r = r + 1) begin
            n = $random(seed) & (NB - 1);
            if ($random(seed) & 1)
                issue_write(n, pat(n, r), ($random(seed) & 3) == 0 ? $random(seed) : 32'h0);
            else
                issue_read(n);
        end
        repeat (200) @(posedge clk);
        $display("%t phase 3: checked %0d errors %0d", $realtime, checked, errors);

        // Let refreshes happen
        repeat (3000) @(posedge clk);
        $display("refreshes %0d max gap %0.1f ns, model reads %0d writes %0d",
                 mem.refs, mem.max_ref_gap, mem.reads, mem.writes);
        if (errors == 0 && mem.errors == 0 && exp_head == exp_tail)
            $display("TB PASS");
        else
            $display("TB FAIL: tb errors %0d, model errors %0d, pending %0d",
                     errors, mem.errors, exp_tail - exp_head);
        $finish;
    end

    // +trace +from=<ns> +to=<ns>: pin trace every fclk half cycle
    real tr_from = 0, tr_to = 0;
    reg  tracing = 0;
    initial begin
        if ($test$plusargs("trace")) begin
            if (!$value$plusargs("from=%f", tr_from)) tr_from = 0;
            if (!$value$plusargs("to=%f", tr_to)) tr_to = 1e9;
            tracing = 1;
        end
    end
    always @(clk400) if (tracing && $realtime >= tr_from && $realtime <= tr_to)
        $display("%t ck=%b cmd=%b%b%b%b ba=%0d a=%h | dqs=%b%b dq0=%h dm=%b | pclk=%b",
                 $realtime, ck, cs_n, ras_n, cas_n, we_n, ba, a, dqs[0], dqs_n[0],
                 dq[7:0], dm[0], clk);

    initial begin
        #(`TIMEOUT_US * 1000.0);
        $display("TB FAIL: timeout");
        show_dbg;
        $finish;
    end
endmodule
