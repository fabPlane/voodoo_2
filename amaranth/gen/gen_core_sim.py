"""Emit ``rtl/CoreSim.v`` for the Verilator Glide harness.

    python -m gen.gen_core_sim                 # -> amaranth/rtl/CoreSim.v
    python -m gen.gen_core_sim -o /tmp/rtl
    python -m gen.gen_core_sim --sync-reset

The top module is named ``CoreSim``, not ``CoreSim_am``: the upstream Glide
build hardcodes ``SIM_MODEL = CoreSim`` in three Makefiles and the harness
includes ``VCoreSim.h``, so the Amaranth model has to answer to the same name
(plan §3.1).  The Phase 0.4 diff harness, which does want ``_am``/``_spinal``
suffixes, generates through ``gen_module.py`` instead.

Why this is not just ``gen_module.py CoreSim``
----------------------------------------------
``CoreSim`` embeds a 4 MiB framebuffer RAM and an 8 MiB texture RAM.  Amaranth
0.5.10 always attaches an explicit initialiser to a ``Memory`` — even
``init=[]`` becomes an all-zero ``$meminit_v2`` cell — and yosys'
``write_verilog`` expands that into one ``initial`` statement *per word*:
3.1 million lines of Verilog, which Verilator turns into C++ no compiler will
finish.  The Spinal ``Mem`` this replaces has no initialiser at all.

So this generator emits RTLIL, deletes the ``$meminit_v2`` cells, and only
then hands the design to yosys.  The memories come out as bare
``reg [31:0] mem [N-1:0]`` arrays with no ``initial`` block, exactly like the
Spinal ones, and Verilator's ``--x-initial fast`` zeroes them at construction.
Building the initialiser text is the slow part of this script (~1 min for the
two RAMs); everything downstream is fast.

Phase 7.2 inherits this: any Amaranth ``Memory`` whose contents are supposed
to start undefined has to go through ``strip_meminit``, and any memory with
*real* contents (dither tables, TMU LUTs) must not.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from amaranth.back import rtlil
from amaranth.back.verilog import _convert_rtlil_text  # noqa: PLC2701 - see below
from amaranth.hdl import _ir
from amaranth.lib import wiring

from avoodoo.core_sim import CoreSim

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "rtl"

# `_convert_rtlil_text` is private, but it is the only seam Amaranth 0.5.10
# offers between "RTLIL text" and "yosys write_verilog": the public
# `verilog.convert()` does both in one call and never exposes the RTLIL.  The
# function is three lines of yosys script; if a future Amaranth removes it,
# reimplement it with `amaranth._toolchain.yosys.find_yosys()`.

_MEMINIT_RE = re.compile(r"^\s*cell \$meminit(_v2)? ")


def strip_meminit(rtlil_text: str) -> tuple[str, int]:
    """Delete every ``$meminit``/``$meminit_v2`` cell from RTLIL text.

    Returns the new text and the number of cells removed.  Cells are
    line-delimited (``cell …`` … ``end``) and never nested, so this does not
    need a real parser.
    """
    lines = rtlil_text.splitlines()
    out: list[str] = []
    removed = 0
    i = 0
    while i < len(lines):
        if _MEMINIT_RE.match(lines[i]):
            # Drop the attribute lines Amaranth emitted for this cell too.
            while out and out[-1].lstrip().startswith("attribute "):
                out.pop()
            while i < len(lines) and lines[i].strip() != "end":
                i += 1
            i += 1  # the "end"
            removed += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + "\n", removed


def _ports_of(component: wiring.Component) -> dict:
    ports = {}
    for path, member, value in component.signature.flatten(component):
        direction = (
            _ir.PortDirection.Input if member.flow == wiring.In else _ir.PortDirection.Output
        )
        ports["__".join(map(str, path))] = (value, direction)
    return ports


def emit(out_dir: Path, *, async_reset: bool = True, verbose: bool = True) -> Path:
    def note(msg: str) -> None:
        if verbose:
            print(f"[gen_core_sim] {msg}", file=sys.stderr)

    t0 = time.monotonic()
    dut = CoreSim(async_reset=async_reset)
    fragment = _ir.Fragment.get(dut, None)
    rtlil_text, _ = rtlil.convert_fragment(fragment, _ports_of(dut), "CoreSim")
    t1 = time.monotonic()
    note(f"rtlil: {len(rtlil_text)/1e6:.1f} MB in {t1 - t0:.1f}s")

    rtlil_text, removed = strip_meminit(rtlil_text)
    t2 = time.monotonic()
    note(f"stripped {removed} meminit cell(s) -> {len(rtlil_text)/1e6:.3f} MB in {t2 - t1:.1f}s")

    verilog_text = _convert_rtlil_text(rtlil_text, strip_internal_attrs=True)
    t3 = time.monotonic()
    note(f"verilog: {len(verilog_text)/1e6:.3f} MB in {t3 - t2:.1f}s")

    if "initial" in verilog_text:
        raise SystemExit(
            "gen_core_sim: the emitted Verilog still contains an `initial` block; "
            "a memory initialiser escaped strip_meminit() and Verilator will choke "
            "on it. Investigate before committing."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "CoreSim.v"
    path.write_text(verilog_text)
    note(f"wrote {path} in {t3 - t0:.1f}s total")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--sync-reset",
        action="store_true",
        help="synchronous reset instead of the upstream sim build's async reset",
    )
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)
    emit(args.out_dir, async_reset=not args.sync_reset, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
