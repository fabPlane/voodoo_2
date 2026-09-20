"""Vertex / triangle record types.

Scala source: the ``package object voodoo`` at the bottom of
``ext/SpinalVoodoo/src/voodoo/Bundles.scala`` (lines 81-90).
``ext/SpinalVoodoo/src/voodoo/Vertex.scala`` exists but is empty, so nothing
comes from it; this module is where the plan's ``avoodoo/vertex.py`` slot
(``docs/amaranth-rewrite-plan.md`` §3.7) is filled.

Two packing rules make these layouts line up with the Spinal Verilog, and
both happen to be Amaranth's defaults:

* a SpinalHDL ``Bundle`` packs its fields **in declaration order with the
  first-declared field at the LSB** -- the ``Color`` comment in
  ``Bundles.scala:35-38`` ("declare in reverse order: b, g, r to get standard
  RGB565") is what pins that down.  ``data.StructLayout`` does the same.
* a SpinalHDL ``Vec`` puts **element 0 at the LSB**, like
  ``data.ArrayLayout``.

So no reordering or reversal is needed anywhere in the port.
"""

from __future__ import annotations

from amaranth.hdl import unsigned
from amaranth.lib import data

from avoodoo.config import QFormat
from avoodoo.lib.fixed import AFixShape

__all__ = ["vertex2d", "triangle", "triangle_with_sign_layout"]


def vertex2d(fmt: QFormat) -> data.ArrayLayout:
    """``def vertex2d(fmt: QFormat) = Vec.fill(2)(AFix(fmt))`` (Bundles.scala:84).

    Index 0 is x, index 1 is y (the Scala only ever spells them ``v(0)`` /
    ``v(1)``; :class:`voodoo.PixelCoords` is the named variant and is not
    used by the raster front end).
    """
    return data.ArrayLayout(AFixShape.from_q(fmt), 2)


def triangle(fmt: QFormat) -> data.ArrayLayout:
    """``def triangle(fmt: QFormat) = Vec.fill(3)(vertex2d(fmt))`` (Bundles.scala:85)."""
    return data.ArrayLayout(vertex2d(fmt), 3)


def triangle_with_sign_layout(fmt: QFormat) -> data.StructLayout:
    """``case class TriangleWithSign(fmt: QFormat)`` (Bundles.scala:87-90).

    ``sign_bit`` is bit 31 of ``triangleCMD``: 0 = CCW (positive area),
    1 = CW (negative area).
    """
    return data.StructLayout(
        {
            "tri": triangle(fmt),
            "sign_bit": unsigned(1),
        }
    )
