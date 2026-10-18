#!/usr/bin/env python3
"""Does each DQS HOLD pulse move the write serializer phase?

  holdsweep.py write   # own_ddr3: after each extra HOLD pulse, write bursts
  holdsweep.py check   # vendor bitstream: read back and classify
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ddr3_test"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ddr3_jtag as dj  # noqa: E402
import wsweep  # noqa: E402

COMBOS = [(2, 2), (2, 3), (3, 0)]
DQS = [0, 50, 100]


def slots():
    r = 1
    for n in range(8):
        for wl, wd in COMBOS:
            for dq in DQS:
                yield r, n, wl, wd, dq
                r += 1


def main():
    j = dj.Jtag(6000, dj.shutil.which("openocd") or "/home/hyper/opt/oss-cad-suite/bin/openocd", True)
    try:
        j.sync()
        if sys.argv[1] == "write":
            j.run([dj.cmd(0xD, 0x0C, 0x100), dj.cmd(0xD, 0x04, 0x8)])
            done = 0
            for r, n, wl, wd, dq in slots():
                while done < n:
                    j.run([dj.cmd(0xD, 0x04, 0x8 | 0x10)])   # one HOLD pulse
                    done += 1
                j.run([dj.cmd(0xD, 0x06, (wl << 24) | (wd << 28)), dj.cmd(0xD, 0x0D, dq * 0x01010101)])
                dj.write_bytes(j, (r + 64) * 0x100, wsweep.pattern(r))
            print("written", r)
        else:
            for r, n, wl, wd, dq in slots():
                got = dj.read_bytes(j, (r + 64) * 0x20, 32)
                exp = wsweep.pattern(r)
                marks = []
                for lane in range(4):
                    m = ""
                    for b in range(8):
                        g, e = got[4 * b + lane], exp[4 * b + lane]
                        m += "o" if g == e else "-" if g == 0 else "x"
                    marks.append(m)
                ok = all(m == "oooooooo" for m in marks)
                print(f"holds {n} lat {wl} dly {wd} dq {dq:3d}: " + " ".join(marks) + ("  <==" if ok else ""))
    finally:
        j.close()


if __name__ == "__main__":
    main()
