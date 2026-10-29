// xsim: jtag_diag_xilinx through the BSCANE2 model (glbl JTAG nets driven
// here as the TAP would): page select + reply format, as board_diag.py.
`timescale 1ns/1ps
module tb_diag;
    reg tck = 0, tdi = 0;
    initial begin
        force glbl.JTAG_TCK_GLBL = tck;
        force glbl.JTAG_TDI_GLBL = tdi;
        glbl.JTAG_SEL1_GLBL = 1;
        glbl.JTAG_CAPTURE_GLBL = 0; glbl.JTAG_SHIFT_GLBL = 0; glbl.JTAG_UPDATE_GLBL = 0;
    end
    jtag_diag_xilinx dut (
        .page0(32'hD1000000), .page1(32'h11111111), .page2(32'h22222222), .page3(32'h33333333),
        .page4(32'h44444444), .page5(32'h55555555), .page6(32'h66666666), .page7(32'h77777777));

    task clk; begin #10 tck = 1; #10 tck = 0; end endtask
    // One DR scan: Capture-DR (1 TCK), Shift-DR (64 TCK, LSB first, TDO
    // sampled on the falling edge before each shift), Update-DR (1 TCK).
    task scan(input [63:0] din, output [63:0] dout);
        integer i;
        begin
            glbl.JTAG_CAPTURE_GLBL = 1; clk; glbl.JTAG_CAPTURE_GLBL = 0;
            glbl.JTAG_SHIFT_GLBL = 1;
            for (i = 0; i < 64; i = i + 1) begin
                tdi = din[i];
                dout[i] = glbl.JTAG_USER_TDO1_GLBL;
                clk;
            end
            glbl.JTAG_SHIFT_GLBL = 0;
            glbl.JTAG_UPDATE_GLBL = 1; clk; glbl.JTAG_UPDATE_GLBL = 0;
        end
    endtask

    reg [63:0] r;
    integer p, errors = 0;
    initial begin
        #100;
        scan(64'd0, r);
        for (p = 1; p <= 8; p = p + 1) begin
            scan(p % 8, r);   // reply carries the page selected by the previous scan
            if (r[31:24] !== 8'hB0 || r[23:16] !== (p - 1) || r[15:0] !== 16'h0 ||
                r[63:32] !== (p == 1 ? 32'hD1000000 : {8{4'(p - 1)}})) begin
                $display("FAIL page %0d: %h", p - 1, r); errors = errors + 1;
            end else $display("page %0d: %h", p - 1, r);
        end
        if (errors == 0) $display("TB PASS"); else $display("TB FAIL");
        $finish;
    end
endmodule
