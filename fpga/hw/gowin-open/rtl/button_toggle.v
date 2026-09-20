module top (
    input  wire       key0_n,
    output wire [1:0] led_n
);
    reg state = 1'b0;

    // Clock from the already-proven MODE/USER input.  This deliberately
    // avoids V22 and every global-clock resource.  Each button release is a
    // rising edge and should toggle both status-light channels together.
    always @(posedge key0_n)
        state <= ~state;

    assign led_n = {2{state}};
endmodule
