"""Phase 1.1 unit tests for :mod:`avoodoo.bus.stream_while`.

Ports the intent of ``ext/SpinalVoodoo/test/StreamWhileTest.scala`` -- the
countdown loop, the ``maxIterations`` cap and the back-pressure case -- and
adds a randomised comparison against a Python reference model plus the two
invariants the Scala only checks under ``GenerationFlags.formal``
(``output.valid === running`` and ``trigger.ready === !running``), which the
RTL port deliberately drops (plan §2: formal comes back in Phase 8).
"""

import random

import pytest
from amaranth import unsigned
from amaranth.sim import Simulator

from avoodoo.bus.stream_while import StreamWhile

PERIOD = 1e-6


def countdown(max_iterations, width=8):
    """The Scala test's loop: emit ``state`` and stop once it reaches 1."""

    def init(m, trigger_payload):
        return trigger_payload

    def step(m, counter_value, state, out_payload):
        m.d.comb += out_payload.eq(state)
        next_state = (state - 1)[:width]
        is_last = state == 1
        return next_state, is_last

    return StreamWhile(unsigned(width), unsigned(width), unsigned(width),
                       max_iterations, init, step)


def run(dut, tb):
    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()


async def cycle(ctx, inputs, outputs):
    for sig, value in inputs:
        ctx.set(sig, value)
    return (await ctx.tick().sample(*outputs))[2:]


def model_countdown(start, max_iterations):
    """What ``countdown`` should emit for one trigger of ``start``."""
    out, state = [], start
    for _ in range(max_iterations):
        out.append(state)
        if state == 1:
            break
        state = (state - 1) & 0xFF
    return out


# ---------------------------------------------------------------------------
# the three StreamWhileTest.scala cases
# ---------------------------------------------------------------------------


def test_decrements_value_to_zero():
    dut = countdown(16)
    seen = []

    async def tb(ctx):
        outs = (dut.output.valid, dut.output.payload, dut.trigger.ready)
        await cycle(ctx, [(dut.trigger.valid, 0), (dut.output.ready, 0)], outs)
        await cycle(ctx, [(dut.trigger.valid, 1), (dut.trigger.payload, 5),
                          (dut.output.ready, 1)], outs)
        for _ in range(20):
            v, p, r = await cycle(ctx, [(dut.trigger.valid, 0)], outs)
            if v:
                seen.append(p)
        _, _, ready = await cycle(ctx, [], outs)
        assert ready == 1, "the trigger is accepted again once the loop ends"

    run(dut, tb)
    assert seen == [5, 4, 3, 2, 1]


def test_respects_max_iterations():
    dut = countdown(3)
    seen = []

    async def tb(ctx):
        outs = (dut.output.valid, dut.output.payload)
        await cycle(ctx, [(dut.trigger.valid, 0), (dut.output.ready, 1)], outs)
        await cycle(ctx, [(dut.trigger.valid, 1), (dut.trigger.payload, 10)], outs)
        for _ in range(20):
            v, p = await cycle(ctx, [(dut.trigger.valid, 0)], outs)
            if v:
                seen.append(p)

    run(dut, tb)
    assert seen == [10, 9, 8]


def test_handles_backpressure():
    dut = countdown(16)
    seen = []

    async def tb(ctx):
        outs = (dut.output.valid, dut.output.payload)
        await cycle(ctx, [(dut.trigger.valid, 0), (dut.output.ready, 0)], outs)
        await cycle(ctx, [(dut.trigger.valid, 1), (dut.trigger.payload, 3)], outs)
        for n in range(20):
            ready = 1 if n % 2 == 0 else 0
            v, p = await cycle(ctx, [(dut.trigger.valid, 0),
                                     (dut.output.ready, ready)], outs)
            if v and ready:
                seen.append(p)

    run(dut, tb)
    assert seen == [3, 2, 1]


# ---------------------------------------------------------------------------
# extra coverage
# ---------------------------------------------------------------------------


def test_trigger_is_blocked_while_running():
    dut = countdown(16)

    async def tb(ctx):
        outs = (dut.trigger.ready, dut.running, dut.output.valid)
        r, run_, v = await cycle(ctx, [(dut.trigger.valid, 1),
                                       (dut.trigger.payload, 4),
                                       (dut.output.ready, 0)], outs)
        assert (r, run_, v) == (1, 0, 0)
        # trigger stays asserted, but the loop now owns the machine
        for _ in range(4):
            r, run_, v = await cycle(ctx, [], outs)
            assert (r, run_, v) == (0, 1, 1)

    run(dut, tb)


@pytest.mark.parametrize("max_iterations", [1, 2, 3, 7, 16])
def test_back_to_back_triggers_against_the_model(max_iterations):
    dut = countdown(max_iterations)
    rng = random.Random(0xB00B5 + max_iterations)
    starts, runs = [], []

    async def tb(ctx):
        outs = (dut.trigger.ready, dut.output.valid, dut.output.payload,
                dut.running)
        current = []
        pending = None
        for _ in range(900):
            if pending is None:
                pending = rng.randint(1, 12)
            ready_out = 1 if rng.random() < 0.6 else 0
            r, v, p, run_ = await cycle(ctx, [
                (dut.trigger.valid, 1), (dut.trigger.payload, pending),
                (dut.output.ready, ready_out)], outs)

            # the two formal invariants of StreamWhile.scala
            assert v == run_, "output.valid must equal running"
            assert r == (not run_), "trigger.ready must be !running"

            if v and ready_out:
                current.append(p)
            if r:
                # the trigger fired this cycle: the previous loop is over
                if current:
                    runs.append(current)
                    current = []
                starts.append(pending)
                pending = None
        # `current` is dropped: the run in flight when the stimulus stops is
        # incomplete and would not match the model.

    run(dut, tb)
    assert len(runs) > 5
    for start, produced in zip(starts, runs):
        assert produced == model_countdown(start, max_iterations)


def test_counter_state_is_carried_between_iterations():
    """A ``step`` that ignores ``state`` and uses the loop counter instead --
    the other half of the Scala signature ``(UInt, TState, TOut)``."""

    def init(m, payload):
        return payload

    def step(m, counter_value, state, out_payload):
        m.d.comb += out_payload.eq(counter_value)
        return state, counter_value == 3

    dut = StreamWhile(unsigned(8), unsigned(8), unsigned(4), 8, init, step)
    seen = []

    async def tb(ctx):
        outs = (dut.output.valid, dut.output.payload)
        await cycle(ctx, [(dut.trigger.valid, 1), (dut.trigger.payload, 0),
                          (dut.output.ready, 1)], outs)
        for _ in range(10):
            v, p = await cycle(ctx, [(dut.trigger.valid, 0)], outs)
            if v:
                seen.append(p)

    run(dut, tb)
    assert seen == [0, 1, 2, 3]


def test_diff_wrapper_matches_the_scala_wrapper_behaviour():
    """``StreamWhileDiffWrap`` is what Phase 0.4 will diff against
    ``fpga/src/voodoo/diff/DiffWrappers.scala``; keep it working."""
    from avoodoo.bus.stream_while import StreamWhileDiffWrap

    dut = StreamWhileDiffWrap()
    seen = []

    async def tb(ctx):
        outs = (dut.output.valid, dut.output.payload, dut.running,
                dut.start.ready)
        await cycle(ctx, [(dut.start.valid, 1), (dut.start.payload, 5),
                          (dut.output.ready, 1)], outs)
        for _ in range(12):
            v, p, r, sr = await cycle(ctx, [(dut.start.valid, 0)], outs)
            assert v == r, "running mirrors output.valid"
            if v:
                seen.append(p)

    run(dut, tb)
    assert seen == [5, 4, 3, 2, 1]
