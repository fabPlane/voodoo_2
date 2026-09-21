"""``Fastfill``: rectangle pixel generator for the ``fastfillCMD`` screen clear.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/raster/Fastfill.scala`` (96 lines,
SpinalHDL 1.15.0), plan Phase 3.  Same ports, same ``StreamWhile`` loop,
same state bundle, same ``takeWhen(...).map(...)`` output filter.

What it does, in the Scala's own terms: one ``NoData`` trigger on ``i``
latches the clip rectangle into the loop state; the loop then emits one
pixel coordinate per cycle, left to right, top to bottom, over
``[clipLeft, clipRight) x [clipLowY, clipHighY)``.  An empty rectangle
(``clipRight <= clipLeft || clipHighY <= clipLowY``) still runs the loop
for exactly one beat, with ``pixelValid`` low, so the ``takeWhen`` drops it
and ``o`` never sees it -- ``running`` still pulses for that one cycle,
which is what the pipeline's busy logic relies on.

Fidelity notes:

* **Widths.**  ``state.x + 1`` and ``state.y + 1`` are Spinal ``UInt +``,
  which does *not* grow: both wrap at 10 bits, and the ``>=`` comparisons
  against ``clipRight`` / ``clipHighY`` are on the wrapped value
  (:func:`avoodoo.lib.intops.u_add`).  ``(False ## state.x).asSInt.resize(12)``
  is an 11-bit non-negative value sign-extended to
  ``vertexFormat.nonFraction`` bits -- :func:`_coord_to_sint` below.
* **Reset.**  The loop's ``state`` is a plain ``Reg`` inside
  :class:`~avoodoo.bus.stream_while.StreamWhile` (already ``reset_less``);
  ``running`` and the iteration counter reset.  Nothing else is registered
  here.
* ``maxIterations = c.maxFbDims._1 * c.maxFbDims._2`` = 480000 for
  ``Config.voodoo1()``, a 19-bit non-power-of-two counter that Spinal
  emits with the wrap comparator (``19'h752ff``); ``lib.stream.Counter``
  does the same.

The companion ``object Fastfill`` bundles are here.  ``Output`` is also
``FastfillWrite.io.pixels``'s payload, so :mod:`avoodoo.raster.fastfill_write`
imports it from here exactly as ``FastfillWrite.scala`` does.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Signal, Value, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.bus.stream_while import StreamWhile
from avoodoo.config import Config
from avoodoo.lib.intops import sext, u_add
from avoodoo.lib.stream import connect_stream, map_payload, take_when

__all__ = [
    "FastfillOutput",
    "FastfillOutputWithFlag",
    "FastfillState",
    "Fastfill",
]


# ---------------------------------------------------------------------------
# object Fastfill -- the companion bundles
# ---------------------------------------------------------------------------


class FastfillOutput(data.StructLayout):
    """``Fastfill.Output(c)`` (``Fastfill.scala:78``).

    ``coords`` is ``Vec.fill(2)(SInt(c.vertexFormat.nonFraction bits))`` --
    a real ``Vec`` here (unlike ``PixelCoords``), so Spinal emits
    ``coords_0`` / ``coords_1`` and Amaranth ``coords__0`` / ``coords__1``.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "coords": data.ArrayLayout(signed(c.vertex_format.non_fraction), 2),
        })


class FastfillOutputWithFlag(data.StructLayout):
    """``Fastfill.OutputWithFlag(c)`` (``Fastfill.scala:82``) -- the loop's
    raw output before the empty-rectangle beat is filtered out."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "data": FastfillOutput(c),
            "pixel_valid": unsigned(1),
        })


class FastfillState(data.StructLayout):
    """``Fastfill.State()`` (``Fastfill.scala:87``) -- the ``StreamWhile`` state."""

    def __init__(self) -> None:
        super().__init__({
            "x": unsigned(10),
            "y": unsigned(10),
            "clip_left": unsigned(10),
            "clip_right": unsigned(10),
            "clip_low_y": unsigned(10),
            "clip_high_y": unsigned(10),
            "empty": unsigned(1),
        })


# ---------------------------------------------------------------------------
# small value helpers
# ---------------------------------------------------------------------------


def _coord_to_sint(value, width: int) -> Value:
    """``(False ## x).asSInt.resize(width bits)`` (``Fastfill.scala:41-42``).

    The ``False ##`` prepends a zero MSB so the ``asSInt`` reinterpretation
    cannot go negative (the MEMORY.md pitfall the Scala comment names); the
    ``resize`` then sign-extends -- from a zero sign bit -- or truncates.
    """
    v = Value.cast(value).as_unsigned()
    return sext(Cat(v, Const(0, 1)).as_signed(), width)


# ---------------------------------------------------------------------------
# the component
# ---------------------------------------------------------------------------


class Fastfill(wiring.Component):
    """``case class Fastfill(c: Config) extends Component``.

    Attributes
    ----------
    i : In(stream.Signature(unsigned(0)))
        ``slave Stream(NoData)`` -- a zero-width payload; Spinal emits no
        payload port for it and neither does the diff wrapper.
    o : Out(stream.Signature(FastfillOutput(c)))
        ``master Stream(Fastfill.Output(c))``.
    clip_left, clip_right, clip_low_y, clip_high_y : In(10)
        The live clip window, sampled on ``i.fire``.
    running : Out(1)
        ``streamWhileResult.running``.
    """

    def __init__(self, c: Config | None = None) -> None:
        # `c` has no default in the Scala; `None` exists so the lockstep-diff
        # registry can build the module like every other core-proper one.
        self.c = c = Config.voodoo1() if c is None else c
        super().__init__({
            "i": In(stream.Signature(unsigned(0))),
            "o": Out(stream.Signature(FastfillOutput(c))),
            "clip_left": In(10),
            "clip_right": In(10),
            "clip_low_y": In(10),
            "clip_high_y": In(10),
            "running": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        width = c.vertex_format.non_fraction

        # `init = (_: NoData) => { ... }`  (Fastfill.scala:27-37)
        def init(m: Module, _trigger_payload):
            state = Signal(FastfillState(), name="initState")
            m.d.comb += [
                state.x.eq(self.clip_left),
                state.y.eq(self.clip_low_y),
                state.clip_left.eq(self.clip_left),
                state.clip_right.eq(self.clip_right),
                state.clip_low_y.eq(self.clip_low_y),
                state.clip_high_y.eq(self.clip_high_y),
                state.empty.eq((self.clip_right <= self.clip_left)
                               | (self.clip_high_y <= self.clip_low_y)),
            ]
            return state

        # `step = (_: UInt, state, output) => { ... }`  (Fastfill.scala:38-66)
        def step(m: Module, _counter_value, state, output):
            # Output current pixel (scala:41-43)
            m.d.comb += [
                output.data.coords[0].eq(_coord_to_sint(state.x, width)),
                output.data.coords[1].eq(_coord_to_sint(state.y, width)),
                output.pixel_valid.eq(~state.empty),
            ]

            next_state = Signal(FastfillState(), name="nextState")
            m.d.comb += [
                next_state.clip_left.eq(state.clip_left),
                next_state.clip_right.eq(state.clip_right),
                next_state.clip_low_y.eq(state.clip_low_y),
                next_state.clip_high_y.eq(state.clip_high_y),
                next_state.empty.eq(state.empty),
            ]

            # Advance: x+1, wrap to next row when reaching clipRight (scala:53-60).
            # `state.x + 1` is a 10-bit Spinal UInt add: it wraps.
            x_plus_1 = u_add(state.x, Const(1, 10))
            y_plus_1 = u_add(state.y, Const(1, 10))
            at_row_end = Signal(name="atRowEnd")
            m.d.comb += at_row_end.eq(x_plus_1 >= state.clip_right)
            with m.If(at_row_end):
                m.d.comb += [
                    next_state.x.eq(state.clip_left),
                    next_state.y.eq(y_plus_1),
                ]
            with m.Else():
                m.d.comb += [
                    next_state.x.eq(x_plus_1),
                    next_state.y.eq(state.y),
                ]

            # Last pixel: empty rectangle, or row end with the next row past
            # clipHighY (scala:63).
            is_last = Signal(name="isLast")
            m.d.comb += is_last.eq(
                state.empty | (at_row_end & (y_plus_1 >= state.clip_high_y)))
            return next_state, is_last

        # Spinal names the Composite after the trigger stream: `i_streamWhile`.
        m.submodules.i_streamWhile = loop = StreamWhile(
            trigger_shape=unsigned(0),
            state_shape=FastfillState(),
            output_shape=FastfillOutputWithFlag(c),
            max_iterations=c.max_fb_dims[0] * c.max_fb_dims[1],
            init=init,
            step=step,
        )
        connect_stream(m, loop.trigger, self.i)

        # Filter out invalid pixels (empty rectangle case) and strip the flag
        # (scala:70-71): `o << withFlag.takeWhen(withFlag.payload.pixelValid).map(_.data)`
        with_flag = loop.output
        taken = take_when(m, with_flag, with_flag.payload.pixel_valid)
        connect_stream(m, self.o, map_payload(m, taken, lambda p: p.data))

        m.d.comb += self.running.eq(loop.running)
        return m
