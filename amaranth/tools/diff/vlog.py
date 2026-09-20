"""Minimal Verilog module-header parser: the port list of a generated file.

Both halves of the differential harness (plan §4.2) hand us Verilog, but they
spell their port lists differently:

* SpinalHDL 1.15 emits **ANSI** headers --
  ``module Fpxx_spinal (\\n  input  wire [31:0]   io_a,\\n ...);``
* Amaranth (via ``amaranth-yosys``'s ``write_verilog``) emits the **non-ANSI**
  form -- ``module Counter(clk, rst, count, carry, en);`` followed by separate
  ``input clk;`` / ``output [7:0] count;`` declarations, each preceded by a
  ``(* src = "..." *)`` attribute.

Rather than trust either generator's conventions, the harness reads the real
port list off both files and compares widths before it wires anything up.  A
width disagreement is a port-map bug, and finding it here gives a readable
error instead of a Verilator ``PINNOTFOUND``/``WIDTH`` avalanche.

Standard library only, and deliberately *not* a Verilog parser: it understands
exactly the two shapes above.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Port", "parse_ports", "module_names"]


@dataclass(frozen=True)
class Port:
    name: str
    direction: str  # "input" | "output" | "inout"
    width: int
    msb: int | None
    lsb: int | None

    @property
    def is_vector(self) -> bool:
        return self.msb is not None


_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_ATTRIBUTE = re.compile(r"\(\*.*?\*\)", re.S)

_DECL = re.compile(
    r"^\s*(?P<dir>input|output|inout)\b"
    r"(?:\s+(?:wire|reg|logic|var))*"
    r"(?:\s+(?P<signed>signed|unsigned))?"
    r"\s*(?:\[\s*(?P<msb>[-\w'\s+]+?)\s*:\s*(?P<lsb>[-\w'\s+]+?)\s*\])?"
    r"\s*(?P<names>[A-Za-z_\\][\w$]*(?:\s*,\s*[A-Za-z_\\][\w$]*)*)\s*$"
)

_MODULE = re.compile(r"\bmodule\s+(?P<name>[A-Za-z_\\][\w$]*)\s*(?P<rest>[#(])", re.S)


def _strip(text: str) -> str:
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _ATTRIBUTE.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    return text


def _int(expr: str | None) -> int | None:
    if expr is None:
        return None
    expr = expr.strip()
    try:
        return int(expr, 0)
    except ValueError:
        raise ValueError(f"non-constant port bound {expr!r}; the harness needs literal widths")


def module_names(path: str | Path) -> list[str]:
    """Every module defined in ``path``, in source order."""
    text = _strip(Path(path).read_text())
    return [m.group("name") for m in _MODULE.finditer(text)]


def _header_and_body(text: str, module: str) -> tuple[str, str]:
    """Return the parenthesised port header and the body of ``module``."""
    for m in _MODULE.finditer(text):
        if m.group("name") != module:
            continue
        # Skip an optional parameter list, then take the port list.
        i = m.end() - 1
        if text[i] == "#":
            i = text.index("(", i)
            i = _match_paren(text, i) + 1
            while text[i].isspace():
                i += 1
        if text[i] != "(":
            raise ValueError(f"module {module}: no port list")
        close = _match_paren(text, i)
        header = text[i + 1 : close]
        end = text.find("endmodule", close)
        body = text[close + 1 : end if end >= 0 else len(text)]
        return header, body
    raise KeyError(f"module {module!r} not found")


def _match_paren(text: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced parentheses in module header")


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on ``sep`` at bracket depth zero."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return out


def _decls(chunk: str, sep: str) -> list[Port]:
    """Parse ``input``/``output`` declarations from ``sep``-separated items."""
    ports: list[Port] = []
    for item in _split_top_level(chunk, sep):
        m = _DECL.match(item)
        if m is None:
            continue
        msb = _int(m.group("msb"))
        lsb = _int(m.group("lsb"))
        width = 1 if msb is None else abs(msb - lsb) + 1
        for name in (n.strip() for n in m.group("names").split(",")):
            ports.append(Port(name, m.group("dir"), width, msb, lsb))
    return ports


def parse_ports(path: str | Path, module: str | None = None) -> dict[str, Port]:
    """Port name -> :class:`Port` for ``module`` (default: the first one).

    Handles ANSI headers (Spinal) and non-ANSI headers with separate
    declarations (Amaranth/yosys).  Port order is preserved.
    """
    text = _strip(Path(path).read_text())
    if module is None:
        names = [m.group("name") for m in _MODULE.finditer(text)]
        if not names:
            raise ValueError(f"{path}: no module definition")
        module = names[0]

    header, body = _header_and_body(text, module)

    ports = _decls(header, ",")
    if ports:  # ANSI style: the header carried the directions
        return {p.name: p for p in ports}

    # Non-ANSI: names in the header, directions and widths in the body.
    order = [n.strip() for n in header.split(",") if n.strip()]
    declared = {p.name: p for p in _decls(body, ";")}
    missing = [n for n in order if n not in declared]
    if missing:
        raise ValueError(f"{path}: module {module} has no declaration for {missing}")
    return {n: declared[n] for n in order}
