"""``object Tmu``'s record layouts and small combinational helpers.

Port of the bundle declarations and pure functions of
``ext/SpinalVoodoo/src/voodoo/texture/Tmu.scala`` (SpinalHDL 1.15.0) that the
texture path shares -- *not* the ``Tmu`` Component itself, which plan Phase 5
gives its own module (``avoodoo/texture/tmu.py``).  Only the pieces
``TmuTexelDecoder`` needs are here so far; ``tmu.py`` and
``tmu_texture_cache.py`` append the rest.

Scala lines, in this file's order:

===========================================  ======================
``Tmu.scala``                                here
===========================================  ======================
``requestIdWidth`` (l. 726)                  :data:`REQUEST_ID_WIDTH`
``case class PaletteWrite`` (l. 854)         :class:`PaletteWrite`
``case class TmuPassthrough`` (l. 860)       :class:`TmuPassthrough`
``case class SampleFetch`` (l. 1008)         :class:`SampleFetch`
``object TextureFormat`` (l. 1044)           :class:`TextureFormat`
``case class NccTableData`` (l. 1097)        :class:`NccTableData`
``case class NccTables`` (l. 1103)           :class:`NccTables`
``case class Output`` (l. 1163)              :class:`TmuOutput`
``case class BilinearWeights`` (l. 728)      :class:`BilinearWeights`
``def bilinearWeights`` (l. 739)             :func:`bilinear_weights`
``def blendChannel`` (l. 752)                :func:`blend_channel`
===========================================  ======================

Conventions are ``core/core_types.py``'s: field names are
``tools.diff.naming.camel_to_snake`` of the Spinal names (``nccTableSelect``
-> ``ncc_table_select``, ``textureAlpha`` -> ``texture_alpha``, ``table0``
stays ``table0``) so the lockstep harness pairs ports without an override,
and declaration order is Spinal's so the packed bit layout matches.

The two arithmetic helpers are transliterations down to the *widths*, because
both of them wrap:

* ``bilinearWeights`` subtracts in 5 bits (``U(16, 5 bits) - ds5``) and keeps
  the 10-bit product;
* ``blendChannel`` sums four 28-bit products in 28 bits (Spinal's ``UInt +
  UInt`` keeps the wider operand's width, it does not grow) and returns
  ``sum(15 downto 8)``.
"""

from __future__ import annotations

from amaranth.hdl import Cat, Const, Signal, unsigned
from amaranth.lib import data

from avoodoo.config import Config
from avoodoo.core.core_types import TRACE_PIXEL_KEY_LAYOUT, Color

__all__ = [
    "REQUEST_ID_WIDTH",
    "TextureFormat",
    "PaletteWrite",
    "TmuPassthrough",
    "SampleFetch",
    "BilinearWeights",
    "NccTableData",
    "NccTables",
    "TmuOutput",
    "bilinear_weights",
    "blend_channel",
]

#: ``val requestIdWidth = 3``.
REQUEST_ID_WIDTH = 3


class TextureFormat:
    """``object Tmu.TextureFormat`` -- the ``tformat`` field, textureMode[11:8].

    6, 7 and 15 are unassigned; the decoder's ``default`` branch treats them
    as RGB565.
    """

    RGB332 = 0
    YIQ422 = 1
    A8 = 2
    I8 = 3
    AI44 = 4
    P8 = 5
    ARGB8332 = 8
    AYIQ8422 = 9
    RGB565 = 10
    ARGB1555 = 11
    ARGB4444 = 12
    AI88 = 13
    AP88 = 14

    #: Every assigned code, low to high -- handy for test sweeps.
    ALL = (RGB332, YIQ422, A8, I8, AI44, P8, ARGB8332, AYIQ8422,
           RGB565, ARGB1555, ARGB4444, AI88, AP88)

    #: The codes that fall through to the ``default`` (RGB565) branch.
    UNASSIGNED = (6, 7, 15)


class PaletteWrite(data.StructLayout):
    """``case class PaletteWrite()`` -- one 24-bit RGB888 palette entry."""

    def __init__(self) -> None:
        super().__init__({
            "address": unsigned(8),
            "data": unsigned(24),
        })


class TmuPassthrough(data.StructLayout):
    """``case class TmuPassthrough(c)`` -- metadata carried alongside a sample.

    ``trace`` exists only when ``c.trace.enabled``; Spinal drops the ``null``
    Bundle field otherwise.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        fields = {
            "format": unsigned(4),
            "bilinear": unsigned(1),
            "ncc_table_select": unsigned(1),
            "ds": unsigned(4),
            "dt": unsigned(4),
        }
        if c.trace.enabled:
            fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(fields)


class SampleFetch(data.StructLayout):
    """``case class SampleFetch(c)`` -- the four raw texel words plus metadata.

    ``texels(0)`` is the point-sampled texel; 1..3 are the +s, +t and +s+t
    neighbours the bilinear blend needs.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "bilinear": unsigned(1),
            "texels": data.ArrayLayout(unsigned(16), 4),
            "passthrough": TmuPassthrough(c),
        })


class BilinearWeights(data.StructLayout):
    """``case class BilinearWeights()`` -- the four 4.4-ish blend weights."""

    def __init__(self) -> None:
        super().__init__({
            "w0": unsigned(10),
            "w1": unsigned(10),
            "w2": unsigned(10),
            "w3": unsigned(10),
        })


class NccTableData(data.StructLayout):
    """``case class NccTableData()`` -- one NCC (YIQ) decompression table.

    ``y`` holds 16 unsigned 8-bit luminance levels; ``i`` and ``q`` hold four
    27-bit chrominance entries each, packed ``[26:18]=R [17:9]=G [8:0]=B`` as
    9-bit *signed* offsets.  The Bundle keeps them as raw ``Bits(27)``, so the
    layout does too and the decoder does the sign extraction.
    """

    def __init__(self) -> None:
        super().__init__({
            "y": data.ArrayLayout(unsigned(8), 16),
            "i": data.ArrayLayout(unsigned(27), 4),
            "q": data.ArrayLayout(unsigned(27), 4),
        })


class NccTables(data.StructLayout):
    """``case class NccTables()`` -- the two tables ``tnccselect`` picks between."""

    def __init__(self) -> None:
        super().__init__({
            "table0": NccTableData(),
            "table1": NccTableData(),
        })


class TmuOutput(data.StructLayout):
    """``case class Tmu.Output(c)`` -- the decoded texel handed to the pixel pipe.

    Named ``TmuOutput`` rather than ``Output`` so it does not collide with
    ``amaranth.lib.wiring.Out`` at a glance, or with the several other
    ``Output`` bundles the core has.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        fields = {
            "texture": Color.u8(),
            "texture_alpha": unsigned(8),
        }
        if c.trace.enabled:
            fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(fields)


# ---------------------------------------------------------------------------
# object Tmu -- combinational helpers
# ---------------------------------------------------------------------------


def _zext(v, width):
    """``UInt.resize(width)`` -- zero-extend or truncate, never sign-extend."""
    v = v.as_unsigned()
    if len(v) >= width:
        return v[:width]
    return Cat(v, Const(0, width - len(v)))


def bilinear_weights(m, ds, dt, *, name="weights"):
    """``def bilinearWeights(ds: UInt, dt: UInt): BilinearWeights``.

    ::

        ds5 = ds.resize(5 bits) ; invDs = U(16, 5 bits) - ds5
        w0 = (invDs * invDt).resize(10 bits)   w1 = (ds5 * invDt).resize(10)
        w2 = (invDs * dt5).resize(10 bits)     w3 = (ds5 * dt5).resize(10)

    ``ds``/``dt`` are 4 bits, so ``invDs`` is 1..16 and the 5-bit subtraction
    never wraps in practice -- but it is written as a 5-bit subtraction
    anyway, because the Scala is, and ``Tmu`` may hand it a wider value later.
    The products are 10 bits wide by construction (5 x 5), so ``resize(10)``
    is a no-op; the slice is spelled out so a wider ``ds`` would truncate the
    same way Spinal does.
    """
    out = Signal(BilinearWeights(), name=name)
    ds5 = _zext(ds, 5)
    dt5 = _zext(dt, 5)
    inv_ds = (Const(16, 5) - ds5)[:5]
    inv_dt = (Const(16, 5) - dt5)[:5]
    m.d.comb += [
        out.w0.eq((inv_ds * inv_dt)[:10]),
        out.w1.eq((ds5 * inv_dt)[:10]),
        out.w2.eq((inv_ds * dt5)[:10]),
        out.w3.eq((ds5 * dt5)[:10]),
    ]
    return out


def blend_channel(weights, t0, t1, t2, t3):
    """``def blendChannel(weights, t0, t1, t2, t3): UInt``.

    ::

        sum = t0.resize(18) * w0 + t1.resize(18) * w1
            + t2.resize(18) * w2 + t3.resize(18) * w3
        (sum >> 8).resize(8 bits)

    Each product is 28 bits (18 + 10) and Spinal's ``UInt + UInt`` keeps the
    wider width, so the running sum stays 28 bits.  With 8-bit texels the
    maximum is ``4 * 255 * 256 = 261120``, well inside 28 bits, so nothing
    wraps; the result is ``sum(15 downto 8)``, i.e. a divide by 256 where the
    weights sum to 256.
    """
    total = (_zext(t0, 18) * weights.w0 + _zext(t1, 18) * weights.w1
             + _zext(t2, 18) * weights.w2 + _zext(t3, 18) * weights.w3)
    return total[8:16]
