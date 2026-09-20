"""``FramebufferAddressMath`` -- pixel (x, y) to plane byte address.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/framebuffer/FramebufferAddressMath.scala``
(30 lines, SpinalHDL 1.15.0; plan Phase 6 "address_math").  The Scala is a
stateless ``object`` of three functions, so this module is three functions on
Amaranth values plus their exact-integer Python twins for the tests.

Callers in the Spinal tree (all through ``planeAddress``): ``Write.scala:18``,
``FramebufferAccess.scala:125/132``, ``Lfb.scala:361``,
``PixelPipeline.scala:137/143``, ``FastfillWordWriter.scala:142/148`` and
``HdmiScanout.scala:271/376/382/421``.  ``avoodoo/core/core_types.py`` carries
a private copy of the same three functions that ``pixel/write.py`` picked up
before this file existed; the two are the same transliteration and this one
is the canonical home (plan §3 layout).

Layout, as the SST-1 fbiInit1 stride register defines it
--------------------------------------------------------

``fbPixelStride`` is the row pitch in *pixels*; the framebuffer is addressed in
128-byte (64-pixel) tiles, so ``tilesX = stride >> 6`` (kept to 4 bits, i.e.
at most 15 tiles = 960 pixels) and a row starts at ``(y * tilesX) << 7``
bytes.  A pixel is two bytes, hence ``x << 1``.  Everything wraps modulo
``2 ** addressWidth``, exactly as the Scala ``.resize`` chain does.

Width rules that are reproduced bit for bit
-------------------------------------------

* ``mulBySmallUnsigned``: the product is built as a shift-and-add over the
  bits of ``coeff`` and every term is ``resize``d to
  ``value.getWidth + coeff.getWidth`` *before* the add tree, whose ``+^``
  results are then ``resize``d back.  Wrapping happens at that width, so a
  ``y`` wide enough to overflow it wraps identically here.
* ``|<<`` is Spinal's non-widening shift; ``(v << n)[0:w]`` is the same thing.
* ``+^`` widens by one bit; the ``resize`` after it truncates.  Amaranth's
  ``+`` widens by one bit too, so a trailing slice is the whole translation.
* ``resize`` zero-extends a narrower operand.  :func:`_resize` does that
  where a plain slice would raise on a too-narrow value.
"""

from __future__ import annotations

from amaranth import Const, Mux, Value
from amaranth.hdl import ValueCastable

__all__ = [
    "mul_by_small_unsigned",
    "pixel_byte_offset",
    "plane_address",
    "mul_by_small_unsigned_model",
    "pixel_byte_offset_model",
    "plane_address_model",
]


def _resize(v, width: int):
    """``UInt.resize(width bits)``: truncate or zero-extend to ``width``."""
    v = Value.cast(v).as_unsigned()
    if len(v) >= width:
        return v[0:width]
    from amaranth import Cat
    return Cat(v, Const(0, width - len(v)))


def mul_by_small_unsigned(value, coeff):
    """``private def mulBySmallUnsigned(value: UInt, coeff: UInt): UInt``.

    ``value * coeff`` as a shift-and-add over the bits of ``coeff``, every
    term and the final sum truncated to ``len(value) + len(coeff)`` bits.
    Written out term by term (not ``value * coeff``) so the adder tree, and
    therefore the wrap width, is the Scala's.
    """
    value = Value.cast(value).as_unsigned()
    coeff = Value.cast(coeff).as_unsigned()
    width = len(value) + len(coeff)
    zero = Const(0, width)
    terms = [
        Mux(coeff[bit], (_resize(value, width) << bit)[0:width], zero)
        for bit in range(len(coeff))
    ]
    total = terms[0]
    for term in terms[1:]:
        total = total + term          # `+^`
    return total[0:width]             # `.resize(width bits)`


def pixel_byte_offset(x, y, fb_pixel_stride, address_width: int):
    """``def pixelByteOffset(x, y, fbPixelStride, addressWidth: Int): UInt``."""
    tiles_x = _resize(Value.cast(fb_pixel_stride).as_unsigned() >> 6, 4)
    row_tiles = mul_by_small_unsigned(y, tiles_x)
    row_bytes = (_resize(row_tiles, address_width) << 7)[0:address_width]
    x_bytes = (_resize(x, address_width) << 1)[0:address_width]
    return (row_bytes + x_bytes)[0:address_width]        # `+^` then resize


def plane_address(base, x, y, fb_pixel_stride):
    """``def planeAddress(base, x, y, fbPixelStride): UInt`` -- ``base.getWidth`` wide."""
    base = Value.cast(base).as_unsigned()
    offset = pixel_byte_offset(x, y, fb_pixel_stride, len(base))
    return (base + offset)[0:len(base)]


# ---------------------------------------------------------------------------
# Exact-integer models (what the tests compare the hardware against)
# ---------------------------------------------------------------------------


def mul_by_small_unsigned_model(value: int, value_width: int,
                                coeff: int, coeff_width: int) -> int:
    width = value_width + coeff_width
    mask = (1 << width) - 1
    total = 0
    for bit in range(coeff_width):
        if (coeff >> bit) & 1:
            total += ((value & mask) << bit) & mask
    return total & mask


def pixel_byte_offset_model(x: int, y: int, y_width: int, fb_pixel_stride: int,
                            address_width: int) -> int:
    mask = (1 << address_width) - 1
    tiles_x = (fb_pixel_stride >> 6) & 0xF
    row_tiles = mul_by_small_unsigned_model(y, y_width, tiles_x, 4)
    row_bytes = ((row_tiles & mask) << 7) & mask
    x_bytes = ((x & mask) << 1) & mask
    return (row_bytes + x_bytes) & mask


def plane_address_model(base: int, base_width: int, x: int, y: int, y_width: int,
                        fb_pixel_stride: int) -> int:
    mask = (1 << base_width) - 1
    return (base + pixel_byte_offset_model(x, y, y_width, fb_pixel_stride,
                                           base_width)) & mask
