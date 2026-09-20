// Sequential-routing diagnostic with no feedback loop.
// RESET releases shift ones through two flip-flops: 00 -> 01 -> 11.
module top (
    input  wire       key1_n,
    output wire [1:0] led_n
);
    reg [1:0] state = 2'b00;

    always @(posedge key1_n)
        state <= {state[0], 1'b1};

    assign led_n = state;
endmodule
