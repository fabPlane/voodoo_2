"""nextpnr hook: print the GW5AST HCLK hard-block BEL inventory.

Run with --pre-place list_hclk_bels.py.  This is deliberately tiny so it can
also be used while bringing up a freshly generated Himbaechel chip database.
"""

for bel in ctx.getBels():
    bel_type = str(ctx.getBelType(bel))
    if bel_type in ("CLKDIV", "CLKDIV2", "PLLA"):
        print(f"HCLK_BEL {bel} {bel_type}")
