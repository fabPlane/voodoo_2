// Minimal JTAG mailbox for deterministic hardware-in-the-loop diagnostics.
//
// Gowin reserves IR 0x42/0x43 for the two fabric-visible data registers.
// A 32-bit DR scan on ER1 returns the previous mailbox value while shifting in
// its replacement, LSB first.  UPDATE-DR commits the new word.
module top (
`ifndef OPEN_SOURCE
    input  wire       tck_pad_i,
    input  wire       tms_pad_i,
    input  wire       tdi_pad_i,
    output wire       tdo_pad_o,
`endif
    output wire [1:0] led_n
);
`ifdef OPEN_SOURCE
    // nextpnr's GW_JTAG packer disconnects these dummy pad-side ports. The
    // hard block remains connected to the dedicated package JTAG pins.
    wire tck_pad_i = 1'b0;
    wire tms_pad_i = 1'b0;
    wire tdi_pad_i = 1'b0;
    wire tdo_pad_o;
`endif
    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire update_dr;
    wire enable_er1;

    reg shift_capture_d = 1'b0;
    reg [31:0] shift_reg = 32'h4a544147; // "JTAG"
    reg [31:0] mailbox   = 32'h4a544147;

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
                shift_reg <= mailbox;
            else
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
        end
        if (enable_er1 && update_dr)
            mailbox <= shift_reg;
    end

    // Active-low RGB channels: make mailbox writes visible as a secondary
    // sanity check, while JTAG readback remains the authoritative result.
    assign led_n = ~mailbox[1:0];
endmodule
