#!/usr/bin/env python3
"""Collect non-executing ELF metadata for Gowin IDE/bin programs."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


BIN_DIR = Path("/home/hyper/opt/gowin/IDE/bin")


def command(*args: str) -> str:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    return result.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


records = []
for path in sorted(BIN_DIR.iterdir()):
    if not path.is_file() or not os.access(path, os.X_OK):
        continue
    description = command("file", "-b", str(path)).strip()
    if "ELF 64-bit" not in description:
        continue

    dynamic = command("readelf", "-dW", str(path))
    notes = command("readelf", "-nW", str(path))
    headers = command("readelf", "-hW", str(path))
    program_headers = command("readelf", "-lW", str(path))
    section_sizes = command("size", str(path)).splitlines()
    dyn_symbols = command("nm", "-D", "--defined-only", str(path)).splitlines()
    all_symbols = command("nm", "--defined-only", str(path)).splitlines()

    needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)
    runpath_match = re.search(r"\((?:RUNPATH|RPATH)\).*?\[(.*?)\]", dynamic)
    build_id_match = re.search(r"Build ID:\s*([0-9a-f]+)", notes)
    entry_match = re.search(r"Entry point address:\s*(\S+)", headers)
    interpreter_match = re.search(r"Requesting program interpreter:\s*([^\]]+)", program_headers)

    size_values = {}
    if len(section_sizes) >= 2:
        fields = section_sizes[-1].split()
        if len(fields) >= 4:
            size_values = dict(zip(("text", "data", "bss", "dec"), map(int, fields[:4])))

    stat = path.stat()
    records.append({
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "sha256": sha256(path),
        "file": description,
        "entry": entry_match.group(1) if entry_match else None,
        "build_id": build_id_match.group(1) if build_id_match else None,
        "interpreter": interpreter_match.group(1) if interpreter_match else None,
        "needed": needed,
        "runpath": runpath_match.group(1) if runpath_match else None,
        "dynamic_defined_symbols": len(dyn_symbols),
        "all_defined_symbols": len(all_symbols),
        "segments": size_values,
    })

print(json.dumps({"binaries": records}, indent=2, sort_keys=True))
