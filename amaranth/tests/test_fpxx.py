"""L0 unit tests for ``avoodoo.lib.fpxx`` and ``avoodoo.lib.fpxx2afix``.

The lockstep diff (``tests/diff/test_diff_fpxx.py``) is the authority on
equivalence with the Spinal RTL; these tests pin the *semantics* against an
independent Python model, so a change that happens to break both ports the
same way still fails somewhere.

The traps worth pinning, all from ``docs/amaranth/afix-semantics.md`` §9:

* ``is_subnormal()`` is only ``exp == 0``, so it is **true for zero**.
* ``full_mant()`` forces the hidden bit even for a subnormal.
* ``Fpxx2AFix`` has no NaN/Inf handling: both take the saturating branch,
  because ``exp = 255`` makes ``shift`` deeply negative.
* Underflow converts to zero with ``overflow`` **clear**.
"""

from __future__ import annotations

import struct

import pytest
from amaranth.sim import Simulator

from avoodoo.lib.fpxx import FpxxConfig, FpxxLayout, pack, unpack
from avoodoo.lib.fpxx2afix import Fpxx2AFix
from tools.diff.wrappers import FpxxDiffWrap

C32 = FpxxConfig.float32()


def f2b(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_derived():
    assert C32.full_size == 32
    assert C32.bias == 127
    assert C32.ieee_like
    assert FpxxConfig.float64().bias == 1023
    assert FpxxConfig.float16().full_size == 16
    assert not FpxxConfig.float8_e5m2fnuz().ieee_like


def test_pack_unpack_roundtrip():
    for x in (0.0, -0.0, 1.0, -1.5, 3.4e38, 1e-40):
        bits = f2b(x)
        assert pack(C32, *unpack(C32, bits)) == bits


def test_layout_is_ieee_order():
    layout = FpxxLayout(C32)
    assert layout["sign"].offset == 31
    assert layout["exp"].offset == 23
    assert layout["mant"].offset == 0


# ---------------------------------------------------------------------------
# the bundle's predicates, through the diff wrapper (same ports as Spinal)
# ---------------------------------------------------------------------------

CASES = [
    # (name, bits, is_zero, is_nan, is_inf, is_subnormal)
    ("+0",        0x00000000, 1, 0, 0, 1),
    ("-0",        0x80000000, 1, 0, 0, 1),
    ("1.0",       f2b(1.0),   0, 0, 0, 0),
    ("-1.0",      f2b(-1.0),  0, 0, 0, 0),
    ("+inf",      0x7F800000, 0, 0, 1, 0),
    ("-inf",      0xFF800000, 0, 0, 1, 0),
    ("qnan",      0x7FC00000, 0, 1, 0, 0),
    ("snan",      0x7F800001, 0, 1, 0, 0),
    ("subnormal", 0x00000001, 0, 0, 0, 1),
    ("max",       0x7F7FFFFF, 0, 0, 0, 0),
]


@pytest.mark.parametrize("name,bits,zero,nan,inf,sub", CASES, ids=[c[0] for c in CASES])
def test_predicates(name, bits, zero, nan, inf, sub):
    dut = FpxxDiffWrap()
    got = {}

    async def bench(ctx):
        ctx.set(dut.a, bits)
        ctx.set(dut.b, bits)
        for field in ("a_is_zero", "a_is_nan", "a_is_inf", "a_is_subnormal",
                      "a_sign", "a_exp", "a_mant", "a_abs", "a_neg",
                      "a_full_mant", "a_flags_nan", "a_flags_inf", "a_flags_sign",
                      "const_nan", "const_inf", "const_zero"):
            got[field] = ctx.get(getattr(dut, field))

    sim = Simulator(dut)
    sim.add_testbench(bench)
    sim.run()

    sign, exp, mant = unpack(C32, bits)
    assert (got["a_is_zero"], got["a_is_nan"], got["a_is_inf"],
            got["a_is_subnormal"]) == (zero, nan, inf, sub)
    assert (got["a_sign"], got["a_exp"], got["a_mant"]) == (sign, exp, mant)
    assert got["a_abs"] == bits & 0x7FFFFFFF
    assert got["a_neg"] == bits ^ 0x80000000
    # the hidden bit is forced, subnormal or not
    assert got["a_full_mant"] == (1 << 23) | mant
    assert (got["a_flags_nan"], got["a_flags_inf"], got["a_flags_sign"]) == (nan, inf, sign)
    assert got["const_nan"] == 0x7FFFFFFF
    assert got["const_inf"] == 0x7F800000
    assert got["const_zero"] == 0x00000000


# ---------------------------------------------------------------------------
# Fpxx2AFix
# ---------------------------------------------------------------------------


NUMBER_BITS = 51


def _signed(raw: int, width: int = NUMBER_BITS) -> int:
    """The port carries a raw word; ``PciFifo`` reads it as ``raw.asSInt``."""
    return raw - (1 << width) if raw >> (width - 1) else raw


def _convert(bits: int) -> tuple[int, int]:
    """Push one word through ``Fpxx2AFix`` and return ``(number_raw, overflow)``.

    ``number`` is returned already interpreted as two's complement: the AFix
    storage word is a plain slice of the payload, so reading it back out of the
    simulator gives an unsigned integer.
    """
    dut = Fpxx2AFix(20, 30, C32, pipe_stages=1)
    out: dict = {}

    async def bench(ctx):
        ctx.set(dut.result.ready, 1)
        ctx.set(dut.op.valid, 1)
        ctx.set(dut.op.payload.as_value(), bits)
        await ctx.tick()
        await ctx.tick()
        raw = ctx.get(dut.result.payload.number.raw)
        out["raw"] = _signed(raw)
        out["ovfl"] = ctx.get(dut.result.payload.overflow)

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(bench)
    sim.run()
    return out["raw"], out["ovfl"]


@pytest.mark.parametrize(
    "name,value,expect_raw,expect_ovfl",
    [
        ("zero", 0.0, 0, 0),
        ("one", 1.0, 1 << 30, 0),
        ("minus one", -1.0, -(1 << 30), 0),
        ("half", 0.5, 1 << 29, 0),
        ("1.5", 1.5, 3 << 29, 0),
        # 2^-31 is below the 2^-30 resolution: truncates to zero, and the
        # converter does NOT flag underflow.
        ("underflow", 2.0 ** -31, 0, 0),
        # 2^20 needs 21 integer bits: saturates positive.
        ("overflow +", 2.0 ** 20, (1 << 50) - 1, 1),
        ("overflow -", -(2.0 ** 20), -(1 << 50), 1),
    ],
)
def test_fpxx2afix_values(name, value, expect_raw, expect_ovfl):
    raw, ovfl = _convert(f2b(value))
    assert (raw, ovfl) == (expect_raw, expect_ovfl)


def test_fpxx2afix_has_no_nan_or_inf_handling():
    """NaN and Inf go down the saturating path, sign-dependent — §9."""
    assert _convert(0x7F800000) == ((1 << 50) - 1, 1)     # +inf
    assert _convert(0xFF800000) == (-(1 << 50), 1)        # -inf
    assert _convert(0x7FC00000) == ((1 << 50) - 1, 1)     # +qnan
    assert _convert(0xFFC00000) == (-(1 << 50), 1)        # -qnan


def test_fpxx2afix_truncates_toward_minus_infinity():
    """The shift is arithmetic, so negatives truncate away from zero.

    ``2**-8 + 2**-31`` is exactly representable in float32 and sits half a
    step above ``2**-8`` at the converter's ``2**-30`` resolution, so the two
    signs land on opposite sides of the same value.
    """
    x = 2.0 ** -8 + 2.0 ** -31
    assert f2b(x) == f2b(2.0 ** -8) + 1, "the test value must be exact in float32"
    raw_pos, ovfl_pos = _convert(f2b(x))
    raw_neg, ovfl_neg = _convert(f2b(-x))
    assert (raw_pos, ovfl_pos) == (1 << 22, 0)
    assert (raw_neg, ovfl_neg) == (-((1 << 22) + 1), 0)
