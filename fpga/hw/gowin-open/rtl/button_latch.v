// Diagnostic DFF test with no LUT feedback or reset path.
// Release RESET once to latch a one until the FPGA is reconfigured.  Starting
// from zero deliberately selects the reset-initialized DFF primitive, avoiding
// the set-initialized primitive used by the first version of this test.
// MODE is deliberately avoided because the board also uses it during
// reconfiguration.
module top (
    input  wire key1_n,
    output wire [1:0] led_n
);
    reg state = 1'b0;

    always @(posedge key1_n)
        state <= 1'b1;

    // Complementary outputs make the latched state a color change rather
    // than relying on brightness or on both channels turning off together.
    assign led_n = {state, ~state};
endmodule
