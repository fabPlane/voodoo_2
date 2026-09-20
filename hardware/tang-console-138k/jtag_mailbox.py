#!/usr/bin/env python3
"""Read and write the 32-bit GW_JTAG ER1 diagnostic mailbox.

The matching FPGA logic is fpga/hw/gowin-open/rtl/jtag_mailbox.v.  Gowin's
GW_JTAG block registers its serial input/output, so a 32-bit payload is framed
as a 34-bit scan with one low padding bit: wire_value = payload << 1.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


CONFIG = Path(__file__).with_name("openocd-gowin-ft2232.cfg")
RESULT_RE = re.compile(r"MAILBOX(\d+)=([0-9a-fA-F]+)")


def scan_words(words: list[int], interval: float = 0.0) -> list[int]:
    command = [
        "openocd",
        "-f", str(CONFIG),
        "-c", "init",
        "-c", "irscan gowin.fpga 0x42",
    ]
    for index, word in enumerate(words):
        if not 0 <= word <= 0xFFFF_FFFF:
            raise ValueError(f"mailbox value out of range: {word:#x}")
        wire_value = word << 1
        command.extend([
            "-c",
            f"echo MAILBOX{index}=[drscan gowin.fpga 34 0x{wire_value:09x}]",
        ])
        if interval and index != len(words) - 1:
            command.extend(["-c", f"sleep {round(interval * 1000)}"])
    command.extend(["-c", "shutdown"])

    result = subprocess.run(command, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    if result.returncode:
        detail = combined.strip() or f"OpenOCD exited with status {result.returncode}"
        raise RuntimeError(detail)
    found = {int(index): int(raw, 16) for index, raw in RESULT_RE.findall(combined)}
    if len(found) != len(words):
        raise RuntimeError(f"expected {len(words)} scans, got {len(found)}\n{combined}")
    return [((found[index] >> 1) & 0xFFFF_FFFF) for index in range(len(words))]


def main() -> None:
    parser = argparse.ArgumentParser()
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--read", action="store_true")
    operation.add_argument("--write", type=lambda value: int(value, 0))
    operation.add_argument(
        "--sequence",
        help="comma-separated values to write repeatedly, e.g. 0,1",
    )
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()

    if args.read:
        current = scan_words([0])[0]
        restored = scan_words([current, current])
        if restored[1] != current:
            raise RuntimeError(
                f"restore verification failed: {restored[1]:#010x} != {current:#010x}"
            )
        print(f"0x{current:08x}")
        return

    if args.write is not None:
        previous, readback = scan_words([args.write, args.write])
        print(f"previous 0x{previous:08x}")
        print(f"readback 0x{readback:08x}")
        if readback != args.write:
            raise SystemExit("mailbox write verification failed")
        return

    values = [int(value.strip(), 0) for value in args.sequence.split(",")]
    if not values or args.count < 1:
        raise SystemExit("sequence and count must not be empty")
    writes = values * args.count
    previous = scan_words(writes, args.interval)
    for index, (written, old) in enumerate(zip(writes, previous)):
        print(f"{index:03}: wrote 0x{written:08x}, previous 0x{old:08x}")
        if index and old != writes[index - 1]:
            raise SystemExit(
                f"mailbox sequence verification failed at transfer {index}"
            )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
