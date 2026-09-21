"""Phase 8.0 unit tests for :mod:`avoodoo.texture.tmu_no_texture_cache`.

``TmuNoTextureCache`` has no SpinalSim test upstream (``TmuTest.scala`` and
the cache tests all build ``Config.voodoo1()``, which selects the cache), so
the intent here is the Scala itself (``TmuTextureCache.scala`` l. 865-996):

1. a request-level model (:func:`expected`): the word reads a request
   issues -- one per *distinct* word among its active taps, in tap order --
   and the texels its ``sampleFetch`` must carry (``texel16`` of the word at
   each tap, 0 for the inactive taps of a point sample), checked on directed
   cases (one word, four words, two taps per word, 8-bit and 16-bit) and on
   random traffic with random memory latency and consumer back-pressure;
2. the ``GenerationFlags.formal`` asserts on ``texRead.cmd`` (READ, ``last``,
   ``length == 3``), plus a word-aligned address, ``busy`` covering every
   valid command / response, and the stream hold rule on both outputs,
   checked on every cycle of every test by :func:`invariants`;
3. the timing and flow-control facts the Scala fixes: the accept-to-fetch
   latency of a point sample against a zero-latency memory, the single
   outstanding response (``raw.ready = !outValid``) and how far the 16-deep
   ``queued`` FIFO lets commands run ahead of a stalled consumer, the
   request window (``texFillRequestWindow``: 16 by default, **2** on the
   Console), ``takeWhen(last)`` on a multi-beat response, ``invalidate``
   being a no-op, and the ``reset_less`` audit of the plain ``Reg``\\ s.

The last group runs ``Tmu`` itself with the Console's configuration (the
plan 8.0 selection in ``tmu.py``): the first sample of a solid texture
decodes -- no cold-miss quirk -- with one 4-byte read.

Run from ``amaranth/``: ``.venv/bin/python -m pytest -o addopts="" tests/test_tmu_no_texture_cache.py``.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from amaranth.hdl import ClockDomain, Elaboratable, Module
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.lib.membus import CMD_READ
from avoodoo.texture.tmu import Tmu
from avoodoo.texture.tmu_no_texture_cache import (DIRECT_QUEUE_DEPTH, DIRECT_READ_LENGTH,
                                                  TmuNoTextureCache)
from avoodoo.texture.tmu_texture_cache import TmuTextureCache
from avoodoo.texture.tmu_types import SampleRequest

AM_ROOT = Path(__file__).resolve().parents[1]
if str(AM_ROOT) not in sys.path:
    sys.path.insert(0, str(AM_ROOT))

C_V1 = Config.voodoo1()
#: ``Config.voodoo1().copy(useTexFillCache = false)`` -- GenOne's TmuNoTextureCache row.
C = replace(C_V1, use_tex_fill_cache=False)
#: The Tang Console (CoreGowinGen.scala:65-80): no cache, a 2-deep request window.
C_CONSOLE = replace(C_V1, use_fb_write_buffer=False, fb_write_buffer_line_words=16,
                    use_fb_read_cache=False, use_tex_fill_cache=False,
                    tex_fill_request_window=2, hdmi_scanout_pixel_repeat_x=4,
                    hdmi_scanout_dmt640x480=True)
CLK = 1e-6
AW = C.address_width


# ===========================================================================
# requests and the reference model
# ===========================================================================


def mem_word(word_addr: int) -> int:
    """Deterministic contents of the 32-bit word at ``word_addr`` (4-aligned)."""
    return ((word_addr >> 2) * 0x9E3779B1 + 0x2545F491) & 0xFFFFFFFF


def texel16(word: int, addr: int, is16_bit: bool) -> int:
    """``TmuTexturePathBase.texel16`` on ints: half ``addr[1]``, byte ``addr[0]``."""
    half = (word >> 16) & 0xFFFF if addr & 2 else word & 0xFFFF
    if is16_bit:
        return half
    byte = (half >> 8) & 0xFF if addr & 1 else half & 0xFF
    return byte | (byte << 8)


@dataclass
class Req:
    """One ``SampleRequest`` as the direct path sees it: taps, format, passthrough."""

    point: int
    bi: tuple[int, int, int, int]
    bilinear: bool
    is16_bit: bool
    format: int = 0
    ds: int = 0
    dt: int = 0
    ncc: int = 0

    @property
    def taps(self) -> list[int]:
        """``tapAddrs``: tap 0 is ``biAddr0`` when bilinear, else ``pointAddr``."""
        return [self.bi[0] if self.bilinear else self.point, *self.bi[1:]]

    @property
    def active(self) -> list[int]:
        return self.taps if self.bilinear else self.taps[:1]

    def const(self, c: Config):
        return SampleRequest(c).const({
            "point_addr": self.point,
            "bi_addr0": self.bi[0], "bi_addr1": self.bi[1],
            "bi_addr2": self.bi[2], "bi_addr3": self.bi[3],
            "lod_base": 0, "lod_shift": 0,
            "is16_bit": int(self.is16_bit),
            "tex_tables": {"tex_base": [0] * 9, "tex_end": [0] * 9, "tex_shift": [0] * 9},
            "tap_startup_decode": [{"valid": 0, "bank": [0] * 4, "pair": 0}] * 4,
            "tap_packed_bank": [0] * 4,
            "tap_packed_pair": [0] * 4,
            "bilinear": int(self.bilinear),
            "passthrough": {"format": self.format, "bilinear": int(self.bilinear),
                            "ncc_table_select": self.ncc, "ds": self.ds, "dt": self.dt},
        })


def expected(req: Req) -> tuple[list[int], list[int]]:
    """``(word reads in order, texels)`` the Scala issues / returns for ``req``.

    ``pendingFirst`` takes the lowest pending tap, ``currentMask`` folds every
    pending tap of the same word into that read, so the reads are the
    distinct words in first-tap order; ``firstWord`` zeroes the texels so a
    point sample carries 0 in taps 1..3.
    """
    words: list[int] = []
    for a in req.active:
        w = a & ~3
        if w not in words:
            words.append(w)
    texels = [texel16(mem_word(a & ~3), a, req.is16_bit) for a in req.active]
    return words, texels + [0] * (4 - len(texels))


def random_req(rng: random.Random, *, is16_bit: bool | None = None) -> Req:
    """Mostly neighbourly taps (so words are shared), sometimes scattered."""
    is16 = rng.random() < 0.5 if is16_bit is None else is16_bit
    bpp = 2 if is16 else 1
    kind = rng.random()
    if kind < 0.6:
        base = rng.randrange(0, 1 << AW)
        pitch = rng.choice([4, 8, 16, 512, 4 * bpp])
        bi = tuple((base + dx * bpp + dy * pitch) & ((1 << AW) - 1)
                   for dy in (0, 1) for dx in (0, 1))
        point = bi[0]
    else:
        bi = tuple(rng.randrange(0, 1 << AW) for _ in range(4))
        point = rng.randrange(0, 1 << AW)
    return Req(point, bi, bilinear=rng.random() < 0.5, is16_bit=is16,
               format=rng.randrange(16), ds=rng.randrange(16), dt=rng.randrange(16),
               ncc=rng.randrange(2))


# ===========================================================================
# simulation plumbing
# ===========================================================================


class _Clocked(Elaboratable):
    def __init__(self, dut):
        self.dut = dut

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.submodules.dut = self.dut
        return m


@dataclass
class Trace:
    cmds: list[int] = field(default_factory=list)
    cmd_lengths: list[int] = field(default_factory=list)
    fetches: list = field(default_factory=list)
    accept_cycles: list[int] = field(default_factory=list)
    cmd_cycles: list[int] = field(default_factory=list)
    fetch_cycles: list[int] = field(default_factory=list)
    busy_cycles: int = 0
    cycle: int = 0


def invariants(dut, trace: Trace):
    """Per-cycle checks: the formal asserts, ``busy``, and the stream hold rule."""
    cmd = dut.tex_read.cmd
    fetch = dut.sample_fetch

    async def proc(ctx):
        last_cmd = None
        last_fetch = None
        async for _clk, _rst, cv, cr, opcode, last, length, address, fv, fr, fetch_val, \
                busy, req_v, req_r in ctx.tick().sample(
                    cmd.valid, cmd.ready, cmd.payload.fragment.opcode, cmd.payload.last,
                    cmd.payload.fragment.length, cmd.payload.fragment.address,
                    fetch.valid, fetch.ready, fetch.payload, dut.busy,
                    dut.sample_request.valid, dut.sample_request.ready):
            trace.cycle += 1
            trace.busy_cycles += int(busy)
            if cv:
                assert opcode == CMD_READ
                assert last == 1
                assert length == DIRECT_READ_LENGTH
                assert address % 4 == 0, hex(address)
                assert busy, "a read command with busy low"
                beat = (opcode, last, length, address)
                if last_cmd is not None:
                    assert beat == last_cmd, "texRead.cmd payload changed while stalled"
                last_cmd = None if cr else beat
                if cr:
                    trace.cmds.append(address)
                    trace.cmd_lengths.append(length)
                    trace.cmd_cycles.append(trace.cycle)
            else:
                assert last_cmd is None, "texRead.cmd valid dropped while stalled"
            if fv:
                assert busy, "a response with busy low"
                val = fetch_val.as_value().value
                if last_fetch is not None:
                    assert val == last_fetch, "sampleFetch payload changed while stalled"
                last_fetch = None if fr else val
                if fr:
                    trace.fetches.append(fetch_val)
                    trace.fetch_cycles.append(trace.cycle)
            else:
                assert last_fetch is None, "sampleFetch valid dropped while stalled"
            if req_v and req_r:
                trace.accept_cycles.append(trace.cycle)

    return proc


def word_responder(dut, *, latency=lambda: 0, gap=lambda: 0, beats: int = 1,
                   cmd_ready=lambda: True):
    """Answer each ``texRead.cmd`` with :func:`mem_word` of its address.

    ``beats > 1`` answers with that many beats, ``last`` only on the final one
    and junk data before it (the Scala's ``takeWhen(last)`` must drop them).
    ``cmd_ready()`` is sampled every cycle so the command side can be stalled.
    """
    cmd = dut.tex_read.cmd
    rsp = dut.tex_read.rsp
    pending: list[int] = []

    async def watcher(ctx):
        ready = cmd_ready()
        ctx.set(cmd.ready, int(ready))
        async for _clk, _rst, valid, address in ctx.tick().sample(
                cmd.valid, cmd.payload.fragment.address):
            if valid and ready:
                pending.append(address)
            ready = cmd_ready()
            ctx.set(cmd.ready, int(ready))

    async def driver(ctx):
        while True:
            if not pending:
                await ctx.tick()
                continue
            address = pending.pop(0)
            for _ in range(latency()):
                await ctx.tick()
            for k in range(beats):
                last = k == beats - 1
                ctx.set(rsp.payload.fragment.data,
                        mem_word(address) if last else ~mem_word(address) & 0xFFFFFFFF)
                ctx.set(rsp.payload.last, int(last))
                ctx.set(rsp.valid, 1)
                await ctx.tick().until(rsp.ready)
                ctx.set(rsp.valid, 0)
                for _ in range(gap()):
                    await ctx.tick()

    return watcher, driver


async def push(ctx, dut, req: Req, *, c: Config):
    ctx.set(dut.sample_request.payload, req.const(c))
    ctx.set(dut.sample_request.valid, 1)
    await ctx.tick().until(dut.sample_request.ready)
    ctx.set(dut.sample_request.valid, 0)


async def idle(ctx, dut, *, limit: int = 2000):
    """Wait until ``busy`` has been low for three cycles (see the cache's ``idle``:
    the request FIFO's latency-2 pop makes ``busy`` dip right after an accept)."""
    low = 0
    for _ in range(limit):
        low = low + 1 if not ctx.get(dut.busy) else 0
        if low >= 3:
            return
        await ctx.tick()
    raise AssertionError("direct path did not go idle")


def run(dut, *procs, deadline: int = 20_000):
    sim = Simulator(_Clocked(dut))
    sim.add_clock(CLK)
    for p in procs:
        for q in (p if isinstance(p, tuple) else (p,)):
            sim.add_testbench(q)
    sim.run_until(deadline * CLK)


def check_fetch(fetch, req: Req) -> None:
    _words, texels = expected(req)
    assert fetch.bilinear == int(req.bilinear)
    assert fetch.passthrough.format == req.format
    assert fetch.passthrough.bilinear == int(req.bilinear)
    assert fetch.passthrough.ncc_table_select == req.ncc
    assert fetch.passthrough.ds == req.ds and fetch.passthrough.dt == req.dt
    got = list(fetch.texels)
    assert got == texels, ([hex(t) for t in got], [hex(t) for t in texels],
                           [hex(a) for a in req.taps])


def check_all(trace: Trace, reqs: list[Req]) -> None:
    assert len(trace.fetches) == len(reqs), (len(trace.fetches), len(reqs))
    want_cmds: list[int] = []
    for f, r in zip(trace.fetches, reqs):
        check_fetch(f, r)
        want_cmds += expected(r)[0]
    assert trace.cmds == want_cmds


# ===========================================================================
# construction
# ===========================================================================


def test_rejects_the_cache_config():
    with pytest.raises(ValueError):
        TmuNoTextureCache(C_V1)
    TmuNoTextureCache(C)
    TmuNoTextureCache(C_CONSOLE)


def test_same_ports_as_the_cache():
    """``TmuTexturePathIo`` is shared: ``Tmu`` swaps the two on the flag alone."""
    from amaranth.hdl._ir import Fragment

    a_dut, b_dut = TmuNoTextureCache(C), TmuTextureCache(C_V1)
    a, b = a_dut.signature, b_dut.signature
    Fragment.get(a_dut, None)
    Fragment.get(b_dut, None)   # both elaborate (and silence UnusedElaboratable)
    assert set(a.members) == set(b.members)
    for name in a.members:
        assert a.members[name] == b.members[name], name


# ===========================================================================
# directed cases against the model
# ===========================================================================


@pytest.mark.parametrize("addr", [0x000000, 0x000002, 0x1234_56, 0x3FFFFFE])
def test_point_16bit_is_one_word_read(addr):
    dut = TmuNoTextureCache(C)
    trace = Trace()
    req = Req(addr, (7, 8, 9, 10), bilinear=False, is16_bit=True, format=3, ds=5, dt=6)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C)
        await idle(ctx, dut)
        assert trace.cmds == [addr & ~3]
        assert len(trace.fetches) == 1
        check_fetch(trace.fetches[0], req)
        f = trace.fetches[0]
        assert list(f.texels)[1:] == [0, 0, 0], "firstWord zeroes the inactive taps"
        half = (mem_word(addr & ~3) >> 16) & 0xFFFF if addr & 2 else mem_word(addr & ~3) & 0xFFFF
        assert list(f.texels)[0] == half

    run(dut, tb, word_responder(dut), invariants(dut, trace))


@pytest.mark.parametrize("addr", [0x10, 0x11, 0x12, 0x13])
def test_point_8bit_replicates_the_byte(addr):
    dut = TmuNoTextureCache(C)
    trace = Trace()
    req = Req(addr, (0, 0, 0, 0), bilinear=False, is16_bit=False)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C)
        await idle(ctx, dut)
        byte = (mem_word(0x10) >> (8 * (addr & 3))) & 0xFF
        assert list(trace.fetches[0].texels) == [byte | (byte << 8), 0, 0, 0]
        assert trace.cmds == [0x10]

    run(dut, tb, word_responder(dut), invariants(dut, trace))


def test_bilinear_four_taps_in_one_word_is_one_read():
    """8-bit taps at 0x40..0x43: one command, four texels from the same word."""
    dut = TmuNoTextureCache(C)
    trace = Trace()
    req = Req(0x999, (0x40, 0x41, 0x42, 0x43), bilinear=True, is16_bit=False, format=9)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C)
        await idle(ctx, dut)
        assert trace.cmds == [0x40]
        check_all(trace, [req])
        assert 0x999 not in trace.cmds, "pointAddr is ignored when bilinear"

    run(dut, tb, word_responder(dut), invariants(dut, trace))


def test_bilinear_four_words_read_in_tap_order():
    dut = TmuNoTextureCache(C)
    trace = Trace()
    req = Req(0, (0x1000, 0x2004, 0x0008, 0x300C), bilinear=True, is16_bit=True)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C)
        await idle(ctx, dut)
        assert trace.cmds == [0x1000, 0x2004, 0x0008, 0x300C]
        check_all(trace, [req])

    run(dut, tb, word_responder(dut), invariants(dut, trace))


def test_bilinear_two_taps_per_word_is_two_reads():
    """16-bit taps at 0x20, 0x22, 0x24, 0x26: words 0x20 and 0x24 (a real 2x2 on a row)."""
    dut = TmuNoTextureCache(C)
    trace = Trace()
    req = Req(0, (0x20, 0x22, 0x24, 0x26), bilinear=True, is16_bit=True)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C)
        await idle(ctx, dut)
        assert trace.cmds == [0x20, 0x24]
        check_all(trace, [req])

    run(dut, tb, word_responder(dut), invariants(dut, trace))


def test_bilinear_shared_words_out_of_order_taps():
    """Taps 0 and 3 share a word, 1 and 2 another: the first read serves taps 0 and
    3 (``currentMask``), the second taps 1 and 2 -- two reads, taps resolved by
    word, not by index."""
    dut = TmuNoTextureCache(C)
    trace = Trace()
    req = Req(0, (0x100, 0x200, 0x201, 0x102), bilinear=True, is16_bit=False)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C)
        await idle(ctx, dut)
        assert trace.cmds == [0x100, 0x200]
        check_all(trace, [req])

    run(dut, tb, word_responder(dut), invariants(dut, trace))


# ===========================================================================
# random traffic against the model
# ===========================================================================


@pytest.mark.parametrize("cfg_name", ["voodoo1_nocache", "console"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_traffic_matches_model(cfg_name, seed):
    """Random requests, random memory latency and gaps, random consumer ready:
    every fetch carries the modelled texels and the reads are the modelled words."""
    c = {"voodoo1_nocache": C, "console": C_CONSOLE}[cfg_name]
    rng = random.Random(seed)
    dut = TmuNoTextureCache(c)
    trace = Trace()
    reqs = [random_req(rng) for _ in range(120)]

    async def tb(ctx):
        ready_rng = random.Random(seed + 100)
        for r in reqs:
            ctx.set(dut.sample_request.payload, r.const(c))
            ctx.set(dut.sample_request.valid, 1)
            while True:
                ctx.set(dut.sample_fetch.ready, int(ready_rng.random() < 0.6))
                if ctx.get(dut.sample_request.ready):
                    await ctx.tick()
                    break
                await ctx.tick()
            ctx.set(dut.sample_request.valid, 0)
            for _ in range(rng.choice([0, 0, 1, 3])):
                ctx.set(dut.sample_fetch.ready, int(ready_rng.random() < 0.6))
                await ctx.tick()
        ctx.set(dut.sample_fetch.ready, 1)
        await idle(ctx, dut, limit=20_000)
        check_all(trace, reqs)

    lat = random.Random(seed + 7)
    run(dut, tb, word_responder(dut, latency=lambda: lat.choice([0, 0, 1, 2, 5]),
                                gap=lambda: lat.choice([0, 0, 1])),
        invariants(dut, trace), deadline=60_000)


def test_random_traffic_with_cmd_backpressure():
    """``texRead.cmd.ready`` stalls: the fork holds ``toMem`` (its linkEnable) while
    ``toQ`` has taken the word, and nothing is lost or duplicated."""
    seed = 11
    rng = random.Random(seed)
    dut = TmuNoTextureCache(C)
    trace = Trace()
    reqs = [random_req(rng) for _ in range(80)]
    cr = random.Random(seed + 3)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        for r in reqs:
            await push(ctx, dut, r, c=C)
        await idle(ctx, dut, limit=20_000)
        check_all(trace, reqs)

    run(dut, tb, word_responder(dut, cmd_ready=lambda: cr.random() < 0.3),
        invariants(dut, trace), deadline=60_000)


# ===========================================================================
# timing and flow control
# ===========================================================================


def test_point_sample_latency_against_zero_latency_memory():
    """Accept -> fetch is a fixed 5 cycles for a point sample: request FIFO pop
    (latency 2) -> command the same cycle -> response next cycle -> ``queued``
    pop (latency 2, already there) joins -> ``outValid`` the cycle after."""
    dut = TmuNoTextureCache(C)
    trace = Trace()
    reqs = [Req(0x40 * k, (0, 0, 0, 0), bilinear=False, is16_bit=True) for k in range(4)]

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        for r in reqs:
            await push(ctx, dut, r, c=C)
            await idle(ctx, dut)
        assert len(trace.accept_cycles) == 4 and len(trace.fetch_cycles) == 4
        lat = [f - a for a, f in zip(trace.accept_cycles, trace.fetch_cycles)]
        assert lat == [5, 5, 5, 5], lat
        cmd_lat = [m - a for a, m in zip(trace.accept_cycles, trace.cmd_cycles)]
        assert cmd_lat == [2, 2, 2, 2], cmd_lat

    run(dut, tb, word_responder(dut), invariants(dut, trace))


def test_one_response_outstanding_and_commands_run_ahead_up_to_the_queue():
    """With ``sampleFetch.ready`` low the first response parks in ``assembled``
    (``raw.ready = !outValid``); word reads keep being issued until ``queued``
    (16 deep) is full plus the one beat the asynchronous fork lets ``toMem``
    take ahead of ``toQ``; the request FIFO then fills and ``sampleRequest``
    stalls.  Releasing ``ready`` drains everything in order."""
    dut = TmuNoTextureCache(C)
    trace = Trace()
    reqs = [Req(0x10 * k, (0, 0, 0, 0), bilinear=False, is16_bit=True, format=k & 15)
            for k in range(60)]

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 0)
        await ctx.tick()
        accepted = 0
        for r in reqs:
            ctx.set(dut.sample_request.payload, r.const(C))
            ctx.set(dut.sample_request.valid, 1)
            stalled = 0
            while not ctx.get(dut.sample_request.ready):
                await ctx.tick()
                stalled += 1
                if stalled > 100:
                    break
            if stalled > 100:
                break
            await ctx.tick()
            accepted += 1
        ctx.set(dut.sample_request.valid, 0)
        for _ in range(50):
            await ctx.tick()
        assert trace.fetches == [], "nothing may come out while ready is low"
        assert ctx.get(dut.sample_fetch.valid) == 1
        assert ctx.get(dut.busy) == 1
        # 1 in `assembled` + 16 in `queued` + at most one more in the fork.
        assert DIRECT_QUEUE_DEPTH + 1 <= len(trace.cmds) <= DIRECT_QUEUE_DEPTH + 2, \
            len(trace.cmds)
        # ... + the 16-deep request window (+ the expander holding nothing:
        # point samples finish in one word) is what the module can hold.
        assert accepted <= len(trace.cmds) + C.tex_fill_request_window + 1, accepted
        assert accepted >= len(trace.cmds) + C.tex_fill_request_window - 1, accepted
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        for r in reqs[accepted:]:
            await push(ctx, dut, r, c=C)
        await idle(ctx, dut, limit=5000)
        check_all(trace, reqs)

    run(dut, tb, word_responder(dut), invariants(dut, trace), deadline=40_000)


@pytest.mark.parametrize("window", [2, 16])
def test_request_window_depth(window):
    """``texFillRequestWindow`` requests are accepted while the expander is stuck
    on a command the memory refuses (``cmd.ready`` low): the FIFO holds
    ``window``, the expander presents the next request without taking it."""
    c = replace(C, tex_fill_request_window=window)
    dut = TmuNoTextureCache(c)
    trace = Trace()
    reqs = [Req(0x40 * k, (0, 0, 0, 0), bilinear=False, is16_bit=True) for k in range(window + 8)]
    gate = {"ready": False}

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        accepted = 0
        for r in reqs:
            ctx.set(dut.sample_request.payload, r.const(c))
            ctx.set(dut.sample_request.valid, 1)
            stalled = 0
            while not ctx.get(dut.sample_request.ready):
                await ctx.tick()
                stalled += 1
                if stalled > 40:
                    break
            if stalled > 40:
                break
            await ctx.tick()
            accepted += 1
        ctx.set(dut.sample_request.valid, 0)
        assert accepted == window, accepted
        assert trace.cmds == [], "nothing may be read while cmd.ready is low"
        assert ctx.get(dut.tex_read.cmd.valid) == 1, "the head request is presented"
        assert ctx.get(dut.busy) == 1
        gate["ready"] = True
        for r in reqs[accepted:]:
            await push(ctx, dut, r, c=c)
        await idle(ctx, dut, limit=5000)
        check_all(trace, reqs)

    run(dut, tb, word_responder(dut, cmd_ready=lambda: gate["ready"]),
        invariants(dut, trace), deadline=20_000)


def test_multi_beat_response_uses_only_the_last_beat():
    """``takeWhen(last)``: a slave that answers with 3 beats (``last`` on the third)
    gets the earlier two dropped; the texel is the last beat's word."""
    dut = TmuNoTextureCache(C)
    trace = Trace()
    reqs = [Req(0x80, (0, 0, 0, 0), bilinear=False, is16_bit=True),
            Req(0, (0x100, 0x102, 0x300, 0x302), bilinear=True, is16_bit=True)]

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        for r in reqs:
            await push(ctx, dut, r, c=C)
        await idle(ctx, dut)
        check_all(trace, reqs)

    run(dut, tb, word_responder(dut, beats=3), invariants(dut, trace))


def test_invalidate_is_a_no_op():
    """No epoch, no lines: pulsing ``invalidate`` every cycle changes nothing."""
    dut = TmuNoTextureCache(C)
    trace = Trace()
    rng = random.Random(5)
    reqs = [random_req(rng) for _ in range(40)]

    async def toggler(ctx):
        k = 0
        while True:
            ctx.set(dut.invalidate, k & 1)
            k += 1
            await ctx.tick()

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        for r in reqs:
            await push(ctx, dut, r, c=C)
        await idle(ctx, dut, limit=10_000)
        check_all(trace, reqs)

    run(dut, tb, toggler, word_responder(dut), invariants(dut, trace), deadline=30_000)


def test_busy_falls_when_idle_and_covers_the_whole_transaction():
    dut = TmuNoTextureCache(C)
    trace = Trace()
    req = Req(0, (0x1000, 0x2004, 0x0008, 0x300C), bilinear=True, is16_bit=True)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        for _ in range(5):
            assert ctx.get(dut.busy) == 0
            await ctx.tick()
        await push(ctx, dut, req, c=C)
        # accept -> fetch: busy is high on every cycle but the FIFO's latency dip
        highs = 0
        while not trace.fetches:
            highs += ctx.get(dut.busy)
            await ctx.tick()
        assert highs >= trace.fetch_cycles[0] - trace.accept_cycles[0] - 1
        await idle(ctx, dut)
        assert ctx.get(dut.busy) == 0

    run(dut, tb, word_responder(dut), invariants(dut, trace))


# ===========================================================================
# reset_less audit
# ===========================================================================


def test_reset_less_registers_match_spinal():
    """Plain ``Reg(...)`` in the Scala (no ``init``) must be ``reset_less`` here
    and every ``RegInit`` / ``init`` must reset (plan §6)."""
    from amaranth.hdl._ir import Fragment

    dut = TmuNoTextureCache(C)
    frag = Fragment.get(dut, None)
    regs: dict[str, bool] = {}

    def walk(f):
        for stmt_domain, stmts in f.statements.items():
            if stmt_domain != "sync":
                continue
            for s in stmts:
                for lhs in _lhs_signals(s):
                    regs[lhs.name] = lhs.reset_less
        for sub, _name, _src in f.subfragments:
            walk(sub)

    walk(frag)
    for name in ["expanded_hold", "assembled"]:
        assert name in regs, name
        assert regs[name], f"{name} is a plain Reg in the Scala and must be reset_less"
    for name in ["expanded_running", "expanded_pending", "outValid"]:
        assert name in regs, name
        assert not regs[name], f"{name} is a RegInit in the Scala and must reset"


def _lhs_signals(stmt):
    from amaranth.hdl._ast import Assign, Switch
    from amaranth.hdl._ast import Signal as _Sig

    if isinstance(stmt, Assign):
        for sig in stmt.lhs._lhs_signals():
            if isinstance(sig, _Sig):
                yield sig
    elif isinstance(stmt, Switch):
        for _patterns, stmts, _src in stmt.cases:
            for s in stmts:
                yield from _lhs_signals(s)


# ===========================================================================
# Tmu with the Console configuration (plan 8.0: the selection in tmu.py)
# ===========================================================================


def _tmu_console_run(tb, **kw):
    """``tests.test_tmu.run`` with ``Tmu(C_CONSOLE)`` instead of ``Tmu(Config.voodoo1())``."""
    from tests import test_tmu as T

    dut = Tmu(C_CONSOLE)
    assert isinstance(dut, Tmu)

    async def wrapper(ctx):
        d = T.Driver(ctx, dut, **kw)
        await d.init()
        await tb(d, dut)

    sim = Simulator(dut)
    sim.add_clock(T.PERIOD)
    sim.add_testbench(wrapper)
    sim.run()


def test_tmu_console_selects_the_direct_path():
    dut = Tmu(C_CONSOLE)
    from amaranth.hdl._ir import Fragment

    Fragment.get(dut, None)
    assert isinstance(dut.texture_path, TmuNoTextureCache)
    v1 = Tmu(C_V1)
    Fragment.get(v1, None)
    assert isinstance(v1.texture_path, TmuTextureCache)


@pytest.mark.parametrize("word,rgba", [
    (0xFFFF, (255, 255, 255, 255)),
    (0xF800, (255, 0, 0, 255)),
    (0x001F, (0, 0, 255, 255)),
])
def test_tmu_console_first_sample_decodes(word, rgba):
    """No cold-miss quirk on the direct path: the *first* sample of a solid RGB565
    texture decodes, with one 4-byte read (``length == 3``)."""
    from tests import test_tmu as T

    async def tb(d, dut):
        await d.push(T.pixel())
        await d.wait_outputs(1)
        assert d.outputs == [rgba]
        assert d.cmds == [(d.cmds[0][0], DIRECT_READ_LENGTH)]
        await d.push(T.pixel())
        await d.wait_outputs(2)
        assert d.outputs[1] == rgba
        assert len(d.cmds) == 2, "every sample reads memory: there is no cache"

    _tmu_console_run(tb, word=(word << 16) | word)


def test_tmu_console_bilinear_first_sample_blends_all_four_taps():
    """The cache's stale-tap quirk is absent: a cold bilinear sample of solid white is white."""
    from tests import test_tmu as T

    async def tb(d, dut):
        px = T.pixel(s=10.25, t=20.75, tm=T.texture_mode(bilinear=True))
        await d.push(px)
        await d.wait_outputs(1)
        assert d.outputs == [(255, 255, 255, 255)]
        # 2x2 16-bit taps on a 256-wide row: two words per row, two rows.
        assert len(d.cmds) == 4 and all(l == DIRECT_READ_LENGTH for _a, l in d.cmds)

    _tmu_console_run(tb, word=0xFFFF_FFFF)
