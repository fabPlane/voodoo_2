"""Phase 2.3 unit tests for :mod:`avoodoo.frontend.swap_buffer`.

Two things are checked here.

1. **The formal invariants** of
   ``ext/SpinalVoodoo/test/SwapBufferFormalTest.scala``.  The Scala runs them
   through SymbiYosys (BMC 20 / prove 10) against
   ``SwapBuffer.GenerationFlags.formal``; the Amaranth port drops the
   properties from the RTL (plan §2/§3), so they are re-expressed here as
   per-cycle assertions under randomised stimulus that honours the same
   assumptions the formal DUT makes:

   ===================================  ==============================
   ``SwapBufferFormalDut``              here
   ===================================  ==============================
   ``assumeInitial(reset)``             the simulator resets before cycle 0
   ``assume(!cmd.valid)`` during reset  stimulus starts after reset
   ``cmd.formalAssumesSlave()``         :func:`drive_cmd` holds ``valid``
                                        and never drops it before ``fire``
   weak asserts (``formalStrong=false``)  :func:`check_weak`
   strong asserts (``formalStrong=true``) :func:`check_strong`
   the three ``cover`` statements       :func:`test_formal_covers_reached`
   ===================================  ==============================

   One wart worth recording: the Scala's first two covers are written
   ``cover(io.cmd.ready && state === State.Sampling)`` and
   ``... && state === State.Waiting``.  ``io.cmd.ready`` is asserted to equal
   ``state === Idle`` two lines above, so both are **unreachable**.  They have
   never failed because ``FormalConfig.withBMC`` runs sby in ``bmc`` mode,
   which does not check ``cover`` at all (only ``cover`` mode does).  The
   evidently intended condition is ``io.cmd.valid``, and that is what
   :func:`test_formal_covers_reached` covers.

   Note the Scala's strong asserts include ``state =/= Idle -> cmd.valid``.
   That holds only because ``formalAssumesSlave`` forbids dropping ``valid``
   before the beat fires -- and ``cmd.ready`` is low for the whole
   Sampling/Waiting excursion, so the beat never fires and the master is
   pinned.  The directed tests below deliberately violate that (a real
   ``s2mPipe`` does drop ``valid``), so the strong check is only applied to
   the run that keeps the assumption.

2. **Directed vsync / immediate swap sequences** against
   :class:`SwapBufferModel`, a cycle-accurate Python transliteration of the
   Scala state machine.  The randomised run compares every output of the RTL
   against the model on every cycle, which is the part that would catch a
   mis-transliterated ``>=`` or an off-by-one in ``swapsPending``.
"""

import random

import pytest
from amaranth.hdl import Fragment
from amaranth.sim import Simulator

from avoodoo.frontend.swap_buffer import State, SwapBuffer

PERIOD = 1e-6


# ---------------------------------------------------------------------------
# reference model
# ---------------------------------------------------------------------------


class SwapBufferModel:
    """Cycle-accurate model of ``SwapBuffer.scala``'s sequential behaviour."""

    def __init__(self):
        self.state = State.Idle
        self.retrace_counter = 0
        self.swap_count = 0
        self.swaps_pending = 0
        self.v_retrace_prev = 0
        self.captured_interval = 0

    # -- combinational outputs, from the current register state -------------

    @property
    def cmd_ready(self):
        return int(self.state == State.Idle)

    @property
    def waiting(self):
        return int(self.state != State.Idle)

    def step(self, *, cmd_valid, v_retrace, vsync_enable, swap_interval,
             swap_cmd_enqueued, swap_count_set=None):
        """Apply one clock edge; returns ``swap_completed`` for that cycle."""
        fire = cmd_valid and self.cmd_ready
        rising = v_retrace and not self.v_retrace_prev

        swap_completed = False
        nxt_state = self.state
        nxt_count = self.swap_count
        nxt_retrace = self.retrace_counter
        nxt_interval = self.captured_interval

        if self.state == State.Idle:
            if fire:
                nxt_state = State.Sampling
        elif self.state == State.Sampling:
            if not vsync_enable:
                nxt_count = (self.swap_count + 1) & 0x3
                swap_completed = True
                nxt_state = State.Idle
            else:
                nxt_state = State.Waiting
                nxt_retrace = 0
                nxt_interval = swap_interval
        elif self.state == State.Waiting:
            if rising:
                if self.retrace_counter >= self.captured_interval:
                    nxt_count = (self.swap_count + 1) & 0x3
                    swap_completed = True
                    nxt_state = State.Idle
                else:
                    nxt_retrace = (self.retrace_counter + 1) & 0xFF

        if swap_cmd_enqueued and not swap_completed:
            self.swaps_pending = (self.swaps_pending + 1) & 0x7
        elif (not swap_cmd_enqueued) and swap_completed:
            self.swaps_pending = (self.swaps_pending - 1) & 0x7

        if swap_count_set is not None:
            nxt_count = swap_count_set & 0x3

        self.state = nxt_state
        self.swap_count = nxt_count
        self.retrace_counter = nxt_retrace
        self.captured_interval = nxt_interval
        self.v_retrace_prev = int(bool(v_retrace))
        return swap_completed


# ---------------------------------------------------------------------------
# simulation helpers
# ---------------------------------------------------------------------------


def run(dut, tb):
    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()


def drive(ctx, dut, *, cmd_valid=0, v_retrace=0, vsync_enable=0,
          swap_interval=0, swap_cmd_enqueued=0):
    ctx.set(dut.cmd.valid, cmd_valid)
    ctx.set(dut.v_retrace, v_retrace)
    ctx.set(dut.vsync_enable, vsync_enable)
    ctx.set(dut.swap_interval, swap_interval)
    ctx.set(dut.swap_cmd_enqueued, swap_cmd_enqueued)


def sample(ctx, dut):
    return {
        "cmd_ready": ctx.get(dut.cmd.ready),
        "waiting": ctx.get(dut.waiting),
        "swap_count": ctx.get(dut.swap_count),
        "swaps_pending": ctx.get(dut.swaps_pending),
    }


def check_weak(ctx, dut, model):
    """``formalStrong = false`` asserts (SwapBuffer.scala:88-93).

    ``io.cmd.ready === (state === Idle)``, ``io.waiting === (state =/= Idle)``,
    ``io.swapCount === swapCountReg`` and ``io.swapsPending ===
    swapsPendingReg``.  The last two are checked against the model's copy of
    the register, which is the only handle a black-box sim has on it.
    """
    got = sample(ctx, dut)
    assert got["cmd_ready"] == model.cmd_ready
    assert got["waiting"] == model.waiting
    assert got["waiting"] == (0 if got["cmd_ready"] else 1)
    assert got["swap_count"] == model.swap_count
    assert got["swaps_pending"] == model.swaps_pending


def check_strong(model, cmd_valid):
    """``formalStrong = true`` asserts (SwapBuffer.scala:97-103).

    ``state === Sampling -> cmd.valid``, ``state === Waiting -> cmd.valid``
    (both only under ``formalAssumesSlave``) and ``retraceCounter <=
    capturedInterval``.
    """
    if model.state in (State.Sampling, State.Waiting):
        assert cmd_valid, f"{model.state} entered without cmd.valid held"
    if model.state == State.Waiting:
        assert model.retrace_counter <= model.captured_interval


# ---------------------------------------------------------------------------
# directed sequences
# ---------------------------------------------------------------------------


def test_immediate_swap_when_vsync_disabled():
    """vsyncEnable = 0: Idle -> Sampling -> Idle, swapCount + 1."""
    dut = SwapBuffer()
    trace = []

    async def tb(ctx):
        drive(ctx, dut)
        await ctx.tick()
        assert ctx.get(dut.cmd.ready) == 1
        assert ctx.get(dut.waiting) == 0

        # accept the command
        drive(ctx, dut, cmd_valid=1)
        await ctx.tick()
        # now in Sampling: not ready, waiting
        drive(ctx, dut, cmd_valid=1)
        trace.append(sample(ctx, dut))
        assert trace[-1]["cmd_ready"] == 0
        assert trace[-1]["waiting"] == 1
        assert trace[-1]["swap_count"] == 0

        await ctx.tick()
        drive(ctx, dut)
        trace.append(sample(ctx, dut))
        # back in Idle with the swap counted
        assert trace[-1]["cmd_ready"] == 1
        assert trace[-1]["waiting"] == 0
        assert trace[-1]["swap_count"] == 1

    run(dut, tb)


def test_swap_count_wraps_at_four():
    """``swapCountReg`` is 2 bits; four immediate swaps bring it back to 0."""
    dut = SwapBuffer()
    seen = []

    async def tb(ctx):
        drive(ctx, dut)
        await ctx.tick()
        for _ in range(5):
            drive(ctx, dut, cmd_valid=1)
            await ctx.tick()          # Idle -> Sampling
            await ctx.tick()          # Sampling -> Idle (+1)
            drive(ctx, dut)
            seen.append(ctx.get(dut.swap_count))

    run(dut, tb)
    assert seen == [1, 2, 3, 0, 1]


@pytest.mark.parametrize("interval", [0, 1, 3])
def test_vsync_swap_waits_for_interval_retraces(interval):
    """vsyncEnable = 1: the swap lands on the ``interval + 1``-th rising edge.

    ``retraceCounter`` starts at 0 and the completion test is
    ``retraceCounter >= capturedInterval``, so interval 0 completes on the
    first rising edge and interval N on the (N+1)-th.
    """
    dut = SwapBuffer()
    edges_before_swap = []

    async def tb(ctx):
        drive(ctx, dut, vsync_enable=1, swap_interval=interval)
        await ctx.tick()

        drive(ctx, dut, cmd_valid=1, vsync_enable=1, swap_interval=interval)
        await ctx.tick()              # Idle -> Sampling
        await ctx.tick()              # Sampling -> Waiting, interval captured
        drive(ctx, dut, vsync_enable=1, swap_interval=interval)
        assert ctx.get(dut.waiting) == 1

        # The interval is captured, so changing swapInterval now must not move
        # the target.
        drive(ctx, dut, vsync_enable=1, swap_interval=0xFF)

        edges = 0
        for _ in range(40):
            if ctx.get(dut.waiting) == 0:
                break
            # one vRetrace pulse: low, then high for a cycle
            drive(ctx, dut, vsync_enable=1, swap_interval=0xFF, v_retrace=1)
            await ctx.tick()
            edges += 1
            drive(ctx, dut, vsync_enable=1, swap_interval=0xFF, v_retrace=0)
            await ctx.tick()
        edges_before_swap.append(edges)
        assert ctx.get(dut.waiting) == 0
        assert ctx.get(dut.swap_count) == 1

    run(dut, tb)
    assert edges_before_swap == [interval + 1]


def test_vretrace_level_does_not_advance_only_rising_edges_do():
    """``vRetraceRising`` gates the counter; a held-high vRetrace is one edge."""
    dut = SwapBuffer()
    result = {}

    async def tb(ctx):
        drive(ctx, dut, vsync_enable=1, swap_interval=2)
        await ctx.tick()
        drive(ctx, dut, cmd_valid=1, vsync_enable=1, swap_interval=2)
        await ctx.tick()
        await ctx.tick()

        # hold vRetrace high for 20 cycles: exactly one rising edge
        drive(ctx, dut, vsync_enable=1, swap_interval=2, v_retrace=1)
        for _ in range(20):
            await ctx.tick()
        result["still_waiting"] = ctx.get(dut.waiting)

    run(dut, tb)
    assert result["still_waiting"] == 1


def test_swaps_pending_counts_enqueued_minus_completed():
    """``swapsPendingReg`` is +1 on enqueue, -1 on completion, 0 if both."""
    dut = SwapBuffer()
    seen = []

    async def tb(ctx):
        drive(ctx, dut)
        await ctx.tick()

        # three enqueues, no completions
        for _ in range(3):
            drive(ctx, dut, swap_cmd_enqueued=1)
            await ctx.tick()
        drive(ctx, dut)
        await ctx.tick()
        seen.append(ctx.get(dut.swaps_pending))

        # one immediate swap -> one completion -> back to 2
        drive(ctx, dut, cmd_valid=1)
        await ctx.tick()
        drive(ctx, dut)
        await ctx.tick()
        seen.append(ctx.get(dut.swaps_pending))

        # enqueue *and* complete in the same cycle: net zero
        drive(ctx, dut, cmd_valid=1)
        await ctx.tick()
        drive(ctx, dut, swap_cmd_enqueued=1)   # Sampling completes this cycle
        await ctx.tick()
        drive(ctx, dut)
        await ctx.tick()
        seen.append(ctx.get(dut.swaps_pending))

    run(dut, tb)
    assert seen == [3, 2, 2]


def test_command_is_accepted_immediately_in_idle_only():
    """``cmd.ready`` is exactly ``state === Idle`` -- no wait states in Idle."""
    dut = SwapBuffer()
    readies = []

    async def tb(ctx):
        drive(ctx, dut, cmd_valid=1, vsync_enable=1, swap_interval=1)
        for _ in range(6):
            readies.append(ctx.get(dut.cmd.ready))
            await ctx.tick()

    run(dut, tb)
    # Idle, then Sampling and Waiting keep it low
    assert readies[0] == 1
    assert readies[1:] == [0] * 5


# ---------------------------------------------------------------------------
# the debug load port (CoreSim only; not in the Scala)
# ---------------------------------------------------------------------------


def test_dbg_load_port_absent_by_default():
    """The default instance's port list must match the Scala's exactly."""
    dut = SwapBuffer()
    Fragment.get(dut, None)          # silence UnusedElaboratable
    names = {name for name, _ in dut.signature.members.items()}
    assert names == {
        "cmd", "v_retrace", "vsync_enable", "swap_interval",
        "swap_cmd_enqueued", "waiting", "swap_count", "swaps_pending",
    }


def test_dbg_load_port_sets_swap_count():
    """``dbg_swap_count_set``/``_we``: the CoreSim backdoor, all four values."""
    dut = SwapBuffer(dbg_load_port=True)
    seen = []

    async def tb(ctx):
        drive(ctx, dut)
        ctx.set(dut.swap_count_we, 0)
        await ctx.tick()
        for value in (3, 2, 1, 0):
            ctx.set(dut.swap_count_set, value)
            ctx.set(dut.swap_count_we, 1)
            await ctx.tick()
            ctx.set(dut.swap_count_we, 0)
            await ctx.tick()
            seen.append(ctx.get(dut.swap_count))

    run(dut, tb)
    assert seen == [3, 2, 1, 0]


def test_dbg_load_port_wins_over_a_concurrent_swap():
    """A load in the cycle the FSM increments takes the loaded value."""
    dut = SwapBuffer(dbg_load_port=True)
    result = {}

    async def tb(ctx):
        drive(ctx, dut)
        ctx.set(dut.swap_count_we, 0)
        await ctx.tick()
        drive(ctx, dut, cmd_valid=1)
        await ctx.tick()               # Idle -> Sampling
        drive(ctx, dut)
        ctx.set(dut.swap_count_set, 2)
        ctx.set(dut.swap_count_we, 1)
        await ctx.tick()               # Sampling completes *and* loads
        ctx.set(dut.swap_count_we, 0)
        result["count"] = ctx.get(dut.swap_count)
        result["waiting"] = ctx.get(dut.waiting)

    run(dut, tb)
    assert result["count"] == 2
    assert result["waiting"] == 0


# ---------------------------------------------------------------------------
# randomised: model equivalence + the formal invariants, every cycle
# ---------------------------------------------------------------------------


def _random_run(seed, cycles, *, hold_valid, strong):
    """One randomised run; returns the states/conditions it covered."""
    dut = SwapBuffer()
    model = SwapBufferModel()
    covered = {"sampling_valid": False, "waiting_valid": False,
               "waiting_counting": False, "immediate": False, "vsync": False}
    rng = random.Random(seed)

    async def tb(ctx):
        cmd_valid = 0
        v_retrace = 0
        for _ in range(cycles):
            if not cmd_valid or (not hold_valid):
                cmd_valid = int(rng.random() < 0.25)
            # a plausible retrace: mostly low, occasional pulse
            if rng.random() < 0.15:
                v_retrace ^= 1
            vsync_enable = int(rng.random() < 0.5)
            swap_interval = rng.choice([0, 0, 1, 2, 3, rng.randrange(256)])
            swap_cmd_enqueued = int(rng.random() < 0.1)

            drive(ctx, dut, cmd_valid=cmd_valid, v_retrace=v_retrace,
                  vsync_enable=vsync_enable, swap_interval=swap_interval,
                  swap_cmd_enqueued=swap_cmd_enqueued)

            # combinational outputs must agree with the model *before* the edge
            check_weak(ctx, dut, model)
            if strong:
                check_strong(model, cmd_valid)

            # the three `cover` statements of the Scala formal DUT, with
            # `cmd.ready` read as the intended `cmd.valid` (see the module
            # docstring: as written they are unreachable)
            if model.state == State.Sampling and cmd_valid:
                covered["sampling_valid"] = True
            if model.state == State.Waiting and cmd_valid:
                covered["waiting_valid"] = True
            if model.state == State.Waiting and model.retrace_counter > 0:
                covered["waiting_counting"] = True

            fired = cmd_valid and model.cmd_ready
            if model.state == State.Sampling:
                covered["immediate" if not vsync_enable else "vsync"] = True

            model.step(cmd_valid=cmd_valid, v_retrace=v_retrace,
                       vsync_enable=vsync_enable, swap_interval=swap_interval,
                       swap_cmd_enqueued=swap_cmd_enqueued)
            await ctx.tick()
            if fired and not hold_valid:
                cmd_valid = 0

        check_weak(ctx, dut, model)

    run(dut, tb)
    return covered


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_matches_model_and_weak_invariants(seed):
    """Free-running stimulus; ``valid`` may drop, so only the weak asserts."""
    _random_run(seed, 1500, hold_valid=False, strong=False)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_under_slave_assumption_holds_strong_invariants(seed):
    """``cmd.formalAssumesSlave()``: ``valid`` held until fire, strong asserts."""
    _random_run(seed, 1500, hold_valid=True, strong=True)


def test_formal_covers_reached():
    """The three ``cover`` statements of ``SwapBufferFormalTest`` are hit."""
    covered = {}
    for seed in range(8):
        for key, value in _random_run(seed, 800, hold_valid=True,
                                      strong=True).items():
            covered[key] = covered.get(key, False) or value
        if all(covered.values()):
            break
    assert covered["sampling_valid"], "cover(cmd.valid && state === Sampling)"
    assert covered["waiting_valid"], "cover(cmd.valid && state === Waiting)"
    assert covered["waiting_counting"], "cover(state === Waiting && retraceCounter > 0)"
    assert covered["immediate"] and covered["vsync"]
