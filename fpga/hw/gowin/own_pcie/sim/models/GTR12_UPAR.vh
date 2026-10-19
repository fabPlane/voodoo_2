// UPAR model: 32-bit register file. A request is taken when ready is high;
// ready then drops for UPAR_BUSY cycles and a read returns with rdvld.
localparam UPAR_BUSY = 3;
reg [31:0] upar_mem [0:4095];
reg [31:0] rddata_r = 0;
reg        rdvld_r = 0, ready_r = 0;
reg [3:0]  busy = 0;
reg        rd_pend = 0;
reg [23:0] rd_addr = 0;
integer    upar_writes = 0, upar_reads = 0;
integer    ui;
initial for (ui = 0; ui < 4096; ui = ui + 1) upar_mem[ui] = 32'hDEAD0000 | ui;
function [11:0] upar_idx(input [23:0] a);
    upar_idx = a[13:2] ^ {a[23:22], a[21:12]};
endfunction
always @(posedge UPAR_CLK) begin
    rdvld_r <= 1'b0;
    if (UPAR_RST) begin
        ready_r <= 1'b0; busy <= 0; rd_pend <= 0;
    end else if (busy != 0) begin
        busy <= busy - 1'b1;
        if (busy == 1) begin
            ready_r <= 1'b1;
            if (rd_pend) begin
                rdvld_r  <= 1'b1;
                rddata_r <= upar_mem[upar_idx(rd_addr)];
                rd_pend  <= 1'b0;
            end
        end
    end else begin
        ready_r <= 1'b1;
        if (ready_r && (UPAR_WREN_S || UPAR_RDEN_S)) begin
            if (UPAR_WREN_S) begin
                upar_mem[upar_idx(UPAR_ADDR_S)] <= UPAR_WRDATA_S;
                upar_writes = upar_writes + 1;
                $display("%t UPAR wr %06h = %08h strb %02h", $time, UPAR_ADDR_S, UPAR_WRDATA_S, UPAR_STRB_S);
            end else begin
                rd_pend <= 1'b1; rd_addr <= UPAR_ADDR_S;
                upar_reads = upar_reads + 1;
            end
            ready_r <= 1'b0;
            busy    <= UPAR_BUSY;
        end
    end
end
assign UPAR_READY_S  = ready_r;
assign UPAR_RDVLD_S  = rdvld_r;
assign UPAR_RDDATA_S = rddata_r;
