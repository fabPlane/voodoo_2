#!/usr/bin/env python3
"""Gowin maps a WxD SDP as ceil(W/9) BSRAM. Wide, shallow Spinal FIFOs
blow the 138K's 340-block cap. Force those arrays into distributed RAM."""

from __future__ import print_function

import re
import sys

ATTR = '(* syn_ramstyle = "distributed_ram" *) '
PAT = re.compile(
    r"^(\s*)((?:\(\*[^*]*\*\)\s*)*)reg\s+\[(\d+):0\]\s+(\S+)\s+\[0:(\d+)\];\s*$"
)


def should_force(width, depth):
    return width >= 24 and depth <= 64


def rewrite(path):
    lines = open(path).read().splitlines(True)
    changed = 0
    out = []
    for line in lines:
        m = PAT.match(line.rstrip("\n"))
        if not m:
            out.append(line)
            continue
        indent, existing, msb, name, last = m.groups()
        width = int(msb) + 1
        depth = int(last) + 1
        if not should_force(width, depth) or "syn_ramstyle" in existing:
            out.append(line)
            continue
        out.append("%s%s%sreg [%s:0] %s [0:%s];\n" % (indent, ATTR, existing, msb, name, last))
        changed += 1
        print("distributed_ram %sx%s %s" % (width, depth, name))
    if changed:
        open(path, "w").writelines(out)
    print("annotated %d memories in %s" % (changed, path))
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: force_distributed_fifo_ram.py <verilog>")
    rewrite(sys.argv[1])
