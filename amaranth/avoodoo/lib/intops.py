"""SpinalHDL ``SInt``/``UInt`` width rules, for Amaranth expressions.

Amaranth and SpinalHDL disagree about what plain integer arithmetic *does to
the width*, and a 1:1 port of the core has to follow Spinal's rule at every
operator or the wrap points move:

=====================  ===================================  ==========================
Operation              SpinalHDL 1.15 (``BitVector``,        Amaranth
                       ``spinal/core/SInt.scala``,
                       ``UInt.scala``, ``BitVector.scala``)
=====================  ===================================  ==========================
``a + b``, ``a - b``   ``max(w_a, w_b)`` bits, operands      ``max(w_a, w_b) + 1`` bits
                       resized to that width first
                       (``+^``/``-^`` are the growing ones)
``x.resize(n)``        sign-/zero-extend, or keep the low    -- (slice/``Cat`` by hand)
                       ``n`` bits
``x.resized``          the same, to the assignment target's  --
                       width
``x |<< n``            shift left, **width kept**, MSBs      ``x << n`` grows by ``n``
                       lost (``ShiftLeftByIntFixedWidth``)
``x >> n`` (``Int``)   ``max(w - n, 0)`` bits, arithmetic    ``x >> n`` keeps ``w`` bits
                       for ``SInt`` (``ShiftRightByInt``)
=====================  ===================================  ==========================

Each helper here is one row of that table, named after the Spinal spelling,
and documented with the rule it reproduces.  They take and return plain
Amaranth ``Value``s; nothing here knows about ``AFix`` (that is
:mod:`avoodoo.lib.fixed`, which uses the same conventions internally).

The raster modules grew private copies of these while ``lib/`` had none
(``triangle_setup._s_add``/``_sext``/``_shl_keep``/``_sar``,
``span_walker._sresize``); this is the shared home they can move to.
"""

from __future__ import annotations

from amaranth.hdl import Cat, Const, Value, unsigned

__all__ = [
    "sext",
    "zext",
    "s_resize",
    "u_resize",
    "resize",
    "s_add",
    "s_sub",
    "u_add",
    "u_sub",
    "shl_keep",
    "sar",
    "u_shr",
]


# ---------------------------------------------------------------------------
# resize
# ---------------------------------------------------------------------------


def _as_signed_width(bits: Value, width: int) -> Value:
    """``bits`` (``width`` wide) reinterpreted as signed; Amaranth has no ``signed(0)``."""
    if width == 0:
        return Const(0, unsigned(0))
    return bits.as_signed()


def sext(value, width: int) -> Value:
    """``SInt.resize(width bits)``.

    SpinalHDL 1.15 ``SInt.resize``: widening sign-extends; narrowing keeps
    the low ``width`` bits (it wraps, silently -- Spinal only warns when the
    *assignment* is implicit).  The argument must already be signed; use
    :func:`resize` when the source's signedness is not known statically.
    """
    v = Value.cast(value)
    if not v.shape().signed:
        raise TypeError(f"sext() wants a signed value, got {v.shape()}")
    if width < 0:
        raise ValueError(f"resize({width}) is not a width")
    if len(v) >= width:
        return _as_signed_width(v[:width], width)
    return Cat(v, v[-1].replicate(width - len(v))).as_signed()


def zext(value, width: int) -> Value:
    """``UInt.resize(width bits)``.

    SpinalHDL 1.15 ``UInt.resize``: widening zero-extends; narrowing keeps
    the low ``width`` bits.  A signed argument is taken as its bit pattern
    (``.asUInt`` first), which is what ``x.asBits.resize`` does.
    """
    v = Value.cast(value).as_unsigned()
    if width < 0:
        raise ValueError(f"resize({width}) is not a width")
    if len(v) >= width:
        return v[:width]
    return Cat(v, Const(0, width - len(v)))


#: Aliases spelled after the Spinal type, for call sites that read that way.
s_resize = sext
u_resize = zext


def resize(value, width: int) -> Value:
    """``x.resize(width bits)`` / ``x.resized`` with ``x``'s own signedness.

    Spinal picks sign- or zero-extension from the *source* type (``SInt`` vs
    ``UInt``) and the result keeps that type; this does the same from the
    Amaranth shape.  ``.resized`` is the same operation with ``width`` taken
    from the assignment target, so pass ``len(target)``.
    """
    v = Value.cast(value)
    return sext(v, width) if v.shape().signed else zext(v, width)


# ---------------------------------------------------------------------------
# add / sub
# ---------------------------------------------------------------------------


def _fixed_width(a: Value, b: Value, width: int | None) -> int:
    return max(len(a), len(b)) if width is None else width


def s_add(a, b, width: int | None = None) -> Value:
    """``SInt + SInt``.

    SpinalHDL 1.15 ``BitVector`` binary ``+`` (``SInt.scala``, ``Num``):
    both operands are resized to ``max(w_a, w_b)`` and the sum is that same
    width -- the carry out is dropped.  ``+^`` is the one that grows.  An
    explicit ``width`` reproduces ``(a + b).resize(width bits)``, which the
    core writes at several accumulators.
    """
    a, b = Value.cast(a), Value.cast(b)
    if not (a.shape().signed and b.shape().signed):
        raise TypeError(f"s_add() wants two signed values, got {a.shape()} and {b.shape()}")
    return sext(a + b, _fixed_width(a, b, width))


def s_sub(a, b, width: int | None = None) -> Value:
    """``SInt - SInt``: as :func:`s_add`, ``max(w_a, w_b)`` bits, no borrow out."""
    a, b = Value.cast(a), Value.cast(b)
    if not (a.shape().signed and b.shape().signed):
        raise TypeError(f"s_sub() wants two signed values, got {a.shape()} and {b.shape()}")
    return sext(a - b, _fixed_width(a, b, width))


def u_add(a, b, width: int | None = None) -> Value:
    """``UInt + UInt``.

    SpinalHDL 1.15 ``UInt.scala``: both operands zero-extended to
    ``max(w_a, w_b)``, sum kept at that width, carry dropped (``+^`` grows).
    """
    a, b = Value.cast(a), Value.cast(b)
    if a.shape().signed or b.shape().signed:
        raise TypeError(f"u_add() wants two unsigned values, got {a.shape()} and {b.shape()}")
    return zext(a + b, _fixed_width(a, b, width))


def u_sub(a, b, width: int | None = None) -> Value:
    """``UInt - UInt``: ``max(w_a, w_b)`` bits, wraps below zero (no borrow out)."""
    a, b = Value.cast(a), Value.cast(b)
    if a.shape().signed or b.shape().signed:
        raise TypeError(f"u_sub() wants two unsigned values, got {a.shape()} and {b.shape()}")
    return zext(a - b, _fixed_width(a, b, width))


# ---------------------------------------------------------------------------
# shifts
# ---------------------------------------------------------------------------


def shl_keep(value, shift: int, width: int | None = None) -> Value:
    """``x |<< n``.

    SpinalHDL 1.15 ``BitVector.|<<`` (``Operator.BitVector.ShiftLeftByIntFixedWidth``):
    shift left by a constant and **keep the width**, so the top ``n`` bits fall
    off.  Plain ``<<`` grows by ``n``.  Works for ``SInt`` and ``UInt`` alike.

    An explicit ``width`` is literally the trailing ``.resize(width bits)`` the
    core writes after most of its ``|<<``: the shift still happens at the
    *operand's* width (so widening cannot bring dropped bits back), and the
    result is then resized.  For ``(x.resize(w) |<< n)`` -- shift *after*
    widening -- resize the operand first, as the Scala does.
    """
    v = Value.cast(value)
    if shift < 0:
        raise ValueError(f"|<< {shift}: shift must be non-negative")
    kept = resize(v << shift, len(v))
    return kept if width is None else resize(kept, width)


def sar(value, shift: int) -> Value:
    """``SInt >> n`` with a constant ``n``.

    SpinalHDL 1.15 ``BitVector.>>(Int)`` (``Operator.BitVector.ShiftRightByInt``):
    arithmetic shift, and the result is ``max(w - n, 0)`` bits wide -- the
    dropped low bits are gone, not zero.  (``>>`` by a *``UInt``* keeps the
    width; that is a different operator and not this helper.)
    """
    v = Value.cast(value)
    if not v.shape().signed:
        raise TypeError(f"sar() wants a signed value, got {v.shape()}")
    if shift < 0:
        raise ValueError(f">> {shift}: shift must be non-negative")
    width = max(len(v) - shift, 0)
    return _as_signed_width(v[shift:shift + width], width)


def u_shr(value, shift: int) -> Value:
    """``UInt >> n`` with a constant ``n``: logical, ``max(w - n, 0)`` bits."""
    v = Value.cast(value)
    if v.shape().signed:
        raise TypeError(f"u_shr() wants an unsigned value, got {v.shape()}")
    if shift < 0:
        raise ValueError(f">> {shift}: shift must be non-negative")
    width = max(len(v) - shift, 0)
    return v[shift:shift + width]
