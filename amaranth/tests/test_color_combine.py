"""Phase 4 unit tests for :mod:`avoodoo.pixel.color_combine`.

Ports the intent of ``ext/SpinalVoodoo/test/ColorCombineTest.scala`` (the ten
directed cases: Gouraud passthrough, color1 select, invert, multiply by
local, subtract-and-add, negative and overflow clamping, reverse blend,
coordinate pass-through, backpressure) onto a pysim run against a Python
model of the CCU/ACU written straight off ``ColorCombine.scala``, then
adds random sweeps over *every* select code -- including the out-of-range
enum codes (``mselect`` 6/7, ``add`` 3, ``alphaLocalSelect`` 3), which the
Spinal ``switch`` folds into its last arm, and the dead ``MSelect.AOTHER``
factor (``docs/amaranth/afix-semantics.md`` §8.1).

The model uses :mod:`avoodoo.lib.fixed_model` for every ``AFix`` step
(``SQ(10,0)`` subtraction result, the ``UQ(0,8)``/``UQ(1,8)`` factors, the
``SQ(10,8)`` multiply/add with ``fixTo`` saturation, the ``sat``+``fixTo``
clamp) so the rounding and saturation points are the library's, not a
re-derivation.  The ``MSelect.AOTHER`` path builds the same zero-bit
``QFormat(0, 8)`` the Scala does, and the test pins that it is zero.

The cycle-exact gate is ``make diff MOD=ColorCombine``
(``tests/diff/test_diff_color_combine.py`` is the short version); this file
is the readable statement of what the module computes.
"""

from __future__ import annotations

import random

import pytest
from amaranth.hdl import Shape
from amaranth.sim import Simulator

from avoodoo.config import Config, QFormat, UQ
from avoodoo.core.core_types import AddMode, AlphaLocalSel, AlphaSel, LocalSel, MSelect, RgbSel
from avoodoo.lib.fixed_model import ModelShape, ModelValue, const as afix_const
from avoodoo.pixel.color_combine import (
    COLOR_MUL_FORMAT,
    ColorCombine,
    ColorCombineConfig,
)

PERIOD = 1e-6
LATENCY = 8  # eight StageLinks


# ---------------------------------------------------------------------------
# Python model (ColorCombine.scala:390-567)
# ---------------------------------------------------------------------------

M_AFTER_SUB = ModelShape.sq(10, 0)          # AFix.SQ(10 bits, 0 bits)
M_FACTOR = ModelShape.uq(0, 8)              # AFix.UQ(0 bits, 8 bits)
M_FACTOR_WIDE = ModelShape.uq(1, 8)         # AFix.UQ(1 bits, 8 bits)
M_MUL = ModelShape.from_q(QFormat(19, 8, True))   # colorMulFormat
M_DEAD_FACTOR = ModelShape.from_q(UQ(0, 8))       # spinal.core.UQ(0, 8): zero bits
M_U8 = ModelShape.from_q(UQ(8, 0))

assert M_MUL.max_raw == COLOR_MUL_FORMAT.max_raw and M_MUL.exp == COLOR_MUL_FORMAT.exp
assert M_DEAD_FACTOR.bit_width == 0


def u(v, bits):
    return v & ((1 << bits) - 1)


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def s9(v):
    """``(False ## v).asSInt`` on an 8-bit ``UInt``."""
    return v & 0xFF


def sint9(v):
    """Spinal ``SInt(9) - SInt(9)`` keeps 9 bits: wrap into -256..255."""
    return sx(v, 9)


def afix_sint(v, width):
    """``AFix(s: SInt)`` -- bounds from the width, exp 0."""
    return ModelValue(ModelShape.from_sint(width), v)


def clamp_sint_to_factor(v):
    """``clampSIntToFactor``: max(0).min(255) bit-copied into ``UQ(0,8)``."""
    clamped = min(max(v, 0), 255)
    return ModelValue(M_FACTOR, clamped)


def clamp_afix_to_factor(v):
    """``clampAFixToFactor``: ``sat(255, 0).fixTo(UQ(0, 8))`` -- zero bits (§8.1)."""
    return v.sat(255, 0).fix_to(M_DEAD_FACTOR)


def clamp_afix_to_u8(v):
    """``clampAFixToU8``: ``sat(255, 0, exp = 0).fixTo(UQ(8, 0)).asUInt``."""
    return v.sat(255, 0, exp=0).fix_to(M_U8).as_unsigned()


def invert_factor(f, reverse_blend):
    """``invertFactor``: ``(reverseBlend ? f : ~f) + 1`` into ``UQ(1,8)``."""
    raw = f.bits() & 0xFF
    xored = raw if reverse_blend else raw ^ 0xFF
    return ModelValue(M_FACTOR_WIDE, u(xored + 1, 9))


def factor_pre_to_payload(v):
    """Assigning the helper's result to the ``UQ(0,8)`` payload (``:=``)."""
    return v.assign_to(M_FACTOR)


def model(t):
    """One transaction through the eight stages; returns the output fields."""
    cfg = t["config"]
    iterated = t["iterated"]
    texture = t["texture"]
    color0 = t["color0"]
    color1 = t["color1"]

    # -- n0: widen and select -------------------------------------------
    texture_alpha = s9(t["texture_alpha"])
    texture_rgb = tuple(s9(v) for v in texture)

    rgb_sel = cfg["rgb_sel"]
    if rgb_sel == RgbSel.ITERATED:
        c_other = tuple(s9(v) for v in iterated)
    elif rgb_sel == RgbSel.TEXTURE:
        c_other = tuple(s9(v) for v in texture)
    elif rgb_sel == RgbSel.COLOR1:
        c_other = tuple(s9(v) for v in color1)
    else:                                   # LFB (the switch's last arm)
        c_other = (0, 0, 0)

    if cfg["local_select_override"] and (t["texture_alpha"] >> 7) & 1:
        c_local = tuple(s9(v) for v in color0)
    elif cfg["local_select"] == LocalSel.ITERATED:
        c_local = tuple(s9(v) for v in iterated)
    else:                                   # COLOR0 (last arm)
        c_local = tuple(s9(v) for v in color0)

    alpha_sel = cfg["alpha_sel"]
    if alpha_sel == AlphaSel.ITERATED:
        a_other = s9(t["iterated_alpha"])
    elif alpha_sel == AlphaSel.TEXTURE:
        a_other = s9(t["texture_alpha"])
    elif alpha_sel == AlphaSel.COLOR1:
        a_other = s9(t["color1_alpha"])
    else:                                   # LFB (last arm)
        a_other = 0

    als = cfg["alpha_local_select"]
    if als == AlphaLocalSel.ITERATED:
        a_local = s9(t["iterated_alpha"])
    elif als == AlphaLocalSel.COLOR0:
        a_local = s9(t["color0_alpha"])
    else:                                   # ITERATED_Z, and code 3 (last arm)
        a_local = s9(t["iterated_z"])

    # -- n1: zero other -------------------------------------------------
    c_after_zero = (0, 0, 0) if cfg["zero_other"] else c_other
    a_after_zero = 0 if cfg["alpha_zero_other"] else a_other

    # -- n2: subtract local (SQ(10,0)) ----------------------------------
    if cfg["sub_clocal"]:
        c_after_sub = tuple(afix_sint(sint9(z - l), 9).assign_to(M_AFTER_SUB)
                            for z, l in zip(c_after_zero, c_local))
    else:
        c_after_sub = tuple(afix_sint(z, 9).assign_to(M_AFTER_SUB) for z in c_after_zero)
    if cfg["alpha_sub_clocal"]:
        a_after_sub = afix_sint(sint9(a_after_zero - a_local), 9).assign_to(M_AFTER_SUB)
    else:
        a_after_sub = afix_sint(a_after_zero, 9).assign_to(M_AFTER_SUB)

    # -- n3: select multiply factor (UQ(0,8)) ---------------------------
    zero_factor = afix_const(M_FACTOR, 0)
    ms = cfg["mselect"]
    if ms == MSelect.ZERO:
        c_factor_pre = (zero_factor,) * 3
    elif ms == MSelect.CLOCAL:
        c_factor_pre = tuple(clamp_sint_to_factor(v) for v in c_local)
    elif ms == MSelect.AOTHER:
        c_factor_pre = (factor_pre_to_payload(clamp_afix_to_factor(a_after_sub)),) * 3
    elif ms == MSelect.ALOCAL:
        c_factor_pre = (clamp_sint_to_factor(a_local),) * 3
    elif ms == MSelect.TEXTURE_ALPHA:
        c_factor_pre = (clamp_sint_to_factor(texture_alpha),) * 3
    else:                                   # TEXTURE_RGB, and codes 6/7 (last arm)
        c_factor_pre = tuple(clamp_sint_to_factor(v) for v in texture_rgb)

    ams = cfg["alpha_mselect"]
    if ams == MSelect.ZERO:
        a_factor_pre = zero_factor
    elif ams == MSelect.CLOCAL:
        a_factor_pre = clamp_sint_to_factor(a_local)     # sic: A_LOCAL, not C_LOCAL
    elif ams == MSelect.AOTHER:
        a_factor_pre = factor_pre_to_payload(clamp_afix_to_factor(a_after_sub))
    elif ams == MSelect.ALOCAL:
        a_factor_pre = clamp_sint_to_factor(a_local)
    elif ams == MSelect.TEXTURE_ALPHA:
        a_factor_pre = clamp_sint_to_factor(texture_alpha)
    else:                                   # TEXTURE_RGB, and codes 6/7 (last arm)
        a_factor_pre = clamp_sint_to_factor(texture_alpha)

    # -- n4: reverse blend (UQ(1,8)) ------------------------------------
    c_factor = tuple(invert_factor(f, cfg["reverse_blend"]) for f in c_factor_pre)
    a_factor = invert_factor(a_factor_pre, cfg["alpha_reverse_blend"])

    # -- n5: multiply -> SQ(10,8), saturated ----------------------------
    c_after_mul = tuple((s * f).fix_to(M_MUL) for s, f in zip(c_after_sub, c_factor))
    a_after_mul = (a_after_sub * a_factor).fix_to(M_MUL)

    # -- n6: add --------------------------------------------------------
    def local_afix(v):
        return afix_sint(v, 9).fix_to(M_MUL)

    add = cfg["add"]
    if add == AddMode.NONE:
        c_after_add = c_after_mul
    elif add == AddMode.CLOCAL:
        c_after_add = tuple((mul + local_afix(l)).fix_to(M_MUL)
                            for mul, l in zip(c_after_mul, c_local))
    else:                                   # ALOCAL, and code 3 (last arm)
        c_after_add = tuple((mul + local_afix(a_local)).fix_to(M_MUL) for mul in c_after_mul)

    aadd = cfg["alpha_add"]
    if aadd == AddMode.NONE:
        a_after_add = a_after_mul
    else:                                   # CLOCAL and ALOCAL both add A_LOCAL
        a_after_add = (a_after_mul + local_afix(a_local)).fix_to(M_MUL)

    # -- n7: clamp ------------------------------------------------------
    c_clamped = tuple(clamp_afix_to_u8(v) for v in c_after_add)
    a_clamped = clamp_afix_to_u8(a_after_add)

    # -- n8: invert output ----------------------------------------------
    color = tuple((v ^ 0xFF) if cfg["invert_output"] else v for v in c_clamped)
    alpha = (a_clamped ^ 0xFF) if cfg["alpha_invert_output"] else a_clamped

    return {
        "color": color,
        "alpha": alpha,
        "coords": t["coords"],
        "depth": t["depth"],
        "iterated_alpha": t["iterated_alpha"],
        "raw_w": t["raw_w"],
        "fog_color": t["fog_color"],
        "chroma_key": t["chroma_key"],
        "za_color": t["za_color"],
        "alpha_mode": t["alpha_mode"],
        "fog_mode": t["fog_mode"],
        "fbz_mode": t["fbz_mode"],
        "routing": t["routing"],
    }


# ---------------------------------------------------------------------------
# stimulus
# ---------------------------------------------------------------------------

CONFIG_FIELDS = ColorCombineConfig.FIELDS
CONFIG_WIDTHS = {name: Shape.cast(ColorCombineConfig()[name].shape).width for name in CONFIG_FIELDS}


def passthrough_config(**overrides):
    """``setPassthroughConfig`` from the Scala test, with overrides."""
    cfg = {
        "rgb_sel": RgbSel.ITERATED,
        "alpha_sel": AlphaSel.ITERATED,
        "local_select": LocalSel.ITERATED,
        "alpha_local_select": AlphaLocalSel.ITERATED,
        "local_select_override": 0,
        "zero_other": 0,
        "sub_clocal": 0,
        "mselect": MSelect.ZERO,
        "reverse_blend": 0,
        "add": AddMode.NONE,
        "invert_output": 0,
        "alpha_zero_other": 0,
        "alpha_sub_clocal": 0,
        "alpha_mselect": MSelect.ZERO,
        "alpha_reverse_blend": 0,
        "alpha_add": AddMode.NONE,
        "alpha_invert_output": 0,
        "texture_enable": 0,
    }
    unknown = set(overrides) - set(cfg)
    assert not unknown, unknown
    cfg.update(overrides)
    return cfg


def default_tx(**overrides):
    """``setDefaultInput`` from the Scala test, with overrides."""
    t = {
        "coords": (100, 200),
        "iterated": (128, 64, 32),
        "iterated_alpha": 255,
        "iterated_z": 100,
        "depth": 0x0000_0800,           # 0.5 in SQ(32,12): 0.5 * 2^12
        "raw_w": 0,
        "texture": (0, 0, 0),
        "texture_alpha": 0,
        "color0": (200, 150, 100),
        "color1": (50, 75, 100),
        "color0_alpha": 0,
        "color1_alpha": 0,
        "fog_color": 0,
        "chroma_key": 0,
        "za_color": 0,
        "config": passthrough_config(),
        "alpha_mode": 0,
        "fog_mode": 0,
        "fbz_mode": 0,
        "routing": 0,
    }
    cfg = overrides.pop("config", None)
    unknown = set(overrides) - set(t)
    assert not unknown, unknown
    t.update(overrides)
    if cfg is not None:
        t["config"] = cfg
    return t


def random_config(rng, *, full_codes=True):
    """Every select at a random code.

    ``full_codes`` draws each enum over its *bit width* rather than its
    enumerant count, so the out-of-range codes the Spinal ``switch`` sends to
    its last arm are exercised too.
    """
    cfg = {}
    for name in CONFIG_FIELDS:
        cfg[name] = rng.randrange(1 << CONFIG_WIDTHS[name])
    if not full_codes:
        cfg["rgb_sel"] = rng.randrange(4)
        cfg["alpha_sel"] = rng.randrange(4)
        cfg["local_select"] = rng.randrange(2)
        cfg["alpha_local_select"] = rng.randrange(3)
        cfg["mselect"] = rng.randrange(6)
        cfg["alpha_mselect"] = rng.randrange(6)
        cfg["add"] = rng.randrange(3)
        cfg["alpha_add"] = rng.randrange(3)
    return cfg


def random_tx(rng, c, **config_overrides):
    rgb = lambda: (rng.randrange(256), rng.randrange(256), rng.randrange(256))  # noqa: E731
    cfg = random_config(rng)
    cfg.update(config_overrides)
    return {
        "coords": (rng.randrange(1 << 12), rng.randrange(1 << 12)),
        "iterated": rgb(),
        "iterated_alpha": rng.randrange(256),
        "iterated_z": rng.randrange(256),
        "depth": rng.randrange(1 << 32),
        "raw_w": rng.randrange(1 << 32),
        "texture": rgb(),
        "texture_alpha": rng.randrange(256),
        "color0": rgb(),
        "color1": rgb(),
        "color0_alpha": rng.randrange(256),
        "color1_alpha": rng.randrange(256),
        "fog_color": rng.randrange(1 << 32),
        "chroma_key": rng.randrange(1 << 32),
        "za_color": rng.randrange(1 << 32),
        "config": cfg,
        "alpha_mode": rng.randrange(1 << 32),
        "fog_mode": rng.randrange(1 << 6),
        "fbz_mode": rng.randrange(1 << 21),
        "routing": rng.randrange(1 << (2 * c.address_width + 11)),
    }


# ---------------------------------------------------------------------------
# pysim plumbing
# ---------------------------------------------------------------------------


def _set_input(ctx, dut, t):
    p = dut.input.payload
    ctx.set(p.coords.x, sx(t["coords"][0], 12))
    ctx.set(p.coords.y, sx(t["coords"][1], 12))
    for color, src in (("iterated", t["iterated"]), ("texture", t["texture"]),
                       ("color0", t["color0"]), ("color1", t["color1"])):
        view = getattr(p, color)
        ctx.set(view.r, src[0])
        ctx.set(view.g, src[1])
        ctx.set(view.b, src[2])
    ctx.set(p.iterated_alpha, t["iterated_alpha"])
    ctx.set(p.iterated_z, t["iterated_z"])
    ctx.set(p.depth.as_value(), sx(t["depth"], 32))
    ctx.set(p.raw_w, sx(t["raw_w"], 32))
    ctx.set(p.texture_alpha, t["texture_alpha"])
    ctx.set(p.color0_alpha, t["color0_alpha"])
    ctx.set(p.color1_alpha, t["color1_alpha"])
    ctx.set(p.fog_color, t["fog_color"])
    ctx.set(p.chroma_key, t["chroma_key"])
    ctx.set(p.za_color, t["za_color"])
    for name in CONFIG_FIELDS:
        ctx.set(getattr(p.config, name), t["config"][name])
    ctx.set(p.alpha_mode.as_value(), t["alpha_mode"])
    ctx.set(p.fog_mode.as_value(), t["fog_mode"])
    ctx.set(p.fbz_mode.as_value(), t["fbz_mode"])
    ctx.set(p.routing.as_value(), t["routing"])


def _out_signals(dut):
    p = dut.output.payload
    return (dut.output.valid, p.color.r, p.color.g, p.color.b, p.alpha,
            p.coords.x, p.coords.y, p.depth.as_value(), p.iterated_alpha, p.raw_w,
            p.fog_color, p.chroma_key, p.za_color,
            p.alpha_mode.as_value(), p.fog_mode.as_value(), p.fbz_mode.as_value(),
            p.routing.as_value())


def _as_dict(sample):
    (valid, r, g, b, alpha, x, y, depth, ia, raw_w, fog, ck, za, am, fm, fbz, routing) = sample
    return {
        "valid": valid,
        "color": (r, g, b),
        "alpha": alpha,
        "coords": (u(x, 12), u(y, 12)),
        "depth": u(depth, 32),
        "iterated_alpha": ia,
        "raw_w": u(raw_w, 32),
        "fog_color": fog,
        "chroma_key": ck,
        "za_color": za,
        "alpha_mode": am,
        "fog_mode": fm,
        "fbz_mode": fbz,
        "routing": routing,
    }


def run_stream(transactions, *, ready_pattern=None, valid_pattern=None, c=None):
    """Push ``transactions`` through the DUT; return the payloads it emitted."""
    c = c if c is not None else Config.voodoo1()
    dut = ColorCombine(c)
    got = []

    async def bench(ctx):
        pending = list(transactions)
        cycle = 0
        outs = _out_signals(dut)
        budget = 10 * (len(transactions) + LATENCY) + 64
        while (pending or len(got) < len(transactions)) and cycle < budget:
            ready = 1 if ready_pattern is None else int(ready_pattern(cycle))
            ctx.set(dut.output.ready, ready)
            present = bool(pending) and (valid_pattern is None or valid_pattern(cycle))
            if pending:
                _set_input(ctx, dut, pending[0])
            ctx.set(dut.input.valid, int(present))
            accepted = present and ctx.get(dut.input.ready)
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


def check(transactions, **kwargs):
    got = run_stream(transactions, **kwargs)
    for i, (t, g) in enumerate(zip(transactions, got)):
        want = model(t)
        for key, value in want.items():
            assert g[key] == value, (
                f"beat {i} field {key}: expected {value!r}, got {g[key]!r}\n"
                f"  config = {t['config']}\n  tx = {t}")
    return got


def rgba(transactions, **kwargs):
    got = check(transactions, **kwargs)
    return [(*g["color"], g["alpha"]) for g in got]


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------


def test_pipeline_structure_is_nine_nodes_eight_stage_links():
    """8 valid registers, latency 8 and exactly the 150 live payload slots."""
    from amaranth.back import rtlil

    dut = ColorCombine()
    rtlil.convert(dut, name="ColorCombine")       # elaborate
    r = dut.report
    assert r.latency == 8
    assert r.valid_register_count == 8
    assert r.payload_register_slots == 150
    assert len(dut.payloads) == 34                 # TRACE excluded: c.trace.enabled is off

    from tests.test_pipeline import COLOR_COMBINE_TOUCHES

    for name, touched in COLOR_COMBINE_TOUCHES.items():
        live = r.live_range(dut.payloads[name])
        assert len(live) == max(touched) - min(touched), name
        assert [link.up.name for link in live] == [f"n{k}" for k in range(min(touched), max(touched))], name


def test_trace_payload_appears_only_when_enabled():
    from dataclasses import replace

    from amaranth.back import rtlil

    c = Config.voodoo1()
    c_trace = replace(c, trace=replace(c.trace, enabled=True))
    dut = ColorCombine(c_trace)
    rtlil.convert(dut, name="ColorCombineTrace")
    assert "TRACE" in dut.payloads
    assert len(dut.payloads) == 35
    assert dut.report.payload_register_slots == 158   # +8 for TRACE n0..n8


# ---------------------------------------------------------------------------
# helpers pinned on their own
# ---------------------------------------------------------------------------


def test_dead_aother_factor_is_zero_in_the_model():
    """§8.1: ``clampAFixToFactor`` returns a zero-bit AFix, i.e. constant 0."""
    for raw in (-1024, -1, 0, 1, 100, 255, 256, 1023):
        v = ModelValue(M_AFTER_SUB, raw)
        dead = clamp_afix_to_factor(v)
        assert dead.shape.bit_width == 0
        assert factor_pre_to_payload(dead).raw == 0


def test_invert_factor_model_matches_86box():
    for f in range(256):
        assert invert_factor(ModelValue(M_FACTOR, f), True).raw == f + 1
        assert invert_factor(ModelValue(M_FACTOR, f), False).raw == (f ^ 0xFF) + 1


def test_clamp_afix_to_u8_model_floors_after_saturating():
    def raw(v):
        return ModelValue(M_MUL, v)
    assert clamp_afix_to_u8(raw(-1)) == 0
    assert clamp_afix_to_u8(raw(0)) == 0
    assert clamp_afix_to_u8(raw(255)) == 0          # 255/256 floors to 0
    assert clamp_afix_to_u8(raw(256)) == 1
    assert clamp_afix_to_u8(raw(65535)) == 255      # 255.996 survives the sat
    assert clamp_afix_to_u8(raw(65536)) == 255      # 256.0 saturates
    assert clamp_afix_to_u8(raw(262143)) == 255


# ---------------------------------------------------------------------------
# ColorCombineTest.scala, case by case
# ---------------------------------------------------------------------------


def test_gouraud_shading_passthrough():
    """zero_other + add=CLOCAL(iterated) passes the iterated colour through."""
    t = default_tx(config=passthrough_config(
        zero_other=1, add=AddMode.CLOCAL, local_select=LocalSel.ITERATED,
        alpha_zero_other=1, alpha_add=AddMode.CLOCAL))
    assert rgba([t]) == [(128, 64, 32, 255)]


def test_color1_selection():
    """rgb_sel=COLOR1 x factor 255/256 (ALOCAL=255, reverse_blend=1)."""
    t = default_tx(config=passthrough_config(
        rgb_sel=RgbSel.COLOR1, zero_other=0, mselect=MSelect.ALOCAL,
        reverse_blend=1, local_select=LocalSel.ITERATED))
    (r, g, b, _a), = rgba([t])
    # color1 = (50, 75, 100) * 256/256 -- the +1 makes 255 an exact 1.0
    assert (r, g, b) == (50, 75, 100)


def test_invert_output():
    t = default_tx(config=passthrough_config(
        zero_other=1, add=AddMode.CLOCAL, local_select=LocalSel.ITERATED, invert_output=1,
        alpha_zero_other=1, alpha_add=AddMode.CLOCAL, alpha_invert_output=1))
    assert rgba([t]) == [(127, 191, 223, 0)]


def test_multiply_by_local_color():
    """iterated * color0 >> 8 with reverse_blend=1: (128*201, 64*151, 32*101) >> 8."""
    t = default_tx(config=passthrough_config(
        rgb_sel=RgbSel.ITERATED, local_select=LocalSel.COLOR0, zero_other=0,
        mselect=MSelect.CLOCAL, reverse_blend=1, add=AddMode.NONE))
    (r, g, b, _a), = rgba([t])
    # The unconditional +1 on the factor: 128*201/256 = 100.5 -> 100, 64*151/256 = 37.75 -> 37,
    # 32*101/256 = 12.6 -> 12.  Same integers the Scala test asserts.
    assert (r, g, b) == (100, 37, 12)


def test_subtract_and_add_local():
    """(iter - color0) * 1/256 + color0, with the per-channel -1 truncation."""
    t = default_tx(config=passthrough_config(
        rgb_sel=RgbSel.ITERATED, local_select=LocalSel.COLOR0, sub_clocal=1,
        mselect=MSelect.ZERO, reverse_blend=1, add=AddMode.CLOCAL))
    (r, g, b, _a), = rgba([t])
    assert (r, g, b) == (199, 149, 99)


def test_clamping_negative_values():
    t = default_tx(iterated=(50, 50, 50), color0=(200, 200, 200),
                   config=passthrough_config(
                       rgb_sel=RgbSel.ITERATED, local_select=LocalSel.COLOR0, sub_clocal=1,
                       mselect=MSelect.ZERO, add=AddMode.NONE))
    (r, g, b, _a), = rgba([t])
    # (50 - 200) * (0^0xFF + 1 = 256)/256 = -150 -> clamped to 0
    assert (r, g, b) == (0, 0, 0)


def test_clamping_overflow_values():
    t = default_tx(iterated=(200, 200, 200), color0=(200, 200, 200),
                   config=passthrough_config(
                       rgb_sel=RgbSel.ITERATED, local_select=LocalSel.COLOR0,
                       mselect=MSelect.ALOCAL, reverse_blend=1, add=AddMode.CLOCAL))
    (r, g, b, _a), = rgba([t])
    assert (r, g, b) == (255, 255, 255)


def test_reverse_blend():
    """factor = ALOCAL = 255, reverse_blend=0 -> ~255 + 1 = 1/256 -> 128/256 floors to 0."""
    t = default_tx(config=passthrough_config(
        rgb_sel=RgbSel.ITERATED, mselect=MSelect.ALOCAL, reverse_blend=0, add=AddMode.NONE))
    (r, g, b, _a), = rgba([t])
    assert (r, g, b) == (0, 0, 0)


def test_coordinates_and_pass_throughs_arrive_unchanged():
    t = default_tx(coords=(123, 456), depth=0xDEADBEEF, raw_w=0x8000_0001,
                   fog_color=0x11223344, chroma_key=0x55667788, za_color=0x99AABBCC,
                   alpha_mode=0xA5A5A5A5, fog_mode=0x2B, fbz_mode=0x1F0F0F,
                   routing=(0x3FFFF << 22) | 0x2AAAA)
    got, = check([t])
    assert got["coords"] == (123, 456)
    assert got["depth"] == 0xDEADBEEF
    assert got["raw_w"] == 0x8000_0001


def test_stream_backpressure_holds_the_beat():
    """Scala's "Stream backpressure": ready low for a while, data not lost."""
    rng = random.Random(7)
    c = Config.voodoo1()
    txs = [random_tx(rng, c) for _ in range(24)]
    # ready low for the first 12 cycles, then a 1-in-3 duty cycle
    check(txs, ready_pattern=lambda k: k >= 12 and k % 3 == 0)


def test_latency_is_eight_cycles():
    dut = ColorCombine()
    t = default_tx()

    async def bench(ctx):
        ctx.set(dut.output.ready, 1)
        _set_input(ctx, dut, t)
        ctx.set(dut.input.valid, 1)
        seen = []
        for _ in range(12):
            _clk, _rst, valid = await ctx.tick().sample(dut.output.valid)
            seen.append(int(valid))
        assert seen == [0] * LATENCY + [1] * (12 - LATENCY), seen

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(bench)
    sim.run()


# ---------------------------------------------------------------------------
# sweeps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mselect", range(8))
def test_every_mselect_code(mselect):
    """All six MSelect enumerants plus the codes 6 and 7 that fall into the last arm."""
    rng = random.Random(100 + mselect)
    c = Config.voodoo1()
    txs = [random_tx(rng, c, mselect=mselect, alpha_mselect=mselect,
                     zero_other=0, alpha_zero_other=0) for _ in range(40)]
    check(txs)


@pytest.mark.parametrize("add", range(4))
def test_every_add_code(add):
    rng = random.Random(200 + add)
    c = Config.voodoo1()
    txs = [random_tx(rng, c, add=add, alpha_add=add) for _ in range(40)]
    check(txs)


def test_aother_factor_is_dead_in_hardware():
    """§8.1 end to end: MSelect.AOTHER gives factor 0 (or 256 when not reverse-blended)."""
    rng = random.Random(3)
    c = Config.voodoo1()
    for reverse_blend in (0, 1):
        txs = [random_tx(rng, c, mselect=MSelect.AOTHER, alpha_mselect=MSelect.AOTHER,
                         reverse_blend=reverse_blend, alpha_reverse_blend=reverse_blend,
                         zero_other=0, alpha_zero_other=0, sub_clocal=1, alpha_sub_clocal=1,
                         add=AddMode.NONE, alpha_add=AddMode.NONE, invert_output=0,
                         alpha_invert_output=0)
               for _ in range(30)]
        got = check(txs)
        if reverse_blend:
            # factor = 0 + 1 = 1/256: |sub| < 256 so every channel floors to 0
            assert all(g["color"] == (0, 0, 0) and g["alpha"] == 0 for g in got)
        else:
            # factor = ~0 + 1 = 256/256 = 1.0: the (clamped) subtraction result itself
            for t, g in zip(txs, got):
                want = model(t)
                assert g["color"] == want["color"]


def test_local_select_override_takes_color0_on_texture_alpha_msb():
    rng = random.Random(11)
    c = Config.voodoo1()
    txs = []
    for ta in (0x00, 0x7F, 0x80, 0xFF):
        for ls in (LocalSel.ITERATED, LocalSel.COLOR0):
            t = random_tx(rng, c, local_select_override=1, local_select=ls,
                          zero_other=1, sub_clocal=0, add=AddMode.CLOCAL, invert_output=0)
            t["texture_alpha"] = ta
            txs.append(t)
    got = check(txs)
    for t, g in zip(txs, got):
        expect_color0 = (t["texture_alpha"] >> 7) & 1 or t["config"]["local_select"] != LocalSel.ITERATED
        assert g["color"] == (t["color0"] if expect_color0 else t["iterated"])


def test_random_stream_matches_the_model():
    rng = random.Random(1)
    c = Config.voodoo1()
    check([random_tx(rng, c) for _ in range(400)])


def test_random_stream_with_backpressure():
    rng = random.Random(2)
    c = Config.voodoo1()
    ready = random.Random(3)
    valid = random.Random(4)
    check([random_tx(rng, c) for _ in range(200)],
          ready_pattern=lambda k: ready.random() < 0.4,
          valid_pattern=lambda k: valid.random() < 0.7)
