"""Generate ``docs/amaranth/register-map.md`` from ``avoodoo.frontend.regdef``.

    cd amaranth && uv run python -m gen.gen_register_map          # write the doc
    cd amaranth && uv run python -m gen.gen_register_map --check  # CI-style diff

The cross-check section is computed, not written by hand: the datasheet
register list is parsed out of the section 5 table in
``ext/SpinalVoodoo/docs/sst-1.md`` (read-only submodule) and compared with the
table this repo implements, in both directions.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from avoodoo.frontend import regdef
from avoodoo.frontend.regdef import Register

REPO = Path(__file__).resolve().parents[2]
DATASHEET = REPO / "ext" / "SpinalVoodoo" / "docs" / "sst-1.md"
OUT = REPO / "docs" / "amaranth" / "register-map.md"

# ``| name | 0x160(352)<br>to<br>0x1dc(476) | ...`` or ``| name | 0x000(0) | ...``
_ROW = re.compile(
    r"^\|\s*(?P<name>[A-Za-z_][\w]*)\s*\|\s*"
    r"0x(?P<start>[0-9a-f]{3})\(\d+\)"
    r"(?:<br>to<br>0x(?P<end>[0-9a-f]{3})\(\d+\))?\s*\|"
)


def parse_datasheet(path: Path) -> dict[int, str]:
    """Addresses named in the SST-1 section 5 register table.

    The datasheet prints the table twice: first with triangle-register
    remapping disabled (``fbiInit3(0)`` = 0), then again with it enabled. Only
    the first copy is the layout RegisterBank implements, so parsing stops when
    ``status`` shows up a second time.
    """
    out: dict[int, str] = {}
    seen_status = False
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        name = m.group("name")
        if name == "status":
            if seen_status:
                break
            seen_status = True
        if not seen_status or name in ("reserved", "Register"):
            continue
        start = int(m.group("start"), 16)
        end = int(m.group("end"), 16) if m.group("end") else start
        for addr in range(start, end + 1, 4):
            out[addr] = name if start == end else f"{name}[{(addr - start) // 4}]"
    return out


def _category_cell(reg: Register) -> str:
    if reg.category is None:
        return "*(none)* -> FIFO=No"
    fifo = "No" if reg.category.fifo_bypass else "Yes"
    sync = "Yes" if reg.category.sync_required else "No"
    return f"`{reg.category.spinal_name}` (FIFO={fifo}, Sync={sync})"


def _fields_cell(reg: Register) -> str:
    if not reg.fields:
        return "*(pulse only)*"
    parts = []
    for f in reg.fields:
        bits = f"[{f.hi}]" if f.width == 1 else f"[{f.hi}:{f.lo}]"
        cell = f"`{f.name}`{bits} {f.access.value}"
        if f.reset:
            cell += f" =0x{f.reset:x}"
        if f.qformat is not None:
            cell += f" {f.qformat}"
        parts.append(cell)
    return "<br>".join(parts)


def _alias_cell(reg: Register) -> str:
    for alias in regdef.FLOAT_ALIASES:
        if alias.target_addr == reg.addr:
            return f"`{alias.name}` 0x{alias.addr:03x} (>>{alias.shift})"
    return ""


def _notes_cell(reg: Register) -> str:
    bits = []
    if reg.command:
        index = regdef.command_by_addr(reg.addr).ready_index
        bits.append(f"command stream, `commandReady[{index}]`")
    if reg.sim_only:
        bits.append("simulation only")
    if reg.effects:
        read_only = bool(reg.fields) and all(f.access.value == "RO" for f in reg.fields)
        bits.append("hardware-driven" if read_only else "write side effects")
    return "; ".join(bits)


def render() -> str:
    rows = []
    for reg in regdef.REGISTERS:
        rows.append(
            "| `0x{addr:03x}` | `{name}` | {doc} | {cat} | {alias} | {fields} | {notes} |".format(
                addr=reg.addr,
                name=reg.name,
                doc=reg.doc,
                cat=_category_cell(reg),
                alias=_alias_cell(reg) or "—",
                fields=_fields_cell(reg),
                notes=_notes_cell(reg) or "—",
            )
        )

    datasheet = parse_datasheet(DATASHEET)
    implemented = {r.addr for r in regdef.REGISTERS}
    alias_addrs = {a.addr for a in regdef.FLOAT_ALIASES}
    covered = implemented | alias_addrs

    missing = sorted(a for a in datasheet if a not in covered)
    extra = sorted(a for a in implemented if a not in datasheet)

    missing_rows = [
        f"| `0x{a:03x}` | `{datasheet[a]}` | not implemented by the upstream core |"
        for a in missing
    ]
    extra_rows = []
    for a in extra:
        reg = regdef.by_addr(a)
        why = (
            "simulation-only debug register"
            if reg.sim_only
            else "reserved in the SST-1 map; implemented here as a Voodoo 2 / V2+ register"
        )
        extra_rows.append(f"| `0x{a:03x}` | `{reg.name}` | {why} |")

    cats = regdef.categories()
    queued = sum(1 for c in cats.values() if not c.fifo_bypass)
    synced = sum(1 for c in cats.values() if c.sync_required)
    bypass = sum(1 for c in cats.values() if c.fifo_bypass)

    quirks = "\n".join(f"- {q}" for q in regdef.DECODE_QUIRKS)

    return f"""# Voodoo register map (Amaranth port)

<!-- GENERATED by amaranth/gen/gen_register_map.py from
     amaranth/avoodoo/frontend/regdef.py — do not edit by hand. -->

Generated from [`amaranth/avoodoo/frontend/regdef.py`](../../amaranth/avoodoo/frontend/regdef.py),
which is the Python port of the register declarations in
`ext/SpinalVoodoo/src/voodoo/frontend/RegisterBank.scala` and the decode
metadata `ext/SpinalVoodoo/src/voodoo/bus/BmbBusInterface.scala` hands to
`PciFifo`. The table is verified against the real elaboration by
`amaranth/tests/test_regdef.py` (ground truth:
`amaranth/tests/data/registerbank_regmeta.json`, written by
`fpga/src/voodoo/diff/GenOne.scala --dump-regmeta`).

Regenerate with:

```
cd amaranth && uv run python -m gen.gen_register_map
```

## Totals

| | |
|---|---|
| Registers declared | {len(regdef.REGISTERS)} ({sum(1 for r in regdef.REGISTERS if r.sim_only)} simulation only) |
| Fields declared | {sum(len(r.fields) for r in regdef.REGISTERS)} |
| Float aliases (`+0x080`) | {len(regdef.FLOAT_ALIASES)} |
| Command streams | {len(regdef.COMMAND_STREAMS)} |
| PciFifo decode entries (`getCategories`) | {len(cats)} |
| …of which FIFO=Yes / Sync=Yes / FIFO=No | {queued} / {synced} / {bypass} |

Categories are `voodoo.bus.RegisterCategory`: `fifoNoSync` (FIFO=Yes, Sync=No),
`fifoWithSync` (FIFO=Yes, Sync=Yes) and `bypassFifo` (FIFO=No). A register with
no category entry — `status` and the five statistics counters, declared with
the plain `newRegAt` — falls through PciFifo's default and behaves as FIFO=No
(`PciFifo.scala:456`).

The **float alias** column gives the IEEE-754 single-precision alias address
(`target + 0x080`) and the arithmetic right shift applied after conversion:
PciFifo converts to a fixed `SQ(50, 30)` intermediate with
`Fpxx2AFix(20 bits, 30 bits, float32)` and then shifts by `30 - fracBits`,
rounding a negative result up when any bit is shifted out
(`PciFifo.floatCorrected`).

## Registers

| Address | Name | Description | Category | Float alias | Fields | Notes |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## Cross-check against `ext/SpinalVoodoo/docs/sst-1.md`

The datasheet's section 5 register table (remapping disabled) lists
{len(datasheet)} register addresses. Comparing them with the table above —
registers plus float-alias addresses:

### In the datasheet, not implemented

| Address | Datasheet name | Note |
|---|---|---|
{chr(10).join(missing_rows) if missing_rows else "| — | — | nothing missing |"}

### Implemented, not in the datasheet's section 5 table

| Address | Name | Note |
|---|---|---|
{chr(10).join(extra_rows) if extra_rows else "| — | — | nothing extra |"}

Everything else matches address for address, including the 30 float aliases at
`0x088`–`0x0fc`, the 32 two-entry `fogTable` words at `0x160`–`0x1dc` and the
two 12-word NCC tables at `0x324`–`0x380`.

Field-level deviations worth knowing (the datasheet's "Bits" column is the
architectural width; RegisterBank often declares the full 32 bits):

- `texBaseAddr`, `texBaseAddr_1`, `texBaseAddr_2`, `texBaseAddr_3_8` are
  declared 24 bits wide; the datasheet defines bits `[18:0]`, and only those
  reach `TexLayoutTables` / `CoreCpuFrontdoor`.
- `fbzMode` and `lfbMode` declare their upper bits as an explicit RW
  `reserved` field (`[31:21]` and `[31:17]`), so they read back what was
  written rather than as undefined.
- `clipLeftRight` / `clipLowYHighY` reset their right/bottom boundary to
  `0x3ff` (the datasheet leaves reset values unspecified).
- `startR` / `startG` / `startB` reset to `255 << 12` rather than 0.
- `fbiInit1.videoTilesX` resets to 10 (a 640-pixel stride).

## Address-decode quirks

{quirks}
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", type=Path, default=OUT)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the file on disk differs from the generated text",
    )
    args = ap.parse_args(argv)

    text = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            print(f"{args.output} is out of date; rerun gen.gen_register_map", file=sys.stderr)
            return 1
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
