#!/usr/bin/env python3
"""Read board_top's JTAG diagnostics (src/jtag_diag.v) over the USB debugger.

  sudo python3 board_diag.py            # one snapshot
  sudo python3 board_diag.py --watch 5  # five snapshots, 1 s apart

Shows PCIe LTSSM / link, DDR3 calibration, PLL locks, core reset, the host
bridge wedge flag, per-domain clock rates and TLP counters.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ddr3_test"))
from ddr3_jtag import Jtag  # noqa: E402  (openocd Tcl-port transport)

# Gowin PCIe LTSSM encodings are vendor-specific; 0x11 (L0) is the one that
# matters. Anything else while the slot is powered means training failed.
PAGES = ["status", "clk_core", "ddr_clk", "tlp_clk", "pix_clk",
         "rx_tlps", "die_temp", "hdmi_frames"]
NOMINAL_MHZ = {"clk_core": 46.15, "ddr_clk": 100.0, "tlp_clk": 75.0, "pix_clk": 25.0}


def read_pages(j: Jtag) -> list[int]:
    # Each scan selects the page returned by the next one.
    resp = j.scan_many(list(range(8)) + [0])
    out = []
    for page, r in enumerate(resp[1:]):
        if (r >> 24) & 0xFF != 0xB0 or (r >> 16) & 0xFF != page:
            raise SystemExit(f"diag register not answering (page {page}: {r:#018x}); "
                             "is the Voodoo bitstream with jtag_diag loaded?")
        out.append(r >> 32)
    return out


def show(prev: tuple[float, list[int]] | None, now: tuple[float, list[int]]) -> None:
    t, v = now
    s = v[0]
    print(f"magic={s >> 24:#04x} ltssm={(s >> 16) & 0x1f:#04x} bus={(s >> 8) & 0xff:#04x} "
          f"perst_n={(s >> 7) & 1} linkup={(s >> 6) & 1} calib={(s >> 5) & 1} "
          f"ddr_rst={(s >> 4) & 1} pll_sys={(s >> 3) & 1} pll_hdmi={(s >> 2) & 1} "
          f"core_rst={(s >> 1) & 1} wedged={s & 1}")
    t = v[6] & 0x3FFF
    t = t - 0x4000 if t & 0x2000 else t
    print(f"tlps rx={v[5]}  hdmi frames={v[7]}  die temp={t / 4:.2f} C (samples {v[6] >> 16})")
    if prev:
        dt = t - prev[0]
        rates = []
        for i, name in enumerate(PAGES[1:5], start=1):
            mhz = ((v[i] - prev[1][i]) & 0xFFFFFFFF) / dt / 1e6
            rates.append(f"{name}={mhz:.1f}MHz (exp {NOMINAL_MHZ[name]:g})")
        print("  " + "  ".join(rates))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--watch", type=int, default=1)
    p.add_argument("--khz", type=int, default=6000)
    p.add_argument("--openocd", default="/home/hyper/opt/oss-cad-suite/bin/openocd")
    args = p.parse_args()
    j = Jtag(args.khz, args.openocd, sudo=True)
    try:
        prev = (time.time(), read_pages(j))
        show(None, prev)
        for _ in range(max(args.watch, 1)):
            time.sleep(1.0)
            now = (time.time(), read_pages(j))
            show(prev, now)
            prev = now
    finally:
        j.close()


if __name__ == "__main__":
    main()
