#!/usr/bin/env python3
"""Read the open-flow 50 MHz counter through GW_JTAG ER1."""

from __future__ import annotations

import argparse
import sys

from jtag_mailbox import scan_words


def gray_to_binary(gray: int) -> int:
    value = gray
    shift = gray >> 1
    while shift:
        value ^= shift
        shift >>= 1
    return value & 0xFFFF_FFFF


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    if args.count < 2:
        raise SystemExit("--count must be at least 2")

    gray_words = scan_words([0] * args.count, args.interval)
    counters = [gray_to_binary(word) for word in gray_words]
    changed = False
    for index, value in enumerate(counters):
        if index == 0:
            print(f"{index:03}: counter 0x{value:08x}")
            continue
        delta = (value - counters[index - 1]) & 0xFFFF_FFFF
        changed |= delta != 0
        print(f"{index:03}: counter 0x{value:08x}, delta {delta}")
    if not changed:
        raise SystemExit("counter did not advance; the 50 MHz path is not running")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
