"""Phase 1.4 unit tests for :mod:`avoodoo.lib.pipeline`.

Three kinds of test:

* directed handshake tests that pin the cycle behaviour a reader of
  ``spinal/lib/misc/pipeline/*.scala`` would predict (latency, bubble
  collapsing, what ``haltWhen``/``throwWhen``/``duplicateWhen`` do to a
  stalled chain);
* randomised back-pressure tests of a four-``CtrlLink`` / three-``StageLink``
  chain with a halt and a throw in the middle, compared **every cycle**
  against a Python model transcribed straight from the Scala's equations
  rather than from the Python implementation;
* a structural test that rebuilds ``ColorCombine.scala``'s stage skeleton and
  its payload liveness and asserts the register count and latency match what
  the Scala produces.
"""

import random

import pytest
from amaranth import Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import data
from amaranth.sim import Simulator

from avoodoo.lib import pipeline as pl
from avoodoo.lib import stream as st

PERIOD = 1e-6


async def tick(ctx, *outputs, domain="sync"):
    """``ctx.tick(...).sample(...)`` minus the ``(clk_hit, rst_active)`` prefix.

    Sampling happens just before the active edge, so what comes back is the
    pre-edge (this cycle's combinational) value of each signal.
    """
    return (await ctx.tick(domain).sample(*outputs))[2:]


def load(m, key):
    """``driveFrom`` body: copy the stream payload into ``node(key)``."""
    def con(node, value):
        m.d.comb += node(key).eq(value)
    return con


def store(m, key):
    """``driveTo`` body: copy ``node(key)`` out onto the stream payload."""
    def con(value, node):
        m.d.comb += value.eq(node(key))
    return con


def run(dut, tb):
    """Simulate a :class:`Module` (or elaboratable) on a single ``sync`` clock."""
    if isinstance(dut, Module):
        top = dut
    else:
        top = Module()
        top.submodules.dut = dut
    keepalive = Signal()
    top.d.sync += keepalive.eq(~keepalive)

    sim = Simulator(top)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()


# ==========================================================================
# plain StageLink chains
# ==========================================================================


def _stage_chain(n_stages, *, width=8, collapse_bubble=True, hold_payload=False):
    """``n_stages`` StageLinks, ``driveFrom`` in, ``driveTo`` out, one payload."""
    m = Module()
    m.i = st.Stream(unsigned(width), path=("i",))
    m.o = st.Stream(unsigned(width), path=("o",))
    nodes = [pl.Node(m, f"n{k}") for k in range(n_stages + 1)]
    DATA = pl.Payload(unsigned(width), name="DATA")

    nodes[0].drive_from(m.i, load(m, DATA))
    nodes[-1].drive_to(m.o, store(m, DATA))

    links = [pl.StageLink(nodes[k], nodes[k + 1],
                          collapse_bubble=collapse_bubble,
                          hold_payload=hold_payload)
             for k in range(n_stages)]
    m.report = pl.Builder(links)
    m.nodes = nodes
    m.DATA = DATA
    return m


@pytest.mark.parametrize("n_stages", [1, 2, 3, 4, 8])
def test_stage_chain_latency_and_throughput(n_stages):
    """Latency is one cycle per StageLink and throughput is one per cycle."""
    dut = _stage_chain(n_stages)
    assert dut.report.latency == n_stages
    assert dut.report.valid_register_count == n_stages
    assert dut.report.payload_register_slots == n_stages

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.i.valid, 1)
        seen = []
        for k in range(n_stages + 12):
            ctx.set(dut.i.payload, 0x10 + k)
            valid, payload, ready = await tick(ctx,
                dut.o.valid, dut.o.payload, dut.i.ready)
            assert ready, "a fully drained collapse-bubble chain never stalls"
            if valid:
                seen.append(payload)
            # First output lands on the tick n_stages after the first push.
            assert bool(valid) == (k >= n_stages)
        assert seen == [0x10 + k for k in range(len(seen))]

    run(dut, tb)


def test_stage_chain_collapse_bubble_fills_holes():
    """``collapseBubble`` lets an empty slot be filled the same cycle."""
    dut = _stage_chain(3)

    async def tb(ctx):
        ctx.set(dut.o.ready, 0)
        ctx.set(dut.i.valid, 1)
        # Three pushes fill the three stage registers even with the output
        # blocked, because up.ready is forced high while down is invalid.
        for k in range(3):
            ctx.set(dut.i.payload, k)
            ready, = await tick(ctx, dut.i.ready)
            assert ready, f"slot {k} should still accept"
        # Now the chain is full and output is blocked.
        ready, valid = await tick(ctx, dut.i.ready, dut.o.valid)
        assert not ready and valid

    run(dut, tb)


def test_stage_chain_without_collapse_is_half_rate_on_a_hole():
    """``withoutCollapse()`` drops ``up.ready setWhen(!down.isValid)``."""
    dut = _stage_chain(1, collapse_bubble=False)

    async def tb(ctx):
        ctx.set(dut.o.ready, 0)
        ctx.set(dut.i.valid, 1)
        ctx.set(dut.i.payload, 0xAB)
        # One push gets in (the register is empty, up.ready = down.ready = 0
        # -- so it does NOT). Without collapse, up.ready is purely down.ready.
        ready, = await tick(ctx, dut.i.ready)
        assert not ready
        ctx.set(dut.o.ready, 1)
        ready, = await tick(ctx, dut.i.ready)
        assert ready

    run(dut, tb)


def test_stage_chain_backpressure_random():
    """Random producer/consumer: tokens in order, no loss, no duplication."""
    dut = _stage_chain(4)
    rng = random.Random(0x5EED)

    async def tb(ctx):
        pushed, popped = [], []
        nxt = 1
        for _ in range(600):
            ctx.set(dut.i.valid, rng.random() < 0.6)
            ctx.set(dut.i.payload, nxt)
            ctx.set(dut.o.ready, rng.random() < 0.6)
            i_valid, i_ready, o_valid, o_ready, o_payload = await tick(ctx,
                dut.i.valid, dut.i.ready, dut.o.valid, dut.o.ready, dut.o.payload)
            if i_valid and i_ready:
                pushed.append(nxt)
                nxt = (nxt + 1) & 0xFF
            if o_valid and o_ready:
                popped.append(o_payload)
        assert popped == pushed[:len(popped)]
        assert len(pushed) - len(popped) <= 4, "at most one token per stage in flight"

    run(dut, tb)


def test_hold_payload_keeps_the_last_payload_through_a_bubble():
    """``withPayloadHold()`` loads on ``up.isValid && up.isReady``."""
    plain = _stage_chain(1)
    held = _stage_chain(1, hold_payload=True)

    async def drive(ctx, dut, expect_after_bubble):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.i.valid, 1)
        ctx.set(dut.i.payload, 0x55)
        await ctx.tick()
        # Bubble: valid low, payload changes underneath.
        ctx.set(dut.i.valid, 0)
        ctx.set(dut.i.payload, 0x00)
        valid, payload = await tick(ctx, dut.o.valid, dut.o.payload)
        assert valid and payload == 0x55
        valid, payload = await tick(ctx, dut.o.valid, dut.o.payload)
        assert not valid
        assert payload == expect_after_bubble

    async def plain_tb(ctx):
        await drive(ctx, plain, 0x00)

    async def held_tb(ctx):
        await drive(ctx, held, 0x55)

    run(plain, plain_tb)
    run(held, held_tb)


# ==========================================================================
# DirectLink -- Fpxx2AFix(pipeStages = 0)
# ==========================================================================


def _fpxx2afix_skeleton(pipe_stages):
    """The ``Fpxx2AFix.scala`` shape: two nodes, ``insert`` on n0, one link."""
    m = Module()
    m.op = st.Stream(unsigned(16), path=("op",))
    m.result = st.Stream(unsigned(16), path=("result",))

    n0 = pl.Node(m, "n0")
    n1 = pl.Node(m, "n1")
    n0.arbitrate_from(m.op)
    OP = n0.insert(m.op.payload, name="op")
    SIGN = n0.insert(m.op.payload[15], name="sign")
    m.d.comb += m.result.payload.eq(n1(OP) ^ (n1(SIGN) << 3))
    n1.arbitrate_to(m.result)

    link = pl.StageLink(n0, n1) if pipe_stages >= 1 else pl.DirectLink(n0, n1)
    m.report = pl.Builder(link)
    return m


def test_fpxx2afix_direct_link_is_combinational():
    dut = _fpxx2afix_skeleton(0)
    assert dut.report.latency == 0
    assert dut.report.valid_register_count == 0
    assert dut.report.payload_register_slots == 0

    async def tb(ctx):
        ctx.set(dut.result.ready, 1)
        ctx.set(dut.op.valid, 1)
        ctx.set(dut.op.payload, 0x8001)
        await ctx.delay(PERIOD / 4)
        assert ctx.get(dut.result.valid)
        assert ctx.get(dut.result.payload) == 0x8001 ^ 0x8
        assert ctx.get(dut.op.ready)
        ctx.set(dut.result.ready, 0)
        await ctx.delay(PERIOD / 4)
        assert not ctx.get(dut.op.ready), "ready is a straight wire through"

    run(dut, tb)


def test_fpxx2afix_stage_link_is_one_cycle():
    dut = _fpxx2afix_skeleton(1)
    assert dut.report.latency == 1
    assert dut.report.valid_register_count == 1
    # Both inserted payloads are live across the single link.
    assert dut.report.payload_register_slots == 2

    async def tb(ctx):
        ctx.set(dut.result.ready, 1)
        ctx.set(dut.op.valid, 1)
        ctx.set(dut.op.payload, 0x8001)
        valid, = await tick(ctx, dut.result.valid)
        assert not valid
        valid, payload = await tick(ctx, dut.result.valid, dut.result.payload)
        assert valid and payload == 0x8001 ^ 0x8

    run(dut, tb)


def test_fpxx2afix_liveness_matches_the_hand_written_port():
    """Only ``ge0``/``shift``/``mant_2c`` cross the link, as in ``Fpxx2AFix``.

    ``Fpxx2AFix.scala`` :29-39 inserts six payloads on ``n0`` but ``n1``
    (:41-66) reads only three of them, so the ``StageLink`` registers three.
    ``avoodoo/lib/fpxx2afix.py`` hand-codes exactly that set of stage
    registers (``n1_ge0``, ``n1_shift``, ``n1_mant_2c``, plus ``n1_flags``
    when ``generateFlags``); this is the cross-check that the generic builder
    prunes the same way, so a module written with it and one written by hand
    are the same netlist.
    """
    m = Module()
    m.op = st.Stream(unsigned(32), path=("op",))
    m.result = st.Stream(unsigned(34), path=("result",))

    n0 = pl.Node(m, "n0")
    n1 = pl.Node(m, "n1")
    n0.arbitrate_from(m.op)
    OP = n0.insert(m.op.payload, name="op")
    SIGN = n0.insert(m.op.payload[31], name="sign")
    GE0 = n0.insert(m.op.payload[23:31] != 0, name="ge0")
    SHIFT = n0.insert(m.op.payload[23:31], name="shift")
    MANT_FULL = n0.insert(Cat(m.op.payload[:23], Const(1, 2)), name="mant_full")
    MANT_2C = n0.insert(Mux(n0(SIGN), ~n0(MANT_FULL), n0(MANT_FULL)),
                        name="mant_2c")
    # n1 reads ge0, shift and mant_2c only.
    m.d.comb += m.result.payload.eq(Cat(n1(GE0), n1(SHIFT), n1(MANT_2C)))
    n1.arbitrate_to(m.result)

    report = pl.Builder(pl.StageLink(n0, n1))
    assert report.latency == 1
    assert report.valid_register_count == 1
    assert [len(report.live_range(p)) for p in (OP, SIGN, MANT_FULL)] == [0, 0, 0]
    assert [len(report.live_range(p)) for p in (GE0, SHIFT, MANT_2C)] == [1, 1, 1]
    assert report.payload_register_slots == 3


def test_stage_registers_are_reset_less_but_valid_is_not():
    """Spinal's ``down(p).setAsReg()`` has no init; ``down.valid`` has one.

    Getting this wrong makes the payload registers read 0 during and just
    after reset where the Spinal Verilog reads X -- the trap written up in
    ``avoodoo/lib/fpxx2afix.py``.
    """
    dut = _stage_chain(2)
    n0, n1, n2 = dut.nodes
    assert n1._ctrl_valid.reset_less is False
    assert n2._ctrl_valid.reset_less is False
    for node in (n0, n1, n2):
        for sig in node.key_to_data.values():
            assert sig.reset_less is True, f"{sig.name} must have no reset"


# ==========================================================================
# CtrlLink: halt / throw / duplicate / terminate / ignoreReady / bypass
# ==========================================================================


def _ctrl_chain(width=8):
    """``c0 -S- c1(halt) -S- c2(throw) -S- c3``, the StageCtrlPipeline shape.

    Four ``CtrlLink``\\ s joined by three ``StageLink``\\ s, so three stage
    registers and three cycles of latency. ``halt`` is an input ANDed with
    ``c1.up.is_valid``; ``throw`` fires on ``payload[0]`` at ``c2.up``.
    """
    m = Module()
    m.i = st.Stream(unsigned(width), path=("i",))
    m.o = st.Stream(unsigned(width), path=("o",))
    m.halt_req = Signal(name="halt_req")

    ctrls = [pl.CtrlLink(m=m, name=f"c{k}") for k in range(4)]
    DATA = pl.Payload(unsigned(width), name="DATA")

    ctrls[0].up.drive_from(m.i, load(m, DATA))
    ctrls[3].down.drive_to(m.o, store(m, DATA))

    ctrls[1].halt_when(ctrls[1].up.is_valid & m.halt_req)
    ctrls[2].throw_when(ctrls[2].up.is_valid & ctrls[2].up(DATA)[0])

    stages = [pl.StageLink(ctrls[k].down, ctrls[k + 1].up) for k in range(3)]
    m.report = pl.Builder(stages, ctrls)
    m.ctrls = ctrls
    m.DATA = DATA
    return m


class CtrlChainModel:
    """Cycle model of :func:`_ctrl_chain`, transcribed from the Scala.

    Node names follow the chain: ``A = c0.up``, ``B = c0.down``,
    ``C = c1.up`` ... ``H = c3.down``. Every equation below is one line of
    ``CtrlLink.build`` / ``StageLink.build`` / ``Node.arbitrate*``.
    """

    def __init__(self):
        self.c_valid = 0
        self.c_data = 0
        self.e_valid = 0
        self.e_data = 0
        self.g_valid = 0
        self.g_data = 0

    def comb(self, i_valid, i_data, o_ready, halt_req):
        # forward: valid/payload
        a_valid, a_data = i_valid, i_data                     # arbitrateFrom
        b_valid, b_data = a_valid, a_data                     # c0: down.valid := up.valid
        c_valid, c_data = self.c_valid, self.c_data
        halt = c_valid and halt_req                           # haltWhen(...)
        d_valid, d_data = (c_valid and not halt), c_data      # halt clears down.valid
        e_valid, e_data = self.e_valid, self.e_data
        throw = e_valid and (e_data & 1)                      # throwWhen(...)
        f_valid, f_data = (e_valid and not throw), e_data     # throw terminates
        g_valid, g_data = self.g_valid, self.g_data
        h_valid, h_data = g_valid, g_data

        # backward: ready. StageLink: up.ready := down.ready; setWhen(!down.isValid)
        h_ready = o_ready
        g_ready = h_ready                                     # c3: up.ready := down.isReady
        f_ready = g_ready or not g_valid
        e_ready = f_ready                                     # c2 has no halt/duplicate
        d_ready = e_ready or not e_valid
        c_ready = 0 if halt else d_ready                      # halt clears up.ready
        b_ready = c_ready or not c_valid
        a_ready = b_ready
        i_ready = a_ready                                     # arbitrateFrom

        return dict(
            i_ready=int(bool(i_ready)), o_valid=int(bool(h_valid)), o_data=h_data,
            b_valid=b_valid, b_data=b_data, b_ready=int(bool(b_ready)),
            d_valid=d_valid, d_data=d_data, d_ready=int(bool(d_ready)),
            f_valid=f_valid, f_data=f_data, f_ready=int(bool(f_ready)),
            throw=int(bool(throw)))

    def step(self, w):
        # StageLink(B, C): no forgetOne on C (c1 only halts).
        if w["b_ready"]:
            self.c_valid, self.c_data = int(bool(w["b_valid"])), w["b_data"]
        # StageLink(D, E): c2's throwWhen created E.forgetOne, and the Scala
        # emits `down.valid clearWhen(forgetOne)` *before* the
        # `when(up.isReady) down.valid := up.isValid`, so a reload wins over
        # the clear. The payload register only ever loads on up.isReady.
        if w["d_ready"]:
            self.e_valid, self.e_data = int(bool(w["d_valid"])), w["d_data"]
        elif w["throw"]:
            self.e_valid = 0
        # StageLink(F, G): no forgetOne on G.
        if w["f_ready"]:
            self.g_valid, self.g_data = int(bool(w["f_valid"])), w["f_data"]


def test_ctrl_chain_structure():
    dut = _ctrl_chain()
    assert dut.report.latency == 3
    assert dut.report.valid_register_count == 3
    assert dut.report.payload_register_slots == 3
    # CtrlLinks add no registers of their own.
    assert all(not link.registered for link in dut.ctrls)


def test_ctrl_chain_clean_latency():
    """With no halt and no throw the chain is three cycles, one token/cycle."""
    dut = _ctrl_chain()

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.halt_req, 0)
        ctx.set(dut.i.valid, 1)
        for k in range(10):
            ctx.set(dut.i.payload, (2 * k + 2) & 0xFF)   # all even: never thrown
            valid, payload = await tick(ctx, dut.o.valid, dut.o.payload)
            assert bool(valid) == (k >= 3)
            if valid:
                assert payload == (2 * (k - 3) + 2) & 0xFF

    run(dut, tb)


def test_halt_blocks_the_chain_combinationally():
    """``haltWhen`` clears down.valid and up.ready, back to ``i.ready``."""
    dut = _ctrl_chain()

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.i.valid, 1)
        ctx.set(dut.i.payload, 0x20)
        ctx.set(dut.halt_req, 0)
        await ctx.tick()            # 0x20 now sits at c1.up
        ctx.set(dut.i.payload, 0x22)
        ctx.set(dut.halt_req, 1)
        # Same cycle: halt reaches i.ready through both collapse-bubble links.
        await ctx.delay(PERIOD / 4)
        assert ctx.get(dut.i.ready) == 0
        for _ in range(6):
            valid, = await tick(ctx, dut.o.valid)
            assert not valid, "nothing gets past a halted stage"
        ctx.set(dut.halt_req, 0)
        await ctx.tick()
        await ctx.tick()
        valid, payload = await tick(ctx, dut.o.valid, dut.o.payload)
        assert valid and payload == 0x20

    run(dut, tb)


def test_throw_removes_the_transaction_even_while_stalled():
    """``throwWhen``'s forgetOne clears the upstream valid register.

    This is the half a bare ``terminateWhen`` would not give. The chain is
    filled behind a blocked output so that the ``StageLink`` feeding the
    throwing ``CtrlLink`` cannot reload: the only way the slot can open is
    the ``down.valid clearWhen(forgetOne)`` that ``throwWhen`` propagates
    upstream. Observable as ``i.ready`` going high again while ``o.ready`` is
    still low.
    """
    dut = _ctrl_chain()

    async def tb(ctx):
        ctx.set(dut.o.ready, 0)          # blocked for most of the test
        ctx.set(dut.halt_req, 0)
        ctx.set(dut.i.valid, 1)
        # 0x02 -> c3.down, 0x01 (odd, thrown) -> c2.up, 0x04 -> c1.up.
        for value in (0x02, 0x01, 0x04):
            ctx.set(dut.i.payload, value)
            ready, = await tick(ctx, dut.i.ready)
            assert ready, f"{value:#04x} should still get in"
        ctx.set(dut.i.valid, 0)
        # Chain full: 0x02 stuck at the output, 0x01 at c2.up, 0x04 at c1.up.
        ready, o_valid = await tick(ctx, dut.i.ready, dut.o.valid)
        assert not ready and o_valid
        # The throw fired on this same cycle; next cycle the slot is free even
        # though the output is still blocked.
        ready, o_valid = await tick(ctx, dut.i.ready, dut.o.valid)
        assert ready, "forgetOne must clear c2.up's valid register"
        assert o_valid
        # Drain: 0x01 never comes out.
        ctx.set(dut.o.ready, 1)
        got = []
        for _ in range(10):
            valid, payload = await tick(ctx, dut.o.valid, dut.o.payload)
            if valid:
                got.append(int(payload))
        assert 0x01 not in got
        assert got[:2] == [0x02, 0x04]

    run(dut, tb)


def test_throw_filters_tokens_in_a_flowing_pipeline():
    dut = _ctrl_chain()

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        ctx.set(dut.halt_req, 0)
        ctx.set(dut.i.valid, 1)
        sent, got = [], []
        for k in range(24):
            value = (k + 1) & 0xFF
            ctx.set(dut.i.payload, value)
            i_ready, o_valid, o_payload = await tick(ctx,
                dut.i.ready, dut.o.valid, dut.o.payload)
            if i_ready:
                sent.append(value)
            if o_valid:
                got.append(o_payload)
        assert got == [v for v in sent if not v & 1][:len(got)]

    run(dut, tb)


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_ctrl_chain_matches_the_scala_model_cycle_by_cycle(seed):
    """Randomised halt/throw/back-pressure against :class:`CtrlChainModel`."""
    dut = _ctrl_chain()
    rng = random.Random(seed)

    async def tb(ctx):
        model = CtrlChainModel()
        nxt = 1
        for cycle in range(900):
            i_valid = int(rng.random() < 0.65)
            i_data = nxt
            o_ready = int(rng.random() < 0.6)
            halt_req = int(rng.random() < 0.2)
            ctx.set(dut.i.valid, i_valid)
            ctx.set(dut.i.payload, i_data)
            ctx.set(dut.o.ready, o_ready)
            ctx.set(dut.halt_req, halt_req)

            w = model.comb(i_valid, i_data, o_ready, halt_req)
            i_ready, o_valid, o_data = await tick(ctx,
                dut.i.ready, dut.o.valid, dut.o.payload)
            assert (int(i_ready), int(o_valid)) == (w["i_ready"], w["o_valid"]), \
                f"cycle {cycle}: handshake mismatch"
            if w["o_valid"]:
                assert int(o_data) == w["o_data"], f"cycle {cycle}: payload mismatch"
            model.step(w)
            if i_valid and w["i_ready"]:
                nxt = (nxt + 1) & 0xFF

    run(dut, tb)


@pytest.mark.parametrize("seed", [11, 12])
def test_ctrl_chain_token_order_random(seed):
    """Under random halt/back-pressure the surviving tokens keep their order.

    Complements the cycle-exact model test: this one only looks at the token
    stream, so it stays readable as a statement of intent (odd payloads are
    thrown, everything else comes out once, in order).
    """
    dut = _ctrl_chain()
    rng = random.Random(seed)

    async def tb(ctx):
        expected, got = [], []
        nxt = 1
        for _ in range(500):
            ctx.set(dut.i.valid, int(rng.random() < 0.5))
            ctx.set(dut.i.payload, nxt)
            ctx.set(dut.o.ready, int(rng.random() < 0.5))
            ctx.set(dut.halt_req, int(rng.random() < 0.15))

            i_valid, i_ready, o_valid, o_ready, o_data = await tick(ctx,
                dut.i.valid, dut.i.ready, dut.o.valid, dut.o.ready, dut.o.payload)
            if i_valid and i_ready:
                if not nxt & 1:
                    expected.append(nxt)
                nxt = (nxt + 1) & 0xFF
            if o_valid and o_ready:
                got.append(int(o_data))

        assert got, "the randomised run must actually move tokens"
        assert got == expected[:len(got)]

    run(dut, tb)


def test_duplicate_when_replays_the_transaction():
    """``duplicateWhen`` clears up.ready only: down sees the token again."""
    m = Module()
    m.i = st.Stream(unsigned(8), path=("i",))
    m.o = st.Stream(unsigned(8), path=("o",))
    m.dup = Signal()
    DATA = pl.Payload(unsigned(8), name="DATA")
    c = pl.CtrlLink(m=m, name="c")
    c.up.drive_from(m.i, load(m, DATA))
    c.down.drive_to(m.o, store(m, DATA))
    c.duplicate_when(m.dup)
    report = pl.Builder(c)
    assert report.latency == 0

    async def tb(ctx):
        ctx.set(m.o.ready, 1)
        ctx.set(m.i.valid, 1)
        ctx.set(m.i.payload, 0x33)
        ctx.set(m.dup, 1)
        await ctx.delay(PERIOD / 4)
        assert ctx.get(m.o.valid) and ctx.get(m.o.payload) == 0x33
        assert not ctx.get(m.i.ready), "the producer is held while duplicating"
        ctx.set(m.dup, 0)
        await ctx.delay(PERIOD / 4)
        assert ctx.get(m.i.ready)

    run(m, tb)


def test_terminate_when_hides_without_forgetting():
    """``terminateWhen`` masks ``down.valid`` but leaves the producer flowing."""
    m = Module()
    m.i = st.Stream(unsigned(8), path=("i",))
    m.o = st.Stream(unsigned(8), path=("o",))
    m.kill = Signal()
    DATA = pl.Payload(unsigned(8), name="DATA")
    c = pl.CtrlLink(m=m, name="c")
    c.up.drive_from(m.i, load(m, DATA))
    c.down.drive_to(m.o, store(m, DATA))
    c.terminate_when(m.kill)
    pl.Builder(c)

    async def tb(ctx):
        ctx.set(m.o.ready, 1)
        ctx.set(m.i.valid, 1)
        ctx.set(m.i.payload, 0x44)
        ctx.set(m.kill, 1)
        await ctx.delay(PERIOD / 4)
        assert not ctx.get(m.o.valid)
        assert ctx.get(m.i.ready), "terminate does not back-pressure"

    run(m, tb)


def test_ignore_ready_when_beats_halt_on_up_ready():
    """Override order: halt, duplicate, terminate, ignoreReady (last wins)."""
    m = Module()
    m.i = st.Stream(unsigned(8), path=("i",))
    m.o = st.Stream(unsigned(8), path=("o",))
    m.halt = Signal()
    m.ign = Signal()
    DATA = pl.Payload(unsigned(8), name="DATA")
    c = pl.CtrlLink(m=m, name="c")
    c.up.drive_from(m.i, load(m, DATA))
    c.down.drive_to(m.o, store(m, DATA))
    c.halt_when(m.halt)
    c.ignore_ready_when(m.ign)
    pl.Builder(c)

    async def tb(ctx):
        ctx.set(m.o.ready, 1)
        ctx.set(m.i.valid, 1)
        ctx.set(m.halt, 1)
        ctx.set(m.ign, 0)
        await ctx.delay(PERIOD / 4)
        assert not ctx.get(m.i.ready) and not ctx.get(m.o.valid)
        ctx.set(m.ign, 1)
        await ctx.delay(PERIOD / 4)
        assert ctx.get(m.i.ready), "ignoreReady is emitted last, so it wins"
        assert not ctx.get(m.o.valid), "but halt still owns down.valid"

    run(m, tb)


def test_bypass_overrides_one_payload():
    m = Module()
    m.i = st.Stream(unsigned(8), path=("i",))
    m.o = st.Stream(unsigned(8), path=("o",))
    m.hazard = Signal()
    DATA = pl.Payload(unsigned(8), name="DATA")
    c = pl.CtrlLink(m=m, name="c")
    c.up.drive_from(m.i, load(m, DATA))
    c.down.drive_to(m.o, store(m, DATA))
    fixed = c.bypass(DATA)
    with m.If(m.hazard):
        m.d.comb += fixed.eq(0xEE)
    pl.Builder(c)

    async def tb(ctx):
        ctx.set(m.o.ready, 1)
        ctx.set(m.i.valid, 1)
        ctx.set(m.i.payload, 0x12)
        await ctx.delay(PERIOD / 4)
        assert ctx.get(m.o.payload) == 0x12
        ctx.set(m.hazard, 1)
        await ctx.delay(PERIOD / 4)
        assert ctx.get(m.o.payload) == 0xEE

    run(m, tb)


# ==========================================================================
# S2MLink
# ==========================================================================


def test_s2m_link_is_a_skid_buffer():
    """``S2MLink`` cuts ``ready`` with zero latency, like ``Stream.s2mPipe``."""
    m = Module()
    m.i = st.Stream(unsigned(8), path=("i",))
    m.o = st.Stream(unsigned(8), path=("o",))
    DATA = pl.Payload(unsigned(8), name="DATA")
    n0 = pl.Node(m, "n0")
    n1 = pl.Node(m, "n1")
    n0.drive_from(m.i, load(m, DATA))
    n1.drive_to(m.o, store(m, DATA))
    report = pl.Builder(pl.S2MLink(n0, n1))
    assert report.latency == 0

    async def tb(ctx):
        ctx.set(m.o.ready, 1)
        ctx.set(m.i.valid, 1)
        ctx.set(m.i.payload, 0x77)
        await ctx.delay(PERIOD / 4)
        assert ctx.get(m.o.valid) and ctx.get(m.o.payload) == 0x77
        assert ctx.get(m.i.ready)
        # Block the output: the skid register absorbs exactly one beat.
        ctx.set(m.o.ready, 0)
        await ctx.tick()
        await ctx.delay(PERIOD / 4)
        assert not ctx.get(m.i.ready)
        assert ctx.get(m.o.valid) and ctx.get(m.o.payload) == 0x77

    run(m, tb)


# ==========================================================================
# ColorCombine.scala stage skeleton
# ==========================================================================

#: Which ``ColorCombine`` node touches which payload, read off
#: ``ext/SpinalVoodoo/src/voodoo/pixel/ColorCombine.scala`` (``c.trace.enabled``
#: off, so ``TRACE`` is ``null`` and never declared). Writes and reads are not
#: distinguished because Spinal does not distinguish them either: both put the
#: key in ``keyToData``, and the stage registers run from the first touched
#: node to the last.
COLOR_COMBINE_TOUCHES = {
    # loadPassThrough at n0 (:247-265) -> storePassThrough at n8 (:330-348)
    "COORDS": (0, 8), "DEPTH": (0, 8), "ITERATED_ALPHA_PT": (0, 8),
    "RAW_W": (0, 8), "FOG_COLOR": (0, 8), "CHROMA_KEY": (0, 8),
    "ZA_COLOR": (0, 8), "ALPHA_MODE": (0, 8), "FOG_MODE": (0, 8),
    "FBZ_MODE": (0, 8), "DRAW_COLOR_BUFFER_BASE": (0, 8),
    "DRAW_AUX_BUFFER_BASE": (0, 8), "FB_PIXEL_STRIDE": (0, 8),
    # CONFIG: written n0, read at n1, n2, n3, n4, n6 and n8 (invertOutput)
    "CONFIG": (0, 1, 2, 3, 4, 6, 8),
    # selectRgbOther / selectAlphaOther at n0, consumed by stage 1
    "C_OTHER": (0, 1), "A_OTHER": (0, 1),
    # selectRgbLocal / selectAlphaLocal at n0; read at n2 (sub), n3 (factor),
    # n6 (AddMode.CLOCAL / ALOCAL)
    "C_LOCAL": (0, 2, 3, 6), "A_LOCAL": (0, 2, 3, 6),
    # texture converted at n0, last used by the stage-3 factor select
    "TEXTURE_ALPHA": (0, 3), "TEXTURE_RGB": (0, 3),
    "C_AFTER_ZERO": (1, 2), "A_AFTER_ZERO": (1, 2),
    # subtraction result: read at n3 (MSelect.AOTHER) and n5 (multiply)
    "C_AFTER_SUB": (2, 3, 5), "A_AFTER_SUB": (2, 3, 5),
    "C_FACTOR_PRE": (3, 4), "A_FACTOR_PRE": (3, 4),
    "C_FACTOR": (4, 5), "A_FACTOR": (4, 5),
    "C_AFTER_MUL": (5, 6), "A_AFTER_MUL": (5, 6),
    "C_AFTER_ADD": (6, 7), "A_AFTER_ADD": (6, 7),
    "C_CLAMPED": (7, 8), "A_CLAMPED": (7, 8),
}


def _color_combine_skeleton(*, with_payloads):
    """``ColorCombine``'s nine nodes and eight default ``StageLink``\\ s."""
    m = Module()
    m.input = st.Stream(unsigned(8), path=("input",))
    m.output = st.Stream(unsigned(8), path=("output",))
    nodes = [pl.Node(m, f"n{k}") for k in range(9)]

    payloads = {}
    if with_payloads:
        # Shapes are irrelevant to the register *count*; one bit each keeps
        # the elaboration cheap while the liveness is the real thing tested.
        for name, touched in COLOR_COMBINE_TOUCHES.items():
            p = pl.Payload(unsigned(1), name=name)
            payloads[name] = p
            for k in touched:
                nodes[k](p)                     # touch == keyToData entry
            m.d.comb += nodes[touched[0]](p).eq(m.input.payload[0])

    def echo(value, node):
        m.d.comb += value.eq(m.input.payload)

    nodes[0].drive_from(m.input)
    nodes[8].drive_to(m.output, echo)

    links = [pl.StageLink(nodes[k], nodes[k + 1]) for k in range(8)]
    m.report = pl.Builder(links)
    m.payloads = payloads
    return m


def test_color_combine_skeleton_latency():
    """Eight StageLinks: eight valid registers, eight cycles, no payloads."""
    dut = _color_combine_skeleton(with_payloads=False)
    assert dut.report.latency == 8
    assert dut.report.valid_register_count == 8
    assert dut.report.payload_register_slots == 0

    async def tb(ctx):
        ctx.set(dut.output.ready, 1)
        ctx.set(dut.input.valid, 1)
        ctx.set(dut.input.payload, 0x5A)
        for k in range(12):
            valid, = await tick(ctx, dut.output.valid)
            assert bool(valid) == (k >= 8), f"first output must land on tick 8, not {k}"

    run(dut, tb)


def test_color_combine_skeleton_registers():
    """Per-payload stage registers match ColorCombine.scala's liveness.

    Each payload is registered on the links between the first and the last
    node that touches it, so the total is far below the naive 33 x 8.
    """
    dut = _color_combine_skeleton(with_payloads=True)
    assert dut.report.latency == 8
    assert dut.report.valid_register_count == 8

    expected_per_payload = {}
    for name, touched in COLOR_COMBINE_TOUCHES.items():
        expected_per_payload[name] = max(touched) - min(touched)

    for name, payload in dut.payloads.items():
        got = len(dut.report.live_range(payload))
        assert got == expected_per_payload[name], (
            f"{name}: {got} stage registers, expected "
            f"{expected_per_payload[name]} (links n{min(COLOR_COMBINE_TOUCHES[name])}"
            f"..n{max(COLOR_COMBINE_TOUCHES[name])})")

    assert sum(expected_per_payload.values()) == 150
    assert dut.report.payload_register_slots == 150
    # The naive "every payload on every link" figure, for contrast:
    # 34 payloads (TRACE excluded) x 8 links.
    assert len(COLOR_COMBINE_TOUCHES) == 34
    assert len(COLOR_COMBINE_TOUCHES) * 8 == 272


def test_payload_liveness_is_bounded_by_the_touched_nodes():
    """A payload written at n0 and read at n1 costs one register, not four."""
    m = Module()
    m.i = st.Stream(unsigned(8), path=("i",))
    m.o = st.Stream(unsigned(8), path=("o",))
    nodes = [pl.Node(m, f"n{k}") for k in range(5)]
    EARLY = pl.Payload(unsigned(8), name="EARLY")
    LATE = pl.Payload(unsigned(8), name="LATE")

    nodes[0].drive_from(m.i, load(m, EARLY))
    m.d.comb += nodes[1](LATE).eq(nodes[1](EARLY) + 1)
    nodes[4].drive_to(m.o, store(m, LATE))

    links = [pl.StageLink(nodes[k], nodes[k + 1]) for k in range(4)]
    report = pl.Builder(links)

    assert len(report.live_range(EARLY)) == 1        # n0 -> n1 only
    assert len(report.live_range(LATE)) == 3         # n1 -> n4
    assert report.payload_register_slots == 4
    assert report.latency == 4

    async def tb(ctx):
        ctx.set(m.o.ready, 1)
        ctx.set(m.i.valid, 1)
        ctx.set(m.i.payload, 0x10)
        for k in range(6):
            valid, payload = await tick(ctx, m.o.valid, m.o.payload)
            if k >= 4:
                assert valid and payload == 0x11

    run(m, tb)


# ==========================================================================
# structured payloads and misc
# ==========================================================================


def test_struct_payload_travels_through_the_chain():
    """Payloads may be ``data`` layouts, as ColorCombine's Config/Color are."""
    layout = data.StructLayout({"lo": unsigned(4), "hi": unsigned(4)})
    m = Module()
    m.i = st.Stream(layout, path=("i",))
    m.o = st.Stream(layout, path=("o",))
    P = pl.Payload(layout, name="P")
    nodes = [pl.Node(m, f"n{k}") for k in range(3)]
    nodes[0].drive_from(m.i, load(m, P))
    nodes[2].drive_to(m.o, store(m, P))
    report = pl.Builder([pl.StageLink(nodes[k], nodes[k + 1]) for k in range(2)])
    assert report.payload_register_slots == 2

    async def tb(ctx):
        ctx.set(m.o.ready, 1)
        ctx.set(m.i.valid, 1)
        ctx.set(m.i.payload.lo, 0x3)
        ctx.set(m.i.payload.hi, 0xC)
        await ctx.tick()
        await ctx.tick()
        valid, lo, hi = await tick(ctx,
            m.o.valid, m.o.payload.lo, m.o.payload.hi)
        assert valid and lo == 0x3 and hi == 0xC

    run(m, tb)


def test_flow_pipeline_has_no_ready_and_loads_unconditionally():
    """No ``ready`` anywhere -> StageLink's other branch, a Flow pipeline."""
    m = Module()
    m.i = st.Flow(unsigned(8), path=("i",))
    m.o = st.Flow(unsigned(8), path=("o",))
    DATA = pl.Payload(unsigned(8), name="DATA")
    nodes = [pl.Node(m, f"n{k}") for k in range(3)]
    nodes[0].drive_from(m.i, load(m, DATA))
    nodes[2].drive_to(m.o, store(m, DATA))
    report = pl.Builder([pl.StageLink(nodes[k], nodes[k + 1]) for k in range(2)])
    assert report.latency == 2
    assert all(n._ctrl_ready is None for n in nodes), "no back-pressure exists"

    async def tb(ctx):
        ctx.set(m.i.valid, 1)
        ctx.set(m.i.payload, 0x99)
        await ctx.tick()
        await ctx.tick()
        valid, payload = await tick(ctx, m.o.valid, m.o.payload)
        assert valid and payload == 0x99
        ctx.set(m.i.valid, 0)
        await ctx.tick()
        await ctx.tick()
        valid, = await tick(ctx, m.o.valid)
        assert not valid

    run(m, tb)


def test_builder_rejects_an_unreachable_link_set():
    """The propagation passes are seeded from the ends; a ring has none."""
    m = Module()
    a = pl.Node(m, "a")
    b = pl.Node(m, "b")
    with pytest.raises(ValueError, match="connected pipeline"):
        pl.Builder([pl.StageLink(a, b), pl.StageLink(b, a)])
    m._MustUse__silence = True      # never elaborated; keep pytest quiet


def test_builder_accepts_two_independent_chains():
    """Two unrelated chains in one Builder call is legal, as in the Scala."""
    m = Module()
    a, b, c, d = (pl.Node(m, n) for n in "abcd")
    P = pl.Payload(unsigned(4), name="P")
    m.d.comb += [a(P).eq(1), c(P).eq(2)]
    m.d.comb += [Signal(4).eq(b(P)), Signal(4).eq(d(P))]
    report = pl.Builder([pl.StageLink(a, b), pl.StageLink(c, d)])
    assert report.latency == 2
    assert report.payload_register_slots == 2


def test_forget_one_without_support_is_rejected():
    """``Node.build``'s ``forgetOneSupported`` guard, as in the Scala."""
    m = Module()
    m.o = st.Stream(unsigned(8), path=("o",))
    DATA = pl.Payload(unsigned(8), name="DATA")
    c = pl.CtrlLink(m=m, name="c")
    # c.up is driven by nothing -- no arbitrateFrom, no upstream link -- so it
    # never gets forgetOneSupported, and a throw needs it.
    m.d.comb += c.up(DATA).eq(0x10)
    c.down.drive_to(m.o, store(m, DATA))
    c.throw_when(c.up(DATA)[0])
    with pytest.raises(ValueError, match="forgetOne"):
        pl.Builder(c)
