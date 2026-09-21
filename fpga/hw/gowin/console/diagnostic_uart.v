// Minimal bring-up console deliberately isolated from DDR and the 75 MHz core.
// It runs entirely from the board's 50 MHz oscillator and therefore remains
// useful when DDR calibration never releases the main design from reset.
module diagnostic_uart (
    input  wire        clk,
    input  wire        enable,
    input  wire        calib,
    input  wire        wedged,
    input  wire        mode_n,
    input  wire        reset_n,
    input  wire        uart_link,
    input  wire [7:0]  uart_cmd,
    input  wire [15:0] uart_rx_count,
    input  wire [15:0] uart_hid_count,
    output wire        tx,
    output wire        busy
);
    localparam integer LINE_LEN = 59;
    localparam [8*LINE_LEN-1:0] TEMPLATE =
        "V2BOOT dbg=1 cal=0 wed=0 key=00 uart=0/00 rx=0000 hc=0000\r\n";

    reg calib_meta, calib_sync;
    reg wedged_meta, wedged_sync;
    reg mode_meta, mode_sync;
    reg reset_meta, reset_sync;
    always @(posedge clk) begin
        calib_meta <= calib;
        calib_sync <= calib_meta;
        wedged_meta <= wedged;
        wedged_sync <= wedged_meta;
        mode_meta <= ~mode_n;
        mode_sync <= mode_meta;
        reset_meta <= ~reset_n;
        reset_sync <= reset_meta;
    end

    function automatic [7:0] hex_digit(input [3:0] value);
        hex_digit = value < 10 ? (8'h30 + value) : (8'h41 + value - 10);
    endfunction

    reg [5:0] index = 0;
    reg [24:0] wait_count = 0;
    reg emitting = 0;
    reg [7:0] tx_data = 0;
    reg tx_start = 0;
    wire tx_busy;
    wire [7:0] template_char = TEMPLATE[8*(LINE_LEN-index)-1 -: 8];

    reg [7:0] selected_char;
    always @* begin
        selected_char = template_char;
        case (index)
            17: selected_char = calib_sync ? "1" : "0";
            23: selected_char = wedged_sync ? "1" : "0";
            29: selected_char = mode_sync ? "1" : "0";
            30: selected_char = reset_sync ? "1" : "0";
            37: selected_char = uart_link ? "1" : "0";
            39: selected_char = hex_digit(uart_cmd[7:4]);
            40: selected_char = hex_digit(uart_cmd[3:0]);
            45: selected_char = hex_digit(uart_rx_count[15:12]);
            46: selected_char = hex_digit(uart_rx_count[11:8]);
            47: selected_char = hex_digit(uart_rx_count[7:4]);
            48: selected_char = hex_digit(uart_rx_count[3:0]);
            53: selected_char = hex_digit(uart_hid_count[15:12]);
            54: selected_char = hex_digit(uart_hid_count[11:8]);
            55: selected_char = hex_digit(uart_hid_count[7:4]);
            56: selected_char = hex_digit(uart_hid_count[3:0]);
            default: ;
        endcase
    end

    always @(posedge clk) begin
        tx_start <= 1'b0;
        if (!enable) begin
            index <= 0;
            wait_count <= 0;
            emitting <= 1'b1;
        end else if (emitting) begin
            if (!tx_busy) begin
                tx_data <= selected_char;
                tx_start <= 1'b1;
                if (index == LINE_LEN - 1) begin
                    index <= 0;
                    emitting <= 1'b0;
                    wait_count <= 0;
                end else begin
                    index <= index + 1'b1;
                end
            end
        end else if (wait_count == 25'd24_999_999) begin
            wait_count <= 0;
            emitting <= 1'b1;
        end else begin
            wait_count <= wait_count + 1'b1;
        end
    end

    async_transmitter #(
        .ClkFrequency(50_000_000),
        .Baud(2_000_000)
    ) transmitter (
        .clk(clk),
        .TxD_start(tx_start),
        .TxD_data(tx_data),
        .TxD(tx),
        .TxD_busy(tx_busy)
    );
    assign busy = tx_busy;
endmodule
