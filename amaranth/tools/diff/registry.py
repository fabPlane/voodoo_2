"""Which Amaranth module diffs against which Spinal module.

One row per diffable module (plan §4.2).  The Spinal half is a key in
``voodoo.diff.GenOne``'s registry (``fpga/src/voodoo/diff/GenOne.scala``); the
Amaranth half is an ``import.path:Attr`` spec plus the constructor arguments
that reproduce the *same* parameters -- for the core proper that always means
``Config.voodoo1()``, which the factory reads for itself.

``overrides`` is the per-module escape hatch required by plan §4.2 step 4: a
Spinal port name whose Amaranth twin the mechanical rule in
:mod:`tools.diff.naming` cannot derive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["DiffEntry", "REGISTRY", "resolve", "names"]


@dataclass(frozen=True)
class DiffEntry:
    spinal: str
    """``GenOne`` registry key; the Verilog lands in ``<Name>_spinal.v``."""

    factory: str | None
    """``package.module:Attr`` producing the Amaranth Elaboratable."""

    note: str

    params: dict = field(default_factory=dict)
    """Constructor keyword arguments for ``factory``."""

    portmap: str | None = None
    """Committed port-map stem under ``fpga/src/voodoo/diff/portmaps/``.

    Defaults to ``spinal``.  ``GenOne`` aliases two names onto one component
    (``Fpxx`` / ``FpxxDiffWrap``), and only one spelling got committed, so the
    alias rows point at the committed file.
    """

    overrides: dict[str, str] = field(default_factory=dict)
    """Spinal port name -> Amaranth port name, for the non-derivable cases."""

    pending: str | None = None
    """Non-``None`` means the Amaranth side does not exist yet; the string says
    who owns it.  ``harness.py`` refuses to run such a row with this message."""

    @property
    def portmap_stem(self) -> str:
        return self.portmap or self.spinal


REGISTRY: dict[str, DiffEntry] = {
    # --- Phase 1.3 ------------------------------------------------------
    "FpxxDiffWrap": DiffEntry(
        spinal="FpxxDiffWrap",
        factory="tools.diff.wrappers:FpxxDiffWrap",
        note="Fpxx predicate/helper set, FpxxConfig.float32() as in PciFifo "
             "(ext/SpinalVoodoo/src/voodoo/math/Fpxx.scala -> avoodoo/lib/fpxx.py)",
        # No overrides: every port is a flat io_ leaf, so camelCase -> snake_case
        # is exact (io_aFullMant -> a_full_mant, io_constNan -> const_nan).
    ),
    "Fpxx": DiffEntry(
        spinal="Fpxx",
        factory="tools.diff.wrappers:FpxxDiffWrap",
        note="alias of FpxxDiffWrap (GenOne registers both spellings)",
        portmap="FpxxDiffWrap",
    ),
    "Fpxx2AFix": DiffEntry(
        spinal="Fpxx2AFix",
        factory="avoodoo.lib.fpxx2afix:Fpxx2AFix",
        note="PciFifo.scala:200 — Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1) "
             "(ext/SpinalVoodoo/src/voodoo/math/Fpxx2AFix.scala)",
        params={"int_nr_bits": 20, "frac_nr_bits": 30, "pipe_stages": 1},
        # io_op_payload_{mant,exp,sign} pair against slices of the single packed
        # Amaranth port `op__payload` (FpxxLayout); tools/diff/amports.py does
        # that without an override.
    ),
    # --- Phase 1.1 (agent p1-stream owns avoodoo/bus/stream_while.py) ----
    "StreamWhileDiffWrap": DiffEntry(
        spinal="StreamWhileDiffWrap",
        factory="avoodoo.bus.stream_while:StreamWhileDiffWrap",
        note="decrement-to-zero loop, width=8 maxIterations=16 "
             "(ext/SpinalVoodoo/src/voodoo/bus/StreamWhile.scala)",
        portmap="StreamWhile",  # the committed map was generated from the alias
    ),
    "StreamWhile": DiffEntry(
        spinal="StreamWhile",
        factory="avoodoo.bus.stream_while:StreamWhileDiffWrap",
        note="alias of StreamWhileDiffWrap",
        portmap="StreamWhile",
    ),
    # --- Phase 0.2 -------------------------------------------------------
    "AfixOps": DiffEntry(
        spinal="AfixOps",
        factory="avoodoo.lib.afix_ops:AfixOps",
        note="AFix op-by-op reference, 282 ports, Config.voodoo1() formats "
             "(fpga/src/voodoo/diff/AfixOps.scala -> avoodoo/lib/fixed.py)",
    ),
}


def names() -> list[str]:
    return sorted(REGISTRY)


def resolve(name: str) -> DiffEntry:
    try:
        return REGISTRY[name]
    except KeyError:
        raise SystemExit(
            f"unknown diff module {name!r}; known: {', '.join(names())}"
        ) from None
