#!/usr/bin/env python3
"""Fail generation if a connector courtyard/bounding box overlaps a part."""

from pathlib import Path
import sys

import pcbnew


BOARD = Path(sys.argv[1]).resolve()
CONNECTORS = ("J2", "J4", "J5", "J6", "J11")


def bounds(fp: pcbnew.FOOTPRINT) -> tuple[float, float, float, float]:
    box = fp.GetBoundingBox(False, False)
    return tuple(
        pcbnew.ToMM(value)
        for value in (box.GetX(), box.GetY(), box.GetRight(), box.GetBottom())
    )


def overlaps(a: tuple[float, float, float, float],
             b: tuple[float, float, float, float]) -> bool:
    return (
        min(a[2], b[2]) - max(a[0], b[0]) > 0.01
        and min(a[3], b[3]) - max(a[1], b[1]) > 0.01
    )


def main() -> None:
    board = pcbnew.LoadBoard(str(BOARD))
    footprints = list(board.GetFootprints())
    by_ref = {fp.GetReference(): fp for fp in footprints}
    missing = [ref for ref in CONNECTORS if ref not in by_ref]
    if missing:
        raise RuntimeError(f"Missing connector footprints: {', '.join(missing)}")

    collisions: list[str] = []
    for ref in CONNECTORS:
        a = bounds(by_ref[ref])
        for other in footprints:
            other_ref = other.GetReference()
            if other_ref in CONNECTORS or other_ref == "J1":
                continue
            if overlaps(a, bounds(other)):
                collisions.append(f"{ref} overlaps {other_ref}")

    bracket = [bounds(by_ref[ref]) for ref in ("J2", "J4", "J5", "J6")]
    gaps = [lower[1] - upper[3] for upper, lower in zip(bracket, bracket[1:])]
    if min(gaps) < 0.75:
        collisions.append(f"bracket gap below 0.75 mm: {min(gaps):.2f} mm")
    if bracket[0][1] < 20.0 or bracket[-1][3] > 119.44:
        collisions.append("bracket stack exceeds the usable board edge")

    if collisions:
        raise RuntimeError("; ".join(collisions))

    print(
        "connector clearance OK; bracket gaps "
        + ", ".join(f"{gap:.2f} mm" for gap in gaps)
    )


if __name__ == "__main__":
    main()
