// JTAG-readable destructive test of one inferred 1024x32 block RAM.
//
// ER1 status format:
//   [31:16] 16'hb5a5 marker
//   [15]    test complete
//   [14]    mismatch detected
//   [13:12] state (WRITE=0, READ=1, CHECK=2, DONE=3)
//   [11:10] zero
//   [9:0]   current address, or first failing address
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

    localparam WRITE = 2'd0;
    localparam READ  = 2'd1;
    localparam CHECK = 2'd2;
    localparam DONE  = 2'd3;

    reg [31:0] memory [0:1023];
    reg [31:0] read_data = 32'b0;
    reg [9:0] address = 10'b0;
    reg [9:0] failure_address = 10'b0;
    reg [1:0] state = WRITE;
    reg failed = 1'b0;

    function automatic [31:0] pattern(input [9:0] a);
        pattern = {a, ~a, a[5:0], 6'h2d};
    endfunction

    always @(posedge clk50) begin
        case (state)
            WRITE: begin
                memory[address] <= pattern(address);
                if (address == 10'h3ff) begin
                    address <= 10'b0;
                    state <= READ;
                end else begin
                    address <= address + 1'b1;
                end
            end
            READ: begin
                read_data <= memory[address];
                state <= CHECK;
            end
            CHECK: begin
                if (read_data != pattern(address)) begin
                    if (!failed)
                        failure_address <= address;
                    failed <= 1'b1;
                end
                if (address == 10'h3ff)
                    state <= DONE;
                else begin
                    address <= address + 1'b1;
                    state <= READ;
                end
            end
            default: state <= DONE;
        endcase
    end

    wire [31:0] status_word = {
        16'hb5a5,
        state == DONE,
        failed,
        state,
        2'b00,
        failed ? failure_address : address
    };

    wire jtag_tck;
    wire jtag_tdi;
    wire shift_capture;
    wire enable_er1;
    reg shift_capture_d = 1'b0;
    reg [31:0] status_sync_1 = 32'b0;
    reg [31:0] status_sync_2 = 32'b0;
    reg [31:0] shift_reg = 32'b0;

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
        status_sync_1 <= status_word;
        status_sync_2 <= status_sync_1;
        shift_capture_d <= shift_capture;
        if (enable_er1 && shift_capture) begin
            if (!shift_capture_d)
                shift_reg <= status_sync_2;
            else
                shift_reg <= {jtag_tdi, shift_reg[31:1]};
        end
    end

    assign led_n[0] = ~(state == DONE && !failed);
    assign led_n[1] = ~failed;
endmodule
