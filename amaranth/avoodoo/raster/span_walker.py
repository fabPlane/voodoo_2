"""``SpanWalker``: walk a set-up triangle row by row and emit one span per row.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/raster/SpanWalker.scala`` (SpinalHDL
1.15.0), plan Phase 3.  Same ports, same eight-state machine, same registers,
same AFix formats and rounding -- every accumulator step is
``(cur + delta).fixTo(<the same QFormat>)``, so the gradient accumulators stay
in the wide ``SQ(48,30)`` (``wAccumFormat``) / ``SQ(46,30)``
(``texCoordsHiFormat``) formats the Scala gives them and the edge functions in
``SQ(34,8)`` (``coefficientFormat``).

How it works, in the Scala's own terms: one triangle arrives on ``i`` as a
``TriangleSetup.Output``.  Its constant part (edge coefficients, both gradient
sets, the TMU/pixel config, the clip window) is latched into
``triangleConst``; four cursors -- ``rowGuess``, ``probe``, ``bookmark`` and
``leftEdge`` -- carry the *interpolated* state (x, y, the three edge functions,
the six linear channels, s/t/alpha in the hi format) and are stepped one pixel
at a time by :meth:`SpanWalker._step_horizontal` / ``_step_vertical``.  The
state machine searches left and right along the current row for the first and
last inside-and-visible pixel, emits that span on ``o``, then moves down one
row and repeats until ``y`` leaves ``yrange(1)``.

Fidelity notes, each one a thing that would silently diverge if ported by
eye:

* **Reset.** Spinal's plain ``Reg(...)`` has *no* reset; only ``RegInit`` does.
  ``triangleConst``, the five cursors and ``emitRight`` are therefore
  ``reset_less=True``; ``state``, ``nextRowLeftBiased``, ``emitFirstSpan``,
  ``firstSpanPending`` and ``recoverFoundInside`` reset, to ``Idle`` / ``0``.
* **``floor(0).asSInt`` is 12 bits, not 16.**  ``AFix.floor(0)`` on
  ``SQ(16,4)`` rounds the *bounds* to ``(2047, -2048)``, i.e.
  ``vertexFormat.nonFraction`` bits.  Every pixel-domain comparison in this
  module is at that width.
* **``visibleEndPix - 1`` wraps at that width.**  Spinal's ``SInt`` ``-`` does
  not grow, so the subtraction is truncated back to ``nonFraction`` bits
  before ``pixelToVertex`` re-scales it; :func:`_sresize` does that.
* **``clipLeft.resize(nonFraction bits)``** zero-extends (or *truncates*, in
  the shrunken test configs where ``nonFraction < 10``) and only then becomes
  an ``SInt``.
* **``stepDownLeft`` is dead code** in the Scala -- ``AdvanceRow`` builds the
  down-left cursor as ``stepVertical`` followed by ``stepHorizontal(left)``.
  It is ported anyway, and equally uncalled, so the two files still read as a
  transliteration.
* The ``GenerationFlags.formal`` block is dropped from the RTL per plan §2/§3;
  its five span invariants are asserted in ``tests/test_span_walker.py`` and
  come back as SymbiYosys properties in Phase 8.

The companion ``object SpanWalker`` bundles live here rather than in
``avoodoo/core/core_types.py`` because ``TriangleConst`` embeds
``TmuConfig``/``PixelPipelineConfig`` from that file; ``Rasterizer`` imports
``LinearState``/``StepX``/``Output`` from here exactly as ``Rasterizer.scala``
does.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, Value, signed, unsigned
from amaranth.lib import data, enum, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (
    GRADIENT_BUNDLE_ALL,
    TRACE_PRIMITIVE_KEY_LAYOUT,
    coefficients_layout,
    pixel_pipeline_config_layout,
    tmu_config_layout,
    triangle_setup_output_layout,
    vertex2d,
)
from avoodoo.lib.fixed import AFixShape, AFixValue
from avoodoo.lib.stream import fire

__all__ = [
    "LINEAR_FIELDS",
    "low_grad_formats",
    "LinearState",
    "StepX",
    "TriangleConst",
    "Cursor",
    "SpanWalkerOutput",
    "WalkerState",
    "SpanWalker",
    # snake_case aliases, for the sibling Phase 3 ports that asked for them
    "linear_state_layout",
    "step_x_layout",
    "span_walker_output_layout",
    "cursor_layout",
    "triangle_const_layout",
]


#: ``SpanWalker.LinearState.all`` order -- ``Seq(red, green, blue, depth,
#: alpha, w)``.  Lines up index for index with ``GradientBundle.all.take(6)``.
LINEAR_FIELDS = ("red", "green", "blue", "depth", "alpha", "w")


def low_grad_formats(c: Config) -> tuple:
    """``private val lowGradFormats`` -- the format of each ``LinearState`` field."""
    return (
        c.v_color_format,
        c.v_color_format,
        c.v_color_format,
        c.v_depth_format,
        c.v_color_format,
        c.w_accum_format,
    )


# ---------------------------------------------------------------------------
# object SpanWalker -- the companion bundles
# ---------------------------------------------------------------------------


class LinearState(data.StructLayout):
    """``case class LinearState(c: Config)``.

    Six linearly interpolated channels.  ``w`` is in ``wAccumFormat``
    (``SQ(48,30)``) and ``depth`` in ``vDepthFormat`` (``SQ(32,12)``); the
    other four are ``vColorFormat`` (``SQ(24,12)``).
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__(
            {
                name: AFixShape.from_q(fmt)
                for name, fmt in zip(LINEAR_FIELDS, low_grad_formats(c))
            }
        )


class StepX(data.StructLayout):
    """``case class StepX(c: Config)`` -- the per-pixel deltas handed downstream."""

    def __init__(self, c: Config) -> None:
        self.c = c
        hi = AFixShape.from_q(c.tex_coords_hi_format)
        super().__init__(
            {
                "linear": LinearState(c),
                "tex": data.ArrayLayout(hi, 2),
                "alpha": hi,
            }
        )


class TriangleConst(data.StructLayout):
    """``case class TriangleConst(c: Config)`` -- everything constant per triangle."""

    def __init__(self, c: Config) -> None:
        self.c = c
        hi = AFixShape.from_q(c.tex_coords_hi_format)
        members = {
            "coeffs": data.ArrayLayout(coefficients_layout(c), 3),
            "xrange": vertex2d(c.vertex_format),
            "yrange": vertex2d(c.vertex_format),
            "linear_dx": LinearState(c),
            "linear_dy": LinearState(c),
            "tex_dx": data.ArrayLayout(hi, 2),
            "tex_dy": data.ArrayLayout(hi, 2),
            "alpha_dx": hi,
            "alpha_dy": hi,
            "tmu_config": tmu_config_layout(c),
            "pixel_config": pixel_pipeline_config_layout(c),
            "enable_clipping": unsigned(1),
            "clip_left": unsigned(10),
            "clip_right": unsigned(10),
            "clip_low_y": unsigned(10),
            "clip_high_y": unsigned(10),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PRIMITIVE_KEY_LAYOUT
        super().__init__(members)


class Cursor(data.StructLayout):
    """``case class Cursor(c: Config)`` -- one walking position and its state."""

    def __init__(self, c: Config) -> None:
        self.c = c
        hi = AFixShape.from_q(c.tex_coords_hi_format)
        super().__init__(
            {
                "coords": vertex2d(c.vertex_format),
                "linear": LinearState(c),
                "hi_s": hi,
                "hi_t": hi,
                "hi_alpha": hi,
                "edge": data.ArrayLayout(AFixShape.from_q(c.coefficient_format), 3),
            }
        )


class SpanWalkerOutput(data.StructLayout):
    """``case class Output(c: Config)`` -- one span, consumed by ``Rasterizer``."""

    def __init__(self, c: Config) -> None:
        self.c = c
        v = AFixShape.from_q(c.vertex_format)
        hi = AFixShape.from_q(c.tex_coords_hi_format)
        members = {
            "x_start": v,
            "x_end": v,
            "y": v,
            "linear": LinearState(c),
            "hi_s": hi,
            "hi_t": hi,
            "hi_alpha": hi,
            "step_x": StepX(c),
            "tmu_config": tmu_config_layout(c),
            "pixel_config": pixel_pipeline_config_layout(c),
            "first_span": unsigned(1),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PRIMITIVE_KEY_LAYOUT
        super().__init__(members)


#: snake_case spellings of the three layouts ``rasterizer.py`` imports.
linear_state_layout = LinearState
step_x_layout = StepX
span_walker_output_layout = SpanWalkerOutput
cursor_layout = Cursor
triangle_const_layout = TriangleConst


class WalkerState(enum.Enum, shape=unsigned(3)):
    """``object WalkerState extends SpinalEnum``, declaration order = encoding."""

    IDLE = 0
    DECIDE = 1
    RECOVER_LEFT = 2
    SEARCH_RIGHT_TO_ENTER = 3
    SEARCH_LEFT_TO_EXIT = 4
    SEARCH_RIGHT_TO_EXIT = 5
    EMIT_SPAN = 6
    ADVANCE_ROW = 7


# ---------------------------------------------------------------------------
# small value helpers
# ---------------------------------------------------------------------------


def _sresize(value, width: int) -> Value:
    """Spinal ``UInt``/``SInt`` ``.resize(width bits)`` followed by ``.asSInt``.

    Narrowing keeps the low bits (it *wraps*, which is what
    ``visibleEndPix - 1`` and ``clipLeft.resize`` rely on); widening extends
    according to the **source**'s signedness, then the result is reinterpreted
    as signed.
    """
    v = Value.cast(value)
    if len(v) > width:
        return v[:width].as_signed()
    if len(v) < width:
        pad = v[-1] if v.shape().signed else Const(0, 1)
        return Cat(v, pad.replicate(width - len(v))).as_signed()
    return v.as_signed()


def _floor_pix(value: AFixValue) -> Value:
    """``value.floor(0).asSInt`` -- ``vertexFormat.nonFraction`` bits signed."""
    return value.floor(0).as_signed()


# ---------------------------------------------------------------------------
# the component
# ---------------------------------------------------------------------------


class SpanWalker(wiring.Component):
    """``case class SpanWalker(c: Config, formalStrong: Boolean = false)``.

    ``formal_strong`` only ever gated the ``GenerationFlags.formal`` asserts in
    the Scala, which this port does not emit; it is accepted so the
    constructor signature matches and ``tests/test_span_walker.py`` can name
    the two upstream formal DUTs.

    ``enable_clipping`` / ``clip_left`` / ``clip_right`` / ``clip_low_y`` /
    ``clip_high_y`` are inputs on the Spinal component too, and are equally
    **unread** there: the clip window that is actually used comes in with the
    triangle, through ``TriangleSetup.Output.config``.  They are kept so the
    port list matches port for port.
    """

    def __init__(self, c: Config | None = None, formal_strong: bool = False) -> None:
        # `c` has no default in the Scala; the `None` case only exists so the
        # lockstep-diff registry can build the module the way it builds every
        # other core-proper one ("the factory reads Config.voodoo1() for
        # itself" -- tools/diff/registry.py).
        self.c = c = Config.voodoo1() if c is None else c
        self.formal_strong = formal_strong
        super().__init__(
            {
                "i": In(stream.Signature(triangle_setup_output_layout(c))),
                "o": Out(stream.Signature(SpanWalkerOutput(c))),
                "enable_clipping": In(1),
                "clip_left": In(10),
                "clip_right": In(10),
                "clip_low_y": In(10),
                "clip_high_y": In(10),
                "running": Out(1),
            }
        )

    # -- the Scala's `def`s, as methods ------------------------------------
    #
    # Each takes the module and a domain name, because the same `def` is used
    # both to drive registers (`dst` is a Reg, Spinal infers the clocked
    # assignment) and to drive the combinational `vertical` / `downLeft`
    # cursors in `AdvanceRow`.

    def _step_horizontal(self, m: Module, dom: str, dst, src, move_right: bool) -> None:
        """``def stepHorizontal(dst, src, moveRight)``."""
        c = self.c
        tc = self._triangle_const
        x_delta = self._one if move_right else self._neg_one
        m.d[dom] += dst.coords[0].eq((src.coords[0] + x_delta).fix_to(c.vertex_format))
        m.d[dom] += dst.coords[1].eq(src.coords[1])
        for idx in range(3):
            a = tc.coeffs[idx].a
            delta = a if move_right else (-a).fix_to(c.coefficient_format)
            m.d[dom] += dst.edge[idx].eq(
                (src.edge[idx] + delta).fix_to(c.coefficient_format)
            )
        for idx, name in enumerate(LINEAR_FIELDS):
            fmt = self._low_grad[idx]
            grad = tc.linear_dx[name]
            delta = grad if move_right else (-grad).fix_to(fmt)
            m.d[dom] += dst.linear[name].eq((src.linear[name] + delta).fix_to(fmt))
        # Note: no `.fixTo` on the negated tex/alpha deltas, unlike the edge
        # and linear ones above -- the Scala negates in place and lets the
        # following add absorb the extra bit.
        s_delta = tc.tex_dx[0] if move_right else -tc.tex_dx[0]
        t_delta = tc.tex_dx[1] if move_right else -tc.tex_dx[1]
        a_delta = tc.alpha_dx if move_right else -tc.alpha_dx
        hi = c.tex_coords_hi_format
        m.d[dom] += dst.hi_s.eq((src.hi_s + s_delta).fix_to(hi))
        m.d[dom] += dst.hi_t.eq((src.hi_t + t_delta).fix_to(hi))
        m.d[dom] += dst.hi_alpha.eq((src.hi_alpha + a_delta).fix_to(hi))

    def _step_vertical(self, m: Module, dom: str, dst, src) -> None:
        """``def stepVertical(dst, src)`` -- one row down, same x."""
        c = self.c
        tc = self._triangle_const
        m.d[dom] += dst.coords[0].eq(src.coords[0])
        m.d[dom] += dst.coords[1].eq((src.coords[1] + self._one).fix_to(c.vertex_format))
        for idx in range(3):
            m.d[dom] += dst.edge[idx].eq(
                (src.edge[idx] + tc.coeffs[idx].b).fix_to(c.coefficient_format)
            )
        for idx, name in enumerate(LINEAR_FIELDS):
            fmt = self._low_grad[idx]
            m.d[dom] += dst.linear[name].eq(
                (src.linear[name] + tc.linear_dy[name]).fix_to(fmt)
            )
        hi = c.tex_coords_hi_format
        m.d[dom] += dst.hi_s.eq((src.hi_s + tc.tex_dy[0]).fix_to(hi))
        m.d[dom] += dst.hi_t.eq((src.hi_t + tc.tex_dy[1]).fix_to(hi))
        m.d[dom] += dst.hi_alpha.eq((src.hi_alpha + tc.alpha_dy).fix_to(hi))

    def _step_down_left(self, m: Module, dom: str, dst, src) -> None:
        """``def stepDownLeft(dst, src)``.

        Unused, here as in the Scala: ``AdvanceRow`` composes ``stepVertical``
        with ``stepHorizontal(moveRight = false)`` instead, which is *not* the
        same arithmetic (two ``fixTo`` saturations rather than one) and is
        what the hardware does.
        """
        c = self.c
        tc = self._triangle_const
        m.d[dom] += dst.coords[0].eq(
            (src.coords[0] + self._neg_one).fix_to(c.vertex_format)
        )
        m.d[dom] += dst.coords[1].eq((src.coords[1] + self._one).fix_to(c.vertex_format))
        for idx in range(3):
            coeff = tc.coeffs[idx]
            m.d[dom] += dst.edge[idx].eq(
                (src.edge[idx] + coeff.b - coeff.a).fix_to(c.coefficient_format)
            )
        for idx, name in enumerate(LINEAR_FIELDS):
            fmt = self._low_grad[idx]
            m.d[dom] += dst.linear[name].eq(
                (src.linear[name] + tc.linear_dy[name] - tc.linear_dx[name]).fix_to(fmt)
            )
        hi = c.tex_coords_hi_format
        m.d[dom] += dst.hi_s.eq((src.hi_s + tc.tex_dy[0] - tc.tex_dx[0]).fix_to(hi))
        m.d[dom] += dst.hi_t.eq((src.hi_t + tc.tex_dy[1] - tc.tex_dx[1]).fix_to(hi))
        m.d[dom] += dst.hi_alpha.eq(
            (src.hi_alpha + tc.alpha_dy - tc.alpha_dx).fix_to(hi)
        )

    def _tmu_config_from_per_triangle(self, m: Module, dom: str, dst, src) -> None:
        """``TriangleSetup.TmuConfig.fromPerTriangle(c, src)``, field for field."""
        m.d[dom] += [
            dst.texture_enable.eq(src.fbz_color_path.texture_enable),
            dst.texture_mode.eq(src.tmu_texture_mode),
            dst.tex_base_addr.eq(src.tmu_tex_base_addr),
            dst.tex_base_addr1.eq(src.tmu_tex_base_addr1),
            dst.tex_base_addr2.eq(src.tmu_tex_base_addr2),
            dst.tex_base_addr38.eq(src.tmu_tex_base_addr38),
            dst.t_lod.eq(src.tmu_tlod),
        ]
        for lhs, rhs in (
            (dst.d_sd_x, src.tmud_sd_x),
            (dst.d_td_x, src.tmud_td_x),
            (dst.d_sd_y, src.tmud_sd_y),
            (dst.d_td_y, src.tmud_td_y),
        ):
            m.d[dom] += lhs.eq(rhs)
        if self.c.packed_tex_layout:
            m.d[dom] += dst.tex_tables.eq(src.tex_tables)

    def _pixel_pipeline_config_from_per_triangle(self, m: Module, dom: str, dst, src) -> None:
        """``TriangleSetup.PixelPipelineConfig.fromPerTriangle(c, src)``."""
        m.d[dom] += [
            dst.fbz_color_path.eq(src.fbz_color_path),
            dst.fog_mode.eq(src.fog_mode),
            dst.alpha_mode.eq(src.alpha_mode),
        ]

    def _init_triangle_const(self, m: Module, dom: str, dst, src) -> None:
        """``def initTriangleConst(dst, input)``."""
        c = self.c
        m.d[dom] += [
            dst.coeffs.eq(src.coeffs),
            dst.xrange.eq(src.xrange),
            dst.yrange.eq(src.yrange),
        ]
        # `linearDx.all.zip(input.grads.all.take(6))` -- the first six
        # gradients are exactly LinearState's six fields, format for format.
        for idx, name in enumerate(LINEAR_FIELDS):
            grad = src.grads[GRADIENT_BUNDLE_ALL[idx]]
            m.d[dom] += dst.linear_dx[name].eq(grad.d[0])
            m.d[dom] += dst.linear_dy[name].eq(grad.d[1])
        m.d[dom] += [
            dst.tex_dx[0].eq(src.tex_hi.d_sd_x),
            dst.tex_dx[1].eq(src.tex_hi.d_td_x),
            dst.tex_dy[0].eq(src.tex_hi.d_sd_y),
            dst.tex_dy[1].eq(src.tex_hi.d_td_y),
            dst.alpha_dx.eq(src.hi_alpha.d_ad_x),
            dst.alpha_dy.eq(src.hi_alpha.d_ad_y),
        ]
        self._tmu_config_from_per_triangle(m, dom, dst.tmu_config, src.config)
        self._pixel_pipeline_config_from_per_triangle(m, dom, dst.pixel_config, src.config)
        m.d[dom] += [
            dst.enable_clipping.eq(src.config.enable_clipping),
            dst.clip_left.eq(src.config.clip_left),
            dst.clip_right.eq(src.config.clip_right),
            dst.clip_low_y.eq(src.config.clip_low_y),
            dst.clip_high_y.eq(src.config.clip_high_y),
        ]
        if c.trace.enabled:
            m.d[dom] += dst.trace.eq(src.trace)

    def _init_cursor(self, m: Module, dom: str, dst, src) -> None:
        """``def initCursor(dst, input)`` -- start at ``(xrange(0), yrange(0))``."""
        m.d[dom] += [
            dst.coords[0].eq(src.xrange[0]),
            dst.coords[1].eq(src.yrange[0]),
            dst.edge.eq(src.edge_start),
        ]
        for idx, name in enumerate(LINEAR_FIELDS):
            m.d[dom] += dst.linear[name].eq(src.grads[GRADIENT_BUNDLE_ALL[idx]].start)
        m.d[dom] += [
            dst.hi_s.eq(src.tex_hi.s_start),
            dst.hi_t.eq(src.tex_hi.t_start),
            dst.hi_alpha.eq(src.hi_alpha.start),
        ]

    def _inside_triangle(self, cursor) -> Value:
        """``def insideTriangle(cursor)`` -- all three edge functions >= 0."""
        z = self._zero
        return (
            (cursor.edge[0] >= z)
            & (cursor.edge[1] >= z)
            & (cursor.edge[2] >= z)
        )

    def _x_pix(self, cursor) -> Value:
        """``def xPix(cursor) = cursor.coords(0).floor(0).asSInt``."""
        return _floor_pix(cursor.coords[0])

    def _pixel_to_vertex(self, pix: Value) -> AFixValue:
        """``def pixelToVertex(pix: SInt): AFix`` -- raw = ``pix ## fracZeros``."""
        c = self.c
        shape = AFixShape.from_q(c.vertex_format)
        raw = Cat(Const(0, c.vertex_format.fraction), _sresize(pix, c.vertex_format.non_fraction))
        return AFixValue.from_raw(shape, raw)

    def _prepare_next_row(self, m: Module, base, left_biased: bool) -> None:
        """``def prepareNextRow(base, leftBiasedGuess)``."""
        m.d.sync += [
            self._next_row_base.eq(base),
            self._next_row_left_biased.eq(1 if left_biased else 0),
            self._state.eq(WalkerState.ADVANCE_ROW),
        ]

    def _capture_visible_left_edge_from_probe(self, m: Module) -> None:
        """``def captureVisibleLeftEdgeFromProbe()``.

        Recover/search-left can overshoot by one pixel when walking clipped
        rows; clamp that case back onto the first visible pixel instead of
        preserving -1.
        """
        with m.If(self._x_pix(self._probe) < self._visible_start_pix):
            self._step_horizontal(m, "sync", self._left_edge, self._probe, move_right=True)
        with m.Else():
            m.d.sync += self._left_edge.eq(self._probe)

    # -- elaboration --------------------------------------------------------

    def elaborate(self, platform):
        c = self.c
        m = Module()

        self._low_grad = low_grad_formats(c)
        vfmt = AFixShape.from_q(c.vertex_format)
        npix = c.vertex_format.non_fraction

        # -- registers (Reg vs RegInit: see the module docstring) ----------
        state = Signal(WalkerState, init=WalkerState.IDLE)
        triangle_const = Signal(TriangleConst(c), reset_less=True)
        row_guess = Signal(Cursor(c), reset_less=True)
        probe = Signal(Cursor(c), reset_less=True)
        bookmark = Signal(Cursor(c), reset_less=True)
        left_edge = Signal(Cursor(c), reset_less=True)
        next_row_base = Signal(Cursor(c), reset_less=True)
        next_row_left_biased = Signal(init=0)
        emit_right = Signal(vfmt, reset_less=True)
        emit_first_span = Signal(init=0)
        first_span_pending = Signal(init=0)
        recover_found_inside = Signal(init=0)

        self._state = state
        self._triangle_const = triangle_const
        self._probe = probe
        self._left_edge = left_edge
        self._next_row_base = next_row_base
        self._next_row_left_biased = next_row_left_biased

        m.d.comb += self.running.eq(state != WalkerState.IDLE)
        m.d.comb += self.i.ready.eq(state == WalkerState.IDLE)

        # `val one/negOne = AFix(c.vertexFormat); one := 1.0` and
        # `val zero = 0.SQ(1 bits, 0 bits)` -- a 2-bit (1, -2, 0) constant.
        self._one = vfmt.const(1.0)
        self._neg_one = vfmt.const(-1.0)
        self._zero = AFixShape.sq(1, 0).const(0)

        # -- the pixel-domain window ---------------------------------------
        triangle_x_start_pix = Signal(signed(npix))
        triangle_x_end_pix = Signal(signed(npix))
        clip_left_pix = Signal(signed(npix))
        clip_right_pix = Signal(signed(npix))
        clip_low_y_pix = Signal(signed(npix))
        clip_high_y_pix = Signal(signed(npix))
        m.d.comb += [
            triangle_x_start_pix.eq(_floor_pix(triangle_const.xrange[0])),
            triangle_x_end_pix.eq(_floor_pix(triangle_const.xrange[1])),
            clip_left_pix.eq(_sresize(triangle_const.clip_left, npix)),
            clip_right_pix.eq(_sresize(triangle_const.clip_right, npix)),
            clip_low_y_pix.eq(_sresize(triangle_const.clip_low_y, npix)),
            clip_high_y_pix.eq(_sresize(triangle_const.clip_high_y, npix)),
        ]

        effective_clip_left_pix = Signal(signed(npix))
        effective_clip_right_pix = Signal(signed(npix))
        effective_clip_low_y_pix = Signal(signed(npix))
        effective_clip_high_y_pix = Signal(signed(npix))
        visible_start_pix = Signal(signed(npix))
        visible_end_pix = Signal(signed(npix))
        m.d.comb += [
            effective_clip_left_pix.eq(
                Mux(triangle_const.enable_clipping, clip_left_pix, triangle_x_start_pix)
            ),
            effective_clip_right_pix.eq(
                Mux(triangle_const.enable_clipping, clip_right_pix, triangle_x_end_pix)
            ),
            effective_clip_low_y_pix.eq(
                Mux(
                    triangle_const.enable_clipping,
                    clip_low_y_pix,
                    _floor_pix(triangle_const.yrange[0]),
                )
            ),
            effective_clip_high_y_pix.eq(
                Mux(
                    triangle_const.enable_clipping,
                    clip_high_y_pix,
                    _floor_pix(triangle_const.yrange[1]),
                )
            ),
        ]
        m.d.comb += [
            visible_start_pix.eq(
                Mux(
                    effective_clip_left_pix > triangle_x_start_pix,
                    effective_clip_left_pix,
                    triangle_x_start_pix,
                )
            ),
            visible_end_pix.eq(
                Mux(
                    effective_clip_right_pix < triangle_x_end_pix,
                    effective_clip_right_pix,
                    triangle_x_end_pix,
                )
            ),
        ]
        self._visible_start_pix = visible_start_pix

        emit_start_pix = _floor_pix(left_edge.coords[0])
        emit_end_pix = _floor_pix(emit_right)
        emit_y_pix = _floor_pix(left_edge.coords[1])
        emit_visible_x = Signal()
        emit_visible_y = Signal()
        emit_visible = Signal()
        m.d.comb += [
            emit_visible_x.eq(
                (emit_start_pix <= emit_end_pix)
                & (emit_end_pix >= effective_clip_left_pix)
                & (emit_start_pix < effective_clip_right_pix)
            ),
            emit_visible_y.eq(
                (emit_y_pix >= effective_clip_low_y_pix)
                & (emit_y_pix < effective_clip_high_y_pix)
            ),
            emit_visible.eq(emit_visible_x & emit_visible_y),
        ]

        m.d.comb += self.o.valid.eq((state == WalkerState.EMIT_SPAN) & emit_visible)

        # -- the output payload, straight off leftEdge / triangleConst -----
        payload = self.o.payload
        m.d.comb += [
            payload.x_start.eq(left_edge.coords[0]),
            payload.x_end.eq(emit_right),
            payload.y.eq(left_edge.coords[1]),
            payload.linear.eq(left_edge.linear),
            payload.hi_s.eq(left_edge.hi_s),
            payload.hi_t.eq(left_edge.hi_t),
            payload.hi_alpha.eq(left_edge.hi_alpha),
            payload.step_x.linear.eq(triangle_const.linear_dx),
            payload.step_x.tex.eq(triangle_const.tex_dx),
            payload.step_x.alpha.eq(triangle_const.alpha_dx),
            payload.tmu_config.eq(triangle_const.tmu_config),
            payload.pixel_config.eq(triangle_const.pixel_config),
            payload.first_span.eq(emit_first_span),
        ]
        if c.trace.enabled:
            m.d.comb += payload.trace.eq(triangle_const.trace)

        # -- `AdvanceRow`'s three combinational cursors --------------------
        #
        # Declared inside the `.otherwise` branch in the Scala; Spinal hoists
        # them to component scope and drives them unconditionally (they show
        # up as `_zz_rowGuess_*` in SpanWalker_spinal.v), which is what this
        # does.  They are only *read* in the AdvanceRow branch.
        vertical = Signal(Cursor(c))
        down_left = Signal(Cursor(c))
        next_cursor = Signal(Cursor(c))
        self._step_vertical(m, "comb", vertical, next_row_base)
        self._step_horizontal(m, "comb", down_left, vertical, move_right=False)
        m.d.comb += next_cursor.eq(vertical)
        with m.If(next_row_left_biased):
            m.d.comb += next_cursor.eq(down_left)

        next_y = (next_row_base.coords[1] + self._one).fix_to(c.vertex_format)

        inside_probe = Signal()
        m.d.comb += inside_probe.eq(self._inside_triangle(probe))
        x_pix_probe = self._x_pix(probe)

        # -- the eight `when(state === ...)` blocks, in Scala order --------

        with m.If((state == WalkerState.IDLE) & fire(self.i)):
            self._init_triangle_const(m, "sync", triangle_const, self.i.payload)
            self._init_cursor(m, "sync", row_guess, self.i.payload)
            self._init_cursor(m, "sync", probe, self.i.payload)
            self._init_cursor(m, "sync", bookmark, self.i.payload)
            m.d.sync += first_span_pending.eq(1)
            m.d.sync += state.eq(WalkerState.DECIDE)

        with m.If(state == WalkerState.DECIDE):
            with m.If(inside_probe):
                m.d.sync += bookmark.eq(probe)
                m.d.sync += state.eq(WalkerState.SEARCH_LEFT_TO_EXIT)
            with m.Else():
                m.d.sync += state.eq(WalkerState.SEARCH_RIGHT_TO_ENTER)

        with m.If(state == WalkerState.RECOVER_LEFT):
            with m.If(inside_probe):
                with m.If(~recover_found_inside):
                    m.d.sync += bookmark.eq(probe)
                    m.d.sync += recover_found_inside.eq(1)
                with m.If(x_pix_probe <= visible_start_pix):
                    self._capture_visible_left_edge_from_probe(m)
                    m.d.sync += probe.eq(bookmark)
                    m.d.sync += recover_found_inside.eq(0)
                    m.d.sync += state.eq(WalkerState.SEARCH_RIGHT_TO_EXIT)
                with m.Else():
                    self._step_horizontal(m, "sync", probe, probe, move_right=False)
            with m.Else():
                with m.If(recover_found_inside):
                    self._step_horizontal(m, "sync", left_edge, probe, move_right=True)
                    m.d.sync += probe.eq(bookmark)
                    m.d.sync += recover_found_inside.eq(0)
                    m.d.sync += state.eq(WalkerState.SEARCH_RIGHT_TO_EXIT)
                with m.Elif(x_pix_probe <= visible_start_pix):
                    m.d.sync += probe.eq(bookmark)
                    m.d.sync += state.eq(WalkerState.SEARCH_RIGHT_TO_ENTER)
                with m.Else():
                    self._step_horizontal(m, "sync", probe, probe, move_right=False)

        with m.If(state == WalkerState.SEARCH_RIGHT_TO_ENTER):
            with m.If(inside_probe & (x_pix_probe >= visible_start_pix)):
                m.d.sync += left_edge.eq(probe)
                m.d.sync += bookmark.eq(probe)
                m.d.sync += state.eq(WalkerState.SEARCH_RIGHT_TO_EXIT)
            with m.Elif(x_pix_probe >= visible_end_pix):
                with m.If(first_span_pending):
                    self._prepare_next_row(m, row_guess, left_biased=False)
                with m.Else():
                    self._prepare_next_row(m, bookmark, left_biased=True)
            with m.Else():
                self._step_horizontal(m, "sync", probe, probe, move_right=True)

        with m.If(state == WalkerState.SEARCH_LEFT_TO_EXIT):
            with m.If(inside_probe):
                with m.If(x_pix_probe <= visible_start_pix):
                    self._capture_visible_left_edge_from_probe(m)
                    m.d.sync += probe.eq(bookmark)
                    m.d.sync += state.eq(WalkerState.SEARCH_RIGHT_TO_EXIT)
                with m.Else():
                    self._step_horizontal(m, "sync", probe, probe, move_right=False)
            with m.Else():
                self._step_horizontal(m, "sync", left_edge, probe, move_right=True)
                m.d.sync += probe.eq(bookmark)
                m.d.sync += state.eq(WalkerState.SEARCH_RIGHT_TO_EXIT)

        with m.If(state == WalkerState.SEARCH_RIGHT_TO_EXIT):
            with m.If(inside_probe):
                with m.If(x_pix_probe >= visible_end_pix):
                    m.d.sync += emit_right.eq(
                        self._pixel_to_vertex(_sresize(visible_end_pix - 1, npix))
                    )
                    m.d.sync += emit_first_span.eq(first_span_pending)
                    m.d.sync += state.eq(WalkerState.EMIT_SPAN)
                with m.Else():
                    self._step_horizontal(m, "sync", probe, probe, move_right=True)
            with m.Else():
                m.d.sync += emit_right.eq(
                    (probe.coords[0] + self._neg_one).fix_to(c.vertex_format)
                )
                m.d.sync += emit_first_span.eq(first_span_pending)
                m.d.sync += state.eq(WalkerState.EMIT_SPAN)

        with m.If((state == WalkerState.EMIT_SPAN) & emit_visible & self.o.ready):
            m.d.sync += first_span_pending.eq(0)
            self._prepare_next_row(m, left_edge, left_biased=True)

        # Span discovery still reaches EmitSpan for some fully off-screen or
        # reversed rows.  Drop them here so negative or out-of-range
        # coordinates do not wrap in later framebuffer address generation.
        with m.If((state == WalkerState.EMIT_SPAN) & ~emit_visible):
            m.d.sync += first_span_pending.eq(0)
            self._prepare_next_row(m, left_edge, left_biased=True)

        with m.If(state == WalkerState.ADVANCE_ROW):
            with m.If(next_y >= triangle_const.yrange[1]):
                m.d.sync += [
                    state.eq(WalkerState.IDLE),
                    first_span_pending.eq(0),
                    recover_found_inside.eq(0),
                ]
            with m.Else():
                m.d.sync += [
                    row_guess.eq(next_cursor),
                    probe.eq(next_cursor),
                    bookmark.eq(next_cursor),
                    recover_found_inside.eq(0),
                    state.eq(WalkerState.DECIDE),
                ]
                with m.If(next_row_left_biased):
                    m.d.sync += state.eq(WalkerState.RECOVER_LEFT)

        return m
