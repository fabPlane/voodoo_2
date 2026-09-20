module top (
    input  wire       key0_n,
    output wire [1:0] led_n
);
    // Active-low LEDs: released USER selects one color, pressed selects the
    // other.  This intentionally uses no clock or state.
    assign led_n[0] = key0_n;
    assign led_n[1] = ~key0_n;
endmodule
