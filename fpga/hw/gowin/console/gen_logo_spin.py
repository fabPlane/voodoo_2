#!/usr/bin/env python3
"""Build a compact SST-1 spinning-chevron stream for the Console menu.

The official Glide grSplash / traces/logo capture is ~100k PCI commands and
will not fit in on-chip ROM. This keeps the working test04 FBI bring-up, then
loops a 3-triangle 3dfx-orange dart so A/Start on the menu actually spins.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

LOOP_AT = 362
FRAMES = 36
OUT_NAME = "demo_logo_spin.bin"
# Fastfill stays inside the 160x120 scanout window. A full-screen clear
# keeps the DDR port busy and the HDMI queue paints black bars.
CLIP_LEFT = 240
CLIP_RIGHT = 400
CLIP_TOP = 180
CLIP_BOTTOM = 300


def f32(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


def load_pairs(path: Path) -> list[tuple[int, int]]:
    data = path.read_bytes()
    return [struct.unpack_from("<II", data, i) for i in range(0, len(data), 8)]


def area_cmd(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> int:
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return 0x80000000 if area < 0 else 0


def triangle(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float, r: float, g: float, b: float
) -> list[tuple[int, int]]:
    return [
        (0x088, f32(ax)),
        (0x08C, f32(ay)),
        (0x090, f32(bx)),
        (0x094, f32(by)),
        (0x098, f32(cx)),
        (0x09C, f32(cy)),
        (0x0A0, f32(r)),
        (0x0C0, 0),
        (0x0E0, 0),
        (0x0A4, f32(g)),
        (0x0C4, 0),
        (0x0E4, 0),
        (0x0A8, f32(b)),
        (0x0C8, 0),
        (0x0E8, 0),
        (0xE20000, 0),
        (0x100, area_cmd(ax, ay, bx, by, cx, cy)),
    ]


def project(x: float, y: float, z: float, ang: float) -> tuple[float, float]:
    c, s = math.cos(ang), math.sin(ang)
    xr = x * c + z * s
    zr = -x * s + z * c
    # Full-frame scale is 210, which puts the tip outside the 160x120 window.
    scale = 110.0 / (zr + 3.2)
    cx = (CLIP_LEFT + CLIP_RIGHT) / 2
    cy = (CLIP_TOP + CLIP_BOTTOM) / 2
    return cx + xr * scale, cy - y * scale


def rotate_face(
    verts: list[tuple[float, float, float]], ang: float
) -> list[tuple[float, float]]:
    return [project(x, y, z, ang) for x, y, z in verts]


def frame_writes(ang: float) -> list[tuple[int, int]]:
    # Clear only the scanout window, then the 3-triangle dart. The FBI clip
    # drops fragments outside that window.
    out: list[tuple[int, int]] = [
        (0x148, 0),
        (0x144, 0),
        (0x130, 0),
        (0x118, (CLIP_LEFT << 16) | CLIP_RIGHT),
        (0x11C, (CLIP_TOP << 16) | CLIP_BOTTOM),
        (0x110, 0x4200),
        (0x124, 0),
        (0x120, 1),
    ]
    dart = [
        ((-1.15, 0.95, 0.0), (-1.15, -0.95, 0.0), (1.35, 0.0, 0.0), (255.0, 102.0, 0.0)),
        ((-1.15, 0.95, 0.0), (1.35, 0.0, 0.0), (-1.15, 0.95, 0.45), (255.0, 204.0, 0.0)),
        ((-1.15, -0.95, 0.0), (-1.15, -0.95, 0.45), (1.35, 0.0, 0.0), (160.0, 48.0, 0.0)),
    ]
    for a, b, c, rgb in dart:
        pa, pb, pc = rotate_face([a, b, c], ang)
        out.extend(triangle(pa[0], pa[1], pb[0], pb[1], pc[0], pc[1], *rgb))
    out.append((0x120, 1))
    out.append((0x128, 1))
    return out


def main() -> None:
    here = Path(__file__).resolve().parent
    src = here / "demo_test04.bin"
    if not src.exists():
        raise SystemExit(f"missing {src}; run pack_demo_trace.py --all")
    test04 = load_pairs(src)
    if len(test04) <= LOOP_AT or test04[LOOP_AT][0] != 0x88:
        raise SystemExit(f"{src} prefix mismatch; expected 0x88 at write {LOOP_AT}")
    out = list(test04[:LOOP_AT])
    for i in range(FRAMES):
        out.extend(frame_writes(2 * math.pi * i / FRAMES))
    dst = here / OUT_NAME
    blob = b"".join(struct.pack("<II", a, d) for a, d in out)
    dst.write_bytes(blob)
    print(
        f"wrote {dst} ({len(out)} writes, {len(blob)} bytes, "
        f"loop_at={LOOP_AT}, frames={FRAMES})"
    )
    import sys

    sys.path.insert(0, str(here))
    from pack_demo_trace import unpack_packed

    trace = here / "demo_logo_spin_trace.bin"
    unpack_packed(dst, trace)
    print(f"wrote {trace} for CoreSim/86Box replay")


if __name__ == "__main__":
    main()
