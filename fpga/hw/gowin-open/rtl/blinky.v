module top (
    input  wire       clk50,
    input  wire       key0_n,
    input  wire       key1_n,
    output wire [1:0] led_n
);
    reg [25:0] counter = 0;

    always @(posedge clk50) begin
        if (!key1_n)
            counter <= 0;
        else
            counter <= counter + 1'b1;
    end

    // LEDs are active-low. LED 0 blinks; LED 1 follows the USER key.
    assign led_n[0] = ~counter[24];
    assign led_n[1] = key0_n;
endmodule
