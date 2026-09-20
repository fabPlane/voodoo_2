"""The TMU's two constant ROMs: the 1/w reciprocal LUT and the log2 LUT.

Port of the ``lookupTables`` area of
``ext/SpinalVoodoo/src/voodoo/texture/Tmu.scala:53-80`` — only the table data,
not the pipeline stages that read it (plan Phase 5 owns ``Tmu`` itself).
Both are generated, neither is copied: the Scala writes the reciprocal table
as a formula and the log table as a 256-entry literal, and the literal is
exactly ``floor(log2(1 + i/256) * 256)`` — the same numbers as 86Box's
``logtable`` in ``emu/86Box/src/video/vid_voodoo_render.c:111``, which
``voodoo_log2`` (line 170) uses as ``(exp << 8) | logtable[frac]``.

``tests/test_tmu_tables.py`` asserts the log table against the Scala literal
entry by entry (extracted to ``tests/data/tmu_tables_spinal.json`` by
:mod:`tools.extract_spinal_tables`) and the reciprocal table against a
transliteration of the Scala expression.
"""

from __future__ import annotations

__all__ = [
    "RECIP_TABLE_SIZE",
    "RECIP_TABLE_BITS",
    "LOG_TABLE_SIZE",
    "LOG_TABLE_BITS",
    "recip_table",
    "log_table",
]

#: ``Array.tabulate(512)``; entries past index 256 repeat index 256, so the
#: hardware can read ``index`` and ``index + 1`` (the linear interpolation in
#: ``Tmu.scala:101``) without a bounds check.
RECIP_TABLE_SIZE = 512
RECIP_TABLE_BITS = 17

#: ``logTableValues``: the 8-bit fractional part of log2, 256 entries.
LOG_TABLE_SIZE = 256
LOG_TABLE_BITS = 8


def _recip(i: int) -> int:
    """``round((1 << 24).toDouble / (256.0 + clamped))`` in exact integers.

    Scala's ``math.round`` is ``floor(x + 0.5)``; with ``a = 1 << 24`` and
    ``b = 256 + clamped`` that is ``(2a + b) // (2b)``.  No tie can occur
    (``2**25 / b`` is even for every ``b`` in 256..512 that divides it), so
    the half-up direction is not observable — it is spelled out anyway.
    """
    clamped = i if i <= 256 else 256
    a = 1 << 24
    b = 256 + clamped
    return (2 * a + b) // (2 * b)


def _log(i: int) -> int:
    """``floor(log2(1 + i/256) * 256)``.

    Computed with :func:`math.log2`; the closest any entry comes to an integer
    boundary is 1.6e-3, so double rounding cannot move one (the test pins all
    256 values against the Scala literal regardless).
    """
    import math

    return int(math.log2(1.0 + i / 256.0) * 256.0)


#: 17-bit ``(1 << 24) / (256 + i)`` reciprocal ROM (``Tmu.recipTable``).
recip_table = tuple(_recip(i) for i in range(RECIP_TABLE_SIZE))

#: 8-bit log2 fraction ROM (``Tmu.logTable``).
log_table = tuple(_log(i) for i in range(LOG_TABLE_SIZE))
