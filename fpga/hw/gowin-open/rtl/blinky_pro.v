// Open-flow smoke test for the Sipeed Tang Mega 138K Pro Dock
// (GW5AST-LV138FPG676A). Four active-low LEDs show a 50 MHz counter.
module top (
    input  wire       clk50,
    output wire [3:0] led_n
);
    reg [26:0] cnt = 27'd0;
    always @(posedge clk50) cnt <= cnt + 27'd1;
    assign led_n = ~cnt[26:23];
endmodule
