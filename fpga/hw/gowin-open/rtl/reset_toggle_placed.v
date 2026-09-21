// Primitive-level oracle used to compare one identical GW5AST slice between
// the vendor and open flows.  Constraints place both cells at LUT2/DFF2.
module top (
    input  wire       key1_n,
    output wire [1:0] led_n
);
    wire state;
    wire next_state;

    LUT1 #(.INIT(2'b01)) u_inv (
        .I0(state),
        .F(next_state)
    );

    DFF u_state (
        .D(next_state),
        .CLK(key1_n),
        .Q(state)
    );

    assign led_n = {state, next_state};
endmodule
