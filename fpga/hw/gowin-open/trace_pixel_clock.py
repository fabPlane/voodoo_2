"""nextpnr pre-route hook for tracing the HDMI pixel-clock source graph."""

for net_name, net in ctx.nets:
    if net.driver.cell is None:
        continue
    if "CLKDIV" not in str(net.driver.cell.type):
        continue

    driver = net.driver
    source = ctx.getBelPinWire(driver.cell.bel, driver.port)
    print(
        "PIXEL_SOURCE",
        driver.cell.name,
        driver.port,
        driver.cell.bel,
        source,
    )

    seen = {source}
    frontier = [source]
    for depth in range(8):
        next_frontier = []
        for wire in frontier:
            for pip in ctx.getPipsDownhill(wire):
                dst = ctx.getPipDstWire(pip)
                if dst in seen:
                    continue
                seen.add(dst)
                next_frontier.append(dst)
                dst_name = str(dst)
                if depth < 2 or any(tag in dst_name for tag in (
                    "CLKDIV", "HCLK", "GCLK", "SPINE", "BRIDGE"
                )):
                    print("PIXEL_REACH", depth + 1, pip, dst_name)
        frontier = next_frontier
        print("PIXEL_DEPTH", depth + 1, len(frontier), len(seen))
        if not frontier or len(seen) > 100000:
            break
