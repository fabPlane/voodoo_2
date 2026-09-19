#!/usr/bin/env python3
"""Convert a Gowin text bitstream (.fs) to its packed binary form (.bin)."""

import argparse
from pathlib import Path
import tempfile


def convert(source: Path, destination: Path) -> None:
    pending = ""
    destination.parent.mkdir(parents=True, exist_ok=True)

    with source.open("r", encoding="ascii") as src, tempfile.NamedTemporaryFile(
        mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as dst:
        temporary = Path(dst.name)
        try:
            for number, line in enumerate(src, 1):
                text = line.strip()
                if not text or text.startswith("//"):
                    continue
                if set(text) - {"0", "1"}:
                    raise ValueError(f"{source}:{number}: expected only binary digits")

                pending += text
                whole_bytes = len(pending) // 8
                if whole_bytes:
                    bits = pending[: whole_bytes * 8]
                    dst.write(bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8)))
                    pending = pending[whole_bytes * 8 :]

            if pending:
                raise ValueError(f"{source}: bit count is not divisible by 8")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    temporary.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    convert(args.source, args.destination)


if __name__ == "__main__":
    main()
