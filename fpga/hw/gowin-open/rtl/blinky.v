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

    // These two active-low outputs drive the Console's two status-light
    // channels. Alternate them so a running clock is visually unambiguous.
    assign led_n[0] =  counter[23];
    assign led_n[1] = ~counter[23];

    wire _unused = key0_n;
endmodule
