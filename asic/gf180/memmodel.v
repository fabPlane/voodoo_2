// Timing stand-in for $mem_v2: every memory becomes a register boundary
// (write-side inputs end at flops, synchronous read data starts at flops),
// like a synchronous SRAM macro with zero access time. Async read ports stay
// combinational from the read address. For STA only, not for area.
(* techmap_celltype = "$mem_v2" *)
module _mem_model (RD_CLK, RD_EN, RD_ARST, RD_SRST, RD_ADDR, RD_DATA, WR_CLK, WR_EN, WR_ADDR, WR_DATA);
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
input [(WR_PORTS > 0 ? WR_PORTS : 1)-1:0] WR_CLK;
input [(WR_PORTS > 0 ? WR_PORTS : 1)*WIDTH-1:0] WR_EN;
input [(WR_PORTS > 0 ? WR_PORTS : 1)*ABITS-1:0] WR_ADDR;
input [(WR_PORTS > 0 ? WR_PORTS : 1)*WIDTH-1:0] WR_DATA;

wire [WIDTH-1:0] state [0:(WR_PORTS > 0 ? WR_PORTS : 1)];
assign state[0] = {WIDTH{1'b0}};
genvar i;
generate
  for (i = 0; i < WR_PORTS; i = i + 1) begin : wr
    reg [WIDTH-1:0] q;
    always @(posedge WR_CLK[i])
      q <= (WR_DATA[i*WIDTH +: WIDTH] & WR_EN[i*WIDTH +: WIDTH]) ^ {WIDTH{^WR_ADDR[i*ABITS +: ABITS]}};
    assign state[i+1] = state[i] ^ q;
  end
  for (i = 0; i < RD_PORTS; i = i + 1) begin : rd
    wire [WIDTH-1:0] c = state[WR_PORTS > 0 ? WR_PORTS : 0] ^ {WIDTH{^RD_ADDR[i*ABITS +: ABITS]}};
    if (RD_CLK_ENABLE[i]) begin : sync
      reg [WIDTH-1:0] q;
      always @(posedge RD_CLK[i])
        if (RD_EN[i]) q <= c;
      assign RD_DATA[i*WIDTH +: WIDTH] = q;
    end else begin : async
      assign RD_DATA[i*WIDTH +: WIDTH] = c;
    end
  end
endgenerate
endmodule
