#!/usr/bin/env python3
"""Pack a SpinalVoodoo Glide trace into addr/data pairs for the Tang Console
on-chip demo player (DemoAxiLitePlayer), and/or summarise its command mix.

The console player ROM format is a flat little-endian stream of
    addr:u32, data:u32
records - exactly the BAR writes the Voodoo core replays. This is the same
format consumed by voodoo.gowin.DemoTraceRom on the FPGA.

Input is the VOODOO binary trace produced by the Glide sim trace harness
(64-byte header, 24-byte entries; see emu/trace/voodoo_trace_format.h).
"""
import argparse
import struct
import sys
from pathlib import Path

MAGIC = 0x564F4F44  # "VOOD"
HEADER_LEN = 64
ENTRY_LEN = 24

# cmd_type values that are writes worth replaying on the BAR.
WRITE_CMDS = {0, 1, 2, 3, 4, 5}
CMD_NAMES = {
    0: "reg32", 1: "reg16", 2: "fb32", 3: "fb16", 4: "tex32",
    5: "cmdfifo", 6: "rdreg32", 7: "rdreg16", 8: "rdfb32", 9: "rdfb16",
    10: "vsync", 11: "swap", 12: "config", 13: "frame_end",
}


def read_entries(src: Path):
    data = src.read_bytes()
    if len(data) < HEADER_LEN or struct.unpack_from("<I", data, 0)[0] != MAGIC:
        raise SystemExit(f"{src}: not a VOODOO trace")
    off = HEADER_LEN
    while off + ENTRY_LEN <= len(data):
        ts, tse, cmd, _pad, addr, val, count = struct.unpack_from(
            "<IIB3sIII", data, off)
        off += ENTRY_LEN
        yield cmd, addr, val, count


def pack_trace(src: Path, dst: Path) -> int:
    out = bytearray()
    n = 0
    for cmd, addr, val, count in read_entries(src):
        if cmd in WRITE_CMDS:
            for _ in range(max(count, 1)):
                out += struct.pack("<II", addr, val)
                n += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    return n


def summarise(src: Path) -> None:
    hist = {}
    regs = set()
    swaps = 0
    packed = 0
    for cmd, addr, val, count in read_entries(src):
        c = max(count, 1)
        hist[cmd] = hist.get(cmd, 0) + c
        if cmd == 11:
            swaps += c
        if cmd in WRITE_CMDS:
            packed += c
            if addr < 0x400:
                regs.add(addr)
    parts = [f"{CMD_NAMES.get(k, k)}={v}" for k, v in sorted(hist.items())]
    print(f"{src.name}: packed_writes={packed} rom_bytes={packed * 8} "
          f"swaps={swaps} sst_regs={len(regs)}")
    print(f"    commands: {', '.join(parts)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trace", type=Path, help="input VOODOO trace (.bin)")
    ap.add_argument("-o", "--output", type=Path, help="packed ROM output")
    ap.add_argument("--summary", action="store_true",
                    help="print a command-mix summary")
    args = ap.parse_args()

    if args.summary:
        summarise(args.trace)
    if args.output:
        n = pack_trace(args.trace, args.output)
        print(f"packed {n} addr:data pairs -> {args.output} "
              f"({n * 8} bytes)", file=sys.stderr)
    if not args.summary and not args.output:
        ap.error("nothing to do: pass --summary and/or -o OUTPUT")


if __name__ == "__main__":
    main()
