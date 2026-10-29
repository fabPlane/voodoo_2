#!/usr/bin/env python3
"""Read the Kintex card's JTAG diagnostics (src/jtag_diag_xilinx.v).

The Kintex counterpart of fpga/hw/gowin/board_diag.py: the same eight pages
(link / DDR3 calibration / clocks / TLP counters / HDMI frames; page 5 XORs
in the MicroBlaze V mailbox when built with MCS=1), read through BSCANE2
USER1 with OpenOCD's xilinx-xc7 target.

  sudo python3 board_diag.py                      # one snapshot
  sudo python3 board_diag.py --watch 5            # five more, 1 s apart
  sudo python3 board_diag.py --interface interface/ftdi/digilent-hs2.cfg

--interface is the board's JTAG adapter (OpenOCD interface script).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import time
from pathlib import Path

TCL_PORT = 6667
USER1 = 0x02          # 7-series IR: USER1 (6-bit IR)
PAGES = ["status", "clk_core", "ddr_clk", "tlp_clk", "pix_clk",
         "rx_tlps", "tx_tlps", "hdmi_frames"]
# board_top defaults: CORE_DIV 10 (100 MHz; build with CORE_DIV=8.375 for 119.4).
NOMINAL_MHZ = {"clk_core": 100.0, "ddr_clk": 100.0, "tlp_clk": 125.0, "pix_clk": 25.0}
L0 = 0x16             # pcie_7x pl_ltssm_state in L0


class Jtag:
    def __init__(self, openocd: str, interface: str, khz: int, sudo: bool):
        argv = [openocd, "-f", interface, "-c", f"adapter speed {khz}",
                "-f", "cpld/xilinx-xc7.cfg",
                "-c", f"tcl_port {TCL_PORT}", "-c", "telnet_port disabled",
                "-c", "gdb_port disabled", "-c", "init",
                "-c", f"irscan xc7.tap {USER1:#x}"]
        if sudo and os.geteuid() != 0:
            argv = ["sudo", "-n"] + argv
        self.log_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"kintex_diag_openocd.{os.getuid()}.log"
        self.log = open(self.log_path, "w")
        self.proc = subprocess.Popen(argv, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 10
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", TCL_PORT), timeout=60)
                break
            except OSError:
                if self.proc.poll() is not None or time.time() > deadline:
                    raise SystemExit(f"openocd failed to start:\n{self.log_path.read_text()}")
                time.sleep(0.1)
        self.buf = b""

    def tcl(self, line: str) -> str:
        self.sock.sendall(line.encode() + b"\x1a")
        while b"\x1a" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("openocd closed the connection")
            self.buf += chunk
        out, _, self.buf = self.buf.partition(b"\x1a")
        return out.decode()

    def scan_many(self, words: list[int]) -> list[int]:
        script = "list " + " ".join(f"[drscan xc7.tap 64 0x{w:016x}]" for w in words)
        raw = self.tcl(script).split()
        if len(raw) != len(words):
            raise RuntimeError(f"bad drscan reply: {raw!r}")
        return [int(r, 16) for r in raw]

    def close(self):
        try:
            self.tcl("shutdown")
        except Exception:
            pass
        self.proc.wait(timeout=5)
        self.log.close()


def read_pages(j: Jtag) -> list[int]:
    # Each scan selects the page returned by the next one.
    resp = j.scan_many(list(range(8)) + [0])
    out = []
    for page, r in enumerate(resp[1:]):
        if (r >> 24) & 0xFF != 0xB0 or (r >> 16) & 0xFF != page:
            raise SystemExit(f"diag register not answering (page {page}: {r:#018x}); "
                             "is the Voodoo bitstream with jtag_diag_xilinx loaded?")
        out.append(r >> 32)
    return out


def show(prev: tuple[float, list[int]] | None, now: tuple[float, list[int]]) -> None:
    t, v = now
    s = v[0]
    ltssm = (s >> 16) & 0x3F
    print(f"magic={s >> 24:#04x} ltssm={ltssm:#04x}{' (L0)' if ltssm == L0 else ''} "
          f"bus={(s >> 8) & 0xff:#04x} perst_n={(s >> 7) & 1} linkup={(s >> 6) & 1} "
          f"calib={(s >> 5) & 1} ddr_rst={(s >> 4) & 1} mmcm={(s >> 3) & 1} "
          f"core_rst={(s >> 1) & 1} wedged={s & 1}")
    print(f"tlps rx={v[5]} tx={v[6]}  hdmi frames={v[7]}")
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
    p.add_argument("--interface", default="interface/ftdi/digilent-hs2.cfg")
    p.add_argument("--openocd", default="/home/hyper/opt/oss-cad-suite/bin/openocd")
    args = p.parse_args()
    j = Jtag(args.openocd, args.interface, args.khz, sudo=True)
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
