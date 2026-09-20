"""``StreamWhile``: one trigger in, a run of outputs out.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/bus/StreamWhile.scala`` (SpinalHDL
1.15.0). The Scala version is an ``object`` that builds a ``Composite`` inside
the caller and returns ``StreamWhileResult(output, running)``; here it is a
:class:`~amaranth.lib.wiring.Component` with the same three signals so it can
be elaborated on its own for the lockstep diff of plan §4.2.

The Scala body, for reference -- the Amaranth ``elaborate`` below is written
statement for statement against it::

    val counter = Counter(maxIterations)
    val running = RegInit(False)
    val state   = Reg(stateType())
    val output  = Stream(outputType)

    when(trigger.fire) {
      running := True
      state   := init(trigger.payload)
      counter.clear()
    }

    val (nextState, isLast) = step(counter.value, state, output.payload)

    output.valid := running

    when(output.fire && isLast) { running := False }

    when(output.fire && !isLast) {
      state := nextState
      counter.increment()
      when(counter.willOverflowIfInc) { running := False }
    }

    trigger.ready := !running

``trigger.ready`` is ``!running`` and ``output.valid`` is ``running``, so a
trigger beat and an output beat can never fire in the same cycle and the three
``when`` blocks are mutually exclusive; the order is kept anyway so the
generated logic matches.

The two formal invariants the Scala guards behind ``GenerationFlags.formal``
(``output.valid === running`` and ``trigger.ready === !running``) are dropped
from the RTL per plan §2/§3 and come back as SymbiYosys properties in Phase 8;
``tests/test_stream_while.py`` asserts both in simulation in the meantime.
"""

from __future__ import annotations

from amaranth import Module, Signal, unsigned
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.stream import Counter, _assign, connect_stream, fire

__all__ = ["StreamWhile", "StreamWhileDiffWrap"]


class StreamWhile(wiring.Component):
    """Run ``step`` from a single trigger until it reports ``is_last``.

    Parameters
    ----------
    trigger_shape : ShapeLike
        ``TIn`` -- payload of the trigger stream.
    state_shape : ShapeLike
        ``TState`` -- the loop state carried between iterations. Registered
        with no reset value, exactly like Spinal's ``Reg(stateType())``.
    output_shape : ShapeLike
        ``TOut`` -- payload of the output stream.
    max_iterations : int
        ``maxIterations``. Bounds the internal ``Counter``; the loop also stops
        when the counter is about to wrap, so at most ``max_iterations``
        outputs are produced per trigger.
    init : callable
        ``init: TIn => TState``. Called as ``init(m, trigger_payload)`` and must
        return the value to load into ``state``.
    step : callable
        ``step: (UInt, TState, TOut) => (TState, Bool)``. Called as
        ``step(m, counter_value, state, output_payload)``; it drives
        ``output_payload`` combinationally (``m.d.comb``) and returns
        ``(next_state, is_last)``.

    Attributes
    ----------
    trigger : In(stream.Signature(trigger_shape))
    output : Out(stream.Signature(output_shape))
    running : Out(1)
        Spinal exposes this through ``StreamWhileResult.running`` (and marks it
        ``simPublic``); the core's users read it as a busy flag.
    """

    def __init__(self, trigger_shape, state_shape, output_shape, max_iterations,
                 init, step):
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self._state_shape = state_shape
        self.max_iterations = max_iterations
        self._init = init
        self._step = step
        super().__init__({
            "trigger": In(stream.Signature(trigger_shape)),
            "output": Out(stream.Signature(output_shape)),
            "running": Out(1),
        })

    def elaborate(self, platform):
        m = Module()

        counter = Counter(m, self.max_iterations, name="counter")
        running = Signal(name="running")
        # `Reg(stateType())`, not `RegInit`: Spinal leaves this one unreset, and
        # `output.payload` is combinational from it whether or not `running` is
        # set, so a zero reset here makes the two implementations disagree for
        # the cycles after a reset. Found by the plan-0.4 lockstep diff (a
        # second seed mismatched at cycle 0 on `output_payload`).
        state = Signal(self._state_shape, reset_less=True, name="state")
        m.d.comb += self.running.eq(running)

        trigger_fire = fire(self.trigger)
        with m.If(trigger_fire):
            m.d.sync += running.eq(1)
            m.d.sync += _assign(state, self._init(m, self.trigger.payload))
            counter.clear(m)

        next_state, is_last = self._step(m, counter.value, state,
                                         self.output.payload)

        m.d.comb += self.output.valid.eq(running)

        output_fire = fire(self.output)
        with m.If(output_fire & is_last):
            m.d.sync += running.eq(0)

        with m.If(output_fire & ~is_last):
            m.d.sync += _assign(state, next_state)
            counter.increment(m)
            with m.If(counter.will_overflow_if_inc):
                m.d.sync += running.eq(0)

        m.d.comb += self.trigger.ready.eq(~running)
        return m


class StreamWhileDiffWrap(wiring.Component):
    """Amaranth counterpart of ``voodoo.diff.StreamWhileDiffWrap``.

    ``StreamWhile`` takes Python callables, so it cannot be built from the
    command line; this is the elaboratable stand-in the Phase 0.4 lockstep diff
    generates on the Amaranth side, carrying the same countdown loop and the
    same port names as
    ``fpga/src/voodoo/diff/DiffWrappers.scala:StreamWhileDiffWrap``
    (``start`` / ``output`` / ``running``; Spinal flattens them to
    ``start_valid``, Amaranth to ``start__valid`` -- see
    ``fpga/src/voodoo/diff/portmaps/StreamWhile.json``)::

        python -m gen.gen_module avoodoo.bus.stream_while:StreamWhileDiffWrap
    """

    def __init__(self, width: int = 8, max_iterations: int = 16):
        self.width = width
        self.max_iterations = max_iterations
        super().__init__({
            "start": In(stream.Signature(unsigned(width))),
            "output": Out(stream.Signature(unsigned(width))),
            "running": Out(1),
        })

    def elaborate(self, platform):
        width = self.width

        def init(m, value):
            return value

        def step(m, counter_value, state, out_payload):
            m.d.comb += out_payload.eq(state)
            return (state - 1)[:width], state == 1

        m = Module()
        m.submodules.loop = loop = StreamWhile(
            unsigned(width), unsigned(width), unsigned(width),
            self.max_iterations, init, step)
        connect_stream(m, loop.trigger, self.start)
        connect_stream(m, self.output, loop.output)
        m.d.comb += self.running.eq(loop.running)
        return m
