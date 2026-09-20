"""Packed texture-memory layout tables: per-LOD base, end and row shift.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/texture/TexLayoutTables.scala``
(SpinalHDL 1.15.0), which is itself the non-split case of 86Box
``voodoo_recalc_tex12`` (``emu/86Box/src/video/vid_voodoo_texture.c:59-144``).

The Scala object is a *combinational* table computation shared by the write
path (Core drain) and the read path (TMU via ``PerTriangleConfig``).  It has
no state and no handshake, so the port is pure Python: :func:`compute` takes a
:class:`TexConfig` and returns :class:`Tables`, with the same field names and
the same 22-bit wrapping the Scala ``resize(22 bits)`` calls impose.  When the
hardware module lands (plan Phase 5), it elaborates the same nine unrolled
stages and can be diffed against this model.

Notes on fidelity, each matching a line of the Scala:

* dimensions are powers of two, so ``width * height * bpp`` is
  ``1 << (wide_bits + narrow_bits + is_16bit)`` — shifts and adds only;
* ``tex_base`` is ``texBaseAddr << 3`` (register 0x30C is in 8-byte units)
  plus the running offset, unless ``tlod_multibase`` redirects LOD 1, LOD 2
  and LODs 3..8 to ``texBaseAddr1``, ``texBaseAddr2`` and ``texBaseAddr38``;
* ``tex_shift`` is ``log2(row width in texels)``: the wide side when
  ``tlod_s_is_wider``, else the narrow side;
* every sum is truncated to 22 bits, including the two running offsets, so
  an overflowing configuration wraps here exactly as it does in the RTL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "LOD_COUNT",
    "ADDR_BITS",
    "ADDR_MASK",
    "ARGB8332",
    "TexConfig",
    "Tables",
    "wide_bits",
    "narrow_bits",
    "compute",
]

#: LODs the table covers (``for (lod <- 0 until 9)``).
LOD_COUNT = 9

#: Width of every address in the table (``UInt(22 bits)``).
ADDR_BITS = 22
ADDR_MASK = (1 << ADDR_BITS) - 1

#: ``Tmu.TextureFormat.ARGB8332``; ``tformat >= ARGB8332`` is 86Box's
#: ``tformat & 8``, i.e. "two bytes per texel".
ARGB8332 = 8


@dataclass(frozen=True)
class TexConfig:
    """``TexLayoutTables.TexConfig``. Widths as in the Scala Bundle."""

    tex_base_addr: int = 0  # register 0x30C bits [18:0]
    tex_base_addr1: int = 0
    tex_base_addr2: int = 0
    tex_base_addr38: int = 0
    tformat: int = 0  # textureMode bits [11:8]
    tlod_aspect: int = 0  # tLOD bits [22:21]
    tlod_s_is_wider: bool = False  # tLOD bit [20]
    tlod_multibase: bool = False  # tLOD bit [24]


@dataclass
class Tables:
    """``TexLayoutTables.Tables``: nine entries each, LOD 0 first."""

    tex_base: list[int] = field(default_factory=list)  # packed byte offset per LOD
    tex_end: list[int] = field(default_factory=list)  # packed end byte offset per LOD
    tex_shift: list[int] = field(default_factory=list)  # log2(row width in texels)


def wide_bits(lod: int) -> int:
    """``max(8 - lod, 0)`` — log2 of the wider side at this LOD."""
    return max(8 - lod, 0)


def narrow_bits(lod: int, aspect: int) -> int:
    """``max(8 - aspect - lod, 0)`` written out per aspect, as the Scala switch."""
    if aspect == 0:
        return wide_bits(lod)
    if aspect == 1:
        return max(7 - lod, 0)
    if aspect == 2:
        return max(6 - lod, 0)
    return max(5 - lod, 0)


def compute(cfg: TexConfig) -> Tables:
    """``TexLayoutTables.compute``: the nine unrolled stages, in order."""
    tables = Tables()

    is_16bit = 1 if cfg.tformat >= ARGB8332 else 0

    # Base address in bytes: texBaseAddr << 3 (19 bits << 3 == 22 bits exactly).
    base0 = (cfg.tex_base_addr << 3) & ADDR_MASK
    base1 = (cfg.tex_base_addr1 << 3) & ADDR_MASK
    base2 = (cfg.tex_base_addr2 << 3) & ADDR_MASK
    base38 = (cfg.tex_base_addr38 << 3) & ADDR_MASK

    offset = 0
    multibase_tail_offset = 0
    for lod in range(LOD_COUNT):
        wide = wide_bits(lod)
        narrow = narrow_bits(lod, cfg.tlod_aspect)

        lod_base = (base0 + offset) & ADDR_MASK
        if cfg.tlod_multibase:
            if lod == 1:
                lod_base = base1
            elif lod == 2:
                lod_base = base2
            elif lod >= 3:
                lod_base = (base38 + multibase_tail_offset) & ADDR_MASK
        tables.tex_base.append(lod_base)
        tables.tex_shift.append(wide if cfg.tlod_s_is_wider else narrow)

        # LOD area in bytes = 1 << (wide + narrow + is16bit); the shift amount
        # is resize(5 bits) in the Scala, hence the mask.
        shift_amount = (wide + narrow + is_16bit) & 0x1F
        lod_size = (1 << shift_amount) & ADDR_MASK
        tables.tex_end.append((lod_base + lod_size) & ADDR_MASK)
        offset = (offset + lod_size) & ADDR_MASK
        if lod >= 3:
            multibase_tail_offset = (multibase_tail_offset + lod_size) & ADDR_MASK

    return tables
