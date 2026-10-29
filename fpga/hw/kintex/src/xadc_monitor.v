// Die temperature / VCCINT monitor on the 7-series XADC, in the format of
// the dock's Gowin AdcMonitor (fpga/src/voodoo/gowin/AdcMonitor.scala):
//   now      = {temp, vcc}        temp [degC] = temp / 4, VCCINT [V] = vcc / 1024
//   extremes = {tempMax, vccMin}  since configuration (XADC min / max registers)
//   samples  = sequences read
// (BAR0 window 31 0x68-0x70 and the frame-DMA status words 0-2.)
//
// The XADC runs its continuous sequencer over the calibration, temperature
// and VCCINT channels with 16-sample averaging; at each end of sequence the
// four result registers are read over the DRP. MIG's temperature tracking
// takes temp_code (mig.prj XADC_En Disabled, device_temp_i), so the one
// XADC on the die serves both.
//
// XADC codes are 12-bit (DO[15:4]), UG480:
//   T [degC] = code * 503.975 / 4096 - 273.15   -> temp4 ~= code * 2016 / 4096 - 1093
//   V [V]    = code * 3 / 4096                  -> vcc   = code * 3 / 4
module xadc_monitor #(
    // DCLK / ADCCLK: ADCCLK must stay <= 26 MHz (100 MHz DCLK / 4 = 25 MHz).
    parameter [7:0] CLK_DIV = 8'd4
) (
    input  wire        clk,         // DRP clock (MIG ui_clk, 100 MHz)
    input  wire        rst,
    output reg  [11:0] temp_code = 12'd0,
    output reg  [31:0] now = 32'd0,
    output reg  [31:0] extremes = 32'd0,
    output reg  [31:0] samples = 32'd0
);
    wire [15:0] do_;
    wire        drdy, eos;
    reg  [6:0]  daddr = 7'h00;
    reg         den = 1'b0;

    XADC #(
        .INIT_40(16'h1000),            // 16-sample averaging
        .INIT_41(16'h2FFF),            // continuous sequence, calibration on, alarms off
        .INIT_42({CLK_DIV, 8'h00}),
        .INIT_48(16'h0301),            // sequence: calibration, temperature, VCCINT
        .INIT_49(16'h0000),
        .INIT_4A(16'h0300),            // average temperature and VCCINT
        .INIT_4B(16'h0000),
        .SIM_MONITOR_FILE("xadc_monitor.stim")
    ) xadc (
        .DCLK(clk), .RESET(rst),
        .DADDR(daddr), .DEN(den), .DWE(1'b0), .DI(16'h0000), .DO(do_), .DRDY(drdy),
        .EOS(eos), .EOC(), .BUSY(), .CHANNEL(), .JTAGBUSY(), .JTAGLOCKED(), .JTAGMODIFIED(),
        .ALM(), .OT(), .MUXADDR(),
        .CONVST(1'b0), .CONVSTCLK(1'b0),
        .VP(1'b0), .VN(1'b0), .VAUXP(16'h0000), .VAUXN(16'h0000)
    );

    // Status registers read per sequence: temperature, VCCINT, max
    // temperature, min VCCINT.
    function [6:0] reg_addr(input [1:0] i);
        case (i)
            2'd0: reg_addr = 7'h00;
            2'd1: reg_addr = 7'h01;
            2'd2: reg_addr = 7'h20;
            default: reg_addr = 7'h25;
        endcase
    endfunction

    function [15:0] to_temp4(input [11:0] code);
        reg [23:0] p;
        begin
            p = code * 24'd2016;
            to_temp4 = {4'd0, p[23:12]} - 16'd1093;
        end
    endfunction

    function [15:0] to_vcc(input [11:0] code);
        reg [13:0] p;
        begin
            p = code * 14'd3;
            to_vcc = {4'd0, p[13:2]};
        end
    endfunction

    reg        busy = 1'b0;
    reg [1:0]  idx = 2'd0;
    reg [11:0] r_temp = 12'd0, r_vcc = 12'd0, r_tmax = 12'd0;

    always @(posedge clk) begin
        den <= 1'b0;
        if (rst) begin
            busy <= 1'b0;
            idx <= 2'd0;
        end else if (!busy) begin
            if (eos) begin
                busy <= 1'b1;
                idx <= 2'd0;
                daddr <= reg_addr(2'd0);
                den <= 1'b1;
            end
        end else if (drdy) begin
            case (idx)
                2'd0: r_temp <= do_[15:4];
                2'd1: r_vcc <= do_[15:4];
                2'd2: r_tmax <= do_[15:4];
                default: begin
                    temp_code <= r_temp;
                    now <= {to_temp4(r_temp), to_vcc(r_vcc)};
                    extremes <= {to_temp4(r_tmax), to_vcc(do_[15:4])};
                    samples <= samples + 1'b1;
                end
            endcase
            if (idx == 2'd3) begin
                busy <= 1'b0;
            end else begin
                idx <= idx + 1'b1;
                daddr <= reg_addr(idx + 1'b1);
                den <= 1'b1;
            end
        end
    end
endmodule
