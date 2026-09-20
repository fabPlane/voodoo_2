"""Checks on ``avoodoo.lib.fixed`` (the SpinalHDL ``AFix`` port).

Three layers:

1. **Doc tables** — every concrete number in ``docs/amaranth/afix-semantics.md``
   §3, §5.1, §5.2, §6.3, §6.4, §6.5, §6.6 and §8 asserted literally.  Those
   numbers were read off SpinalHDL 1.15.0 and cross-checked against the
   committed ``fpga/rtl/artix/CoreArtix.v``, so this layer is a port-fidelity
   check, not a restatement of the Python.
2. **Shape sweeps** — the shape algebra of :mod:`avoodoo.lib.fixed` against the
   independent reference model in :mod:`avoodoo.lib.fixed_model`, over every
   format pair the core actually builds.
3. **pysim sweeps** — random and directed operands through small
   ``Elaboratable``s, hardware results against the same reference model,
   including negatives, exact halves, saturation at both bounds, and the
   width traps of §8.

The fourth layer lives next door: ``tests/diff/test_diff_afix_ops.py`` runs
``avoodoo/lib/afix_ops.py`` against SpinalHDL's own elaboration of
``fpga/src/voodoo/diff/AfixOps.scala`` under Verilator (282 ports; the full
gate is ``make diff MOD=AfixOps``).  That is the check that these semantics
match *Spinal*; the sweeps here are the check that they match the written
spec and stay fast enough to run on every commit.
"""

import random

import pytest
from amaranth.hdl import Module, Signal, Value, unsigned
from amaranth.lib import data
from amaranth.sim import Simulator

from avoodoo.config import SQ, UQ, Config, QFormat
from avoodoo.lib import fixed_model as model
from avoodoo.lib.fixed import (
    AFixShape,
    AFixValue,
    RoundType,
    add_shape,
    afix_mux,
    fix_to_shape,
    holding_shape,
    mul_shape,
    neg_shape,
    resize_shape,
    round_bigint,
    round_shape,
    rounded_bounds,
    sat_shape,
    sub_shape,
)

CFG = Config.voodoo1()

#: The modes with defined semantics; ROUND_HALF_TO_EVEN and SCRAP exist only so
#: a ``fixTo`` naming them elaborates at drop == 0 (see the dedicated test).
ROUND_TYPES = [
    RoundType.FLOOR,
    RoundType.CEIL,
    RoundType.ROUND_HALF_UP,
    RoundType.ROUND_HALF_DOWN,
    RoundType.FLOOR_TO_ZERO,
    RoundType.CEIL_TO_INF,
]

# --- the formats the core actually builds (doc §3) -------------------------

NAMED_Q = {
    "vertexFormat": SQ(16, 4),
    "coefficientFormat": SQ(34, 8),
    "vColorFormat": SQ(24, 12),
    "vDepthFormat": SQ(32, 12),
    "texCoordsFormat": SQ(32, 18),
    "texCoordsHiFormat": SQ(46, 30),
    "wFormat": SQ(32, 30),
    "texCoordsAccumFormat": SQ(48, 18),
    "wAccumFormat": SQ(48, 30),
    "colorMulFormat": QFormat(19, 8, True),
    "UQ(8,0)": UQ(8, 0),
    "UQ(0,8)": UQ(0, 8),
}

ADHOC = {
    "AFix.SQ(10b,0b)": AFixShape.sq(10, 0),
    "AFix.UQ(0b,8b)": AFixShape.uq(0, 8),
    "AFix.UQ(1b,8b)": AFixShape.uq(1, 8),
    "AFix.SQ(3b,4b)": AFixShape.sq(3, 4),
    "AFix.SQ(20b,30b)": AFixShape.sq(20, 30),
    "0.SQ(1b,0b)": AFixShape(1, -2, 0),
    "AFix(SInt(9b))": AFixShape.from_sint_width(9),
}

ALL_SHAPES = {name: AFixShape.from_q(q) for name, q in NAMED_Q.items()} | ADHOC


def as_tuple(shape):
    return (shape.max_raw, shape.min_raw, shape.exp, shape.bit_width, shape.signed)


def to_model(shape):
    return model.ModelShape(shape.max_raw, shape.min_raw, shape.exp)


# ===========================================================================
# 1. doc tables
# ===========================================================================


def test_named_formats_match_doc_section_3():
    expect = {
        "vertexFormat": (32767, -32768, -4, 16),
        "coefficientFormat": (8589934591, -8589934592, -8, 34),
        "vColorFormat": (8388607, -8388608, -12, 24),
        "vDepthFormat": (2147483647, -2147483648, -12, 32),
        "texCoordsFormat": (2147483647, -2147483648, -18, 32),
        "texCoordsHiFormat": (35184372088831, -35184372088832, -30, 46),
        "wFormat": (2147483647, -2147483648, -30, 32),
        "texCoordsAccumFormat": (140737488355327, -140737488355328, -18, 48),
        "wAccumFormat": (140737488355327, -140737488355328, -30, 48),
        "colorMulFormat": (262143, -262144, -8, 19),
    }
    for name, (hi, lo, exp, width) in expect.items():
        shape = AFixShape.from_q(NAMED_Q[name])
        assert as_tuple(shape) == (hi, lo, exp, width, True), name


def test_config_fields_produce_the_named_formats():
    """The Config.voodoo1() QFormats feed AFixShape.from_q directly."""
    assert AFixShape.from_q(CFG.vertex_format) == AFixShape(32767, -32768, -4)
    assert AFixShape.from_q(CFG.coefficient_format) == AFixShape(8589934591, -8589934592, -8)
    assert AFixShape.from_q(CFG.tex_coords_hi_format).bit_width == 46
    assert AFixShape.from_q(CFG.w_accum_format).bit_width == 48


def test_adhoc_formats_match_doc_section_3():
    assert as_tuple(ADHOC["AFix.SQ(10b,0b)"]) == (1023, -1024, 0, 11, True)
    assert as_tuple(ADHOC["AFix.UQ(0b,8b)"]) == (255, 0, -8, 8, False)
    assert as_tuple(ADHOC["AFix.UQ(1b,8b)"]) == (511, 0, -8, 9, False)
    assert as_tuple(ADHOC["AFix.SQ(3b,4b)"]) == (127, -128, -4, 8, True)
    assert as_tuple(ADHOC["0.SQ(1b,0b)"]) == (1, -2, 0, 2, True)
    assert as_tuple(ADHOC["AFix(SInt(9b))"]) == (255, -256, 0, 9, True)
    fpxx_out = ADHOC["AFix.SQ(20b,30b)"]
    assert as_tuple(fpxx_out) == (2 ** 50 - 1, -(2 ** 50), -30, 51, True)


def test_uq_bitcount_factory_is_not_the_qformat_one():
    """``AFix.UQ(0 bits, 8 bits)`` vs ``spinal.core.UQ(0, 8)`` (§1.1, §8.1)."""
    assert AFixShape.uq(0, 8).bit_width == 8
    assert AFixShape.from_q(UQ(0, 8)).bit_width == 0
    assert AFixShape.from_q(UQ(0, 8)) == AFixShape(0, 0, -8)


def test_derived_fields_of_section_1():
    v = AFixShape.from_q(SQ(16, 4))
    assert (v.sign_width, v.frac_width, v.whole_width) == (1, 4, 11)
    assert (v.int_width, v.num_width) == (12, 15)
    assert (v.max_exp, v.left_exp) == (11, 12)
    assert v.max_value == 2047.9375 and v.min_value == -2048
    u = AFixShape.uq(1, 8)
    assert (u.sign_width, u.frac_width, u.whole_width, u.num_width) == (0, 8, 1, 9)


def test_add_sub_examples_of_section_5_1():
    vertex = AFixShape.from_q(SQ(16, 4))
    coeff = AFixShape.from_q(SQ(34, 8))
    hi = AFixShape.from_q(SQ(46, 30))
    color_mul = AFixShape.from_q(QFormat(19, 8, True))
    half_pixel = AFixShape.sq(3, 4)

    assert as_tuple(add_shape(vertex, vertex)) == (65534, -65536, -4, 17, True)
    assert as_tuple(add_shape(coeff, coeff)) == (17179869182, -17179869184, -8, 35, True)
    assert as_tuple(add_shape(hi, hi)) == (70368744177662, -70368744177664, -30, 47, True)
    assert as_tuple(sub_shape(add_shape(coeff, coeff), coeff)) == (
        25769803774, -25769803775, -8, 36, True)
    assert as_tuple(sub_shape(vertex, vertex)) == (65535, -65535, -4, 17, True)
    assert as_tuple(add_shape(vertex, half_pixel)) == (32894, -32896, -4, 17, True)
    assert as_tuple(add_shape(color_mul, color_mul)) == (524286, -524288, -8, 20, True)


def test_mul_examples_of_section_5_2():
    vertex = AFixShape.from_q(SQ(16, 4))
    coeff = AFixShape.from_q(SQ(34, 8))
    sub = AFixShape.sq(10, 0)
    factor = AFixShape.uq(1, 8)

    assert as_tuple(mul_shape(vertex, vertex)) == (1073741824, -1073709056, -8, 32, True)
    assert as_tuple(mul_shape(coeff, vertex)) == (
        281474976710656, -281474976677888, -12, 50, True)
    assert as_tuple(mul_shape(sub, factor)) == (522753, -523264, -8, 20, True)


def test_neg_and_holding_of_sections_5_3_and_5_4():
    sym = AFixShape(65535, -65535, -4)
    assert neg_shape(sym) == sym and sym.bit_width == 17
    assert as_tuple(neg_shape(AFixShape.uq(1, 8))) == (511, -511, -8, 10, True)
    vertex = AFixShape.from_q(SQ(16, 4))
    # Mux over two identical formats is that format (TriangleSetup:246-248).
    assert holding_shape([sym, neg_shape(sym)]) == sym
    assert holding_shape([vertex, vertex]) == vertex
    # bounding box at the finest exp
    assert as_tuple(holding_shape([AFixShape(3, -1, 0), AFixShape(5, -9, -2)])) == (
        12, -9, -2, 5, True)


def test_rounding_table_of_section_6_3():
    """The worked SQ(6,2) -> exp 0 table, both signs and the halves."""
    src = AFixShape(31, -32, -2)  # 6 raw bits, exp -2
    rows = {
        7: (1, 2, 2, 2),
        6: (1, 2, 2, 1),
        5: (1, 2, 1, 1),
        4: (1, 1, 1, 1),
        2: (0, 1, 1, 0),
        1: (0, 1, 0, 0),
        0: (0, 0, 0, 0),
        -1: (-1, 0, 0, 0),
        -2: (-1, 0, 0, -1),
        -3: (-1, 0, -1, -1),
        -5: (-2, -1, -1, -1),
        -6: (-2, -1, -1, -2),
        -7: (-2, -1, -2, -2),
        -8: (-2, -2, -2, -2),
    }
    for raw, (flr, cel, up, down) in rows.items():
        drop = 0 - src.exp
        assert round_bigint(raw, drop, RoundType.FLOOR) == flr, raw
        assert round_bigint(raw, drop, RoundType.CEIL) == cel, raw
        assert round_bigint(raw, drop, RoundType.ROUND_HALF_UP) == up, raw
        assert round_bigint(raw, drop, RoundType.ROUND_HALF_DOWN) == down, raw


def test_rounded_bound_widths_of_section_6_3():
    """``ceil(0)``/``roundHalfDown(0)`` of SQ(16,4) are 13 bits, not 12 (§8.3)."""
    vertex = AFixShape.from_q(SQ(16, 4))
    assert rounded_bounds(vertex, 0, RoundType.FLOOR) == (2047, -2048)
    assert rounded_bounds(vertex, 0, RoundType.CEIL) == (2048, -2048)
    assert rounded_bounds(vertex, 0, RoundType.ROUND_HALF_DOWN) == (2048, -2048)
    assert round_shape(vertex, 0, RoundType.FLOOR).bit_width == 12
    assert round_shape(vertex, 0, RoundType.CEIL).bit_width == 13
    assert round_shape(vertex, 0, RoundType.ROUND_HALF_DOWN).bit_width == 13


def test_round_bigint_matches_plain_arithmetic():
    """§6.1/§8.6: the truncate-toward-zero decomposition is exact floor/ceil/half.

    ``fixed.round_bigint`` is written around Scala's ``BigInt./``; the model's
    is written around Python's flooring shift.  Exhaustive agreement over the
    range the doc verified.
    """
    for drop in range(1, 7):
        for value in range(-300, 301):
            for rt in ROUND_TYPES:
                assert round_bigint(value, drop, rt) == model.round_bigint(value, drop, rt)


def test_trunc_toward_zero_is_visible_in_sat_rescale():
    """§8.6: the sat(…, exp) rescale divides toward zero, not toward -inf."""
    from avoodoo.lib.fixed import rescale_sat_bounds

    src = AFixShape(1000, -1000, 0)  # exp 0, so exp_diff = 4 against exp -4
    assert rescale_sat_bounds(src, 255, -255, -4) == (15, -15)  # not (15, -16)
    assert -255 // 16 == -16  # what Python's // would have produced


def test_fix_to_table_of_section_6_4():
    cases = [
        (AFixShape(65534, -65536, -4), SQ(16, 4),
         (32767, -32768, -4, 16, True)),
        (AFixShape(17179869182, -17179869184, -8), SQ(34, 8),
         (8589934591, -8589934592, -8, 34, True)),
        (AFixShape(522753, -523264, -8), QFormat(19, 8, True),
         (262143, -262144, -8, 19, True)),
        (AFixShape.from_sint_width(9), QFormat(19, 8, True),
         (255, -256, 0, 9, True)),               # drop = -8 < 0 => unchanged
        (AFixShape.from_q(SQ(46, 30)), SQ(48, 18),
         (8589934591, -8589934592, -18, 34, True)),
        (AFixShape(2047, -2048, 0), SQ(16, 4),
         (2047, -2048, 0, 12, True)),            # drop = -4 => unchanged
    ]
    for src, target_q, expect_intermediate in cases:
        intermediate, final = fix_to_shape(src, target_q)
        assert as_tuple(intermediate) == expect_intermediate, (src, target_q)
        assert final == AFixShape.from_q(target_q)


def test_sat_table_of_section_6_5():
    cases = [
        (AFixShape.from_q(SQ(24, 12)), 255, 0, 0, (1048575, 0, -12, 20, False)),
        (AFixShape.from_q(SQ(32, 12)), 255, 0, 12, (2147483647, 0, -12, 31, False)),
        (AFixShape.from_q(SQ(46, 30)), 255, 0, 0, (274877906943, 0, -30, 38, False)),
        (AFixShape.sq(10, 0), 255, 0, None, (255, 0, 0, 8, False)),
        (AFixShape.from_q(QFormat(19, 8, True)), 255, 0, 0, (65535, 0, -8, 16, False)),
    ]
    for src, hi, lo, exp, expect in cases:
        assert as_tuple(sat_shape(src, hi, lo, exp)) == expect, (src, exp)


def test_sat_rescale_fudge_and_zero_exception():
    """§6.5: ``+ (2**-expDiff - 1)`` on the coarser-target branch, skipped at 0."""
    from avoodoo.lib.fixed import rescale_sat_bounds

    src = AFixShape.from_q(SQ(24, 12))  # exp -12
    assert rescale_sat_bounds(src, 255, 0, 0) == (255 * 4096 + 4095, 0)
    assert rescale_sat_bounds(src, 255, -255, 0) == (255 * 4096 + 4095, -255 * 4096 + 4095)
    assert rescale_sat_bounds(src, 0, 0, 0) == (0, 0)


def test_sat_makes_values_unsigned_section_8_4():
    out = sat_shape(AFixShape.from_q(SQ(24, 12)), 255, 0, 0)
    assert out.signed is False and out.min_raw == 0
    assert out.bit_width == 20  # sign bit gone


def test_truncated_bounds_of_section_6_6():
    src = AFixShape(563087392374768, -563087392309248, -12)
    assert src.bit_width == 51
    rounded = round_shape(src, -8, RoundType.FLOOR)
    assert as_tuple(rounded) == (35192962023423, -35192962019328, -8, 47, True)


def test_resize_only_adds_low_bits():
    src = AFixShape(2047, -2048, 0)
    assert as_tuple(resize_shape(src, -4)) == (32752, -32768, -4, 16, True)
    with pytest.raises(ValueError):
        resize_shape(src, 4)


# ===========================================================================
# 2. shape sweeps against the reference model
# ===========================================================================


def test_shape_derivation_matches_model_for_every_core_format():
    for name, shape in ALL_SHAPES.items():
        ref = to_model(shape)
        for field in ("signed", "bit_width", "sign_width", "frac_width", "whole_width",
                      "int_width", "num_width", "max_exp", "left_exp",
                      "max_value", "min_value", "step"):
            assert getattr(shape, field) == getattr(ref, field), (name, field)


def test_binary_shape_algebra_matches_model_for_every_pair():
    names = sorted(ALL_SHAPES)
    for a_name in names:
        for b_name in names:
            a, b = ALL_SHAPES[a_name], ALL_SHAPES[b_name]
            ma, mb = to_model(a), to_model(b)
            for op, ref_op in ((add_shape, model.add_shape),
                               (sub_shape, model.sub_shape),
                               (mul_shape, model.mul_shape)):
                got, want = op(a, b), ref_op(ma, mb)
                assert as_tuple(got) == as_tuple(want), (a_name, b_name, op.__name__)
            assert as_tuple(holding_shape([a, b])) == as_tuple(
                model.holding_shape([ma, mb])), (a_name, b_name)


def test_unary_shape_algebra_matches_model_for_every_format():
    for name, shape in ALL_SHAPES.items():
        ref = to_model(shape)
        assert as_tuple(neg_shape(shape)) == as_tuple(model.neg_shape(ref)), name
        for exp in (-30, -12, -8, -4, 0, 4):
            for rt in ROUND_TYPES:
                assert as_tuple(round_shape(shape, exp, rt)) == as_tuple(
                    model.round_shape(ref, exp, rt)), (name, exp, rt)
            for hi, lo in ((255, 0), (255, -255), (0, 0), (1023, -1024)):
                assert as_tuple(sat_shape(shape, hi, lo, exp)) == as_tuple(
                    model.sat_shape(ref, hi, lo, exp)), (name, exp, hi, lo)


def test_fix_to_shape_matches_model_for_every_pair():
    for src_name, src in ALL_SHAPES.items():
        for tgt_name, tgt in ALL_SHAPES.items():
            got_i, got_f = fix_to_shape(src, tgt)
            want_i, want_f = model.fix_to_shape(to_model(src), to_model(tgt))
            assert as_tuple(got_i) == as_tuple(want_i), (src_name, tgt_name)
            assert as_tuple(got_f) == as_tuple(want_f), (src_name, tgt_name)


# ===========================================================================
# 3. pysim
# ===========================================================================


def build(in_shapes, make_exprs):
    """Wire one comb Elaboratable exposing every expression as an output."""
    sigs = {n: Signal(s.as_shape() if isinstance(s, AFixShape) else s, name=n)
            for n, s in in_shapes.items()}
    ins = {n: (AFixValue(s, sigs[n]) if isinstance(s, AFixShape) else sigs[n])
           for n, s in in_shapes.items()}
    exprs = make_exprs(ins)
    m = Module()
    outs = {}
    for name, expr in exprs.items():
        value = expr.raw if isinstance(expr, AFixValue) else Value.cast(expr)
        out = Signal(value.shape(), name=f"o_{name}")
        m.d.comb += out.eq(value)
        outs[name] = out
    return m, sigs, outs, exprs


def run(m, sigs, outs, vectors, check):
    sim = Simulator(m)

    async def testbench(ctx):
        for vector in vectors:
            for name, value in vector.items():
                ctx.set(sigs[name], value)
            check(vector, {name: ctx.get(out) for name, out in outs.items()})

    sim.add_testbench(testbench)
    sim.run()


def raw_samples(shape, rng, count):
    """Corners, small values, and ``count`` random raws inside the bounds."""
    lo, hi = shape.min_raw, shape.max_raw
    picks = {lo, hi, 0}
    for delta in (1, -1, 2, -2):
        for anchor in (lo, hi, 0):
            candidate = anchor + delta
            if lo <= candidate <= hi:
                picks.add(candidate)
    while len(picks) < count:
        picks.add(rng.randint(lo, hi))
    return sorted(picks)


def pair_samples(a, b, rng, count):
    a_picks, b_picks = raw_samples(a, rng, 12), raw_samples(b, rng, 12)
    vectors = [{"a": x, "b": y} for x in a_picks[:6] for y in b_picks[:6]]
    while len(vectors) < count:
        vectors.append({"a": rng.randint(a.min_raw, a.max_raw),
                        "b": rng.randint(b.min_raw, b.max_raw)})
    return vectors


# format pairs exercised in hardware: every distinct exp/signedness combination
# the core produces (§5.1, §5.2)
SIM_PAIRS = [
    ("vertex x vertex", AFixShape.from_q(SQ(16, 4)), AFixShape.from_q(SQ(16, 4))),
    ("coeff x coeff", AFixShape.from_q(SQ(34, 8)), AFixShape.from_q(SQ(34, 8))),
    ("coeff x vertex", AFixShape.from_q(SQ(34, 8)), AFixShape.from_q(SQ(16, 4))),
    ("sub x factor", AFixShape.sq(10, 0), AFixShape.uq(1, 8)),
    ("sint9 x colorMul", AFixShape.from_sint_width(9),
     AFixShape.from_q(QFormat(19, 8, True))),
    ("texHi x texHi", AFixShape.from_q(SQ(46, 30)), AFixShape.from_q(SQ(46, 30))),
    ("vertex x halfPixel", AFixShape.from_q(SQ(16, 4)), AFixShape.sq(3, 4)),
    ("uq08 x vertex", AFixShape.uq(0, 8), AFixShape.from_q(SQ(16, 4))),
]


@pytest.mark.parametrize("name,a_shape,b_shape", SIM_PAIRS,
                         ids=[c[0] for c in SIM_PAIRS])
def test_sim_binary_ops(name, a_shape, b_shape):
    rng = random.Random(0xA71F ^ hash(name) % 65536)

    def exprs(ins):
        a, b = ins["a"], ins["b"]
        return {
            "add": a + b,
            "sub": a - b,
            "mul": a * b,
            "neg_a": -a,
            "ge": a >= b,
            "gt": a > b,
            "lt": a < b,
            "le": a <= b,
            "eq": a == b,
            "ne": a != b,
            "min": a.min(b),
            "max": a.max(b),
        }

    m, sigs, outs, built = build({"a": a_shape, "b": b_shape}, exprs)

    def check(vector, got):
        ma = model.ModelValue(to_model(a_shape), vector["a"])
        mb = model.ModelValue(to_model(b_shape), vector["b"])
        want = {
            "add": (ma + mb).raw,
            "sub": (ma - mb).raw,
            "mul": (ma * mb).raw,
            "neg_a": (-ma).raw,
            "ge": int(ma >= mb),
            "gt": int(ma > mb),
            "lt": int(ma < mb),
            "le": int(ma <= mb),
            "eq": int(ma.eq_op(mb)),
            "ne": int(ma.ne_op(mb)),
            "min": ma.min(mb).raw,
            "max": ma.max(mb).raw,
        }
        for key, expected in want.items():
            assert got[key] == expected, (name, key, vector, got[key], expected)

    # result shapes must agree with the model too
    ma, mb = to_model(a_shape), to_model(b_shape)
    assert as_tuple(built["add"].afix_shape) == as_tuple(model.add_shape(ma, mb))
    assert as_tuple(built["mul"].afix_shape) == as_tuple(model.mul_shape(ma, mb))
    assert as_tuple(built["min"].afix_shape) == as_tuple(model.holding_shape([ma, mb]))

    run(m, sigs, outs, pair_samples(a_shape, b_shape, rng, 240), check)


ROUND_CASES = [
    ("vertex->0", AFixShape.from_q(SQ(16, 4)), 0),
    ("SQ(6,2)->0", AFixShape(31, -32, -2), 0),
    ("texHi->-18", AFixShape.from_q(SQ(46, 30)), -18),
    ("coeff->-4", AFixShape.from_q(SQ(34, 8)), -4),
    ("factor->0", AFixShape.uq(1, 8), 0),
    ("vertex->-8 (drop<0)", AFixShape.from_q(SQ(16, 4)), -8),
    ("vertex->-4 (drop=0)", AFixShape.from_q(SQ(16, 4)), -4),
]


@pytest.mark.parametrize("name,shape,target_exp", ROUND_CASES,
                         ids=[c[0] for c in ROUND_CASES])
def test_sim_rounding(name, shape, target_exp):
    rng = random.Random(0x0F10 ^ len(name))
    drop = target_exp - shape.exp

    def exprs(ins):
        a = ins["a"]
        return {
            "floor": a.floor(target_exp),
            "ceil": a.ceil(target_exp),
            "half_up": a.round_half_up(target_exp),
            "half_down": a.round_half_down(target_exp),
        }

    m, sigs, outs, built = build({"a": shape}, exprs)
    ref = to_model(shape)

    for key, rt in (("floor", RoundType.FLOOR), ("ceil", RoundType.CEIL),
                    ("half_up", RoundType.ROUND_HALF_UP),
                    ("half_down", RoundType.ROUND_HALF_DOWN)):
        assert as_tuple(built[key].afix_shape) == as_tuple(
            model.round_shape(ref, target_exp, rt)), (name, key)

    vectors = [{"a": v} for v in raw_samples(shape, rng, 60)]
    if drop > 0:  # exact halves and just-either-side of them, both signs
        unit, half = 1 << drop, 1 << (drop - 1)
        for step in range(-4, 5):
            for offset in (-1, 0, 1):
                candidate = step * unit + half + offset
                if shape.min_raw <= candidate <= shape.max_raw:
                    vectors.append({"a": candidate})
    while len(vectors) < 200:
        vectors.append({"a": rng.randint(shape.min_raw, shape.max_raw)})

    def check(vector, got):
        mv = model.ModelValue(ref, vector["a"])
        want = {
            "floor": mv.floor(target_exp).raw,
            "ceil": mv.ceil(target_exp).raw,
            "half_up": mv.round_half_up(target_exp).raw,
            "half_down": mv.round_half_down(target_exp).raw,
        }
        for key, expected in want.items():
            assert got[key] == expected, (name, key, vector, got[key], expected)

    run(m, sigs, outs, vectors, check)


SAT_CASES = [
    ("redGrad", AFixShape.from_q(SQ(24, 12)), 255, 0, 0),
    ("depthGrad", AFixShape.from_q(SQ(32, 12)), 255, 0, 12),
    ("alphaGradHi", AFixShape.from_q(SQ(46, 30)), 255, 0, 0),
    ("clampAFixToFactor", AFixShape.sq(10, 0), 255, 0, None),
    ("clampAFixToU8", AFixShape.from_q(QFormat(19, 8, True)), 255, 0, 0),
    ("symmetric", AFixShape.from_q(SQ(16, 4)), 1000, -1000, None),
    ("no-clamp", AFixShape.sq(3, 4), 1 << 20, -(1 << 20), None),
]


@pytest.mark.parametrize("name,shape,hi,lo,exp", SAT_CASES, ids=[c[0] for c in SAT_CASES])
def test_sim_sat(name, shape, hi, lo, exp):
    rng = random.Random(0x5A70 ^ len(name))

    def exprs(ins):
        return {"sat": ins["a"].sat(hi, lo, exp)}

    m, sigs, outs, built = build({"a": shape}, exprs)
    ref = to_model(shape)
    assert as_tuple(built["sat"].afix_shape) == as_tuple(model.sat_shape(ref, hi, lo, exp))

    # make sure both clamps actually fire
    from avoodoo.lib.fixed import rescale_sat_bounds
    eff_hi, eff_lo = (rescale_sat_bounds(shape, hi, lo, exp) if exp is not None else (hi, lo))
    vectors = [{"a": v} for v in raw_samples(shape, rng, 60)]
    for boundary in (eff_hi, eff_lo):
        for offset in (-2, -1, 0, 1, 2):
            candidate = boundary + offset
            if shape.min_raw <= candidate <= shape.max_raw:
                vectors.append({"a": candidate})
    while len(vectors) < 200:
        vectors.append({"a": rng.randint(shape.min_raw, shape.max_raw)})

    fired = {"hi": 0, "lo": 0, "pass": 0}

    def check(vector, got):
        expected = model.ModelValue(ref, vector["a"]).sat(hi, lo, exp).raw
        assert got["sat"] == expected, (name, vector, got["sat"], expected)
        fired["hi" if vector["a"] > eff_hi else "lo" if vector["a"] < eff_lo else "pass"] += 1

    run(m, sigs, outs, vectors, check)

    # the stimulus has to actually reach both clamps, where they exist at all
    assert fired["pass"], name
    assert fired["hi"] or shape.max_raw <= eff_hi, name
    assert fired["lo"] or shape.min_raw >= eff_lo, name


FIX_TO_CASES = [
    ("add17 -> vertex", AFixShape(65534, -65536, -4), SQ(16, 4)),
    ("add35 -> coeff", AFixShape(17179869182, -17179869184, -8), SQ(34, 8)),
    ("mul20 -> colorMul", AFixShape(522753, -523264, -8), QFormat(19, 8, True)),
    ("sint9 -> colorMul", AFixShape.from_sint_width(9), QFormat(19, 8, True)),
    ("texHi -> accum", AFixShape.from_q(SQ(46, 30)), SQ(48, 18)),
    ("floor12 -> vertex", AFixShape(2047, -2048, 0), SQ(16, 4)),
    ("sub11 -> UQ(8,0)", AFixShape.sq(10, 0), UQ(8, 0)),
    ("sub11 -> UQ(0,8) [§8.1]", AFixShape.sq(10, 0), UQ(0, 8)),
]


@pytest.mark.parametrize("name,src,target", FIX_TO_CASES, ids=[c[0] for c in FIX_TO_CASES])
def test_sim_fix_to(name, src, target):
    rng = random.Random(0xF1C0 ^ len(name))
    target_shape = AFixShape.from_q(target)

    def exprs(ins):
        return {"fix": ins["a"].fix_to(target)}

    m, sigs, outs, built = build({"a": src}, exprs)
    assert built["fix"].afix_shape == target_shape

    ref, ref_target = to_model(src), to_model(target_shape)
    vectors = [{"a": v} for v in raw_samples(src, rng, 40)]
    while len(vectors) < 200:
        vectors.append({"a": rng.randint(src.min_raw, src.max_raw)})

    def check(vector, got):
        expected = model.ModelValue(ref, vector["a"]).fix_to(ref_target).raw
        assert got["fix"] == expected, (name, vector, got["fix"], expected)

    run(m, sigs, outs, vectors, check)


def test_sim_conversions_and_mux():
    """asUInt/asSInt (§5.6, §8.2), Mux over mixed formats, resize."""
    rng = random.Random(0xC0FFEE)
    vertex = AFixShape.from_q(SQ(16, 4))
    coarse = AFixShape(1, -2, 0)  # 0.SQ(1 bits, 0 bits)

    def exprs(ins):
        a, b, sel = ins["a"], ins["b"], ins["sel"]
        floored = a.floor(0)
        return {
            "as_unsigned": floored.as_unsigned(),
            "as_signed": floored.as_signed(),
            "u_as_signed": AFixValue(AFixShape.uq(1, 8), ins["u"]).as_signed(),
            "u_as_unsigned": AFixValue(AFixShape.uq(1, 8), ins["u"]).as_unsigned(),
            "mux": afix_mux(sel, -a, a),
            "mux_mixed": afix_mux(sel, a, b),
            "resize": a.resize(-8),
        }

    m, sigs, outs, built = build(
        {"a": vertex, "b": coarse, "u": AFixShape.uq(1, 8), "sel": unsigned(1)}, exprs)
    assert built["mux"].afix_shape == AFixShape(32768, -32768, -4)
    assert built["mux_mixed"].afix_shape == AFixShape(32767, -32768, -4)
    assert built["resize"].afix_shape == AFixShape(32767 * 16, -32768 * 16, -8)

    ref, ref_u, ref_b = to_model(vertex), model.ModelShape(511, 0, -8), to_model(coarse)
    vectors = []
    for value in raw_samples(vertex, rng, 40):
        vectors.append({"a": value, "b": rng.randint(-2, 1),
                        "u": rng.randint(0, 511), "sel": rng.randint(0, 1)})
    while len(vectors) < 200:
        vectors.append({"a": rng.randint(vertex.min_raw, vertex.max_raw),
                        "b": rng.randint(-2, 1), "u": rng.randint(0, 511),
                        "sel": rng.randint(0, 1)})

    def check(vector, got):
        ma = model.ModelValue(ref, vector["a"])
        mb = model.ModelValue(ref_b, vector["b"])
        mu = model.ModelValue(ref_u, vector["u"])
        floored = ma.floor(0)
        assert got["as_unsigned"] == floored.as_unsigned(), vector
        assert got["as_signed"] == floored.as_signed(), vector
        assert got["u_as_signed"] == mu.as_signed(), vector
        assert got["u_as_unsigned"] == mu.as_unsigned(), vector
        assert got["mux"] == model.mux(bool(vector["sel"]), -ma, ma).raw, vector
        assert got["mux_mixed"] == model.mux(bool(vector["sel"]), ma, mb).raw, vector
        assert got["resize"] == ma.resize(-8).raw, vector

    run(m, sigs, outs, vectors, check)


def test_sim_asuint_clamps_negatives_section_8_2():
    """``pixelX`` of a negative coordinate must read 0, not a wrapped value."""
    vertex = AFixShape.from_q(SQ(16, 4))

    def exprs(ins):
        return {"pixel_x": ins["a"].floor(0).as_unsigned()}

    m, sigs, outs, _ = build({"a": vertex}, exprs)
    cases = {-16: 0, -1: 0, -32768: 0, 0: 0, 16: 1, 32767: 2047}

    def check(vector, got):
        assert got["pixel_x"] == cases[vector["a"]], vector

    run(m, sigs, outs, [{"a": raw} for raw in cases], check)


def test_sim_truncated_assignment_wraps_section_6_6():
    """``edgeStartVec(idx) := (a*x + b*y + c).truncated`` — 51 bits into 34."""
    coeff = AFixShape.from_q(SQ(34, 8))
    vertex = AFixShape.from_q(SQ(16, 4))
    rng = random.Random(0x7A11)

    a_sig = Signal(coeff.as_shape())
    b_sig = Signal(coeff.as_shape())
    x_sig = Signal(vertex.as_shape())
    y_sig = Signal(vertex.as_shape())
    c_sig = Signal(coeff.as_shape())

    a = AFixValue(coeff, a_sig)
    b = AFixValue(coeff, b_sig)
    x = AFixValue(vertex, x_sig)
    y = AFixValue(vertex, y_sig)
    c = AFixValue(coeff, c_sig)

    total = a * x + b * y + c
    assert as_tuple(total.afix_shape) == (563087392374768, -563087392309248, -12, 51, True)

    dest = Signal(coeff.as_shape())
    target = AFixValue(coeff, dest)
    m = Module()
    m.d.comb += target.eq(total.truncated())

    # untagged assignment of a finer exp must be rejected (§5.5 step 1)
    with pytest.raises(ValueError, match="precision would be lost"):
        target.eq(total)

    ref_coeff, ref_vertex = to_model(coeff), to_model(vertex)
    sim = Simulator(m)

    async def testbench(ctx):
        for _ in range(200):
            values = {
                "a": rng.randint(coeff.min_raw, coeff.max_raw),
                "b": rng.randint(coeff.min_raw, coeff.max_raw),
                "x": rng.randint(vertex.min_raw, vertex.max_raw),
                "y": rng.randint(vertex.min_raw, vertex.max_raw),
                "c": rng.randint(coeff.min_raw, coeff.max_raw),
            }
            for name, sig in (("a", a_sig), ("b", b_sig), ("x", x_sig),
                              ("y", y_sig), ("c", c_sig)):
                ctx.set(sig, values[name])
            expected = (
                model.ModelValue(ref_coeff, values["a"])
                * model.ModelValue(ref_vertex, values["x"])
                + model.ModelValue(ref_coeff, values["b"])
                * model.ModelValue(ref_vertex, values["y"])
                + model.ModelValue(ref_coeff, values["c"])
            ).assign_to(ref_coeff, truncated=True)
            assert ctx.get(dest) == expected.raw, values

    sim.add_testbench(testbench)
    sim.run()


def test_sim_uq08_factor_is_constant_zero_section_8_1():
    """``clampAFixToFactor`` = ``v.sat(255,0).fixTo(UQ(0,8))`` is hard zero."""
    src = AFixShape.sq(10, 0)
    factor = AFixShape.uq(0, 8)  # the *intended* 8-bit factor payload

    def exprs(ins):
        clamped = ins["a"].sat(255, 0).fix_to(UQ(0, 8))
        assert clamped.afix_shape.bit_width == 0
        payload = AFixValue(factor, Signal(factor.as_shape()))
        return {"clamped": clamped, "payload_bits": payload.raw}

    a_sig = Signal(src.as_shape())
    value = AFixValue(src, a_sig)
    clamped = value.sat(255, 0).fix_to(UQ(0, 8))
    assert clamped.afix_shape == AFixShape(0, 0, -8)
    assert clamped.afix_shape.bit_width == 0

    # the downstream payload register really is 8 bits of zero
    payload = Signal(factor.as_shape())
    m = Module()
    m.d.comb += AFixValue(factor, payload).eq(clamped)

    sim = Simulator(m)

    async def testbench(ctx):
        for raw in (-1024, -1, 0, 1, 255, 256, 1023):
            ctx.set(a_sig, raw)
            assert ctx.get(payload) == 0, raw

    sim.add_testbench(testbench)
    sim.run()


# ===========================================================================
# assignment rules and the Amaranth protocols
# ===========================================================================


def test_eq_requires_a_tag_for_precision_loss():
    coarse = AFixValue(AFixShape(2047, -2048, 0), Signal(12, name="coarse"))
    fine = AFixValue(AFixShape.from_q(SQ(16, 4)), Signal(16, name="fine"))
    # fine -> coarse loses the four fractional bits
    with pytest.raises(ValueError, match="precision would be lost"):
        coarse.eq(fine)
    assert coarse.eq(fine.truncated())          # wrap
    assert coarse.eq(fine.saturated())          # saturate
    assert coarse.eq(fine, truncated=True)
    # coarse -> fine is exact and needs no tag
    assert fine.eq(coarse)


def test_eq_rejects_an_out_of_range_source():
    small = AFixValue(AFixShape(255, 0, 0), Signal(8))
    big = AFixValue(AFixShape(1023, -1024, 0), Signal(unsigned(11)).as_signed())
    with pytest.raises(ValueError, match="does not cover"):
        small.eq(big)
    assert small.eq(big.saturated())


def test_eq_from_a_plain_value_is_a_raw_bit_copy():
    """``ColorCombine.clampSIntToFactor``: a UInt 0..255 becomes n/256 (§5.5)."""
    factor = AFixShape.uq(0, 8)
    source = Signal(8)
    dest = Signal(factor.as_shape())
    m = Module()
    m.d.comb += AFixValue(factor, dest).eq(source)

    sim = Simulator(m)

    async def testbench(ctx):
        for value in (0, 1, 128, 255):
            ctx.set(source, value)
            assert ctx.get(dest) == value

    sim.add_testbench(testbench)
    sim.run()


def test_shape_castable_protocol():
    shape = AFixShape.from_q(SQ(16, 4))
    signal = Signal(shape)
    assert isinstance(signal, AFixValue)
    assert signal.shape() == shape
    assert len(Value.cast(signal)) == 16
    assert Value.cast(signal).shape().signed

    init = Signal(shape, init=shape.const(0.5))
    assert len(Value.cast(init)) == 16
    assert shape.from_bits(0xFFFF) == -1
    with pytest.raises(ValueError):
        shape.const(0.01)  # not representable at exp = -4


def test_const_and_literals():
    """``one := 1.0``, ``negOne := -1.0``, ``halfPixel := 0.5``, ``0.SQ(1b,0b)``."""
    vertex = AFixShape.from_q(SQ(16, 4))
    assert vertex.const(1.0).raw.value == 16
    assert vertex.const(-1.0).raw.value == -16
    assert AFixShape.sq(3, 4).const(0.5).raw.value == 8
    assert AFixShape(1, -2, 0).const_trunc(0.0).raw.value == 0
    assert AFixShape(1, -2, 0).const_trunc(0.9).raw.value == 0
    assert AFixShape(1, -2, 0).const_trunc(-0.9).raw.value == 0


# ===========================================================================
# the modes AfixOps exercises beyond the core's own four
# ===========================================================================


def test_round_is_the_global_default_which_is_floor():
    """``.round(0, false)`` is plain floor here (§1.3), not a half-rounding.

    ``AfixOps_spinal.v`` settles it: ``round0_vertex`` is a bare ``>>> 4`` and
    is 12 bits wide, i.e. it carries floor's ``(2047, -2048)`` bounds rather
    than ceil's 13-bit ``(2048, -2048)``.
    """
    vertex = AFixShape.from_q(SQ(16, 4))
    value = AFixValue(vertex, Signal(vertex.as_shape()))
    assert value.round(0).afix_shape == round_shape(vertex, 0, RoundType.FLOOR)
    assert value.round(0).afix_shape.bit_width == 12


def test_toward_zero_and_away_from_zero_bounds():
    vertex = AFixShape.from_q(SQ(16, 4))
    assert rounded_bounds(vertex, 0, RoundType.FLOOR_TO_ZERO) == (2047, -2048)
    assert rounded_bounds(vertex, 0, RoundType.CEIL_TO_INF) == (2048, -2048)
    assert round_shape(vertex, 0, RoundType.FLOOR_TO_ZERO).bit_width == 12
    assert round_shape(vertex, 0, RoundType.CEIL_TO_INF).bit_width == 13


def test_unreached_round_types_refuse_to_guess():
    """ROUNDTOEVEN/SCRAP exist for ``fixTo``'s sake but are identity-only."""
    for rt in (RoundType.ROUND_HALF_TO_EVEN, RoundType.SCRAP):
        assert round_bigint(1234, 0, rt) == 1234      # drop == 0 is the identity
        assert round_bigint(1234, -3, rt) == 1234     # drop < 0 too (§8.5)
        with pytest.raises(NotImplementedError):
            round_bigint(1234, 2, rt)


def test_sat_bits_keeps_the_format():
    """``AFix.sat(m)`` narrows the reachable values, not the format (§4)."""
    vertex = AFixShape.from_q(SQ(16, 4))
    value = AFixValue(vertex, Signal(vertex.as_shape()))
    assert value.sat_bits(1).afix_shape == vertex
    assert len(value.sat_bits(1).raw) == 16
    u8 = AFixShape.from_q(UQ(8, 0))
    assert len(AFixValue(u8, Signal(8)).sat_bits(1).raw) == 8


@pytest.mark.parametrize("name,shape", [
    ("vertex", AFixShape.from_q(SQ(16, 4))),
    ("w", AFixShape.from_q(SQ(32, 30))),
    ("colormul", AFixShape.from_q(QFormat(19, 8, True))),
    ("u8", AFixShape.from_q(UQ(8, 0))),
])
def test_sim_extra_rounding_and_sat_bits(name, shape):
    """``round``/``floorToZero``/``ceilToInf``/``sat(m)`` against the model."""
    rng = random.Random(0xE7A3 ^ len(name))
    signed = shape.signed

    def exprs(ins):
        a = ins["a"]
        out = {"sat_bits1": a.sat_bits(1), "sat_bits2": a.sat_bits(2)}
        if signed:
            out |= {
                "round0": a.round(0),
                "floor_to_zero0": a.floor_to_zero(0),
                "ceil_to_inf0": a.ceil_to_inf(0),
            }
        return out

    m, sigs, outs, _ = build({"a": shape}, exprs)
    ref = to_model(shape)
    drop = 0 - shape.exp
    vectors = [{"a": v} for v in raw_samples(shape, rng, 40)]
    if drop > 0:  # straddle zero, where toward/away-from-zero differ from floor
        for candidate in (-1, 0, 1, (1 << drop) - 1, -(1 << drop) + 1, 1 << drop):
            if shape.min_raw <= candidate <= shape.max_raw:
                vectors.append({"a": candidate})
    while len(vectors) < 200:
        vectors.append({"a": rng.randint(shape.min_raw, shape.max_raw)})

    def check(vector, got):
        mv = model.ModelValue(ref, vector["a"])
        assert got["sat_bits1"] == mv.sat_bits(1).raw, (name, vector)
        assert got["sat_bits2"] == mv.sat_bits(2).raw, (name, vector)
        if signed:
            assert got["round0"] == mv.round(0).raw, (name, vector)
            assert got["floor_to_zero0"] == mv.floor_to_zero(0).raw, (name, vector)
            assert got["ceil_to_inf0"] == mv.ceil_to_inf(0).raw, (name, vector)

    run(m, sigs, outs, vectors, check)


def test_sim_uint_sat():
    """``RegisterBank:207`` — ``io.pciFifoFree.sat(1)``, a plain UInt shrink."""
    from avoodoo.lib.fixed import sat_unsigned

    source = Signal(7)
    out = Signal(6)
    m = Module()
    m.d.comb += out.eq(sat_unsigned(source, 1))

    sim = Simulator(m)

    async def testbench(ctx):
        for value in range(128):
            ctx.set(source, value)
            assert ctx.get(out) == model.sat_uint(value, 7, 1), value

    sim.add_testbench(testbench)
    sim.run()


# ===========================================================================
# AFix inside an amaranth.lib.data layout
# ===========================================================================


def test_layout_field_keeps_the_shape_signedness():
    """Regression: a signed AFix in a ``data`` layout must not go unsigned.

    ``data.View.__getitem__`` resolves a shape-castable field by handing it an
    *unsigned* slice of the packed signal.  If ``AFixValue`` kept the Value's
    signedness instead of the shape's, every add, compare and saturation on
    that field would silently be unsigned arithmetic.  Found by the
    SpanRasterizer lockstep diff (p3-rasterizer), where ``state.x`` of
    ``-13278`` read as ``+52258`` and ``(x + one).fixTo(vertexFormat)``
    saturated to ``0x7FFF`` instead of giving Spinal's ``0xCC32``.

    The core keeps every AFix in a Bundle (``Coefficients``, ``Cursor``,
    ``InputGradient``, the Tmu payloads), so this is the normal case, not
    a corner one.
    """
    vertex = AFixShape.from_q(SQ(16, 4))
    layout = data.StructLayout({"x": vertex, "y": vertex})
    view = Signal(layout)
    free = Signal(vertex)

    # both spellings must agree, and both must follow the *shape*
    assert view.x.raw.shape().signed is True
    assert free.raw.shape().signed is True
    assert view.x.afix_shape == vertex


def test_sim_layout_field_arithmetic_matches_the_model():
    """Negative operands through a layout field: add, sub, mul, compare, fixTo."""
    rng = random.Random(0xDA7A)
    vertex = AFixShape.from_q(SQ(16, 4))
    layout = data.StructLayout({"x": vertex, "y": vertex})
    view = Signal(layout)
    one = vertex.const(1.0)

    outs = {
        "add": (view.x + view.y).raw,
        "sub": (view.x - view.y).raw,
        "mul": (view.x * view.y).raw,
        "neg": (-view.x).raw,
        "ge": view.x >= view.y,
        "fix_plus_one": (view.x + one).fix_to(SQ(16, 4)).raw,
        "floor0": view.x.floor(0).raw,
        "as_unsigned": view.x.floor(0).as_unsigned(),
    }
    m = Module()
    ports = {}
    for name, value in outs.items():
        port = Signal(value.shape(), name=f"o_{name}")
        m.d.comb += port.eq(value)
        ports[name] = port

    ref = to_model(vertex)
    ref_one = model.const(ref, 1)
    # the reported failure, plus corners and random negatives
    vectors = [(-13278, 1)]
    for x in raw_samples(vertex, rng, 20):
        vectors.append((x, rng.randint(vertex.min_raw, vertex.max_raw)))
    while len(vectors) < 200:
        vectors.append((rng.randint(vertex.min_raw, -1),
                        rng.randint(vertex.min_raw, vertex.max_raw)))

    sim = Simulator(m)

    async def testbench(ctx):
        for x, y in vectors:
            ctx.set(view.x.raw, x)
            ctx.set(view.y.raw, y)
            mx, my = model.ModelValue(ref, x), model.ModelValue(ref, y)
            want = {
                "add": (mx + my).raw,
                "sub": (mx - my).raw,
                "mul": (mx * my).raw,
                "neg": (-mx).raw,
                "ge": int(mx >= my),
                "fix_plus_one": (mx + ref_one).fix_to(ref).raw,
                "floor0": mx.floor(0).raw,
                "as_unsigned": mx.floor(0).as_unsigned(),
            }
            for name, expected in want.items():
                assert ctx.get(ports[name]) == expected, (name, x, y)

    sim.add_testbench(testbench)
    sim.run()


def test_layout_field_reproduces_the_reported_span_walker_value():
    """The exact value from the SpanRasterizer diff: raw -13278 -> 0xCC32."""
    vertex = AFixShape.from_q(SQ(16, 4))
    view = Signal(data.StructLayout({"x": vertex}))
    result = (view.x + vertex.const(1.0)).fix_to(SQ(16, 4))
    out = Signal(16)
    m = Module()
    m.d.comb += out.eq(result.raw)

    sim = Simulator(m)

    async def testbench(ctx):
        ctx.set(view.x.raw, -13278)
        assert ctx.get(out) & 0xFFFF == 0xCC32

    sim.add_testbench(testbench)
    sim.run()


def test_align_is_not_implemented_on_purpose():
    """§1.3: AFixRounding.saturation is False everywhere, so align is unused."""
    value = AFixValue(AFixShape.from_q(SQ(16, 4)), Signal(16))
    with pytest.raises(NotImplementedError):
        value.ceil(0, align=True)
