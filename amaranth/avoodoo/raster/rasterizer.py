"""Triangle rasterizer: spans in, pixels out.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/raster/Rasterizer.scala`` (234
lines, SpinalHDL 1.15.0).  Structure of the Scala file, and of this one:

===========================  ===================================================
Scala                        Python
===========================  ===================================================
``case class Rasterizer``    :class:`Rasterizer`        -- the top: a 2-deep
                             input queue, :class:`~avoodoo.raster.span_walker.SpanWalker`,
                             a 4-deep span queue, a *synchronous* ``StreamFork2``
                             and :class:`SpanRasterizer`
``Rasterizer.InputGradient`` ``core_types.input_gradient_layout``
``Rasterizer.GradientBundle`` ``core_types.gradient_bundle_layout``
``Rasterizer.Output``        :func:`output_layout`
``Rasterizer.PrefetchSpan``  :func:`prefetch_span_layout`
  ``.fromSpanWalker``        :func:`prefetch_span_from_span_walker`
``Rasterizer.PostTmu``       :func:`post_tmu_layout`
  ``.fromOutput``            :func:`post_tmu_from_output`
``Rasterizer.SpanState``     :func:`span_state_layout`
``Rasterizer.SpanRasterizer`` :class:`SpanRasterizer`
===========================  ===================================================

``InputGradient`` and ``GradientBundle`` live in
:mod:`avoodoo.core.core_types` rather than here, because
``TriangleSetup.Output`` -- this module's *input* payload -- embeds them and
Python, unlike Scala, cannot have the two files import each other.  They are
re-exported below so a reader following the Scala finds them where the Scala
puts them.

The whole datapath is :mod:`avoodoo.lib.fixed` (the ``AFix`` clone); every
``fixTo`` here is the default-rounding form, which in this design is
**floor then saturate** (``docs/amaranth/afix-semantics.md`` §6.4).
"""

from __future__ import annotations

from amaranth import Module, Signal, signed
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.bus.stream_while import StreamWhile
from avoodoo.config import Config
from avoodoo.core.core_types import (
    TRACE_PIXEL_KEY_LAYOUT,
    TRACE_PRIMITIVE_KEY_LAYOUT,
    gradient_bundle_layout,
    input_gradient_layout,
    pixel_pipeline_config_layout,
    tmu_config_layout,
    triangle_setup_output_layout,
)
from avoodoo.lib.fixed import AFixShape
from avoodoo.lib.stream import (
    arbitration_from,
    connect_stream,
    fire,
    fork2,
    m2s_pipe,
    queue,
)

__all__ = [
    "input_gradient_layout",
    "gradient_bundle_layout",
    "output_layout",
    "prefetch_span_layout",
    "prefetch_span_from_span_walker",
    "post_tmu_layout",
    "post_tmu_from_output",
    "span_state_layout",
    "SpanRasterizer",
    "Rasterizer",
    "low_grad_formats",
    "LINEAR_STATE_ALL",
]


def _span_walker_layouts():
    """``SpanWalker.LinearState`` / ``StepX`` / ``Output``.

    Owned by :mod:`avoodoo.raster.span_walker`, for the same reason
    ``GradientBundle`` is not owned here: ``SpanWalker.TriangleConst`` embeds
    ``TriangleSetup.TmuConfig``, so ``span_walker`` imports ``core_types`` and
    not the other way round.  Resolved through a function, and inside
    :meth:`Rasterizer.elaborate` for the component itself, because the Scala
    has the two files refer to each other freely and Python does not.
    """
    from avoodoo.raster import span_walker

    return (
        span_walker.linear_state_layout,
        span_walker.step_x_layout,
        span_walker.span_walker_output_layout,
    )


#: ``SpanWalker.LinearState.all`` -- the six low-precision interpolants, in the
#: order the Scala's ``all`` returns them.  ``SpanRasterizer.step`` zips this
#: against :func:`low_grad_formats` positionally, so the order is contractual.
LINEAR_STATE_ALL = ("red", "green", "blue", "depth", "alpha", "w")


def low_grad_formats(c: Config):
    """``lowGradFormats`` (``Rasterizer.scala:195-202``), matching ``LINEAR_STATE_ALL``."""
    return (
        c.v_color_format,
        c.v_color_format,
        c.v_color_format,
        c.v_depth_format,
        c.v_color_format,
        c.w_accum_format,
    )


# ---------------------------------------------------------------------------
# object Rasterizer -- the payload bundles
# ---------------------------------------------------------------------------


def output_layout(c: Config) -> data.StructLayout:
    """``Rasterizer.Output(c)`` (``Rasterizer.scala:66-73``).

    ``coords`` is ``Vec.fill(2)(SInt(vertexFormat.nonFraction bits))`` -- plain
    integers, **not** AFix: the fixed-point position has already been shifted
    down to whole pixels by ``SpanRasterizer.toPixelCoord``.
    """
    fields = {
        "grads": gradient_bundle_layout(c),
        "alpha_grad_hi": AFixShape.from_q(c.tex_coords_hi_format),
        "coords": data.ArrayLayout(signed(c.vertex_format.non_fraction), 2),
        "tmu_config": tmu_config_layout(c),
        "pixel_config": pixel_pipeline_config_layout(c),
    }
    if c.trace.enabled:
        fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
    return data.StructLayout(fields)


def prefetch_span_layout(c: Config) -> data.StructLayout:
    """``Rasterizer.PrefetchSpan(c)`` (``Rasterizer.scala:75-80``)."""
    vtx = AFixShape.from_q(c.vertex_format)
    return data.StructLayout({
        "x_start": vtx,
        "x_end": vtx,
        "y": vtx,
        "pixel_config": pixel_pipeline_config_layout(c),
    })


def prefetch_span_from_span_walker(m, out, src):
    """``Rasterizer.PrefetchSpan.fromSpanWalker(c, in)`` (``Rasterizer.scala:83-90``).

    Drives ``out`` (a :class:`~amaranth.lib.data.View` of
    :func:`prefetch_span_layout`) combinationally from a ``SpanWalker.Output``
    view.  Unlike the Scala it takes the destination rather than returning a
    fresh Bundle, because Amaranth has no ``cloneOf``.
    """
    m.d.comb += [
        out.x_start.eq(src.x_start),
        out.x_end.eq(src.x_end),
        out.y.eq(src.y),
        out.pixel_config.eq(src.pixel_config),
    ]


def post_tmu_layout(c: Config) -> data.StructLayout:
    """``Rasterizer.PostTmu(c)`` (``Rasterizer.scala:93-99``).

    Not produced by this module -- ``Tmu`` and ``PixelPipeline`` build it from
    an :func:`output_layout` payload -- but it is declared in Rasterizer.scala,
    so the port keeps it here.  Note ``config``, not ``pixelConfig``, and no
    ``tmuConfig``: the TMU has consumed it by this point.
    """
    fields = {
        "grads": gradient_bundle_layout(c),
        "alpha_grad_hi": AFixShape.from_q(c.tex_coords_hi_format),
        "coords": data.ArrayLayout(signed(c.vertex_format.non_fraction), 2),
        "config": pixel_pipeline_config_layout(c),
    }
    if c.trace.enabled:
        fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
    return data.StructLayout(fields)


def post_tmu_from_output(m, c: Config, out, src):
    """``Rasterizer.PostTmu.fromOutput(c, in)`` (``Rasterizer.scala:102-112``)."""
    m.d.comb += [
        out.grads.eq(src.grads),
        out.alpha_grad_hi.eq(src.alpha_grad_hi),
        out.coords.eq(src.coords),
        out.config.eq(src.pixel_config),
    ]
    if c.trace.enabled:
        m.d.comb += out.trace.eq(src.trace)


def span_state_layout(c: Config) -> data.StructLayout:
    """``Rasterizer.SpanState(c)`` (``Rasterizer.scala:115-127``) -- the
    ``StreamWhile`` loop state: one pixel's cursor plus the per-span constants."""
    linear_state_layout, step_x_layout, _ = _span_walker_layouts()
    vtx = AFixShape.from_q(c.vertex_format)
    hi = AFixShape.from_q(c.tex_coords_hi_format)
    fields = {
        "x": vtx,
        "y": vtx,
        "linear": linear_state_layout(c),
        "hi_s": hi,
        "hi_t": hi,
        "hi_alpha": hi,
        "x_end": vtx,
        "step_x": step_x_layout(c),
        "tmu_config": tmu_config_layout(c),
        "pixel_config": pixel_pipeline_config_layout(c),
    }
    if c.trace.enabled:
        fields["trace"] = TRACE_PRIMITIVE_KEY_LAYOUT
    return data.StructLayout(fields)


# ---------------------------------------------------------------------------
# case class SpanRasterizer
# ---------------------------------------------------------------------------


class SpanRasterizer(wiring.Component):
    """``Rasterizer.SpanRasterizer(c)`` (``Rasterizer.scala:129-233``).

    One ``SpanWalker.Output`` in, one pixel per cycle out until the cursor
    passes ``xEnd``, via :class:`~avoodoo.bus.stream_while.StreamWhile`.  All
    of the per-pixel arithmetic in the design's raster front end is the ``step``
    closure below.
    """

    def __init__(self, c: Config | None = None):
        # `c` has no default in the Scala; the `None` case only exists so the
        # lockstep-diff registry can build the module the way it builds every
        # other core-proper one ("the factory reads Config.voodoo1() for
        # itself" -- tools/diff/registry.py).
        self.c = c = Config.voodoo1() if c is None else c
        _, _, span_walker_output_layout = _span_walker_layouts()
        super().__init__({
            "i": In(stream.Signature(span_walker_output_layout(c))),
            "o": Out(stream.Signature(output_layout(c))),
            "running": Out(1),
        })

    # -- def toPixelCoord (Rasterizer.scala:137-141) ------------------------

    def _to_pixel_coord(self, value):
        """``(value.asBits.asSInt >> c.vertexFormat.fraction).resize(nonFraction bits)``.

        Span state carries 12.4 fixed-point positions; downstream coords are
        integer pixels.  Spinal emits this as a shift of the *unsigned* storage
        word followed by a truncation to 12 bits
        (``Rasterizer_spinal.v:9267``), so slicing the raw word is exactly
        equivalent and is what is written here; the result is read as signed.
        """
        frac = self.c.vertex_format.fraction
        width = self.c.vertex_format.non_fraction
        raw = value.as_value()
        return raw[frac:frac + width].as_signed()

    def elaborate(self, platform):
        c = self.c
        m = Module()

        # `Reg(UInt(24 bits)) init (0)` -- a *reset* register, unlike the
        # StreamWhile state, so no reset_less here.
        pixel_seq_counter = Signal(24) if c.trace.enabled else None
        current_pixel_seq = pixel_seq_counter

        vtx = AFixShape.from_q(c.vertex_format)
        hi = c.tex_coords_hi_format
        state_shape = span_state_layout(c)
        grads_all = LINEAR_STATE_ALL
        formats = low_grad_formats(c)

        # -- init = (input: SpanWalker.Output) => ... (Rasterizer.scala:148-164)
        def init(sub, payload):
            state = Signal(state_shape, name="initState")
            sub.d.comb += [
                state.x.eq(payload.x_start),
                state.y.eq(payload.y),
                state.linear.eq(payload.linear),
                state.hi_s.eq(payload.hi_s),
                state.hi_t.eq(payload.hi_t),
                state.hi_alpha.eq(payload.hi_alpha),
                state.x_end.eq(payload.x_end),
                state.step_x.eq(payload.step_x),
                state.tmu_config.eq(payload.tmu_config),
                state.pixel_config.eq(payload.pixel_config),
            ]
            if c.trace.enabled:
                sub.d.comb += state.trace.eq(payload.trace)
            return state

        # -- step = (idx, state, output) => (next, isLast) (Rasterizer.scala:165-218)
        def step(sub, idx, state, output):
            state = data.View(state_shape, state)
            out = data.View(output_layout(c), output)

            sub.d.comb += [
                out.coords[0].eq(self._to_pixel_coord(state.x)),
                out.coords[1].eq(self._to_pixel_coord(state.y)),
                out.grads.red_grad.eq(state.linear.red),
                out.grads.green_grad.eq(state.linear.green),
                out.grads.blue_grad.eq(state.linear.blue),
                out.grads.depth_grad.eq(state.linear.depth),
                out.grads.alpha_grad.eq(state.linear.alpha),
                out.grads.w_grad.eq(state.linear.w),
                out.grads.s_grad.eq(state.hi_s.fix_to(c.tex_coords_accum_format)),
                out.grads.t_grad.eq(state.hi_t.fix_to(c.tex_coords_accum_format)),
                out.alpha_grad_hi.eq(state.hi_alpha),
                out.tmu_config.eq(state.tmu_config),
                out.pixel_config.eq(state.pixel_config),
            ]
            if c.trace.enabled:
                sub.d.comb += [
                    out.trace.primitive.eq(state.trace),
                    out.trace.pixel_seq.eq(current_pixel_seq),
                ]

            nxt = Signal(state_shape, name="nextState")
            sub.d.comb += [
                nxt.y.eq(state.y),
                nxt.x_end.eq(state.x_end),
                nxt.step_x.eq(state.step_x),
                nxt.tmu_config.eq(state.tmu_config),
                nxt.pixel_config.eq(state.pixel_config),
            ]
            if c.trace.enabled:
                sub.d.comb += nxt.trace.eq(state.trace)

            one = vtx.const(1)
            sub.d.comb += nxt.x.eq((state.x + one).fix_to(c.vertex_format))
            for name, fmt in zip(grads_all, formats):
                cur = getattr(state.linear, name)
                grad = getattr(state.step_x.linear, name)
                sub.d.comb += getattr(nxt.linear, name).eq((cur + grad).fix_to(fmt))
            sub.d.comb += [
                nxt.hi_s.eq((state.hi_s + state.step_x.tex[0]).fix_to(hi)),
                nxt.hi_t.eq((state.hi_t + state.step_x.tex[1]).fix_to(hi)),
                nxt.hi_alpha.eq((state.hi_alpha + state.step_x.alpha).fix_to(hi)),
            ]

            is_last = self._to_pixel_coord(state.x) >= self._to_pixel_coord(state.x_end)
            return nxt, is_last

        m.submodules.streamWhile = loop = StreamWhile(
            trigger_shape=self.i.payload.shape(),
            state_shape=state_shape,
            output_shape=output_layout(c),
            max_iterations=c.max_fb_dims[0],
            init=init,
            step=step,
        )
        connect_stream(m, loop.trigger, self.i)
        m.d.comb += self.running.eq(loop.running)
        connect_stream(m, self.o, loop.output)

        if c.trace.enabled:
            with m.If(fire(self.i) & self.i.payload.first_span):
                m.d.sync += pixel_seq_counter.eq(0)
            with m.Elif(fire(loop.output)):
                m.d.sync += pixel_seq_counter.eq(current_pixel_seq + 1)

        return m


# ---------------------------------------------------------------------------
# case class Rasterizer
# ---------------------------------------------------------------------------


class Rasterizer(wiring.Component):
    """``Rasterizer(c, formalStrong)`` (``Rasterizer.scala:9-42``).

    ``i`` -> ``queue(2)`` -> ``m2sPipe`` -> ``SpanWalker`` -> ``queue(4)`` ->
    ``StreamFork2(synchronous = true)`` -> { ``m2sPipe`` -> ``SpanRasterizer``
    -> ``o``, ``prefetchSpan`` }.

    The fork is **synchronous**, i.e. ``outputs.valid := i.valid && i.ready``
    (valid depends on ready -- illegal in AXI, deliberate here): it keeps
    prefetch acceptance coupled to span launch so the cached framebuffer reader
    always sees a span descriptor before the per-pixel read stream catches up.
    """

    def __init__(self, c: Config | None = None, formal_strong: bool = False):
        # See SpanRasterizer.__init__ on the `None` default.
        self.c = c = Config.voodoo1() if c is None else c
        self.formal_strong = formal_strong
        super().__init__({
            "i": In(stream.Signature(triangle_setup_output_layout(c))),
            "o": Out(stream.Signature(output_layout(c))),
            "prefetch_span": Out(stream.Signature(prefetch_span_layout(c))),
            "enable_clipping": In(1),
            "clip_left": In(10),
            "clip_right": In(10),
            "clip_low_y": In(10),
            "clip_high_y": In(10),
            "running": Out(1),
        })

    def elaborate(self, platform):
        from avoodoo.raster.span_walker import SpanWalker

        c = self.c
        m = Module()

        queued_input = queue(m, self.i, 2, name="queuedInput")
        m.submodules.spanWalker = span_walker = SpanWalker(
            c, formal_strong=self.formal_strong)
        m.submodules.spanRasterizer = span_rasterizer = SpanRasterizer(c)
        queued_span = queue(m, span_walker.o, 4, name="queuedSpan")
        span_launch_fork = fork2(m, queued_span, synchronous=True,
                                 name="spanLaunchFork")

        connect_stream(m, span_walker.i, m2s_pipe(m, queued_input))
        m.d.comb += [
            span_walker.enable_clipping.eq(self.enable_clipping),
            span_walker.clip_left.eq(self.clip_left),
            span_walker.clip_right.eq(self.clip_right),
            span_walker.clip_low_y.eq(self.clip_low_y),
            span_walker.clip_high_y.eq(self.clip_high_y),
        ]
        connect_stream(m, span_rasterizer.i, m2s_pipe(m, span_launch_fork[0]))

        # prefetchSpan.translateFrom(spanLaunchFork._2) { ... }
        arbitration_from(m, self.prefetch_span, span_launch_fork[1])
        prefetch_span_from_span_walker(
            m, self.prefetch_span.payload, span_launch_fork[1].payload)

        connect_stream(m, self.o, span_rasterizer.o)

        m.d.comb += self.running.eq(
            queued_input.valid | span_walker.running
            | queued_span.valid | span_rasterizer.running
        )
        return m
