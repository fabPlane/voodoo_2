"""``avoodoo.raster.triangle_setup`` against a Python model of the setup math.

Ports the intent of ``ext/SpinalVoodoo/test/TriangleSetupTest.scala`` (17
SpinalSim cases: bounding boxes, the top-left rule, edge values at the
bounding-box corner, and five real trace triangles at a widened vertex
format) and of ``ext/SpinalVoodoo/test/TriangleSetupFormalTest.scala`` (three
bounding-box invariants, checked here every cycle ``o.valid`` is high under
the same assumptions the formal harness makes).

The upstream Scala tests are almost all loose: ``abs(x - y) < 5000.0``,
"non-zero for a non-degenerate edge", "edge >= -1 at an interior point".
They are kept because they say what the block is *for*, but the test that
actually pins the port is :func:`test_model_matches_hardware_random`, which
compares **every** output bit against :class:`SetupModel` -- an independent
re-derivation of the Scala built on :mod:`avoodoo.lib.fixed_model` (the
Amaranth-free AFix reference), not on :mod:`avoodoo.lib.fixed`.  Where the
model and the RTL share a helper they share only the *spec*; the two
implementations are written separately.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

import pytest
from amaranth.hdl import Fragment
from amaranth.sim import Simulator

from avoodoo.config import SQ, Config
from avoodoo.lib.fixed_model import ModelShape, ModelValue, const, mux
from avoodoo.raster.triangle_setup import EDGE_PAIRS, ITER_CHANNELS, SetupState, TriangleSetup

# ---------------------------------------------------------------------------
# integer helpers -- Spinal SInt/UInt width semantics, written independently
# of avoodoo.raster.triangle_setup's versions
# ---------------------------------------------------------------------------


def sext(value: int, width: int) -> int:
    """Interpret the low ``width`` bits of ``value`` as two's complement."""
    value &= (1 << width) - 1
    return value - (1 << width) if value >> (width - 1) else value


def zext(value: int, width: int) -> int:
    return value & ((1 << width) - 1)


def mul_by_subpixel(value: int, value_width: int, coeff: int, coeff_width: int) -> int:
    """``mulBySubpixel`` (``TriangleSetup.scala:62-70``)."""
    width = value_width + coeff_width
    total = 0
    for bit in range(coeff_width):
        term = sext(value << bit, width) if (coeff >> bit) & 1 else 0
        total = sext(total + term, width)
    return total


def signed_magnitude(value: int, width: int) -> int:
    """``signedMagnitude`` (scala:72-79).  Result is ``width`` bits unsigned."""
    wide = sext(value, width + 1)
    return zext(-wide, width) if wide < 0 else zext(wide, width)


def sign_adjust(value: int, negate: bool, width: int) -> int:
    """``signAdjust`` (scala:81-84)."""
    resized = sext(value, width)
    return sext(-resized, width) if negate else resized


def scale_by_chunk(term: int, chunk: int, width: int) -> int:
    """``scaleByChunk`` (scala:86-101)."""
    if chunk == 1:
        return term
    if chunk == 2:
        return sext(term << 1, width)
    if chunk == 3:
        return sext(term + sext(term << 1, width), width)
    return 0


# ---------------------------------------------------------------------------
# the reference model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupInput:
    """Everything ``TriangleSetup.Input`` carries that changes the output.

    Raw bit patterns, exactly as they appear on the wire.  ``config`` fields
    other than ``paramAdjust`` are pass-through and are not modelled.
    """

    tri: tuple  # ((x0, y0), (x1, y1), (x2, y2)) as raw vertexFormat words
    sign_bit: int = 0
    param_adjust: int = 0
    #: channel name -> (start, dx, dy) raw words, in ITER_CHANNELS order
    channels: dict = None

    def channel(self, name: str) -> tuple:
        if not self.channels:
            return (0, 0, 0)
        return self.channels.get(name, (0, 0, 0))


@dataclass(frozen=True)
class SetupOutput:
    xrange: tuple
    yrange: tuple
    coeffs: tuple   # three (a, b, c) triples of raw words
    edge_start: tuple
    starts: dict    # channel name -> raw word


class SetupModel:
    """Cycle-independent model of ``Prep`` + the eight ``Iterate`` steps."""

    def __init__(self, c: Config):
        self.c = c
        self.V = ModelShape.from_q(c.vertex_format)
        self.C = ModelShape.from_q(c.coefficient_format)
        #: start-value format per ITER_CHANNELS entry
        fmts = {
            "redStart": c.v_color_format,
            "greenStart": c.v_color_format,
            "blueStart": c.v_color_format,
            "depthStart": c.v_depth_format,
            "alphaStart": c.v_color_format,
            "wStart": c.w_accum_format,
            "sStart": c.tex_coords_accum_format,
            "tStart": c.tex_coords_accum_format,
            "hiSStart": c.tex_coords_hi_format,
            "hiTStart": c.tex_coords_hi_format,
            "hiAlphaStart": c.tex_coords_hi_format,
        }
        self.channel_shapes = {k: ModelShape.from_q(v) for k, v in fmts.items()}

    # -- helpers -----------------------------------------------------------

    def _v(self, raw_bits: int) -> ModelValue:
        return ModelValue(self.V, self.V.wrap(raw_bits))

    def run(self, inp: SetupInput) -> SetupOutput:
        V, C = self.V, self.C
        tri = [[self._v(inp.tri[v][a]) for a in range(2)] for v in range(3)]

        # -- scala:226-231 ------------------------------------------------
        xmin = tri[0][0].min(tri[1][0]).min(tri[2][0])
        xmax = tri[0][0].max(tri[1][0]).max(tri[2][0])
        xrange0 = xmin.floor(0).fix_to(V)
        xrange1 = xmax.ceil(0).fix_to(V)
        yrange0 = tri[0][1].round_half_down(0).fix_to(V)
        yrange1 = tri[2][1].round_half_down(0).fix_to(V)

        # -- scala:233-261 ------------------------------------------------
        zero_c = ModelValue(C, 0)
        bias_c = ModelValue(C, 1)
        coeffs = []
        for i0, i1 in EDGE_PAIRS:
            v0, v1 = tri[i0], tri[i1]
            a_raw = v0[1] - v1[1]
            b_raw = v1[0] - v0[0]
            c_raw = v0[0] * v1[1] - v1[0] * v0[1]
            neg = bool(inp.sign_bit)
            a_fixed = mux(neg, -a_raw, a_raw).fix_to(C)
            b_fixed = mux(neg, -b_raw, b_raw).fix_to(C)
            c_fixed = mux(neg, -c_raw, c_raw).fix_to(C)
            is_top_left = (a_fixed > zero_c) or (a_fixed.eq_op(zero_c) and b_fixed > zero_c)
            c_final = c_fixed if is_top_left else (c_fixed - bias_c).fix_to(C)
            coeffs.append((a_fixed, b_fixed, c_final))

        # -- scala:263-270 ------------------------------------------------
        half = const(ModelShape.sq(3, 4), 0.5)
        x_center = (xrange0 + half).fix_to(V)
        y_center = (yrange0 + half).fix_to(V)
        edge_start = [
            (a * x_center + b * y_center + cc).assign_to(C, truncated=True)
            for a, b, cc in coeffs
        ]

        # -- scala:272-283 ------------------------------------------------
        frac_ax = zext(tri[0][0].bits(), 4)
        frac_ay = zext(tri[0][1].bits(), 4)
        dx_sub_raw = zext(8 - zext(frac_ax, 5), 4)
        dy_sub_raw = zext(8 - zext(frac_ay, 5), 4)

        start_x_pix = xrange0.floor(0).raw           # 12-bit signed
        start_y_pix = yrange0.floor(0).raw
        a_x_pix = tri[0][0].round_half_down(0).raw   # 13-bit signed
        a_y_pix = tri[0][1].round_half_down(0).raw
        dx_pix = sext(start_x_pix - a_x_pix, 13)     # `SInt - SInt` wraps at 13
        dy_pix = sext(start_y_pix - a_y_pix, 13)
        dx_neg = dx_pix < 0
        dy_neg = dy_pix < 0
        dx_mag = signed_magnitude(dx_pix, 16)
        dy_mag = signed_magnitude(dy_pix, 16)

        # -- scala:103-135, the eleven IterChannels -----------------------
        starts = {}
        for name in ITER_CHANNELS:
            shape = self.channel_shapes[name]
            sw = shape.bit_width
            aw = sw + 18
            start_bits, dx_bits, dy_bits = inp.channel(name)
            start_bits = zext(start_bits, sw)
            dx_raw = sext(dx_bits, sw)
            dy_raw = sext(dy_bits, sw)

            corr = sext(
                mul_by_subpixel(dx_raw, sw, dx_sub_raw, 4)
                + mul_by_subpixel(dy_raw, sw, dy_sub_raw, 4),
                sw + 4,
            ) >> 4
            corrected_bits = zext(sext(sext(start_bits, sw) + corr, sw), sw)
            adjusted = corrected_bits if inp.param_adjust else start_bits

            acc = sext(adjusted, sw)
            dx_term = sign_adjust(dx_raw, dx_neg, aw)
            dy_term = sign_adjust(dy_raw, dy_neg, aw)
            dxm, dym = dx_mag, dy_mag
            for _ in range(8):
                acc = sext(
                    acc
                    + scale_by_chunk(dx_term, dxm & 3, aw)
                    + scale_by_chunk(dy_term, dym & 3, aw),
                    aw,
                )
                dx_term = sext(dx_term << 2, aw)
                dy_term = sext(dy_term << 2, aw)
                dxm >>= 2
                dym >>= 2
            starts[name] = zext(acc, sw)

        return SetupOutput(
            xrange=(xrange0.bits(), xrange1.bits()),
            yrange=(yrange0.bits(), yrange1.bits()),
            coeffs=tuple((a.bits(), b.bits(), cc.bits()) for a, b, cc in coeffs),
            edge_start=tuple(e.bits() for e in edge_start),
            starts=starts,
        )


# ---------------------------------------------------------------------------
# simulation plumbing
# ---------------------------------------------------------------------------

CFG = Config.voodoo1()
#: ``TriangleSetupTest.testLargeTriangle``'s widened config (scala:400).
CFG_LARGE = replace(CFG, vertex_format=SQ(20, 4), coefficient_format=SQ(40, 8))

#: `i.fire` -> `o.valid`: Idle -> Prep -> 8 x Iterate -> Done.
LATENCY = 10

#: channel name -> the payload fields it reads and the output field it drives.
_CHANNEL_PORTS = {
    "redStart": ("grads", "red_grad"),
    "greenStart": ("grads", "green_grad"),
    "blueStart": ("grads", "blue_grad"),
    "depthStart": ("grads", "depth_grad"),
    "alphaStart": ("grads", "alpha_grad"),
    "wStart": ("grads", "w_grad"),
    "sStart": ("grads", "s_grad"),
    "tStart": ("grads", "t_grad"),
    "hiSStart": ("tex_hi", "s_start"),
    "hiTStart": ("tex_hi", "t_start"),
    "hiAlphaStart": ("hi_alpha", "start"),
}


def _channel_raws(payload, name):
    """``(start, dx, dy)`` raw words of one channel inside a payload view."""
    group, field = _CHANNEL_PORTS[name]
    if group == "grads":
        g = getattr(payload.grads, field)
        return g.start.raw, g.d[0].raw, g.d[1].raw
    if group == "tex_hi":
        prefix = "d_sd" if field == "s_start" else "d_td"
        return (
            getattr(payload.tex_hi, field).raw,
            getattr(payload.tex_hi, f"{prefix}_x").raw,
            getattr(payload.tex_hi, f"{prefix}_y").raw,
        )
    return payload.hi_alpha.start.raw, payload.hi_alpha.d_ad_x.raw, payload.hi_alpha.d_ad_y.raw


def _out_start_raw(payload, name):
    group, field = _CHANNEL_PORTS[name]
    if group == "grads":
        return getattr(payload.grads, field).start.raw
    if group == "tex_hi":
        return getattr(payload.tex_hi, field).raw
    return payload.hi_alpha.start.raw


def set_raw(ctx, raw, bits: int) -> None:
    """Drive a bit pattern into an ``AFixValue.raw``.

    ``.raw`` of a signed AFix is an Amaranth ``signed`` value, so a bit
    pattern has to be reinterpreted before it is set; the tests speak in bit
    patterns throughout, because that is what the model returns and what the
    Verilog ports carry.
    """
    if raw.shape().signed:
        bits = sext(bits, len(raw))
    ctx.set(raw, bits)


def get_raw(ctx, raw) -> int:
    """Read an ``AFixValue.raw`` back as an unsigned bit pattern."""
    return zext(ctx.get(raw), len(raw))


def apply_input(ctx, dut, inp: SetupInput) -> None:
    p = dut.i.payload
    for v in range(3):
        for a in range(2):
            set_raw(ctx, p.tri_with_sign.tri[v][a].raw, inp.tri[v][a])
    ctx.set(p.tri_with_sign.sign_bit, inp.sign_bit)
    ctx.set(p.config.fbz_color_path.param_adjust, inp.param_adjust)
    for name in ITER_CHANNELS:
        start, dx, dy = inp.channel(name)
        s_raw, dx_raw, dy_raw = _channel_raws(p, name)
        set_raw(ctx, s_raw, start)
        set_raw(ctx, dx_raw, dx)
        set_raw(ctx, dy_raw, dy)


def read_output(ctx, dut, c: Config) -> SetupOutput:
    p = dut.o.payload
    return SetupOutput(
        xrange=(get_raw(ctx, p.xrange[0].raw), get_raw(ctx, p.xrange[1].raw)),
        yrange=(get_raw(ctx, p.yrange[0].raw), get_raw(ctx, p.yrange[1].raw)),
        coeffs=tuple(
            tuple(get_raw(ctx, getattr(p.coeffs[i], f).raw) for f in "abc")
            for i in range(3)
        ),
        edge_start=tuple(get_raw(ctx, p.edge_start[i].raw) for i in range(3)),
        starts={n: get_raw(ctx, _out_start_raw(p, n)) for n in ITER_CHANNELS},
    )


def run_one(inp: SetupInput, c: Config = CFG) -> SetupOutput:
    """Drive one triangle through the DUT and return its output bundle."""
    dut = TriangleSetup(c)
    captured = []

    async def tb(ctx):
        ctx.set(dut.i.valid, 0)
        ctx.set(dut.o.ready, 0)
        await ctx.tick()
        apply_input(ctx, dut, inp)
        ctx.set(dut.i.valid, 1)
        await ctx.tick()          # i.fire
        ctx.set(dut.i.valid, 0)
        for _ in range(LATENCY - 1):
            assert not ctx.get(dut.o.valid), "o.valid asserted early"
            await ctx.tick()
        assert ctx.get(dut.o.valid), "o.valid did not assert after the setup latency"
        captured.append(read_output(ctx, dut, c))

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()
    return captured[0]


# ---------------------------------------------------------------------------
# fixed-point conveniences for the ported Scala assertions
# ---------------------------------------------------------------------------


def q(value: float, c: Config = CFG) -> int:
    """``dut.i.triWithSign.tri(i)(j) #= (x * 16).toInt``.

    SpinalSim's ``AFix #= Int`` assigns the **value**, not the raw word, so
    the Scala test's "scale factor is 2^4 = 16" does not convert to fixed
    point at all -- it makes every vertex sixteen times bigger.  That is why
    its expected bounding boxes are the scaled numbers (``96.0`` for
    ``x = 6.0``) and why ``fromFixed`` reads them back unchanged.  The port
    reproduces it literally rather than "fixing" the test, so the numbers
    below can be diffed against the Scala line for line.
    """
    scale = 1 << c.vertex_format.fraction
    return zext(int(value * 16) * scale, c.vertex_format.width)


def vertex_value(raw: int, c: Config = CFG) -> float:
    """``fromFixed(v: AFix)`` -- the AFix value, i.e. ``raw * 2**exp``."""
    return sext(raw, c.vertex_format.width) / (1 << c.vertex_format.fraction)


def coeff_value(raw: int, c: Config = CFG) -> float:
    """A coefficient raw word as the ``fromFixed`` double the Scala prints."""
    return sext(raw, c.coefficient_format.width) / (1 << c.coefficient_format.fraction)


def tri_of(vertices, c: Config = CFG):
    return tuple((q(x, c), q(y, c)) for x, y in vertices)


def setup_of(vertices, sign_bit=False, c: Config = CFG, **kw) -> SetupInput:
    return SetupInput(tri=tri_of(vertices, c), sign_bit=int(sign_bit), **kw)


# ---------------------------------------------------------------------------
# TriangleSetupTest.scala -- bounding boxes
# ---------------------------------------------------------------------------

# (name, vertices, signBit, expected xmin/xmax/ymin/ymax as the Scala's
#  scaled-by-16 doubles).  The Scala prints these as `fromFixed(...)`, which
#  is the raw word -- it forgot to divide by 16 -- so the numbers below are
#  raw vertex words, which is also what the hardware carries.
BBOX_CASES = [
    ("simple", [(0.0, 0.0), (6.0, 0.0), (3.0, 6.0)], False, 0.0, 96.0, 0.0, 96.0),
    ("negative", [(-1.5, -1.5), (1.5, -1.5), (0.0, 1.5)], False, -24.0, 24.0, -24.0, 24.0),
    ("obtuse", [(1.0, 0.0), (8.0, 0.0), (3.0, 4.0)], False, 16.0, 128.0, 0.0, 64.0),
    ("acute", [(2.0, 0.0), (6.0, 0.0), (4.0, 3.0)], False, 32.0, 96.0, 0.0, 48.0),
    ("thin", [(5.0, 0.0), (6.0, 4.0), (5.0, 8.0)], False, 80.0, 96.0, 0.0, 128.0),
    ("tall", [(4.0, 0.0), (6.0, 0.0), (5.0, 8.0)], False, 64.0, 96.0, 0.0, 128.0),
    ("upside_down", [(5.0, 0.0), (2.0, 4.0), (8.0, 4.0)], True, 32.0, 128.0, 0.0, 64.0),
]


@pytest.mark.parametrize("name,verts,sign,xmin,xmax,ymin,ymax", BBOX_CASES,
                         ids=[r[0] for r in BBOX_CASES])
def test_bounding_box(name, verts, sign, xmin, xmax, ymin, ymax):
    out = run_one(setup_of(verts, sign))
    assert vertex_value(out.xrange[0]) == pytest.approx(xmin, abs=0.1)
    assert vertex_value(out.xrange[1]) == pytest.approx(xmax, abs=0.1)
    assert vertex_value(out.yrange[0]) == pytest.approx(ymin, abs=0.1)
    assert vertex_value(out.yrange[1]) == pytest.approx(ymax, abs=0.1)


# ---------------------------------------------------------------------------
# TriangleSetupTest.scala -- the edge functions
# ---------------------------------------------------------------------------


def test_edge_equations_right_triangle():
    """``"Edge equations for right triangle"`` -- a non-degenerate edge has
    a non-zero coefficient in the axis it is not parallel to."""
    out = run_one(setup_of([(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)]))
    assert coeff_value(out.coeffs[0][1]) != 0.0   # horizontal edge 0->1: b != 0
    assert coeff_value(out.coeffs[2][0]) != 0.0   # vertical edge 2->0: a != 0


def test_edge_start_matches_corner_evaluation():
    """``"Initial edge values at bounding box corner"``.

    ``edgeStart`` is ``a*xCenter + b*yCenter + c`` truncated into 34 bits, so
    it differs from ``a*xmin + b*ymin + c`` by the half-pixel offset; the
    Scala allows 5000.0 of slack for exactly that.
    """
    out = run_one(setup_of([(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)]))
    xmin = vertex_value(out.xrange[0])
    ymin = vertex_value(out.yrange[0])
    for i in range(3):
        a, b, cc = (coeff_value(v) for v in out.coeffs[i])
        expected = a * xmin + b * ymin + cc
        assert abs(coeff_value(out.edge_start[i]) - expected) < 5000.0


INTERIOR_CASES = [
    ("ccw", [(0.0, 0.0), (4.0, 0.0), (2.0, 2.0)], False, 32.0, 16.0),
    ("cw", [(0.0, 0.0), (2.0, 2.0), (4.0, 0.0)], True, 32.0, 16.0),
    ("large", [(-100.0, -80.0), (100.0, -80.0), (0.0, 80.0)], False, 0.0, 0.0),
    ("obtuse_centroid", [(1.0, 0.0), (8.0, 0.0), (3.0, 4.0)], False,
     (16.0 + 128.0 + 48.0) / 3, (0.0 + 0.0 + 64.0) / 3),
]


@pytest.mark.parametrize("name,verts,sign,px,py", INTERIOR_CASES,
                         ids=[r[0] for r in INTERIOR_CASES])
def test_all_edges_nonnegative_at_interior_point(name, verts, sign, px, py):
    """The winding-sign fix-up must make all three edge functions >= 0 inside.

    This is the property the whole ``signBit`` mux exists for, and the Scala
    checks it for both windings of the same triangle.
    """
    out = run_one(setup_of(verts, sign))
    for i in range(3):
        a, b, cc = (coeff_value(v) for v in out.coeffs[i])
        edge = a * px + b * py + cc
        assert edge >= -1.0, f"edge {i} is {edge} at the interior point"


# ---------------------------------------------------------------------------
# TriangleSetupTest.scala -- real trace triangles at SQ(20,4)/SQ(40,8)
# ---------------------------------------------------------------------------

TRACE_CASES = [
    ("tiny_cw", [(264.69, 148.94), (263.63, 151.38), (267.38, 154.25)], True,
     4218.0, 4278.0, 2383.0, 2468.0),
    ("large_cw_sliver", [(315.31, 123.50), (282.44, 149.50), (315.25, 326.56)], True,
     4519.0, 5045.0, 1976.0, 5225.0),
    ("medium_ccw", [(226.31, 216.00), (261.25, 221.38), (228.88, 233.75)], False,
     3621.0, 4180.0, 3456.0, 3740.0),
    ("very_tiny_ccw", [(263.63, 151.38), (267.38, 154.25), (266.38, 156.56)], False,
     4218.0, 4278.0, 2422.0, 2505.0),
    ("small_wide_cw", [(217.63, 161.56), (184.25, 169.25), (217.00, 173.00)], True,
     2948.0, 3482.0, 2585.0, 2768.0),
]


@pytest.mark.parametrize("name,verts,sign,xmin,xmax,ymin,ymax", TRACE_CASES,
                         ids=[r[0] for r in TRACE_CASES])
def test_trace_triangle(name, verts, sign, xmin, xmax, ymin, ymax):
    c = CFG_LARGE
    out = run_one(setup_of(verts, sign, c=c), c=c)
    assert vertex_value(out.xrange[0], c) == pytest.approx(xmin, abs=2.0)
    assert vertex_value(out.xrange[1], c) == pytest.approx(xmax, abs=2.0)
    assert vertex_value(out.yrange[0], c) == pytest.approx(ymin, abs=2.0)
    assert vertex_value(out.yrange[1], c) == pytest.approx(ymax, abs=2.0)

    cx = sum(v[0] for v in verts) * 16.0 / 3
    cy = sum(v[1] for v in verts) * 16.0 / 3
    for i in range(3):
        a, b, cc = (coeff_value(v, c) for v in out.coeffs[i])
        assert a * cx + b * cy + cc >= -1.0, f"edge {i} negative at the centroid"


# ---------------------------------------------------------------------------
# the real equivalence test: every output bit against the model
# ---------------------------------------------------------------------------


def _random_input(rng: random.Random, c: Config, *, sorted_y: bool = False) -> SetupInput:
    vw = c.vertex_format.width
    xs = [rng.randrange(1 << vw) for _ in range(3)]
    ys = [rng.randrange(1 << vw) for _ in range(3)]
    if sorted_y:
        ys = sorted(ys, key=lambda r: sext(r, vw))
    model = SetupModel(c)
    channels = {}
    for name in ITER_CHANNELS:
        w = model.channel_shapes[name].bit_width
        channels[name] = tuple(rng.randrange(1 << w) for _ in range(3))
    return SetupInput(
        tri=tuple((xs[i], ys[i]) for i in range(3)),
        sign_bit=rng.randrange(2),
        param_adjust=rng.randrange(2),
        channels=channels,
    )


def _assert_same(got: SetupOutput, want: SetupOutput, inp: SetupInput) -> None:
    ctx = f"\ninput: {inp}"
    assert got.xrange == want.xrange, f"xrange{ctx}"
    assert got.yrange == want.yrange, f"yrange{ctx}"
    for i in range(3):
        assert got.coeffs[i] == want.coeffs[i], f"coeffs[{i}]{ctx}"
    assert got.edge_start == want.edge_start, f"edgeStart{ctx}"
    for name in ITER_CHANNELS:
        assert got.starts[name] == want.starts[name], f"{name}{ctx}"


DIRECTED_MODEL_CASES = [
    # degenerate: all three vertices identical
    SetupInput(tri=((0, 0), (0, 0), (0, 0))),
    # every vertex at the format's extremes -- exercises ceil()'s 13th bit and
    # the `fixTo` saturation that clips it back (afix-semantics §6.3)
    SetupInput(tri=((0x7FFF, 0x7FFF), (0x7FFF, 0x7FFF), (0x7FFF, 0x7FFF))),
    SetupInput(tri=((0x8000, 0x8000), (0x8000, 0x8000), (0x8000, 0x8000))),
    SetupInput(tri=((0x8000, 0x8000), (0x7FFF, 0x7FFF), (0, 0)), sign_bit=1),
    # exact .5 fractions: the only place roundHalfDown differs from roundHalfUp
    SetupInput(tri=((0x0008, 0x0008), (0xFFF8, 0xFFF8), (0x0018, 0x0018))),
    # paramAdjust on, with a sub-pixel fraction of zero (dxSubRaw == 8)
    SetupInput(tri=((0, 0), (0x0100, 0), (0, 0x0100)), param_adjust=1,
               channels={n: (0x5555, 0x0123, 0x4567) for n in ITER_CHANNELS}),
    # paramAdjust off with the same payload, to pin the mux
    SetupInput(tri=((0, 0), (0x0100, 0), (0, 0x0100)), param_adjust=0,
               channels={n: (0x5555, 0x0123, 0x4567) for n in ITER_CHANNELS}),
]


@pytest.mark.parametrize("inp", DIRECTED_MODEL_CASES,
                         ids=[f"case{i}" for i in range(len(DIRECTED_MODEL_CASES))])
def test_model_matches_hardware_directed(inp):
    _assert_same(run_one(inp), SetupModel(CFG).run(inp), inp)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_model_matches_hardware_random(seed):
    """Random triangles and random gradients, every output bit compared.

    Run in one simulation with back-to-back triangles, which also covers the
    ``Done -> Idle -> Prep`` turnaround that a per-triangle test never sees.
    """
    rng = random.Random(seed)
    c = CFG
    inputs = [_random_input(rng, c) for _ in range(8)]
    model = SetupModel(c)
    expected = [model.run(i) for i in inputs]
    dut = TriangleSetup(c)
    got = []

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.i.valid, 0)
        await ctx.tick()
        for inp in inputs:
            apply_input(ctx, dut, inp)
            ctx.set(dut.i.valid, 1)
            # wait for the cycle on which i.fire happens
            while not ctx.get(dut.i.ready):
                await ctx.tick()
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            for _ in range(LATENCY - 1):
                await ctx.tick()
            assert ctx.get(dut.o.valid)
            got.append(read_output(ctx, dut, c))
            await ctx.tick()      # o.ready is high, so this retires it

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()

    assert len(got) == len(expected)
    for g, w, i in zip(got, expected, inputs):
        _assert_same(g, w, i)


# ---------------------------------------------------------------------------
# handshake / FSM shape
# ---------------------------------------------------------------------------


def test_handshake_and_latency():
    """``i.ready`` only in ``Idle``, ten cycles to ``o.valid``, and ``o``
    holds its payload until ``o.ready``."""
    inp = SetupInput(tri=((q(1.0), q(0.0)), (q(8.0), q(0.0)), (q(3.0), q(4.0))))
    dut = TriangleSetup(CFG)
    trace = []

    async def tb(ctx):
        ctx.set(dut.i.valid, 0)
        ctx.set(dut.o.ready, 0)
        await ctx.tick()
        assert ctx.get(dut.i.ready), "i.ready must be high in Idle"
        apply_input(ctx, dut, inp)
        ctx.set(dut.i.valid, 1)
        await ctx.tick()
        ctx.set(dut.i.valid, 0)
        for cycle in range(LATENCY - 1):
            assert not ctx.get(dut.i.ready), f"i.ready high mid-setup (cycle {cycle})"
            assert not ctx.get(dut.o.valid)
            await ctx.tick()
        assert ctx.get(dut.o.valid)
        assert not ctx.get(dut.i.ready), "i.ready must stay low while o.valid"
        held = read_output(ctx, dut, CFG)
        # o.ready stays low: the payload must not move for several cycles
        for _ in range(4):
            await ctx.tick()
            assert ctx.get(dut.o.valid)
            trace.append(read_output(ctx, dut, CFG))
        ctx.set(dut.o.ready, 1)
        await ctx.tick()
        assert not ctx.get(dut.o.valid), "o.valid must drop the cycle after o.ready"
        assert ctx.get(dut.i.ready), "back to Idle"
        for later in trace:
            assert later == held

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()


def test_state_encoding_matches_spinal():
    """``localparam SetupState_Idle = 2'd0`` ... in the Spinal Verilog."""
    assert [s.value for s in SetupState] == [0, 1, 2, 3]
    assert [s.name for s in SetupState] == ["Idle", "Prep", "Iterate", "Done"]


def test_reset_less_registers_match_spinal():
    """``pending`` and ``outputReg`` are plain Spinal ``Reg``s, so they must
    have no reset; everything else is ``RegInit``/``.init(0)``.

    The differential harness resets before every seed, so a spurious reset
    value here is a mismatch (plan 0.4, finding (c)).
    """
    frag = Fragment.get(TriangleSetup(CFG), None)
    # Names are stable because the port sets them explicitly with `name=`.
    names = {}
    for sig in _all_signals(frag):
        names.setdefault(sig.name, sig)
    for name in ("pending", "outputReg"):
        assert name in names, f"{name} not found in the elaborated design"
        assert names[name].reset_less, f"{name} must be reset_less (plain Spinal Reg)"
    for name in ("state", "iterIndex", "dxMagWork", "dyMagWork",
                 "redStart_acc", "hiAlphaStart_dyTerm"):
        assert name in names, f"{name} not found in the elaborated design"
        assert not names[name].reset_less, f"{name} is a Spinal RegInit and must reset"


def _all_signals(frag):
    out = []
    for stmt_domain in frag.statements.values():
        for sig in stmt_domain._lhs_signals():
            out.append(sig)
    for sub, _name, _src in frag.subfragments:
        out.extend(_all_signals(sub))
    return out


# ---------------------------------------------------------------------------
# TriangleSetupFormalTest.scala -- the bounding-box invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [11, 22, 33])
def test_formal_bounding_box_invariants(seed):
    """``GenerationFlags.formal`` in ``TriangleSetup.scala:326-336``.

    Whenever ``o.valid``:  ``xrange(0) <= min(x)``, ``xrange(1) >= max(x)``,
    and ``xrange(1) >= xrange(0)``.  The formal DUT assumes y-sorted vertices
    and constrains the coordinates so ``ceil`` cannot overflow
    (``TriangleSetupFormalDut:41-53``); both assumptions are reproduced,
    because the second one is load-bearing -- ``ceil(2047.x)`` is 2048, which
    ``fixTo`` then saturates back to 2047 and the first invariant would fail.
    """
    rng = random.Random(seed)
    c = CFG
    vw = c.vertex_format.width
    max_int = (1 << (c.vertex_format.non_fraction - 1)) - 1

    def constrained():
        while True:
            inp = _random_input(rng, c, sorted_y=True)
            ok = True
            for v in range(3):
                for a in range(2):
                    # assume(v(a).floor(0).asSInt < maxInt)
                    if sext(inp.tri[v][a], vw) >> c.vertex_format.fraction >= max_int:
                        ok = False
            if ok:
                return inp

    inputs = [constrained() for _ in range(6)]
    dut = TriangleSetup(c)

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.i.valid, 0)
        await ctx.tick()
        for inp in inputs:
            apply_input(ctx, dut, inp)
            ctx.set(dut.i.valid, 1)
            await ctx.tick()
            ctx.set(dut.i.valid, 0)
            for _ in range(LATENCY - 1):
                _check_invariants(ctx, dut, inp, c)
                await ctx.tick()
            _check_invariants(ctx, dut, inp, c)
            await ctx.tick()

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()


def _check_invariants(ctx, dut, inp, c):
    if not ctx.get(dut.o.valid):
        return
    vw = c.vertex_format.width
    xs = [sext(inp.tri[v][0], vw) for v in range(3)]
    x0 = sext(get_raw(ctx, dut.o.payload.xrange[0].raw), vw)
    x1 = sext(get_raw(ctx, dut.o.payload.xrange[1].raw), vw)
    assert x0 <= min(xs), f"xrange(0)={x0} > min(x)={min(xs)}"
    assert x1 >= max(xs), f"xrange(1)={x1} < max(x)={max(xs)}"
    assert x1 >= x0, f"xrange(1)={x1} < xrange(0)={x0}"
