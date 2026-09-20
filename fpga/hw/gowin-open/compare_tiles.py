#!/usr/bin/env python3
"""Report tile-local fuse differences between GW5AST bitstreams."""

from __future__ import annotations

import argparse
from pathlib import Path

from apycula import bslib, chipdb


def changed_bits(left: list[list[int]], right: list[list[int]]):
    return [
        (row, col, old, new)
        for row, (left_row, right_row) in enumerate(zip(left, right))
        for col, (old, new) in enumerate(zip(left_row, right_row))
        if old != new
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("bitstream", type=Path)
    parser.add_argument("--against", type=Path)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--tile", action="append", default=[])
    args = parser.parse_args()

    database = chipdb.load_chipdb(args.database)
    bitmap, _, _, _ = bslib.read_bitstream(args.bitstream)
    if database.template is None:
        raise RuntimeError("database has no template bitmap")

    tiles = chipdb.tile_bitmap(database, bitmap, empty=True)
    if args.against:
        reference_bitmap, _, _, _ = bslib.read_bitstream(args.against)
        template_tiles = chipdb.tile_bitmap(database, reference_bitmap, empty=True)
    else:
        template_tiles = chipdb.tile_bitmap(database, database.template, empty=True)
    differences = {
        location: changed_bits(template_tiles[location], tile)
        for location, tile in tiles.items()
    }
    ranked = sorted(
        ((len(bits), location) for location, bits in differences.items() if bits),
        reverse=True,
    )

    print(f"changed tiles: {len(ranked)}")
    for count, location in ranked[: args.top]:
        print(f"tile {location}: {count} bits")

    for tile_text in args.tile:
        row, col = (int(part) for part in tile_text.split(",", 1))
        bits = differences[(row, col)]
        print(f"tile ({row}, {col}) bits: {bits}")


if __name__ == "__main__":
    main()
