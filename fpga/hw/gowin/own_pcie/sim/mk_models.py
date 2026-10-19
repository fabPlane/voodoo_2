#!/usr/bin/env python3
"""Behavioural GTR12_QUAD / GTR12_UPAR / GTR12_PMAC for iverilog.

  mk_models.py <prim_sim.v> <out.v>

Gowin's prim_sim.v has these hard blocks as empty port lists. Take the
port lists and put models/<name>.vh inside each.
"""

import re
import sys
from pathlib import Path

NAMES = ["GTR12_QUAD", "GTR12_UPAR", "GTR12_PMAC"]


def main():
    src, out = sys.argv[1:3]
    text = Path(src).read_text(errors="replace")
    here = Path(__file__).resolve().parent / "models"
    parts = ["`timescale 1ps/1ps\n"]
    for n in NAMES:
        m = re.search(rf"^module {n}\b.*?^endmodule", text, re.S | re.M)
        if not m:
            sys.exit(f"{n} not in {src}")
        body = m.group(0)[: -len("endmodule")]
        parts.append(body + f'`include "{here / (n + ".vh")}"\nendmodule\n')
    Path(out).write_text("\n".join(parts))


if __name__ == "__main__":
    main()
