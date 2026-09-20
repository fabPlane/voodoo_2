"""Ordered-dither lookup tables, generated rather than copied.

Port of ``ext/SpinalVoodoo/src/voodoo/pixel/DitherTables.scala`` (whose 931
lines of literal data live in ``DitherTableData.scala``).  Those numbers are
the 86Box tables in
``ext/SpinalVoodoo/emu/86Box/src/include/86box/vid_voodoo_dither.h``
(``dither_rb``, ``dither_g``, ``dither_rb2x2``, ``dither_g2x2``,
``dithersub_rb``, ``dithersub_g``, ``dithersub_rb2x2``, ``dithersub_g2x2``) —
verified byte-for-byte by :mod:`tools.extract_spinal_tables`.

86Box ships the eight tables as literal arrays; it has no generator.  The
generator below was recovered from the data and reproduces all 10 240 entries
exactly (``tests/test_dither_tables.py`` asserts every one against the Scala
literals, and again against the 86Box header when the submodule is present).
Nothing here is hand-copied.

How the dither tables are built
-------------------------------

For an ``n``x``n`` tile and a ``bits``-wide output code, each 8-bit input ``v``
maps to a level count on a scale of ``n*n`` sub-steps per output code::

    levels(v) = 2*v - (v >> 4) + (v >> 7)      # 4x4, 5-bit (r/b)
    levels(v) = 4*v - (v >> 4) + (v >> 6)      # 4x4, 6-bit (g)
    levels(v) = (levels_4x4(v) + 2) >> 2       # the 2x2 tables, rounded

``levels`` is a slightly-irregular approximation of ``v * n*n * max_code /
255`` (exact at both ends: ``levels(0) == 0``, ``levels(255) == n*n *
max_code``).  The irregularity is 86Box's, not ours, and is what defeats every
clean closed form — see the note at the bottom of this docstring.

``base, count = divmod(levels(v), n*n)`` then gives the output code for the
whole tile plus how many of its cells are bumped by one; the cells bumped are
those whose rank in :data:`ORDER_4X4` / :data:`ORDER_2X2` (the classic Bayer
matrix, rows flipped) is below ``count``::

    table[v][y][x] = base + (1 if ORDER[y][x] < count else 0)

Both 4x4 tables share one order matrix, as do both 2x2 tables.

How the dither-subtract tables are built
----------------------------------------

``dithersub_*`` undoes the dither on a destination pixel read back from the
framebuffer (``FramebufferAccess.scala:294``: ``enableDitherSubtract``).  Each
cell just biases the 8-bit value by a per-cell constant, saturating at 0/255,
with values below half an output step forced to 0::

    sub[v][y][x] = 0 if v < step // 2 else clamp(v + delta(ORDER[y][x]))
    delta(o)     = (((o + 1) * mul) >> shift) - bias

where ``step`` is the 8-bit distance between output codes (8 for 5-bit, 4 for
6-bit) and ``(mul, shift, bias)`` is :data:`_SUB_DELTA_PARAMS`.  The resulting
per-cell biases are, for the 4x4 r/b table::

    [[ 5,  1,  4,  0],
     [-1,  3, -2,  2],
     [ 3, -1,  4,  0],
     [-3,  1, -2,  2]]

Why ``levels()`` is not just ``v * n*n * max_code // 255``
-----------------------------------------------------------

Per cell, the 86Box tables are provably *not* ``floor((a*v + b) / c)`` for any
integers (an exhaustive LP over the thresholds rules out every real affine
form).  ``levels()`` above is the shift-and-add approximation of ``/255`` that
does reproduce them; the ``+ (v >> 7)`` / ``+ (v >> 6)`` terms are exactly the
1/256 correction term of ``1/255 = (1/256)(1 + 1/256 + ...)``.

API
---
Same shape as the Scala ``object DitherTables``: eight flat tables, the eight
``lookup*`` accessors, and the four ``*_packed`` ROM images used by
``Dither.scala`` (romR/romG/romB) and ``FramebufferAccess.scala``
(dsubRbRom/dsubGRom).  Flat indexing matches the Scala exactly:
``v * n*n + y * n + x``.
"""

from __future__ import annotations

__all__ = [
    "ORDER_4X4",
    "ORDER_2X2",
    "rb4x4",
    "g4x4",
    "rb2x2",
    "g2x2",
    "dsub_rb4x4",
    "dsub_g4x4",
    "dsub_rb2x2",
    "dsub_g2x2",
    "rb_packed",
    "g_packed",
    "dsub_rb_packed",
    "dsub_g_packed",
    "lookup_rb",
    "lookup_g",
    "lookup_rb2x2",
    "lookup_g2x2",
    "lookup_dsub_rb",
    "lookup_dsub_g",
    "lookup_dsub_rb2x2",
    "lookup_dsub_g2x2",
    "packed_index",
    "PACKED_2X2_BASE",
]


# Bayer ranks: the order in which a tile's cells take the higher of the two
# output codes.  Both are the textbook Bayer matrix with its rows flipped.
ORDER_4X4 = (
    (15, 7, 13, 5),
    (3, 11, 1, 9),
    (12, 4, 14, 6),
    (0, 8, 2, 10),
)

ORDER_2X2 = (
    (3, 1),
    (0, 2),
)

# (mul, shift, bias) of the dithersub per-cell delta, per (n, bits).
_SUB_DELTA_PARAMS = {
    (4, 5): (1, 1, 3),
    (4, 6): (1, 2, 1),
    (2, 5): (2, 0, 4),
    (2, 6): (1, 0, 2),
}


def _levels_4x4(v: int, bits: int) -> int:
    """Sub-step count for an 8-bit channel value on the 16-cell tiles."""
    if bits == 5:
        return 2 * v - (v >> 4) + (v >> 7)
    return 4 * v - (v >> 4) + (v >> 6)


def _levels(v: int, n: int, bits: int) -> int:
    four = _levels_4x4(v, bits)
    return four if n == 4 else (four + 2) >> 2


def _build_dither(n: int, bits: int) -> tuple[int, ...]:
    order = ORDER_4X4 if n == 4 else ORDER_2X2
    cells = n * n
    max_code = (1 << bits) - 1
    out: list[int] = []
    for v in range(256):
        base, count = divmod(_levels(v, n, bits), cells)
        for y in range(n):
            for x in range(n):
                code = base + (1 if order[y][x] < count else 0)
                out.append(min(max_code, code))
    return tuple(out)


def _build_dither_sub(n: int, bits: int) -> tuple[int, ...]:
    order = ORDER_4X4 if n == 4 else ORDER_2X2
    mul, shift, bias = _SUB_DELTA_PARAMS[(n, bits)]
    step = 256 >> bits  # 8-bit distance between adjacent output codes
    out: list[int] = []
    for v in range(256):
        for y in range(n):
            for x in range(n):
                if v < step // 2:
                    out.append(0)
                    continue
                delta = (((order[y][x] + 1) * mul) >> shift) - bias
                out.append(max(0, min(255, v + delta)))
    return tuple(out)


#: ``DitherTableData.rb4x4`` — 5-bit red/blue, 4x4 tile, ``v*16 + y*4 + x``.
rb4x4 = _build_dither(4, 5)
#: ``DitherTableData.g4x4`` — 6-bit green, 4x4 tile.
g4x4 = _build_dither(4, 6)
#: ``DitherTableData.rb2x2`` — 5-bit red/blue, 2x2 tile, ``v*4 + y*2 + x``.
rb2x2 = _build_dither(2, 5)
#: ``DitherTableData.g2x2`` — 6-bit green, 2x2 tile.
g2x2 = _build_dither(2, 6)

#: ``DitherTableData.dsubRb4x4`` — 8-bit dither-subtract, red/blue, 4x4 tile.
dsub_rb4x4 = _build_dither_sub(4, 5)
#: ``DitherTableData.dsubG4x4`` — 8-bit dither-subtract, green, 4x4 tile.
dsub_g4x4 = _build_dither_sub(4, 6)
#: ``DitherTableData.dsubRb2x2`` — 8-bit dither-subtract, red/blue, 2x2 tile.
dsub_rb2x2 = _build_dither_sub(2, 5)
#: ``DitherTableData.dsubG2x2`` — 8-bit dither-subtract, green, 2x2 tile.
dsub_g2x2 = _build_dither_sub(2, 6)


def lookup_rb(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupRb``."""
    return rb4x4[v * 16 + y * 4 + x]


def lookup_g(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupG``."""
    return g4x4[v * 16 + y * 4 + x]


def lookup_rb2x2(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupRb2x2``."""
    return rb2x2[v * 4 + y * 2 + x]


def lookup_g2x2(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupG2x2``."""
    return g2x2[v * 4 + y * 2 + x]


def lookup_dsub_rb(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupDsubRb``."""
    return dsub_rb4x4[v * 16 + y * 4 + x]


def lookup_dsub_g(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupDsubG``."""
    return dsub_g4x4[v * 16 + y * 4 + x]


def lookup_dsub_rb2x2(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupDsubRb2x2``."""
    return dsub_rb2x2[v * 4 + y * 2 + x]


def lookup_dsub_g2x2(v: int, y: int, x: int) -> int:
    """``DitherTables.lookupDsubG2x2``."""
    return dsub_g2x2[v * 4 + y * 2 + x]


#: First address of the 2x2 half of a packed ROM image (``Dither.scala``: the
#: 4x4 table is exactly 2**12 entries, so bit 12 selects 2x2 with no adder).
PACKED_2X2_BASE = 4096

#: ``DitherTables.rbPacked`` — ``rb4x4 ++ rb2x2``, the romR/romB image.
rb_packed = rb4x4 + rb2x2
#: ``DitherTables.gPacked`` — ``g4x4 ++ g2x2``, the romG image.
g_packed = g4x4 + g2x2
#: ``DitherTables.dsubRbPacked`` — ``dsubRb4x4 ++ dsubRb2x2``, the dsubRbRom image.
dsub_rb_packed = dsub_rb4x4 + dsub_rb2x2
#: ``DitherTables.dsubGPacked`` — ``dsubG4x4 ++ dsubG2x2``, the dsubGRom image.
dsub_g_packed = dsub_g4x4 + dsub_g2x2


def packed_index(v: int, y: int, x: int, use_2x2: bool) -> int:
    """Address into a ``*_packed`` ROM image.

    Mirrors ``Dither.packedAddr`` (``Dither.scala:38``) and the identical
    ``ditherAddr`` in ``FramebufferAccess.scala:286``: 4x4 is
    ``v ## y(2) ## x(2)``, 2x2 is ``1 ## v ## y(0) ## x(0)``.
    """
    if use_2x2:
        return PACKED_2X2_BASE + (v << 2 | (y & 1) << 1 | (x & 1))
    return v << 4 | (y & 3) << 2 | (x & 3)
