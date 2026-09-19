module top (
    input  wire       clk50,
    input  wire       key0_n,
    input  wire       key1_n,
    output wire [1:0] led_n
);
    localparam WRITE = 2'd0;
    localparam READ  = 2'd1;
    localparam CHECK = 2'd2;
    localparam DONE  = 2'd3;

    reg [31:0] memory [0:1023];
    reg [31:0] read_data = 0;
    reg [9:0]  address = 0;
    reg [1:0]  state = WRITE;
    reg        failed = 0;
    reg [25:0] heartbeat = 0;

    function automatic [31:0] pattern(input [9:0] a);
        pattern = {a, ~a, a[5:0], 6'h2d};
    endfunction

    always @(posedge clk50) begin
        heartbeat <= heartbeat + 1'b1;

        if (!key1_n) begin
            address <= 0;
            state <= WRITE;
            failed <= 0;
        end else begin
            case (state)
                WRITE: begin
                    memory[address] <= pattern(address);
                    if (address == 10'h3ff) begin
                        address <= 0;
                        state <= READ;
                    end else begin
                        address <= address + 1'b1;
                    end
                end
                READ: begin
                    read_data <= memory[address];
                    state <= CHECK;
                end
                CHECK: begin
                    if (read_data != pattern(address))
                        failed <= 1'b1;
                    if (address == 10'h3ff)
                        state <= DONE;
                    else begin
                        address <= address + 1'b1;
                        state <= READ;
                    end
                end
                default: begin
                    // USER reruns the test without disturbing the heartbeat.
                    if (!key0_n) begin
                        address <= 0;
                        state <= WRITE;
                        failed <= 0;
                    end
                end
            endcase
        end
    end

    // Passing: LED 0 blinks. Failure: LED 1 stays lit.
    assign led_n[0] = (state == DONE && !failed) ? ~heartbeat[24] : 1'b1;
    assign led_n[1] = ~failed;
endmodule
