"""Phase 6 unit tests for :mod:`avoodoo.framebuffer.plane_buffer`.

Ports the intent of two Scala tests:

``test/FramebufferPlaneBufferThroughputTest.scala``
    the 2-word-line configuration (``addressWidth = 12``,
    ``memBurstLengthWidth = 4``, ``fbWriteBufferLineWords = 2``): a sequential
    run stays one-write-per-cycle across the line rollover, and a drain starts
    while the next line is still being filled.

``test/FramebufferPlaneBufferFormalTest.scala``
    its ``assert``s become per-cycle checks in :func:`_run` (slot metadata
    invariants, ``drainState`` implies a valid+draining slot, the
    ``writeReqPipe.ready`` promise, every bus command is a WRITE, ``readRsp``
    is never valid) and its ``cover`` becomes a hard assertion in
    :func:`test_full_sequential_line_drains_with_source_one`.

Everything else runs constrained-random write streams (locality-biased so
merges, appends, rotates and flushes all happen) against :class:`Model`, a
scoreboard rather than a cycle model.  The scoreboard knows three things the
RTL must agree with:

* **dirty-word tracking** -- consecutive accepted writes to one address merge
  into one word (new bytes overlay, mask ORs); any other write closes the
  word.  Every bus beat must carry the next such word, in order, with exactly
  that data and mask, addresses climbing one word per beat, ``length =
  4 * beats - 1``, ``last`` on the final beat only.
* **drain reasons** -- ``full`` when a line is complete and byte-complete
  with nothing arriving, ``rotate`` when an accepted write does not fit,
  ``flush`` when ``io.flush`` is high with nothing arriving.  The Scala
  evaluates full and flush as two independent ``when``s in the same cycle,
  so both reason counters step when both hold; the model does the same.
* **single-word drains** and where they start, and how a rotate classifies
  the incoming address (adjacent physical line / gap in the same line /
  elsewhere).

At the end of every run the twenty statistic outputs are compared with the
model's counts, so a counter that is off by one in a corner case fails even
if the bus traffic was right.
"""

from __future__ import annotations

import gc
import random
from dataclasses import dataclass, field, replace

import pytest
from amaranth import Elaboratable
from amaranth.sim import Simulator

from avoodoo.config import Config, TraceConfig
from avoodoo.framebuffer.plane_buffer import (DRAIN_REASON_FLUSH, DRAIN_REASON_FULL,
                                              DRAIN_REASON_ROTATE, STAT_NAMES,
                                              DrainState, FramebufferPlaneBuffer)
from avoodoo.lib.membus import CMD_WRITE

PERIOD = 1e-6


def mk_config(line_words: int = 2, *, address_width: int = 12,
              use_fb_write_buffer: bool = True) -> Config:
    """``FramebufferPlaneBufferThroughputTest.mkConfig``, parameterised on the line."""
    return replace(
        Config.voodoo1(),
        address_width=address_width,
        mem_burst_length_width=4,
        fb_write_buffer_line_words=line_words,
        fb_write_buffer_count=2,
        use_fb_write_buffer=use_fb_write_buffer,
        use_tex_fill_cache=False,
        trace=TraceConfig(enabled=True),
    )


# ---------------------------------------------------------------------------
# scoreboard
# ---------------------------------------------------------------------------


@dataclass
class Word:
    addr: int
    data: int
    mask: int

    def merge(self, data: int, mask: int) -> None:
        for lane in range(4):
            if (mask >> lane) & 1:
                self.data = (self.data & ~(0xFF << (8 * lane))) | (data & (0xFF << (8 * lane)))
        self.mask |= mask


@dataclass
class Line:
    start: int
    words: list[Word] = field(default_factory=list)


@dataclass
class Model:
    """What the combiner must do, from the accepted-write sequence alone."""

    c: Config
    lines: list[Line] = field(default_factory=list)   # closed, in drain order
    cur: Line | None = None
    stats: dict = field(default_factory=lambda: {n: 0 for n in STAT_NAMES})
    expected_beats: list[Word] = field(default_factory=list)

    @property
    def run_depth(self) -> int:
        return self.c.fb_write_buffer_line_words

    @property
    def addr_mask(self) -> int:
        return (1 << self.c.address_width) - 1

    def _phys(self, addr: int) -> int:
        return (addr >> 2) & (self.run_depth - 1)

    def next_addr(self) -> int:
        return (self.cur.start + 4 * len(self.cur.words)) & self.addr_mask

    def _close(self, reasons: list[int]) -> None:
        line = self.cur
        n = len(line.words)
        s = self.stats
        s["write_drain_count"] += 1
        s["write_drain_dirty_word_total"] += n
        if n == self.run_depth:
            s["write_full_drain_count"] += 1
        else:
            s["write_partial_drain_count"] += 1
        if n == 1:
            s["write_single_word_drain_count"] += 1
            if self._phys(line.start) == 0:
                s["write_single_word_drain_start_at_zero_count"] += 1
            if self._phys(line.start) == self.run_depth - 1:
                s["write_single_word_drain_start_at_last_count"] += 1
        for r in reasons:
            s[{DRAIN_REASON_FULL: "write_drain_reason_full_count",
               DRAIN_REASON_ROTATE: "write_drain_reason_rotate_count",
               DRAIN_REASON_FLUSH: "write_drain_reason_flush_count"}[r]] += 1
        self.lines.append(line)
        self.expected_beats.extend(line.words)
        self.cur = None

    # -- the three events the testbench reports --------------------------

    def on_write(self, addr: int, data: int, mask: int) -> str:
        """An accepted write at the combiner (``writeReqPipe.fire``)."""
        if self.cur is None:
            self.cur = Line(addr, [Word(addr, data, mask)])
            return "init"
        last = self.cur.words[-1]
        if last.addr == addr:
            last.merge(data, mask)
            return "merge"
        if len(self.cur.words) < self.run_depth and addr == self.next_addr():
            self.cur.words.append(Word(addr, data, mask))
            return "append"
        # rotate: classify the incoming address, then drain and reopen
        if self._phys(last.addr) == self.run_depth - 1 and self._phys(addr) == 0:
            self.stats["write_rotate_adjacent_line_count"] += 1
        elif last.addr < addr < self.next_addr():
            self.stats["write_rotate_same_line_gap_count"] += 1
        else:
            self.stats["write_rotate_other_line_count"] += 1
        self._close([DRAIN_REASON_ROTATE])
        self.cur = Line(addr, [Word(addr, data, mask)])
        return "rotate"

    def full_ready(self) -> bool:
        return (self.cur is not None and len(self.cur.words) == self.run_depth
                and self.cur.words[-1].mask == 0xF)

    def on_idle_drain(self, flush: bool) -> None:
        """A drain started with no write arriving: full and/or flush."""
        reasons = []
        if self.full_ready():
            reasons.append(DRAIN_REASON_FULL)
        if flush and self.cur is not None:
            reasons.append(DRAIN_REASON_FLUSH)
        assert reasons, "drain started with neither a full line nor a flush"
        self._close(reasons)


# ---------------------------------------------------------------------------
# testbench
# ---------------------------------------------------------------------------


@dataclass
class Write:
    addr: int
    data: int = 0xC0DE0000
    mask: int = 0xF


class Stimulus:
    """Locality-biased random writes, or a scripted list."""

    def __init__(self, c: Config, rng: random.Random, count: int,
                 script: list[Write] | None = None, valid_density: float = 0.7):
        self.c = c
        self.rng = rng
        self.remaining = count
        self.script = list(script) if script is not None else None
        self.valid_density = valid_density
        self.last_addr = 0
        self.run_start = 0
        self.run_len = 0

    def next(self) -> Write | None:
        if self.script is not None:
            return self.script.pop(0) if self.script else None
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        rng = self.rng
        run_depth = self.c.fb_write_buffer_line_words
        mask = (1 << self.c.address_width) - 1
        kind = rng.choices(
            ["same", "next", "gap", "adjacent", "random"],
            weights=[15, 40, 5, 10, 30])[0]
        if kind == "same":
            addr = self.last_addr
        elif kind == "next":
            addr = (self.last_addr + 4) & mask
        elif kind == "gap":
            addr = (self.last_addr + 8) & mask
        elif kind == "adjacent":
            # jump to word 0 of the physical line after the current one
            line_bytes = 4 * run_depth
            addr = ((self.last_addr | (line_bytes - 1)) + 1) & mask
        else:
            addr = rng.getrandbits(self.c.address_width) & ~0x3
        self.last_addr = addr
        data = rng.getrandbits(32)
        m = rng.choice([0xF, 0xF, 0x3, 0xC, 0x1, 0x8, 0x6])
        return Write(addr, data, m)


@dataclass
class Beat:
    addr: int
    data: int
    mask: int
    source: int
    length: int
    last: int


def _run(dut: FramebufferPlaneBuffer, *, seed: int, count: int, cycles: int,
         script: list[Write] | None = None, cmd_ready_density: float = 0.7,
         flush_density: float = 0.0, valid_density: float = 0.7,
         final_flush: bool = True, on_cycle=None) -> tuple[Model, list[Beat], dict]:
    """Drive ``dut``, check the invariants every cycle, return model + bus log.

    ``info`` carries the burst list, the accepted-write kinds, and ``stats``:
    the twenty statistic outputs as read after the last cycle.
    """
    c = dut.c
    rng = random.Random(seed)
    model = Model(c)
    stim = Stimulus(c, rng, count, script, valid_density)
    beats: list[Beat] = []
    bursts: list[list[Beat]] = []
    combiner = c.use_fb_write_buffer
    d = dut.dbg
    info = {"accepted": 0, "kinds": {}, "max_cycle": 0, "bursts": bursts}

    async def tb(ctx):
        pending: Write | None = None
        rsp_queue: list[int] = []      # sources of bursts awaiting a response
        rsp_valid = False
        cur_burst: list[Beat] = []
        drained = 0
        writes_done = False
        idle_cycles = 0
        for cycle in range(cycles):
            # -- drive inputs ----------------------------------------------
            if pending is None and (script is not None or rng.random() < valid_density):
                pending = stim.next()
                if pending is None:
                    writes_done = True
            ctx.set(dut.write_req.valid, pending is not None)
            if pending is not None:
                ctx.set(dut.write_req.payload.address, pending.addr)
                ctx.set(dut.write_req.payload.data, pending.data)
                ctx.set(dut.write_req.payload.mask, pending.mask)
            flush = 0
            if writes_done and final_flush:
                flush = 1
            elif flush_density and rng.random() < flush_density:
                flush = 1
            ctx.set(dut.flush, flush)
            cmd_ready = rng.random() < cmd_ready_density
            ctx.set(dut.mem.cmd.ready, cmd_ready)
            # one outstanding response at a time, offered until accepted
            if not rsp_valid and rsp_queue:
                rsp_valid = True
                ctx.set(dut.mem.rsp.payload.fragment.source, rsp_queue[0])
                ctx.set(dut.mem.rsp.payload.last, 1)
            ctx.set(dut.mem.rsp.valid, rsp_valid)
            ctx.set(dut.read_req.valid, 0)
            ctx.set(dut.read_rsp.ready, 1)

            # -- sample (combinational outputs are settled after set) -----
            assert ctx.get(dut.read_rsp.valid) == 0
            assert ctx.get(dut.read_req.ready) == 0
            cmd_valid = ctx.get(dut.mem.cmd.valid)
            cmd_fire = cmd_valid and cmd_ready
            if cmd_valid:
                assert ctx.get(dut.mem.cmd.payload.fragment.opcode) == CMD_WRITE
            rsp_fire = rsp_valid and ctx.get(dut.mem.rsp.ready)

            wp = d.write_req_pipe
            wp_valid = ctx.get(wp.valid)
            wp_ready = ctx.get(wp.ready)
            if combiner:
                _check_invariants(ctx, d, c)
                if wp_valid:
                    # the formal promise: every acceptable write is accepted now
                    acceptable = ctx.get(d.active_append_ready) or (
                        ctx.get(d.needs_rotate) and ctx.get(d.can_rotate))
                    assert not acceptable or wp_ready, f"cycle {cycle}: ready withheld"
                drain_idle = ctx.get(d.drain_state) == DrainState.IDLE
                active = ctx.get(d.active_slot)
                active_valid = ctx.get(d.slot_valid[active])
                other_valid = ctx.get(d.slot_valid[active ^ 1])
                quiet = drain_idle and active_valid and not other_valid and not wp_valid
                full_now = quiet and model.full_ready()
                flush_now = quiet and flush
                if wp_valid and wp_ready:
                    kind = model.on_write(ctx.get(wp.payload.address),
                                          ctx.get(wp.payload.data),
                                          ctx.get(wp.payload.mask))
                    info["kinds"][kind] = info["kinds"].get(kind, 0) + 1
                    if kind == "rotate":
                        assert ctx.get(d.needs_rotate) and ctx.get(d.can_rotate)
                    elif kind == "merge":
                        assert ctx.get(d.same_word_merge)
                    elif kind == "append":
                        assert ctx.get(d.sequential_append)
                elif full_now or flush_now:
                    model.on_idle_drain(bool(flush))
                if wp_valid and not wp_ready:
                    model.stats["write_stall_cycles"] += 1
                if wp_valid and ctx.get(d.needs_rotate) and not ctx.get(d.can_rotate):
                    model.stats["write_rotate_blocked_cycles"] += 1
            else:
                if wp_valid and not wp_ready:
                    model.stats["write_stall_cycles"] += 1
                if wp_valid and wp_ready:
                    model.expected_beats.append(Word(
                        ctx.get(wp.payload.address), ctx.get(wp.payload.data),
                        ctx.get(wp.payload.mask)))

            if cmd_fire:
                f = dut.mem.cmd.payload.fragment
                b = Beat(ctx.get(f.address), ctx.get(f.data), ctx.get(f.mask),
                         ctx.get(f.source), ctx.get(f.length),
                         ctx.get(dut.mem.cmd.payload.last))
                beats.append(b)
                cur_burst.append(b)
                if b.last:
                    bursts.append(cur_burst)
                    rsp_queue.append(b.source)
                    cur_burst = []
                # dirty-word tracking: this beat must be the next expected word
                assert drained < len(model.expected_beats), \
                    f"cycle {cycle}: unexpected beat {b}"
                exp = model.expected_beats[drained]
                assert (b.addr, b.data, b.mask) == (exp.addr, exp.data, exp.mask), \
                    f"cycle {cycle}: beat {drained} got {b}, expected {exp}"
                drained += 1
            if rsp_fire:
                rsp_queue.pop(0)
                rsp_valid = False

            if on_cycle is not None:
                on_cycle(ctx, cycle)

            if ctx.get(dut.write_req.valid) and ctx.get(dut.write_req.ready):
                pending = None
                info["accepted"] += 1

            busy = ctx.get(dut.busy)
            if writes_done and not busy and not rsp_queue and not rsp_valid \
                    and drained == len(model.expected_beats):
                idle_cycles += 1
                if idle_cycles > 4:
                    info["max_cycle"] = cycle
                    break
            else:
                idle_cycles = 0
            await ctx.tick()
        else:
            info["max_cycle"] = cycles
            if writes_done and final_flush:
                pytest.fail(f"did not drain within {cycles} cycles "
                            f"({drained}/{len(model.expected_beats)} beats)")

        info["drained"] = drained
        info["stats"] = {name: ctx.get(getattr(dut, name)) for name in STAT_NAMES}

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()
    return model, beats, info


def _check_invariants(ctx, d, c: Config) -> None:
    """``FramebufferPlaneBufferFormalDut``'s ``formalStrong`` asserts, per cycle."""
    run_depth = c.fb_write_buffer_line_words
    for slot in range(2):
        valid = ctx.get(d.slot_valid[slot])
        draining = ctx.get(d.slot_draining[slot])
        count = ctx.get(d.slot_word_count[slot])
        occ = ctx.get(d.slot_occupancy[slot])
        if not valid:
            assert not draining, f"slot {slot} draining while invalid"
            assert count == 0, f"slot {slot} count {count} while invalid"
            assert occ == 0, f"slot {slot} FIFO holds {occ} while invalid"
        else:
            assert 1 <= count <= run_depth, f"slot {slot} count {count}"
    state = ctx.get(d.drain_state)
    if state != DrainState.IDLE:
        ds = ctx.get(d.drain_slot)
        assert ctx.get(d.slot_valid[ds]) and ctx.get(d.slot_draining[ds])
        assert state == DrainState.SEND, "Rsp is unreachable"


def _check_bursts(model: Model, info: dict, c: Config) -> None:
    bursts = info["bursts"]
    assert info["drained"] == len(model.expected_beats)
    if c.use_fb_write_buffer:
        assert len(bursts) == len(model.lines)
        for burst, line in zip(bursts, model.lines):
            assert len(burst) == len(line.words)
            for i, b in enumerate(burst):
                assert b.source == 1
                assert b.length == 4 * len(burst) - 1
                assert b.last == (i == len(burst) - 1)
                assert b.addr == (line.start + 4 * i) & model.addr_mask
    else:
        for burst in bursts:
            assert len(burst) == 1
            (b,) = burst
            assert b.source == 0 and b.length == 3 and b.last == 1


def _check_stats(model: Model, dut_stats: dict) -> None:
    for name in STAT_NAMES:
        assert dut_stats[name] == model.stats[name], \
            f"{name}: dut {dut_stats[name]} model {model.stats[name]}"


def _run_and_check(c: Config, **kw):
    dut = FramebufferPlaneBuffer(c)
    model, beats, info = _run(dut, **kw)
    _check_bursts(model, info, c)
    _check_stats(model, info["stats"])
    return model, beats, info


# ---------------------------------------------------------------------------
# FramebufferPlaneBufferThroughputTest
# ---------------------------------------------------------------------------


def test_sequential_line_rollover_stays_one_per_cycle():
    """Four sequential words across a 2-word line boundary, each accepted the
    cycle it is offered, with the memory always ready."""
    c = mk_config(2)
    dut = FramebufferPlaneBuffer(c)
    script = [Write(a, 0x11110000 + i) for i, a in enumerate((0x000, 0x004, 0x008, 0x00C))]
    accepted_at = []

    def on_cycle(ctx, cycle):
        if ctx.get(dut.write_req.valid) and ctx.get(dut.write_req.ready):
            accepted_at.append(cycle)

    model, beats, info = _run(dut, seed=1, count=0, cycles=60, script=script,
                              cmd_ready_density=1.0, on_cycle=on_cycle)
    assert accepted_at == [0, 1, 2, 3], accepted_at
    # two full lines, both drained as 2-beat bursts with source 1
    assert [len(b) for b in info["bursts"]] == [2, 2]
    assert all(b.source == 1 for b in beats)
    _check_bursts(model, info, c)
    _check_stats(model, info["stats"])
    assert model.stats["write_full_drain_count"] == 2


def test_drain_starts_while_next_line_is_filled():
    """0x000, 0x004 fill line 0; 0x008 rotates into slot 1 while slot 0 drains."""
    c = mk_config(2)
    dut = FramebufferPlaneBuffer(c)
    script = [Write(a, 0x22220000 + i) for i, a in enumerate((0x000, 0x004, 0x008))]
    saw_draining = []

    def on_cycle(ctx, cycle):
        d = dut.dbg
        if ctx.get(d.slot_draining[0]) or ctx.get(d.slot_draining[1]):
            saw_draining.append(cycle)

    model, beats, info = _run(dut, seed=1, count=0, cycles=40, script=script,
                              cmd_ready_density=1.0, final_flush=False,
                              on_cycle=on_cycle)
    assert saw_draining and saw_draining[0] < 13, saw_draining
    # line 0 drained by the rotate; the single word 0x008 stays parked (no flush)
    assert [len(b) for b in info["bursts"]] == [2]
    assert model.cur is not None and model.cur.start == 0x008


# ---------------------------------------------------------------------------
# FramebufferPlaneBufferFormalTest coverage point, as a hard check
# ---------------------------------------------------------------------------


def test_full_sequential_line_drains_with_source_one():
    """``cover(sawFullSequentialLine && cmd.fire && source === 1)``."""
    c = mk_config(2, address_width=8)
    script = [Write(0x00, 0xAAAA0000), Write(0x04, 0xBBBB0000)]
    # no flush at all: the line drains because it is full and byte-complete
    model, beats, info = _run_and_check(c, seed=3, count=0, cycles=60, script=script,
                                        cmd_ready_density=1.0, final_flush=False)
    assert len(beats) == 2 and beats[0].source == 1 and beats[1].last
    assert model.stats["write_drain_reason_full_count"] == 1
    assert model.stats["write_drain_reason_flush_count"] == 0


# ---------------------------------------------------------------------------
# dirty-word tracking, drain reasons, single-word drains
# ---------------------------------------------------------------------------


def test_same_word_merge_overlays_bytes():
    """Three masked writes to one word drain as one beat with the OR'd mask and
    the byte overlay; the untouched lane keeps the *first* write's byte."""
    c = mk_config(2)
    script = [Write(0x10, 0x11223344, 0x3), Write(0x10, 0xAABBCCDD, 0x4),
              Write(0x10, 0x55667788, 0x2)]
    model, beats, info = _run_and_check(c, seed=4, count=0, cycles=60, script=script,
                                        cmd_ready_density=1.0)
    assert len(beats) == 1
    assert beats[0].mask == 0x7
    assert beats[0].data == 0x11BB7744
    assert model.stats["write_single_word_drain_count"] == 1
    assert model.stats["write_drain_reason_flush_count"] == 1


def test_single_word_drains_at_line_edges():
    """Scattered single words: each rotate drains one word; the start-at-zero /
    start-at-last counters classify by physical word index."""
    c = mk_config(4)
    # word indices within a 4-word line: 0, 3, 1, 3, 0 -> zero x2, last x2
    script = [Write(0x100), Write(0x20C), Write(0x304), Write(0x40C), Write(0x500)]
    model, beats, info = _run_and_check(c, seed=5, count=0, cycles=120, script=script,
                                        cmd_ready_density=1.0)
    s = model.stats
    assert s["write_single_word_drain_count"] == 5
    assert s["write_single_word_drain_start_at_zero_count"] == 2
    assert s["write_single_word_drain_start_at_last_count"] == 2
    assert s["write_drain_reason_rotate_count"] == 4
    assert s["write_drain_reason_flush_count"] == 1
    # 0x40C -> 0x500 is "adjacent" by the Scala's index-only test (last -> 0)
    # even though the lines are not neighbours; the other three are "other".
    assert s["write_rotate_adjacent_line_count"] == 1
    assert s["write_rotate_other_line_count"] == 3


def test_rotate_classification():
    """adjacent physical line / gap inside the line / elsewhere.

    A run is a chain of *sequential* words, so ``nextAddr`` is always
    ``pendingAddr + 4`` and the "same line gap" band ``(pending, next)`` can
    only be hit by a non-word-aligned address; the module takes byte
    addresses, so that is a legal input even if ``Write`` never produces one.
    The "adjacent" test compares word indices only (last -> 0), so 0x10C ->
    0x200 counts even though 0x200 is not the neighbouring line.
    """
    c = mk_config(4)
    script = [
        Write(0x10C),   # word 3 of a line
        Write(0x200),   # word 0 -> rotate, adjacent (index-only)
        Write(0x202),   # 0x200 < 0x202 < 0x204 -> rotate, same-line gap
        Write(0x300),   # -> rotate, other
    ]
    model, beats, info = _run_and_check(c, seed=6, count=0, cycles=120, script=script,
                                        cmd_ready_density=1.0)
    s = model.stats
    assert s["write_rotate_adjacent_line_count"] == 1
    assert s["write_rotate_same_line_gap_count"] == 1
    assert s["write_rotate_other_line_count"] == 1
    assert s["write_drain_reason_rotate_count"] == 3
    assert s["write_single_word_drain_count"] == 4


def test_full_and_flush_in_the_same_cycle_count_both_reasons():
    """The Scala's full and flush ``when``s are independent: a complete line
    seen while ``io.flush`` is high steps both reason counters, one drain."""
    c = mk_config(2)
    script = [Write(0x00), Write(0x04)]
    # flush held high from the first cycle: the line completes under flush
    model, beats, info = _run_and_check(c, seed=7, count=0, cycles=60, script=script,
                                        cmd_ready_density=1.0, flush_density=1.0)
    s = model.stats
    assert s["write_drain_count"] == 1
    assert s["write_drain_reason_full_count"] == 1
    assert s["write_drain_reason_flush_count"] == 1


@pytest.mark.parametrize("line_words", [2, 4])
@pytest.mark.parametrize("seed", [11, 12, 13])
def test_random_traffic_matches_model(line_words, seed):
    c = mk_config(line_words)
    model, beats, info = _run_and_check(c, seed=seed, count=300, cycles=6000,
                                        cmd_ready_density=0.6, flush_density=0.02)
    kinds = info["kinds"]
    assert kinds.get("merge", 0) > 0 and kinds.get("append", 0) > 0 \
        and kinds.get("rotate", 0) > 0, kinds
    assert model.stats["write_drain_reason_full_count"] > 0
    assert model.stats["write_drain_reason_flush_count"] > 0
    assert model.stats["write_rotate_blocked_cycles"] > 0


def test_random_traffic_backpressured():
    """A starved memory: rotates block, stalls count, nothing is lost."""
    c = mk_config(4)
    model, beats, info = _run_and_check(c, seed=21, count=200, cycles=8000,
                                        cmd_ready_density=0.15, valid_density=0.9)
    assert model.stats["write_stall_cycles"] > 0
    assert model.stats["write_rotate_blocked_cycles"] > 0


# ---------------------------------------------------------------------------
# the direct-queue branch (Config.voodoo1(): useFbWriteBuffer = false)
# ---------------------------------------------------------------------------


def test_direct_queue_passes_writes_through_in_order():
    c = mk_config(2, use_fb_write_buffer=False)
    model, beats, info = _run_and_check(c, seed=31, count=150, cycles=3000,
                                        cmd_ready_density=0.5)
    assert len(beats) == 150
    assert model.stats["write_stall_cycles"] > 0
    for name in STAT_NAMES:
        if name != "write_stall_cycles":
            assert model.stats[name] == 0


def test_direct_queue_busy_until_drained():
    c = mk_config(2, use_fb_write_buffer=False)
    dut = FramebufferPlaneBuffer(c)
    script = [Write(0x40 + 4 * i, i) for i in range(6)]
    seen_busy = []

    def on_cycle(ctx, cycle):
        seen_busy.append(ctx.get(dut.busy))

    _run(dut, seed=32, count=0, cycles=60, script=script, cmd_ready_density=0.3,
         on_cycle=on_cycle)
    assert seen_busy[1] == 1            # the m2sPipe holds the first write
    assert seen_busy[-1] == 0


# ---------------------------------------------------------------------------
# the Scala `require`s
# ---------------------------------------------------------------------------


def test_requires():
    # `_MustUse__silence` is how Amaranth's own test suite suppresses the
    # UnusedElaboratable warning for a component that is built to fail.
    Elaboratable._MustUse__silence = True
    try:
        with pytest.raises(ValueError, match="LineWords"):
            FramebufferPlaneBuffer(mk_config(1))
        with pytest.raises(ValueError, match="Count"):
            FramebufferPlaneBuffer(replace(mk_config(2), fb_write_buffer_count=1))
        gc.collect()          # the warning is emitted from __del__
    finally:
        Elaboratable._MustUse__silence = False
