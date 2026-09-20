"""``avoodoo.lib.intops`` against plain Python integers.

Each helper claims to reproduce one SpinalHDL 1.15 ``SInt``/``UInt`` width
rule.  The reference below is that rule written directly on Python ints
(two's-complement wrap at a width), and the sweep drives random operands of
random widths through pysim and compares both the *value* and the *shape* of
what the helper built.
"""

import random

import pytest
from amaranth.hdl import Const, Module, Signal, Value, signed, unsigned
from amaranth.sim import Simulator

from avoodoo.lib import intops

# --- the rules on Python ints ------------------------------------------------


def wrap_s(x: int, width: int) -> int:
    """Two's-complement wrap of ``x`` into ``width`` signed bits."""
    if width == 0:
        return 0
    x &= (1 << width) - 1
    return x - (1 << width) if x >> (width - 1) else x


def wrap_u(x: int, width: int) -> int:
    return x & ((1 << width) - 1) if width else 0


def ref_sext(x, w_in, w_out):
    return wrap_s(x, w_out) if w_out < w_in else x          # extension is exact


def ref_zext(x, w_in, w_out):
    return wrap_u(x, w_out) if w_out < w_in else x


def ref_sar(x, w, n):
    return wrap_s(x >> n, max(w - n, 0))                   # floor division


def ref_u_shr(x, w, n):
    return wrap_u(x >> n, max(w - n, 0))


# --- pysim plumbing ------------------------------------------------------------


def run(cases, seed):
    """``cases``: list of (name, in_shapes, build(ins) -> Value, ref(vals) -> int)."""
    rng = random.Random(seed)
    m = Module()
    sigs, outs = {}, {}
    for name, shapes, build, _ref in cases:
        ins = {k: Signal(s, name=f"{name}_{k}") for k, s in shapes.items()}
        value = build(ins)
        out = Signal(value.shape(), name=f"o_{name}")
        m.d.comb += out.eq(value)
        sigs[name], outs[name] = ins, out

    def sample(shape):
        if shape.signed:
            lo, hi = -(1 << (shape.width - 1)), (1 << (shape.width - 1)) - 1
        else:
            lo, hi = 0, (1 << shape.width) - 1
        return rng.choice([lo, hi, 0, rng.randint(lo, hi), rng.randint(lo, hi)])

    sim = Simulator(m)

    async def testbench(ctx):
        for _ in range(120):
            for name, shapes, _build, ref in cases:
                vals = {k: sample(s) for k, s in shapes.items()}
                for k, v in vals.items():
                    ctx.set(sigs[name][k], v)
                assert ctx.get(outs[name]) == ref(vals), (name, vals)

    sim.add_testbench(testbench)
    sim.run()


def widths(rng, n, lo=1, hi=40):
    return [rng.randint(lo, hi) for _ in range(n)]


# --- resize -------------------------------------------------------------------


def test_sext_zext_shapes_and_values():
    rng = random.Random(1)
    cases = []
    for w_in in widths(rng, 12):
        for w_out in (0, 1, w_in - 1, w_in, w_in + 1, w_in + 7, 64):
            if w_out < 0:
                continue
            cases.append((f"sext_{w_in}_{w_out}", {"x": signed(w_in)},
                          lambda i, w=w_out: intops.sext(i["x"], w),
                          lambda v, a=w_in, b=w_out: ref_sext(v["x"], a, b)))
            cases.append((f"zext_{w_in}_{w_out}", {"x": unsigned(w_in)},
                          lambda i, w=w_out: intops.zext(i["x"], w),
                          lambda v, a=w_in, b=w_out: ref_zext(v["x"], a, b)))
    # shapes: sext yields signed(w_out) (unsigned(0) for the degenerate case)
    assert intops.sext(Signal(signed(8)), 12).shape() == signed(12)
    assert intops.sext(Signal(signed(8)), 3).shape() == signed(3)
    assert intops.sext(Signal(signed(8)), 0).shape() == unsigned(0)
    assert intops.zext(Signal(unsigned(8)), 12).shape() == unsigned(12)
    assert intops.zext(Signal(signed(8)), 12).shape() == unsigned(12)  # bit pattern
    run(cases, 11)


def test_resize_follows_the_source_signedness():
    s, u = Signal(signed(8)), Signal(unsigned(8))
    assert intops.resize(s, 12).shape() == signed(12)
    assert intops.resize(u, 12).shape() == unsigned(12)
    assert intops.s_resize is intops.sext and intops.u_resize is intops.zext
    with pytest.raises(TypeError):
        intops.sext(u, 12)
    with pytest.raises(ValueError):
        intops.zext(u, -1)


def test_resize_of_a_negative_through_zext_is_the_bit_pattern():
    """``x.asBits.resize`` on a negative SInt zero-extends the pattern."""
    run([("neg_zext", {"x": signed(8)},
          lambda i: intops.zext(i["x"], 12),
          lambda v: wrap_u(v["x"], 8))], 5)


# --- add / sub ----------------------------------------------------------------


def test_add_sub_keep_max_operand_width():
    rng = random.Random(2)
    cases = []
    for wa, wb in zip(widths(rng, 10), widths(rng, 10)):
        w = max(wa, wb)
        cases += [
            (f"s_add_{wa}_{wb}", {"a": signed(wa), "b": signed(wb)},
             lambda i: intops.s_add(i["a"], i["b"]),
             lambda v, w=w: wrap_s(v["a"] + v["b"], w)),
            (f"s_sub_{wa}_{wb}", {"a": signed(wa), "b": signed(wb)},
             lambda i: intops.s_sub(i["a"], i["b"]),
             lambda v, w=w: wrap_s(v["a"] - v["b"], w)),
            (f"u_add_{wa}_{wb}", {"a": unsigned(wa), "b": unsigned(wb)},
             lambda i: intops.u_add(i["a"], i["b"]),
             lambda v, w=w: wrap_u(v["a"] + v["b"], w)),
            (f"u_sub_{wa}_{wb}", {"a": unsigned(wa), "b": unsigned(wb)},
             lambda i: intops.u_sub(i["a"], i["b"]),
             lambda v, w=w: wrap_u(v["a"] - v["b"], w)),
        ]
    # explicit width == (a + b).resize(width bits)
    cases += [
        ("s_add_w20", {"a": signed(16), "b": signed(16)},
         lambda i: intops.s_add(i["a"], i["b"], 20),
         lambda v: wrap_s(v["a"] + v["b"], 20)),
        ("s_add_w12", {"a": signed(16), "b": signed(16)},
         lambda i: intops.s_add(i["a"], i["b"], 12),
         lambda v: wrap_s(v["a"] + v["b"], 12)),
        ("u_sub_w4", {"a": unsigned(8), "b": unsigned(8)},
         lambda i: intops.u_sub(i["a"], i["b"], 4),
         lambda v: wrap_u(v["a"] - v["b"], 4)),
    ]
    assert intops.s_add(Signal(signed(9)), Signal(signed(13))).shape() == signed(13)
    assert intops.u_add(Signal(unsigned(9)), Signal(unsigned(13))).shape() == unsigned(13)
    assert intops.s_add(Signal(signed(16)), Signal(signed(16)), 20).shape() == signed(20)
    run(cases, 22)


def test_add_sub_wrap_at_the_operand_width():
    """The whole point: Spinal's ``+`` drops the carry that Amaranth keeps."""
    run([
        ("wrap", {"a": signed(8), "b": signed(8)},
         lambda i: intops.s_add(i["a"], i["b"]),
         lambda v: wrap_s(v["a"] + v["b"], 8)),
    ], 3)
    # 127 + 1 must come out as -128, not 128
    a, b = Signal(signed(8)), Signal(signed(8))
    out = Signal(8)
    m = Module()
    m.d.comb += out.eq(intops.s_add(a, b))
    sim = Simulator(m)

    async def testbench(ctx):
        ctx.set(a, 127)
        ctx.set(b, 1)
        assert ctx.get(out.as_signed()) == -128
        ctx.set(a, -128)
        ctx.set(b, -1)
        assert ctx.get(out.as_signed()) == 127

    sim.add_testbench(testbench)
    sim.run()


def test_add_sub_reject_mixed_signedness():
    s, u = Signal(signed(8)), Signal(unsigned(8))
    with pytest.raises(TypeError):
        intops.s_add(s, u)
    with pytest.raises(TypeError):
        intops.u_sub(s, u)


# --- shifts -------------------------------------------------------------------


def test_shl_keep_drops_the_top_bits():
    rng = random.Random(4)
    cases = []
    for w in widths(rng, 10, 2, 40):
        for n in (0, 1, 2, w - 1, w, w + 3):
            cases.append((f"shl_s_{w}_{n}", {"x": signed(w)},
                          lambda i, n=n: intops.shl_keep(i["x"], n),
                          lambda v, w=w, n=n: wrap_s(v["x"] << n, w)))
            cases.append((f"shl_u_{w}_{n}", {"x": unsigned(w)},
                          lambda i, n=n: intops.shl_keep(i["x"], n),
                          lambda v, w=w, n=n: wrap_u(v["x"] << n, w)))
    # with an explicit width: (x |<< n).resize(width bits)
    cases.append(("shl_s_16_2_w20", {"x": signed(16)},
                  lambda i: intops.shl_keep(i["x"], 2, 20),
                  lambda v: wrap_s(wrap_s(v["x"] << 2, 16), 20)))
    assert intops.shl_keep(Signal(signed(16)), 3).shape() == signed(16)
    assert intops.shl_keep(Signal(unsigned(16)), 3).shape() == unsigned(16)
    assert intops.shl_keep(Signal(signed(16)), 3, 20).shape() == signed(20)
    run(cases, 44)


def test_sar_and_u_shr_narrow_by_the_shift():
    rng = random.Random(6)
    cases = []
    for w in widths(rng, 10, 1, 40):
        for n in (0, 1, w // 2, w - 1, w, w + 2):
            cases.append((f"sar_{w}_{n}", {"x": signed(w)},
                          lambda i, n=n: intops.sar(i["x"], n),
                          lambda v, w=w, n=n: ref_sar(v["x"], w, n)))
            cases.append((f"ushr_{w}_{n}", {"x": unsigned(w)},
                          lambda i, n=n: intops.u_shr(i["x"], n),
                          lambda v, w=w, n=n: ref_u_shr(v["x"], w, n)))
    assert intops.sar(Signal(signed(16)), 4).shape() == signed(12)
    assert intops.sar(Signal(signed(16)), 16).shape() == unsigned(0)
    assert intops.sar(Signal(signed(16)), 20).shape() == unsigned(0)
    assert intops.u_shr(Signal(unsigned(16)), 4).shape() == unsigned(12)
    run(cases, 66)


def test_sar_is_arithmetic():
    """``-5 >> 1`` is ``-3`` (floor), never ``+125``."""
    x = Signal(signed(8))
    out = Signal(signed(7))
    m = Module()
    m.d.comb += out.eq(intops.sar(x, 1))
    sim = Simulator(m)

    async def testbench(ctx):
        for value, want in ((-5, -3), (-1, -1), (-128, -64), (5, 2), (127, 63)):
            ctx.set(x, value)
            assert ctx.get(out) == want, value

    sim.add_testbench(testbench)
    sim.run()


def test_shift_argument_checks():
    with pytest.raises(TypeError):
        intops.sar(Signal(unsigned(8)), 1)
    with pytest.raises(TypeError):
        intops.u_shr(Signal(signed(8)), 1)
    with pytest.raises(ValueError):
        intops.shl_keep(Signal(signed(8)), -1)


# --- the patterns the raster modules actually write -------------------------


def test_triangle_setup_patterns():
    """``(term |<< 1).resize(w)``, ``(a + b) >> 4``, ``(x >> 2).resize(16)``."""
    run([
        ("term_x3", {"t": signed(20)},
         lambda i: intops.s_add(i["t"], intops.shl_keep(i["t"], 1), 20),
         lambda v: wrap_s(v["t"] + wrap_s(v["t"] << 1, 20), 20)),
        ("sum_sar4", {"a": signed(24), "b": signed(24)},
         lambda i: intops.sar(intops.s_add(i["a"], i["b"]), 4),
         lambda v: ref_sar(wrap_s(v["a"] + v["b"], 24), 24, 4)),
        ("mag_work", {"x": signed(20)},
         lambda i: intops.sext(intops.sar(i["x"], 2), 16),
         lambda v: wrap_s(v["x"] >> 2, 16)),
    ], 77)
