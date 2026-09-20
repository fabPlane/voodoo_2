module top (
    input  wire       clk50,
    output wire [1:0] led_n
);
    reg [24:0] counter = 0;

    always @(posedge clk50)
        counter <= counter + 1'b1;

    // About 3 Hz at 50 MHz.  Complementary channels make activity obvious.
    assign led_n[0] =  counter[23];
    assign led_n[1] = ~counter[23];
endmodule
