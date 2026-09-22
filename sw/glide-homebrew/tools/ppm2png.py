#!/usr/bin/env python3
"""Convert a binary PPM (P6) framebuffer dump to PNG. Stdlib only (zlib)."""
import struct
import sys
import zlib
from pathlib import Path


def read_ppm(path: Path):
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise SystemExit(f"{path}: not a binary PPM (P6)")
    # Parse header: P6 <w> <h> <maxval> then binary rows.
    idx = 2
    fields = []
    while len(fields) < 3:
        while idx < len(data) and data[idx] in b" \t\r\n":
            idx += 1
        if idx < len(data) and data[idx:idx + 1] == b"#":
            while idx < len(data) and data[idx] not in b"\r\n":
                idx += 1
            continue
        start = idx
        while idx < len(data) and data[idx] not in b" \t\r\n":
            idx += 1
        fields.append(int(data[start:idx]))
    idx += 1  # single whitespace after maxval
    w, h, _maxval = fields
    return w, h, data[idx:idx + w * h * 3]


def write_png(path: Path, w: int, h: int, rgb: bytes) -> None:
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * w * 3:(y + 1) * w * 3]
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: ppm2png.py in.ppm out.png")
    w, h, rgb = read_ppm(Path(sys.argv[1]))
    if len(rgb) < w * h * 3:
        raise SystemExit(f"short pixel data: {len(rgb)} < {w*h*3}")
    write_png(Path(sys.argv[2]), w, h, rgb)
    print(f"{sys.argv[2]}: {w}x{h}")


if __name__ == "__main__":
    main()
