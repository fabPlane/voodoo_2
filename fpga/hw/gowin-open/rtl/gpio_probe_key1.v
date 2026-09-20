module top (
    input  wire       key1_n,
    output wire [1:0] led_n
);
    // Active-low LEDs: the second case button selects one color when pressed
    // and the other when released.  This isolates blinky's reset input from
    // every clocked resource.
    assign led_n[0] = key1_n;
    assign led_n[1] = ~key1_n;
endmodule
