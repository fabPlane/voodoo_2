// Read-only board diagnostics over the on-board USB-JTAG (GW_JTAG ER1, IR 0x42).
//
// 64-bit DR, 66-bit scan with one low padding bit (see ddr3_test/ddr3_jtag.py).
// Shift in {32'x, 24'x, page[7:0]}; the *next* scan returns
// {page_data[31:0], 8'hB0, page[7:0], 16'h0000}.
//
// GW_JTAG launches tdi_o / its state outputs on the TCK rising edge; they
// are retimed on the falling edge first (sampling them on the same rising
// edge corrupted bits on the Pro Dock).
module jtag_diag (
    input  wire        tck_pad_i,
    input  wire        tms_pad_i,
    input  wire        tdi_pad_i,
    output wire        tdo_pad_o,
    input  wire [31:0] page0,
    input  wire [31:0] page1,
    input  wire [31:0] page2,
    input  wire [31:0] page3,
    input  wire [31:0] page4,
    input  wire [31:0] page5,
    input  wire [31:0] page6,
    input  wire [31:0] page7
);
    wire jtag_tck, jtag_tdi, shift_capture, update_dr, enable_er1;
    reg  [63:0] sr = 64'h0;

    (* keep *) GW_JTAG u_jtag (
        .tck_pad_i             (tck_pad_i),
        .tms_pad_i             (tms_pad_i),
        .tdi_pad_i             (tdi_pad_i),
        .tdo_pad_o             (tdo_pad_o),
        .tck_o                 (jtag_tck),
        .tdi_o                 (jtag_tdi),
        .test_logic_reset_o    (),
        .run_test_idle_er1_o   (),
        .run_test_idle_er2_o   (),
        .shift_dr_capture_dr_o (shift_capture),
        .pause_dr_o            (),
        .update_dr_o           (update_dr),
        .enable_er1_o          (enable_er1),
        .enable_er2_o          (),
        .tdo_er1_i             (sr[0]),
        .tdo_er2_i             (1'b0)
    );

    reg tdi_n = 1'b0, sc_n = 1'b0, upd_n = 1'b0, en_n = 1'b0;
    always @(negedge jtag_tck) begin
        tdi_n <= jtag_tdi;
        sc_n  <= shift_capture;
        upd_n <= update_dr;
        en_n  <= enable_er1;
    end

    reg       sc_d = 1'b0, upd_d = 1'b0;
    reg [7:0] page = 8'h0;
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

    always @(posedge jtag_tck) begin
        sc_d  <= sc_n;
        upd_d <= upd_n;
        if (en_n && sc_n) begin
            if (!sc_d)
                sr <= {data, 8'hB0, page, 16'h0000};  // inputs are quasi-static
            else
                sr <= {tdi_n, sr[63:1]};
        end
        if (en_n && upd_n && !upd_d)
            page <= sr[7:0];
    end
endmodule
