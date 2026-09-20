"""Range-based fixed point, bit-compatible with SpinalHDL ``AFix``.

Mirrors **SpinalHDL 1.15.0** ``spinal/core/AFix.scala`` (with
``spinal/core/QFormat.scala``, ``spinal/core/FixedPoint.scala`` and the
``UInt``/``SInt`` rounding primitives it delegates to), which is the version
``fpga/project.scala`` pins and therefore the one that produces today's
bitstreams.  The full semantics, every trap, and the concrete formats used by
the core are written up in ``docs/amaranth/afix-semantics.md``; that document
is the specification this module implements, and the section numbers in the
comments below refer to it.

Phase 0.2 of ``docs/amaranth-rewrite-plan.md``.  ``amaranth.lib.fixed`` does
not exist in any released Amaranth (§10.1), and the shape it proposes is
``(width, fraction)``-based, which cannot express AFix's arbitrary
``(maxRaw, minRaw)`` bounds — so this is our own implementation.

The essential idea, and the reason a width-only port would be wrong: an
``AFix`` is a raw two's-complement word *plus a statically known integer value
range* ``[min_raw, max_raw] * 2**exp``.  Widths, signedness, saturation
comparisons and rounded output widths all fall out of the **bounds**, not out
of an (integer bits, fraction bits) pair.

Only the surface the core actually uses is implemented; see §4 of the spec for
the inventory and §10.2 for this API.  ``RoundType`` is defined in the
Amaranth-free :mod:`avoodoo.lib.fixed_model` and re-exported here so the
hardware implementation and the reference model share one enum.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Iterable

from amaranth.hdl import Cat, Const, Mux, Shape, ShapeCastable, Value, ValueCastable, signed, unsigned

from avoodoo.config import QFormat, SQ, UQ
from avoodoo.lib.fixed_model import RoundType

__all__ = [
    "RoundType",
    "QFormat",
    "SQ",
    "UQ",
    "AFixShape",
    "AFixValue",
    "afix_mux",
    "round_bigint",
    "rounded_bounds",
    "round_shape",
    "rescale_sat_bounds",
    "add_shape",
    "sub_shape",
    "mul_shape",
    "neg_shape",
    "sat_shape",
    "holding_shape",
    "resize_shape",
    "fix_to_shape",
]


# ---------------------------------------------------------------------------
# integer helpers
# ---------------------------------------------------------------------------


def _trunc_div(numer: int, denom: int) -> int:
    """Scala ``BigInt./``: truncates **toward zero** (§8.6).

    Python's ``//`` floors, which differs for negative numerators.  Used in
    exactly the two places Spinal uses ``BigInt./``: ``_roundFixedBigInt`` and
    the three-argument ``sat`` rescale.
    """
    quot = abs(numer) // abs(denom)
    return -quot if (numer < 0) != (denom < 0) else quot


def round_bigint(value: int, drop: int, rt: RoundType) -> int:
    """``_roundFixedBigInt(i, exp, roundType)`` (§6.1).

    Written the way Spinal writes it — around a truncate-toward-zero division
    and the ``halfBit``/``halfDropped`` decomposition — rather than around
    Python's flooring shift, so that the §8.6 trap is reproduced literally
    rather than papered over.  ``drop <= 0`` is the identity (§8.5).
    """
    if drop <= 0:
        return value
    scaled = _trunc_div(value, 1 << drop)
    magnitude = abs(value)
    half_bit = (magnitude >> (drop - 1)) & 1
    half_dropped = (magnitude & ((1 << (drop - 1)) - 1)) != 0
    dropped_bits = bool(half_bit) or half_dropped
    positive = value >= 0

    if rt is RoundType.FLOOR:
        # toward -inf: trunc already floors for positives, overshoots for negatives
        return scaled if positive else scaled - (1 if dropped_bits else 0)
    if rt is RoundType.CEIL:
        # toward +inf
        return scaled + (1 if dropped_bits else 0) if positive else scaled
    if rt is RoundType.ROUND_HALF_UP:
        # RoundType.ROUNDUP: ties toward +inf == floor(i + 2**(drop-1))
        return round_bigint(value + (1 << (drop - 1)), drop, RoundType.FLOOR)
    if rt is RoundType.ROUND_HALF_DOWN:
        # RoundType.ROUNDDOWN: ties toward -inf == ceil(i - 2**(drop-1))
        return round_bigint(value - (1 << (drop - 1)), drop, RoundType.CEIL)
    raise ValueError(f"unsupported RoundType {rt!r}")


# ---------------------------------------------------------------------------
# AFixShape
# ---------------------------------------------------------------------------


class AFixShape(ShapeCastable):
    """``class AFix(maxRaw: BigInt, minRaw: BigInt, exp: Int)`` (§1).

    Immutable and hashable.  Usable wherever Amaranth accepts a shape-castable
    (``Signal(shape)``, ``data.StructLayout`` members, ``wiring.Signature``),
    in which case the underlying storage is ``signed``/``unsigned(bit_width)``
    and the wrapper handed back is an :class:`AFixValue`.
    """

    __slots__ = ("_max_raw", "_min_raw", "_exp")

    def __init__(self, max_raw: int, min_raw: int, exp: int):
        max_raw = int(max_raw)
        min_raw = int(min_raw)
        if min_raw > max_raw:
            raise ValueError(f"empty AFix range: min_raw={min_raw} > max_raw={max_raw}")
        object.__setattr__(self, "_max_raw", max_raw)
        object.__setattr__(self, "_min_raw", min_raw)
        object.__setattr__(self, "_exp", int(exp))

    def __setattr__(self, name, value):  # pragma: no cover - immutability guard
        raise AttributeError("AFixShape is immutable")

    # -- the three defining fields ----------------------------------------

    @property
    def max_raw(self) -> int:
        return self._max_raw

    @property
    def min_raw(self) -> int:
        return self._min_raw

    @property
    def exp(self) -> int:
        return self._exp

    # -- derived fields, verbatim from AFix.scala:133-162 (§1) -------------

    @property
    def signed(self) -> bool:
        return self._max_raw < 0 or self._min_raw < 0

    @property
    def sign_width(self) -> int:
        return 1 if self.signed else 0

    @property
    def bit_width(self) -> int:
        sign_width = self.sign_width
        max_shifted = abs(self._max_raw) - (sign_width if self._max_raw < 0 else 0)
        min_shifted = abs(self._min_raw) - (sign_width if self._min_raw < 0 else 0)
        return max(max_shifted.bit_length(), min_shifted.bit_length()) + sign_width

    @property
    def frac_width(self) -> int:
        return max(-self._exp, 0)

    @property
    def whole_width(self) -> int:
        return self.bit_width - self.frac_width - self.sign_width

    @property
    def int_width(self) -> int:
        return self.bit_width - self.frac_width

    @property
    def num_width(self) -> int:
        return self.bit_width - self.sign_width

    @property
    def max_exp(self) -> int:
        return self._exp + self.num_width

    @property
    def left_exp(self) -> int:
        return self._exp + self.bit_width

    @property
    def step(self) -> Fraction:
        return Fraction(2) ** self._exp

    @property
    def max_value(self) -> Fraction:
        return self._max_raw * self.step

    @property
    def min_value(self) -> Fraction:
        return self._min_raw * self.step

    # -- constructors (§2) -------------------------------------------------

    @classmethod
    def from_exp(cls, max_exp: int, min_exp: int, signed: bool) -> "AFixShape":
        """``AFix(maxExp exp, minExp exp, signed)``."""
        n = max_exp - min_exp
        if n < 0:
            raise ValueError(f"AFix(max_exp={max_exp}, min_exp={min_exp}) is empty")
        return cls((1 << n) - 1, -(1 << n) if signed else 0, min_exp)

    @classmethod
    def from_q(cls, q: QFormat) -> "AFixShape":
        """``AFix(Q: QFormat)`` (§1.1).

        Careful: ``UQ(0, 8)`` is a *zero bit* format, not "0 integer bits and
        8 fractional bits" — see §8.1, which this must reproduce.
        """
        integer_width = q.width - q.fraction - (1 if q.signed else 0)
        return cls.from_exp(integer_width, -q.fraction, q.signed)

    @classmethod
    def sq(cls, int_bits: int, frac_bits: int) -> "AFixShape":
        """``AFix.SQ(i bits, f bits)`` — the sign bit is **added on top**."""
        return cls.from_exp(int_bits, -frac_bits, True)

    @classmethod
    def uq(cls, int_bits: int, frac_bits: int) -> "AFixShape":
        """``AFix.UQ(i bits, f bits)``."""
        return cls.from_exp(int_bits, -frac_bits, False)

    @classmethod
    def from_sint_width(cls, width: int) -> "AFixShape":
        """The shape of ``AFix(s: SInt)`` for an ``SInt(width bits)``."""
        return cls((1 << (width - 1)) - 1, -(1 << (width - 1)), 0)

    @classmethod
    def holding(cls, shapes: Iterable["AFixShape"]) -> "AFixShape":
        """``AFix.holdingParams`` — the mux bounding box (§5.4)."""
        shapes = list(shapes)
        if not shapes:
            raise ValueError("AFixShape.holding() needs at least one shape")
        exp = min(s.exp for s in shapes)
        return cls(
            max(s.max_raw << max(s.exp - exp, 0) for s in shapes),
            min(s.min_raw << max(s.exp - exp, 0) for s in shapes),
            exp,
        )

    # -- ShapeCastable protocol -------------------------------------------

    def as_shape(self) -> Shape:
        width = self.bit_width
        return signed(width) if self.signed else unsigned(width)

    def __call__(self, target) -> "AFixValue":
        return AFixValue(self, target)

    def const(self, value) -> "AFixValue":
        """``x := <BigDecimal>`` (§2): raw is ``value * 2**-exp``, exactly."""
        if value is None:
            value = 0
        if isinstance(value, AFixValue):
            if value.afix_shape != self:
                raise ValueError(f"cannot use {value.afix_shape} as a constant of {self}")
            return value
        scaled = Fraction(value) / self.step
        if scaled.denominator != 1:
            raise ValueError(f"{value} is not representable at exp={self.exp}")
        return self.from_raw_int(int(scaled))

    def const_trunc(self, value) -> "AFixValue":
        """``AF(value, i, f, signed)`` literal: raw rounds toward zero (§2)."""
        scaled = Fraction(value) / self.step
        return self.from_raw_int(int(scaled))  # int(Fraction) truncates toward zero

    def from_raw_int(self, raw: int) -> "AFixValue":
        if not (self._min_raw <= raw <= self._max_raw):
            raise ValueError(f"raw {raw} is outside {self}")
        width = self.bit_width
        if width == 0:
            return AFixValue(self, Const(0, unsigned(0)))
        return AFixValue(self, Const(raw, self.as_shape()))

    def from_bits(self, raw: int):
        """Simulator hook: lift a bit pattern to a Python integer raw value."""
        width = self.bit_width
        if width == 0:
            return 0
        raw &= (1 << width) - 1
        if self.signed and raw >> (width - 1):
            raw -= 1 << width
        return raw

    # -- misc --------------------------------------------------------------

    def covers(self, other: "AFixShape") -> bool:
        """True if ``other``'s *value* range fits in this one's (§5.5)."""
        return self.min_value <= other.min_value and other.max_value <= self.max_value

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, AFixShape)
            and self._max_raw == other._max_raw
            and self._min_raw == other._min_raw
            and self._exp == other._exp
        )

    def __hash__(self) -> int:
        return hash((AFixShape, self._max_raw, self._min_raw, self._exp))

    def __repr__(self) -> str:
        kind = "S" if self.signed else "U"
        return (
            f"AFixShape(max_raw={self._max_raw}, min_raw={self._min_raw}, exp={self._exp})"
            f"  # {kind}{self.bit_width}b, exp {self._exp}"
        )


# ---------------------------------------------------------------------------
# raw-word plumbing
# ---------------------------------------------------------------------------


def _fit(value: Value, shape: AFixShape) -> Value:
    """Put ``value`` into ``shape``'s storage word.

    Low bits are kept and high bits dropped when narrowing (Spinal's
    ``trim``/``resized``); widening sign- or zero-extends according to the
    *source*'s signedness so the numeric value is preserved.
    """
    width = shape.bit_width
    if width == 0:
        return Const(0, unsigned(0))
    value = Value.cast(value)
    if len(value) == 0:
        return Const(0, shape.as_shape())
    if len(value) < width:
        fill = value[-1] if value.shape().signed else Const(0, 1)
        value = Cat(value, fill.replicate(width - len(value)))
    bits = value[:width]
    return bits.as_signed() if shape.signed else bits


def _fit_bits(value: Value, shape: AFixShape) -> Value:
    """Copy ``value`` into ``shape``'s storage word as a **bit pattern**.

    Spinal's ``Bits`` ``resized``: zero-extends when widening whatever the
    source's signedness is, keeps the low bits when narrowing.  This is what
    ``x.raw := <bits>`` and ``x := <UInt>/<SInt>`` do (§5.5).
    """
    width = shape.bit_width
    if width == 0:
        return Const(0, unsigned(0))
    value = Value.cast(value).as_unsigned()
    if len(value) < width:
        value = Cat(value, Const(0, width - len(value)))
    bits = value[:width]
    return bits.as_signed() if shape.signed else bits


def _align_raws(left: "AFixValue", right: "AFixValue") -> tuple[Value, Value]:
    """``alignLR``: shift the coarser operand's raw left to the finer exp."""
    exp_diff = left.afix_shape.exp - right.afix_shape.exp
    lraw, rraw = left.raw, right.raw
    if exp_diff > 0:
        lraw = lraw << exp_diff
    elif exp_diff < 0:
        rraw = rraw << -exp_diff
    return lraw, rraw


class _Tagged:
    """``TagAFixTruncated``: a value plus the tag its next ``:=`` consumes (§6.6)."""

    __slots__ = ("value", "saturation", "overflow", "rounding")

    def __init__(self, value: "AFixValue", saturation: bool, overflow: bool, rounding: RoundType):
        self.value = value
        self.saturation = saturation
        self.overflow = overflow
        self.rounding = rounding


# ---------------------------------------------------------------------------
# AFixValue
# ---------------------------------------------------------------------------


class AFixValue(ValueCastable):
    """An Amaranth value carrying an :class:`AFixShape`.

    ``raw`` is the storage word, typed ``signed``/``unsigned(bit_width)`` —
    Spinal's ``Bits(bitWidth)`` plus the "interpreted as signed iff ``signed``"
    rule of §1 folded in.
    """

    __slots__ = ("_shape", "_raw")

    def __init__(self, shape: AFixShape, target):
        if not isinstance(shape, AFixShape):
            raise TypeError(f"{shape!r} is not an AFixShape")
        raw = Value.cast(target)
        if len(raw) != shape.bit_width:
            raise ValueError(
                f"{shape} needs a {shape.bit_width}-bit storage word, got {len(raw)} bits"
            )
        self._shape = shape
        self._raw = raw

    # -- ValueCastable protocol -------------------------------------------

    def shape(self) -> AFixShape:
        """The :class:`AFixShape` (Amaranth's ``ValueCastable`` hook)."""
        return self._shape

    #: Same thing as an attribute, for call sites that read like the Scala.
    @property
    def afix_shape(self) -> AFixShape:
        return self._shape

    def as_value(self) -> Value:
        return self._raw

    @property
    def raw(self) -> Value:
        """``.raw`` — the storage word (§5.6)."""
        return self._raw

    # -- construction ------------------------------------------------------

    @staticmethod
    def from_sint(value: Value) -> "AFixValue":
        """``AFix(s: SInt)`` (§2): bounds from the width, ``exp = 0``."""
        value = Value.cast(value)
        if not value.shape().signed:
            raise TypeError("AFixValue.from_sint() wants a signed value")
        return AFixValue(AFixShape.from_sint_width(len(value)), value)

    @staticmethod
    def from_raw(shape: AFixShape, raw) -> "AFixValue":
        """``x.raw := <bits>`` (§2): bit copy into an existing format."""
        return AFixValue(shape, _fit_bits(Value.cast(raw), shape))

    @staticmethod
    def const(shape: AFixShape, value) -> "AFixValue":
        return shape.const(value)

    def _with(self, shape: AFixShape, value: Value) -> "AFixValue":
        return AFixValue(shape, _fit(value, shape))

    # -- arithmetic (§5.1, §5.2, §5.3) -------------------------------------

    def __add__(self, other: "AFixValue") -> "AFixValue":
        other = _coerce(other, self)
        out = add_shape(self._shape, other._shape)
        lraw, rraw = _align_raws(self, other)
        return self._with(out, lraw + rraw)

    def __sub__(self, other: "AFixValue") -> "AFixValue":
        other = _coerce(other, self)
        out = sub_shape(self._shape, other._shape)
        lraw, rraw = _align_raws(self, other)
        return self._with(out, lraw - rraw)

    def __mul__(self, other: "AFixValue") -> "AFixValue":
        other = _coerce(other, self)
        out = mul_shape(self._shape, other._shape)
        # No range alignment: exp_out = l.exp + r.exp, raw multiply, then trim.
        return self._with(out, self._raw * other._raw)

    def __neg__(self) -> "AFixValue":
        out = neg_shape(self._shape)
        return self._with(out, -self._raw)

    # -- comparisons (§5.4) ------------------------------------------------

    def _cmp(self, other, op) -> Value:
        other = _coerce(other, self)
        lraw, rraw = _align_raws(self, other)
        return op(lraw, rraw)

    def __eq__(self, other) -> Value:  # type: ignore[override]
        return self._cmp(other, lambda a, b: a == b)

    def __ne__(self, other) -> Value:  # type: ignore[override]
        return self._cmp(other, lambda a, b: a != b)

    def __lt__(self, other) -> Value:
        return self._cmp(other, lambda a, b: a < b)

    def __le__(self, other) -> Value:
        return self._cmp(other, lambda a, b: a <= b)

    def __gt__(self, other) -> Value:
        return self._cmp(other, lambda a, b: a > b)

    def __ge__(self, other) -> Value:
        return self._cmp(other, lambda a, b: a >= b)

    __hash__ = None  # type: ignore[assignment]

    def min(self, other: "AFixValue") -> "AFixValue":
        """``Num.min`` = ``Mux(this < right, this, right)``."""
        other = _coerce(other, self)
        return afix_mux(self < other, self, other)

    def max(self, other: "AFixValue") -> "AFixValue":
        """``Num.max`` = ``Mux(this < right, right, this)``."""
        other = _coerce(other, self)
        return afix_mux(self < other, other, self)

    # -- rounding (§6.1, §6.2) ---------------------------------------------

    def _round(self, rt: RoundType, exp: int = 0, align: bool = False) -> "AFixValue":
        if align:
            raise NotImplementedError(
                "align=True would insert Spinal's sat(1) step; AFixRounding.saturation is "
                "False everywhere in this design (§1.3), so it is deliberately unsupported"
            )
        drop = exp - self._shape.exp
        if drop < 0:
            # §8.5: _round does not up-convert; it returns the value unchanged.
            return AFixValue(self._shape, self._raw)
        out = AFixShape(
            round_bigint(self._shape.max_raw, drop, rt),
            round_bigint(self._shape.min_raw, drop, rt),
            exp,
        )
        if drop == 0:
            return self._with(out, self._raw)
        unit = 1 << drop
        half = unit >> 1
        if rt is RoundType.FLOOR:
            shifted = self._raw >> drop
        elif rt is RoundType.CEIL:
            shifted = (self._raw + (unit - 1)) >> drop
        elif rt is RoundType.ROUND_HALF_UP:
            shifted = (self._raw + half) >> drop
        elif rt is RoundType.ROUND_HALF_DOWN:
            shifted = (self._raw + (half - 1)) >> drop
        else:  # pragma: no cover - guarded by round_bigint above
            raise ValueError(f"unsupported RoundType {rt!r}")
        return self._with(out, shifted)

    def floor(self, exp: int = 0) -> "AFixValue":
        return self._round(RoundType.FLOOR, exp)

    def ceil(self, exp: int = 0, align: bool = False) -> "AFixValue":
        return self._round(RoundType.CEIL, exp, align)

    def round_half_up(self, exp: int = 0, align: bool = False) -> "AFixValue":
        return self._round(RoundType.ROUND_HALF_UP, exp, align)

    def round_half_down(self, exp: int = 0, align: bool = False) -> "AFixValue":
        return self._round(RoundType.ROUND_HALF_DOWN, exp, align)

    # -- saturation (§6.5) -------------------------------------------------

    def sat(self, sat_max: int, sat_min: int, exp: int | None = None) -> "AFixValue":
        if exp is not None:
            sat_max, sat_min = rescale_sat_bounds(self._shape, sat_max, sat_min, exp)
        out = sat_shape(self._shape, sat_max, sat_min)
        fitted = _fit(self._raw, out)
        # Spinal emits the when/elsewhen chain unconditionally, but a clamp that
        # can never fire folds away; do the folding here so the RTL stays small.
        clamp_hi = self._shape.max_raw > sat_max
        clamp_lo = self._shape.min_raw < sat_min
        if not clamp_hi and not clamp_lo:
            return AFixValue(out, fitted)
        result = fitted
        if clamp_lo:
            result = Mux(self._raw < sat_min, _const_raw(out, out.min_raw), result)
        if clamp_hi:
            result = Mux(self._raw > sat_max, _const_raw(out, out.max_raw), result)
        return AFixValue(out, _fit(result, out))

    # -- conversion (§5.5, §6.4) -------------------------------------------

    def resize(self, new_exp: int) -> "AFixValue":
        """``resize(newExp exp)`` — only ever adds low bits; exact."""
        out = resize_shape(self._shape, new_exp)
        shift = self._shape.exp - new_exp
        return self._with(out, self._raw << shift if shift else self._raw)

    def fix_to(self, target, rounding: RoundType = RoundType.FLOOR) -> "AFixValue":
        """``fixTo(Q)`` = round to ``Q``'s exp, saturate to ``Q``, assign (§6.4).

        The default is ``FLOOR`` because ``AFixRounding``'s default is
        ``RoundType.FLOOR`` and nothing in the core overrides it (§1.3).
        """
        target = _as_shape(target)
        intermediate = self._round(rounding, target.exp).sat(
            target.max_raw, target.min_raw, target.exp
        )
        return AFixValue(target, _convert_exact(intermediate, target))

    def truncated(self, rounding: RoundType = RoundType.FLOOR) -> _Tagged:
        """``.truncated`` — permits precision loss, then **wraps** (§6.6)."""
        return _Tagged(self, saturation=False, overflow=True, rounding=rounding)

    def saturated(self, rounding: RoundType = RoundType.FLOOR) -> _Tagged:
        """``.saturated`` — permits precision loss, then saturates (§6.6)."""
        return _Tagged(self, saturation=True, overflow=False, rounding=rounding)

    def rounded(self, rounding: RoundType = RoundType.FLOOR) -> _Tagged:
        """``.rounded`` — permits precision loss, neither wraps nor saturates."""
        return _Tagged(self, saturation=False, overflow=False, rounding=rounding)

    def as_unsigned(self) -> Value:
        """``.asUInt`` — **clamps** negatives to 0, keeps ``bit_width`` bits (§5.6)."""
        if self._shape.bit_width == 0:
            return Const(0, unsigned(0))
        if self._shape.signed:
            bits = self._raw.as_unsigned()
            return Mux(self._raw[-1], Const(0, unsigned(len(bits))), bits)
        return self._raw

    def as_signed(self) -> Value:
        """``.asSInt`` — reinterpret; an unsigned AFix widens by one bit."""
        if self._shape.signed:
            return self._raw
        return Cat(self._raw, Const(0, 1)).as_signed()

    # -- assignment (§5.5) -------------------------------------------------

    def eq(self, other, *, truncated: bool = False, saturated: bool = False,
           rounding: RoundType = RoundType.FLOOR):
        """``:=``.  Returns the statement list, Amaranth style.

        Rejects a precision-losing assignment that carries no tag, exactly as
        Spinal raises ``PendingError("Cannot assign … precision would be
        lost")``.  That check is what keeps a transliteration honest.
        """
        if isinstance(other, _Tagged):
            if truncated or saturated:
                raise ValueError("pass either a tagged value or the eq() flags, not both")
            truncated, saturated, rounding = other.overflow, other.saturation, other.rounding
            other = other.value
        if not isinstance(other, AFixValue):
            # x := <UInt>/<SInt> is a raw bit copy with no scaling (§5.5).
            return [self._raw.eq(_fit_bits(Value.cast(other), self._shape))]

        target = self._shape
        tagged = truncated or saturated
        src = other
        if target.exp > src._shape.exp and not tagged:
            raise ValueError(
                f"precision would be lost: assigning exp={src._shape.exp} into exp={target.exp} "
                f"without a .truncated()/.saturated() tag"
            )
        if src._shape.exp > target.exp:
            src = src.resize(target.exp)
        elif src._shape.exp < target.exp:
            src = src._round(rounding, target.exp)

        if saturated:
            src = src.sat(target.max_raw, target.min_raw, target.exp)
        elif truncated:
            return [self._raw.eq(_fit(src.raw, target))]  # overflow: wrap

        if not target.covers(src._shape) and not tagged:
            raise ValueError(
                f"target range {target} does not cover source range {src._shape}"
            )
        return [self._raw.eq(_convert_exact(src, target))]

    def __repr__(self) -> str:
        return f"AFixValue({self._shape!r}, {self._raw!r})"


# ---------------------------------------------------------------------------
# free functions
# ---------------------------------------------------------------------------


def _as_shape(target) -> AFixShape:
    if isinstance(target, AFixShape):
        return target
    if isinstance(target, QFormat):
        return AFixShape.from_q(target)
    raise TypeError(f"{target!r} is neither an AFixShape nor a QFormat")


def _coerce(other, like: AFixValue) -> AFixValue:
    if isinstance(other, AFixValue):
        return other
    if isinstance(other, (int, Fraction)):
        return like._shape.const(other)
    raise TypeError(f"cannot use {other!r} as an AFix operand")


def _const_raw(shape: AFixShape, raw: int) -> Value:
    if shape.bit_width == 0:
        return Const(0, unsigned(0))
    return Const(raw, shape.as_shape())


def _convert_exact(src: AFixValue, target: AFixShape) -> Value:
    """Exact (non-rounding) conversion of ``src``'s raw into ``target``'s word."""
    shift = src.afix_shape.exp - target.exp
    if shift < 0:
        raise ValueError(f"cannot convert exp={src.afix_shape.exp} into exp={target.exp} exactly")
    raw = src.raw << shift if shift else src.raw
    return _fit(raw, target)


def afix_mux(sel, if_true: AFixValue, if_false: AFixValue) -> AFixValue:
    """``Mux(cond, a, b)`` on AFix: the output format is the bounding box (§5.4)."""
    out = AFixShape.holding([if_true.afix_shape, if_false.afix_shape])
    return AFixValue(
        out, Mux(sel, _convert_exact(if_true, out), _convert_exact(if_false, out))
    )


# -- shape algebra, usable without elaborating anything (§10.2) -------------


def add_shape(a: AFixShape, b: AFixShape) -> AFixShape:
    exp = min(a.exp, b.exp)
    ka, kb = 1 << (a.exp - exp), 1 << (b.exp - exp)
    return AFixShape(a.max_raw * ka + b.max_raw * kb, a.min_raw * ka + b.min_raw * kb, exp)


def sub_shape(a: AFixShape, b: AFixShape) -> AFixShape:
    exp = min(a.exp, b.exp)
    ka, kb = 1 << (a.exp - exp), 1 << (b.exp - exp)
    return AFixShape(a.max_raw * ka - b.min_raw * kb, a.min_raw * ka - b.max_raw * kb, exp)


def mul_shape(a: AFixShape, b: AFixShape) -> AFixShape:
    corners = (
        a.max_raw * b.max_raw,
        a.max_raw * b.min_raw,
        a.min_raw * b.max_raw,
        a.min_raw * b.min_raw,
    )
    return AFixShape(max(corners), min(corners), a.exp + b.exp)


def neg_shape(a: AFixShape) -> AFixShape:
    return AFixShape(max(-a.min_raw, a.max_raw), min(-a.max_raw, a.min_raw), a.exp)


def rounded_bounds(shape: AFixShape, exp: int, rt: RoundType) -> tuple[int, int]:
    drop = exp - shape.exp
    return round_bigint(shape.max_raw, drop, rt), round_bigint(shape.min_raw, drop, rt)


def round_shape(shape: AFixShape, exp: int, rt: RoundType) -> AFixShape:
    if exp - shape.exp < 0:
        return shape
    hi, lo = rounded_bounds(shape, exp, rt)
    return AFixShape(hi, lo, exp)


def rescale_sat_bounds(shape: AFixShape, sat_max: int, sat_min: int, exp: int) -> tuple[int, int]:
    """Re-express value-level ``sat`` bounds on the source's raw grid (§6.5)."""
    exp_diff = shape.exp - exp
    if exp_diff > 0:
        scale = 1 << exp_diff
        return _trunc_div(sat_max, scale), _trunc_div(sat_min, scale)
    if exp_diff < 0:
        scale = 1 << -exp_diff
        # The "+ (scale - 1)" fudge makes the clamp inclusive of the whole
        # coarse step, and is skipped when the bound is exactly 0.
        return (
            sat_max * scale + (scale - 1 if sat_max != 0 else 0),
            sat_min * scale + (scale - 1 if sat_min != 0 else 0),
        )
    return sat_max, sat_min


def sat_shape(shape: AFixShape, sat_max: int, sat_min: int, exp: int | None = None) -> AFixShape:
    if exp is not None:
        sat_max, sat_min = rescale_sat_bounds(shape, sat_max, sat_min, exp)
    return AFixShape(min(sat_max, shape.max_raw), max(sat_min, shape.min_raw), shape.exp)


def holding_shape(shapes: Iterable[AFixShape]) -> AFixShape:
    return AFixShape.holding(shapes)


def resize_shape(shape: AFixShape, new_exp: int) -> AFixShape:
    if new_exp > shape.exp:
        raise ValueError(f"resize({new_exp}) would lose precision (exp={shape.exp})")
    scale = 1 << (shape.exp - new_exp)
    return AFixShape(shape.max_raw * scale, shape.min_raw * scale, new_exp)


def fix_to_shape(shape: AFixShape, target, rounding: RoundType = RoundType.FLOOR):
    """``fixTo``: returns ``(intermediate, final)`` shapes (§6.4)."""
    target = _as_shape(target)
    rounded = round_shape(shape, target.exp, rounding)
    intermediate = sat_shape(rounded, target.max_raw, target.min_raw, target.exp)
    return intermediate, target
