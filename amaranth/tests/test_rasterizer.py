"""``avoodoo.raster.rasterizer`` -- the intent of the upstream Scala tests.

Ports ``ext/SpinalVoodoo/test/RasterizerTest.scala`` (784 lines, 14 SpinalSim
tests) and the invariants of ``RasterizerFormalTest.scala`` (three SymbiYosys
properties) onto Amaranth's pysim, per plan §4.1 level L0 and the Phase 3 exit
criterion.  The bit-exactness gate is the lockstep diff
(``make diff MOD=SpanRasterizer`` / ``MOD=Rasterizer``, plan §4.2); what these
tests add is a *readable statement of intent* and a reference model that does
not go through Verilator, so a broken port fails in seconds.

What is ported and what is not:

* the span walk, gradient interpolation, and back-pressure tests become
  :class:`SpanModel`-driven comparisons of every output beat;
* ``renderTriangleToImage`` and the PNG artefacts are dropped -- they were a
  debugging aid, and the golden images live in ``test-output/``;
* the whole-triangle tests (``referencePixels``: bounding-box scan + three
  edge functions, rows in the order the walker emits them) drive the full
  ``Rasterizer`` -- the nine small triangles under pysim, the five large ones
  only through the Verilator lockstep run;
* ``RasterizerFormalTest``'s properties become per-cycle asserts inside the
  pysim testbenches (plan §5 Phase 9 re-expresses them in ``sby``).

The reference model is built on :mod:`avoodoo.lib.fixed_model`, the pure-Python
AFix mirror, so "correct" here means the same rounding and the same saturation
as SpinalHDL, not floating-point approximately-right.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from amaranth.sim import Simulator

from avoodoo.config import Config, TraceConfig
from avoodoo.lib import fixed_model as fm
from avoodoo.raster.rasterizer import (
    LINEAR_STATE_ALL,
    Rasterizer,
    SpanRasterizer,
    low_grad_formats,
    output_layout,
    prefetch_span_layout,
    span_state_layout,
)

REPO = Path(__file__).resolve().parents[2]
PORTMAPS = REPO / "fpga" / "src" / "voodoo" / "diff" / "portmaps"

# test_port_surface_... inspects a Rasterizer's *signature* without ever
# elaborating it; Amaranth reports that at garbage-collection time, i.e. from
# whichever test happens to run next.
pytestmark = pytest.mark.filterwarnings("ignore:.*created but never used:")


# ---------------------------------------------------------------------------
# reference model
# ---------------------------------------------------------------------------


class SpanModel:
    """``Rasterizer.SpanRasterizer``'s ``StreamWhile`` loop, in Python.

    A transliteration of ``Rasterizer.scala:143-219``: the same state fields,
    the same per-pixel ``fixTo`` chain, the same ``isLast`` test, the same
    ``maxIterations`` cap.  Raw integers throughout, via
    :class:`avoodoo.lib.fixed_model.ModelValue`, so the comparison against the
    RTL is exact rather than within a tolerance.
    """

    def __init__(self, c: Config):
        self.c = c
        self.vtx = fm.ModelShape.from_q(c.vertex_format)
        self.hi = fm.ModelShape.from_q(c.tex_coords_hi_format)
        self.accum = fm.ModelShape.from_q(c.tex_coords_accum_format)
        self.linear_shapes = [fm.ModelShape.from_q(f) for f in low_grad_formats(c)]
        self.one = fm.const(self.vtx, 1)

    def to_pixel_coord(self, raw: int) -> int:
        """``toPixelCoord``: arithmetic shift down to whole pixels, 12 bits."""
        frac = self.c.vertex_format.fraction
        width = self.c.vertex_format.non_fraction
        shifted = raw >> frac
        shifted &= (1 << width) - 1
        if shifted >> (width - 1):
            shifted -= 1 << width
        return shifted

    def run(self, span: dict) -> list[dict]:
        """Every output beat for one ``SpanWalker.Output``, in order.

        ``span`` carries raw integers: ``x_start``, ``x_end``, ``y``,
        ``linear`` (6), ``hi_s``, ``hi_t``, ``hi_alpha``, ``step_linear`` (6),
        ``step_tex`` (2), ``step_alpha``.
        """
        x = fm.ModelValue(self.vtx, span["x_start"])
        y = fm.ModelValue(self.vtx, span["y"])
        x_end = fm.ModelValue(self.vtx, span["x_end"])
        linear = [fm.ModelValue(s, v)
                  for s, v in zip(self.linear_shapes, span["linear"])]
        hi_s = fm.ModelValue(self.hi, span["hi_s"])
        hi_t = fm.ModelValue(self.hi, span["hi_t"])
        hi_alpha = fm.ModelValue(self.hi, span["hi_alpha"])
        step_lin = [fm.ModelValue(s, v)
                    for s, v in zip(self.linear_shapes, span["step_linear"])]
        step_tex = [fm.ModelValue(self.hi, v) for v in span["step_tex"]]
        step_alpha = fm.ModelValue(self.hi, span["step_alpha"])

        out = []
        for _ in range(self.c.max_fb_dims[0]):
            out.append({
                "coords": (self.to_pixel_coord(x.raw), self.to_pixel_coord(y.raw)),
                "linear": [v.raw for v in linear],
                "s_grad": hi_s.fix_to(self.accum).raw,
                "t_grad": hi_t.fix_to(self.accum).raw,
                "alpha_grad_hi": hi_alpha.raw,
            })
            if self.to_pixel_coord(x.raw) >= self.to_pixel_coord(x_end.raw):
                break
            x = (x + self.one).fix_to(self.vtx)
            linear = [(cur + grad).fix_to(shape)
                      for cur, grad, shape in zip(linear, step_lin, self.linear_shapes)]
            hi_s = (hi_s + step_tex[0]).fix_to(self.hi)
            hi_t = (hi_t + step_tex[1]).fix_to(self.hi)
            hi_alpha = (hi_alpha + step_alpha).fix_to(self.hi)
        return out


def make_span(c: Config, *, x_start: float = 0.0, x_end: float = 0.0, y: float = 0.0,
              linear=(0,) * 6, step_linear=(0,) * 6, hi_s=0, hi_t=0, hi_alpha=0,
              step_tex=(0, 0), step_alpha=0) -> dict:
    """A ``SpanWalker.Output`` as raw integers; pixel positions in pixels."""
    scale = 1 << c.vertex_format.fraction
    return {
        "x_start": int(round(x_start * scale)),
        "x_end": int(round(x_end * scale)),
        "y": int(round(y * scale)),
        "linear": list(linear),
        "step_linear": list(step_linear),
        "hi_s": hi_s,
        "hi_t": hi_t,
        "hi_alpha": hi_alpha,
        "step_tex": list(step_tex),
        "step_alpha": step_alpha,
    }


# ---------------------------------------------------------------------------
# pysim plumbing
# ---------------------------------------------------------------------------


def _drive_span(ctx, dut, span: dict) -> None:
    p = dut.i.payload
    ctx.set(p.x_start.as_value(), span["x_start"])
    ctx.set(p.x_end.as_value(), span["x_end"])
    ctx.set(p.y.as_value(), span["y"])
    for name, value in zip(LINEAR_STATE_ALL, span["linear"]):
        ctx.set(getattr(p.linear, name).as_value(), value)
    for name, value in zip(LINEAR_STATE_ALL, span["step_linear"]):
        ctx.set(getattr(p.step_x.linear, name).as_value(), value)
    ctx.set(p.hi_s.as_value(), span["hi_s"])
    ctx.set(p.hi_t.as_value(), span["hi_t"])
    ctx.set(p.hi_alpha.as_value(), span["hi_alpha"])
    ctx.set(p.step_x.tex[0].as_value(), span["step_tex"][0])
    ctx.set(p.step_x.tex[1].as_value(), span["step_tex"][1])
    ctx.set(p.step_x.alpha.as_value(), span["step_alpha"])


def _sample_output(ctx, dut) -> dict:
    o = dut.o.payload
    return {
        "coords": (ctx.get(o.coords[0]), ctx.get(o.coords[1])),
        "linear": [ctx.get(getattr(o.grads, f"{n}_grad").as_value())
                   for n in ("red", "green", "blue", "depth", "alpha", "w")],
        "s_grad": ctx.get(o.grads.s_grad.as_value()),
        "t_grad": ctx.get(o.grads.t_grad.as_value()),
        "alpha_grad_hi": ctx.get(o.alpha_grad_hi.as_value()),
    }


def run_span_rasterizer(c: Config, spans, *, ready_pattern=None, max_cycles=20000,
                        check_invariants=True):
    """Feed ``spans`` through :class:`SpanRasterizer`; return the beats seen.

    ``ready_pattern`` is a callable ``cycle -> bool`` for ``o.ready``; the
    default is always ready.  ``check_invariants`` turns on the per-cycle
    ``RasterizerFormalTest`` / ``StreamWhile`` properties.
    """
    dut = SpanRasterizer(c)
    beats: list[dict] = []
    failures: list[str] = []

    async def tb(ctx):
        ctx.set(dut.i.valid, 0)
        ctx.set(dut.o.ready, 1)
        await ctx.tick()

        pending = list(spans)
        cycle = 0
        idle_for = 0
        while cycle < max_cycles:
            ready = True if ready_pattern is None else bool(ready_pattern(cycle))
            ctx.set(dut.o.ready, ready)
            if pending:
                _drive_span(ctx, dut, pending[0])
                ctx.set(dut.i.valid, 1)
            else:
                ctx.set(dut.i.valid, 0)

            await ctx.delay(1e-10)  # settle the combinational outputs
            running = ctx.get(dut.running)
            o_valid = ctx.get(dut.o.valid)
            i_ready = ctx.get(dut.i.ready)
            if check_invariants:
                # StreamWhile.scala's two GenerationFlags.formal invariants.
                if o_valid != running:
                    failures.append(f"cycle {cycle}: o.valid={o_valid} != running={running}")
                if i_ready == running:
                    failures.append(f"cycle {cycle}: i.ready={i_ready} != !running={not running}")
            if o_valid and ready:
                beats.append(_sample_output(ctx, dut))
            took = pending and ctx.get(dut.i.valid) and i_ready

            await ctx.tick()
            if took:
                pending.pop(0)
            if not pending and not running and not o_valid:
                idle_for += 1
                if idle_for > 2:
                    break
            else:
                idle_for = 0
            cycle += 1
        else:  # pragma: no cover - a hang is a bug, not a slow test
            raise AssertionError(f"SpanRasterizer did not go idle in {max_cycles} cycles")

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()
    assert not failures, failures[:8]
    return beats


# ---------------------------------------------------------------------------
# 1. the port surface -- no Verilator needed, and the real guard on core_types
# ---------------------------------------------------------------------------


def test_port_surface_matches_the_committed_spinal_portmap():
    """Every one of ``Rasterizer_spinal.v``'s 259 ports pairs mechanically.

    This is plan §4.2 step 4 checked without building anything: the committed
    port map is the Spinal Verilog's port list, ``tools.diff.naming`` is the
    camelCase -> snake_case rule, and ``tools.diff.amports`` says which packed
    Amaranth port each leaf lives in.  The assertions below are what let
    ``tools/diff/registry.py`` carry **no** ``overrides`` for this module --
    and they pin every field name and width in ``avoodoo/core/core_types.py``
    that the raster path touches, which is the part several agents share.
    """
    from tools.diff.amports import amaranth_fields
    from tools.diff.naming import amaranth_port_name

    pm = json.loads((PORTMAPS / "Rasterizer.json").read_text())
    dut = Rasterizer(Config.voodoo1())
    fields = amaranth_fields(dut)

    problems = []
    paired = set()
    for port in pm["ports"]:
        if port["role"] in ("clock", "reset"):
            continue
        want = amaranth_port_name(port["path"], spinal_name=port["name"])
        field = fields.get(want)
        if field is None:
            problems.append(f"{port['name']} -> {want}: no such leaf")
        elif field.width != port["width"]:
            problems.append(
                f"{port['name']} -> {want}: {field.width} bits, Spinal has {port['width']}")
        else:
            paired.add(want)
    assert not problems, problems[:12]
    assert not set(fields) - paired, sorted(set(fields) - paired)[:12]


def test_layout_widths():
    """The three payload bundles, against ``Config.voodoo1()``'s formats."""
    c = Config.voodoo1()
    out = output_layout(c)
    assert out["alpha_grad_hi"].shape.bit_width == 46      # texCoordsHiFormat
    assert out["grads"].shape["w_grad"].shape.bit_width == 48    # wAccumFormat
    assert out["grads"].shape["s_grad"].shape.bit_width == 48    # texCoordsAccumFormat
    assert out["grads"].shape["depth_grad"].shape.bit_width == 32  # vDepthFormat
    assert out["grads"].shape["red_grad"].shape.bit_width == 24    # vColorFormat
    # coords are SInt(vertexFormat.nonFraction bits), not AFix
    assert out["coords"].shape.length == 2
    assert prefetch_span_layout(c)["x_start"].shape.bit_width == 16
    assert "trace" not in {n for n, _ in out}
    traced = output_layout(Config.voodoo1(TraceConfig(enabled=True)))
    assert "trace" in {n for n, _ in traced}


def test_span_state_covers_the_scala_bundle():
    c = Config.voodoo1()
    names = {name for name, _ in span_state_layout(c)}
    assert names == {"x", "y", "linear", "hi_s", "hi_t", "hi_alpha", "x_end",
                     "step_x", "tmu_config", "pixel_config"}
    traced = {name for name, _ in span_state_layout(Config.voodoo1(TraceConfig(True)))}
    assert "trace" in traced


# ---------------------------------------------------------------------------
# 2. RasterizerTest.scala: the span walk
# ---------------------------------------------------------------------------


def test_emits_exact_left_to_right_span():
    """``"Rasterizer emits exact left-to-right spans"``, one span's worth.

    The Scala drives a 3x3 box through the whole Rasterizer; the same
    statement about a single row is a SpanRasterizer test.
    """
    c = Config.voodoo1()
    beats = run_span_rasterizer(c, [make_span(c, x_start=0.0, x_end=2.0, y=1.0)])
    assert [b["coords"] for b in beats] == [(0, 1), (1, 1), (2, 1)]


def test_single_pixel_span():
    """``xStart == xEnd`` emits exactly one pixel: ``isLast`` is checked
    *before* the step, so the loop never runs twice."""
    c = Config.voodoo1()
    beats = run_span_rasterizer(c, [make_span(c, x_start=7.0, x_end=7.0, y=3.0)])
    assert [b["coords"] for b in beats] == [(7, 3)]


def test_reversed_span_still_emits_one_pixel():
    """``xEnd < xStart``: ``isLast`` is true on the first beat, so one pixel
    comes out rather than the loop running to ``maxIterations``."""
    c = Config.voodoo1()
    beats = run_span_rasterizer(c, [make_span(c, x_start=9.0, x_end=4.0, y=0.0)])
    assert [b["coords"] for b in beats] == [(9, 0)]


def test_negative_coordinates():
    """``"Triangle with negative coordinates"``: the 12.4 position is shifted
    down arithmetically, so x = -3 must not wrap to +4093."""
    c = Config.voodoo1()
    beats = run_span_rasterizer(c, [make_span(c, x_start=-3.0, x_end=1.0, y=-2.0)])
    # `coords` is signed(12), and pysim hands a signed shape back as a signed int.
    assert [b["coords"] for b in beats] == [(-3, -2), (-2, -2), (-1, -2), (0, -2), (1, -2)]


def test_interpolates_gradients_against_the_model():
    """``"Rasterizer correctly interpolates gradients"``, bit-exact.

    The Scala compares ``o.grads.all(0)`` to ``0.1 + x*0.01 + y*0.02`` within
    1e-3.  Here every one of the eight gradients is compared to
    :class:`SpanModel` exactly, which also pins the ``hiS/hiT -> sGrad/tGrad``
    ``fixTo(texCoordsAccumFormat)`` (floor by 12 bits, then saturate to
    +-2^33) that the tolerance test could never see.
    """
    c = Config.voodoo1()
    span = make_span(
        c, x_start=2.0, x_end=9.0, y=4.0,
        linear=(409, -1234, 77, 500000, 4096, 1 << 30),
        step_linear=(41, 4, -13, 12345, 40, 1 << 20),
        hi_s=(1 << 33) + 5, hi_t=-(1 << 31), hi_alpha=123456789,
        step_tex=((1 << 29), -(1 << 27)), step_alpha=987654321,
    )
    beats = run_span_rasterizer(c, [span])
    assert beats == SpanModel(c).run(span)


def test_saturation_is_reached_and_sticks():
    """A step big enough to overflow every accumulator.

    ``fixTo`` saturates rather than wrapping, and a saturated accumulator stays
    saturated; the lockstep diff found the one bug in this port right here
    (``AFix`` inside a ``data.Layout`` was doing unsigned arithmetic, so a
    negative position saturated *positive*).
    """
    c = Config.voodoo1()
    span = make_span(
        c, x_start=-6.0, x_end=6.0, y=0.0,
        linear=(-(1 << 22), (1 << 22), 0, -(1 << 30), 0, -(1 << 46)),
        step_linear=(-(1 << 21), (1 << 21), 0, (1 << 29), 0, -(1 << 45)),
        hi_s=-(1 << 44), hi_t=(1 << 44), hi_alpha=(1 << 44),
        step_tex=(-(1 << 43), (1 << 43)), step_alpha=(1 << 43),
    )
    model = SpanModel(c).run(span)
    assert run_span_rasterizer(c, [span]) == model
    hi_max = fm.ModelShape.from_q(c.tex_coords_hi_format).max_raw
    assert model[-1]["alpha_grad_hi"] == hi_max, "the test should reach saturation"


def test_handles_backpressure():
    """``"Rasterizer handles backpressure"``: ready 2 cycles in 3 loses no pixel."""
    c = Config.voodoo1()
    span = make_span(c, x_start=0.0, x_end=3.0, y=2.0,
                     linear=(1, 2, 3, 4, 5, 6), step_linear=(1, 1, 1, 1, 1, 1))
    beats = run_span_rasterizer(c, [span], ready_pattern=lambda i: i % 3 != 0)
    assert beats == SpanModel(c).run(span)


def test_back_to_back_spans():
    """Two spans in a row: the second must start from *its* payload, and
    ``running``/``i.ready`` must hand over without dropping a beat."""
    c = Config.voodoo1()
    spans = [
        make_span(c, x_start=1.0, x_end=3.0, y=0.0, linear=(10,) * 6,
                  step_linear=(1,) * 6),
        make_span(c, x_start=5.0, x_end=6.0, y=1.0, linear=(20,) * 6,
                  step_linear=(2,) * 6),
    ]
    model = SpanModel(c).run(spans[0]) + SpanModel(c).run(spans[1])
    assert run_span_rasterizer(c, spans) == model


def test_max_iterations_caps_a_runaway_span():
    """``maxIterations = c.maxFbDims._1``.

    ``StreamWhile`` stops on ``counter.willOverflowIfInc`` even when ``isLast``
    never fires, so a span whose ``xEnd`` is unreachable cannot hang the core.
    A 16-wide config keeps the test quick; the real one is 800.
    """
    c = Config.voodoo1()
    small = Config.voodoo1()
    object.__setattr__(small, "max_fb_dims", (16, 16))
    beats = run_span_rasterizer(small, [make_span(small, x_start=0.0, x_end=900.0)])
    assert len(beats) == 16
    assert [b["coords"][0] for b in beats] == list(range(16))
    assert c.max_fb_dims[0] == 800  # the shipped cap, for the record


def test_y_is_constant_across_a_span():
    """``next.y := state.y`` -- ``RasterizerFormalDut``'s per-primitive y bound."""
    c = Config.voodoo1()
    beats = run_span_rasterizer(c, [make_span(c, x_start=0.0, x_end=20.0, y=5.0)])
    assert {b["coords"][1] for b in beats} == {5}


# ---------------------------------------------------------------------------
# 3. RasterizerFormalTest.scala: the trace counter
# ---------------------------------------------------------------------------


def test_pixel_seq_counts_within_a_primitive_and_resets_on_first_span():
    """``RasterizerFormalDut(formalStrong = true)``'s two ``pixelSeq`` asserts.

    ``pixelSeqCounter`` resets when a span arrives with ``firstSpan`` set and
    otherwise increments on every output beat, so ``pixelSeq`` numbers the
    pixels of one primitive from zero.  Trace is off in the shipped config;
    this is the only test that turns it on.
    """
    c = Config.voodoo1(TraceConfig(enabled=True))
    dut = SpanRasterizer(c)
    seen: list[tuple[int, int, int]] = []

    spans = [
        (make_span(c, x_start=0.0, x_end=2.0, y=0.0), 1, 7),   # firstSpan, primitive 7
        (make_span(c, x_start=0.0, x_end=1.0, y=1.0), 0, 7),   # same primitive
        (make_span(c, x_start=0.0, x_end=0.0, y=2.0), 1, 9),   # new primitive
    ]

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.i.valid, 0)
        await ctx.tick()
        for span, first, prim in spans:
            _drive_span(ctx, dut, span)
            ctx.set(dut.i.payload.first_span, first)
            ctx.set(dut.i.payload.trace.valid, 1)
            ctx.set(dut.i.payload.trace.primitive_id, prim)
            ctx.set(dut.i.valid, 1)
            while True:
                await ctx.delay(1e-10)
                took = ctx.get(dut.i.ready)
                if ctx.get(dut.o.valid):
                    seen.append((ctx.get(dut.o.payload.trace.primitive.primitive_id),
                                 ctx.get(dut.o.payload.trace.pixel_seq),
                                 ctx.get(dut.o.payload.coords[0])))
                await ctx.tick()
                if took:
                    break
            ctx.set(dut.i.valid, 0)
            while True:
                await ctx.delay(1e-10)
                if ctx.get(dut.o.valid):
                    seen.append((ctx.get(dut.o.payload.trace.primitive.primitive_id),
                                 ctx.get(dut.o.payload.trace.pixel_seq),
                                 ctx.get(dut.o.payload.coords[0])))
                elif not ctx.get(dut.running):
                    break
                await ctx.tick()

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()

    # primitive 7: three pixels of span 0 then two of span 1, numbered 0..4;
    # primitive 9 arrives with firstSpan, so its counter restarts at 0.
    assert seen == [(7, 0, 0), (7, 1, 1), (7, 2, 2),
                    (7, 3, 0), (7, 4, 1),
                    (9, 0, 0)]


# ---------------------------------------------------------------------------
# 4. the whole Rasterizer: bounding-box scan + edge functions reference
# ---------------------------------------------------------------------------


def edges_from_vertices(vertices, *, upstream_sign: bool = False):
    """``RasterizerTest.edgesFromVertices``: three edge equations + bounding box.

    The Scala normalises the winding with
    ``sign = if (signedArea2 < 0) -1 else 1`` and then treats
    ``a*x + b*y + c >= 0`` as "inside".  **That sign is backwards**: with
    ``a = sign*(y1-y0)``, ``b = sign*(x0-x1)``, ``c = sign*(x1*y0 - x0*y1)``
    and a *positive* signed area, the third vertex evaluates negative, so the
    "inside" test is never satisfied and ``referencePixels`` returns the empty
    list for all fourteen of the Scala test's triangles -- see
    :func:`test_upstream_reference_convention_is_vacuous`, which pins that.

    ``upstream_sign=True`` reproduces the Scala verbatim; the default flips it
    so the edges really do bound the triangle, which is what makes the
    hardware comparison mean something.
    """
    (ax, ay), (bx, by), (cx, cy) = vertices
    area2 = ax * (by - cy) + bx * (cy - ay) + cx * (ay - by)
    sign = -1.0 if area2 < 0.0 else 1.0
    if not upstream_sign:
        sign = -sign
    edges = []
    for i0, i1 in ((0, 1), (1, 2), (2, 0)):
        x0, y0 = vertices[i0]
        x1, y1 = vertices[i1]
        edges.append((sign * (y1 - y0), sign * (x0 - x1), sign * (x1 * y0 - x0 * y1)))
    import math
    xmin = math.floor(min(v[0] for v in vertices))
    xmax = math.ceil(max(v[0] for v in vertices))
    ymin = math.floor(min(v[1] for v in vertices))
    ymax = math.ceil(max(v[1] for v in vertices))
    starts = [a * xmin + b * ymin + cc for a, b, cc in edges]
    return edges, starts, (xmin, xmax, ymin, ymax)


def reference_pixels(vertices, *, upstream_sign: bool = False):
    """``RasterizerTest.referencePixels``: bounding-box scan over three edge
    functions, rows top to bottom and each row left to right -- the order the
    span walker's serpentine search produces once the spans are emitted.

    Both range ends are **exclusive**, which is the hardware's convention:
    ``TriangleSetup`` sets ``xrange(1) = ceil(xmax)`` and ``SpanWalker`` emits
    up to ``visibleEndPix - 1``.  The Scala reference scans
    ``xmin.toInt to xmax.toInt`` (inclusive) -- a second latent slip in that
    test that its wrong-way winding (above) kept from ever showing.
    """
    edges, _starts, (xmin, xmax, ymin, ymax) = edges_from_vertices(
        vertices, upstream_sign=upstream_sign)
    out = []
    for y in range(int(ymin), int(ymax)):
        for x in range(int(xmin), int(xmax)):
            if all(a * x + b * y + cc >= 0.0 for a, b, cc in edges):
                out.append((x, y))
    return out


#: ``"Rasterizer matches software reference across many triangle
#: configurations"`` -- the 14 cases, verbatim.
REFERENCE_TRIANGLES = [
    [(0, 0), (8, 0), (0, 8)],
    [(8, 0), (8, 8), (0, 8)],
    [(-4, 3), (6, 1), (2, 11)],
    [(0, 0), (15, 1), (1, 14)],
    [(12, 4), (14, 18), (2, 10)],
    [(20, 5), (3, 6), (21, 7)],
    [(5, 10), (25, 10), (13, 11)],
    [(30, 20), (0, 30), (31, 31)],
    [(-8, -8), (8, -6), (-6, 8)],
    [(100, 40), (140, 44), (101, 45)],
    [(611, 368), (474, 467), (568, 479)],
    [(471, 363), (611, 368), (474, 467)],
    [(458, 129), (401, 138), (409, 148)],
    [(456, 116), (458, 129), (401, 138)],
]


def test_reference_model_is_self_consistent():
    """The software reference alone, so the edge functions are checked even
    while the RTL half of a case is too slow for pysim."""
    assert reference_pixels([(0, 0), (4, 0), (0, 4)]) == [
        (0, 0), (1, 0), (2, 0), (3, 0),
        (0, 1), (1, 1), (2, 1), (3, 1),
        (0, 2), (1, 2), (2, 2),
        (0, 3), (1, 3),
    ]
    for verts in REFERENCE_TRIANGLES:
        px = reference_pixels(verts)
        assert px, verts
        _e, _s, (xmin, xmax, ymin, ymax) = edges_from_vertices(verts)
        assert all(xmin <= x < xmax and ymin <= y < ymax for x, y in px)


def test_upstream_reference_convention_is_vacuous():
    """A finding about ``RasterizerTest.scala``, pinned so it is not lost.

    ``edgesFromVertices`` flips the winding the wrong way, so
    ``referencePixels`` is **empty for every one of the fourteen triangles**
    in ``"Rasterizer matches software reference across many triangle
    configurations"``.  The DUT is fed the same edge equations, finds nothing
    inside either, and ``got == expected`` compares two empty lists: the test
    passes without rasterising a pixel.  Worth fixing upstream (one ``-``);
    until then this repo's version uses the corrected sign, above.
    """
    for verts in REFERENCE_TRIANGLES:
        assert reference_pixels(verts, upstream_sign=True) == []
        assert reference_pixels(verts) != []


@pytest.mark.parametrize("verts", REFERENCE_TRIANGLES[:9],
                         ids=lambda v: f"{v[0]}-{v[1]}-{v[2]}")
def test_rasterizer_matches_software_reference(verts):
    """``"Rasterizer matches software reference across many triangle
    configurations"``: every pixel the hardware emits, in order.

    Only the small triangles are run under pysim; the large ones (up to
    204 rows) belong to the Verilator lockstep run, ``make diff MOD=Rasterizer``.
    """
    c = Config.voodoo1()
    edges, starts, (xmin, xmax, ymin, ymax) = edges_from_vertices(verts)
    dut = Rasterizer(c)
    vscale = 1 << c.vertex_format.fraction
    cscale = 1 << c.coefficient_format.fraction
    got: list[tuple[int, int]] = []

    async def tb(ctx):
        p = dut.i.payload
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.prefetch_span.ready, 1)
        ctx.set(dut.enable_clipping, 0)
        for name in ("clip_left", "clip_right", "clip_low_y", "clip_high_y"):
            ctx.set(getattr(dut, name), 0)
        # `.as_value()` everywhere: pysim would otherwise route a bare int
        # through AFixShape.const() and read it as a *real* number.
        ctx.set(p.xrange[0].as_value(), int(round(xmin * vscale)))
        ctx.set(p.xrange[1].as_value(), int(round(xmax * vscale)))
        ctx.set(p.yrange[0].as_value(), int(round(ymin * vscale)))
        ctx.set(p.yrange[1].as_value(), int(round(ymax * vscale)))
        for i, (a, b, cc) in enumerate(edges):
            ctx.set(p.coeffs[i].a.as_value(), int(round(a * cscale)))
            ctx.set(p.coeffs[i].b.as_value(), int(round(b * cscale)))
            ctx.set(p.coeffs[i].c.as_value(), int(round(cc * cscale)))
            ctx.set(p.edge_start[i].as_value(), int(round(starts[i] * cscale)))
        ctx.set(dut.i.valid, 1)
        while True:
            await ctx.delay(1e-10)
            took = ctx.get(dut.i.ready)
            await ctx.tick()
            if took:
                break
        ctx.set(dut.i.valid, 0)
        idle = 0
        for _ in range(200000):
            await ctx.delay(1e-10)
            if ctx.get(dut.o.valid):
                got.append((ctx.get(dut.o.payload.coords[0]),
                            ctx.get(dut.o.payload.coords[1])))
            busy = ctx.get(dut.running) or ctx.get(dut.o.valid)
            idle = 0 if busy else idle + 1
            if idle > 8:
                return
            await ctx.tick()
        raise AssertionError("Rasterizer did not go idle")

    sim = Simulator(dut)
    sim.add_clock(1e-8)
    sim.add_testbench(tb)
    sim.run()

    expected = reference_pixels(verts)
    seen = list(dict.fromkeys(got))
    assert seen == expected
