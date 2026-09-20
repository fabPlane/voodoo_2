// Minimal sequential-routing diagnostic.
// Each RESET release is a rising edge and must toggle the status-light color.
// This avoids both the MODE/configuration pin and the 50 MHz clock input.
module top (
    input  wire       key1_n,
    output wire [1:0] led_n
);
    reg state = 1'b0;

    always @(posedge key1_n)
        state <= ~state;

    assign led_n = {state, ~state};
endmodule
