"""Pull the Scala constant-table literals out of the submodule into JSON.

One-off, but kept so the ground truth the tests compare against is
reproducible rather than pasted::

    cd amaranth && python -m tools.extract_spinal_tables

writes

* ``tests/data/dither_tables_spinal.json`` — the eight arrays of
  ``ext/SpinalVoodoo/src/voodoo/pixel/DitherTableData.scala`` (10 240 ints),
  ground truth for :mod:`avoodoo.pixel.dither_tables`;
* ``tests/data/tmu_tables_spinal.json`` — the 256-entry ``logTableValues``
  literal of ``ext/SpinalVoodoo/src/voodoo/texture/Tmu.scala``, ground truth
  for :mod:`avoodoo.texture.tmu_tables`.

Both files also record whether the Scala numbers agree with the 86Box C source
they came from (``emu/86Box/src/include/86box/vid_voodoo_dither.h`` and
``emu/86Box/src/video/vid_voodoo_render.c``); they do, exactly, and the tests
re-check it whenever the submodule is checked out.

Nothing here imports Amaranth or ``avoodoo``: it only reads the (read-only)
submodule and writes JSON.  Run it again after a submodule bump.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UPSTREAM = REPO / "ext" / "SpinalVoodoo"
DITHER_SCALA = UPSTREAM / "src" / "voodoo" / "pixel" / "DitherTableData.scala"
TMU_SCALA = UPSTREAM / "src" / "voodoo" / "texture" / "Tmu.scala"
DITHER_H = UPSTREAM / "emu" / "86Box" / "src" / "include" / "86box" / "vid_voodoo_dither.h"
RENDER_C = UPSTREAM / "emu" / "86Box" / "src" / "video" / "vid_voodoo_render.c"

DATA_DIR = Path(__file__).resolve().parents[1] / "tests" / "data"

#: Scala array name -> 86Box C array name.
DITHER_NAMES = {
    "rb4x4": "dither_rb",
    "g4x4": "dither_g",
    "rb2x2": "dither_rb2x2",
    "g2x2": "dither_g2x2",
    "dsubRb4x4": "dithersub_rb",
    "dsubG4x4": "dithersub_g",
    "dsubRb2x2": "dithersub_rb2x2",
    "dsubG2x2": "dithersub_g2x2",
}


def _balanced(text: str, start: int, opener: str, closer: str) -> str:
    """Body between ``opener`` at/after ``start`` and its matching ``closer``."""
    i = text.index(opener, start)
    depth = 0
    j = i
    while True:
        if text[j] == opener:
            depth += 1
        elif text[j] == closer:
            depth -= 1
            if depth == 0:
                return text[i + 1 : j]
        j += 1


def scala_arrays(path: Path, names: set[str] | None = None) -> dict[str, list[int]]:
    """Every ``lazy val <name>: Array[Int] = Array(...)`` / ``val <name>: Array[Int] = Array(...)``."""
    text = path.read_text()
    out: dict[str, list[int]] = {}
    pattern = re.compile(r"(?:lazy\s+)?val\s+(\w+)\s*:\s*Array\[Int\]\s*=\s*Array\(")
    for m in pattern.finditer(text):
        name = m.group(1)
        if names is not None and name not in names:
            continue
        body = _balanced(text, m.end() - 1, "(", ")")
        out[name] = [int(tok, 0) for tok in re.findall(r"-?0x[0-9a-fA-F]+|-?\d+", body)]
    return out


def c_arrays(path: Path) -> dict[str, list[int]]:
    """Every ``static const uint8_t <name>[...][n][n] = {...}`` / ``uint8_t <name>[256]``."""
    text = path.read_text()
    out: dict[str, list[int]] = {}
    pattern = re.compile(r"static\s+(?:const\s+)?uint8_t\s+(\w+)\s*(\[[^=]*?\])\s*=\s*\{")
    for m in pattern.finditer(text):
        body = _balanced(text, m.end() - 1, "{", "}")
        out[m.group(1)] = [int(tok, 0) for tok in re.findall(r"0x[0-9a-fA-F]+|\d+", body)]
    return out


def extract_dither() -> dict:
    scala = scala_arrays(DITHER_SCALA, set(DITHER_NAMES))
    missing = set(DITHER_NAMES) - set(scala)
    if missing:
        raise SystemExit(f"{DITHER_SCALA}: missing arrays {sorted(missing)}")
    matches_86box = None
    if DITHER_H.exists():
        c = c_arrays(DITHER_H)
        matches_86box = all(scala[s] == c.get(cname) for s, cname in DITHER_NAMES.items())
    return {
        "_source": str(DITHER_SCALA.relative_to(REPO)),
        "_c_source": str(DITHER_H.relative_to(REPO)),
        "_86box_names": DITHER_NAMES,
        "_matches_86box": matches_86box,
        "tables": scala,
    }


def extract_tmu() -> dict:
    scala = scala_arrays(TMU_SCALA, {"logTableValues"})
    if "logTableValues" not in scala:
        raise SystemExit(f"{TMU_SCALA}: no logTableValues array")
    matches_86box = None
    if RENDER_C.exists():
        matches_86box = c_arrays(RENDER_C).get("logtable") == scala["logTableValues"]
    return {
        "_source": str(TMU_SCALA.relative_to(REPO)),
        "_c_source": str(RENDER_C.relative_to(REPO)),
        "_86box_names": {"logTableValues": "logtable"},
        "_matches_86box": matches_86box,
        "tables": scala,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("dither_tables_spinal.json", extract_dither()),
        ("tmu_tables_spinal.json", extract_tmu()),
    ):
        path = args.out_dir / name
        path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        counts = {k: len(v) for k, v in payload["tables"].items()}
        print(f"{path}: 86box match={payload['_matches_86box']} {counts}")


if __name__ == "__main__":
    main()
