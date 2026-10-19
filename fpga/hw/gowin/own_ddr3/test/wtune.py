#!/usr/bin/env python3
"""Write-timing sweep inside own_ddr3, with the read path set by hand.

  wtune.py [--gate 20] [--lats 8,8,8,7] [--wsteps 0,32,64,96]

Sets every lane's read gate / latency (from explore.py), then for each
(wr_lat, wr_dly, shift, wstep) writes a burst and reads it straight back.
Prints bad bits per lane; all zero = a working write setting.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ddr3_test"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ddr3_jtag as dj  # noqa: E402
import wsweep  # noqa: E402


def wr(reg, val):
    return dj.cmd(0xD, reg, val)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gates", default="26,24,28,17")
    ap.add_argument("--lats", default="8,8,8,7")
    ap.add_argument("--wsteps", default="0,32,64,96")
    a = ap.parse_args()
    lats = [int(x) for x in a.lats.split(",")]
    gates = [int(x) for x in a.gates.split(",")]
    lmax = max(lats)
    j = dj.Jtag(6000, dj.shutil.which("openocd") or "/home/hyper/opt/oss-cad-suite/bin/openocd", True)
    try:
        j.sync()
        cfg = [wr(0x04, 0xC)]   # manual + HOLD on every read
        cfg += [wr(0x08 + l, gates[l] | (lats[l] << 8) | ((lats[l] == lmax) << 20)) for l in range(4)]
        cfg += [wr(0x0C, 0x100 | lmax)]
        j.run(cfg)
        n = 0
        best = []
        for ws in [int(x) for x in a.wsteps.split(",")]:
            for wl in (1, 2, 3):
                for wd in range(4):
                    for sh in (0, 1):
                        n += 1
                        j.run([wr(0x01, ws * 0x01010101),
                               wr(0x06, (sh << 16) | (wl << 24) | (wd << 28))])
                        addr = 0x10000 + n * 0x100
                        exp = wsweep.pattern(n)
                        dj.write_bytes(j, addr, exp)
                        got = dj.read_bytes(j, addr, 32)
                        bad = [sum(bin(got[4 * b + l] ^ exp[4 * b + l]).count("1") for b in range(8))
                               for l in range(4)]
                        tag = "  <== " if sum(bad) == 0 else ""
                        print(f"wstep {ws:3d} wr_lat {wl} wr_dly {wd} shift {sh}: bad {bad}{tag}")
                        best.append((sum(bad), ws, wl, wd, sh))
        best.sort()
        print("best:", best[:6])
    finally:
        j.close()


if __name__ == "__main__":
    main()
