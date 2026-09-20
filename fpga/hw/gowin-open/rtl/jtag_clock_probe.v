// JTAG-readable probe for the board's 50 MHz clock path.
//
// ER1 returns a Gray-coded free-running counter. Successive host reads must
// change even when the LEDs, buttons, or video output are unavailable.
module top (
    input  wire       clk50,
`ifndef OPEN_SOURCE
    input  wire       tck_pad_i,
    input  wire       tms_pad_i,
    input  wire       tdi_pad_i,
    output wire       tdo_pad_o,
`endif
    output wire [1:0] led_n
);
`ifdef OPEN_SOURCE
    wire tck_pad_i = 1'b0;
    wire tms_pad_i = 1'b0;
    wire tdi_pad_i = 1'b0;
    wire tdo_pad_o;
`endif

    reg [31:0] counter = 32'b0;
    reg [31:0] counter_gray = 32'b0;
    always @(posedge clk50) begin
        counter <= counter + 1'b1;
        counter_gray <= (counter >> 1) ^ counter;
    end

    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] gray_sync_1 = 32'b0;
    reg [31:0] gray_sync_2 = 32'b0;
    reg [31:0] shift_reg = 32'b0;

    // The serial output leaves through the dedicated JTAG pad, which is not a
    // normal top-level net in the open flow. Keep the hard block explicitly.
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
        .update_dr_o(),
        .enable_er1_o(enable_er1),
        .enable_er2_o(),
        .tdo_er1_i(shift_reg[0]),
        .tdo_er2_i(1'b0)
    );

    always @(posedge jtag_tck) begin
        gray_sync_1 <= counter_gray;
        gray_sync_2 <= gray_sync_1;
        shift_capture_d <= shift_capture;
        if (enable_er1 && shift_capture) begin
            if (!shift_capture_d)
                shift_reg <= gray_sync_2;
            else
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
        end
    end

    assign led_n = ~counter[25:24];
endmodule
