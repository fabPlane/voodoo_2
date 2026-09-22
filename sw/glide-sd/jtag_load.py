#!/usr/bin/env python3
"""Prepare a V2GL image for JTAG poke into DDR at 0x11000000.

Always writes payload.bin + load.gdb. If openFPGALoader is installed, print
the bitstream command and any external-RAM write the probe actually supports.
This does not invent a USB BAR stream.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import subprocess
from pathlib import Path

MAGIC = 0x4C473256
DDR_BASE = 0x11000000


def check_v2g(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 16:
        raise SystemExit(f"{path}: too small")
    magic, ver, slots, _ = struct.unpack_from("<IIII", data, 0)
    if magic != MAGIC:
        raise SystemExit(f"{path}: not V2GL")
    if ver != 1 or slots < 1:
        raise SystemExit(f"{path}: bad header ver={ver} slots={slots}")
    return len(data)


def write_gdb(out: Path, payload: Path) -> None:
    text = (
        f"# Soft-CPU / Andes A25 GDB after the bitstream is up.\n"
        f"# restore the V2GL image into the glide-play DDR window.\n"
        f"restore {payload} binary {DDR_BASE:#x}\n"
        f"# then from the pad: SD CARD PLAY + A\n"
    )
    (out / "load.gdb").write_text(text)


def probe_openfpgaloader() -> str:
    exe = shutil.which("openFPGALoader")
    if not exe:
        return "openFPGALoader not on PATH; program the bitstream, then GDB restore"
    try:
        help_txt = subprocess.check_output([exe, "--help"], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        help_txt = exc.output or ""
    lines = [
        f"bitstream: {exe} -c ft2232 <console.fs>   # or GAO-bridge as in hardware/tang-console-138k",
    ]
    if "--external-ram" in help_txt or "external ram" in help_txt.lower():
        lines.append(
            f"ddr poke:  {exe} -c ft2232 --external-ram --offset {DDR_BASE:#x} payload.bin"
        )
    else:
        lines.append(
            "this openFPGALoader build has no external-RAM write; use load.gdb"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path, help="PLAY.V2G")
    ap.add_argument("--out", type=Path, default=Path("jtag"))
    ap.add_argument("--base", type=lambda s: int(s, 0), default=DDR_BASE)
    args = ap.parse_args()
    size = check_v2g(args.image)
    args.out.mkdir(parents=True, exist_ok=True)
    payload = args.out / "payload.bin"
    payload.write_bytes(args.image.read_bytes())
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    write_gdb(args.out, payload.resolve())
    (args.out / "addresses.txt").write_text(
        f"base={args.base:#x}\nsize={size}\nsha256={digest}\n"
        f"menu=SD CARD PLAY\n"
    )
    print(f"jtag payload {payload} ({size} bytes) @ {args.base:#x}")
    print(f"sha256 {digest}")
    print(probe_openfpgaloader())


if __name__ == "__main__":
    main()
