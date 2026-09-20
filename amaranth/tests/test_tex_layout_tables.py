"""``avoodoo.texture.tex_layout_tables`` against two independent models.

The Scala (``ext/SpinalVoodoo/src/voodoo/texture/TexLayoutTables.scala``) is
formula-driven combinational logic, not a literal table, so there is nothing
to extract with ``tools/extract_spinal_tables.py``.  Instead:

1. :func:`recalc_tex12_reference` is an independent reimplementation of
   86Box ``voodoo_recalc_tex12``
   (``ext/SpinalVoodoo/emu/86Box/src/video/vid_voodoo_texture.c:59-144``),
   written from the C's own ``width``/``height``/``shift`` loop rather than
   from the Scala's shift arithmetic.  Every non-multibase configuration
   (4 aspects x 2 ``sIsWider`` x both texel sizes) is compared LOD by LOD.
2. The multibase cases and the 22-bit wrapping are pinned with hand-computed
   expectations, because there the **Scala deliberately differs from 86Box**:
   86Box keeps adding its running ``offset`` to the replaced base (so its
   ``tex_base[1]`` is ``texBaseAddr1 + size(LOD0)``), while the Scala uses
   ``texBaseAddr1`` bare for LOD 1, ``texBaseAddr2`` bare for LOD 2, and
   ``texBaseAddr38`` plus an offset that restarts at LOD 3.  The port follows
   the Scala (plan §6: 1:1 with the Scala, refactors later); this test records
   the divergence so it cannot be "fixed" by accident.
"""

from __future__ import annotations

import pytest

from avoodoo.texture import tex_layout_tables as tlt
from avoodoo.texture.tex_layout_tables import ADDR_MASK, LOD_COUNT, TexConfig, compute


def recalc_tex12_reference(cfg: TexConfig) -> tuple[list[int], list[int], list[int]]:
    """86Box ``voodoo_recalc_tex12``, non-split path, addresses in bytes.

    Follows the C literally (dimensions and a shift counter, clamped each
    iteration) instead of the Scala's ``1 << (wBits + hBits + is16bit)``.
    ``base`` is scaled by 8 because the Scala works in bytes while 86Box keeps
    the raw register value.
    """
    aspect = cfg.tlod_aspect
    width = 256
    height = 256
    shift = 8
    base = (cfg.tex_base_addr << 3) & ADDR_MASK
    offset = 0
    tex_lod = 0
    two_bytes = 2 if (cfg.tformat & 8) else 1

    if cfg.tlod_s_is_wider:
        height >>= aspect
    else:
        width >>= aspect
        shift -= aspect

    bases: list[int] = []
    ends: list[int] = []
    shifts: list[int] = []
    for _lod in range(LOD_COUNT):
        width = max(width, 1)
        height = max(height, 1)
        shift = max(shift, 0)
        size = width * height * two_bytes
        bases.append((base + offset) & ADDR_MASK)
        ends.append((base + offset + size) & ADDR_MASK)
        shifts.append(shift)

        offset = (offset + size) & ADDR_MASK
        width >>= 1
        height >>= 1
        shift -= 1
        tex_lod += 1
        if cfg.tlod_multibase:
            base = [
                cfg.tex_base_addr,
                cfg.tex_base_addr1,
                cfg.tex_base_addr2,
                cfg.tex_base_addr38,
            ][min(tex_lod, 3)]
            base = (base << 3) & ADDR_MASK
    return bases, ends, shifts


NON_MULTIBASE = [
    (aspect, s_is_wider, tformat, base)
    for aspect in range(4)
    for s_is_wider in (False, True)
    for tformat in (0, 3, 8, 10, 13)
    for base in (0, 1, 0x100, 0x7FFF)
]


@pytest.mark.parametrize("aspect, s_is_wider, tformat, base", NON_MULTIBASE)
def test_matches_86box_reference(aspect, s_is_wider, tformat, base):
    cfg = TexConfig(
        tex_base_addr=base,
        tformat=tformat,
        tlod_aspect=aspect,
        tlod_s_is_wider=s_is_wider,
    )
    tables = compute(cfg)
    want_base, want_end, want_shift = recalc_tex12_reference(cfg)
    assert tables.tex_base == want_base
    assert tables.tex_end == want_end
    assert tables.tex_shift == want_shift


def test_default_square_8bit_layout():
    """Hand-computed: 256x256 8bpp, aspect 0, base 0 — the textbook mip chain."""
    tables = compute(TexConfig())
    sizes = [1 << (2 * max(8 - lod, 0)) for lod in range(LOD_COUNT)]
    assert sizes == [65536, 16384, 4096, 1024, 256, 64, 16, 4, 1]
    running = 0
    for lod in range(LOD_COUNT):
        assert tables.tex_base[lod] == running
        assert tables.tex_end[lod] == running + sizes[lod]
        assert tables.tex_shift[lod] == max(8 - lod, 0)
        running += sizes[lod]
    assert running == 87381


def test_16bit_doubles_every_lod():
    eight = compute(TexConfig(tformat=0))
    sixteen = compute(TexConfig(tformat=tlt.ARGB8332))
    for lod in range(LOD_COUNT):
        assert sixteen.tex_base[lod] == 2 * eight.tex_base[lod]
        assert sixteen.tex_end[lod] - sixteen.tex_base[lod] == 2 * (
            eight.tex_end[lod] - eight.tex_base[lod]
        )
        assert sixteen.tex_shift[lod] == eight.tex_shift[lod]


@pytest.mark.parametrize("aspect, narrow_at_lod0", [(0, 8), (1, 7), (2, 6), (3, 5)])
def test_aspect_selects_the_narrow_side(aspect, narrow_at_lod0):
    """``tex_shift`` is the wide side iff ``sIsWider``, else the narrow one."""
    wide = compute(TexConfig(tlod_aspect=aspect, tlod_s_is_wider=True))
    narrow = compute(TexConfig(tlod_aspect=aspect, tlod_s_is_wider=False))
    assert wide.tex_shift == [max(8 - lod, 0) for lod in range(LOD_COUNT)]
    assert narrow.tex_shift == [max(narrow_at_lod0 - lod, 0) for lod in range(LOD_COUNT)]
    assert tlt.narrow_bits(0, aspect) == narrow_at_lod0
    # the per-LOD sizes only depend on the pair, not on which side is wider
    assert [e - b for b, e in zip(wide.tex_base, wide.tex_end)] == [
        e - b for b, e in zip(narrow.tex_base, narrow.tex_end)
    ]


def test_multibase_redirects_lod1_lod2_and_the_tail():
    cfg = TexConfig(
        tex_base_addr=0x10,
        tex_base_addr1=0x20,
        tex_base_addr2=0x30,
        tex_base_addr38=0x40,
        tlod_multibase=True,
    )
    tables = compute(cfg)
    sizes = [1 << (2 * max(8 - lod, 0)) for lod in range(LOD_COUNT)]
    assert tables.tex_base[0] == 0x10 << 3
    assert tables.tex_base[1] == 0x20 << 3
    assert tables.tex_base[2] == 0x30 << 3
    tail = 0
    for lod in range(3, LOD_COUNT):
        assert tables.tex_base[lod] == (0x40 << 3) + tail
        assert tables.tex_end[lod] == (0x40 << 3) + tail + sizes[lod]
        tail += sizes[lod]
    # LOD 0 still ends at base0 + size0; the tail offset is separate.
    assert tables.tex_end[0] == (0x10 << 3) + sizes[0]


def test_multibase_diverges_from_86box_by_design():
    """86Box adds its running offset to the replaced base; the Scala does not."""
    cfg = TexConfig(
        tex_base_addr=0x10,
        tex_base_addr1=0x20,
        tex_base_addr2=0x30,
        tex_base_addr38=0x40,
        tlod_multibase=True,
    )
    ours = compute(cfg).tex_base
    theirs, _ends, _shifts = recalc_tex12_reference(cfg)
    assert ours[0] == theirs[0]
    assert ours[1] != theirs[1]
    assert theirs[1] == (0x20 << 3) + 65536


def test_multibase_ignored_when_disabled():
    cfg = TexConfig(tex_base_addr=0x10, tex_base_addr1=0x2000, tex_base_addr38=0x3000)
    assert compute(cfg).tex_base == compute(TexConfig(tex_base_addr=0x10)).tex_base


def test_addresses_wrap_at_22_bits():
    """``resize(22 bits)`` truncates; a base near the top wraps, it does not saturate."""
    cfg = TexConfig(tex_base_addr=0x7FFFF)  # 19 bits set -> 0x3FFFF8 bytes
    tables = compute(cfg)
    assert tables.tex_base[0] == 0x3FFFF8
    assert tables.tex_end[0] == (0x3FFFF8 + 65536) & ADDR_MASK
    assert max(tables.tex_base) <= ADDR_MASK
    assert max(tables.tex_end) <= ADDR_MASK
    assert all(0 <= s <= 15 for s in tables.tex_shift)


def test_table_lengths():
    tables = compute(TexConfig())
    assert len(tables.tex_base) == len(tables.tex_end) == len(tables.tex_shift) == 9
