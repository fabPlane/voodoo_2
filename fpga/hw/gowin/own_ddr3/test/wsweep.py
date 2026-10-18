#!/usr/bin/env python3
"""Write-timing sweep for own_ddr3, checked through the vendor IP.

  wsweep.py write   # own_ddr3 loaded: one burst per (WSTEP, shift) in its own row
  wsweep.py check   # vendor bitstream loaded: read the rows back, score each

Setting n uses bank 0 / row 0 / column 8(n+64), past the Gowin IP's
training bursts at 0..0x9f: own_ddr3 byte address (n+64)*0x100,
Gowin IP (rank-bank-row-column) byte address (n+64)*0x20.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ddr3_test"))
import ddr3_jtag as dj  # noqa: E402

WSTEPS = list(range(0, 256, 16))
SHIFTS = [0, 1]
PINS = [0xA0, 0x42, 0xBE, 0x5C, 0xCC, 0xB1, 0xF2, 0xD8]


def pattern(row: int) -> bytes:
    # different per row so stale rows can't pass; every pin toggles
    seq = [(PINS[p % 8] ^ (0x11 * (p // 8)) ^ (row * 0x25)) & 0xFF for p in range(32)]
    words = [sum(((seq[p] >> b) & 1) << p for p in range(32)) for b in range(8)]
    return b"".join(w.to_bytes(4, "little") for w in words)


def settings():
    # (index, wstep, 0x06 value: shift, wr_lat, wr_dly, DQ IODELAY step)
    r = 1
    for wl, wd in ((2, 2), (2, 3), (3, 0), (3, 1)):
        for sh in (0, 1):
            for dq in range(0, 251, 25):
                yield r, 0, (sh << 16) | (wl << 24) | (wd << 28), dq
                r += 1


def main():
    mode = sys.argv[1]
    j = dj.Jtag(6000, dj.shutil.which("openocd") or "/home/hyper/opt/oss-cad-suite/bin/openocd", True)
    try:
        j.sync()
        if mode == "write":
            j.run([dj.cmd(0xD, 0x0C, 0x100)])          # cal_done for user access
            for r, ws, sh, dq in settings():
                j.run([dj.cmd(0xD, 0x01, ws * 0x01010101), dj.cmd(0xD, 0x06, sh),
                       dj.cmd(0xD, 0x0D, dq * 0x01010101)])
                dj.write_bytes(j, (r + 64) * 0x100, pattern(r))
            print("written", r)
        else:
            for r, ws, sh, dq in settings():
                got = dj.read_bytes(j, (r + 64) * 0x20, 32)
                exp = pattern(r)
                # per lane, per beat: '-' untouched (still 0), 'o' right, 'x' wrong
                marks = []
                nbad = 0
                for lane in range(4):
                    m = ""
                    for b in range(8):
                        g, e = got[4 * b + lane], exp[4 * b + lane]
                        if g == e:
                            m += "o"
                        elif g == 0:
                            m += "-"; nbad += 1
                        else:
                            m += "x"; nbad += 1
                    marks.append(m)
                print(f"dq_dly {dq:3d} lat {sh >> 24 & 3} dly {sh >> 28 & 3} shift {sh >> 16 & 1}: "
                      + " ".join(marks) + ("  <==" if nbad == 0 else ""))
    finally:
        j.close()


if __name__ == "__main__":
    main()
