#!/usr/bin/env python3
"""Report tile-local fuse differences between GW5AST bitstreams."""

from __future__ import annotations

import argparse
from pathlib import Path

from apycula import attrids, bslib, chipdb
from apycula.gowin_unpack import parse_attrvals


def changed_bits(left: list[list[int]], right: list[list[int]]):
    return [
        (row, col, old, new)
        for row, (left_row, right_row) in enumerate(zip(left, right))
        for col, (old, new) in enumerate(zip(left_row, right_row))
        if old != new
    ]


def selected_pips(tiledata, tile):
    result = {}
    for destination, sources in tiledata.pips.items():
        domain = set().union(*sources.values())
        used = {bit for bit in domain if tile[bit[0]][bit[1]]}
        matches = [source for source, bits in sources.items() if bits == used]
        if len(matches) == 1:
            result[destination] = matches[0]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("bitstream", type=Path)
    parser.add_argument("--against", type=Path)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--tile", action="append", default=[])
    parser.add_argument("--decode-tile")
    args = parser.parse_args()

    database = chipdb.load_chipdb(args.database)
    bitmap, _, _, _ = bslib.read_bitstream(args.bitstream)
    if args.against is None and database.template is None:
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

    if args.decode_tile:
        row, col = (int(part) for part in args.decode_tile.split(",", 1))
        tiledata = database[row, col]
        current_pips = selected_pips(tiledata, tiles[(row, col)])
        reference_pips = selected_pips(tiledata, template_tiles[(row, col)])
        pip_names = sorted(set(current_pips) | set(reference_pips))
        print("pip differences:")
        for name in pip_names:
            current = current_pips.get(name)
            reference = reference_pips.get(name)
            if current != reference:
                print(f"  {name}: reference={reference} current={current}")

        print("slice attributes:")
        for table_name in sorted(database.shortval.get(tiledata.ttyp, {})):
            if not table_name.startswith("CLS"):
                continue
            table = database.shortval[tiledata.ttyp][table_name]
            current = parse_attrvals(
                tiles[(row, col)], database.rev_logicinfo("SLICE"), table,
                attrids.cls_attrids, table_name,
            )
            reference = parse_attrvals(
                template_tiles[(row, col)], database.rev_logicinfo("SLICE"),
                table, attrids.cls_attrids, table_name,
            )
            if current != reference:
                print(f"  {table_name}: reference={reference} current={current}")


if __name__ == "__main__":
    main()
