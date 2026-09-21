// TangCore BL616 UART, trimmed from NESTang iosys_bl616.v (nand2mario).
// Commands 0x01/0x02 (core id / config) and 0x09 (USB HID pads).
// UART TX is 8N2 via uart_fixed.v — the MCU will not parse 8N1 replies.
// joy1/joy2 are DS2/SNES pads reported *to* the MCU. Do not OR hid into them
// (nestang_top feeds hid only into the core, not back over UART).

module iosys_hid #(
    parameter integer FREQ = 50_000_000,
    parameter [7:0]   CORE_ID = 8'd1
) (
    input  wire        clk,
    input  wire        resetn,
    input  wire        uart_rx,
    output wire        uart_tx,
    input  wire [15:0] joy1,
    input  wire [15:0] joy2,
    output reg  [15:0] hid1,
    output reg  [15:0] hid2,
    output reg         link_seen,
    output reg  [7:0]  last_cmd,
    output reg  [15:0] rx_count,
    output reg  [15:0] hid_count
);

    // Same OSD-key config string nestang sends. TangCore parses this.
    localparam integer STR_LEN = 73;
    localparam [8*STR_LEN-1:0] CONF_STR = "Tangcores;-;O12,OSD key,Right+Select,Select+Start,Select+RB;-;V,v20240101";
    localparam integer CLK_FREQ = FREQ;
    localparam integer BAUD_RATE = 2_000_000;
    localparam integer JOY_UPDATE_INTERVAL = 50_000_000 / 50;

    wire [7:0] rx_data;
    wire       rx_valid;
    wire       tx_busy;
    wire       tx_ready = ~tx_busy;
    reg  [7:0] tx_data;
    reg        tx_valid;

    reg uart_rx_r = 1'b1, uart_rx_rr = 1'b1;
    always @(posedge clk) begin
        uart_rx_r  <= uart_rx;
        uart_rx_rr <= uart_rx_r;
    end

    async_receiver #(
        .ClkFrequency(CLK_FREQ),
        .Baud(BAUD_RATE)
    ) uart_receiver (
        .clk(clk),
        .RxD(uart_rx_rr),
        .RxD_data(rx_data),
        .RxD_data_ready(rx_valid)
    );

    async_transmitter #(
        .ClkFrequency(CLK_FREQ),
        .Baud(BAUD_RATE)
    ) uart_transmitter (
        .clk(clk),
        .TxD(uart_tx),
        .TxD_data(tx_data),
        .TxD_start(tx_valid),
        .TxD_busy(tx_busy)
    );

    localparam RECV_IDLE         = 7'b0000001;
    localparam RECV_LEN1         = 7'b0000010;
    localparam RECV_LEN2         = 7'b0000100;
    localparam RECV_CMD          = 7'b0001000;
    localparam RECV_PARAM        = 7'b0010000;
    localparam RECV_RESPONSE_REQ = 7'b0100000;
    localparam RECV_RESPONSE_ACK = 7'b1000000;
    reg [6:0] recv_state = RECV_IDLE;

    reg [7:0]  cmd_reg;
    reg [15:0] len_reg;
    reg [15:0] data_cnt;
    reg [7:0]  response_type;
    reg        response_req;
    reg        response_ack;
    reg [15:0] rx_gap;

    always @(posedge clk) begin
        if (!resetn) begin
            recv_state <= RECV_IDLE;
            cmd_reg <= 8'd0;
            len_reg <= 16'd0;
            hid1 <= 16'd0;
            hid2 <= 16'd0;
            data_cnt <= 16'd0;
            response_type <= 8'd0;
            response_req <= 1'b0;
            rx_gap <= 16'd0;
            link_seen <= 1'b0;
            last_cmd <= 8'd0;
            rx_count <= 16'd0;
            hid_count <= 16'd0;
        end else begin
            // A dropped UART byte must not strand the parser in the middle of
            // a frame forever. At 50 MHz this is about a 1.3 ms quiet timeout;
            // a valid 2 Mbaud frame never comes close to it.
            if (recv_state == RECV_IDLE || rx_valid)
                rx_gap <= 16'd0;
            else if (&rx_gap) begin
                recv_state <= RECV_IDLE;
                rx_gap <= 16'd0;
            end else
                rx_gap <= rx_gap + 16'd1;

            if (rx_valid) begin
                link_seen <= 1'b1;
                rx_count <= rx_count + 16'd1;
            end
            case (recv_state)
                RECV_IDLE: if (rx_valid && rx_data == 8'hAA) recv_state <= RECV_LEN1;

                RECV_LEN1: if (rx_valid) begin
                    len_reg[15:8] <= rx_data;
                    recv_state <= (rx_data < 8) ? RECV_LEN2 : RECV_IDLE;
                end

                RECV_LEN2: if (rx_valid) begin
                    len_reg[7:0] <= rx_data;
                    recv_state <= RECV_CMD;
                end

                RECV_CMD: if (rx_valid) begin
                    cmd_reg <= rx_data;
                    last_cmd <= rx_data;
                    if (rx_data == 8'd9)
                        hid_count <= hid_count + 16'd1;
                    data_cnt <= 16'd0;
                    if (rx_data == 8'd1 || rx_data == 8'd2)
                        recv_state <= RECV_RESPONSE_REQ;
                    else if (len_reg > 16'd1)
                        recv_state <= RECV_PARAM;
                    else
                        recv_state <= RECV_IDLE;
                end

                RECV_PARAM: if (rx_valid) begin
                    data_cnt <= data_cnt + 16'd1;
                    if (data_cnt + 16'd2 == len_reg)
                        recv_state <= RECV_IDLE;
                    if (cmd_reg == 8'd9) begin
                        case (data_cnt)
                            16'd0: hid1[15:8] <= rx_data;
                            16'd1: hid1[7:0]  <= rx_data;
                            16'd2: hid2[15:8] <= rx_data;
                            16'd3: hid2[7:0]  <= rx_data;
                            default: ;
                        endcase
                    end
                end

                RECV_RESPONSE_REQ: begin
                    if (cmd_reg == 8'd1 || cmd_reg == 8'd2) begin
                        response_type <= cmd_reg;
                        response_req <= ~response_req;
                        recv_state <= RECV_RESPONSE_ACK;
                    end else
                        recv_state <= RECV_IDLE;
                end

                RECV_RESPONSE_ACK:
                    if (response_req == response_ack)
                        recv_state <= RECV_IDLE;
                default: recv_state <= RECV_IDLE;
            endcase
        end
    end

    localparam SEND_IDLE          = 3'd0;
    localparam SEND_CORE_ID       = 3'd1;
    localparam SEND_CONFIG_STRING = 3'd2;
    localparam SEND_JOYPAD        = 3'd3;
    localparam SEND_HEADER        = 3'd6;

    reg [2:0] send_state, send_state_next;
    reg [$clog2(STR_LEN+1)-1:0] send_idx;
    reg [$clog2(JOY_UPDATE_INTERVAL+1)-1:0] joy_timer;
    reg [15:0] joy1_reg;
    reg [15:0] joy2_reg;
    reg [15:0] resp_frame_len;

    always @(posedge clk) begin
        if (!resetn) begin
            send_state <= SEND_IDLE;
            send_state_next <= SEND_IDLE;
            send_idx <= 0;
            tx_valid <= 1'b0;
            tx_data <= 8'd0;
            response_ack <= 1'b0;
            joy_timer <= 0;
            joy1_reg <= 16'd0;
            joy2_reg <= 16'd0;
            resp_frame_len <= 16'd0;
        end else begin
            tx_valid <= 1'b0;
            joy_timer <= (joy_timer == 0) ? 0 : joy_timer - 1'd1;
            case (send_state)
                SEND_IDLE: begin
                    send_idx <= 0;
                    if (joy_timer == 0 && (joy1 != joy1_reg || joy2 != joy2_reg)) begin
                        joy_timer <= JOY_UPDATE_INTERVAL;
                        joy1_reg <= joy1;
                        joy2_reg <= joy2;
                        send_state_next <= SEND_JOYPAD;
                        send_state <= SEND_HEADER;
                        resp_frame_len <= 16'd5;
                    end else if (response_req != response_ack) begin
                        if (response_type == 8'd2) begin
                            send_state_next <= SEND_CONFIG_STRING;
                            send_state <= SEND_HEADER;
                            resp_frame_len <= STR_LEN + 16'd1;
                        end else if (response_type == 8'd1) begin
                            send_state_next <= SEND_CORE_ID;
                            send_state <= SEND_HEADER;
                            resp_frame_len <= 16'd2;
                        end
                    end
                end

                SEND_HEADER: if (tx_ready && ~tx_valid) begin
                    tx_valid <= 1'b1;
                    send_idx <= send_idx + 1'd1;
                    case (send_idx[1:0])
                        2'd0: tx_data <= 8'hAA;
                        2'd1: tx_data <= resp_frame_len[15:8];
                        2'd2: tx_data <= resp_frame_len[7:0];
                        2'd3: begin
                            tx_data <= {5'd0, send_state_next};
                            send_state <= send_state_next;
                            send_idx <= 0;
                        end
                    endcase
                end

                SEND_CORE_ID: if (tx_ready && ~tx_valid) begin
                    tx_data <= CORE_ID;
                    tx_valid <= 1'b1;
                    send_state <= SEND_IDLE;
                    response_ack <= response_req;
                end

                SEND_CONFIG_STRING: if (tx_ready && ~tx_valid) begin
                    tx_data <= CONF_STR[8*(STR_LEN - send_idx - 1) +: 8];
                    tx_valid <= 1'b1;
                    send_idx <= send_idx + 1'd1;
                    if (send_idx == STR_LEN - 1) begin
                        send_state <= SEND_IDLE;
                        response_ack <= response_req;
                    end
                end

                SEND_JOYPAD: if (tx_ready && ~tx_valid) begin
                    case (send_idx)
                        0: tx_data <= joy1_reg[15:8];
                        1: tx_data <= joy1_reg[7:0];
                        2: tx_data <= joy2_reg[15:8];
                        3: tx_data <= joy2_reg[7:0];
                        default: ;
                    endcase
                    tx_valid <= 1'b1;
                    send_idx <= send_idx + 1'd1;
                    if (send_idx == 3) begin
                        send_state <= SEND_IDLE;
                    end
                end

                default: send_state <= SEND_IDLE;
            endcase
        end
    end

endmodule
