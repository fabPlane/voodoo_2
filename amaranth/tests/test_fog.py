"""Phase 4 unit tests for :mod:`avoodoo.pixel.fog`.

There is no ``FogTest.scala`` upstream, so this is a fresh pysim test against
a Python model of ``ext/SpinalVoodoo/src/voodoo/pixel/Fog.scala`` written
straight off the Scala:

* :func:`voodoo_fls` -- the four-step leading-zero binary search;
* :func:`w_depth` -- the 86Box ``depthCalc`` reconstruction from ``rawW``,
  including the two edge cases (``hwW`` negative or bit 30 set -> 0,
  ``wUpper == 0`` -> 0xF001) and the 0xFFFF clamp;
* :func:`w_fog_factor` -- the 64-entry table lookup, the signed
  base+delta*blend interpolation and its [0, 255] clamp;
* :func:`blend` -- the four fog modes (disabled, constant, additive,
  multiplicative).

All four reproduce Spinal's *wrapping fixed-width* arithmetic, which is where
a 1:1 port is most likely to drift: ``+``/``-`` do not grow in SpinalHDL, so
``wFogFactorRaw`` and the per-channel ``diff``/``finalColor`` adds are taken
mod 2^10, and ``wBlend.asSInt`` reinterprets a ``UInt(8)`` *before*
sign-extending it, so table blends of 128..255 are negative.

The model is deliberately independent of :mod:`avoodoo.lib.fixed`: the only
AFix value in the module is ``depth``, which is a pure pass-through, so the
arithmetic here is all plain integers.  ``avoodoo.lib.fixed_model`` is used
only to build the ``depth`` stimulus in the ``AFix`` raw encoding.

The cycle-exact gate is ``make diff MOD=Fog``; this test is the readable
statement of what the module computes.
"""

import random

import pytest
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.lib.fixed_model import ModelShape, const as afix_const
from avoodoo.pixel.fog import Fog

PERIOD = 1e-6
LATENCY = 3  # three m2sPipe stages


# ---------------------------------------------------------------------------
# Python model
# ---------------------------------------------------------------------------


def u(v, bits):
    """Spinal's ``resize`` to a narrower ``UInt``: keep the low bits."""
    return v & ((1 << bits) - 1)


def sx(v, bits):
    """Reinterpret the low ``bits`` of ``v`` as a two's-complement ``SInt``."""
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def voodoo_fls(v):
    """``def voodooFls(v: UInt): UInt``."""
    v0 = u(v, 16)
    test0 = (v0 >> 8) == 0
    n0 = 8 if test0 else 0
    s0 = u(v0 << 8, 16) if test0 else v0

    test1 = (s0 >> 12) == 0
    n1 = u(n0 + (4 if test1 else 0), 5)
    s1 = u(s0 << 4, 16) if test1 else s0

    test2 = (s1 >> 14) == 0
    n2 = u(n1 + (2 if test2 else 0), 5)
    s2 = u(s1 << 2, 16) if test2 else s1

    test3 = ((s2 >> 15) & 1) == 0
    return u(n2 + (1 if test3 else 0), 5)


def w_depth(raw_w):
    """The ``wStage`` W-buffer depth.  ``raw_w`` is the 32-bit ``rawW`` pattern."""
    hw = u(raw_w, 32)
    w_upper = (hw >> 14) & 0xFFFF
    w_exp = voodoo_fls(w_upper)

    not_w = u(~u(hw << 2, 32), 32)
    shift = u(19 - w_exp, 5)
    mantissa = (not_w >> shift) & 0xFFF

    exp_shifted = u(w_exp << 12, 17)
    raw = u((exp_shifted | mantissa) + 1, 17)
    clamped = 0xFFFF if raw > 0xFFFF else u(raw, 16)

    if (hw >> 31) & 1 or (hw >> 30) & 1:
        return 0
    if w_upper == 0:
        return 0xF001
    return clamped


def w_fog_factor(table, wd):
    """``stage1``'s fog-table lookup and interpolation."""
    entry = table[(wd >> 10) & 0x3F]
    fog_base = entry & 0xFF
    fog_delta = sx(entry >> 8, 8)
    # `wBlend.asSInt.resize(16 bits)`: reinterpret THEN sign-extend.
    w_blend = sx((wd >> 2) & 0xFF, 8)
    delta_product = fog_delta * w_blend            # SInt(24)
    dp = sx(delta_product >> 8, 10)                # >>8 -> SInt(16), resize(10)
    raw = sx(fog_base + dp, 10)                    # SInt(10), wrapping
    if raw < 0:
        return 0
    if raw > 255:
        return 255
    return raw & 0xFF


def fog_a(table, *, wd, mode, alpha_fog_factor, z_fog_factor):
    """The ``switch(fogModeSelect)`` in ``stage1``."""
    if mode == 0:
        return w_fog_factor(table, wd)
    if mode == 1:
        return alpha_fog_factor
    if mode == 2:
        return z_fog_factor
    return 0


def blend(a, cbf, fog_rgb, *, fog_enable, fog_constant, fog_add, fog_mult):
    """``stage2`` -- ``cbf`` and ``fog_rgb`` are ``(r, g, b)`` tuples."""
    if not fog_enable:
        return tuple(cbf)
    if fog_constant:
        # `(s.resize(9) + f.resize(9)).min(255).resize(8)`
        return tuple(min(c + f, 255) for c, f in zip(cbf, fog_rgb))

    a1 = u(a + 1, 9)                                   # fogAPlus1
    base = (0, 0, 0) if fog_add else tuple(fog_rgb)    # resize(10).asSInt
    if not fog_mult:
        diff = tuple(sx(b - c, 10) for b, c in zip(base, cbf))
    else:
        diff = tuple(sx(b, 10) for b in base)

    out = []
    for i in range(3):
        mul = sx((diff[i] * a1) >> 8, 10)              # (d*a1)>>8, resize(10)
        fin = mul if fog_mult else sx(cbf[i] + mul, 10)
        out.append(0 if fin < 0 else (255 if fin > 255 else fin & 0xFF))
    return tuple(out)


def model(table, tx):
    """The whole module, for one transaction dict."""
    wd = w_depth(tx["raw_w"])
    z = (tx["depth"] >> 20) & 0xFF
    a = fog_a(table, wd=wd, mode=tx["fog_mode_select"],
              alpha_fog_factor=tx["iterated_alpha"], z_fog_factor=z)
    cbf = (tx["r"], tx["g"], tx["b"])
    fog_rgb = ((tx["fog_color"] >> 16) & 0xFF,
               (tx["fog_color"] >> 8) & 0xFF,
               tx["fog_color"] & 0xFF)
    color = blend(a, cbf, fog_rgb,
                  fog_enable=tx["fog_enable"], fog_constant=tx["fog_constant"],
                  fog_add=tx["fog_add"], fog_mult=tx["fog_mult"])
    return {
        "w_depth": wd,
        "color": color,
        "color_before_fog": cbf,
        "alpha": tx["alpha"],
        "depth": tx["depth"],
        "chroma_key": tx["chroma_key"],
        "za_color": tx["za_color"],
        "coords": (tx["x"], tx["y"]),
    }


# ---------------------------------------------------------------------------
# stimulus
# ---------------------------------------------------------------------------


DEFAULT_TX = {
    "x": 0, "y": 0, "r": 0, "g": 0, "b": 0, "alpha": 0, "depth": 0,
    "iterated_alpha": 0, "raw_w": 0, "fog_color": 0, "chroma_key": 0,
    "za_color": 0, "fog_enable": 0, "fog_add": 0, "fog_mult": 0,
    "fog_constant": 0, "fog_mode_select": 0,
}


def tx(**kwargs):
    out = dict(DEFAULT_TX)
    out.update(kwargs)
    return out


def afix_depth_raw(c, value):
    """The raw ``SQ(32,12)`` encoding of a real ``value``, via the AFix model.

    Only used to prove the pass-through carries a *meaningful* depth rather
    than an arbitrary bit pattern; the module never looks at anything but
    bits [27:20] of it.
    """
    shape = ModelShape.from_q(c.v_depth_format)
    return u(afix_const(shape, value).raw, 32)


def random_tx(rng):
    return {
        "x": rng.randrange(1 << 12),
        "y": rng.randrange(1 << 12),
        "r": rng.randrange(256),
        "g": rng.randrange(256),
        "b": rng.randrange(256),
        "alpha": rng.randrange(256),
        "depth": rng.randrange(1 << 32),
        "iterated_alpha": rng.randrange(256),
        "raw_w": rng.randrange(1 << 32),
        "fog_color": rng.randrange(1 << 32),
        "chroma_key": rng.randrange(1 << 32),
        "za_color": rng.randrange(1 << 32),
        "fog_enable": rng.randrange(2),
        "fog_add": rng.randrange(2),
        "fog_mult": rng.randrange(2),
        "fog_constant": rng.randrange(2),
        "fog_mode_select": rng.randrange(4),
    }


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _set_input(ctx, dut, t):
    p = dut.input.payload
    ctx.set(p.coords.x, t["x"])
    ctx.set(p.coords.y, t["y"])
    ctx.set(p.color.r, t["r"])
    ctx.set(p.color.g, t["g"])
    ctx.set(p.color.b, t["b"])
    ctx.set(p.alpha, t["alpha"])
    ctx.set(p.depth.as_value(), sx(t["depth"], 32))
    ctx.set(p.iterated_alpha, t["iterated_alpha"])
    ctx.set(p.raw_w, sx(t["raw_w"], 32))
    ctx.set(p.fog_color, t["fog_color"])
    ctx.set(p.chroma_key, t["chroma_key"])
    ctx.set(p.za_color, t["za_color"])
    ctx.set(p.fog_mode.fog_enable, t["fog_enable"])
    ctx.set(p.fog_mode.fog_add, t["fog_add"])
    ctx.set(p.fog_mode.fog_mult, t["fog_mult"])
    ctx.set(p.fog_mode.fog_constant, t["fog_constant"])
    ctx.set(p.fog_mode.fog_mode_select, t["fog_mode_select"])


def _out_signals(dut):
    p = dut.output.payload
    return (dut.output.valid, p.w_depth, p.color.r, p.color.g, p.color.b,
            p.color_before_fog.r, p.color_before_fog.g, p.color_before_fog.b,
            p.alpha, p.depth.as_value(), p.chroma_key, p.za_color,
            p.coords.x, p.coords.y)


def _as_dict(sample):
    (valid, wd, cr, cg, cb, br, bg, bb, alpha, depth, ck, za, x, y) = sample
    return {
        "valid": valid,
        "w_depth": wd,
        "color": (cr, cg, cb),
        "color_before_fog": (br, bg, bb),
        "alpha": alpha,
        "depth": u(depth, 32),
        "chroma_key": ck,
        "za_color": za,
        "coords": (u(x, 12), u(y, 12)),
    }


def run_stream(table, transactions, *, ready_pattern=None, c=None, seed=0):
    """Push ``transactions`` through the DUT; return the payloads it emitted.

    ``ready_pattern`` is a callable ``cycle -> bool`` for ``output.ready``;
    the default holds it high.
    """
    c = c if c is not None else Config.voodoo1()
    dut = Fog(c)
    got = []
    rng = random.Random(seed)

    async def bench(ctx):
        packed = 0
        for i, word in enumerate(table):
            packed |= word << (16 * i)
        ctx.set(dut.fog_table.as_value(), packed)

        pending = list(transactions)
        cycle = 0
        outs = _out_signals(dut)
        # enough cycles to drain the three stages even with stalls
        budget = 8 * (len(transactions) + LATENCY) + 32
        while (pending or len(got) < len(transactions)) and cycle < budget:
            ready = 1 if ready_pattern is None else int(ready_pattern(cycle))
            ctx.set(dut.output.ready, ready)
            if pending:
                _set_input(ctx, dut, pending[0])
                ctx.set(dut.input.valid, 1)
            else:
                ctx.set(dut.input.valid, 0)

            accepted = ctx.get(dut.input.ready) and bool(pending)
            # `.sample()` prepends the tick's own (clk, rst) pair.
            sample = (await ctx.tick().sample(*outs))[2:]
            if sample[0] and ready:
                got.append(_as_dict(sample))
            if accepted:
                pending.pop(0)
            cycle += 1

        assert len(got) == len(transactions), (
            f"only {len(got)}/{len(transactions)} beats came out in {cycle} cycles")

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(bench)
    sim.run()
    return got


def check(table, transactions, **kwargs):
    got = run_stream(table, transactions, **kwargs)
    for i, (t, g) in enumerate(zip(transactions, got)):
        want = model(table, t)
        for key, value in want.items():
            assert g[key] == value, (
                f"beat {i} field {key}: expected {value!r}, got {g[key]!r}\n"
                f"  tx = {t}")


ZERO_TABLE = [0] * 64


def ramp_table(rng=None):
    """A plausible fog table: rising base, small signed deltas."""
    rng = rng or random.Random(1)
    return [((rng.randrange(256) << 8) | min(255, i * 4)) for i in range(64)]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_latency_is_three_and_busy_tracks_the_stages():
    """Three ``m2sPipe`` stages; ``io.busy`` is the OR of their valids."""
    dut = Fog(Config.voodoo1())
    seen = []

    async def bench(ctx):
        ctx.set(dut.output.ready, 1)
        _set_input(ctx, dut, tx(r=1, g=2, b=3))
        ctx.set(dut.input.valid, 1)
        await ctx.tick()
        ctx.set(dut.input.valid, 0)
        for _ in range(6):
            _, _, valid, busy = await ctx.tick().sample(dut.output.valid, dut.busy)
            seen.append((valid, busy))

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(bench)
    sim.run()

    valids = [v for v, _ in seen]
    assert valids.count(1) == 1, "exactly one beat comes out"
    first = valids.index(1)
    assert first == LATENCY - 1, f"latency {first + 1}, expected {LATENCY}"
    # busy is high while the beat is anywhere in the pipe and clear afterwards
    assert all(b for _, b in seen[: first + 1])
    assert not any(b for _, b in seen[first + 1:])


def test_fog_disabled_passes_the_colour_through():
    table = ramp_table()
    txs = [tx(r=r, g=g, b=b, fog_color=0xFF8040, fog_enable=0,
              raw_w=0x0800_0000, fog_mode_select=0)
           for r, g, b in [(0, 0, 0), (1, 2, 3), (255, 128, 7), (255, 255, 255)]]
    got = run_stream(table, txs)
    for t, g in zip(txs, got):
        assert g["color"] == (t["r"], t["g"], t["b"])
        assert g["color"] == g["color_before_fog"]
    check(table, txs)


def test_fog_constant_adds_and_saturates():
    table = ZERO_TABLE
    txs = [
        tx(r=10, g=20, b=30, fog_color=0x010203, fog_enable=1, fog_constant=1),
        tx(r=250, g=250, b=250, fog_color=0x0A0A0A, fog_enable=1, fog_constant=1),
        tx(r=255, g=255, b=255, fog_color=0xFFFFFF, fog_enable=1, fog_constant=1),
    ]
    got = run_stream(table, txs)
    assert got[1]["color"] == (255, 255, 255), "the 9-bit sum saturates at 255"
    assert got[2]["color"] == (255, 255, 255)
    check(table, txs)


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_each_fog_mode_select(mode):
    """``fogModeSelect``: 0 = W table, 1 = iterated alpha, 2 = Z, 3 = zero."""
    table = ramp_table()
    rng = random.Random(0x50 + mode)
    txs = []
    for _ in range(40):
        t = random_tx(rng)
        t["fog_mode_select"] = mode
        t["fog_enable"] = 1
        t["fog_constant"] = 0
        txs.append(t)
    check(table, txs)


def test_fog_mode_3_is_transparent_to_the_table():
    """Mode 3 forces ``fogA = 0``, so the table cannot matter."""
    rng = random.Random(7)
    txs = [dict(random_tx(rng), fog_mode_select=3, fog_enable=1, fog_constant=0)
           for _ in range(20)]
    a = run_stream(ramp_table(random.Random(11)), txs)
    b = run_stream(ramp_table(random.Random(99)), txs)
    assert [g["color"] for g in a] == [g["color"] for g in b]


def test_w_depth_edge_cases():
    """The three special ``rawW`` paths, at the port."""
    table = ZERO_TABLE
    cases = [
        (0x8000_0000, 0),        # hwW negative
        (0xFFFF_FFFF, 0),        # hwW negative
        (0x4000_0000, 0),        # bit 30 set
        (0x0000_0001, 0xF001),   # wUpper == 0 (bits [29:14] all zero)
        (0x0000_3FFF, 0xF001),
        (0x0000_4000, None),     # wUpper == 1, the smallest non-zero exponent
        (0x3FFF_FFFF, None),     # the largest legal hwW
    ]
    txs = [tx(raw_w=raw) for raw, _ in cases]
    got = run_stream(table, txs)
    for (raw, want), g in zip(cases, got):
        expect = w_depth(raw) if want is None else want
        assert g["w_depth"] == expect, f"rawW={raw:#010x}"
        assert w_depth(raw) == expect, "the model agrees with the RTL"


def test_voodoo_fls_model_matches_a_leading_zero_count():
    """Sanity check on the model itself before it is used as an oracle."""
    for v in [0, 1, 2, 3, 0x7FFF, 0x8000, 0xFFFF]:
        expect = 15 if v == 0 else (15 - v.bit_length() + 1)
        assert voodoo_fls(v) == expect, f"{v:#06x}"
    rng = random.Random(3)
    for _ in range(500):
        v = rng.randrange(1 << 16)
        expect = 15 if v == 0 else 16 - v.bit_length()
        assert voodoo_fls(v) == expect, f"{v:#06x}"


def test_w_blend_reinterpretation_really_goes_negative():
    """``wBlend.asSInt`` before the sign-extension: blends >= 128 subtract.

    This is the single easiest thing to "fix" while porting, so it gets its
    own case: two wDepths whose blend differs only in bit 7 must move the
    interpolation in opposite directions for a positive delta.
    """
    table = [0] * 64
    table[0] = (0x10 << 8) | 0x80  # base 0x80, delta +16
    lo = w_fog_factor(table, 0x0000 | (0x40 << 2))   # blend 0x40 -> +4
    hi = w_fog_factor(table, 0x0000 | (0xC0 << 2))   # blend 0xC0 -> -4
    assert lo > 0x80 > hi, (lo, hi)


def test_random_stream_matches_the_model():
    rng = random.Random(0xF06)
    table = ramp_table(random.Random(0xBEEF))
    check(table, [random_tx(rng) for _ in range(300)])


def test_random_stream_with_backpressure():
    """Stalls must not change any beat, only when it comes out."""
    rng = random.Random(0xB0B)
    table = ramp_table(random.Random(0xCAFE))
    txs = [random_tx(rng) for _ in range(150)]
    stall = random.Random(5)
    check(table, txs, ready_pattern=lambda _cycle: stall.random() < 0.55)


def test_depth_is_an_untouched_pass_through():
    """``depth`` is the only AFix here and ``Fog`` never rewrites it."""
    c = Config.voodoo1()
    table = ZERO_TABLE
    values = [0.0, 1.0, -1.0, 1024.5, -2048.0, 524287.9375]
    txs = [tx(depth=afix_depth_raw(c, v), raw_w=0x1000_0000) for v in values]
    got = run_stream(table, txs)
    for v, t, g in zip(values, txs, got):
        assert g["depth"] == t["depth"], f"depth {v} did not survive the pipe"
