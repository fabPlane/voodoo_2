"""Spinal Verilog port name -> Amaranth Verilog port name.

Plan §4.2 step 4 wants "an explicit mapping table per module, generated once
from the Spinal Verilog port list and committed".  The committed half is
``fpga/src/voodoo/diff/portmaps/<Module>.json`` (written by
``fpga/src/voodoo/diff/ports.py``): it already decomposes every flat Spinal
port name into a *path* -- ``io_cpuSide_cmd_payload_fragment_address`` becomes
``["cpuSide", "cmd", "payload", "fragment", "address"]``, with the ``io_``
bundle prefix stripped and Vec indices identified.  See
``docs/amaranth/spinal-naming-and-harness.md`` §1 for how those paths are
derived and why ``_<N>`` is ambiguous.

Amaranth flattens a ``wiring.Signature`` the same way but with ``__`` as the
separator and snake_case leaves, so the whole mapping is mechanical:

====================================  ==============================
Spinal                                Amaranth
====================================  ==============================
``start_valid``       ``["start", "valid"]``       ``start__valid``
``io_aFullMant``      ``["aFullMant"]``            ``a_full_mant``
``i_payload_coords_0``  ``[..., "coords", "0"]``   ``payload__coords__0``
``clk``                                            ``clk``
``reset``                                          ``rst``
====================================  ==============================

Two rules are *not* derivable and need the per-module override table in
:mod:`tools.diff.registry`:

1. A Spinal leaf whose snake_case spelling collides with a Python keyword or
   with a different Amaranth member name (the Amaranth port then had to be
   spelled differently on purpose).
2. A trailing ``_<N>`` that ``ports.py`` classified ``ambiguous`` -- Spinal's
   duplicate-identifier suffix rather than a Vec index.  Those are taken
   literally (``foo_1`` -> ``foo_1``), which is right for the suffix case and
   wrong for a Vec index that ``ports.py`` could not prove contiguous; an
   override settles it.
"""

from __future__ import annotations

import re

__all__ = ["camel_to_snake", "amaranth_port_name", "CLOCK_RESET"]

#: Clock and reset never follow the path rule: Spinal always names them ``clk``
#: and ``reset`` (``spinal-naming-and-harness.md`` §1.1); Amaranth's default
#: ``sync`` domain emits ``clk`` and ``rst``.
CLOCK_RESET = {"clk": "clk", "reset": "rst"}

_BOUNDARY_1 = re.compile(r"(.)([A-Z][a-z]+)")
_BOUNDARY_2 = re.compile(r"([a-z0-9])([A-Z])")


def camel_to_snake(name: str) -> str:
    """``aFullMant`` -> ``a_full_mant``; ``TMDSclk`` -> ``tmd_sclk`` (beware).

    Digits stay attached to the preceding word (``coords0`` -> ``coords0``);
    a path segment that is *entirely* digits (a Vec index) is returned as is.
    """
    if name.isdigit():
        return name
    out = _BOUNDARY_1.sub(r"\1_\2", name)
    out = _BOUNDARY_2.sub(r"\1_\2", out)
    return out.lower()


def amaranth_port_name(path: list[str], *, spinal_name: str,
                       overrides: dict[str, str] | None = None) -> str:
    """Amaranth port name for a Spinal port map entry.

    ``overrides`` is keyed by the *full Spinal port name*, so an override is
    always unambiguous even when two paths snake_case to the same thing.
    """
    if overrides and spinal_name in overrides:
        return overrides[spinal_name]
    if spinal_name in CLOCK_RESET and len(path) == 1:
        return CLOCK_RESET[spinal_name]
    return "__".join(camel_to_snake(seg) for seg in path)
