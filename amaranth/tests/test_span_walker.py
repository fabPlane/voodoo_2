"""``SpanWalker`` unit tests: a Python reference model plus the ported upstream tests.

Covers ``avoodoo/raster/span_walker.py``, the port of
``ext/SpinalVoodoo/src/voodoo/raster/SpanWalker.scala``.

Three things are checked here, all in Amaranth's pysim (the cycle-exact
Verilator lockstep against the Spinal RTL is ``make diff MOD=SpanWalker``,
plan §4.2):

1. :class:`SpanWalkerModel` -- an independent, cycle-accurate Python model of
   the eight-state machine, built on :mod:`avoodoo.lib.fixed_model` (the
   AFix reference model, *not* the Amaranth ``lib/fixed.py`` the RTL uses).
   The span iteration, the clip window and every gradient step are re-derived
   there, so a mistake in either implementation shows up as a disagreement
   rather than as two copies of one bug.  ``test_model_matches_rtl`` runs both
   over a set of triangles -- flat, sloped, clipped, off-screen, reversed --
   and compares the full emitted payloads, gradient accumulators included.

2. The intent of ``ext/SpinalVoodoo/test/SpanWalkerClipDisabledRegressionTest.scala``:
   the shrunken ``Config`` it uses, a 2x2 box with clipping disabled and the
   clip registers at zero, and its literal expectation that the first span is
   ``(xStart, xEnd, y) == (0, 1, 0)``.

3. The invariants of ``ext/SpinalVoodoo/test/SpanWalkerFormalTest.scala``.
   The five that ``SpanWalker.scala``'s own ``GenerationFlags.formal`` block
   asserts (``outputStartPix <= outputEndPix`` and the four clip-window
   bounds) are checked on every emitted span of every triangle in the suite;
   the sequencing properties of ``SpanWalkerFormalDut`` (``y`` advances by
   exactly one within a primitive, ``firstSpan`` is set on the first span of a
   primitive and clear afterwards) are checked on the two-box stimulus it
   builds.

   **Not ported: that DUT's two literal ``xEnd`` constants.**  It builds a box
   with ``xrange = (1, 2)`` and asserts ``xEnd === 2``, and
   ``SpanWalkerClipLeftFormalDut`` builds ``xrange = (-1, 1)`` with clipping on
   and asserts ``xEnd === 1``.  The RTL emits ``visibleEndPix - 1`` in both
   cases (``SpanWalker.scala:317``), i.e. 1 and 0 -- and the *simulation*
   regression in (2) agrees with the RTL, asserting ``xEnd == 1`` for
   ``xrange = (0, 2)``.  Both the Spinal RTL and this port do the same thing,
   and the lockstep diff confirms it bit for bit, so the two formal constants
   are an upstream-test discrepancy, not a porting error.  What the clip-left
   case is actually *for* -- that a span starting left of the clip window is
   clamped to ``clipLeft`` rather than kept at -1 -- is asserted.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
from amaranth.sim import Simulator

from avoodoo.config import Config, QFormat, TraceConfig
from avoodoo.core.core_types import GRADIENT_BUNDLE_ALL, triangle_setup_output_layout
from avoodoo.lib import fixed_model as fm
from avoodoo.raster.span_walker import (
    LINEAR_FIELDS,
    SpanWalker,
    low_grad_formats,
)

REPO = Path(__file__).resolve().parents[2]
SPINAL_V = REPO / "fpga" / "rtl" / "diff" / "SpanWalker_spinal.v"


# ---------------------------------------------------------------------------
# configurations
# ---------------------------------------------------------------------------


def small_config(trace: bool = False) -> Config:
    """The shrunken ``Config`` both upstream ``SpanWalker`` tests build.

    Verbatim from ``SpanWalkerClipDisabledRegressionTest.scala:11-26`` and
    ``SpanWalkerFormalTest.scala:11-25``; it keeps every format wide enough to
    be distinct but small enough that a whole 4x4 framebuffer walks in a few
    dozen cycles.
    """
    return dataclasses.replace(
        Config.voodoo1(TraceConfig(enabled=trace)),
        vertex_format=QFormat(5, 1, True),
        v_color_format=QFormat(4, 1, True),
        v_depth_format=QFormat(4, 1, True),
        w_accum_format=QFormat(4, 1, True),
        coefficient_format=QFormat(6, 1, True),
        tex_coords_format=QFormat(4, 1, True),
        tex_coords_accum_format=QFormat(5, 1, True),
        tex_coords_hi_format=QFormat(5, 1, True),
        address_width=4,
        max_fb_dims=(4, 4),
    )


# ---------------------------------------------------------------------------
# stimulus: a TriangleSetup.Output as a plain nested dict of numbers
# ---------------------------------------------------------------------------


def box_triangle(
    c: Config,
    *,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    enable_clipping: bool = False,
    clip_left: int = 0,
    clip_right: int = 0,
    clip_low_y: int = 0,
    clip_high_y: int = 0,
    grad_start: float = 0.0,
    grad_dx: float = 0.0,
    grad_dy: float = 0.0,
    tex_start: float = 0.0,
    tex_dx: float = 0.0,
    tex_dy: float = 0.0,
) -> dict:
    """``buildBox`` from ``SpanWalkerFormalTest.scala``, with optional gradients.

    ``a = b = 0, c = 1, edgeStart = 1`` makes all three edge functions the
    constant ``+1``, so ``insideTriangle`` is always true and the walker is
    bounded purely by ``xrange``/``yrange`` and the clip window -- which is
    what makes the span geometry predictable.
    """
    grads = {}
    for name in GRADIENT_BUNDLE_ALL:
        grads[name] = {"start": grad_start, "d": [grad_dx, grad_dy]}
    return {
        "coeffs": [{"a": 0.0, "b": 0.0, "c": 1.0}] * 3,
        "xrange": [x0, x1],
        "yrange": [y0, y1],
        "edge_start": [1.0, 1.0, 1.0],
        "grads": grads,
        "hi_alpha": {"start": tex_start, "d_ad_x": tex_dx, "d_ad_y": tex_dy},
        "tex_hi": {
            "s_start": tex_start,
            "t_start": tex_start,
            "d_sd_x": tex_dx,
            "d_td_x": tex_dx,
            "d_sd_y": tex_dy,
            "d_td_y": tex_dy,
        },
        "config": {
            "enable_clipping": int(enable_clipping),
            "clip_left": clip_left,
            "clip_right": clip_right,
            "clip_low_y": clip_low_y,
            "clip_high_y": clip_high_y,
        },
    }


# ---------------------------------------------------------------------------
# the reference model
# ---------------------------------------------------------------------------


def _ms(fmt: QFormat) -> fm.ModelShape:
    return fm.ModelShape.from_q(fmt)


def _wrap_signed(value: int, width: int) -> int:
    """Spinal ``SInt.resize``/``-`` truncation: keep ``width`` bits, two's complement."""
    mask = (1 << width) - 1
    value &= mask
    return value - (1 << width) if value >> (width - 1) else value


def _floor_pix(mv: fm.ModelValue) -> int:
    """``value.floor(0).asSInt``."""
    return mv.floor(0).as_signed()


class SpanWalkerModel:
    """Cycle-accurate model of ``SpanWalker``; ``run`` returns the spans it emits.

    State names, register names and the order of the eight ``when`` blocks are
    the Scala's.  ``o.ready`` is held high (the RTL is compared against this
    with ``o.ready`` high too), so a span is emitted on every cycle the walker
    spends in ``EmitSpan`` with ``emitVisible``.
    """

    STATES = (
        "Idle",
        "Decide",
        "RecoverLeft",
        "SearchRightToEnter",
        "SearchLeftToExit",
        "SearchRightToExit",
        "EmitSpan",
        "AdvanceRow",
    )

    def __init__(self, c: Config) -> None:
        self.c = c
        self.vs = _ms(c.vertex_format)
        self.cs = _ms(c.coefficient_format)
        self.hs = _ms(c.tex_coords_hi_format)
        self.lg = [_ms(f) for f in low_grad_formats(c)]
        self.npix = c.vertex_format.non_fraction
        self.one = fm.const(self.vs, 1)
        self.neg_one = fm.const(self.vs, -1)
        self.zero = fm.ModelValue(fm.ModelShape.sq(1, 0), 0)

    # -- payload -> model values ------------------------------------------

    def _triangle_const(self, tri: dict) -> dict:
        """``initTriangleConst``: the constant half of the triangle."""
        cfg = tri["config"]
        return {
            "coeffs": [
                {k: fm.const(self.cs, v) for k, v in coeff.items()} for coeff in tri["coeffs"]
            ],
            "xrange": [fm.const(self.vs, v) for v in tri["xrange"]],
            "yrange": [fm.const(self.vs, v) for v in tri["yrange"]],
            "linear_dx": {
                n: fm.const(self.lg[i], tri["grads"][GRADIENT_BUNDLE_ALL[i]]["d"][0])
                for i, n in enumerate(LINEAR_FIELDS)
            },
            "linear_dy": {
                n: fm.const(self.lg[i], tri["grads"][GRADIENT_BUNDLE_ALL[i]]["d"][1])
                for i, n in enumerate(LINEAR_FIELDS)
            },
            "tex_dx": [
                fm.const(self.hs, tri["tex_hi"]["d_sd_x"]),
                fm.const(self.hs, tri["tex_hi"]["d_td_x"]),
            ],
            "tex_dy": [
                fm.const(self.hs, tri["tex_hi"]["d_sd_y"]),
                fm.const(self.hs, tri["tex_hi"]["d_td_y"]),
            ],
            "alpha_dx": fm.const(self.hs, tri["hi_alpha"]["d_ad_x"]),
            "alpha_dy": fm.const(self.hs, tri["hi_alpha"]["d_ad_y"]),
            "enable_clipping": bool(cfg["enable_clipping"]),
            "clip_left": cfg["clip_left"],
            "clip_right": cfg["clip_right"],
            "clip_low_y": cfg["clip_low_y"],
            "clip_high_y": cfg["clip_high_y"],
        }

    def _cursor(self, tri: dict) -> dict:
        """``initCursor``: start at ``(xrange(0), yrange(0))`` with the start values."""
        return {
            "coords": [
                fm.const(self.vs, tri["xrange"][0]),
                fm.const(self.vs, tri["yrange"][0]),
            ],
            "linear": {
                n: fm.const(self.lg[i], tri["grads"][GRADIENT_BUNDLE_ALL[i]]["start"])
                for i, n in enumerate(LINEAR_FIELDS)
            },
            "hi_s": fm.const(self.hs, tri["tex_hi"]["s_start"]),
            "hi_t": fm.const(self.hs, tri["tex_hi"]["t_start"]),
            "hi_alpha": fm.const(self.hs, tri["hi_alpha"]["start"]),
            "edge": [fm.const(self.cs, v) for v in tri["edge_start"]],
        }

    def _zero_cursor(self) -> dict:
        """What a ``reset_less`` cursor register holds before it is first written."""
        return {
            "coords": [fm.ModelValue(self.vs, 0), fm.ModelValue(self.vs, 0)],
            "linear": {n: fm.ModelValue(self.lg[i], 0) for i, n in enumerate(LINEAR_FIELDS)},
            "hi_s": fm.ModelValue(self.hs, 0),
            "hi_t": fm.ModelValue(self.hs, 0),
            "hi_alpha": fm.ModelValue(self.hs, 0),
            "edge": [fm.ModelValue(self.cs, 0)] * 3,
        }

    # -- the three step functions -----------------------------------------

    def step_horizontal(self, tc: dict, src: dict, move_right: bool) -> dict:
        x_delta = self.one if move_right else self.neg_one
        out = {"coords": [(src["coords"][0] + x_delta).fix_to(self.vs), src["coords"][1]]}
        out["edge"] = []
        for idx in range(3):
            a = tc["coeffs"][idx]["a"]
            delta = a if move_right else (-a).fix_to(self.cs)
            out["edge"].append((src["edge"][idx] + delta).fix_to(self.cs))
        out["linear"] = {}
        for idx, name in enumerate(LINEAR_FIELDS):
            fmt = self.lg[idx]
            grad = tc["linear_dx"][name]
            delta = grad if move_right else (-grad).fix_to(fmt)
            out["linear"][name] = (src["linear"][name] + delta).fix_to(fmt)
        s_delta = tc["tex_dx"][0] if move_right else -tc["tex_dx"][0]
        t_delta = tc["tex_dx"][1] if move_right else -tc["tex_dx"][1]
        a_delta = tc["alpha_dx"] if move_right else -tc["alpha_dx"]
        out["hi_s"] = (src["hi_s"] + s_delta).fix_to(self.hs)
        out["hi_t"] = (src["hi_t"] + t_delta).fix_to(self.hs)
        out["hi_alpha"] = (src["hi_alpha"] + a_delta).fix_to(self.hs)
        return out

    def step_vertical(self, tc: dict, src: dict) -> dict:
        out = {
            "coords": [src["coords"][0], (src["coords"][1] + self.one).fix_to(self.vs)],
            "edge": [
                (src["edge"][idx] + tc["coeffs"][idx]["b"]).fix_to(self.cs) for idx in range(3)
            ],
            "linear": {
                name: (src["linear"][name] + tc["linear_dy"][name]).fix_to(self.lg[idx])
                for idx, name in enumerate(LINEAR_FIELDS)
            },
            "hi_s": (src["hi_s"] + tc["tex_dy"][0]).fix_to(self.hs),
            "hi_t": (src["hi_t"] + tc["tex_dy"][1]).fix_to(self.hs),
            "hi_alpha": (src["hi_alpha"] + tc["alpha_dy"]).fix_to(self.hs),
        }
        return out

    # -- the walk ----------------------------------------------------------

    def run(self, tri: dict, max_cycles: int = 4000) -> list[dict]:
        """Emit the span list for one triangle, starting from a just-loaded ``Idle``."""
        c = self.c
        npix = self.npix
        tc = self._triangle_const(tri)
        start = self._cursor(tri)

        state = "Decide"
        row_guess = probe = bookmark = start
        left_edge = self._zero_cursor()
        next_row_base = self._zero_cursor()
        next_row_left_biased = False
        emit_right = fm.ModelValue(self.vs, 0)
        emit_first_span = False
        first_span_pending = True
        recover_found_inside = False

        spans: list[dict] = []
        for _ in range(max_cycles):
            # -- combinational ---------------------------------------------
            tri_x_start = _floor_pix(tc["xrange"][0])
            tri_x_end = _floor_pix(tc["xrange"][1])
            if tc["enable_clipping"]:
                eff_left = _wrap_signed(tc["clip_left"], npix)
                eff_right = _wrap_signed(tc["clip_right"], npix)
                eff_low_y = _wrap_signed(tc["clip_low_y"], npix)
                eff_high_y = _wrap_signed(tc["clip_high_y"], npix)
            else:
                eff_left = tri_x_start
                eff_right = tri_x_end
                eff_low_y = _floor_pix(tc["yrange"][0])
                eff_high_y = _floor_pix(tc["yrange"][1])
            visible_start = max(eff_left, tri_x_start)
            visible_end = min(eff_right, tri_x_end)

            emit_start = _floor_pix(left_edge["coords"][0])
            emit_end = _floor_pix(emit_right)
            emit_y = _floor_pix(left_edge["coords"][1])
            emit_visible = (
                emit_start <= emit_end
                and emit_end >= eff_left
                and emit_start < eff_right
                and emit_y >= eff_low_y
                and emit_y < eff_high_y
            )

            def inside(cur):
                return all(e >= self.zero for e in cur["edge"])

            def x_pix(cur):
                return _floor_pix(cur["coords"][0])

            if state == "EmitSpan" and emit_visible:
                spans.append(
                    {
                        "x_start": left_edge["coords"][0].raw,
                        "x_end": emit_right.raw,
                        "y": left_edge["coords"][1].raw,
                        "linear": {n: left_edge["linear"][n].raw for n in LINEAR_FIELDS},
                        "hi_s": left_edge["hi_s"].raw,
                        "hi_t": left_edge["hi_t"].raw,
                        "hi_alpha": left_edge["hi_alpha"].raw,
                        "first_span": int(emit_first_span),
                        "x_start_pix": emit_start,
                        "x_end_pix": emit_end,
                        "y_pix": emit_y,
                        "eff_left": eff_left,
                        "eff_right": eff_right,
                        "eff_low_y": eff_low_y,
                        "eff_high_y": eff_high_y,
                    }
                )

            # -- next state ------------------------------------------------
            n_state = state
            n_probe, n_bookmark, n_left_edge = probe, bookmark, left_edge
            n_row_guess, n_next_row_base = row_guess, next_row_base
            n_left_biased, n_recover = next_row_left_biased, recover_found_inside
            n_emit_right, n_emit_first = emit_right, emit_first_span
            n_pending = first_span_pending

            def capture_from_probe():
                """``captureVisibleLeftEdgeFromProbe``."""
                if x_pix(probe) < visible_start:
                    return self.step_horizontal(tc, probe, move_right=True)
                return probe

            def prepare_next_row(base, left_biased):
                return base, left_biased, "AdvanceRow"

            if state == "Decide":
                if inside(probe):
                    n_bookmark, n_state = probe, "SearchLeftToExit"
                else:
                    n_state = "SearchRightToEnter"

            elif state == "RecoverLeft":
                if inside(probe):
                    if not recover_found_inside:
                        n_bookmark, n_recover = probe, True
                    if x_pix(probe) <= visible_start:
                        n_left_edge = capture_from_probe()
                        n_probe, n_recover = bookmark, False
                        n_state = "SearchRightToExit"
                    else:
                        n_probe = self.step_horizontal(tc, probe, move_right=False)
                else:
                    if recover_found_inside:
                        n_left_edge = self.step_horizontal(tc, probe, move_right=True)
                        n_probe, n_recover = bookmark, False
                        n_state = "SearchRightToExit"
                    elif x_pix(probe) <= visible_start:
                        n_probe, n_state = bookmark, "SearchRightToEnter"
                    else:
                        n_probe = self.step_horizontal(tc, probe, move_right=False)

            elif state == "SearchRightToEnter":
                if inside(probe) and x_pix(probe) >= visible_start:
                    n_left_edge = n_bookmark = probe
                    n_state = "SearchRightToExit"
                elif x_pix(probe) >= visible_end:
                    base = row_guess if first_span_pending else bookmark
                    n_next_row_base, n_left_biased, n_state = prepare_next_row(
                        base, not first_span_pending
                    )
                else:
                    n_probe = self.step_horizontal(tc, probe, move_right=True)

            elif state == "SearchLeftToExit":
                if inside(probe):
                    if x_pix(probe) <= visible_start:
                        n_left_edge = capture_from_probe()
                        n_probe, n_state = bookmark, "SearchRightToExit"
                    else:
                        n_probe = self.step_horizontal(tc, probe, move_right=False)
                else:
                    n_left_edge = self.step_horizontal(tc, probe, move_right=True)
                    n_probe, n_state = bookmark, "SearchRightToExit"

            elif state == "SearchRightToExit":
                if inside(probe):
                    if x_pix(probe) >= visible_end:
                        pix = _wrap_signed(visible_end - 1, npix)
                        n_emit_right = fm.ModelValue(self.vs, pix << c.vertex_format.fraction)
                        n_emit_first, n_state = first_span_pending, "EmitSpan"
                    else:
                        n_probe = self.step_horizontal(tc, probe, move_right=True)
                else:
                    n_emit_right = (probe["coords"][0] + self.neg_one).fix_to(self.vs)
                    n_emit_first, n_state = first_span_pending, "EmitSpan"

            elif state == "EmitSpan":
                # o.ready is held high, so both `EmitSpan` blocks collapse to
                # "retire the row" whether or not the span was visible.
                n_pending = False
                n_next_row_base, n_left_biased, n_state = prepare_next_row(left_edge, True)

            elif state == "AdvanceRow":
                next_y = (next_row_base["coords"][1] + self.one).fix_to(self.vs)
                if next_y >= tc["yrange"][1]:
                    n_state, n_pending, n_recover = "Idle", False, False
                else:
                    vertical = self.step_vertical(tc, next_row_base)
                    down_left = self.step_horizontal(tc, vertical, move_right=False)
                    nxt = down_left if next_row_left_biased else vertical
                    n_row_guess = n_probe = n_bookmark = nxt
                    n_recover = False
                    n_state = "RecoverLeft" if next_row_left_biased else "Decide"

            state = n_state
            probe, bookmark, left_edge = n_probe, n_bookmark, n_left_edge
            row_guess, next_row_base = n_row_guess, n_next_row_base
            next_row_left_biased, recover_found_inside = n_left_biased, n_recover
            emit_right, emit_first_span = n_emit_right, n_emit_first
            first_span_pending = n_pending

            if state == "Idle":
                return spans

        raise AssertionError(f"SpanWalkerModel did not finish in {max_cycles} cycles")


# ---------------------------------------------------------------------------
# the RTL side
# ---------------------------------------------------------------------------


def run_rtl(c: Config, triangles: list[dict], max_cycles: int = 4000) -> list[list[dict]]:
    """Feed ``triangles`` through the DUT with ``o.ready`` high; group spans per triangle."""
    dut = SpanWalker(c)
    layout = triangle_setup_output_layout(c)
    collected: list[list[dict]] = []

    async def bench(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.i.valid, 0)
        # The clip inputs are unread by the design (the window arrives with the
        # triangle); drive them anyway so the port list is exercised.
        ctx.set(dut.enable_clipping, 0)
        for port in (dut.clip_left, dut.clip_right, dut.clip_low_y, dut.clip_high_y):
            ctx.set(port, 0)
        await ctx.tick()

        for tri in triangles:
            ctx.set(dut.i.payload, layout.const(tri))
            ctx.set(dut.i.valid, 1)
            # `i.ready` is combinational (`state === Idle`), so sample it
            # *before* the edge that takes the beat.
            while True:
                accepted = ctx.get(dut.i.ready)
                await ctx.tick()
                if accepted:
                    break
            ctx.set(dut.i.valid, 0)

            spans: list[dict] = []
            for _ in range(max_cycles):
                if ctx.get(dut.o.valid):
                    p = dut.o.payload
                    spans.append(
                        {
                            "x_start": ctx.get(p.x_start),
                            "x_end": ctx.get(p.x_end),
                            "y": ctx.get(p.y),
                            "linear": {n: ctx.get(p.linear[n]) for n in LINEAR_FIELDS},
                            "hi_s": ctx.get(p.hi_s),
                            "hi_t": ctx.get(p.hi_t),
                            "hi_alpha": ctx.get(p.hi_alpha),
                            "first_span": ctx.get(p.first_span),
                        }
                    )
                await ctx.tick()
                if not ctx.get(dut.running):
                    break
            else:
                raise AssertionError("SpanWalker did not return to Idle")
            collected.append(spans)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()
    return collected


def _strip(span: dict) -> dict:
    """Only the fields the RTL side can observe (the model carries extras)."""
    return {k: span[k] for k in
            ("x_start", "x_end", "y", "linear", "hi_s", "hi_t", "hi_alpha", "first_span")}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_port_names_and_widths_match_spinal():
    """Every Spinal leaf pairs with an Amaranth one by the mechanical rule.

    This is what ``tools/diff/harness.py`` relies on (plan §4.2 step 4), and
    it fails fast and without a simulator if a layout field is renamed,
    reordered or given the wrong format.
    """
    if not SPINAL_V.exists():
        pytest.skip(f"{SPINAL_V} not generated (run: make diff MOD=SpanWalker)")
    from tools.diff.amports import amaranth_fields
    from tools.diff.naming import camel_to_snake

    header = SPINAL_V.read_text().split(");", 1)[0]
    spinal = []
    for line in header.splitlines():
        mm = re.match(r"\s*(input|output)\s+wire\s*(\[(\d+):0\])?\s+(\w+),?", line)
        if mm:
            spinal.append((mm.group(4), int(mm.group(3)) + 1 if mm.group(3) else 1))
    assert len(spinal) == 231, "SpanWalker_spinal.v port count changed"

    from amaranth.hdl._ir import Fragment

    dut = SpanWalker(Config.voodoo1())
    Fragment.get(dut, None)  # marks it used; the ports are what we are after
    fields = amaranth_fields(dut)
    for name, width in spinal:
        if name in ("clk", "reset"):
            continue
        key = "__".join(camel_to_snake(seg) for seg in name.split("_"))
        assert key in fields, f"{name} has no Amaranth twin ({key})"
        assert fields[key].width == width, f"{name}: spinal {width} bits, amaranth {fields[key].width}"


def test_registers_without_init_are_reset_less():
    """Spinal ``Reg(...)`` has no reset; ``RegInit`` does (README, Phase 0.4).

    The six Scala registers declared with a bare ``Reg`` must not reset, or
    the two designs disagree for the cycles after a reset -- which the diff
    driver provokes by resetting before every seed.
    """
    from amaranth.hdl._ir import Fragment
    from amaranth.hdl._xfrm import LHSMaskCollector

    frag = Fragment.get(SpanWalker(), None)
    collector = LHSMaskCollector()
    collector.visit_stmt(frag.statements["sync"])
    by_name = {chunk[0].name: chunk[0] for chunk in collector.chunks()}
    expect_reset_less = {
        "triangle_const", "row_guess", "probe", "bookmark", "left_edge",
        "next_row_base", "emit_right",
    }
    expect_reset = {
        "state", "next_row_left_biased", "emit_first_span", "first_span_pending",
        "recover_found_inside",
    }
    for name in expect_reset_less:
        assert name in by_name, f"{name} is not a sync register"
        assert by_name[name].reset_less, f"{name} must be reset_less (Spinal `Reg`, no init)"
    for name in expect_reset:
        assert name in by_name, f"{name} is not a sync register"
        assert not by_name[name].reset_less, f"{name} must reset (Spinal `RegInit`)"


def test_clip_disabled_regression():
    """``SpanWalkerClipDisabledRegressionTest``: spans still come out, and ``xEnd >= 0``.

    Clipping off with all four clip registers at zero used to emit a span with
    ``xEnd == -1``, which wrapped in the framebuffer address maths downstream.
    """
    c = small_config(trace=False)
    tri = box_triangle(c, x0=0.0, x1=2.0, y0=0.0, y1=2.0, enable_clipping=False)
    (spans,) = run_rtl(c, [tri])

    assert spans, "expected at least one emitted span with clipping disabled"
    assert all(s["x_end"] >= 0 for s in spans), f"expected non-negative xEnd, got {spans}"
    frac = c.vertex_format.fraction
    first = (spans[0]["x_start"] >> frac, spans[0]["x_end"] >> frac, spans[0]["y"] >> frac)
    assert first == (0, 1, 0), f"unexpected first span: {first}"
    assert spans[0]["first_span"] == 1


def test_clip_left_offscreen_span_is_clamped():
    """``SpanWalkerClipLeftFormalDut``: a span starting left of ``clipLeft`` is clamped.

    ``xrange = (-1, 1)`` with clipping on and ``clipLeft = 0``: the walker must
    hand out ``xStart = 0``, not ``-1``.  (The DUT's ``xEnd === 1`` is not
    asserted -- see the module docstring.)
    """
    c = small_config(trace=False)
    tri = box_triangle(
        c, x0=-1.0, x1=1.0, y0=0.0, y1=1.0,
        enable_clipping=True, clip_left=0, clip_right=c.max_fb_dims[0],
        clip_low_y=0, clip_high_y=c.max_fb_dims[1],
    )
    (spans,) = run_rtl(c, [tri])

    assert spans, "expected the clipped row to be emitted"
    frac = c.vertex_format.fraction
    for s in spans:
        assert s["x_start"] >> frac == 0, f"xStart not clamped to clipLeft: {s}"
        assert s["y"] >> frac == 0


@pytest.mark.parametrize("trace", [False])
def test_formal_sequencing_invariants(trace):
    """``SpanWalkerFormalDut``'s sequencing properties, on its own two boxes.

    ``y`` advances by exactly one per span within a primitive, ``firstSpan``
    is set on the first span of each primitive and clear on every later one,
    and the second primitive starts at ``y == 2``.
    """
    c = small_config(trace=trace)
    tri0 = box_triangle(
        c, x0=1.0, x1=2.0, y0=0.0, y1=2.0,
        clip_right=c.max_fb_dims[0], clip_high_y=c.max_fb_dims[1],
    )
    tri1 = box_triangle(
        c, x0=0.0, x1=1.0, y0=2.0, y1=4.0,
        clip_right=c.max_fb_dims[0], clip_high_y=c.max_fb_dims[1],
    )
    per_tri = run_rtl(c, [tri0, tri1])
    frac = c.vertex_format.fraction

    assert all(spans for spans in per_tri), f"both primitives must emit: {per_tri}"
    for spans in per_tri:
        assert spans[0]["first_span"] == 1
        assert all(s["first_span"] == 0 for s in spans[1:])
        ys = [s["y"] >> frac for s in spans]
        assert ys == list(range(ys[0], ys[0] + len(ys))), f"y must step by 1: {ys}"
    assert (per_tri[0][0]["y"] >> frac) == 0
    assert (per_tri[1][0]["y"] >> frac) == 2


# The suite the span invariants and the model comparison both run over: flat,
# sloped, clipped on each side, entirely off-screen, and reversed (x1 < x0).
def _suite(c: Config) -> list[dict]:
    w, h = c.max_fb_dims
    return [
        box_triangle(c, x0=0.0, x1=2.0, y0=0.0, y1=2.0),
        box_triangle(c, x0=1.0, x1=2.0, y0=0.0, y1=2.0),
        box_triangle(c, x0=0.0, x1=1.0, y0=2.0, y1=4.0),
        box_triangle(c, x0=-1.0, x1=1.0, y0=0.0, y1=1.0,
                     enable_clipping=True, clip_left=0, clip_right=w,
                     clip_low_y=0, clip_high_y=h),
        box_triangle(c, x0=0.0, x1=3.0, y0=0.0, y1=3.0,
                     enable_clipping=True, clip_left=1, clip_right=2,
                     clip_low_y=1, clip_high_y=2),
        # fully outside the clip window in y
        box_triangle(c, x0=0.0, x1=2.0, y0=0.0, y1=2.0,
                     enable_clipping=True, clip_left=0, clip_right=w,
                     clip_low_y=3, clip_high_y=4),
        # reversed x range
        box_triangle(c, x0=2.0, x1=0.0, y0=0.0, y1=2.0),
        # with gradients, so the accumulators and their fixTo saturation move
        box_triangle(c, x0=0.0, x1=3.0, y0=0.0, y1=3.0,
                     grad_start=0.5, grad_dx=0.5, grad_dy=-0.5,
                     tex_start=-1.0, tex_dx=0.5, tex_dy=0.5),
        box_triangle(c, x0=0.0, x1=3.0, y0=0.0, y1=3.0,
                     enable_clipping=True, clip_left=1, clip_right=3,
                     clip_low_y=0, clip_high_y=3,
                     grad_start=-1.5, grad_dx=-0.5, grad_dy=0.5,
                     tex_start=1.5, tex_dx=-0.5, tex_dy=-0.5),
    ]


def test_span_invariants():
    """The five ``GenerationFlags.formal`` asserts of ``SpanWalker.scala:390-403``.

    Every emitted span must be non-empty and inside the effective clip window
    in both axes -- the property the ``emitVisible`` gate exists to guarantee.
    """
    c = small_config(trace=False)
    model = SpanWalkerModel(c)
    for tri in _suite(c):
        for span in model.run(tri):
            assert span["x_start_pix"] <= span["x_end_pix"], span
            assert span["x_start_pix"] >= span["eff_left"], span
            assert span["x_end_pix"] < span["eff_right"], span
            assert span["y_pix"] >= span["eff_low_y"], span
            assert span["y_pix"] < span["eff_high_y"], span


def test_model_matches_rtl():
    """The Python model and the Amaranth RTL emit the same spans, payload for payload.

    This is the unit-level check that the span iteration, the clip decisions
    *and* the gradient stepping (wide ``SQ(48,30)``-style accumulators in the
    real config, their shrunken equivalents here) agree with an independent
    derivation.
    """
    c = small_config(trace=False)
    triangles = _suite(c)
    model = SpanWalkerModel(c)
    expected = [[_strip(s) for s in model.run(tri)] for tri in triangles]
    actual = run_rtl(c, triangles)
    for idx, (want, got) in enumerate(zip(expected, actual)):
        assert got == want, f"triangle {idx}: rtl {got} != model {want}"


def test_model_matches_rtl_voodoo1_formats():
    """Same, in the real ``Config.voodoo1()`` formats.

    The point is the wide accumulators: ``w`` is ``SQ(48,30)`` and ``hiS`` /
    ``hiT`` / ``hiAlpha`` are ``SQ(46,30)``, so a ``fixTo`` that saturated at
    the wrong width would show up here and not in the shrunken config.
    """
    c = Config.voodoo1()
    triangles = [
        box_triangle(c, x0=0.0, x1=4.0, y0=0.0, y1=3.0,
                     grad_start=1.5, grad_dx=0.25, grad_dy=-0.75,
                     tex_start=-2.5, tex_dx=0.125, tex_dy=0.25),
        box_triangle(c, x0=-2.0, x1=5.0, y0=0.0, y1=2.0,
                     enable_clipping=True, clip_left=1, clip_right=4,
                     clip_low_y=0, clip_high_y=2,
                     grad_start=-3.0, grad_dx=-0.5, grad_dy=0.5,
                     tex_start=7.75, tex_dx=-0.0625, tex_dy=0.5),
    ]
    model = SpanWalkerModel(c)
    expected = [[_strip(s) for s in model.run(tri)] for tri in triangles]
    actual = run_rtl(c, triangles)
    assert actual == expected
