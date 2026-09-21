// 32 MiB x16 SDR-SDRAM line controller for the Tang Console 138K.
//
// The front end intentionally mirrors the small subset of Gowin's native
// DDR3 UI used by TangConsole138k.  One command transfers one aligned
// 128-bit line as an SDRAM BL8 burst.  This lets the existing AXI/native
// adapter and bundled-data clock crossing be reused unchanged while the
// DDR3 PHY is removed from the experiment.
module sdram_line_controller (
    input  wire         clk,
    input  wire         reset,

    output wire         cmd_ready,
    input  wire [2:0]   cmd,
    input  wire         cmd_en,
    input  wire [27:0]  addr,
    output wire         wr_data_rdy,
    input  wire [127:0] wr_data,
    input  wire         wr_data_en,
    input  wire         wr_data_end,
    input  wire [15:0]  wr_data_mask,
    output reg  [127:0] rd_data,
    output reg          rd_data_valid,
    output reg          rd_data_end,
    output reg          init_complete,

    output wire [12:0]  O_sdram_addr,
    output wire [1:0]   O_sdram_ba,
    output wire         O_sdram_cs_n,
    output wire         O_sdram_ras_n,
    output wire         O_sdram_cas_n,
    output wire         O_sdram_wen_n,
    output wire         O_sdram_cke,
    output wire [1:0]   O_sdram_dqm,
    inout  wire [15:0]  IO_sdram_dq
);

    localparam [2:0] CMD_WRITE = 3'd0;
    localparam [2:0] CMD_READ  = 3'd1;

    // {CS#, RAS#, CAS#, WE#}
    localparam [3:0] SDR_NOP       = 4'b0111;
    localparam [3:0] SDR_MRS       = 4'b0000;
    localparam [3:0] SDR_REFRESH   = 4'b0001;
    localparam [3:0] SDR_PRECHARGE = 4'b0010;
    localparam [3:0] SDR_ACTIVE    = 4'b0011;
    localparam [3:0] SDR_WRITE     = 4'b0100;
    localparam [3:0] SDR_READ      = 4'b0101;

    localparam [4:0] ST_INIT_WAIT     = 5'd0;
    localparam [4:0] ST_INIT_PRE      = 5'd1;
    localparam [4:0] ST_INIT_REF1     = 5'd2;
    localparam [4:0] ST_INIT_REF2     = 5'd3;
    localparam [4:0] ST_INIT_MRS      = 5'd4;
    localparam [4:0] ST_IDLE          = 5'd5;
    localparam [4:0] ST_ACT_WAIT      = 5'd6;
    localparam [4:0] ST_WRITE_CMD     = 5'd7;
    localparam [4:0] ST_WRITE_BURST   = 5'd8;
    localparam [4:0] ST_READ_CMD      = 5'd9;
    localparam [4:0] ST_READ_LATENCY  = 5'd10;
    localparam [4:0] ST_READ_BURST    = 5'd11;
    localparam [4:0] ST_RECOVERY      = 5'd12;
    localparam [4:0] ST_REFRESH_WAIT  = 5'd13;

    reg [4:0] state;
    reg [14:0] init_count;
    reg [3:0] delay_count;
    reg [9:0] refresh_count;
    reg refresh_pending;
    reg pending_write;
    reg [23:0] line_addr;
    reg [127:0] write_line;
    reg [15:0] write_mask;
    reg [127:0] read_line;
    reg [2:0] burst_index;

    reg [3:0] sdram_cmd;
    reg [12:0] sdram_addr;
    reg [1:0] sdram_ba;
    reg [1:0] sdram_dqm;
    reg [15:0] dq_out;
    reg dq_drive;

    wire [15:0] dq_in = IO_sdram_dq;
    assign IO_sdram_dq = dq_drive ? dq_out : 16'hzzzz;
    assign {O_sdram_cs_n, O_sdram_ras_n, O_sdram_cas_n, O_sdram_wen_n} = sdram_cmd;
    assign O_sdram_addr = sdram_addr;
    assign O_sdram_ba = sdram_ba;
    assign O_sdram_dqm = sdram_dqm;
    // Console PCB SDRAM CKE is FPGA pin M13.  It must be asserted for the
    // device to accept initialization, refresh, reads, or writes.
    assign O_sdram_cke = 1'b1;

    assign cmd_ready = init_complete && state == ST_IDLE && !refresh_pending;
    assign wr_data_rdy = cmd_ready;

    // At 60 MHz, 13,000 clocks is a conservative 216 us power-up delay.
    // Refresh every 468 clocks is the SDRAM's approximately 7.8 us tREFI.
    always @(posedge clk) begin
        if (reset) begin
            state <= ST_INIT_WAIT;
            init_count <= 15'd0;
            delay_count <= 4'd0;
            refresh_count <= 10'd0;
            refresh_pending <= 1'b0;
            pending_write <= 1'b0;
            line_addr <= 24'd0;
            write_line <= 128'd0;
            write_mask <= 16'hffff;
            read_line <= 128'd0;
            rd_data <= 128'd0;
            rd_data_valid <= 1'b0;
            rd_data_end <= 1'b0;
            init_complete <= 1'b0;
            burst_index <= 3'd0;
            sdram_cmd <= SDR_NOP;
            sdram_addr <= 13'd0;
            sdram_ba <= 2'd0;
            sdram_dqm <= 2'b11;
            dq_out <= 16'd0;
            dq_drive <= 1'b0;
        end else begin
            sdram_cmd <= SDR_NOP;
            sdram_dqm <= 2'b11;
            dq_drive <= 1'b0;
            rd_data_valid <= 1'b0;
            rd_data_end <= 1'b0;

            if (init_complete) begin
                // 468 clocks at 60 MHz is 7.8 us.
                if (refresh_count == 10'd467) begin
                    refresh_count <= 10'd0;
                    refresh_pending <= 1'b1;
                end else begin
                    refresh_count <= refresh_count + 1'b1;
                end
            end

            case (state)
                ST_INIT_WAIT: begin
                    if (init_count == 15'd13000) begin
                        sdram_cmd <= SDR_PRECHARGE;
                        sdram_addr <= 13'b001_0000000000; // A10=1: all banks
                        delay_count <= 4'd1;
                        state <= ST_INIT_PRE;
                    end else begin
                        init_count <= init_count + 1'b1;
                    end
                end

                ST_INIT_PRE: begin
                    if (delay_count == 0) begin
                        sdram_cmd <= SDR_REFRESH;
                        delay_count <= 4'd5;
                        state <= ST_INIT_REF1;
                    end else delay_count <= delay_count - 1'b1;
                end

                ST_INIT_REF1: begin
                    if (delay_count == 0) begin
                        sdram_cmd <= SDR_REFRESH;
                        delay_count <= 4'd5;
                        state <= ST_INIT_REF2;
                    end else delay_count <= delay_count - 1'b1;
                end

                ST_INIT_REF2: begin
                    if (delay_count == 0) begin
                        // BL8, sequential, CAS latency 2, programmed write burst.
                        sdram_cmd <= SDR_MRS;
                        sdram_ba <= 2'd0;
                        sdram_addr <= 13'h023;
                        delay_count <= 4'd1;
                        state <= ST_INIT_MRS;
                    end else delay_count <= delay_count - 1'b1;
                end

                ST_INIT_MRS: begin
                    if (delay_count == 0) begin
                        init_complete <= 1'b1;
                        refresh_count <= 10'd0;
                        state <= ST_IDLE;
                    end else delay_count <= delay_count - 1'b1;
                end

                ST_IDLE: begin
                    if (refresh_pending) begin
                        sdram_cmd <= SDR_REFRESH;
                        refresh_pending <= 1'b0;
                        refresh_count <= 10'd0;
                        delay_count <= 4'd5;
                        state <= ST_REFRESH_WAIT;
                    end else if (cmd_en && (cmd == CMD_READ || (cmd == CMD_WRITE && wr_data_en))) begin
                        // Gowin-native addresses are x16 physical word addresses.
                        // The upstream adapter aligns each command to eight words.
                        line_addr <= addr[23:0];
                        pending_write <= cmd == CMD_WRITE;
                        write_line <= wr_data;
                        write_mask <= wr_data_mask;
                        sdram_ba <= addr[23:22];
                        sdram_addr <= addr[21:9];
                        sdram_cmd <= SDR_ACTIVE;
                        delay_count <= 4'd1; // tRCD = 2 clocks
                        state <= ST_ACT_WAIT;
                    end
                end

                ST_ACT_WAIT: begin
                    if (delay_count == 0) begin
                        burst_index <= 3'd0;
                        state <= pending_write ? ST_WRITE_CMD : ST_READ_CMD;
                    end else delay_count <= delay_count - 1'b1;
                end

                ST_WRITE_CMD: begin
                    sdram_cmd <= SDR_WRITE;
                    sdram_ba <= line_addr[23:22];
                    sdram_addr <= {2'b00, 1'b1, 1'b0, line_addr[8:0]};
                    dq_drive <= 1'b1;
                    dq_out <= write_line[15:0];
                    sdram_dqm <= write_mask[1:0];
                    burst_index <= 3'd1;
                    state <= ST_WRITE_BURST;
                end

                ST_WRITE_BURST: begin
                    dq_drive <= 1'b1;
                    case (burst_index)
                        3'd1: begin dq_out <= write_line[31:16];   sdram_dqm <= write_mask[3:2];   end
                        3'd2: begin dq_out <= write_line[47:32];   sdram_dqm <= write_mask[5:4];   end
                        3'd3: begin dq_out <= write_line[63:48];   sdram_dqm <= write_mask[7:6];   end
                        3'd4: begin dq_out <= write_line[79:64];   sdram_dqm <= write_mask[9:8];   end
                        3'd5: begin dq_out <= write_line[95:80];   sdram_dqm <= write_mask[11:10]; end
                        3'd6: begin dq_out <= write_line[111:96];  sdram_dqm <= write_mask[13:12]; end
                        default: begin dq_out <= write_line[127:112]; sdram_dqm <= write_mask[15:14]; end
                    endcase
                    if (burst_index == 3'd7) begin
                        delay_count <= 4'd3; // tWR + tRP margin
                        state <= ST_RECOVERY;
                    end else burst_index <= burst_index + 1'b1;
                end

                ST_READ_CMD: begin
                    sdram_cmd <= SDR_READ;
                    sdram_ba <= line_addr[23:22];
                    sdram_addr <= {2'b00, 1'b1, 1'b0, line_addr[8:0]};
                    // DQM is active-high.  Keep both byte lanes unmasked from
                    // the READ command through the end of the CL2/BL8 data.
                    sdram_dqm <= 2'b00;
                    delay_count <= 4'd1;
                    burst_index <= 3'd0;
                    state <= ST_READ_LATENCY;
                end

                ST_READ_LATENCY: begin
                    sdram_dqm <= 2'b00;
                    if (delay_count == 0) state <= ST_READ_BURST;
                    else delay_count <= delay_count - 1'b1;
                end

                ST_READ_BURST: begin
                    sdram_dqm <= 2'b00;
                    case (burst_index)
                        3'd0: read_line[15:0] <= dq_in;
                        3'd1: read_line[31:16] <= dq_in;
                        3'd2: read_line[47:32] <= dq_in;
                        3'd3: read_line[63:48] <= dq_in;
                        3'd4: read_line[79:64] <= dq_in;
                        3'd5: read_line[95:80] <= dq_in;
                        3'd6: read_line[111:96] <= dq_in;
                        default: read_line[127:112] <= dq_in;
                    endcase
                    if (burst_index == 3'd7) begin
                        rd_data <= {dq_in, read_line[111:0]};
                        rd_data_valid <= 1'b1;
                        rd_data_end <= 1'b1;
                        delay_count <= 4'd2; // auto-precharge tRP margin
                        state <= ST_RECOVERY;
                    end else burst_index <= burst_index + 1'b1;
                end

                ST_RECOVERY: begin
                    // Read DQM is pipelined by CAS latency.  Hold it unmasked
                    // during recovery so the tail of BL8 is not suppressed.
                    if (!pending_write) sdram_dqm <= 2'b00;
                    if (delay_count == 0) state <= ST_IDLE;
                    else delay_count <= delay_count - 1'b1;
                end

                ST_REFRESH_WAIT: begin
                    if (delay_count == 0) state <= ST_IDLE;
                    else delay_count <= delay_count - 1'b1;
                end

                default: state <= ST_INIT_WAIT;
            endcase
        end
    end

    wire _unused = &{1'b0, wr_data_end, addr[27:24]};

endmodule
