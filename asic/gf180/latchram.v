// Area / routing stand-in for $mem_v2 (place-and-route fit check): every
// writable memory becomes a latch array (DFFRAM style). Write data, address
// and enables are registered; one clock gate (icgtp on the inverted clock)
// per word and byte opens that byte's latches in the low half of the next
// cycle; read ports are mux trees on the latch outputs, registered when the
// port is synchronous. Read-during-write order, reset values and enables
// finer than a byte are not modelled (not for simulation). ROMs (no write
// port) fail the map and become logic.
(* techmap_celltype = "$mem_v2" *)
module _mem_latch (RD_CLK, RD_EN, RD_ARST, RD_SRST, RD_ADDR, RD_DATA, WR_CLK, WR_EN, WR_ADDR, WR_DATA);
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

wire _TECHMAP_FAIL_ = WR_PORTS != 1;

// Clock gates per word and byte (the last group may be narrower).
localparam G = 8;
localparam NG = (WIDTH + G - 1) / G;

reg [WIDTH-1:0] wd;
reg [ABITS-1:0] wa;
reg [NG-1:0] wg;
wire nclk = ~WR_CLK[0];
genvar w, g, b, p;
for (g = 0; g < NG; g = g + 1) begin : en
  always @(posedge WR_CLK[0]) wg[g] <= WR_EN[g * G];
end
always @(posedge WR_CLK[0]) begin
  wd <= WR_DATA[WIDTH-1:0];
  wa <= WR_ADDR[ABITS-1:0];
end

wire [SIZE*WIDTH-1:0] q;
for (w = 0; w < SIZE; w = w + 1) begin : word
  wire sel = wa == w + OFFSET;
  for (g = 0; g < NG; g = g + 1) begin : grp
    wire gclk;
    gf180mcu_fd_sc_mcu7t5v0__icgtp_1 icg (.CLK(nclk), .E(sel & wg[g]), .TE(1'b0), .Q(gclk));
    for (b = 0; b < G && g * G + b < WIDTH; b = b + 1) begin : bit
      gf180mcu_fd_sc_mcu7t5v0__latq_1 l (.E(gclk), .D(wd[g * G + b]), .Q(q[w * WIDTH + g * G + b]));
    end
  end
end

// Read: a binary mux tree on the address bits (a variable part-select
// would become a barrel shifter for widths that are not powers of two).
localparam N = 1 << ABITS;
genvar k;
for (p = 0; p < RD_PORTS; p = p + 1) begin : rd
  wire [ABITS-1:0] a = RD_ADDR[p * ABITS +: ABITS] - OFFSET;
  wire [(2 * N - 1) * WIDTH - 1:0] t; // heap: node k's children 2k+1, 2k+2
  for (k = 0; k < N; k = k + 1) begin : leaf
    if (k < SIZE) assign t[(N - 1 + k) * WIDTH +: WIDTH] = q[k * WIDTH +: WIDTH];
    else assign t[(N - 1 + k) * WIDTH +: WIDTH] = {WIDTH{1'b0}};
  end
  for (k = 0; k < N - 1; k = k + 1) begin : node
    // depth of node k = floor(log2(k + 1)); it selects on address bit ABITS-1-depth
    assign t[k * WIDTH +: WIDTH] = a[ABITS - $clog2(k + 2)] ? t[(2 * k + 2) * WIDTH +: WIDTH] : t[(2 * k + 1) * WIDTH +: WIDTH];
  end
  wire [WIDTH-1:0] d = t[0 +: WIDTH];
  if (RD_CLK_ENABLE[p]) begin : sync
    reg [WIDTH-1:0] r;
    always @(posedge RD_CLK[p]) if (RD_EN[p]) r <= d;
    assign RD_DATA[p * WIDTH +: WIDTH] = r;
  end else begin : async
    assign RD_DATA[p * WIDTH +: WIDTH] = d;
  end
end
endmodule
