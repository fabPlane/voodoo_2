#!/usr/bin/env python3
"""Capture every JPEG from a short MJPEG diagnostic-camera interval."""

from __future__ import annotations

import argparse
import hashlib
import time
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="full token-protected /stream.mjpg URL")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=Path("webcam-burst"))
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.seconds
    buffer = bytearray()
    seen: set[str] = set()
    saved = 0
    started = time.time()

    with urllib.request.urlopen(args.url, timeout=args.timeout) as stream:
        while time.monotonic() < deadline:
            chunk = stream.read(65536)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9", start + 2)
                if start < 0 or end < 0:
                    if start > 0:
                        del buffer[:start]
                    break
                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                digest = hashlib.sha256(frame).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                elapsed_ms = round((time.time() - started) * 1000)
                path = args.output / f"frame-{saved:04d}-{elapsed_ms:06d}ms.jpg"
                path.write_bytes(frame)
                saved += 1

    print(f"captured {saved} distinct frames in {args.output}")


if __name__ == "__main__":
    main()
