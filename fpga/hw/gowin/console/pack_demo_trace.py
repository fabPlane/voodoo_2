#!/usr/bin/env python3
"""Pack a SpinalVoodoo PCI trace into addr/data pairs for DemoAxiLitePlayer."""

import argparse
import struct
from pathlib import Path

MAGIC = 0x564F4F44
WRITE_CMDS = {0, 1, 2, 3, 4, 5}


def pack_trace(src: Path, dst: Path) -> int:
    data = src.read_bytes()
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MAGIC:
        raise SystemExit(f"{src} is not a VOODOO trace (magic {magic:#x})")
    off = 64
    out = bytearray()
    n = 0
    while off + 24 <= len(data):
        _ts, _tse, cmd, _pad, addr, val, count = struct.unpack_from("<IIB3sIII", data, off)
        off += 24
        if cmd in WRITE_CMDS:
            for _ in range(max(count, 1)):
                out += struct.pack("<II", addr, val)
                n += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    return n


def unpack_packed(src: Path, dst: Path) -> int:
    """Inverse of pack_trace: addr/data pairs → VOODOO PCI trace for CoreSim."""
    data = src.read_bytes()
    if len(data) % 8:
        raise SystemExit(f"{src} length {len(data)} is not a multiple of 8")
    n = len(data) // 8
    hdr = struct.pack(
        "<8IQ6I",
        MAGIC,
        1,  # version
        1,  # Voodoo 1
        4,  # fb_size_mb
        4,  # tex_size_mb
        1,  # num_tmus
        0,  # pci_base
        0,  # flags
        0,  # cpu_speed_hz
        0,  # pci_speed_hz
        n,  # entry_count
        0,
        0,
        0,
        0,
    )
    if len(hdr) != 64:
        raise SystemExit(f"header size {len(hdr)} != 64")
    out = bytearray(hdr)
    for i in range(n):
        addr, val = struct.unpack_from("<II", data, i * 8)
        cmd = 4 if addr >= 0x800000 else 0
        out += struct.pack("<IIB3sIII", i, i, cmd, b"\x00\x00\x00", addr, val, 1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    return n


def main() -> None:
    here = Path(__file__).resolve().parent
    repo = here.parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", nargs="?", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument(
        "--all",
        action="store_true",
        help="pack test04, test16 and test_alphabet into this directory",
    )
    parser.add_argument(
        "--unpack",
        action="store_true",
        help="packed addr/data pairs → VOODOO trace for CoreSim/86Box",
    )
    args = parser.parse_args()
    if args.unpack:
        src = args.trace or here / "demo_logo_spin.bin"
        dst = args.output or src.with_name(src.stem + "_trace.bin")
        n = unpack_packed(src, dst)
        print(f"wrote {dst} ({n} writes) from {src}")
        return
    if args.all or args.trace is None:
        traces = [
            ("test04.bin", "demo_test04.bin"),
            ("test16.bin", "demo_test16.bin"),
            ("test_alphabet.bin", "demo_test_alphabet.bin"),
        ]
        for src_name, dst_name in traces:
            n = pack_trace(repo / "ext/SpinalVoodoo/traces" / src_name, here / dst_name)
            print(f"wrote {here / dst_name} ({n} writes, {n * 8} bytes)")
        return
    dst = args.output or here / "demo_test04.bin"
    n = pack_trace(args.trace, dst)
    print(f"wrote {dst} ({n} writes, {n * 8} bytes) from {args.trace}")


if __name__ == "__main__":
    main()
