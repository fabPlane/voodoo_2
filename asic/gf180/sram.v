// $mem_v2 onto the foundry's single-port 512x8 SRAM macro
// (gf180mcu_fd_ip_sram__sram512x8m8wm1), one macro per byte lane. Only for
// memories whose read and write never fall in one cycle (the texture cache
// data banks with Config.texCacheSinglePort): a write takes the port, a read
// is a synchronous read with enable (the macro's Q holds otherwise). Up to
// 512 words, widths in whole bytes, one write and one synchronous read port.
(* techmap_celltype = "$mem_v2" *)
module _mem_sram512 (RD_CLK, RD_EN, RD_ARST, RD_SRST, RD_ADDR, RD_DATA, WR_CLK, WR_EN, WR_ADDR, WR_DATA);
parameter MEMID = "";
parameter signed SIZE = 4;
parameter signed OFFSET = 0;
parameter signed ABITS = 2;
parameter signed WIDTH = 8;
parameter signed INIT = 1'bx;
parameter signed RD_PORTS = 1;
parameter RD_CLK_ENABLE = 1'b1;
parameter RD_CLK_POLARITY = 1'b1;
parameter RD_TRANSPARENCY_MASK = 1'b0;
parameter RD_COLLISION_X_MASK = 1'b0;
parameter RD_WIDE_CONTINUATION = 1'b0;
parameter RD_CE_OVER_SRST = 1'b0;
parameter RD_ARST_VALUE = 1'b0;
parameter RD_SRST_VALUE = 1'b0;
parameter RD_INIT_VALUE = 1'b0;
parameter signed WR_PORTS = 1;
parameter WR_CLK_ENABLE = 1'b1;
parameter WR_CLK_POLARITY = 1'b1;
parameter WR_PRIORITY_MASK = 1'b0;
parameter WR_WIDE_CONTINUATION = 1'b0;

input [RD_PORTS-1:0] RD_CLK, RD_EN, RD_ARST, RD_SRST;
input [RD_PORTS*ABITS-1:0] RD_ADDR;
output [RD_PORTS*WIDTH-1:0] RD_DATA;
input [WR_PORTS-1:0] WR_CLK;
input [WR_PORTS*WIDTH-1:0] WR_EN;
input [WR_PORTS*ABITS-1:0] WR_ADDR;
input [WR_PORTS*WIDTH-1:0] WR_DATA;

wire _TECHMAP_FAIL_ = WR_PORTS != 1 || RD_PORTS != 1 || !RD_CLK_ENABLE[0] || WIDTH % 8 != 0 ||
  SIZE + OFFSET > 512 || ABITS > 9;

wire we = |WR_EN;
wire [8:0] a = we ? WR_ADDR : RD_ADDR;
genvar b;
for (b = 0; b < WIDTH / 8; b = b + 1) begin : lane
  gf180mcu_fd_ip_sram__sram512x8m8wm1 m (
    .CLK(WR_CLK[0]), .CEN(~(we | RD_EN[0])), .GWEN(~we), .WEN(~WR_EN[b * 8 +: 8]),
    .A(a), .D(WR_DATA[b * 8 +: 8]), .Q(RD_DATA[b * 8 +: 8]));
end
endmodule
