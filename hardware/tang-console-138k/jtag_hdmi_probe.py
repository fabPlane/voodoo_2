#!/usr/bin/env python3
"""Read clock and timing state from the open HDMI color-bar image."""

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
    return value & 0xff


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()

    words = scan_words([0] * args.count, args.interval)
    previous = None
    changed = [False, False, False]
    for index, word in enumerate(words):
        if word >> 28 != 0xd:
            raise RuntimeError(f"bad HDMI probe signature in 0x{word:08x}")
        frame = gray_to_binary((word >> 16) & 0xff)
        pixel = gray_to_binary((word >> 8) & 0xff)
        serial = gray_to_binary(word & 0xff)
        flags = (bool(word & (1 << 27)), bool(word & (1 << 26)),
                 bool(word & (1 << 25)), bool(word & (1 << 24)))
        deltas = None if previous is None else tuple(
            (now - old) & 0xff
            for now, old in zip((frame, pixel, serial), previous)
        )
        if deltas is not None:
            changed = [old or delta != 0 for old, delta in zip(changed, deltas)]
        print(f"{index:03}: 0x{word:08x} lock={int(flags[0])} "
              f"reset_n={int(flags[1])} user_n={int(flags[2])} "
              f"active={int(flags[3])} frame={frame:02x} pixel={pixel:02x} "
              f"serial={serial:02x}" +
              ("" if deltas is None else f" delta={deltas}"))
        previous = (frame, pixel, serial)

    if not all(changed):
        names = [name for name, did_change in
                 zip(("frame", "pixel", "serial"), changed) if not did_change]
        raise RuntimeError("counter did not advance: " + ", ".join(names))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
