"""Phase 1.1 unit tests for :mod:`avoodoo.lib.stream`.

Two kinds of test:

* directed handshake tests that pin the exact cycle behaviour a reader of
  ``spinal/lib/Stream.scala`` would predict (latency, bubble collapsing, the
  half-rate of ``halfPipe``, arbitration order, ...);
* randomised back-pressure tests against Python reference models. The FIFO
  model is transcribed from the Scala independently of
  :class:`avoodoo.lib.stream.Fifo`, and every observable output -- including
  ``occupancy`` and ``availability`` -- is compared **every cycle**, because
  the plan's §4.2 lockstep diff will hold the Verilog to exactly that.
"""

import random

import pytest
from amaranth import Module, Signal, unsigned
from amaranth.hdl import ClockDomain
from amaranth.lib import data
from amaranth.sim import Simulator
from amaranth.utils import ceil_log2

from avoodoo.lib import stream as st
from avoodoo.lib.fragment import fragment_layout

PERIOD = 1e-6


def run(dut, tb):
    """Simulate ``dut`` on a single ``sync`` clock.

    A dummy ``sync`` register is always added: several helpers under test are
    purely combinational, and ``add_clock`` refuses a design with no ``sync``
    domain.
    """
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


async def cycle(ctx, inputs, outputs):
    """Apply ``inputs``, sample ``outputs`` just before the next active edge.

    ``ctx.tick().sample()`` yields ``(clk_hit, rst_active, *values)``; only the
    values are interesting here.
    """
    for sig, value in inputs:
        ctx.set(sig, value)
    return (await ctx.tick().sample(*outputs))[2:]


def is_pow2(n):
    return n > 0 and (n & (n - 1)) == 0


# ---------------------------------------------------------------------------
# pipes
# ---------------------------------------------------------------------------


def test_m2s_pipe_latency_one_and_full_throughput():
    dut = st.M2SPipe(unsigned(8))
    seen = []

    async def tb(ctx):
        outs = (dut.i.ready, dut.o.valid, dut.o.payload)
        # sink always ready; feed 0..4 back to back
        for value in range(5):
            r, v, p = await cycle(ctx, [(dut.i.valid, 1), (dut.i.payload, value),
                                        (dut.o.ready, 1)], outs)
            assert r == 1, "collapsBubble keeps the pipe accepting every cycle"
            seen.append((v, p))
        for _ in range(3):
            v, p = (await cycle(ctx, [(dut.i.valid, 0), (dut.o.ready, 1)],
                                (dut.o.valid, dut.o.payload)))
            seen.append((v, p))

    run(dut, tb)
    fired = [p for v, p in seen if v]
    assert fired == [0, 1, 2, 3, 4]
    # latency 1: nothing valid on the first cycle.
    assert seen[0][0] == 0


def test_m2s_pipe_holds_payload_under_backpressure():
    dut = st.M2SPipe(unsigned(8))

    async def tb(ctx):
        outs = (dut.i.ready, dut.o.valid, dut.o.payload)
        await cycle(ctx, [(dut.i.valid, 1), (dut.i.payload, 0xA5), (dut.o.ready, 0)], outs)
        # payload is registered, sink stalled: o stays valid, i.ready drops
        # (the one buffered slot is taken).
        for _ in range(4):
            r, v, p = await cycle(ctx, [(dut.i.valid, 1), (dut.i.payload, 0x5A),
                                        (dut.o.ready, 0)], outs)
            assert (r, v, p) == (0, 1, 0xA5)
        r, v, p = await cycle(ctx, [(dut.i.valid, 0), (dut.o.ready, 1)], outs)
        assert (r, v, p) == (1, 1, 0xA5)

    run(dut, tb)


def test_s2m_pipe_is_combinational_until_stalled():
    dut = st.S2MPipe(unsigned(8))

    async def tb(ctx):
        outs = (dut.i.ready, dut.o.valid, dut.o.payload)
        # latency 0: payload flows through the same cycle.
        r, v, p = await cycle(ctx, [(dut.i.valid, 1), (dut.i.payload, 0x11),
                                    (dut.o.ready, 1)], outs)
        assert (r, v, p) == (1, 1, 0x11)
        # stall: the value is captured into rData, i.ready falls next cycle.
        r, v, p = await cycle(ctx, [(dut.i.valid, 1), (dut.i.payload, 0x22),
                                    (dut.o.ready, 0)], outs)
        assert (r, v, p) == (1, 1, 0x22)
        r, v, p = await cycle(ctx, [(dut.i.valid, 0), (dut.o.ready, 0)], outs)
        assert (r, v, p) == (0, 1, 0x22), "buffered value survives"
        r, v, p = await cycle(ctx, [(dut.i.valid, 0), (dut.o.ready, 1)], outs)
        assert (r, v, p) == (0, 1, 0x22)
        r, v, p = await cycle(ctx, [(dut.i.valid, 0), (dut.o.ready, 1)], outs)
        assert (r, v) == (1, 0)

    run(dut, tb)


def test_half_pipe_runs_at_half_rate():
    dut = st.HalfPipe(unsigned(8))
    accepted, emitted = [], []

    async def tb(ctx):
        outs = (dut.i.ready, dut.o.valid, dut.o.payload)
        nxt = 0
        for _ in range(20):
            r, v, p = await cycle(ctx, [(dut.i.valid, 1), (dut.i.payload, nxt),
                                        (dut.o.ready, 1)], outs)
            if r:
                accepted.append(nxt)
                nxt += 1
            if v:
                emitted.append(p)

    run(dut, tb)
    assert emitted == accepted[:len(emitted)]
    # one transfer every two cycles
    assert 9 <= len(emitted) <= 10


@pytest.mark.parametrize("pipe_cls", [st.M2SPipe, st.S2MPipe, st.HalfPipe])
def test_pipe_conserves_tokens_under_random_backpressure(pipe_cls):
    dut = pipe_cls(unsigned(8))
    rng = random.Random(0x5EED)
    pushed, popped = [], []

    async def tb(ctx):
        outs = (dut.i.ready, dut.o.valid, dut.o.payload)
        nxt, valid, held = 0, 0, 0
        for _ in range(400):
            if not valid and rng.random() < 0.7:
                valid, held = 1, nxt & 0xFF
                nxt += 1
            ready = 1 if rng.random() < 0.6 else 0
            r, v, p = await cycle(ctx, [(dut.i.valid, valid), (dut.i.payload, held),
                                        (dut.o.ready, ready)], outs)
            if valid and r:
                pushed.append(held)
                valid = 0
            if v and ready:
                popped.append(p)

    run(dut, tb)
    assert len(popped) > 30
    assert popped == pushed[:len(popped)]


# ---------------------------------------------------------------------------
# fork
# ---------------------------------------------------------------------------


def test_fork_synchronous_fires_all_outputs_together():
    dut = st.Fork(unsigned(8), 3, synchronous=True)

    async def tb(ctx):
        outs = (dut.i.ready, dut.outputs[0].valid, dut.outputs[1].valid,
                dut.outputs[2].valid)
        # one output not ready -> nothing moves, and (Spinal quirk) no output
        # is even valid, because valid := i.valid && i.ready.
        r, v0, v1, v2 = await cycle(ctx, [
            (dut.i.valid, 1), (dut.i.payload, 7),
            (dut.outputs[0].ready, 1), (dut.outputs[1].ready, 1),
            (dut.outputs[2].ready, 0)], outs)
        assert (r, v0, v1, v2) == (0, 0, 0, 0)
        r, v0, v1, v2 = await cycle(ctx, [(dut.outputs[2].ready, 1)], outs)
        assert (r, v0, v1, v2) == (1, 1, 1, 1)

    run(dut, tb)


def test_fork_asynchronous_delivers_each_token_once_per_output():
    n = 5  # FramebufferAccess.scala:172 forks the request stream five ways
    dut = st.Fork(unsigned(8), n, synchronous=False)
    rng = random.Random(1234)
    got = [[] for _ in range(n)]
    pushed = []

    presented = []

    async def tb(ctx):
        outs = [dut.i.ready] + [s.valid for s in dut.outputs]
        nxt, valid, held = 0, 0, 0
        for _ in range(900):
            if not valid:
                valid, held = 1, nxt & 0xFF
                nxt += 1
                presented.append(held)
            readies = [1 if rng.random() < 0.45 else 0 for _ in range(n)]
            ins = [(dut.i.valid, valid), (dut.i.payload, held)]
            for k in range(n):
                ins.append((dut.outputs[k].ready, readies[k]))
            sampled = await cycle(ctx, ins, outs)
            r, valids = sampled[0], sampled[1:]
            for k in range(n):
                if valids[k] and readies[k]:
                    got[k].append(held)
            if valid and r:
                pushed.append(held)
                valid = 0

    run(dut, tb)
    assert len(pushed) > 20
    for k in range(n):
        # in order, no duplicates, no drops
        assert got[k] == presented[:len(got[k])], f"output {k} out of order"
        # every consumed input reached every output exactly once; the token
        # still in flight may already have been taken by some outputs.
        assert len(pushed) <= len(got[k]) <= len(pushed) + 1


def test_fork_single_port_is_a_pass_through():
    dut = st.Fork(unsigned(8), 1)

    async def tb(ctx):
        r, v, p = await cycle(ctx, [(dut.i.valid, 1), (dut.i.payload, 0x3C),
                                    (dut.outputs[0].ready, 1)],
                              (dut.i.ready, dut.outputs[0].valid,
                               dut.outputs[0].payload))
        assert (r, v, p) == (1, 1, 0x3C)

    run(dut, tb)


# ---------------------------------------------------------------------------
# join / demux / mux
# ---------------------------------------------------------------------------


def test_join_waits_for_every_input():
    dut = st.Join([unsigned(8), unsigned(4)])

    async def tb(ctx):
        outs = (dut.o.valid, dut.o.payload.as_value(), dut.i0.ready, dut.i1.ready)
        v, _, r0, r1 = await cycle(ctx, [(dut.i0.valid, 1), (dut.i0.payload, 0xAB),
                                         (dut.i1.valid, 0), (dut.o.ready, 1)], outs)
        assert (v, r0, r1) == (0, 0, 0)
        v, p, r0, r1 = await cycle(ctx, [(dut.i1.valid, 1), (dut.i1.payload, 0x5)],
                                   outs)
        assert (v, r0, r1) == (1, 1, 1)
        assert p == (0x5 << 8) | 0xAB  # _1 is the low field
        # ready is o.fire, so a stalled output stalls both inputs
        v, _, r0, r1 = await cycle(ctx, [(dut.o.ready, 0)], outs)
        assert (v, r0, r1) == (1, 0, 0)

    run(dut, tb)


def test_demux_routes_by_select_and_blocks_others():
    dut = st.Demux(unsigned(8), 2)

    async def tb(ctx):
        outs = (dut.i.ready, dut.outputs[0].valid, dut.outputs[1].valid,
                dut.outputs[0].payload, dut.outputs[1].payload)
        r, v0, v1, p0, p1 = await cycle(ctx, [
            (dut.i.valid, 1), (dut.i.payload, 0x42), (dut.select, 1),
            (dut.outputs[0].ready, 1), (dut.outputs[1].ready, 0)], outs)
        assert (r, v0, v1) == (0, 0, 1)
        assert p0 == p1 == 0x42, "payload fans out unconditionally"
        r, v0, v1, _, _ = await cycle(ctx, [(dut.outputs[1].ready, 1)], outs)
        assert (r, v0, v1) == (1, 0, 1)
        r, v0, v1, _, _ = await cycle(ctx, [(dut.select, 0)], outs)
        assert (r, v0, v1) == (1, 1, 0)

    run(dut, tb)


def test_mux_selects_one_input():
    dut = st.Mux_(unsigned(8), 3)

    async def tb(ctx):
        outs = (dut.o.valid, dut.o.payload, dut.inputs[0].ready,
                dut.inputs[1].ready, dut.inputs[2].ready)
        ins = [(dut.o.ready, 1), (dut.select, 2)]
        for k in range(3):
            ins.append((dut.inputs[k].valid, 1))
            ins.append((dut.inputs[k].payload, 0x10 + k))
        v, p, r0, r1, r2 = await cycle(ctx, ins, outs)
        assert (v, p, r0, r1, r2) == (1, 0x12, 0, 0, 1)
        v, p, r0, r1, r2 = await cycle(ctx, [(dut.select, 0)], outs)
        assert (v, p, r0, r1, r2) == (1, 0x10, 1, 0, 0)

    run(dut, tb)


# ---------------------------------------------------------------------------
# arbiter
# ---------------------------------------------------------------------------


def _drain_arbiter(dut, n, valids, cycles=12):
    """Hold ``valids`` constant, sink always ready; return the chosen ports."""
    chosen = []

    async def tb(ctx):
        ins = [(dut.o.ready, 1)]
        for k in range(n):
            ins.append((dut.inputs[k].valid, valids[k]))
            ins.append((dut.inputs[k].payload, k))
        for _ in range(cycles):
            v, p = await cycle(ctx, ins, (dut.o.valid, dut.o.payload))
            ins = []
            if v:
                chosen.append(p)

    run(dut, tb)
    return chosen


def test_arbiter_lower_first_is_fixed_priority():
    dut = st.Arbiter(unsigned(8), 4, arbitration="lowerFirst")
    assert _drain_arbiter(dut, 4, [0, 1, 1, 1], cycles=6) == [1] * 6


def test_arbiter_round_robin_cycles_through_ports():
    dut = st.Arbiter(unsigned(8), 4, arbitration="roundRobin")
    chosen = _drain_arbiter(dut, 4, [1, 1, 1, 1], cycles=9)
    assert chosen == [0, 1, 2, 3, 0, 1, 2, 3, 0]


def test_arbiter_round_robin_skips_idle_ports():
    dut = st.Arbiter(unsigned(8), 4, arbitration="roundRobin")
    chosen = _drain_arbiter(dut, 4, [1, 0, 1, 0], cycles=6)
    assert chosen == [0, 2, 0, 2, 0, 2]


def test_arbiter_sequential_order_is_strict():
    dut = st.Arbiter(unsigned(8), 3, arbitration="sequentialOrder")
    # port 1 is silent: sequentialOrder waits for it rather than skipping.
    chosen = _drain_arbiter(dut, 3, [1, 0, 1], cycles=6)
    assert chosen == [0]


def test_arbiter_assume_oh_input_has_no_lock_register():
    dut = st.Arbiter(unsigned(8), 2, arbitration="assumeOhInput")
    assert dut.lock_flag_enable is False
    assert dut.mask_lock_flag_enable is False

    async def tb(ctx):
        outs = (dut.o.valid, dut.o.payload, dut.inputs[0].ready,
                dut.inputs[1].ready)
        v, p, r0, r1 = await cycle(ctx, [
            (dut.o.ready, 1),
            (dut.inputs[0].valid, 0), (dut.inputs[0].payload, 0xA0),
            (dut.inputs[1].valid, 1), (dut.inputs[1].payload, 0xB1)], outs)
        assert (v, p, r0, r1) == (1, 0xB1, 0, 1)
        v, p, r0, r1 = await cycle(ctx, [
            (dut.inputs[0].valid, 1), (dut.inputs[1].valid, 0)], outs)
        assert (v, p, r0, r1) == (1, 0xA0, 1, 0)

    run(dut, tb)


def test_arbiter_transaction_lock_holds_the_choice_while_stalled():
    dut = st.Arbiter(unsigned(8), 2, arbitration="lowerFirst",
                     lock="transactionLock")

    async def tb(ctx):
        outs = (dut.o.valid, dut.o.payload, dut.chosen)
        ins = [(dut.o.ready, 0),
               (dut.inputs[0].valid, 1), (dut.inputs[0].payload, 0xA0),
               (dut.inputs[1].valid, 0), (dut.inputs[1].payload, 0xB1)]
        v, p, c = await cycle(ctx, ins, outs)
        assert (v, p, c) == (1, 0xA0, 0)
        # port 0 drops out mid-transaction, port 1 shows up: the lock keeps
        # the arbiter pointed at port 0.
        v, p, c = await cycle(ctx, [(dut.inputs[0].valid, 0),
                                    (dut.inputs[1].valid, 1)], outs)
        assert (p, c) == (0xA0, 0)
        assert v == 0, "input 0 went away, so the locked output is not valid"

    run(dut, tb)


def test_arbiter_no_lock_may_switch_every_cycle():
    dut = st.Arbiter(unsigned(8), 2, arbitration="lowerFirst", lock="noLock")
    assert dut.lock_flag_enable is False

    async def tb(ctx):
        outs = (dut.o.valid, dut.o.payload)
        v, p = await cycle(ctx, [(dut.o.ready, 0),
                                 (dut.inputs[0].valid, 1), (dut.inputs[0].payload, 0xA0),
                                 (dut.inputs[1].valid, 1), (dut.inputs[1].payload, 0xB1)],
                           outs)
        assert (v, p) == (1, 0xA0)
        v, p = await cycle(ctx, [(dut.inputs[0].valid, 0)], outs)
        assert (v, p) == (1, 0xB1)

    run(dut, tb)


def test_arbiter_fragment_lock_holds_until_last():
    layout = fragment_layout(unsigned(8))
    dut = st.Arbiter(layout, 2, arbitration="lowerFirst", lock="fragmentLock")
    chosen = []

    async def tb(ctx):
        outs = (dut.o.valid, dut.o.payload.fragment, dut.o.payload.last,
                dut.chosen)
        # port 0 sends a two-beat burst, port 1 is valid the whole time.
        beats = [(0x10, 0), (0x11, 1)]
        ins = [(dut.o.ready, 1),
               (dut.inputs[1].valid, 1),
               (dut.inputs[1].payload.fragment, 0xB0),
               (dut.inputs[1].payload.last, 1)]
        for frag, last in beats:
            ins.extend([(dut.inputs[0].valid, 1),
                        (dut.inputs[0].payload.fragment, frag),
                        (dut.inputs[0].payload.last, last)])
            v, f, l, c = await cycle(ctx, ins, outs)
            ins = []
            chosen.append((v, f, l, c))
        ins = [(dut.inputs[0].valid, 0)]
        v, f, l, c = await cycle(ctx, ins, outs)
        chosen.append((v, f, l, c))

    run(dut, tb)
    assert chosen[0][:3] == (1, 0x10, 0)
    assert chosen[1][:3] == (1, 0x11, 1)
    assert [c[3] for c in chosen[:2]] == [0, 0], "locked to port 0 for the burst"
    assert chosen[2][:2] == (1, 0xB0), "released after last"


# ---------------------------------------------------------------------------
# StreamFifo -- cycle-accurate reference model
# ---------------------------------------------------------------------------


class SpinalFifoModel:
    """Cycle-accurate model of ``StreamFifo(T, depth, latency = 2)``.

    Transcribed from ``spinal/lib/Stream.scala`` (``class StreamFifo``,
    ``depth > 1`` branch, ``withAsyncRead = false``, ``withBypass = false``,
    ``allowExtraMsb = true``, ``forFMax = false``) rather than from the
    Amaranth port, so the two agree only if the port is right.
    """

    def __init__(self, depth):
        assert depth > 1
        self.depth = depth
        self.addr_w = ceil_log2(depth)
        self.with_extra_msb = is_pow2(depth)
        self.ptr_w = self.addr_w + (1 if self.with_extra_msb else 0)
        self.ptr_mask = (1 << self.ptr_w) - 1
        self.occ_w = ceil_log2(depth + 1)
        self.occ_mask = (1 << self.occ_w) - 1

        self.push_ptr = 0
        self.pop_ptr = 0
        self.pop_reg = 0        # popOnIo = RegNextWhen(pop, readArbitration.fire)
        self.went_up = 0
        self.counter = 0        # the !withExtraMsb occupancy counter
        self.ram = [0] * depth
        self.ra_valid = 0       # readArbitration (an m2sPipe) valid register
        self.rsp = 0            # ram.readSyncPort response register

    # -- combinational view -------------------------------------------------
    def comb(self, push_valid, push_payload, pop_ready, flush=0):
        if self.with_extra_msb:
            full = ((self.push_ptr ^ self.pop_reg ^ self.depth) & self.ptr_mask) == 0
            empty = self.push_ptr == self.pop_ptr
        else:
            full = self.push_ptr == self.pop_reg and self.went_up
            empty = self.push_ptr == self.pop_ptr and not self.went_up

        push_ready = 0 if full else 1
        push_fire = push_valid and push_ready

        ag_valid = 0 if empty else 1
        ag_payload = self.pop_ptr & ((1 << self.addr_w) - 1)
        # io.pop << readArbitration.translateWith(readPort.rsp)
        pop_valid = self.ra_valid
        pop_payload = self.rsp
        pop_fire = pop_valid and pop_ready
        # m2sPipe with collapsBubble: ag.ready = ra.ready | !ra.valid
        ag_ready = 1 if (pop_ready or not self.ra_valid) else 0
        ag_fire = ag_valid and ag_ready

        if self.with_extra_msb:
            occupancy = (self.push_ptr - self.pop_reg) & self.occ_mask
        else:
            occupancy = self.counter
        return dict(push_ready=push_ready, push_fire=push_fire,
                    pop_valid=pop_valid, pop_payload=pop_payload,
                    pop_fire=pop_fire, ag_valid=ag_valid, ag_ready=ag_ready,
                    ag_fire=ag_fire, ag_payload=ag_payload,
                    occupancy=occupancy,
                    availability=(self.depth - occupancy) & self.occ_mask)

    # -- register update ----------------------------------------------------
    def step(self, push_valid, push_payload, pop_ready, flush=0):
        c = self.comb(push_valid, push_payload, pop_ready, flush)
        do_push, do_pop = c["push_fire"], c["ag_fire"]

        if c["push_fire"]:
            self.ram[self.push_ptr & ((1 << self.addr_w) - 1)] = push_payload
        if do_push:
            nxt = (self.push_ptr + 1) & self.ptr_mask
            if not is_pow2(self.depth) and self.push_ptr == self.depth - 1:
                nxt = 0
            self.push_ptr = nxt
        if do_pop:
            nxt = (self.pop_ptr + 1) & self.ptr_mask
            if not is_pow2(self.depth) and self.pop_ptr == self.depth - 1:
                nxt = 0
            new_pop = nxt
        else:
            new_pop = self.pop_ptr

        if do_push != do_pop:
            self.went_up = 1 if do_push else 0

        if not self.with_extra_msb:
            self.counter = (self.counter + c["push_fire"] - c["pop_fire"]) & self.occ_mask

        # readArbitration = addressGen.m2sPipe(flush)
        if c["ag_ready"]:
            self.ra_valid = c["ag_valid"]
        # ram.readSyncPort driven by addressGen.toFlowFire
        if c["ag_fire"]:
            self.rsp = self.ram[c["ag_payload"]]
        # popReg = RegNextWhen(ptr.pop, readArbitration.fire)
        if c["pop_fire"]:
            self.pop_reg = self.pop_ptr

        self.pop_ptr = new_pop

        if flush:
            self.push_ptr = 0
            self.pop_ptr = 0
            self.pop_reg = 0
            self.went_up = 0
            self.counter = 0
            self.ra_valid = 0
        return c


@pytest.mark.parametrize("depth", [2, 4, 8, 3, 5, 16])
def test_fifo_matches_the_spinal_model_cycle_by_cycle(depth):
    dut = st.Fifo(unsigned(8), depth)
    model = SpinalFifoModel(depth)
    rng = random.Random(0xC0FFEE + depth)
    transfers = 0

    async def tb(ctx):
        nonlocal transfers
        outs = (dut.push.ready, dut.pop.valid, dut.pop.payload,
                dut.occupancy, dut.availability)
        payload = 0
        for n in range(600):
            push_valid = 1 if rng.random() < 0.6 else 0
            pop_ready = 1 if rng.random() < 0.5 else 0
            payload = (payload + 1) & 0xFF
            expect = model.comb(push_valid, payload, pop_ready)
            got = await cycle(ctx, [(dut.push.valid, push_valid),
                                    (dut.push.payload, payload),
                                    (dut.pop.ready, pop_ready),
                                    (dut.flush, 0)], outs)
            assert got[0] == expect["push_ready"], f"push.ready @{n}"
            assert got[1] == expect["pop_valid"], f"pop.valid @{n}"
            if expect["pop_valid"]:
                assert got[2] == expect["pop_payload"], f"pop.payload @{n}"
            assert got[3] == expect["occupancy"], f"occupancy @{n}"
            assert got[4] == expect["availability"], f"availability @{n}"
            if expect["pop_fire"]:
                transfers += 1
            model.step(push_valid, payload, pop_ready)

    run(dut, tb)
    assert transfers > 100


@pytest.mark.parametrize("depth", [2, 4, 8, 3, 5])
def test_fifo_is_fifo_and_holds_exactly_depth_entries(depth):
    """Capacity is ``depth``: ``full`` is compared against ``popOnIo``, so the
    output register does *not* buy an extra slot."""
    dut = st.Fifo(unsigned(8), depth)
    pushed, popped, max_occ = [], [], 0

    async def tb(ctx):
        nonlocal max_occ
        outs = (dut.push.ready, dut.pop.valid, dut.pop.payload, dut.occupancy)
        # fill with the sink blocked
        value = 1
        for _ in range(depth + 6):
            r, v, p, occ = await cycle(ctx, [(dut.push.valid, 1),
                                             (dut.push.payload, value),
                                             (dut.pop.ready, 0)], outs)
            max_occ = max(max_occ, occ)
            if r:
                pushed.append(value)
                value += 1
        # drain
        for _ in range(3 * depth + 8):
            r, v, p, occ = await cycle(ctx, [(dut.push.valid, 0),
                                             (dut.pop.ready, 1)], outs)
            max_occ = max(max_occ, occ)
            if v:
                popped.append(p)

    run(dut, tb)
    assert len(pushed) == depth
    assert popped == pushed
    assert max_occ == depth


def test_fifo_flush_empties_it_immediately():
    dut = st.Fifo(unsigned(8), 8)

    async def tb(ctx):
        outs = (dut.pop.valid, dut.occupancy, dut.availability)
        for k in range(4):
            await cycle(ctx, [(dut.push.valid, 1), (dut.push.payload, k),
                              (dut.pop.ready, 0), (dut.flush, 0)], outs)
        v, occ, avail = await cycle(ctx, [(dut.push.valid, 0)], outs)
        assert occ == 4 and avail == 4 and v == 1
        await cycle(ctx, [(dut.flush, 1)], outs)
        v, occ, avail = await cycle(ctx, [(dut.flush, 0)], outs)
        assert (v, occ, avail) == (0, 0, 8)

    run(dut, tb)


def test_fifo_latency_two_is_the_default():
    dut = st.Fifo(unsigned(8), 4)

    async def tb(ctx):
        outs = (dut.pop.valid, dut.pop.payload)
        v, _ = await cycle(ctx, [(dut.push.valid, 1), (dut.push.payload, 0x77),
                                 (dut.pop.ready, 1)], outs)
        assert v == 0
        v, _ = await cycle(ctx, [(dut.push.valid, 0)], outs)
        assert v == 0, "one cycle for the pointer, one for the RAM read"
        v, p = await cycle(ctx, [], outs)
        assert (v, p) == (1, 0x77)

    run(dut, tb)


@pytest.mark.parametrize("latency,first_valid_cycle", [(0, 0), (1, 1), (2, 2)])
def test_fifo_latency_variants(latency, first_valid_cycle):
    dut = st.Fifo(unsigned(8), 4, latency=latency)
    seen = []

    async def tb(ctx):
        outs = (dut.pop.valid, dut.pop.payload)
        for n in range(4):
            ins = [(dut.pop.ready, 1), (dut.flush, 0)]
            if n == 0:
                ins.extend([(dut.push.valid, 1), (dut.push.payload, 0x99)])
            else:
                ins.append((dut.push.valid, 0))
            seen.append(await cycle(ctx, ins, outs))

    run(dut, tb)
    valid_at = [n for n, (v, _) in enumerate(seen) if v]
    assert valid_at and valid_at[0] == first_valid_cycle
    assert seen[valid_at[0]][1] == 0x99


def test_fifo_depth_one_is_an_m2s_pipe():
    dut = st.Fifo(unsigned(8), 1)

    async def tb(ctx):
        outs = (dut.push.ready, dut.pop.valid, dut.pop.payload, dut.occupancy,
                dut.availability)
        r, v, p, o, a = await cycle(ctx, [(dut.push.valid, 1),
                                          (dut.push.payload, 0x5A),
                                          (dut.pop.ready, 0), (dut.flush, 0)], outs)
        assert (r, v, o, a) == (1, 0, 0, 1)
        r, v, p, o, a = await cycle(ctx, [(dut.push.valid, 0)], outs)
        assert (r, v, p, o, a) == (0, 1, 0x5A, 1, 0)

    run(dut, tb)


def test_fifo_carries_a_struct_payload():
    layout = data.StructLayout({"addr": unsigned(12), "data": unsigned(16)})
    dut = st.Fifo(layout, 4)

    async def tb(ctx):
        outs = (dut.pop.valid, dut.pop.payload.addr, dut.pop.payload.data)
        await cycle(ctx, [(dut.push.valid, 1), (dut.push.payload.addr, 0xABC),
                          (dut.push.payload.data, 0x1234), (dut.pop.ready, 1),
                          (dut.flush, 0)], outs)
        await cycle(ctx, [(dut.push.valid, 0)], outs)
        v, a, d = await cycle(ctx, [], outs)
        assert (v, a, d) == (1, 0xABC, 0x1234)

    run(dut, tb)


# ---------------------------------------------------------------------------
# StreamFifoCC
# ---------------------------------------------------------------------------


def test_fifo_cc_moves_data_between_clock_domains():
    dut = st.FifoCC(unsigned(8), 8, push_domain="push", pop_domain="pop",
                    with_pop_buffered_reset=False)
    popped = []

    async def push_tb(ctx):
        for value in range(1, 17):
            ctx.set(dut.push.valid, 1)
            ctx.set(dut.push.payload, value)
            while True:
                _, _, ready = await ctx.tick("push").sample(dut.push.ready)
                if ready:
                    break
            ctx.set(dut.push.valid, 0)

    async def pop_tb(ctx):
        ctx.set(dut.pop.ready, 1)
        for _ in range(400):
            _, _, valid, payload = await ctx.tick("pop").sample(
                dut.pop.valid, dut.pop.payload)
            if valid:
                popped.append(payload)

    sim = Simulator(dut)
    sim.add_clock(PERIOD, domain="push")
    sim.add_clock(PERIOD * 0.37, domain="pop")
    sim.add_testbench(push_tb)
    sim.add_testbench(pop_tb)
    sim.run()

    assert popped == list(range(1, 17))


# ---------------------------------------------------------------------------
# functional helpers (the `m`-taking wrappers)
# ---------------------------------------------------------------------------


def test_throw_when_drops_and_acknowledges():
    m = Module()
    src = st.Stream(unsigned(8))
    cond = Signal()
    out = st.throw_when(m, src, cond)

    async def tb(ctx):
        outs = (src.ready, out.valid, out.payload)
        r, v, p = await cycle(ctx, [(src.valid, 1), (src.payload, 0x21), (cond, 0),
                                    (out.ready, 0)], outs)
        assert (r, v, p) == (0, 1, 0x21)
        r, v, p = await cycle(ctx, [(cond, 1)], outs)
        assert (r, v) == (1, 0), "thrown beats are acknowledged, not stalled"

    run(m, tb)


def test_halt_and_take_when():
    m = Module()
    src = st.Stream(unsigned(8))
    gate = Signal()
    halted = st.halt_when(m, src, gate)

    async def tb(ctx):
        outs = (src.ready, halted.valid)
        r, v = await cycle(ctx, [(src.valid, 1), (gate, 1), (halted.ready, 1)], outs)
        assert (r, v) == (0, 0)
        r, v = await cycle(ctx, [(gate, 0)], outs)
        assert (r, v) == (1, 1)

    run(m, tb)

    m2 = Module()
    src2 = st.Stream(unsigned(8))
    keep = Signal()
    taken = st.take_when(m2, src2, keep)

    async def tb2(ctx):
        outs = (src2.ready, taken.valid)
        r, v = await cycle(ctx, [(src2.valid, 1), (keep, 0), (taken.ready, 0)], outs)
        assert (r, v) == (1, 0), "dropped when !cond"
        r, v = await cycle(ctx, [(keep, 1)], outs)
        assert (r, v) == (0, 1)

    run(m2, tb2)


def test_translate_with_and_map_keep_arbitration():
    m = Module()
    src = st.Stream(unsigned(8))
    doubled = st.map_payload(m, src, lambda p: (p << 1)[:9])

    async def tb(ctx):
        outs = (src.ready, doubled.valid, doubled.payload)
        r, v, p = await cycle(ctx, [(src.valid, 1), (src.payload, 0x21),
                                    (doubled.ready, 1)], outs)
        assert (r, v, p) == (1, 1, 0x42)

    run(m, tb)


def test_queue_helper_wires_a_fifo():
    m = Module()
    src = st.Stream(unsigned(8))
    popped = st.queue(m, src, 4)

    async def tb(ctx):
        outs = (src.ready, popped.valid, popped.payload)
        await cycle(ctx, [(src.valid, 1), (src.payload, 0x11), (popped.ready, 1)], outs)
        await cycle(ctx, [(src.valid, 0)], outs)
        r, v, p = await cycle(ctx, [], outs)
        assert (v, p) == (1, 0x11)

    run(m, tb)


def test_fork2_helper_returns_a_pair():
    m = Module()
    src = st.Stream(unsigned(8))
    a, b = st.fork2(m, src, synchronous=True)

    async def tb(ctx):
        outs = (src.ready, a.valid, b.valid, a.payload, b.payload)
        r, va, vb, pa, pb = await cycle(ctx, [(src.valid, 1), (src.payload, 0x7E),
                                              (a.ready, 1), (b.ready, 1)], outs)
        assert (r, va, vb, pa, pb) == (1, 1, 1, 0x7E, 0x7E)

    run(m, tb)


def test_join_helper_builds_the_tuple_layout():
    m = Module()
    a = st.Stream(unsigned(8))
    b = st.Stream(unsigned(4))
    joined = st.join(m, a, b)
    # Spinal's TupleBundleN field names; Amaranth reserves leading-underscore
    # attribute access on a data.View, so they are reached by indexing.
    assert set(joined.payload.shape().members) == {"_1", "_2"}

    async def tb(ctx):
        outs = (joined.valid, joined.payload["_1"], joined.payload["_2"])
        v, x, y = await cycle(ctx, [(a.valid, 1), (a.payload, 0xDE),
                                    (b.valid, 1), (b.payload, 0x7),
                                    (joined.ready, 1)], outs)
        assert (v, x, y) == (1, 0xDE, 0x7)

    run(m, tb)


def test_arbiter_on_helper_returns_a_comb_stage():
    m = Module()
    a = st.Stream(unsigned(8))
    b = st.Stream(unsigned(8))
    out = st.arbiter_on(m, [a, b], arbitration="assumeOhInput")

    async def tb(ctx):
        outs = (out.valid, out.payload, a.ready, b.ready)
        v, p, ra, rb = await cycle(ctx, [(a.valid, 0), (a.payload, 1),
                                         (b.valid, 1), (b.payload, 2),
                                         (out.ready, 1)], outs)
        assert (v, p, ra, rb) == (1, 2, 0, 1)

    run(m, tb)


# ---------------------------------------------------------------------------
# Counter / OHMasking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state_count", [2, 3, 4, 5, 8, 16])
def test_counter_wraps_at_state_count(state_count):
    m = Module()
    counter = st.Counter(m, state_count)
    go = Signal()
    with m.If(go):
        counter.increment(m)
    values = []

    async def tb(ctx):
        for _ in range(3 * state_count + 2):
            v, ovf = await cycle(ctx, [(go, 1)],
                                 (counter.value, counter.will_overflow_if_inc))
            values.append((v, ovf))

    run(m, tb)
    seq = [v for v, _ in values]
    assert seq == [n % state_count for n in range(len(seq))]
    assert all(ovf == (v == state_count - 1) for v, ovf in values)


def test_counter_clear_beats_increment():
    m = Module()
    counter = st.Counter(m, 8)
    go, rst = Signal(), Signal()
    with m.If(go):
        counter.increment(m)
    with m.If(rst):
        counter.clear(m)
    seen = []

    async def tb(ctx):
        for n in range(6):
            v, = await cycle(ctx, [(go, 1), (rst, 1 if n == 2 else 0)],
                             (counter.value,))
            seen.append(v)

    run(m, tb)
    assert seen == [0, 1, 2, 0, 1, 2]


def test_oh_masking_and_mux_helpers():
    m = Module()
    req = Signal(4)
    prio = Signal(4)
    first = Signal(4)
    rr = Signal(4)
    idx = Signal(2)
    m.d.comb += [
        first.eq(st.oh_masking_first(req)),
        rr.eq(st.oh_masking_round_robin(req, prio)),
        idx.eq(st.oh_to_uint(req)),
    ]

    async def tb(ctx):
        f, r, i = await cycle(ctx, [(req, 0b1010), (prio, 0b0001)],
                              (first, rr, idx))
        assert f == 0b0010
        assert r == 0b0010  # priority starts at bit 0 -> the first request up
        f, r, i = await cycle(ctx, [(req, 0b1010), (prio, 0b0100)], (first, rr, idx))
        assert f == 0b0010
        assert r == 0b1000
        f, r, i = await cycle(ctx, [(req, 0b0100), (prio, 0b0001)], (first, rr, idx))
        assert (f, r, i) == (0b0100, 0b0100, 2)

    run(m, tb)


# ---------------------------------------------------------------------------
# unreset registers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pipe_cls", [st.M2SPipe, st.S2MPipe, st.HalfPipe])
def test_payload_registers_are_unreset(pipe_cls):
    """Spinal's ``rData`` is a plain ``Reg``, not a ``RegInit``: it keeps its
    value across a reset while ``rValid`` clears.

    The plan-0.4 lockstep diff caught the same mistake in ``StreamWhile``'s
    ``state`` (a second seed mismatched at cycle 0 on ``output_payload``), so
    every plain Spinal ``Reg`` ported here is ``reset_less=True``.
    """
    m = Module()
    cd = ClockDomain("sync")
    m.domains += cd
    m.submodules.pipe = dut = pipe_cls(unsigned(8))

    async def tb(ctx):
        outs = (dut.o.valid, dut.o.payload)
        # get 0xA5 into rData, then stall so it stays there
        await cycle(ctx, [(cd.rst, 0), (dut.i.valid, 1),
                          (dut.i.payload, 0xA5), (dut.o.ready, 0)], outs)
        await cycle(ctx, [(dut.i.valid, 0)], outs)
        v, p = await cycle(ctx, [], outs)
        assert (v, p) == (1, 0xA5)

        await cycle(ctx, [(cd.rst, 1)], outs)
        v, p = await cycle(ctx, [(cd.rst, 0)], outs)
        assert v == 0, "the valid register does reset"
        assert p == 0xA5, "the payload register does not"

    sim = Simulator(m)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()
