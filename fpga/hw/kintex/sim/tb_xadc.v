// xsim: xadc_monitor with the XADC model and xadc_monitor.stim (25 degC,
// VCCINT 1.000 V, then 60 degC / 0.950 V at 2 ms): the AdcMonitor-format words and
// MIG's temperature code.
`timescale 1ns/1ps
module tb_xadc;
    reg clk = 0, rst = 1;
    always #5 clk = ~clk;
    wire [11:0] temp_code;
    wire [31:0] now, extremes, samples;
    xadc_monitor dut (.clk(clk), .rst(rst), .temp_code(temp_code),
                      .now(now), .extremes(extremes), .samples(samples));
    integer errors = 0;
    reg [31:0] n0;
    task check(input [15:0] t4, input [15:0] v, input [15:0] tmax4, input [15:0] vmin);
        begin
            $display("t=%0t samples=%0d temp=%0d/4 vcc=%0d/1024 tmax=%0d/4 vmin=%0d/1024 code=%0d",
                     $time, samples, $signed(now[31:16]), now[15:0],
                     $signed(extremes[31:16]), extremes[15:0], temp_code);
            if ($signed(now[31:16]) < $signed(t4) - 2 || $signed(now[31:16]) > $signed(t4) + 2 ||
                now[15:0] < v - 2 || now[15:0] > v + 2 ||
                $signed(extremes[31:16]) < $signed(tmax4) - 2 || $signed(extremes[31:16]) > $signed(tmax4) + 2 ||
                extremes[15:0] < vmin - 2 || extremes[15:0] > vmin + 2) begin
                $display("FAIL"); errors = errors + 1;
            end
        end
    endtask
    initial begin
        #200 rst = 0;
        wait (samples >= 3);
        check(100, 1024, 100, 1024);
        if (now[31:16] < 98) $display("(first sample after the 2 ms switch?)");
        #(2000000 - $time);
        n0 = samples;
        wait (samples >= n0 + 3);
        check(240, 973, 240, 973);
        if (errors == 0) $display("TB PASS"); else $display("TB FAIL");
        $finish;
    end
    initial begin #6000000 $display("TB FAIL timeout"); $finish; end
endmodule
