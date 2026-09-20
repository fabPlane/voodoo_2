"""Parameterised float bundle, 1:1 port of ``Fpxx``.

Scala source: ``ext/SpinalVoodoo/src/voodoo/math/Fpxx.scala`` (259 lines,
SpinalHDL 1.15.0).  Semantics write-up: ``docs/amaranth/afix-semantics.md`` §9.
Plan task: Phase 1.3 of ``docs/amaranth-rewrite-plan.md``.

The design uses exactly one configuration, ``FpxxConfig.float32()``, reached
through ``Fpxx2AFix`` from ``PciFifo`` (register float aliases).  Everything
else in the Scala file is parameterisation that upstream carries for its own
test suite; it is ported anyway because it is cheap and the diff wrapper
``fpga/src/voodoo/diff/DiffWrappers.scala`` exercises the predicate set.

**Bit layout.** The Scala ``Bundle`` declares ``mant``, ``exp``, ``sign`` in
that order, and Spinal packs a Bundle's first-declared field at the LSB, so
``asBits`` is the IEEE order: ``[31] sign, [30:23] exp, [22:0] mant``.
:class:`FpxxLayout` is a ``data.StructLayout`` with the members in the same
order, which gives Amaranth the same packing.

**Denormals do not exist here.** ``full_mant()`` unconditionally sets the
hidden bit, and nothing anywhere renormalises, so a subnormal input is read as
if it were normal.  ``is_subnormal()`` is only ``exp == 0`` — it is true for
zero as well.  This is upstream behaviour and the port reproduces it; see §9.

The simulation-only ``FpxxHost`` / ``toHost`` / ``#=`` / ``init`` members of
the Scala file are *not* hardware and are not ported here: the equivalent
host-side helpers are :func:`pack` and :func:`unpack` below, which the diff
driver and the unit tests use.
"""

from __future__ import annotations

from dataclasses import dataclass

from amaranth.hdl import Cat, Const, Value
from amaranth.lib import data

__all__ = [
    "InfinityEncoding",
    "NoInfinity",
    "IEEEInfinity",
    "NanEncoding",
    "IEEENan",
    "SpecialNan",
    "Bias",
    "IEEEBias",
    "CustomBias",
    "FpxxConfig",
    "FpxxFlagsLayout",
    "FpxxLayout",
    "FpxxView",
    "pack",
    "unpack",
]


# ---------------------------------------------------------------------------
# The three sealed traits (Fpxx.scala:7-16)
# ---------------------------------------------------------------------------


class InfinityEncoding:
    """``sealed trait InfinityEncoding``."""


@dataclass(frozen=True)
class NoInfinity(InfinityEncoding):
    """``case class NoInfinity(replaceWith: BigInt)``."""

    replace_with: int


@dataclass(frozen=True)
class IEEEInfinity(InfinityEncoding):
    """``case class IEEEInfinity()``."""


class NanEncoding:
    """``sealed trait NanEncoding``."""


@dataclass(frozen=True)
class IEEENan(NanEncoding):
    """``case class IEEENan()``."""


@dataclass(frozen=True)
class SpecialNan(NanEncoding):
    """``case class SpecialNan(encoding: BigInt)``."""

    encoding: int


class Bias:
    """``sealed trait Bias``."""


@dataclass(frozen=True)
class IEEEBias(Bias):
    """``case class IEEEBias()``."""


@dataclass(frozen=True)
class CustomBias(Bias):
    """``case class CustomBias(bias: Int)``."""

    bias: int


# ---------------------------------------------------------------------------
# FpxxConfig (Fpxx.scala:18-62)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FpxxConfig:
    """``case class FpxxConfig(exp_size, mant_size, ...)``."""

    exp_size: int
    mant_size: int
    nan_encoding: NanEncoding = IEEENan()
    inf_encoding: InfinityEncoding = IEEEInfinity()
    exp_bias: Bias = IEEEBias()
    signed_zero: bool = True

    # -- object FpxxConfig -------------------------------------------------

    @staticmethod
    def float64() -> "FpxxConfig":
        return FpxxConfig(11, 52)

    @staticmethod
    def float32() -> "FpxxConfig":
        return FpxxConfig(8, 23)

    @staticmethod
    def float16() -> "FpxxConfig":
        return FpxxConfig(5, 10)

    @staticmethod
    def bfloat16() -> "FpxxConfig":
        return FpxxConfig(8, 7)

    @staticmethod
    def float8_e5m2fnuz() -> "FpxxConfig":
        return FpxxConfig(
            5,
            2,
            SpecialNan(0b10000000),
            inf_encoding=NoInfinity(0b10000000),
            exp_bias=CustomBias(16),
            signed_zero=False,
        )

    @staticmethod
    def float8_e4m3fnuz() -> "FpxxConfig":
        return FpxxConfig(
            4,
            3,
            SpecialNan(0b10000000),
            inf_encoding=NoInfinity(0b10000000),
            exp_bias=CustomBias(8),
            signed_zero=False,
        )

    # -- derived -----------------------------------------------------------

    @property
    def full_size(self) -> int:
        return 1 + self.exp_size + self.mant_size

    @property
    def bias(self) -> int:
        if isinstance(self.exp_bias, CustomBias):
            return self.exp_bias.bias
        return (1 << (self.exp_size - 1)) - 1

    @property
    def ieee_like(self) -> bool:
        return (
            isinstance(self.nan_encoding, IEEENan)
            and isinstance(self.inf_encoding, IEEEInfinity)
            and isinstance(self.exp_bias, IEEEBias)
            and self.signed_zero
        )


# ---------------------------------------------------------------------------
# Fpxx.Flags (Fpxx.scala:72-76)
# ---------------------------------------------------------------------------


class FpxxFlagsLayout(data.StructLayout):
    """``case class Flags() extends Bundle { nan; inf; sign }``.

    Declaration order is ``nan``, ``inf``, ``sign``, so packed bit 0 is ``nan``.
    """

    def __init__(self) -> None:
        super().__init__({"nan": 1, "inf": 1, "sign": 1})

    def __repr__(self) -> str:
        return "FpxxFlagsLayout()"


# ---------------------------------------------------------------------------
# Fpxx (Fpxx.scala:78-180)
# ---------------------------------------------------------------------------


class FpxxLayout(data.StructLayout):
    """``case class Fpxx(c: FpxxConfig) extends Bundle``.

    Use it as a shape: ``Signal(FpxxLayout(FpxxConfig.float32()))`` yields an
    :class:`FpxxView` carrying the Scala bundle's methods.
    """

    def __init__(self, c: FpxxConfig) -> None:
        self.c = c
        super().__init__({"mant": c.mant_size, "exp": c.exp_size, "sign": 1})

    def __call__(self, target) -> "FpxxView":
        return FpxxView(self, target)

    def __eq__(self, other) -> bool:
        return isinstance(other, FpxxLayout) and other.c == self.c

    def __hash__(self) -> int:
        return hash((FpxxLayout, self.c))

    def __repr__(self) -> str:
        return f"FpxxLayout({self.c!r})"


class FpxxView(data.View):
    """The methods of the Scala ``Fpxx`` bundle.

    Predicates return plain Amaranth ``Value``s; value-returning helpers
    (:meth:`abs`, :meth:`__invert__`) return another :class:`FpxxView` over a
    freshly built ``Cat``, mirroring Scala's "build a new Fpxx and return it".
    The ``set_*`` methods are *assignment producers*: they return a list of
    statements, so a caller writes ``m.d.comb += value.set_nan()`` where the
    Scala writes ``value.set_nan()``.
    """

    @property
    def c(self) -> FpxxConfig:
        # NB: ``data.View`` falls back to ``__getattr__`` (a "no such field"
        # error) if a property raises AttributeError, so keep this simple.
        return self.shape().c

    # -- predicates --------------------------------------------------------

    def is_zero(self) -> Value:
        """``exp === 0 && mant === 0``."""
        return (self.exp == 0) & (self.mant == 0)

    def is_nan(self) -> Value:
        enc = self.c.nan_encoding
        if isinstance(enc, IEEENan):
            return self.exp.all() & self.mant.any()
        if isinstance(enc, SpecialNan):
            return self.as_value() == Const(enc.encoding, self.c.full_size)
        raise TypeError(enc)

    def is_infinite(self) -> Value:
        enc = self.c.inf_encoding
        if isinstance(enc, IEEEInfinity):
            return self.exp.all() & ~self.mant.any()
        if isinstance(enc, NoInfinity):
            return Const(0, 1)  # Scala: `False`
        raise TypeError(enc)

    def is_subnormal(self) -> Value:
        """``exp === 0`` — note this is also true for zero (§9)."""
        return self.exp == 0

    # -- constants / assignment producers ----------------------------------

    def set_nan(self) -> list:
        """``set_nan()``: IEEE sets exp and mant to all ones, leaving `sign`."""
        enc = self.c.nan_encoding
        if isinstance(enc, IEEENan):
            return [self.exp.eq(~Const(0, self.c.exp_size)),
                    self.mant.eq(~Const(0, self.c.mant_size))]
        if isinstance(enc, SpecialNan):
            return [self.as_value().eq(Const(enc.encoding, self.c.full_size))]
        raise TypeError(enc)

    def set_inf(self) -> list:
        enc = self.c.inf_encoding
        if isinstance(enc, IEEEInfinity):
            return [self.exp.eq(~Const(0, self.c.exp_size)), self.mant.eq(0)]
        if isinstance(enc, NoInfinity):
            return [self.as_value().eq(Const(enc.replace_with, self.c.full_size))]
        raise TypeError(enc)

    def set_zero(self) -> list:
        stmts = []
        if not self.c.signed_zero:
            stmts.append(self.sign.eq(0))
        stmts += [self.exp.eq(0), self.mant.eq(0)]
        return stmts

    # -- value-returning helpers -------------------------------------------

    def abs(self) -> "FpxxView":
        """``abs()``: clear the sign, keep exp/mant."""
        return FpxxLayout(self.c)(Cat(self.mant, self.exp, Const(0, 1)))

    def __invert__(self) -> "FpxxView":
        """``unary_~``: flip the sign bit."""
        return FpxxLayout(self.c)(Cat(self.mant, self.exp, ~self.sign))

    def neg(self) -> "FpxxView":
        """Spelled-out alias of :meth:`__invert__` for readability."""
        return ~self

    def full_mant(self) -> Value:
        """``mant.resize(mant_size + 1) | (1 << mant_size)`` — hidden bit forced."""
        return Cat(self.mant, Const(1, 1))

    def flags(self):
        """``flags()`` -> the ``Fpxx.Flags`` bundle {nan, inf, sign}."""
        return FpxxFlagsLayout()(Cat(self.is_nan(), self.is_infinite(), self.sign))


# ---------------------------------------------------------------------------
# Host-side helpers (the parts of FpxxHost the tests and diff driver need)
# ---------------------------------------------------------------------------


def pack(c: FpxxConfig, sign: int, exp: int, mant: int) -> int:
    """Assemble the packed integer encoding, MSB sign."""
    return ((sign & 1) << (c.exp_size + c.mant_size)) | ((exp & ((1 << c.exp_size) - 1)) << c.mant_size) | (
        mant & ((1 << c.mant_size) - 1)
    )


def unpack(c: FpxxConfig, value: int) -> tuple[int, int, int]:
    """``(sign, exp, mant)`` of a packed encoding."""
    return (
        (value >> (c.exp_size + c.mant_size)) & 1,
        (value >> c.mant_size) & ((1 << c.exp_size) - 1),
        value & ((1 << c.mant_size) - 1),
    )
