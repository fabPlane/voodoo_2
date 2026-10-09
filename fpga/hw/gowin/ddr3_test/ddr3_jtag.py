#!/usr/bin/env python3
"""Host side of the Tang Mega 138K Pro DDR3 bring-up (ddr3_jtag_top.v).

Talks to the GW_JTAG ER1 register (IR 0x42) through OpenOCD's Tcl port. The
fabric register is 64 bits; GW_JTAG registers the serial path, so a scan is
66 bits with one low padding bit (same framing as the console mailbox).

  ddr3_jtag.py status
  ddr3_jtag.py bist --bursts 0x100000 [--addr 0] [--seed 0x1234]
  ddr3_jtag.py write FILE --addr 0x100000      # push a file into DDR3
  ddr3_jtag.py read --addr 0x100000 --len 256 [--out FILE]
  ddr3_jtag.py verify FILE --addr 0x100000

OpenOCD needs USB access to the FT2232 (run as root, or add a udev rule);
by default this script starts `sudo openocd` itself.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TCL_PORT = 6666

OP_NOP, OP_SETADDR, OP_WBUF, OP_WRITE, OP_READ, OP_RBUF = 0, 1, 2, 3, 4, 5
OP_BIST, OP_BISTRES, OP_ADDR, OP_ECHO, OP_CYCLES, OP_REFRESH = 6, 7, 8, 9, 0xA, 0xB
AUTOINC = 1 << 8


def cmd(op: int, lo: int = 0, data: int = 0) -> int:
    return ((data & 0xFFFFFFFF) << 32) | (op << 28) | (lo & 0x0FFFFFFF)


def decode_status(s: int) -> dict:
    return {
        "magic": s >> 24, "seq": (s >> 16) & 0xFF,
        "calib": (s >> 15) & 1, "pll_lock": (s >> 14) & 1, "ddr_rst": (s >> 13) & 1,
        "busy": (s >> 12) & 1, "bist_done": (s >> 11) & 1, "bist_fail": (s >> 10) & 1,
        "rd_timeout": (s >> 9) & 1, "cmd_ready": (s >> 8) & 1,
        "wr_data_rdy": (s >> 7) & 1, "last_op": s & 0xF,
    }


class Jtag:
    def __init__(self, khz: int, openocd: str, sudo: bool):
        self.proc = None
        cfg = HERE / "openocd-tang-mega-138k-pro.cfg"
        argv = [openocd, "-f", str(cfg), "-c", f"adapter speed {khz}",
                "-c", f"tcl_port {TCL_PORT}", "-c", "telnet_port disabled",
                "-c", "gdb_port disabled", "-c", "init",
                "-c", "irscan gowin.fpga 0x42"]
        if sudo and os.geteuid() != 0:
            argv = ["sudo", "-n"] + argv
        # Log to a file: an unread stderr pipe fills up and stalls openocd.
        self.log_path = Path(os.environ.get("TMPDIR", "/tmp")) / "ddr3_jtag_openocd.log"
        self.log = open(self.log_path, "w")
        self.proc = subprocess.Popen(argv, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 10
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", TCL_PORT), timeout=60)
                break
            except OSError:
                if self.proc.poll() is not None or time.time() > deadline:
                    err = self.log_path.read_text()
                    raise SystemExit(f"openocd failed to start:\n{err}")
                time.sleep(0.1)
        self.buf = b""
        self.seq = None

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
        """Shift each 64-bit word; return the 64-bit response of each scan."""
        out = []
        for i in range(0, len(words), 64):
            chunk = words[i:i + 64]
            script = "list " + " ".join(
                f"[drscan gowin.fpga 66 0x{(w << 1):017x}]" for w in chunk)
            raw = self.tcl(script).split()
            if len(raw) != len(chunk):
                raise RuntimeError(f"bad drscan reply: {raw!r}")
            out.extend(((int(r, 16) >> 1) & 0xFFFFFFFFFFFFFFFF) for r in raw)
        return out

    def run(self, words: list[int]) -> list[int]:
        """Issue commands; check every one landed (seq counter); return the
        response *to* each command (i.e. shifted out by the following scan)."""
        resp = self.scan_many(words + [cmd(OP_NOP)])[1:]
        # NOP itself increments seq too, but its own response isn't read.
        if self.seq is not None:
            for i, r in enumerate(resp):
                exp = (self.seq + i + 1) & 0xFF
                got = (r >> 16) & 0xFF
                if got != exp or (r >> 24) & 0xFF != 0xD3:
                    raise RuntimeError(
                        f"command {i} lost or link bad: seq {got:#x} != {exp:#x}, resp {r:#018x}")
        self.seq = ((resp[-1] >> 16) & 0xFF) + 1  # +1 for the trailing NOP
        return resp

    def sync(self) -> int:
        r = self.scan_many([cmd(OP_NOP), cmd(OP_NOP)])[1]
        if (r >> 24) & 0xFF != 0xD3:
            raise SystemExit(f"no DDR3 test design answering (got {r:#018x}); is ddr3_test.fs loaded?")
        self.seq = ((r >> 16) & 0xFF) + 1
        return r

    def close(self):
        try:
            self.tcl("shutdown")
        except Exception:
            pass
        if self.proc:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def status(j: Jtag) -> dict:
    r = j.sync()
    st = decode_status(r & 0xFFFFFFFF)
    c1 = j.run([cmd(OP_CYCLES)])[0] >> 32
    t1 = time.time()
    time.sleep(0.2)
    c2 = j.run([cmd(OP_CYCLES)])[0] >> 32
    t2 = time.time()
    mhz = ((c2 - c1) & 0xFFFFFFFF) / (t2 - t1) / 1e6
    print("status: " + " ".join(f"{k}={v}" for k, v in st.items() if k != "magic"))
    print(f"UI clock ~{mhz:.1f} MHz (expect 100)")
    return st


def write_bytes(j: Jtag, addr: int, data: bytes) -> None:
    if addr % 32:
        raise SystemExit("address must be 32-byte aligned")
    data = data + b"\0" * (-len(data) % 32)
    words = [cmd(OP_SETADDR, data=addr)]
    for off in range(0, len(data), 32):
        for lane in range(8):
            w = int.from_bytes(data[off + lane * 4: off + lane * 4 + 4], "little")
            words.append(cmd(OP_WBUF, lane, w))
        words.append(cmd(OP_WRITE, AUTOINC))
    for i in range(0, len(words), 900):
        j.run(words[i:i + 900])


def read_bytes(j: Jtag, addr: int, length: int) -> bytes:
    base = addr & ~31
    end = (addr + length + 31) & ~31
    words = [cmd(OP_SETADDR, data=base)]
    for _ in range(base, end, 32):
        words.append(cmd(OP_READ, AUTOINC))
        words.extend(cmd(OP_RBUF, lane) for lane in range(8))
    out = bytearray()
    for i in range(0, len(words), 900):
        chunk = words[i:i + 900]
        resp = j.run(chunk)
        for w, r in zip(chunk, resp):
            if (w >> 28) & 0xF == OP_READ and (r >> 9) & 1:
                raise RuntimeError("DDR3 read timed out")
            if (w >> 28) & 0xF == OP_RBUF:
                out += ((r >> 32) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(out[addr - base: addr - base + length])


def bist(j: Jtag, addr: int, bursts: int, seed: int, gap: int = 0, inflight: int = 32) -> bool:
    j.sync()
    lo = ((inflight - 1) & 0x3F) << 20 | (gap & 0xF) << 16 | (seed & 0xFFFF)
    j.run([cmd(OP_SETADDR, data=addr), cmd(OP_BIST, lo, bursts)])
    t0 = time.time()
    while True:
        # The executor ignores commands while busy, so poll without the
        # sequence check and resynchronise afterwards.
        st = decode_status(j.scan_many([cmd(OP_NOP)])[0] & 0xFFFFFFFF)
        if st["bist_done"] and not st["busy"]:
            j.sync()
            break
        if time.time() - t0 > 120:
            raise SystemExit("BIST did not finish in 120 s")
        time.sleep(0.05)
    dt = time.time() - t0
    errs, first, bits, checked, issued = (r >> 32 for r in j.run(
        [cmd(OP_BISTRES, n) for n in range(5)]))
    mib = bursts * 32 / 2**20
    ok = errs == 0 and not st["bist_fail"] and checked == bursts
    print(f"BIST {'PASS' if ok else 'FAIL'}: {mib:.1f} MiB at {addr:#x} seed {seed:#06x}, "
          f"{checked} bursts checked, {errs} bad bursts"
          + (f", first bad {first:#010x}, bad DQ bits {bits:#010x}" if errs else "")
          + (f", read timeout ({issued} reads issued)" if st["rd_timeout"] else "")
          + f" [gap {gap}, {inflight} in flight]"
          + f" ({dt:.2f} s incl. polling)")
    return ok


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--khz", type=int, default=2000)
    p.add_argument("--openocd", default=shutil.which("openocd") or "/home/hyper/opt/oss-cad-suite/bin/openocd")
    p.add_argument("--no-sudo", action="store_true")
    p.add_argument("--refresh", type=lambda v: int(v, 0), default=None,
                   help="pulse the IP's ref_req every N UI cycles (0 = off)")
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("status")
    b = sub.add_parser("bist")
    b.add_argument("--addr", type=lambda v: int(v, 0), default=0)
    b.add_argument("--bursts", type=lambda v: int(v, 0), default=1 << 20)
    b.add_argument("--seed", type=lambda v: int(v, 0), default=0x1234)
    b.add_argument("--gap", type=int, default=0, help="idle UI cycles between commands (0-15)")
    b.add_argument("--inflight", type=int, default=32, help="max reads outstanding (1-64)")
    w = sub.add_parser("write")
    w.add_argument("file")
    w.add_argument("--addr", type=lambda v: int(v, 0), default=0x100000)
    r = sub.add_parser("read")
    r.add_argument("--addr", type=lambda v: int(v, 0), default=0x100000)
    r.add_argument("--len", type=lambda v: int(v, 0), default=256)
    r.add_argument("--out")
    v = sub.add_parser("verify")
    v.add_argument("file")
    v.add_argument("--addr", type=lambda v: int(v, 0), default=0x100000)
    args = p.parse_args()

    j = Jtag(args.khz, args.openocd, not args.no_sudo)
    try:
        if args.refresh is not None:
            j.sync()
            acks = j.run([cmd(OP_REFRESH, data=args.refresh)])[0] >> 32
            print(f"ref_req period {args.refresh} cycles (ref_acks so far {acks})")
        if args.op == "status":
            status(j)
        elif args.op == "bist":
            ok = bist(j, args.addr, args.bursts, args.seed, args.gap, args.inflight)
            sys.exit(0 if ok else 1)
        elif args.op == "write":
            data = Path(args.file).read_bytes()
            j.sync()
            t0 = time.time()
            write_bytes(j, args.addr, data)
            dt = time.time() - t0
            print(f"wrote {len(data)} bytes at {args.addr:#x} in {dt:.1f} s ({len(data)/dt/1024:.1f} KiB/s)")
        elif args.op == "read":
            j.sync()
            data = read_bytes(j, args.addr, args.len)
            if args.out:
                Path(args.out).write_bytes(data)
                print(f"read {len(data)} bytes -> {args.out}")
            else:
                for off in range(0, len(data), 16):
                    row = data[off:off + 16]
                    asc = "".join(chr(c) if 32 <= c < 127 else "." for c in row)
                    print(f"{args.addr + off:08x}  {row.hex(' '):<47}  {asc}")
        elif args.op == "verify":
            data = Path(args.file).read_bytes()
            j.sync()
            got = read_bytes(j, args.addr, len(data))
            bad = [i for i in range(len(data)) if got[i] != data[i]]
            if bad:
                print(f"VERIFY FAIL: {len(bad)} of {len(data)} bytes differ, first at +{bad[0]:#x}")
                sys.exit(1)
            print(f"VERIFY OK: {len(data)} bytes at {args.addr:#x} match {args.file}")
    finally:
        j.close()


if __name__ == "__main__":
    main()
