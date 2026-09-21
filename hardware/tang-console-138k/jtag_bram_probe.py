#!/usr/bin/env python3
"""Read and validate the open-flow block-RAM self-test through JTAG ER1."""

from __future__ import annotations

import argparse
import sys

from jtag_mailbox import scan_words


STATE_NAMES = ("WRITE", "READ", "CHECK", "DONE")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--interval", type=float, default=0.05)
    args = parser.parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1")

    words = scan_words([0] * args.count, args.interval)
    for index, word in enumerate(words):
        marker = word >> 16
        done = bool(word & (1 << 15))
        failed = bool(word & (1 << 14))
        state = (word >> 12) & 3
        address = word & 0x3FF
        print(
            f"{index:03}: status 0x{word:08x}, state {STATE_NAMES[state]}, "
            f"done {int(done)}, failed {int(failed)}, address 0x{address:03x}"
        )

    marker = words[-1] >> 16
    done = bool(words[-1] & (1 << 15))
    failed = bool(words[-1] & (1 << 14))
    address = words[-1] & 0x3FF
    if marker != 0xB5A5:
        raise SystemExit(f"bad probe marker 0x{marker:04x}")
    if not done:
        raise SystemExit("block-RAM self-test did not complete")
    if failed:
        raise SystemExit(f"block-RAM mismatch at address 0x{address:03x}")
    if address != 0x3FF:
        raise SystemExit(f"unexpected final address 0x{address:03x}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
