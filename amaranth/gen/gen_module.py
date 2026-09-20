"""Elaborate one named avoodoo module and write Verilog to ``amaranth/rtl/``.

    python -m gen.gen_module Counter
    python -m gen.gen_module Counter width=16
    python -m gen.gen_module avoodoo.example:Counter -o /tmp/rtl

Modules are looked up in :data:`REGISTRY` by their CamelCase name; a
``package.module:ClassName`` spec also works, so a freshly ported module can
be generated before anyone remembers to register it.

The emitted Verilog top module is named after the class (``Counter``), which
is what the Phase 0.4 diff harness expects (it renames to ``<Name>_am``).
Verilog emission uses the ``amaranth-yosys`` wheel, so no system yosys is
needed on either build machine.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from amaranth.back import verilog
from amaranth.lib import wiring

# CamelCase module name -> "import.path:attribute"
REGISTRY: dict[str, str] = {
    "Counter": "avoodoo.example:Counter",
    "SpikeBlinky": "avoodoo.gowin.spike_blinky:SpikeBlinky",
}

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "rtl"


def resolve(name: str):
    """Return the Elaboratable class for a registry name or ``mod:attr`` spec."""
    spec = REGISTRY.get(name, name)
    if ":" not in spec:
        raise SystemExit(
            f"unknown module {name!r}; known: {', '.join(sorted(REGISTRY))} "
            f"(or pass package.module:ClassName)"
        )
    mod_name, _, attr = spec.partition(":")
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        raise SystemExit(f"cannot import {mod_name!r} for module {name!r}: {exc}") from exc
    try:
        return getattr(mod, attr)
    except AttributeError as exc:
        raise SystemExit(f"{mod_name!r} has no attribute {attr!r}") from exc


def _coerce(text: str):
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    if text in ("True", "true"):
        return True
    if text in ("False", "false"):
        return False
    return text


def parse_params(pairs: list[str]) -> dict:
    params = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"parameter {pair!r} is not key=value")
        key, _, value = pair.partition("=")
        params[key] = _coerce(value)
    return params


def emit(name: str, out_dir: Path, params: dict | None = None) -> Path:
    """Elaborate ``name`` and write ``<out_dir>/<top>.v``. Returns the path."""
    cls = resolve(name)
    top = name.rpartition(":")[2] or name
    dut = cls(**(params or {}))

    ports = None
    if not isinstance(dut, wiring.Component):
        # Non-Component elaboratables must advertise their ports themselves.
        if not hasattr(dut, "ports"):
            raise SystemExit(
                f"{top} is not a wiring.Component and has no ports() method; "
                f"give it a signature so the port list is unambiguous"
            )
        ports = dut.ports()

    # A wiring.Component carries its own port list; anything else supplied one
    # above. Pass the elaboratable itself, not a Fragment: convert() only reads
    # the signature off the former.
    text = verilog.convert(dut, name=top, ports=ports)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{top}.v"
    path.write_text(text)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("module", help="registry name or package.module:ClassName")
    parser.add_argument("params", nargs="*", help="constructor arguments, key=value")
    parser.add_argument("-o", "--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--list", action="store_true", help="list registry names and exit")
    args = parser.parse_args(argv)

    if args.list:
        for key, spec in sorted(REGISTRY.items()):
            print(f"{key}\t{spec}")
        return 0

    path = emit(args.module, args.out_dir, parse_params(args.params))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
