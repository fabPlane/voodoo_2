module top (
    input  wire       clk50,
    input  wire       key0_n,
    input  wire       key1_n,
    input  wire       uart_rx,
    output reg        uart_tx = 1'b1,
    output wire [1:0] led_n
);
    localparam integer CLOCK_HZ = 50_000_000;
    localparam integer BAUD = 115_200;
    localparam integer DIVISOR = CLOCK_HZ / BAUD;
    localparam integer MESSAGE_LEN = 19;

    reg [8:0]  baud_count = 0;
    reg [3:0]  bit_index = 0;
    reg [7:0]  char_index = 0;
    reg [9:0]  shift = 10'h3ff;
    reg        sending = 0;
    reg [25:0] pause_count = 0;
    reg [25:0] heartbeat = 0;

    function automatic [7:0] message(input [7:0] index);
        case (index)
            0:  message = "O";
            1:  message = "P";
            2:  message = "E";
            3:  message = "N";
            4:  message = " ";
            5:  message = "G";
            6:  message = "O";
            7:  message = "W";
            8:  message = "I";
            9:  message = "N";
            10: message = " ";
            11: message = "1";
            12: message = "3";
            13: message = "8";
            14: message = "K";
            15: message = " ";
            16: message = "O";
            17: message = "K";
            default: message = 8'h0a;
        endcase
    endfunction

    always @(posedge clk50) begin
        heartbeat <= heartbeat + 1'b1;

        if (!key1_n) begin
            uart_tx <= 1'b1;
            baud_count <= 0;
            bit_index <= 0;
            char_index <= 0;
            shift <= 10'h3ff;
            sending <= 0;
            pause_count <= 0;
        end else if (!sending) begin
            uart_tx <= 1'b1;
            if (!key0_n || pause_count == CLOCK_HZ - 1) begin
                pause_count <= 0;
                char_index <= 0;
                shift <= {1'b1, message(0), 1'b0};
                bit_index <= 0;
                baud_count <= 0;
                sending <= 1;
            end else begin
                pause_count <= pause_count + 1'b1;
            end
        end else begin
            uart_tx <= shift[0];
            if (baud_count == DIVISOR - 1) begin
                baud_count <= 0;
                shift <= {1'b1, shift[9:1]};
                if (bit_index == 9) begin
                    bit_index <= 0;
                    if (char_index == MESSAGE_LEN - 1) begin
                        sending <= 0;
                        uart_tx <= 1'b1;
                    end else begin
                        char_index <= char_index + 1'b1;
                        shift <= {1'b1, message(char_index + 1'b1), 1'b0};
                    end
                end else begin
                    bit_index <= bit_index + 1'b1;
                end
            end else begin
                baud_count <= baud_count + 1'b1;
            end
        end
    end

    // Keep the otherwise-unused RX input visible to synthesis.
    assign led_n[0] = ~heartbeat[24];
    assign led_n[1] = uart_rx;
endmodule
