#!/usr/bin/env python3
"""Sample GW5AST boundary cells through the connected Gowin JTAG cable.

This is deliberately read-only.  It uses Programmer's BSDL-read operation and
reports which boundary bits changed between samples, allowing clocked output
tests to be checked without relying on visible LEDs.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import time


PROGRAMMER = (
    "/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA/Programmer/"
    "bin/programmer_cli"
)
VALUE_RE = re.compile(r"^Value: ([01]+)$", re.MULTILINE)


def capture() -> str:
    result = subprocess.run(
        [
            PROGRAMMER,
            "--device",
            "GW5AST-138C",
            "--operation_index",
            "18",
            "--cable-index",
            "4",
            "--frequency",
            "2.5MHz",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    match = VALUE_RE.search(result.stdout)
    if not match:
        raise RuntimeError("Programmer returned no boundary-scan Value")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()

    first = capture()
    previous = first
    ever_changed: set[int] = set()
    print(f"sample 0: {len(first)} boundary bits")

    for sample_index in range(1, args.count):
        time.sleep(args.interval)
        current = capture()
        if len(current) != len(first):
            raise RuntimeError(
                f"boundary length changed from {len(first)} to {len(current)}"
            )
        changed = {
            index for index, (old, new) in enumerate(zip(previous, current))
            if old != new
        }
        ever_changed.update(changed)
        print(f"sample {sample_index}: changed {sorted(changed)}")
        previous = current

    print(f"ever changed: {sorted(ever_changed)}")


if __name__ == "__main__":
    main()
