"""Speed up the extracted GW2A DQS model: replace its three 256-tap
#0.025 delay chains with one transport delay each (same behaviour)."""
import re, sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
chain = re.search(r"assign dqsw0_dly_in\[0\].*?endgenerate\n", s, re.S)
assert chain, "delay chain not found"
s = s[:chain.start()] + """reg dqsw0_d = 0, dqsw90_d = 0, dqsr90_d = 0;
always @(fclk_in) dqsw0_d <= #(WSTEP * 0.025) fclk_in;
always @(fclk_in) dqsw90_d <= #(wstep_reg * 0.025) fclk_in;
always @(dqs_r_clean) dqsr90_d <= #(rstep_reg * 0.025) dqs_r_clean;
""" + s[chain.end():]
s = s.replace("dqsw0_dly_in[WSTEP]", "dqsw0_d")
s = s.replace("dqsw270_dly_in[wstep_reg]", "dqsw90_d")
s = s.replace("dqsr90_dly_in[rstep_reg]", "dqsr90_d")
s = s.replace("wire [255:0] dqsw270_dly_in,dqsr90_dly_in,dqsw0_dly_in;", "")
assert "dly_in" not in s
open(dst, "w").write(s)
