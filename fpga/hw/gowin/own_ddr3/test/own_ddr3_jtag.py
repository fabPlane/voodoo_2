#!/usr/bin/env python3
"""Host tool for the own_ddr3 JTAG test bitstream (own_ddr3_jtag_top.v).

Everything from ../../ddr3_test/ddr3_jtag.py (status, bist, read, write,
verify) plus the own_ddr3 debug registers (JTAG ops C / D):

  own_ddr3_jtag.py cal                 # calibration result per lane + maps
  own_ddr3_jtag.py rd 0x01             # read a debug register
  own_ddr3_jtag.py wr 0x01 0x00000000  # write one (e.g. WSTEP per lane)
  own_ddr3_jtag.py recal               # rerun read calibration
  own_ddr3_jtag.py reinit              # full DRAM init + calibration
  own_ddr3_jtag.py bist --bursts 0x2000000 --gap 8   (as ddr3_jtag.py)

Debug registers (own_ddr3_ctrl.v): 00 status, 01 WSTEP x4, 02 MR1:MR0,
03 MR3:MR2, 04 control (0 recal, 1 reinit, 2 HOLD per read, 3 manual,
4 HOLD pulse), 05 gate base/len, 08-0B per-lane result, 0C latency,
10 + 4*lane + word: pass map.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ddr3_test"))
import ddr3_jtag as dj  # noqa: E402

OP_DBGRD, OP_DBGWR = 0xC, 0xD


def dbg_read(j: dj.Jtag, reg: int) -> int:
    return j.run([dj.cmd(OP_DBGRD, reg)])[0] >> 32


def dbg_write(j: dj.Jtag, reg: int, val: int) -> None:
    j.run([dj.cmd(OP_DBGWR, reg, val)])


def cal_report(j: dj.Jtag) -> None:
    st = dbg_read(j, 0x00)
    print(f"status {st:#010x}: istate {(st >> 4) & 0xF} cstate {(st >> 8) & 0xF} "
          f"done {(st >> 12) & 1} fail {(st >> 16) & 1} runs {(st >> 20) & 0xF}")
    print(f"wstep {dbg_read(j, 0x01):#010x} mr1:mr0 {dbg_read(j, 0x02):#010x} "
          f"mr3:mr2 {dbg_read(j, 0x03):#010x} gate {dbg_read(j, 0x05):#06x}")
    for lane in range(4):
        r = dbg_read(j, 0x08 + lane)
        words = [dbg_read(j, 0x10 + 4 * lane + w) for w in range(4)]
        rows = []
        for q in range(4):
            bits = (sum(w << (32 * i) for i, w in enumerate(words)) >> (32 * q)) & 0xFFFFFFFF
            rows.append("".join("#" if bits >> o & 1 else "." for o in range(32)))
        print(f"lane {lane}: gate o={(r >> 2) & 0x1F} q={r & 3} rows {(r >> 16) & 0xF:04b} "
              f"lat {(r >> 24) & 0xF} rot {(r >> 28) & 7} late {r >> 31}")
        for q, row in enumerate(rows):
            print(f"   q{q} {row}")
    lat = dbg_read(j, 0x0C)
    print(f"latency min/max {lat >> 4 & 0xF}/{lat & 0xF}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("cal", "rd", "wr", "recal", "reinit"):
        op = sys.argv[1]
        j = dj.Jtag(6000, "openocd", True)
        try:
            j.sync()
            if op == "cal":
                cal_report(j)
            elif op == "rd":
                print(f"{dbg_read(j, int(sys.argv[2], 0)):#010x}")
            elif op == "wr":
                dbg_write(j, int(sys.argv[2], 0), int(sys.argv[3], 0))
            else:
                dbg_write(j, 0x04, 1 if op == "recal" else 2)
                time.sleep(0.5)
                cal_report(j)
        finally:
            j.close()
    else:
        dj.main()


if __name__ == "__main__":
    main()
