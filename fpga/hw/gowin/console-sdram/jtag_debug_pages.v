// Read-only diagnostic register bank exposed through the GW5AST ER1 JTAG
// instruction (0x42).  Each scan shifts in the page number for the next scan
// and shifts out the currently selected 32-bit page.
module jtag_debug_pages (
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
    input  wire [31:0] page7,
    input  wire [31:0] page8,
    input  wire [31:0] page9,
    input  wire [31:0] page10,
    input  wire [31:0] page11,
    input  wire [31:0] page12,
    input  wire [31:0] page13,
    input  wire [31:0] page14,
    input  wire [31:0] page15,
    input  wire [31:0] fb0,
    input  wire [31:0] fb1,
    input  wire [31:0] fb2,
    input  wire [31:0] fb3,
    input  wire [31:0] fb4,
    input  wire [31:0] fb5,
    input  wire [31:0] fb6,
    input  wire [31:0] fb7,
    input  wire [31:0] fb8,
    input  wire [31:0] fb9,
    input  wire [31:0] fb10,
    input  wire [31:0] fb11,
    input  wire [31:0] fb12,
    input  wire [31:0] fb13,
    input  wire [31:0] fb14,
    input  wire [31:0] fb15,
    input  wire [31:0] wr0,
    input  wire [31:0] wr1,
    input  wire [31:0] wr2,
    input  wire [31:0] wr3,
    input  wire [31:0] wr4,
    input  wire [31:0] wr5,
    input  wire [31:0] wr6,
    input  wire [31:0] wr7,
    input  wire [31:0] wr8,
    input  wire [31:0] wr9,
    input  wire [31:0] wr10,
    input  wire [31:0] wr11,
    input  wire [31:0] wr12,
    input  wire [31:0] wr13,
    input  wire [31:0] wr14,
    input  wire [31:0] wr15
);
    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire update_dr;
    wire enable_er1;

    reg shift_capture_d = 1'b0;
    reg [31:0] shift_reg = 32'h564f0001;
    reg [5:0] page_select = 6'd0;
    reg [31:0] selected_page;

    always @* begin
        case (page_select)
            5'h00: selected_page = page0;
            5'h01: selected_page = page1;
            5'h02: selected_page = page2;
            5'h03: selected_page = page3;
            5'h04: selected_page = page4;
            5'h05: selected_page = page5;
            5'h06: selected_page = page6;
            5'h07: selected_page = page7;
            5'h08: selected_page = page8;
            5'h09: selected_page = page9;
            5'h0a: selected_page = page10;
            5'h0b: selected_page = page11;
            5'h0c: selected_page = page12;
            5'h0d: selected_page = page13;
            5'h0e: selected_page = page14;
            5'h0f: selected_page = page15;
            5'h10: selected_page = fb0;
            5'h11: selected_page = fb1;
            5'h12: selected_page = fb2;
            5'h13: selected_page = fb3;
            5'h14: selected_page = fb4;
            5'h15: selected_page = fb5;
            5'h16: selected_page = fb6;
            5'h17: selected_page = fb7;
            5'h18: selected_page = fb8;
            5'h19: selected_page = fb9;
            5'h1a: selected_page = fb10;
            5'h1b: selected_page = fb11;
            5'h1c: selected_page = fb12;
            5'h1d: selected_page = fb13;
            5'h1e: selected_page = fb14;
            6'h1f: selected_page = fb15;
            6'h20: selected_page = wr0;
            6'h21: selected_page = wr1;
            6'h22: selected_page = wr2;
            6'h23: selected_page = wr3;
            6'h24: selected_page = wr4;
            6'h25: selected_page = wr5;
            6'h26: selected_page = wr6;
            6'h27: selected_page = wr7;
            6'h28: selected_page = wr8;
            6'h29: selected_page = wr9;
            6'h2a: selected_page = wr10;
            6'h2b: selected_page = wr11;
            6'h2c: selected_page = wr12;
            6'h2d: selected_page = wr13;
            6'h2e: selected_page = wr14;
            default: selected_page = wr15;
        endcase
    end

    (* keep *) GW_JTAG u_jtag (
        .tck_pad_i(tck_pad_i),
        .tms_pad_i(tms_pad_i),
        .tdi_pad_i(tdi_pad_i),
        .tdo_pad_o(tdo_pad_o),
        .tck_o(jtag_tck),
        .tdi_o(jtag_tdi),
        .test_logic_reset_o(),
        .run_test_idle_er1_o(),
        .run_test_idle_er2_o(),
        .shift_dr_capture_dr_o(shift_capture),
        .pause_dr_o(),
        .update_dr_o(update_dr),
        .enable_er1_o(enable_er1),
        .enable_er2_o(),
        .tdo_er1_i(shift_reg[0]),
        .tdo_er2_i(1'b0)
    );

    always @(posedge jtag_tck) begin
        shift_capture_d <= shift_capture;
        if (enable_er1 && shift_capture) begin
            if (!shift_capture_d)
                shift_reg <= selected_page;
            else
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
        end
        if (enable_er1 && update_dr)
            page_select <= shift_reg[5:0];
    end
endmodule
