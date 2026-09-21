#!/usr/bin/env python3
"""Dump Voodoo's selectable GW_JTAG ER1 diagnostic pages."""

from __future__ import annotations

import argparse
import time

from jtag_mailbox import scan_words


PAGE_NAMES = (
    "signature", "prefetch/read_req", "read_rsp/fifo", "read_issue/physical",
    "physical_rsp/cdc_rsp", "cache_flags_lo", "cache_flags_hi", "read_addr",
    "expected_addr", "occupancy", "remaining", "fill_hits", "fill_misses",
    "fill_bursts", "fill_beats", "stall_cycles",
)


def read_pages(base: int = 0) -> list[int]:
    # The first scan selects a page; the second returns it.  Repeating each
    # selector keeps the protocol independent of the previously selected page.
    # GW_JTAG's 34-bit DR framing consumes the low transport bit; compensate
    # here so the fabric receives the intended six-bit page number.
    selectors = [(base + page) << 1 for page in range(16) for _ in range(2)]
    values = scan_words(selectors)
    return values[1::2]


def print_pages(values: list[int]) -> None:
    for page, (name, value) in enumerate(zip(PAGE_NAMES, values)):
        print(f"{page:x} {name:24s} 0x{value:08x}")

    pf, rq = values[1] >> 16, values[1] & 0xffff
    rs, fifo = values[2] >> 16, values[2] & 0xffff
    ri, rp = values[3] >> 16, values[3] & 0xffff
    rv, rr = values[4] >> 16, values[4] & 0xffff
    print(f"PF={pf:04x} RQ={rq:04x} RS={rs:04x} F={fifo:04x} "
          f"RI={ri:04x} RP={rp:04x} RV={rv:04x} RR={rr:04x}")


def print_framebuffer(values: list[int]) -> None:
    meta = values[1]
    sample_count = meta & 0xffff
    nonzero = (meta >> 16) & 0x1f
    write_index = (meta >> 21) & 0xf
    half = (meta >> 25) & 1
    last_pixel = values[3] & 0xffff
    last_addr = values[3] >> 16
    pixels = []
    for word in values[4:]:
        pixels.extend((word & 0xffff, word >> 16))

    print(f"signature=0x{values[0]:08x} samples={sample_count:04x} "
          f"nonzero={nonzero}/24 write={write_index} half={half}")
    print(f"nonzero_total=0x{values[2]:08x} last_addr=0x{last_addr:04x} "
          f"last_pixel=0x{last_pixel:04x}")
    for row in range(0, 24, 8):
        print("pixels " + " ".join(f"{pixel:04x}" for pixel in pixels[row:row + 8]))


def print_writes(values: list[int]) -> None:
    transactions, writes = values[1] >> 16, values[1] & 0xffff
    physical, physical_writes = values[2] >> 16, values[2] & 0xffff
    bar_writes, player_index = values[5] >> 16, values[5] & 0xffff
    mask = values[11] & 0xffff
    status = values[15]
    print(f"signature=0x{values[0]:08x} tx={transactions:04x} wr={writes:04x} "
          f"physical={physical:04x} physical_wr={physical_writes:04x}")
    print(f"bar_writes={bar_writes:04x} player={player_index:04x} "
          f"bar_addr=0x{values[3]:08x} bar_data=0x{values[4]:08x}")
    print(f"native_addr=0x{values[6]:08x} mask=0x{mask:04x} "
          f"nonzero_writes=0x{values[12]:08x}")
    print("native_data " + " ".join(f"{word:08x}" for word in values[7:11]))
    print(f"busy=0x{values[13]:08x} wedge_pc=0x{values[14]:08x} "
          f"status=0x{status:08x}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=1, help="number of dumps")
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--framebuffer", action="store_true",
                        help="dump the rolling raw scanout pixel window")
    parser.add_argument("--writes", action="store_true",
                        help="dump BAR/player and native SDRAM write diagnostics")
    args = parser.parse_args()
    for index in range(args.watch):
        if index:
            time.sleep(args.interval)
            print()
        base = 0x20 if args.writes else (0x10 if args.framebuffer else 0)
        values = read_pages(base)
        if args.writes:
            print_writes(values)
        elif args.framebuffer:
            print_framebuffer(values)
        else:
            print_pages(values)


if __name__ == "__main__":
    main()
