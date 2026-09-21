`timescale 1ns/1ps

module iosys_hid_tb;
    reg clk = 0;
    reg resetn = 0;
    reg uart_rx = 1;
    wire uart_tx;
    wire [15:0] hid1;
    wire [15:0] hid2;
    wire link_seen;
    wire [7:0] last_cmd;
    wire [15:0] rx_count;
    wire [15:0] hid_count;
    reg [15:0] joy1 = 16'd0;
    reg [15:0] joy2 = 16'd0;

    always #10 clk = ~clk;

    iosys_hid #(.FREQ(50_000_000), .CORE_ID(8'd1)) dut (
        .clk(clk),
        .resetn(resetn),
        .uart_rx(uart_rx),
        .uart_tx(uart_tx),
        .joy1(joy1),
        .joy2(joy2),
        .hid1(hid1),
        .hid2(hid2),
        .link_seen(link_seen),
        .last_cmd(last_cmd),
        .rx_count(rx_count),
        .hid_count(hid_count)
    );

    task send_byte(input [7:0] value);
        integer bit_index;
        begin
            uart_rx = 0;
            repeat (25) @(posedge clk);
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                uart_rx = value[bit_index];
                repeat (25) @(posedge clk);
            end
            uart_rx = 1;
            repeat (25) @(posedge clk);
        end
    endtask

    // Decode the FPGA's 2 Mbaud, 8N2 transmit stream exactly as the BL616
    // does.  The old test only exercised uart_rx and therefore missed broken
    // core-ID/config/joypad replies.
    task recv_byte(output [7:0] value);
        integer bit_index;
        reg [7:0] sampled;
        begin
            @(negedge uart_tx);
            repeat (12) @(posedge clk); // middle of start bit
            if (uart_tx !== 1'b0)
                $fatal(1, "bad UART start bit");
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                repeat (25) @(posedge clk);
                sampled[bit_index] = uart_tx;
            end
            repeat (25) @(posedge clk);
            if (uart_tx !== 1'b1)
                $fatal(1, "bad UART first stop bit");
            repeat (25) @(posedge clk);
            if (uart_tx !== 1'b1)
                $fatal(1, "bad UART second stop bit");
            value = sampled;
        end
    endtask

    task expect_byte(input [7:0] expected);
        reg [7:0] actual;
        begin
            recv_byte(actual);
            if (actual !== expected)
                $fatal(1, "UART TX mismatch: expected=%02h actual=%02h", expected, actual);
        end
    endtask

    task send_query(input [7:0] command);
        begin
            send_byte(8'haa);
            send_byte(8'h00);
            send_byte(8'h01);
            send_byte(command);
        end
    endtask

    task send_hid(input [15:0] first, input [15:0] second);
        begin
            send_byte(8'haa);
            send_byte(8'h00);
            send_byte(8'h05);
            send_byte(8'h09);
            send_byte(first[15:8]);
            send_byte(first[7:0]);
            send_byte(second[15:8]);
            send_byte(second[7:0]);
        end
    endtask

    initial begin
        repeat (12) @(posedge clk);
        resetn = 1;
        repeat (20) @(posedge clk);

        // TangCore first identifies the loaded core, then reads its config.
        // Verify the complete replies, including length and command fields.
        fork
            send_query(8'h01);
            begin
                expect_byte(8'haa);
                expect_byte(8'h00);
                expect_byte(8'h02);
                expect_byte(8'h01);
                expect_byte(8'h01);
            end
        join

        fork
            send_query(8'h02);
            begin : config_reply
                integer config_index;
                reg [7:0] config_byte;
                reg [8*73-1:0] expected_config;
                expected_config = "Tangcores;-;O12,OSD key,Right+Select,Select+Start,Select+RB;-;V,v20240101";
                expect_byte(8'haa);
                expect_byte(8'h00);
                expect_byte(8'h4a);
                expect_byte(8'h02);
                for (config_index = 0; config_index < 73; config_index = config_index + 1) begin
                    recv_byte(config_byte);
                    if (config_byte !== expected_config[8*(73-config_index-1) +: 8])
                        $fatal(1, "config byte %0d mismatch: expected=%02h actual=%02h",
                               config_index, expected_config[8*(73-config_index-1) +: 8], config_byte);
                end
            end
        join

        // This is the board-top retry chord.  It must reach the MCU as a
        // cmd=3 joypad frame so firmware can close the OSD and start cmd=9 HID.
        fork
            begin
                joy1 = 16'h0084; // Select + Right in TangCore's SNES layout
            end
            begin
                expect_byte(8'haa);
                expect_byte(8'h00);
                expect_byte(8'h05);
                expect_byte(8'h03);
                expect_byte(8'h00);
                expect_byte(8'h84);
                expect_byte(8'h00);
                expect_byte(8'h00);
            end
        join

        send_hid(16'h0110, 16'h0002);
        repeat (100) @(posedge clk);
        if (hid1 !== 16'h0110 || hid2 !== 16'h0002 || hid_count !== 16'h0001)
            $fatal(1, "first HID frame failed: hid1=%h hid2=%h count=%h", hid1, hid2, hid_count);

        // Leave a frame incomplete. The watchdog must recover and recognize
        // the next complete frame instead of remaining stuck in RECV_PARAM.
        send_byte(8'haa);
        send_byte(8'h00);
        send_byte(8'h05);
        send_byte(8'h09);
        send_byte(8'hff);
        repeat (65_700) @(posedge clk);
        send_hid(16'h0020, 16'h0100);
        repeat (100) @(posedge clk);
        if (hid1 !== 16'h0020 || hid2 !== 16'h0100 || hid_count !== 16'h0003)
            $fatal(1, "watchdog recovery failed: hid1=%h hid2=%h count=%h", hid1, hid2, hid_count);

        $display("iosys_hid_tb PASS rx=%0d hid=%0d", rx_count, hid_count);
        $finish;
    end
endmodule
