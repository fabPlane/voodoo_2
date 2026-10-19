#!/usr/bin/env python3
"""Estimate register-to-register delays from a GowinSynthesis netlist (.vg).

  path_depth.py <design.vg> <design.timing_paths> [--limit 11.1] [--clock clk_core]

Flattens the hierarchical netlist, then walks every combinational cone
between flops (and BSRAM / DSP registers) with a simple per-cell delay
model. The model's per-hop routing delay is fitted to the real PnR
paths in the .timing_paths file, so the numbers are estimates of what
place-and-route would report, not exact. Prints, per module, how many
endpoints exceed --limit (default 11.1 ns, 90 MHz) and the worst one.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys

sys.setrecursionlimit(100000)

TOK = re.compile(r"\\\S+\s|[A-Za-z_][\w$]*|\d+'[bhd][0-9a-fA-FxXzZ_]+|\d+|[\[\]{}():,;.=]")

# primitive -> output pins
OUTPUTS = {
    "LUT1": {"F"}, "LUT2": {"F"}, "LUT3": {"F"}, "LUT4": {"F"}, "INV": {"O"},
    "MUX2_LUT5": {"O"}, "MUX2_LUT6": {"O"}, "MUX2_LUT7": {"O"}, "MUX2_LUT8": {"O"},
    "ALU": {"SUM", "COUT"},
}
FF = {"DFFRE", "DFFCE", "DFFSE", "DFFPE", "DFF", "DFFE", "DFFR", "DFFS", "DFFC", "DFFP"}
RAM = {"SDPB", "SDPX9B", "DPB", "DPX9B", "SP", "SPX9", "pROM", "pROMX9"}
DSP = {"MULT27X36", "MULT12X12", "MULTADDALU12X12", "MULTALU27X18", "MULTALU12X12", "MULTADD"}


def dsp_pin_regd(prm, pin):
    base = re.match(r"[A-Z]+\d?", pin)
    if not base:
        return False
    key = base.group(0) + "REG_CLK"
    if key not in prm:
        key = re.sub(r"\d", "", base.group(0)) + "REG_CLK"
    return prm.get(key, '"BYPASS"') != '"BYPASS"'


def dsp_regd(prm):
    return any(k.endswith("REG_CLK") and k != "OREG_CLK" and v != '"BYPASS"' for k, v in prm.items())


def tokens(text):
    for m in TOK.finditer(text):
        t = m.group(0)
        yield t.strip() if t.startswith("\\") else t


class Module:
    def __init__(self, name):
        self.name = name
        self.vec = {}          # net name -> (msb, lsb)
        self.ports = []        # port names in order
        self.insts = []        # (type, name, {pin: [bits]}, params)


def parse(path):
    text = open(path, errors="replace").read()
    text = re.sub(r"//[^\n]*", "", text)
    mods = {}
    toks = list(tokens(text))
    i = 0
    n = len(toks)
    cur = None
    defparams = collections.defaultdict(dict)

    def expr(j):
        """Parse a connection expression; return (bits, next index)."""
        if toks[j] == "{":
            bits = []
            j += 1
            while toks[j] != "}":
                b, j = expr(j)
                bits += b
                if toks[j] == ",":
                    j += 1
            return bits, j + 1
        t = toks[j]
        if re.match(r"\d+'", t):
            w = int(t.split("'")[0])
            return ["$const"] * w, j + 1
        j += 1
        if j < n and toks[j] == "[":
            a = int(toks[j + 1])
            if toks[j + 2] == ":":
                b = int(toks[j + 3])
                rng = range(a, b - 1, -1) if a >= b else range(a, b + 1)
                return [f"{t}[{k}]" for k in rng], j + 5
            return [f"{t}[{a}]"], j + 3
        if t in cur.vec:
            a, b = cur.vec[t]
            rng = range(a, b - 1, -1) if a >= b else range(a, b + 1)
            return [f"{t}[{k}]" for k in rng], j
        return [t], j

    while i < n:
        t = toks[i]
        if t == "module":
            cur = Module(toks[i + 1])
            mods[cur.name] = cur
            j = i + 3
            while toks[j] != ")":
                if toks[j] != ",":
                    cur.ports.append(toks[j])
                j += 1
            i = j + 2
        elif t == "endmodule":
            cur = None
            i += 1
        elif cur is not None and t in ("input", "output", "inout", "wire", "reg"):
            j = i + 1
            rng = None
            if toks[j] == "[":
                rng = (int(toks[j + 1]), int(toks[j + 3]))
                j += 5
            while toks[j] != ";":
                if toks[j] != ",":
                    if rng:
                        cur.vec[toks[j]] = rng
                j += 1
            i = j + 1
        elif cur is not None and t == "defparam":
            j = i + 1
            inst, param = toks[j], toks[j + 2]
            j += 4
            val = []
            while toks[j] != ";":
                val.append(toks[j])
                j += 1
            defparams[(cur.name, inst)][param] = "".join(val)
            i = j + 1
        elif cur is not None and t == "assign":
            j = i
            while toks[j] != ";":
                j += 1
            i = j + 1
        elif cur is not None and re.match(r"[A-Za-z_\\~]", t) and i + 2 < n and toks[i + 2] == "(":
            typ, name = t, toks[i + 1]
            j = i + 3
            pins = {}
            while toks[j] != ")":
                if toks[j] == ".":
                    pin = toks[j + 1]
                    if toks[j + 3] == ")":
                        bits, j = [], j + 4
                    else:
                        bits, j = expr(j + 3)
                        j += 1  # ')'
                    pins[pin] = bits
                else:
                    j += 1
            cur.insts.append((typ, name, pins))
            i = j + 2
        else:
            i += 1
    return mods, defparams


def flatten(mods, defparams, top):
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    cells = []  # (type, hier name, {pin: [global nets]}, params, module path)

    def walk(mname, prefix, portmap, mpath):
        m = mods[mname]

        def g(bit):
            if bit in ("$const", "VCC", "GND"):
                return "$const"
            return portmap.get(bit, prefix + bit)

        for typ, name, pins in m.insts:
            if typ in mods:
                sub = mods[typ]
                pm = {}
                for p, bits in pins.items():
                    if p in sub.vec:
                        a, b = sub.vec[p]
                        rng = range(a, b - 1, -1) if a >= b else range(a, b + 1)
                        local = [f"{p}[{k}]" for k in rng]
                    else:
                        local = [p]
                    for lb, pb in zip(local, bits):
                        pm[lb] = g(pb)
                walk(typ, prefix + name + "/", pm, mpath + "/" + name)
            else:
                cells.append((typ, prefix + name,
                              {p: [g(b) for b in bits] for p, bits in pins.items()},
                              defparams.get((mname, name), {}), mpath))

    walk(top, "", {}, "")
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vg")
    ap.add_argument("paths")
    ap.add_argument("--limit", type=float, default=11.1)
    ap.add_argument("--clock", default="clk_core")
    ap.add_argument("--top", default="board_top")
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--calib", action="store_true")
    ap.add_argument("--trace", help="print the modelled worst path into this cell")
    a = ap.parse_args()

    mods, dps = parse(a.vg)
    cells = flatten(mods, dps, a.top)
    print(f"{len(cells)} cells", file=sys.stderr)

    driver = {}   # net -> cell index
    for ci, (typ, name, pins, prm, mp) in enumerate(cells):
        outs = OUTPUTS.get(typ)
        for p, nets in pins.items():
            is_out = (outs and p in outs) or (typ in FF and p == "Q") \
                or (typ in RAM and p.startswith("DO")) or (typ in DSP and p in ("DOUT", "CASO", "SOA", "SOB")) \
                or (outs is None and typ not in FF and typ not in RAM and typ not in DSP
                    and p in ("O", "Q", "F", "DO", "DOUT", "Q0", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9"))
            if is_out:
                for nb in nets:
                    if nb != "$const":
                        driver[nb] = (ci, p)

    fanout = collections.Counter()
    for typ, name, pins, prm, mp in cells:
        outs = OUTPUTS.get(typ, ())
        for p, nets in pins.items():
            if p in outs or (typ in FF and p == "Q"):
                continue
            for nb in nets:
                fanout[nb] += 1
    cell_out = {}
    for net, (ci, p) in driver.items():
        cell_out.setdefault(cells[ci][1], net)

    # Fit routing per hop from the real report: arrival - launch over node count.
    fit = []
    hopfit = []
    real_end = {}
    for blk in open(a.paths).read().split("====="):
        l = [x for x in blk.strip().split("\n") if x]
        if len(l) > 8 and l[0] == "SETUP":
            # start, launch, start Q, then per node: [in, out,] name, last: in
            body = l[4:]
            times = [float(x) for x in body if re.match(r"^-?[\d.]+$", x)]
            names = [x for x in body if not re.match(r"^-?[\d.]+$", x)]
            hops = len(names) - 1
            # per hop: route = next cell's input time - this cell's output time
            seq = body[1:]
            outs_t, nm_t = [], []
            k = 0
            nodes = []
            cur = [names[0]]
            for x in body[1:]:
                if re.match(r"^-?[\d.]+$", x):
                    cur.append(float(x))
                else:
                    nodes.append(cur)
                    cur = [x]
            nodes.append(cur)
            for u, v in zip(nodes, nodes[1:]):
                if len(u) >= 3 and len(v) >= 2:
                    net = cell_out.get(u[0])
                    if net:
                        hopfit.append((v[1] - u[-1], fanout[net]))
            if hops > 3:
                fit.append((times[-1] - times[0], hops))
                real_end.setdefault(names[-1], times[-1] - times[0])
    t_cell = 0.42
    import math
    xs = [math.log2(f + 1) for r, f in hopfit]
    ys = [r for r, f in hopfit]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    kf = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    r0 = my - kf * mx
    route = my
    print(f"fitted {len(hopfit)} PnR hops: route = {r0:.2f} + {kf:.2f}*log2(fanout+1) ns, "
          f"mean {my:.2f}", file=sys.stderr)

    def rt(net):
        return max(0.1, r0 + kf * math.log2(fanout[net] + 1))

    # arrival(net): time the net's driver output switches, from the clock edge.
    # pin(net): time it reaches a sink, adding routing for the net's fanout.
    T_CLKQ, T_RAM, T_DSP = 0.35, 2.6, 3.5
    memo = {}

    def pin(net):
        t, src = arrival(net)
        return t + rt(net), src

    def arrival(net):
        if net in memo:
            return memo[net]
        d = driver.get(net)
        if d is None:
            memo[net] = (0.0, None)
            return memo[net]
        ci, opin = d
        typ, name, pins, prm, mp = cells[ci]
        memo[net] = (0.0, None)  # loop guard
        def worst(cands):
            return max(cands, default=(0.0, None))
        if typ in FF:
            r = (T_CLKQ, ci)
        elif typ in RAM:
            r = (T_RAM, ci)
        elif typ in DSP:
            # Registered inputs launch from the DSP; bypassed ones go through.
            r = (T_DSP, ci) if dsp_regd(prm) else (0.0, None)
            for p, nets in pins.items():
                if p in ("DOUT", "CLK", "CE", "RESET", "CASO", "SOA", "SOB") or dsp_pin_regd(prm, p):
                    continue
                for nb in nets:
                    x = pin(nb)
                    if x[0] + 4.0 > r[0]:
                        r = (x[0] + 4.0, x[1])
        elif typ == "ALU":
            c = [(pin(nb)[0] + 0.5, pin(nb)[1]) for p in ("I0", "I1", "I3") for nb in pins.get(p, [])]
            # carry: dedicated wire, no fabric routing
            c += [(arrival(nb)[0] + (0.06 if opin == "COUT" else 0.3), arrival(nb)[1]) for nb in pins.get("CIN", [])]
            r = worst(c)
        elif typ.startswith("MUX2_LUT"):
            # I0/I1 come from the LUTs / muxes next to it in the slice
            c = [(arrival(nb)[0] + 0.15, arrival(nb)[1]) for p in ("I0", "I1") for nb in pins.get(p, [])]
            c += [(pin(nb)[0] + 0.3, pin(nb)[1]) for nb in pins.get("S0", [])]
            r = worst(c)
        elif typ in OUTPUTS:
            c = [pin(nb) for p, nets in pins.items() if p not in OUTPUTS[typ] for nb in nets]
            x = worst(c)
            r = (x[0] + t_cell, x[1])
        else:
            r = (0.0, None)  # I/O and hard blocks: treat as sources
        memo[net] = r
        return r

    # Endpoints: flop D/CE/reset, BSRAM and registered DSP inputs.
    def clock_of(pins):
        c = pins.get("CLK") or pins.get("CLKA") or pins.get("CLKB") or pins.get("CLK0") or []
        return c[0] if c else "?"

    if a.trace:
        ci = next(i for i, c in enumerate(cells) if c[1] == a.trace)
        typ, name, pins, prm, mp = cells[ci]
        print(typ, name, {p: [(nb, round(arrival(nb)[0], 2)) for nb in nets[:3]] for p, nets in pins.items()})
        nets = [nb for p, ns in pins.items() if p in ("D", "A", "B", "CE") for nb in ns]
        nb = max(nets, key=lambda x: pin(x)[0])
        for _ in range(40):
            d = driver.get(nb)
            if not d:
                print("  (no driver)", nb); break
            c = cells[d[0]]
            print(f"  {arrival(nb)[0]:6.2f} +{rt(nb):4.2f} fo {fanout[nb]:4d} {c[0]:10s} {d[1]:5s} {c[1]}")
            if c[0] in FF or c[0] in RAM:
                break
            ins = [x for p, ns in c[2].items() if not (c[0] in OUTPUTS and p in OUTPUTS[c[0]]) and p not in ("DOUT", "CLK", "CE", "RESET") for x in ns if x != "$const"]
            if not ins: break
            nb = max(ins, key=lambda x: pin(x)[0])
        return
    calib = []
    clocknames = collections.Counter()
    per_mod = collections.defaultdict(lambda: [0, 0.0, "", ""])
    n_end = 0
    for ci, (typ, name, pins, prm, mp) in enumerate(cells):
        if typ in FF:
            inp = [nb for p in ("D", "CE", "RESET", "SET", "CLEAR", "PRESET") for nb in pins.get(p, [])]
        elif typ in RAM:
            inp = [nb for p, nets in pins.items() if not p.startswith("DO") and not p.startswith("CLK") for nb in nets]
        elif typ in DSP and dsp_regd(prm):
            inp = [nb for p, nets in pins.items() if p not in ("DOUT", "CLK", "CE", "RESET")
                   and dsp_pin_regd(prm, p) for nb in nets]
        else:
            continue
        clk = clock_of(pins)
        clocknames[clk] += 1
        if a.clock and a.clock not in clk:
            continue
        best = max((pin(nb) for nb in inp if nb != "$const"), default=(0.0, None))
        d = best[0] + 0.1
        if name in real_end:
            calib.append((real_end[name], d, name))
        n_end += 1
        if d > a.limit:
            mod = re.sub(r"/[^/]*$", "", name)
            mod = re.sub(r"^u_voodoo/core_1/core_1_0/", "core/", mod)
            s = per_mod[mod]
            s[0] += 1
            if d > s[1]:
                src = cells[best[1]][1] if best[1] is not None else "?"
                s[1], s[2], s[3] = d, name, src

    if a.calib:
        for r, d, nm in sorted(calib)[-15:] + sorted(calib)[:5]:
            print(f"calib real {r:6.2f} model {d:6.2f}  {nm}", file=sys.stderr)
        for lo, hi in ((0, 10), (10, 14), (14, 18), (18, 30)):
            b = [d / r for r, d, _ in calib if lo <= r < hi]
            if b:
                b.sort()
                print(f"calib real {lo}-{hi} ns: n={len(b)} model/real median {b[len(b)//2]:.2f} "
                      f"[{b[len(b)//10]:.2f}..{b[len(b)*9//10]:.2f}]", file=sys.stderr)
        print(f"calib n={len(calib)} mean model/real {sum(d/r for r,d,_ in calib)/max(1,len(calib)):.2f}", file=sys.stderr)
    print(f"clock nets with most endpoints: {clocknames.most_common(8)}", file=sys.stderr)
    total = sum(v[0] for v in per_mod.values())
    print(f"{a.clock}: {total} of {n_end} endpoints estimated > {a.limit} ns")
    print(f"{'endpoints':>9} {'worst ns':>8}  module  (worst start -> end)")
    for mod, (cnt, w, end, src) in sorted(per_mod.items(), key=lambda kv: -kv[1][0])[:a.rows]:
        print(f"{cnt:9d} {w:8.1f}  {mod}\n{'':19s}{src.split('/')[-1]} -> {end.split('/')[-1]}")


if __name__ == "__main__":
    main()
