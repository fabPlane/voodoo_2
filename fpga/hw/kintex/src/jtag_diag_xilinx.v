// Read-only board diagnostics over JTAG, the Kintex counterpart of
// fpga/hw/gowin/src/jtag_diag.v with the same eight pages and reply format
// (read by fpga/hw/kintex/board_diag.py).
//
// BSCANE2 USER1 (IR 0x02, 6-bit IR on Kintex-7), 64-bit DR. Shift in
// {56'x, page[7:0]}; the *next* scan returns
// {page_data[31:0], 8'hB0, page[7:0], 16'h0000}. No padding bit (the
// Gowin GW_JTAG needed one).
module jtag_diag_xilinx (
    input  wire [31:0] page0,
    input  wire [31:0] page1,
    input  wire [31:0] page2,
    input  wire [31:0] page3,
    input  wire [31:0] page4,
    input  wire [31:0] page5,
    input  wire [31:0] page6,
    input  wire [31:0] page7
);
    wire tck, tdi, sel, capture, shift, update;
    reg  [63:0] sr = 64'h0;

    BSCANE2 #(.JTAG_CHAIN(1)) bscan (
        .CAPTURE(capture), .DRCK(), .RESET(), .RUNTEST(), .SEL(sel), .SHIFT(shift),
        .TCK(tck), .TDI(tdi), .TMS(), .UPDATE(update), .TDO(sr[0])
    );

    reg [7:0]  page = 8'h0;
    reg [31:0] data;
    always @* begin
        case (page[2:0])
            3'd0: data = page0;
            3'd1: data = page1;
            3'd2: data = page2;
            3'd3: data = page3;
            3'd4: data = page4;
            3'd5: data = page5;
            3'd6: data = page6;
            default: data = page7;
        endcase
    end

    // Pages are other clock domains' counters, sampled as-is (quasi-static
    // for a 1 s rate reading); the paths are false-pathed in kintex.xdc.
    always @(posedge tck) begin
        if (sel && capture)
            sr <= {data, 8'hB0, page, 16'h0000};
        else if (sel && shift)
            sr <= {tdi, sr[63:1]};
    end

    // UPDATE is high for one TCK in Update-DR; the shifted-in page selects
    // the next reply.
    always @(posedge tck)
        if (sel && update)
            page <= sr[7:0];
endmodule
