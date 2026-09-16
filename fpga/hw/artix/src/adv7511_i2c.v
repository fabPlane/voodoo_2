// ADV7511 I2C init for the AMD AC701.
// First write selects PCA9548 channel 5 (HDMI, UG952), then Analog's
// 24-bit RGB 4:4:4 power-up into 7-bit address 0x39. Device bytes are
// 8-bit including R/W = 0. Mux writes are one payload byte; register
// writes are two.

module adv7511_i2c (
    input  wire clk,
    input  wire reset,
    inout  wire scl,
    inout  wire sda
);
    localparam [15:0] DIV = 16'd500; // ~200 kHz at 200 MHz; ~156 kHz at 156.25 MHz

    // {nbytes, dev, b0, b1}  nbytes=1 mux select, nbytes=2 reg+data
    localparam integer STEPS = 15;
    reg [31:0] step [0:STEPS-1];
    initial begin
        step[0]  = {8'd1, 8'hE8, 8'h20, 8'h00}; // PCA9548 0x74, CH5 HDMI
        step[1]  = {8'd2, 8'h72, 8'h41, 8'h10}; // power up
        step[2]  = {8'd2, 8'h72, 8'h98, 8'h03};
        step[3]  = {8'd2, 8'h72, 8'h9A, 8'hE0};
        step[4]  = {8'd2, 8'h72, 8'h9C, 8'h30};
        step[5]  = {8'd2, 8'h72, 8'h9D, 8'h61};
        step[6]  = {8'd2, 8'h72, 8'hA2, 8'hA4};
        step[7]  = {8'd2, 8'h72, 8'hA3, 8'hA4};
        step[8]  = {8'd2, 8'h72, 8'hE0, 8'hD0};
        step[9]  = {8'd2, 8'h72, 8'hF9, 8'h00};
        step[10] = {8'd2, 8'h72, 8'h15, 8'h00}; // 24-bit RGB 4:4:4
        step[11] = {8'd2, 8'h72, 8'h16, 8'h00};
        step[12] = {8'd2, 8'h72, 8'h18, 8'h46}; // CSC off
        step[13] = {8'd2, 8'h72, 8'hAF, 8'h04}; // HDMI mode
        step[14] = {8'd2, 8'h72, 8'hD6, 8'hC0}; // HPD override
    end

    localparam [2:0]
        ST_IDLE  = 3'd0,
        ST_START = 3'd1,
        ST_BYTE  = 3'd2,
        ST_ACK   = 3'd3,
        ST_STOP  = 3'd4,
        ST_DONE  = 3'd5,
        ST_WAIT  = 3'd6;

    reg [2:0]  state;
    reg [2:0]  byte_idx;
    reg [3:0]  step_idx;
    reg [7:0]  shift;
    reg [3:0]  bit_idx;
    reg [15:0] div;
    reg        scl_oe, sda_oe;
    reg        scl_q, sda_q;
    reg        tick;

    assign scl = scl_oe ? 1'b0 : 1'bz;
    assign sda = sda_oe ? 1'b0 : 1'bz;

    wire [7:0] nbytes = step[step_idx][31:24];
    wire [7:0] dev   = step[step_idx][23:16];
    wire [7:0] b0    = step[step_idx][15:8];
    wire [7:0] b1    = step[step_idx][7:0];

    always @(*) begin
        case (byte_idx)
            3'd0: shift = dev;
            3'd1: shift = b0;
            default: shift = b1;
        endcase
    end

    always @(posedge clk) begin
        if (reset) begin
            div <= 0;
            tick <= 1'b0;
        end else if (div == DIV) begin
            div <= 0;
            tick <= 1'b1;
        end else begin
            div <= div + 1'b1;
            tick <= 1'b0;
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            state <= ST_WAIT;
            step_idx <= 0;
            byte_idx <= 0;
            bit_idx <= 0;
            scl_oe <= 1'b0;
            sda_oe <= 1'b0;
            scl_q <= 1'b1;
            sda_q <= 1'b1;
        end else if (tick) begin
            case (state)
                ST_WAIT: begin
                    scl_oe <= 1'b0;
                    sda_oe <= 1'b0;
                    state <= ST_START;
                end
                ST_START: begin
                    sda_oe <= 1'b1;
                    scl_oe <= 1'b0;
                    byte_idx <= 0;
                    bit_idx <= 4'd7;
                    state <= ST_BYTE;
                end
                ST_BYTE: begin
                    scl_oe <= scl_q;
                    sda_oe <= ~shift[bit_idx];
                    if (scl_q == 1'b0) begin
                        scl_q <= 1'b1;
                    end else begin
                        scl_q <= 1'b0;
                        if (bit_idx == 0) begin
                            state <= ST_ACK;
                        end else begin
                            bit_idx <= bit_idx - 1'b1;
                        end
                    end
                end
                ST_ACK: begin
                    sda_oe <= 1'b0;
                    scl_oe <= scl_q;
                    if (scl_q == 1'b0) begin
                        scl_q <= 1'b1;
                    end else begin
                        scl_q <= 1'b0;
                        if (byte_idx == nbytes[2:0]) begin
                            state <= ST_STOP;
                        end else begin
                            byte_idx <= byte_idx + 1'b1;
                            bit_idx <= 4'd7;
                            state <= ST_BYTE;
                        end
                    end
                end
                ST_STOP: begin
                    scl_oe <= 1'b0;
                    sda_oe <= 1'b1;
                    state <= ST_IDLE;
                end
                ST_IDLE: begin
                    sda_oe <= 1'b0;
                    if (step_idx == STEPS - 1) begin
                        state <= ST_DONE;
                    end else begin
                        step_idx <= step_idx + 1'b1;
                        state <= ST_WAIT;
                    end
                end
                ST_DONE: begin
                    scl_oe <= 1'b0;
                    sda_oe <= 1'b0;
                end
                default: state <= ST_DONE;
            endcase
        end
    end
endmodule
