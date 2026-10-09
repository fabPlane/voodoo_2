// iverilog testbench for ddr3_jtag_top with behavioural PLL / DDR3 / JTAG.
`timescale 1ns/1ps

module Gowin_PLL_Sys (input clkin, output clkout0, output clkout1, output reg lock, input enclk1);
    assign clkout0 = clkin; assign clkout1 = clkin;
    initial begin lock = 0; #200 lock = 1; end
endmodule

module DDR3_Memory_Interface_Top (
    input clk, output pll_stop, input memory_clk, input pll_lock, input rst_n,
    output reg clk_out, output reg ddr_rst, output reg init_calib_complete,
    output cmd_ready, input [2:0] cmd, input cmd_en, input [28:0] addr,
    output wr_data_rdy, input [255:0] wr_data, input wr_data_en, input wr_data_end,
    input [31:0] wr_data_mask, output reg [255:0] rd_data, output reg rd_data_valid,
    output rd_data_end, input sr_req, input ref_req, output sr_ack, output ref_ack,
    input burst,
    output [14:0] O_ddr_addr, output [2:0] O_ddr_ba, output O_ddr_cs_n, output O_ddr_ras_n,
    output O_ddr_cas_n, output O_ddr_we_n, output O_ddr_clk, output O_ddr_clk_n,
    output O_ddr_cke, output O_ddr_odt, output O_ddr_reset_n, output [3:0] O_ddr_dqm,
    inout [31:0] IO_ddr_dq, inout [3:0] IO_ddr_dqs, inout [3:0] IO_ddr_dqs_n);
    assign pll_stop = 1'b1;
    assign rd_data_end = rd_data_valid;
    initial begin clk_out = 0; forever #5 clk_out = ~clk_out; end
    initial begin ddr_rst = 1; init_calib_complete = 0; #500 ddr_rst = 0; #300 init_calib_complete = 1; end
    reg [255:0] mem [0:4095];
    reg [31:0] lfsr = 32'h1;
    always @(posedge clk_out) lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
    assign cmd_ready = init_calib_complete && lfsr[3:0] != 0;
    assign wr_data_rdy = init_calib_complete && lfsr[7:4] != 0;
    // read pipeline: fixed latency 24 cycles
    reg [24:0] v; reg [11:0] a [0:24];
    integer i;
    // A fault-injection knob: flip this DQ bit on reads of burst `bad_burst`.
    reg [31:0] flip_bit = 0; reg [11:0] bad_burst = 12'hfff; reg inject = 0;
    always @(posedge clk_out) begin
        if (cmd_en && !cmd_ready) begin $display("ERROR cmd_en without cmd_ready"); $finish; end
        if (cmd_en && cmd == 0 && !wr_data_en) begin $display("ERROR write without data"); $finish; end
        if (wr_data_en && !wr_data_rdy) begin $display("ERROR data without rdy"); $finish; end
        if (cmd_en && addr[2:0] != 0) begin $display("ERROR unaligned"); $finish; end
        if (cmd_en && cmd == 0) mem[addr[14:3]] <= wr_data;
        v <= {v[23:0], cmd_en && cmd == 1};
        a[0] <= addr[14:3];
        for (i = 1; i <= 24; i = i + 1) a[i] <= a[i-1];
        rd_data_valid <= v[24];
        rd_data <= mem[a[24]] ^ ((inject && a[24] == bad_burst) ? {8{flip_bit}} & {224'h0, 32'hffffffff} : 256'h0);
    end
endmodule

module GW_JTAG (input tck_pad_i, input tms_pad_i, input tdi_pad_i, output tdo_pad_o,
    output tck_o, output reg tdi_o, output test_logic_reset_o, output run_test_idle_er1_o,
    output run_test_idle_er2_o, output reg shift_dr_capture_dr_o, output pause_dr_o,
    output reg update_dr_o, output enable_er1_o, output enable_er2_o,
    input tdo_er1_i, input tdo_er2_i);
    reg tck = 0;
    assign tck_o = tck;
    assign enable_er1_o = 1'b1;
    initial begin shift_dr_capture_dr_o = 0; update_dr_o = 0; tdi_o = 0; end
endmodule

module tb;
    reg clk50 = 0; always #10 clk50 = ~clk50;
    wire [3:0] led_n;
    ddr3_jtag_top dut (.clk50(clk50), .led_n(led_n), .tck_pad_i(1'b0), .tms_pad_i(1'b0), .tdi_pad_i(1'b0), .tdo_pad_o());

    `define J dut.u_jtag
    task tckp; begin #50 `J.tck = 1; #50 `J.tck = 0; end endtask

    reg [63:0] rx;
    task scan(input [63:0] tx);
        integer n;
        begin
            `J.shift_dr_capture_dr_o = 1;
            tckp;                         // capture
            for (n = 0; n < 64; n = n + 1) begin
                rx[n] = dut.jtag_sr[0];
                `J.tdi_o = tx[n];
                tckp;
            end
            `J.shift_dr_capture_dr_o = 0;
            tckp;                         // exit1
            `J.update_dr_o = 1; tckp; `J.update_dr_o = 0;
            tckp; tckp;                   // idle
        end
    endtask

    function [63:0] c(input [3:0] op, input [27:0] lo, input [31:0] data);
        c = {data, op, lo};
    endfunction

    reg [63:0] r;
    integer i, j, errors;
    reg [31:0] words [0:15];
    initial begin
        errors = 0;
        #2000;
        scan(c(4'h9, 0, 32'hcafef00d));
        scan(c(4'h0, 0, 0)); r = rx;
        $display("echo resp %h", r);
        if (r[63:32] != 32'hcafef00d || r[31:24] != 8'hd3) begin $display("FAIL echo"); errors = errors + 1; end
        if (!r[15]) begin $display("FAIL calib"); errors = errors + 1; end

        // write two bursts with autoinc, read back
        scan(c(4'h1, 0, 32'h00001000));
        for (j = 0; j < 2; j = j + 1) begin
            for (i = 0; i < 8; i = i + 1) begin
                words[j*8+i] = $random;
                scan(c(4'h2, i, words[j*8+i]));
            end
            scan(c(4'h3, 28'h100, 0));
        end
        scan(c(4'h8, 0, 0)); scan(c(4'h0, 0, 0));
        if (rx[63:32] != 32'h1040) begin $display("FAIL autoinc %h", rx[63:32]); errors = errors + 1; end
        scan(c(4'h1, 0, 32'h00001000));
        for (j = 0; j < 2; j = j + 1) begin
            scan(c(4'h4, 28'h100, 0));
            for (i = 0; i < 8; i = i + 1) begin
                scan(c(4'h5, i, 0));
                if (i > 0 && rx[63:32] != words[j*8+i-1]) begin $display("FAIL rd %0d %0d %h", j, i, rx[63:32]); errors = errors + 1; end
            end
            scan(c(4'h0, 0, 0));
            if (rx[63:32] != words[j*8+7]) begin $display("FAIL rd %0d 7", j); errors = errors + 1; end
        end

        // BIST pass
        scan(c(4'h1, 0, 32'h0));
        scan(c(4'h6, 28'h1F01234, 32'd1000));
        repeat (300) tckp;
        scan(c(4'h7, 0, 0)); scan(c(4'h7, 3, 0)); $display("bist status %h errors %0d", rx[31:0], rx[63:32]);
        if (!rx[11] || rx[10] || rx[63:32] != 0) begin $display("FAIL bist"); errors = errors + 1; end
        scan(c(4'h0, 0, 0)); $display("bist checked %0d", rx[63:32]);
        if (rx[63:32] != 1000) begin $display("FAIL bist count"); errors = errors + 1; end

        // BIST with injected fault on DQ bit 5 at burst 77
        dut.u_ddr3.flip_bit = 32'h20; dut.u_ddr3.bad_burst = 77; dut.u_ddr3.inject = 1;
        scan(c(4'h6, 28'h1F2beef, 32'd200));
        repeat (300) tckp;
        scan(c(4'h7, 0, 0)); scan(c(4'h7, 1, 0)); $display("errs %0d", rx[63:32]);
        if (rx[63:32] != 1) begin $display("FAIL inject count"); errors = errors + 1; end
        scan(c(4'h7, 2, 0)); $display("first bad %h", rx[63:32]);
        if (rx[63:32] != 77*32) begin $display("FAIL first bad"); errors = errors + 1; end
        scan(c(4'h0, 0, 0)); $display("bad bits %h status %h", rx[63:32], rx[31:0]);
        if (rx[63:32] != 32'h20 || !rx[10]) begin $display("FAIL bad bits"); errors = errors + 1; end

        if (errors == 0) $display("PASS"); else $display("FAILED %0d", errors);
        $finish;
    end
endmodule
