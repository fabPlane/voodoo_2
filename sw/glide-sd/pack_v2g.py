#!/usr/bin/env python3
"""Pack DemoTraceRom-style addr:data bins into a V2GL image for SD/JTAG."""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

MAGIC = 0x4C473256
VERSION = 1
MAX_SLOTS = 16
HEADER = 16
SLOT_BYTES = 16
TABLE = HEADER + MAX_SLOTS * SLOT_BYTES


def load_packed(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) < 8 or len(data) % 8:
        raise SystemExit(f"{path}: packed ROM must be a multiple of 8 bytes")
    return data


def pack(slots: list[tuple[bytes, int]]) -> bytes:
    if not slots or len(slots) > MAX_SLOTS:
        raise SystemExit(f"need 1..{MAX_SLOTS} slots, got {len(slots)}")
    recs = bytearray()
    table = bytearray()
    off = TABLE
    for raw, loop_at in slots:
        count = len(raw) // 8
        if count < 1:
            raise SystemExit("empty slot")
        if loop_at < 0 or loop_at >= count:
            raise SystemExit(f"loop-at {loop_at} out of range for {count} writes")
        table += struct.pack("<IIII", off, count, loop_at, 0)
        recs += raw
        off += len(raw)
    table += b"\x00" * (MAX_SLOTS * SLOT_BYTES - len(table))
    header = struct.pack("<IIII", MAGIC, VERSION, len(slots), 0)
    return header + table + recs


def write_fat(root: Path, image: bytes, sources: list[Path]) -> None:
    glide = root / "glide"
    traces = glide / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    (glide / "PLAY.V2G").write_bytes(image)
    lines = [
        "SpinalVoodoo Glide replay (V2GL)",
        f"bytes={len(image)} slots={len(sources)} ddr=0x11000000",
        "Menu: SD CARD PLAY  (A runs slot 0)",
        "",
    ]
    for i, src in enumerate(sources):
        dest = traces / src.name
        dest.write_bytes(src.read_bytes())
        lines.append(f"{i:02d}  {src.name}")
    (glide / "INDEX.TXT").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slot", action="append", default=[], metavar="PACKED.bin",
                    help="packed addr:data ROM (repeatable, max 16)")
    ap.add_argument("--loop-at", action="append", default=[], type=int,
                    help="loop index per slot (defaults to 0)")
    ap.add_argument("-o", "--output", type=Path, help="PLAY.V2G path")
    ap.add_argument("--fat", type=Path, help="write glide/ FAT tree here")
    args = ap.parse_args()
    if not args.slot:
        ap.error("pass at least one --slot")
    packed = [load_packed(Path(p)) for p in args.slot]
    loops = list(args.loop_at) + [0] * (len(packed) - len(args.loop_at))
    image = pack(list(zip(packed, loops)))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(image)
        print(f"wrote {args.output} ({len(image)} bytes, {len(packed)} slots)")
    if args.fat:
        write_fat(args.fat, image, [Path(p) for p in args.slot])
        print(f"FAT tree {args.fat}/glide")
    if not args.output and not args.fat:
        ap.error("pass -o and/or --fat")


if __name__ == "__main__":
    main()
