"""pysim tests for ``avoodoo.framebuffer.plane_reader`` and ``plane_direct_reader``.

Ports the intent of ``ext/SpinalVoodoo/test/FramebufferPlaneReaderTest.scala``
(five directed SpinalSim tests, same addresses and data words) and
``FramebufferPlaneReaderFormalTest.scala`` (the four ``GenerationFlags.formal``
assert groups the RTL port dropped, here re-derived from the ``cacheDebug``
port and checked every cycle by :func:`invariant_monitor`), then adds what
neither has: a randomised run against a Python model -- a ``MemBus`` slave
that answers commands in order (:class:`Responder`) and a
:class:`ConsumeModel` that predicts, per read request, whether the reader
serves it from the prefetched lanes or as a direct miss.  Data is checked
against the memory image, the miss count against the model, and the fifteen
diagnostic counters against what the testbench itself observed (stall
accounting, request-step classification, burst beats).

Why this is the real coverage rather than the Verilator diff: the generic
diff driver randomises ``io_mem_rsp`` independently of the commands and holds
a beat while stalled, so a ``source = 1`` beat with no direct miss outstanding
blocks the reader forever after a few dozen cycles.  See
``tests/diff/test_diff_plane_reader.py`` for the numbers.

Sizes follow the Scala tests' ``mkConfig``: 12-bit addresses (a 4 KiB
memory), 6-bit burst length, ``maxFbDims = (16, 16)`` (spans of at most 16
lanes, ``spanLaneCountWidth = 5``, a 17-lane ``laneFifo``).
``mkLengthFixerConfig`` widens the framebuffer to 128 pixels so
``needBurstSplitter`` becomes true and the ``AlignedSplitter`` +
``ContextRemover`` chain is elaborated.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import replace
from functools import partial

import pytest
from amaranth import ClockDomain, Elaboratable, Module
from amaranth.sim import Simulator

from avoodoo.config import Config, TraceConfig
from avoodoo.core import membus_params as mp
from avoodoo.framebuffer.plane_direct_reader import FramebufferPlaneDirectReader
from avoodoo.framebuffer.plane_reader import (ISSUE_QUEUE_DEPTH, FramebufferPlaneReader,
                                              SpanCmd)
from avoodoo.lib.membus import CMD_READ, RSP_SUCCESS, cmd_layout, rsp_layout

CLK = 1e-6

# The parameter tests build a Component only for its derived numbers.
pytestmark = pytest.mark.filterwarnings("ignore:.*created but never used:")


def mk_config(**overrides) -> Config:
    """``FramebufferPlaneReaderTest.mkConfig``."""
    base = dict(
        address_width=12,
        mem_burst_length_width=6,
        fb_write_buffer_line_words=4,
        fb_write_buffer_count=2,
        use_fb_write_buffer=True,
        use_tex_fill_cache=False,
        max_fb_dims=(16, 16),
        trace=TraceConfig(enabled=True),
    )
    base.update(overrides)
    return replace(Config.voodoo1(), **base)


def mk_length_fixer_config() -> Config:
    """``FramebufferPlaneReaderTest.mkLengthFixerConfig`` -- needs the splitter."""
    return mk_config(address_width=20, max_fb_dims=(128, 16))


# ==========================================================================
# parameter maths
# ==========================================================================


def test_small_config_parameters():
    c = mk_config()
    dut = FramebufferPlaneReader(c)
    assert dut.span_lane_count_width == 5          # log2Up(17)
    assert dut.line_buffer_lanes == 17             # max(17, 16)
    assert dut.need_burst_splitter is False        # ((16+1)/2)*4 = 32 <= 64
    assert dut.mem_params == mp.plane_reader_internal_params(c)
    assert dut.mem_params.access.length_width == 6
    assert SpanCmd(12, 5).size == 4 * 12 + 2 * 5


def test_voodoo1_parameters():
    c = Config.voodoo1()
    dut = FramebufferPlaneReader(c)
    assert dut.span_lane_count_width == 10         # log2Up(801)
    assert dut.line_buffer_lanes == 801
    assert dut.need_burst_splitter is False
    assert SpanCmd(26, 10).size == 124
    direct = FramebufferPlaneDirectReader(c)
    assert direct.mem_params == dut.mem_params


def test_length_fixer_config_needs_splitter():
    c = mk_length_fixer_config()
    dut = FramebufferPlaneReader(c)
    assert dut.need_burst_splitter is True         # ((128+1)/2)*4 = 256 > 64
    assert dut.internal_params.access.length_width == 8   # max(6+1, log2Up(256))
    assert dut.mem_params.access.length_width == 6        # splitter output
    assert dut.mem_params.access.source_width == 0        # ContextRemover output
    assert dut.mem_params.access.context_width == 0


def test_distributed_ram_attrs_follow_the_spinal_rule():
    """Only the three 124-bit SpanCmd FIFOs get ``syn_ramstyle`` (voodoo1)."""
    from amaranth.back import verilog

    text = verilog.convert(FramebufferPlaneReader(Config.voodoo1()), name="R")
    lines = text.splitlines()
    tagged = []
    for i, line in enumerate(lines):
        if "syn_ramstyle" in line:
            tagged.append(lines[i + 1].strip())
    assert sorted(tagged) == sorted([
        "reg [123:0] ram [63:0];",      # spanIssueQueue
        "reg [123:0] ram [3:0];",       # issuedSpanQueue
        "reg [123:0] ram [63:0];",      # consumeQueue
    ])
    assert "reg [15:0] ram [800:0];" in text     # laneFifo, untagged
    direct = verilog.convert(FramebufferPlaneDirectReader(Config.voodoo1()), name="D")
    assert "syn_ramstyle" not in direct


def test_every_register_resets():
    """Every register in ``FramebufferPlaneReader.scala`` is ``init(0)`` / ``RegInit``.

    So, unlike SpanWalker or TriangleSetup, *no* register here may be
    ``reset_less`` (plan §6): the diff driver resets before every seed and a
    reset-less counter would keep the previous seed's value on one side only.
    The 22 register names are the module docstring's register map.
    """
    from amaranth.hdl._ir import Fragment
    from amaranth.hdl._xfrm import LHSMaskCollector

    frag = Fragment.get(FramebufferPlaneReader(Config.voodoo1()), None)
    collector = LHSMaskCollector()
    collector.visit_stmt(frag.statements["sync"])
    by_name = {chunk[0].name: chunk[0] for chunk in collector.chunks()}
    expected = {
        "fillHits", "fillMisses", "fillBurstCount", "fillBurstBeats",
        "fillStallCycles", "reqCount", "reqForwardStepCount",
        "reqBackwardStepCount", "reqSameWordCount", "reqSameLineCount",
        "reqOtherCount", "singleBeatBurstCount", "multiBeatBurstCount",
        "maxOccupancy", "prevReqValid", "prevReqAddr", "consumeActive",
        "consumeExpectedAddr", "consumeRemainingLanes", "activeFillWordIndex",
        "pendingLaneValid", "pendingLaneData",
    }
    missing = expected - set(by_name)
    assert not missing, f"not sync registers: {sorted(missing)}"
    reset_less = sorted(n for n, sig in by_name.items() if sig.reset_less)
    assert reset_less == [], f"unexpected reset_less registers: {reset_less}"

    direct = Fragment.get(FramebufferPlaneDirectReader(Config.voodoo1()), None)
    assert "sync" not in direct.statements or not list(direct.statements["sync"]), \
        "FramebufferPlaneDirectReader has no registers of its own"


# ==========================================================================
# simulation plumbing
# ==========================================================================


class _Clocked(Elaboratable):
    def __init__(self, dut):
        self.dut = dut

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.submodules.dut = self.dut
        return m


def run(dut, *testbenches, processes=(), deadline=20000):
    sim = Simulator(_Clocked(dut))
    sim.add_clock(CLK)
    for tb in testbenches:
        sim.add_testbench(tb)
    for pr in processes:
        sim.add_testbench(pr, background=True)
    sim.run_until(deadline * CLK)


class Memory16:
    """A little-endian byte image read as 32-bit words and 16-bit lanes."""

    def __init__(self, size: int, rng: random.Random | None = None):
        self.mem = bytearray(size)
        if rng is not None:
            self.mem[:] = rng.randbytes(size)

    def word(self, addr: int) -> int:
        a = addr & ~3
        return int.from_bytes(self.mem[a:a + 4], "little")

    def lane(self, addr: int) -> int:
        a = addr & ~1
        return int.from_bytes(self.mem[a:a + 2], "little")

    def set_word(self, addr: int, value: int) -> None:
        self.mem[addr & ~3:(addr & ~3) + 4] = value.to_bytes(4, "little")


class Responder:
    """In-order ``MemBus`` read slave: every command gets its beats, in order.

    ``source`` (when the bus has one) is echoed on every beat, ``last`` on the
    final beat, ``opcode = SUCCESS``.  ``cmd_ready_density`` /
    ``rsp_valid_density`` add random back-pressure and latency; a beat, once
    offered, is held until accepted (the Stream contract).
    """

    def __init__(self, mem: Memory16, p, rng: random.Random, *,
                 cmd_ready_density=1.0, rsp_valid_density=1.0):
        self.mem = mem
        self.p = p
        self.rng = rng
        self.cmd_ready_density = cmd_ready_density
        self.rsp_valid_density = rsp_valid_density
        self.cmd_log: list[dict] = []
        self.rsp_beats = 0
        self.cmd_members = list(cmd_layout(p)["fragment"].shape.members)
        self.rsp_layout = rsp_layout(p)

    def _rsp(self, **kw):
        has = self.rsp_layout["fragment"].shape.members
        return self.rsp_layout.const({
            "last": kw.pop("last"),
            "fragment": {k: v for k, v in kw.items() if k in has},
        })

    async def run(self, ctx, bus):
        pending: deque = deque()
        holding = None
        ctx.set(bus.cmd.ready, 0)
        ctx.set(bus.rsp.valid, 0)
        while True:
            rdy = 1 if self.rng.random() < self.cmd_ready_density else 0
            ctx.set(bus.cmd.ready, rdy)
            if holding is None and pending and self.rng.random() < self.rsp_valid_density:
                holding = pending.popleft()
                ctx.set(bus.rsp.payload, self._rsp(**holding))
                ctx.set(bus.rsp.valid, 1)
            _clk, _rst, cmd_valid, rsp_ready, *frag = await ctx.tick().sample(
                bus.cmd.valid, bus.rsp.ready,
                *[bus.cmd.payload.fragment[k] for k in self.cmd_members])
            if cmd_valid and rdy:
                beat = dict(zip(self.cmd_members, frag))
                self.cmd_log.append(beat)
                assert beat["opcode"] == CMD_READ
                base = beat["address"] & ~3
                beats = ((beat["address"] & 3) + beat.get("length", 0)) // 4 + 1
                for i in range(beats):
                    pending.append(dict(last=int(i == beats - 1), opcode=RSP_SUCCESS,
                                        data=self.mem.word(base + 4 * i),
                                        source=beat.get("source", 0), context=0))
            if holding is not None and rsp_ready:
                holding = None
                self.rsp_beats += 1
                ctx.set(bus.rsp.valid, 0)


class ConsumeModel:
    """The reader's consume tracker (Scala lines 161-203, 292-312) in Python.

    Predicts, for a read at ``addr`` issued once every relevant prefetch has
    been accepted, whether it is served from the cache (``"hit"``, possibly
    after skipping cached lanes) or as a direct miss (``"miss"``).  Timing
    does not enter: the skip only waits for lanes that are guaranteed to
    arrive, and a miss never changes the consume state.
    """

    def __init__(self):
        self.queue: deque = deque()     # (start, lane_count)
        self.active = False
        self.expected = 0
        self.remaining = 0
        self.skipped = 0

    def prefetch(self, start: int, end: int, addr_width: int) -> None:
        start &= ~1
        end &= ~1
        lane_count = (((end - start) & ((1 << addr_width) - 1)) >> 1) + 1
        self.queue.append((start, lane_count))

    def _current(self):
        if self.active:
            return self.expected, self.remaining
        if self.queue:
            return self.queue[0]
        return None

    def _advance(self, cur):
        if not self.active:
            self.queue.popleft()            # consumeQueue.pop.ready := onFirstLane
        addr, remaining = cur
        if remaining == 1:
            self.active, self.expected, self.remaining = False, 0, 0
        else:
            self.active, self.expected, self.remaining = True, addr + 2, remaining - 1

    def read(self, addr: int) -> str:
        while True:
            cur = self._current()
            if cur is None or addr < cur[0]:
                return "miss"
            if addr == cur[0]:
                self._advance(cur)
                return "hit"
            self.skipped += 1
            self._advance(cur)


def classify_step(curr: int, prev: int, addr_width: int) -> int:
    """``classifyReqStep``: 0 same word, 1 forward, 2 backward, 4 other."""
    mask = (1 << addr_width) - 1
    if (curr & ~3) == (prev & ~3):
        return 0
    if curr == ((prev + 2) & mask):
        return 1
    if prev == ((curr + 2) & mask):
        return 2
    return 4


# cacheDebug bit positions (Scala lines 349-371)
CD_READ_VALID, CD_READ_READY, CD_EXPECTED_VALID, CD_ADDR_MATCHES = 0, 1, 2, 3
CD_LANE_POP_VALID, CD_RSP_FIFO_PUSH_READY = 5, 6
CD_SKIP, CD_DM_CMD_VALID, CD_DM_CMD_SELECTED, CD_DM_READ_READY = 15, 16, 17, 18
CD_DM_OUTSTANDING, CD_DM_POP_VALID, CD_DM_MEM_RSP_VALID, CD_LANE_POP_READY = 19, 20, 21, 22


async def invariant_monitor(ctx, dut: FramebufferPlaneReader, stats: dict):
    """The four ``GenerationFlags.formal`` assert groups, per cycle, from ports.

    ``cachedReadFire`` is ``readReq.fire && !directMissReadReady`` (the two
    are mutually exclusive: one needs ``consumeAddrMatches``, the other
    ``directMissNeeded``), ``directMissRspFire`` is bits 21 & 20 & 6, and
    ``consumeAddrAhead`` is ``cacheDebugReadAddr > cacheDebugExpectedAddr``.
    """
    stats.setdefault("stall_cycles", 0)
    stats.setdefault("read_fires", 0)
    stats.setdefault("cached_fires", 0)
    stats.setdefault("skips", 0)
    stats.setdefault("dm_rsp_fires", 0)
    stats.setdefault("addresses", [])
    async for _clk, _rst, cd, ra, ea in ctx.tick().sample(
            dut.cache_debug, dut.cache_debug_read_addr, dut.cache_debug_expected_addr):
        bit = lambda n: (cd >> n) & 1  # noqa: E731
        read_valid, read_ready = bit(CD_READ_VALID), bit(CD_READ_READY)
        expected_valid, matches = bit(CD_EXPECTED_VALID), bit(CD_ADDR_MATCHES)
        ahead = int(ra > ea)
        direct_miss_needed = (not expected_valid) or (expected_valid and not matches
                                                     and not ahead)
        direct_miss_read_ready = bit(CD_DM_READ_READY)
        cached_read_fire = read_valid and read_ready and not direct_miss_read_ready
        skip = bit(CD_SKIP)
        dm_rsp_fire = bit(CD_DM_MEM_RSP_VALID) and bit(CD_DM_POP_VALID) and bit(
            CD_RSP_FIFO_PUSH_READY)
        if cached_read_fire:
            assert expected_valid, "cachedReadFire without consumeExpectedValid"
            assert matches, "cachedReadFire without consumeAddrMatches"
            assert not bit(CD_DM_OUTSTANDING), "cachedReadFire with a miss outstanding"
            stats["cached_fires"] += 1
        if skip:
            assert expected_valid and not matches and ahead, "skipCachedLane predicate"
            assert bit(CD_LANE_POP_VALID), "skipCachedLane with an empty laneFifo"
            stats["skips"] += 1
        if direct_miss_read_ready:
            assert direct_miss_needed, "directMissReadReady without directMissNeeded"
            assert bit(CD_DM_CMD_VALID) and bit(CD_DM_CMD_SELECTED)
            assert not bit(CD_LANE_POP_READY), "direct miss popped a cached lane"
        if dm_rsp_fire:
            assert bit(CD_DM_POP_VALID)
            stats["dm_rsp_fires"] += 1
        if read_valid and not read_ready:
            stats["stall_cycles"] += 1
        if read_valid and read_ready:
            stats["read_fires"] += 1
            stats["addresses"].append(ra)


# ==========================================================================
# directed tests (FramebufferPlaneReaderTest.scala)
# ==========================================================================


def _init(ctx, dut):
    ctx.set(dut.prefetch_req.valid, 0)
    ctx.set(dut.read_req.valid, 0)
    ctx.set(dut.read_rsp.ready, 1)
    ctx.set(dut.mem.cmd.ready, 0)
    ctx.set(dut.mem.rsp.valid, 0)


async def _prefetch(ctx, dut, start, end):
    ctx.set(dut.prefetch_req.payload.start_address, start)
    ctx.set(dut.prefetch_req.payload.end_address, end)
    ctx.set(dut.prefetch_req.valid, 1)
    await ctx.tick().until(dut.prefetch_req.ready)
    ctx.set(dut.prefetch_req.valid, 0)


async def _wait_cmd(ctx, dut):
    """``mem.cmd.ready := true; wait for cmd.valid`` -> (address, length)."""
    ctx.set(dut.mem.cmd.ready, 1)
    frag = dut.mem.cmd.payload.fragment
    address, length = await ctx.tick().sample(frag.address, frag.length).until(
        dut.mem.cmd.valid)
    return address, length


async def _send_rsp_beat(ctx, dut, data, last, source=0):
    """``sendRspBeat``: offer one beat, hold until accepted."""
    lay = rsp_layout(dut.mem_params)
    has = lay["fragment"].shape.members
    frag = {"opcode": RSP_SUCCESS, "data": data}
    if "source" in has:
        frag["source"] = source
    ctx.set(dut.mem.rsp.payload, lay.const({"last": int(last), "fragment": frag}))
    ctx.set(dut.mem.rsp.valid, 1)
    await ctx.tick().until(dut.mem.rsp.ready)
    ctx.set(dut.mem.rsp.valid, 0)


async def _read(ctx, dut, address, *, expect_ready_now=True):
    """``readReq.valid := true; waitSampling; assert ready`` -> readRsp.data."""
    ctx.set(dut.read_req.payload.address, address)
    ctx.set(dut.read_req.valid, 1)
    if expect_ready_now:
        assert ctx.get(dut.read_req.ready), f"address {address:#x} should be ready"
        await ctx.tick()
    else:
        await ctx.tick().until(dut.read_req.ready)
    ctx.set(dut.read_req.valid, 0)
    (data,) = await ctx.tick().sample(dut.read_rsp.payload.data).until(dut.read_rsp.valid)
    return data


def test_span_prefetch_issues_exact_length_burst():
    dut = FramebufferPlaneReader(mk_config())

    async def main(ctx):
        _init(ctx, dut)
        await ctx.tick()
        await _prefetch(ctx, dut, 0x000, 0x006)
        address, length = await _wait_cmd(ctx, dut)
        assert (address, length) == (0x000, 7)
        ctx.set(dut.read_rsp.ready, 0)
        await _send_rsp_beat(ctx, dut, 0x56781234, last=False)
        await _send_rsp_beat(ctx, dut, 0xDEF09ABC, last=True)
        await ctx.tick()
        # the first read is issued with readRsp.ready low, as in the Scala
        ctx.set(dut.read_req.payload.address, 0x000)
        ctx.set(dut.read_req.valid, 1)
        assert ctx.get(dut.read_req.ready)
        await ctx.tick()
        ctx.set(dut.read_req.valid, 0)
        ctx.set(dut.read_rsp.ready, 1)
        (data,) = await ctx.tick().sample(dut.read_rsp.payload.data).until(dut.read_rsp.valid)
        assert data == 0x1234
        assert await _read(ctx, dut, 0x002) == 0x5678
        assert await _read(ctx, dut, 0x004) == 0x9ABC
        assert await _read(ctx, dut, 0x006) == 0xDEF0
        await ctx.tick().repeat(4)
        assert ctx.get(dut.busy) == 0
        assert ctx.get(dut.fill_hits) == 4
        assert ctx.get(dut.fill_misses) == 1
        assert ctx.get(dut.fill_burst_count) == 1
        assert ctx.get(dut.fill_burst_beats) == 2
        assert ctx.get(dut.multi_beat_burst_count) == 1
        assert ctx.get(dut.single_beat_burst_count) == 0
        assert ctx.get(dut.req_count) == 4
        assert ctx.get(dut.req_same_word_count) == 2         # 0->2 and 4->6
        assert ctx.get(dut.req_forward_step_count) == 1      # 2->4
        assert ctx.get(dut.max_occupancy) == 4

    run(dut, main)


def test_odd_lane_span_only_enqueues_requested_halfwords():
    dut = FramebufferPlaneReader(mk_config())

    async def main(ctx):
        _init(ctx, dut)
        await ctx.tick()
        await _prefetch(ctx, dut, 0x002, 0x006)
        address, length = await _wait_cmd(ctx, dut)
        assert (address, length) == (0x000, 7)
        await _send_rsp_beat(ctx, dut, 0x56781234, last=False)
        await _send_rsp_beat(ctx, dut, 0xDEF09ABC, last=True)
        for addr, expected in [(0x002, 0x5678), (0x004, 0x9ABC), (0x006, 0xDEF0)]:
            assert await _read(ctx, dut, addr) == expected
            await ctx.tick()
        await ctx.tick().repeat(4)
        assert ctx.get(dut.busy) == 0
        assert ctx.get(dut.fill_hits) == 3
        assert ctx.get(dut.fill_burst_beats) == 2

    run(dut, main)


def test_length_fixer_splits_long_span_bursts():
    """The splitter path: one 0x40..0xa0 span becomes (0x40, 63) + (0x80, 35)."""
    dut = FramebufferPlaneReader(mk_length_fixer_config())
    seen = []

    async def main(ctx):
        _init(ctx, dut)
        await ctx.tick()
        await _prefetch(ctx, dut, 0x040, 0x0A0)
        ctx.set(dut.mem.cmd.ready, 1)
        frag = dut.mem.cmd.payload.fragment
        while len(seen) < 2:
            _clk, _rst, valid, address, length, last = await ctx.tick().sample(
                dut.mem.cmd.valid, frag.address, frag.length, dut.mem.cmd.payload.last)
            if valid:
                seen.append((address, length, last))

    run(dut, main)
    assert seen == [(0x040, 63, 1), (0x080, 35, 1)]


def test_prefetch_acceptance_is_backpressured_by_consume_tracking():
    dut = FramebufferPlaneReader(mk_config())

    async def main(ctx):
        _init(ctx, dut)
        await ctx.tick()
        ctx.set(dut.mem.cmd.ready, 1)
        ctx.set(dut.prefetch_req.valid, 1)
        for idx in range(ISSUE_QUEUE_DEPTH):
            ctx.set(dut.prefetch_req.payload.start_address, idx * 2)
            ctx.set(dut.prefetch_req.payload.end_address, idx * 2)
            assert ctx.get(dut.prefetch_req.ready), f"prefetch {idx} should be accepted"
            await ctx.tick()
        ctx.set(dut.prefetch_req.payload.start_address, 0x200)
        ctx.set(dut.prefetch_req.payload.end_address, 0x200)
        await ctx.tick()
        assert not ctx.get(dut.prefetch_req.ready), \
            "prefetch must stall when consume queue is full"
        ctx.set(dut.prefetch_req.valid, 0)
        assert ctx.get(dut.fill_misses) == ISSUE_QUEUE_DEPTH
        assert ctx.get(dut.cache_debug_occupancy) & 0x7F == ISSUE_QUEUE_DEPTH

    run(dut, main)


def test_direct_miss_does_not_consume_cached_lane():
    dut = FramebufferPlaneReader(mk_config())

    async def main(ctx):
        _init(ctx, dut)
        await ctx.tick()
        await _prefetch(ctx, dut, 0x000, 0x006)
        await _wait_cmd(ctx, dut)
        ctx.set(dut.mem.cmd.ready, 0)
        await _send_rsp_beat(ctx, dut, 0x56781234, last=False)
        await _send_rsp_beat(ctx, dut, 0xDEF09ABC, last=True)
        for addr, expected in [(0x000, 0x1234), (0x002, 0x5678)]:
            assert await _read(ctx, dut, addr) == expected
            await ctx.tick()

        # A backward request (0x000 again, expected lane is 0x004) is a
        # direct miss: it must issue a one-word command with source = 1 ...
        ctx.set(dut.read_req.payload.address, 0x000)
        ctx.set(dut.read_req.valid, 1)
        ctx.set(dut.mem.cmd.ready, 1)
        frag = dut.mem.cmd.payload.fragment
        valid, address, length, source = await ctx.tick().sample(
            dut.mem.cmd.valid, frag.address, frag.length, frag.source).until(
            dut.read_req.ready)
        assert valid, "backward direct miss should issue a memory command"
        assert (address, length, source) == (0x000, 3, 1)
        ctx.set(dut.read_req.valid, 0)
        ctx.set(dut.mem.cmd.ready, 0)
        # ... whose response comes back tagged source = 1 ...
        await _send_rsp_beat(ctx, dut, 0xCAFEBABE, last=True, source=1)
        (data,) = await ctx.tick().sample(dut.read_rsp.payload.data).until(dut.read_rsp.valid)
        assert data == 0xBABE
        await ctx.tick()
        # ... and the cached lane at 0x004 survives it.
        assert await _read(ctx, dut, 0x004) == 0x9ABC
        await ctx.tick().repeat(3)
        # 0x000 -> 0x002 and 0x002 -> 0x000 are both "same word" (that test
        # comes first in classifyReqStep), 0x000 -> 0x004 is "other".
        assert ctx.get(dut.req_same_word_count) == 2
        assert ctx.get(dut.req_backward_step_count) == 0
        assert ctx.get(dut.req_forward_step_count) == 0
        assert ctx.get(dut.req_other_count) == 1
        assert ctx.get(dut.req_count) == 4

    run(dut, main)


# ==========================================================================
# randomised model run
# ==========================================================================


@pytest.mark.parametrize("seed,cmd_ready,rsp_valid,read_rsp_ready", [
    (1, 1.0, 1.0, 1.0),
    (2, 0.6, 0.5, 0.7),
    (3, 0.3, 0.9, 0.4),
])
def test_random_prefetch_and_read_against_model(seed, cmd_ready, rsp_valid, read_rsp_ready):
    c = mk_config()
    aw = c.address_width
    dut = FramebufferPlaneReader(c)
    rng = random.Random(seed)
    mem = Memory16(1 << aw, rng)
    responder = Responder(mem, dut.mem_params, random.Random(seed + 100),
                          cmd_ready_density=cmd_ready, rsp_valid_density=rsp_valid)
    model = ConsumeModel()
    stats: dict = {}
    log: list[tuple[int, str]] = []          # (address, hit|miss) in request order
    prefetches: list[tuple[int, int]] = []
    rsp_data: list[int] = []

    async def consumer(ctx):
        """``readRsp.ready`` random; collect every response."""
        r = random.Random(seed + 200)
        async for _clk, _rst, valid, ready, data in ctx.tick().sample(
                dut.read_rsp.valid, dut.read_rsp.ready, dut.read_rsp.payload.data):
            if valid and ready:
                rsp_data.append(data)
            ctx.set(dut.read_rsp.ready, 1 if r.random() < read_rsp_ready else 0)

    async def main(ctx):
        _init(ctx, dut)
        await ctx.tick()
        for _batch in range(4):
            # -- prefetch a batch of spans (all accepted before any read) ----
            spans = []
            for _ in range(rng.randint(3, 12)):
                lanes = rng.randint(1, 16)
                start = rng.randrange(0, (1 << aw) - 2 * lanes, 2)
                if rng.random() < 0.3:
                    start |= 1            # bit 0 is ignored by exactLaneAddress
                end = (start & ~1) + 2 * (lanes - 1) + (1 if rng.random() < 0.3 else 0)
                spans.append((start, end))
            for start, end in spans:
                await _prefetch(ctx, dut, start, end)
                model.prefetch(start, end, aw)
                prefetches.append((start & ~1, end & ~1))
            await ctx.tick().repeat(4)      # consumeQueue pop latency

            # -- reads: mostly in-span in order, some forward skips, some
            #    backward/random (direct misses) --------------------------
            reads = []
            for start, end in spans:
                start, end = start & ~1, end & ~1
                addr = start
                while addr <= end:
                    if rng.random() < 0.8:
                        reads.append(addr)
                    if rng.random() < 0.15 and reads:
                        reads.append(rng.choice(reads))        # a repeat: behind
                    if rng.random() < 0.05:
                        reads.append(rng.randrange(0, 1 << aw, 2))  # anywhere
                    addr += 2
            for addr in reads:
                log.append((addr, model.read(addr)))
                ctx.set(dut.read_req.payload.address, addr)
                ctx.set(dut.read_req.valid, 1)
                await ctx.tick().until(dut.read_req.ready)
                ctx.set(dut.read_req.valid, 0)
                if rng.random() < 0.3:
                    await ctx.tick().repeat(rng.randint(1, 3))
            # drain before the next batch so the model's "everything
            # prefetched" assumption holds again
            await ctx.tick().repeat(2)
            await ctx.tick().until(~dut.busy)
            await ctx.tick().repeat(2)

        # every response arrived
        for _ in range(200):
            if len(rsp_data) == len(log):
                break
            await ctx.tick()
        await ctx.tick().repeat(4)
        assert ctx.get(dut.busy) == 0

        # ---- checks --------------------------------------------------------
        assert [mem.lane(a) for a, _ in log] == rsp_data
        misses = sum(1 for _, kind in log if kind == "miss")
        cmds = responder.cmd_log
        assert sum(1 for b in cmds if b["source"] == 1) == misses
        assert sum(1 for b in cmds if b["source"] == 0) == len(prefetches)
        assert stats["dm_rsp_fires"] == misses
        assert stats["cached_fires"] == len(log) - misses
        assert stats["skips"] == model.skipped
        # direct-miss commands: one word at the request's word address
        dm = [b for b in cmds if b["source"] == 1]
        assert all(b["length"] == 3 and b["address"] & 3 == 0 for b in dm)
        assert [b["address"] for b in dm] == [a & ~3 for a, k in log if k == "miss"]
        # prefetch bursts: exact word-aligned span, in prefetch order
        pf = [b for b in cmds if b["source"] == 0]
        expect_bursts = [(s & ~3, ((e & ~3) - (s & ~3) + 3) & 0x3F) for s, e in prefetches]
        assert [(b["address"], b["length"]) for b in pf] == expect_bursts
        word_counts = [(((e & ~3) - (s & ~3)) >> 2) + 1 for s, e in prefetches]
        # counters
        assert ctx.get(dut.fill_hits) == len(log)
        assert ctx.get(dut.fill_misses) == len(prefetches)
        assert ctx.get(dut.fill_burst_count) == len(prefetches)
        assert ctx.get(dut.fill_burst_beats) == sum(word_counts)
        assert ctx.get(dut.single_beat_burst_count) == sum(1 for w in word_counts if w == 1)
        assert ctx.get(dut.multi_beat_burst_count) == sum(1 for w in word_counts if w != 1)
        assert ctx.get(dut.fill_stall_cycles) == stats["stall_cycles"]
        assert ctx.get(dut.req_count) == len(log)
        assert stats["addresses"] == [a for a, _ in log]
        steps = [classify_step(b, a, aw) for a, b in zip(stats["addresses"],
                                                          stats["addresses"][1:])]
        assert ctx.get(dut.req_same_word_count) == steps.count(0)
        assert ctx.get(dut.req_forward_step_count) == steps.count(1)
        assert ctx.get(dut.req_backward_step_count) == steps.count(2)
        assert ctx.get(dut.req_other_count) == steps.count(4)
        assert ctx.get(dut.req_same_line_count) == 0
        assert 1 <= ctx.get(dut.max_occupancy) <= 17
        assert misses > 0 and model.skipped > 0 and len(log) - misses > 0

    run(dut, main, processes=[
        partial(responder.run, bus=dut.mem),
        partial(invariant_monitor, dut=dut, stats=stats),
        consumer,
    ], deadline=60000)


def test_direct_miss_answers_stay_in_request_order_while_hits_wait():
    """A hit issued right after a miss must not overtake the miss's answer."""
    c = mk_config()
    dut = FramebufferPlaneReader(c)
    mem = Memory16(1 << c.address_width)
    for a in range(0, 0x40, 4):
        mem.set_word(a, (a + 1) << 16 | a)
    responder = Responder(mem, dut.mem_params, random.Random(0), rsp_valid_density=0.2)
    got: list[int] = []

    async def collect(ctx):
        async for _clk, _rst, valid, ready, data in ctx.tick().sample(
                dut.read_rsp.valid, dut.read_rsp.ready, dut.read_rsp.payload.data):
            if valid and ready:
                got.append(data)

    async def main(ctx):
        _init(ctx, dut)
        await ctx.tick()
        await _prefetch(ctx, dut, 0x010, 0x01E)
        await ctx.tick().repeat(40)        # let the slow responder fill the lanes
        # 0x004 and 0x000 are direct misses (behind the expected lane); the
        # hits after each must wait for the miss's answer.
        seq = [0x010, 0x012, 0x004, 0x014, 0x016, 0x000, 0x018, 0x01A, 0x01C, 0x01E]
        for addr in seq:
            ctx.set(dut.read_req.payload.address, addr)
            ctx.set(dut.read_req.valid, 1)
            await ctx.tick().until(dut.read_req.ready)
            ctx.set(dut.read_req.valid, 0)
        await ctx.tick().repeat(100)
        assert got == [mem.lane(a) for a in seq]
        assert ctx.get(dut.busy) == 0

    run(dut, main, processes=[partial(responder.run, bus=dut.mem), collect])


# ==========================================================================
# FramebufferPlaneDirectReader
# ==========================================================================


def test_direct_reader_reads_one_word_per_lane():
    c = mk_config()
    dut = FramebufferPlaneDirectReader(c)
    mem = Memory16(1 << c.address_width)
    mem.set_word(0x100, 0x56781234)
    mem.set_word(0x104, 0xDEF09ABC)
    responder = Responder(mem, dut.mem_params, random.Random(0))
    got: list[int] = []

    async def collect(ctx):
        ctx.set(dut.read_rsp.ready, 1)
        async for _clk, _rst, valid, ready, data in ctx.tick().sample(
                dut.read_rsp.valid, dut.read_rsp.ready, dut.read_rsp.payload.data):
            if valid and ready:
                got.append(data)

    async def main(ctx):
        ctx.set(dut.read_req.valid, 0)
        await ctx.tick()
        assert ctx.get(dut.busy) == 0
        for addr in [0x100, 0x102, 0x106, 0x104]:
            ctx.set(dut.read_req.payload.address, addr)
            ctx.set(dut.read_req.valid, 1)
            await ctx.tick().until(dut.read_req.ready)
            ctx.set(dut.read_req.valid, 0)
        await ctx.tick().repeat(20)
        assert got == [0x1234, 0x5678, 0xDEF0, 0x9ABC]
        assert ctx.get(dut.busy) == 0
        assert [(b["address"], b["length"], b["source"]) for b in responder.cmd_log] == [
            (0x100, 3, 0), (0x100, 3, 0), (0x104, 3, 0), (0x104, 3, 0)]

    run(dut, main, processes=[partial(responder.run, bus=dut.mem), collect])


@pytest.mark.parametrize("seed,cmd_ready,rsp_valid,read_rsp_ready", [
    (1, 1.0, 1.0, 1.0),
    (2, 0.5, 0.5, 0.5),
    (3, 0.2, 0.9, 0.3),
])
def test_direct_reader_random(seed, cmd_ready, rsp_valid, read_rsp_ready):
    c = mk_config()
    aw = c.address_width
    dut = FramebufferPlaneDirectReader(c)
    rng = random.Random(seed)
    mem = Memory16(1 << aw, rng)
    responder = Responder(mem, dut.mem_params, random.Random(seed + 1),
                          cmd_ready_density=cmd_ready, rsp_valid_density=rsp_valid)
    got: list[int] = []
    busy_seen = [False]

    async def collect(ctx):
        r = random.Random(seed + 2)
        ctx.set(dut.read_rsp.ready, 1)
        async for _clk, _rst, valid, ready, data, busy in ctx.tick().sample(
                dut.read_rsp.valid, dut.read_rsp.ready, dut.read_rsp.payload.data, dut.busy):
            if valid and ready:
                got.append(data)
            busy_seen[0] |= bool(busy)
            ctx.set(dut.read_rsp.ready, 1 if r.random() < read_rsp_ready else 0)

    reads = [rng.randrange(0, 1 << aw, 2) for _ in range(300)]

    async def main(ctx):
        ctx.set(dut.read_req.valid, 0)
        await ctx.tick()
        for addr in reads:
            ctx.set(dut.read_req.payload.address, addr)
            ctx.set(dut.read_req.valid, 1)
            await ctx.tick().until(dut.read_req.ready)
            ctx.set(dut.read_req.valid, 0)
            if rng.random() < 0.3:
                await ctx.tick().repeat(rng.randint(1, 4))
        for _ in range(500):
            if len(got) == len(reads):
                break
            await ctx.tick()
        await ctx.tick().repeat(3)
        assert got == [mem.lane(a) for a in reads]
        assert ctx.get(dut.busy) == 0 and busy_seen[0]
        assert [b["address"] for b in responder.cmd_log] == [a & ~3 for a in reads]
        assert all(b["length"] == 3 for b in responder.cmd_log)
        assert responder.rsp_beats == len(reads)

    run(dut, main, processes=[partial(responder.run, bus=dut.mem), collect],
        deadline=20000)
