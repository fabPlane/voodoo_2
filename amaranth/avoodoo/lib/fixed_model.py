"""Pure-Python reference model of SpinalHDL ``AFix`` semantics.

This module exists **only** to check :mod:`avoodoo.lib.fixed` in the pytest
sweeps.  It never imports Amaranth and never emits RTL: shapes are plain
dataclasses and values are plain Python ints, so it can be evaluated at
elaboration time (or in a test loop) without a simulator.

It is deliberately an *independent* derivation of the rules in
``docs/amaranth/afix-semantics.md`` rather than a refactor of
:mod:`avoodoo.lib.fixed`:

* the bit width here is found by searching for the narrowest two's-complement
  word that covers ``[min_raw, max_raw]``, not by Spinal's
  ``bitLength``-of-``abs``-minus-sign formula (§1) — they are provably the same
  function, and the tests assert that on every format in the doc;
* rounding here is written with Python's floor-division/shift operators, not
  with Spinal's truncate-toward-zero ``BigInt./`` decomposition (§6.1, §8.6).

So a bug in either derivation shows up as a test failure rather than as two
copies of the same mistake.

Spec: ``docs/amaranth/afix-semantics.md`` (SpinalHDL 1.15.0 ``AFix.scala``).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from fractions import Fraction

from avoodoo.config import QFormat

__all__ = [
    "RoundType",
    "ModelShape",
    "ModelValue",
    "round_bigint",
    "rounded_bounds",
    "add_shape",
    "sub_shape",
    "mul_shape",
    "neg_shape",
    "sat_shape",
    "holding_shape",
    "fix_to_shape",
]


class RoundType(enum.Enum):
    """The four ``spinal.core.RoundType`` values the core can reach (§1.2)."""

    FLOOR = "floor"
    CEIL = "ceil"
    ROUND_HALF_UP = "roundup"
    ROUND_HALF_DOWN = "rounddown"


# --------------------------------------------------------------------------
# rounding of a raw integer (§6.1)
# --------------------------------------------------------------------------


def round_bigint(value: int, drop: int, rt: RoundType) -> int:
    """``_roundFixedBigInt``, as the arithmetic identities of §6.1.

    ``drop`` is ``target_exp - source_exp``.  ``drop < 0`` is the identity
    (§8.5: ``_round`` does not up-convert), and so is ``drop == 0``.
    """
    if drop <= 0:
        return value
    unit = 1 << drop
    half = unit >> 1
    if rt is RoundType.FLOOR:
        return value >> drop                       # floor(v / 2**drop)
    if rt is RoundType.CEIL:
        return -((-value) >> drop)                 # ceil(v / 2**drop)
    if rt is RoundType.ROUND_HALF_UP:
        return (value + half) >> drop              # ties -> +inf
    if rt is RoundType.ROUND_HALF_DOWN:
        return -((half - value) >> drop)           # ceil((v - half)/unit), ties -> -inf
    raise ValueError(f"unsupported RoundType {rt!r}")


def _trunc_div(numer: int, denom: int) -> int:
    """Scala ``BigInt./`` — truncates toward zero, unlike Python ``//`` (§8.6)."""
    return int(Fraction(numer, denom))


# --------------------------------------------------------------------------
# shapes
# --------------------------------------------------------------------------


def _covering_width(min_raw: int, max_raw: int) -> tuple[bool, int]:
    """Narrowest two's-complement word covering ``[min_raw, max_raw]``.

    Equivalent to Spinal's ``bitWidth`` derivation (§1) — see module docstring.
    """
    signed = min_raw < 0 or max_raw < 0
    if signed:
        width = 1
        while not (-(1 << (width - 1)) <= min_raw and max_raw <= (1 << (width - 1)) - 1):
            width += 1
        return True, width
    width = 0
    while max_raw > (1 << width) - 1:
        width += 1
    return False, width


@dataclass(frozen=True)
class ModelShape:
    """``(maxRaw, minRaw, exp)`` with the derived fields of §1."""

    max_raw: int
    min_raw: int
    exp: int

    def __post_init__(self) -> None:
        if self.min_raw > self.max_raw:
            raise ValueError(f"empty AFix range: min_raw={self.min_raw} > max_raw={self.max_raw}")

    # -- derived -----------------------------------------------------------

    @property
    def signed(self) -> bool:
        return _covering_width(self.min_raw, self.max_raw)[0]

    @property
    def bit_width(self) -> int:
        return _covering_width(self.min_raw, self.max_raw)[1]

    @property
    def sign_width(self) -> int:
        return 1 if self.signed else 0

    @property
    def frac_width(self) -> int:
        return max(-self.exp, 0)

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
        return self.exp + self.num_width

    @property
    def left_exp(self) -> int:
        return self.exp + self.bit_width

    @property
    def step(self) -> Fraction:
        return Fraction(2) ** self.exp

    @property
    def max_value(self) -> Fraction:
        return self.max_raw * self.step

    @property
    def min_value(self) -> Fraction:
        return self.min_raw * self.step

    # -- constructors ------------------------------------------------------

    @classmethod
    def from_exp(cls, max_exp: int, min_exp: int, signed: bool) -> "ModelShape":
        n = max_exp - min_exp
        if n < 0:
            raise ValueError(f"AFix(max_exp={max_exp}, min_exp={min_exp}) is empty")
        return cls((1 << n) - 1, -(1 << n) if signed else 0, min_exp)

    @classmethod
    def from_q(cls, q: QFormat) -> "ModelShape":
        integer_width = q.width - q.fraction - (1 if q.signed else 0)
        return cls.from_exp(integer_width, -q.fraction, q.signed)

    @classmethod
    def sq(cls, int_bits: int, frac_bits: int) -> "ModelShape":
        return cls.from_exp(int_bits, -frac_bits, True)

    @classmethod
    def uq(cls, int_bits: int, frac_bits: int) -> "ModelShape":
        return cls.from_exp(int_bits, -frac_bits, False)

    @classmethod
    def from_sint(cls, width: int) -> "ModelShape":
        return cls((1 << (width - 1)) - 1, -(1 << (width - 1)), 0)

    # -- helpers -----------------------------------------------------------

    def covers(self, other: "ModelShape") -> bool:
        """True if ``other``'s value range fits in this one's (§5.5 step 4)."""
        return self.min_value <= other.min_value and other.max_value <= self.max_value

    def wrap(self, raw: int) -> int:
        """Reduce ``raw`` into this shape's storage word (two's complement)."""
        width = self.bit_width
        if width == 0:
            return 0
        raw &= (1 << width) - 1
        if self.signed and raw >> (width - 1):
            raw -= 1 << width
        return raw


# --------------------------------------------------------------------------
# shape algebra (§5, §6)
# --------------------------------------------------------------------------


def _align(a: ModelShape, b: ModelShape) -> tuple[int, int, int]:
    """``alignRanges``: scale factors for a and b, plus the common exp (§5)."""
    exp = min(a.exp, b.exp)
    return 1 << (a.exp - exp), 1 << (b.exp - exp), exp


def add_shape(a: ModelShape, b: ModelShape) -> ModelShape:
    ka, kb, exp = _align(a, b)
    return ModelShape(a.max_raw * ka + b.max_raw * kb, a.min_raw * ka + b.min_raw * kb, exp)


def sub_shape(a: ModelShape, b: ModelShape) -> ModelShape:
    ka, kb, exp = _align(a, b)
    return ModelShape(a.max_raw * ka - b.min_raw * kb, a.min_raw * ka - b.max_raw * kb, exp)


def mul_shape(a: ModelShape, b: ModelShape) -> ModelShape:
    corners = [
        a.max_raw * b.max_raw,
        a.max_raw * b.min_raw,
        a.min_raw * b.max_raw,
        a.min_raw * b.min_raw,
    ]
    return ModelShape(max(corners), min(corners), a.exp + b.exp)


def neg_shape(a: ModelShape) -> ModelShape:
    return ModelShape(max(-a.min_raw, a.max_raw), min(-a.max_raw, a.min_raw), a.exp)


def rounded_bounds(shape: ModelShape, exp: int, rt: RoundType) -> tuple[int, int]:
    drop = exp - shape.exp
    return round_bigint(shape.max_raw, drop, rt), round_bigint(shape.min_raw, drop, rt)


def round_shape(shape: ModelShape, exp: int, rt: RoundType) -> ModelShape:
    if exp - shape.exp < 0:
        return shape
    hi, lo = rounded_bounds(shape, exp, rt)
    return ModelShape(hi, lo, exp)


def rescale_sat_bounds(shape: ModelShape, sat_max: int, sat_min: int, exp: int) -> tuple[int, int]:
    """The three-argument ``sat`` rescale of §6.5, fudge and all."""
    exp_diff = shape.exp - exp
    if exp_diff > 0:
        scale = 1 << exp_diff
        return _trunc_div(sat_max, scale), _trunc_div(sat_min, scale)
    if exp_diff < 0:
        scale = 1 << -exp_diff
        return (
            sat_max * scale + (scale - 1 if sat_max != 0 else 0),
            sat_min * scale + (scale - 1 if sat_min != 0 else 0),
        )
    return sat_max, sat_min


def sat_shape(shape: ModelShape, sat_max: int, sat_min: int, exp: int | None = None) -> ModelShape:
    if exp is not None:
        sat_max, sat_min = rescale_sat_bounds(shape, sat_max, sat_min, exp)
    return ModelShape(min(sat_max, shape.max_raw), max(sat_min, shape.min_raw), shape.exp)


def holding_shape(shapes) -> ModelShape:
    shapes = list(shapes)
    exp = min(s.exp for s in shapes)
    return ModelShape(
        max(s.max_raw << max(s.exp - exp, 0) for s in shapes),
        min(s.min_raw << max(s.exp - exp, 0) for s in shapes),
        exp,
    )


def resize_shape(shape: ModelShape, new_exp: int) -> ModelShape:
    if new_exp > shape.exp:
        raise ValueError(f"resize({new_exp}) would lose precision (exp={shape.exp})")
    scale = 1 << (shape.exp - new_exp)
    return ModelShape(shape.max_raw * scale, shape.min_raw * scale, new_exp)


def fix_to_shape(shape: ModelShape, target: ModelShape, rt: RoundType = RoundType.FLOOR):
    """``fixTo``: returns ``(intermediate, final)`` shapes (§6.4)."""
    rounded = round_shape(shape, target.exp, rt)
    intermediate = sat_shape(rounded, target.max_raw, target.min_raw, target.exp)
    return intermediate, target


# --------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelValue:
    """A raw integer plus its :class:`ModelShape`."""

    shape: ModelShape
    raw: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", self.shape.wrap(self.raw))

    @property
    def value(self) -> Fraction:
        return self.raw * self.shape.step

    # -- arithmetic --------------------------------------------------------

    def __add__(self, other: "ModelValue") -> "ModelValue":
        ka, kb, _ = _align(self.shape, other.shape)
        return ModelValue(add_shape(self.shape, other.shape), self.raw * ka + other.raw * kb)

    def __sub__(self, other: "ModelValue") -> "ModelValue":
        ka, kb, _ = _align(self.shape, other.shape)
        return ModelValue(sub_shape(self.shape, other.shape), self.raw * ka - other.raw * kb)

    def __mul__(self, other: "ModelValue") -> "ModelValue":
        return ModelValue(mul_shape(self.shape, other.shape), self.raw * other.raw)

    def __neg__(self) -> "ModelValue":
        return ModelValue(neg_shape(self.shape), -self.raw)

    # -- comparisons (aligned integer compares, §5.4) ----------------------

    def _aligned_pair(self, other: "ModelValue") -> tuple[int, int]:
        ka, kb, _ = _align(self.shape, other.shape)
        return self.raw * ka, other.raw * kb

    def eq_op(self, other: "ModelValue") -> bool:
        a, b = self._aligned_pair(other)
        return a == b

    def ne_op(self, other: "ModelValue") -> bool:
        return not self.eq_op(other)

    def __lt__(self, other: "ModelValue") -> bool:
        a, b = self._aligned_pair(other)
        return a < b

    def __le__(self, other: "ModelValue") -> bool:
        a, b = self._aligned_pair(other)
        return a <= b

    def __gt__(self, other: "ModelValue") -> bool:
        a, b = self._aligned_pair(other)
        return a > b

    def __ge__(self, other: "ModelValue") -> bool:
        a, b = self._aligned_pair(other)
        return a >= b

    def min(self, other: "ModelValue") -> "ModelValue":
        return mux(self < other, self, other)

    def max(self, other: "ModelValue") -> "ModelValue":
        return mux(self < other, other, self)

    # -- rounding ----------------------------------------------------------

    def _round(self, rt: RoundType, exp: int = 0) -> "ModelValue":
        drop = exp - self.shape.exp
        if drop < 0:
            return self
        return ModelValue(round_shape(self.shape, exp, rt), round_bigint(self.raw, drop, rt))

    def floor(self, exp: int = 0) -> "ModelValue":
        return self._round(RoundType.FLOOR, exp)

    def ceil(self, exp: int = 0) -> "ModelValue":
        return self._round(RoundType.CEIL, exp)

    def round_half_up(self, exp: int = 0) -> "ModelValue":
        return self._round(RoundType.ROUND_HALF_UP, exp)

    def round_half_down(self, exp: int = 0) -> "ModelValue":
        return self._round(RoundType.ROUND_HALF_DOWN, exp)

    # -- saturation --------------------------------------------------------

    def sat(self, sat_max: int, sat_min: int, exp: int | None = None) -> "ModelValue":
        if exp is not None:
            sat_max, sat_min = rescale_sat_bounds(self.shape, sat_max, sat_min, exp)
        out = sat_shape(self.shape, sat_max, sat_min)
        if self.raw > sat_max:
            return ModelValue(out, out.max_raw)
        if self.raw < sat_min:
            return ModelValue(out, out.min_raw)
        return ModelValue(out, self.raw)

    # -- conversion --------------------------------------------------------

    def resize(self, new_exp: int) -> "ModelValue":
        out = resize_shape(self.shape, new_exp)
        return ModelValue(out, self.raw << (self.shape.exp - new_exp))

    def assign_to(
        self,
        target: ModelShape,
        *,
        truncated: bool = False,
        saturated: bool = False,
        rounding: RoundType = RoundType.FLOOR,
    ) -> "ModelValue":
        """``:=`` per §5.5.  ``truncated`` is Spinal's overflow/wrap tag."""
        tagged = truncated or saturated
        src = self
        if target.exp > src.shape.exp and not tagged:
            raise ValueError(
                f"precision would be lost assigning exp={src.shape.exp} into exp={target.exp}"
            )
        if src.shape.exp > target.exp:
            src = src.resize(target.exp)
        elif src.shape.exp < target.exp:
            src = src._round(rounding, target.exp)
        if saturated:
            src = src.sat(target.max_raw, target.min_raw, target.exp)
        elif truncated:
            return ModelValue(target, src.raw)  # wrap
        if not target.covers(src.shape) and not tagged:
            raise ValueError(f"target range {target} does not cover source range {src.shape}")
        return ModelValue(target, src.raw)

    def fix_to(self, target: ModelShape, rt: RoundType = RoundType.FLOOR) -> "ModelValue":
        return self._round(rt, target.exp).sat(target.max_raw, target.min_raw, target.exp).assign_to(
            target
        )

    # -- bit views (§5.6) --------------------------------------------------

    def as_unsigned(self) -> int:
        """``.asUInt`` — clamps negatives to 0, keeps ``bit_width`` bits."""
        if self.shape.signed and self.raw < 0:
            return 0
        return self.raw & ((1 << self.shape.bit_width) - 1)

    def as_signed(self) -> int:
        """``.asSInt`` — reinterpret; unsigned widens by one bit."""
        return self.raw

    def bits(self) -> int:
        """``.raw`` as an unsigned bit pattern."""
        width = self.shape.bit_width
        return self.raw & ((1 << width) - 1) if width else 0


def mux(cond: bool, if_true: ModelValue, if_false: ModelValue) -> ModelValue:
    out = holding_shape([if_true.shape, if_false.shape])
    chosen = if_true if cond else if_false
    return ModelValue(out, chosen.raw << (chosen.shape.exp - out.exp))


def const(shape: ModelShape, value) -> ModelValue:
    """``x := <BigDecimal>`` — raw is ``value * 2**-exp``, which must be whole."""
    scaled = Fraction(value) / shape.step
    if scaled.denominator != 1:
        raise ValueError(f"{value} is not representable at exp={shape.exp}")
    raw = int(scaled)
    if not (shape.min_raw <= raw <= shape.max_raw):
        raise ValueError(f"{value} is outside {shape}")
    return ModelValue(shape, raw)
