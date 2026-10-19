#!/usr/bin/env python3
"""Read own_pcie_probe (rtl/own_pcie_probe.v) from a probe build over JTAG.

  sudo python3 pcie_probe.py          # decoded report
  sudo python3 pcie_probe.py --raw    # also all 96 pages in hex

Page layout is in own_pcie_probe.v; JTAG diag page 8+k is probe page k.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ddr3_test"))
from ddr3_jtag import Jtag  # noqa: E402

NPAGES = 96


def read_probe(j: Jtag) -> list[int]:
    # Each scan returns the page selected by the previous one.
    sel = [8 + k for k in range(NPAGES)]
    resp = j.scan_many(sel + [0])
    out = []
    for k, r in enumerate(resp[1:]):
        if (r >> 24) & 0xFF != 0xB0 or (r >> 16) & 0xFF != 8 + k:
            raise SystemExit(f"diag page {8 + k} not answering: {r:#018x}")
        out.append(r >> 32)
    if out[0] != 0x0B5E0001:
        raise SystemExit(f"no own_pcie_probe here (page 8 = {out[0]:#010x})")
    return out


def lo(v):
    return v & 0xFFFF


def hi(v):
    return v >> 16


def report(p: list[int]) -> None:
    f = p[2]
    print(f"pl_test_out {p[1]:08x}  ltssm_o {f >> 27:#04x} linkup {(f >> 26) & 1} "
          f"pmac_rstn {(f >> 25) & 1} VCC {(f >> 24) & 1} GND {(f >> 23) & 1}")
    print(f"rx_masknp_o {(f >> 22) & 1} rx_wait_o {(f >> 21) & 1} (user rx_wait {(f >> 20) & 1}, "
          f"masknp {(f >> 19) & 1})  tx_wait_o {(f >> 18) & 1} (PMAC {(f >> 17) & 1})")
    print(f"upar_rst {(f >> 16) & 1} upar_bus_width {(f >> 15) & 1} PERST# {(f >> 14) & 1} "
          f"upar_ready {(f >> 13) & 1} upar_strb {(f >> 4) & 0xFF:02x}")
    print(f"tl_clk_freq_o {p[3]} ({p[3]:#x})  tl_tx_prot_o {p[4]:08x}  "
          f"brsw_in {p[5] >> 24:02x} obffcontrol {(p[5] >> 20) & 0xF:x} cfg_busdev_o {p[5] & 0x1FFF:04x}")
    print(f"PMAC credits  [31:0] {p[6]:08x} [63:32] {p[7]:08x} [95:64] {p[8]:08x}")
    print(f"controller    p {p[9]:08x} np {p[10]:08x} cpl {p[11]:08x}")
    print("mismatches (controller output vs input delayed by d tl_clk cycles):")
    print(f"  rx  d0 {lo(p[12])} d1 {hi(p[12])} d2 {lo(p[13])} d3 {hi(p[13])}")
    print(f"  tx  d0 {lo(p[14])} d1 {hi(p[14])} d2 {lo(p[15])} d3 {hi(p[15])}")
    print(f"  ltssm_o vs test_out[4:0]  d0 {lo(p[16])} d1 {hi(p[16])} d2 {lo(p[17])}")
    print(f"  rx_wait_o vs rx_wait_i d0 {hi(p[17])}   linkup vs ltssm==0x11 {hi(p[22])}")
    c = [lo(p[18]), hi(p[18]), lo(p[19]), hi(p[19]), lo(p[20]), hi(p[20]),
         lo(p[21]), hi(p[21]), lo(p[22])]
    for i, name in enumerate(("p", "np", "cpl")):
        print(f"  credits {name:3s} vs slice 0/1/2: {c[3*i]} {c[3*i+1]} {c[3*i+2]}")
    print(f"PERST# rise -> PMAC reset release {p[23]} cycles, -> linkup {p[24]} cycles")
    print(f"PERST# rises {hi(p[25])}, PMAC releases {lo(p[25])}")
    print(f"controller DRP writes {lo(p[26])} reads {hi(p[26])};  "
          f"UPAR bus writes {lo(p[27])} reads {hi(p[27])};  upar_rst cycles {p[28]}")
    n = min(16, lo(p[27]) + hi(p[27]))
    for i in range(n):
        a, d = p[32 + 2 * i], p[33 + 2 * i]
        kind = "wr" if a >> 31 else "rd" if (a >> 30) & 1 else "??"
        print(f"  upar {i:2d} {kind} {a & 0xFFFFFF:06x} {d:08x}")
    print("LTSSM history (pl_test_out, cycles since PERST# rise):")
    for i in range(16):
        t, c = p[64 + 2 * i], p[65 + 2 * i]
        if i and t == 0 and c == 0:
            break
        print(f"  {i:2d} ltssm {t & 0x1F:#04x} test_out {t:08x} @ {c}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()
    j = Jtag(6000, shutil.which("openocd") or "/home/hyper/opt/oss-cad-suite/bin/openocd", sudo=True)
    try:
        p = read_probe(j)
    finally:
        j.close()
    report(p)
    if a.raw:
        for k in range(0, NPAGES, 8):
            print(f"{8 + k:3d}: " + " ".join(f"{v:08x}" for v in p[k:k + 8]))


if __name__ == "__main__":
    main()
