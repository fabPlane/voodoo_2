#!/usr/bin/env python3
"""Manual read-path exploration for own_ddr3 on the board.

Puts the controller in manual mode (no auto calibration), writes a known
burst, then for every (latency, gate o, gate q) reads it back with the
lanes' gate/latency forced through the debug registers, and reports per
lane where the written pattern shows up (as raw beats, any bit offset per
pin across two consecutive latencies).

  explore.py [--addr 0x1000] [--lat 0-15] [--gbase 1] [--hold]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ddr3_test"))
import ddr3_jtag as dj  # noqa: E402

OP_DBGRD, OP_DBGWR = 0xC, 0xD


def wr(reg, val):
    return dj.cmd(OP_DBGWR, reg, val)


def pin_seq(words, pin):
    """8 beats of one DQ pin from 8 x 32-bit beat words -> int, bit b = beat b."""
    return sum(((words[b] >> pin) & 1) << b for b in range(8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x1000)
    ap.add_argument("--lat", default="0-15")
    ap.add_argument("--gbase", type=int, default=1)
    ap.add_argument("--hold", action="store_true", help="HOLD on every read")
    ap.add_argument("--reinit", action="store_true")
    ap.add_argument("--dump", action="store_true", help="print raw beats")
    ap.add_argument("--nowrite", action="store_true", help="data already in DRAM")
    a = ap.parse_args()
    lat_lo, lat_hi = (int(x) for x in a.lat.split("-"))

    j = dj.Jtag(6000, dj.shutil.which("openocd") or "/home/hyper/opt/oss-cad-suite/bin/openocd", True)
    try:
        j.sync()
        ctl = 0x8 | (0x4 if a.hold else 0)
        if a.reinit:
            j.run([wr(0x04, ctl | 0x2)])     # manual + reinit
            time.sleep(0.5)
        j.run([wr(0x04, ctl), wr(0x05, (0x0F << 8) | a.gbase)])
        # pattern: beat b word = per-pin sequences, lanes distinct
        pins = [0xA0, 0x42, 0xBE, 0x5C, 0xCC, 0xB1, 0xF2, 0xD8]
        pinseq = [pins[p % 8] ^ (0x11 * (p // 8)) & 0xFF for p in range(32)]
        words = [sum(((pinseq[p] >> b) & 1) << p for p in range(32)) for b in range(8)]
        data = b"".join(w.to_bytes(4, "little") for w in words)
        # cal_done = 1 with latency lat_max (needed for user reads)
        j.run([wr(0x0C, 0x100 | 0)])
        if not a.nowrite:
            dj.write_bytes(j, a.addr, data)
        print("pattern beats:", " ".join(f"{w:08x}" for w in words))

        hits = {}
        frames = {}
        for lat in range(lat_lo, lat_hi + 1):
            for q in range(4):
                for o in range(32):
                    g = (o << 2) | q
                    cmds = [wr(0x08 + l, g | (lat << 8) | (1 << 20)) for l in range(4)]
                    cmds += [wr(0x0C, 0x100 | lat),
                             dj.cmd(dj.OP_SETADDR, 0, a.addr), dj.cmd(dj.OP_READ, 0)]
                    cmds += [dj.cmd(dj.OP_RBUF, k) for k in range(8)]
                    r = j.run(cmds)
                    got = [x >> 32 for x in r[-8:]]
                    st = dj.decode_status(r[-9] & 0xFFFFFFFF) if False else None
                    frames[(lat, q, o)] = got
                    if a.dump and lat == lat_lo:
                        print(f"lat {lat} q{q} o{o:2d}: " + " ".join(f"{w:08x}" for w in got))
        # Search: for each lane, gate (q,o), find pattern across (lat, lat+1)
        for lane in range(4):
            found = []
            for (lat, q, o), got in frames.items():
                nxt = frames.get((lat + 1, q, o))
                if nxt is None:
                    continue
                ok = True
                offs = set()
                for p in range(8 * lane, 8 * lane + 8):
                    s = pin_seq(got, p) | (pin_seq(nxt, p) << 8)
                    m = [k for k in range(9) if (s >> k) & 0xFF == pinseq[p]]
                    if not m:
                        ok = False
                        break
                    offs.add(m[0])
                if ok and len(offs) == 1:
                    found.append((lat, q, o, offs.pop()))
            print(f"lane {lane}: {len(found)} hits", found[:24])
        # Coarse activity summary: how many distinct read results per lane
        for lane in range(4):
            vals = set(tuple((w >> (8 * lane)) & 0xFF for w in got) for got in frames.values())
            print(f"lane {lane}: {len(vals)} distinct frames; sample {list(vals)[:3]}")
    finally:
        j.close()


if __name__ == "__main__":
    main()
