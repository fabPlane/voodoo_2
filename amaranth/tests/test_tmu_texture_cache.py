"""Phase 5 unit tests for :mod:`avoodoo.texture.tmu_texture_cache`.

Three sources of intent, all re-expressed on Amaranth's pysim:

1. ``test/TmuTextureCacheReuseTest.scala`` (three cases).  The Scala file is
   stale -- it drives ``pointBankSel`` / ``biBankSel*`` / ``lodEnd`` /
   ``passthrough.readIdx`` fields that ``Tmu.SampleRequest`` no longer has --
   so the cases are rebuilt on the current bundle: the per-tap
   ``tapPackedBank`` / ``tapPackedPair`` / ``tapStartupDecode`` are computed
   with the same ``packedLocation22`` arithmetic ``Tmu`` uses (the pure-Python
   copy in ``tests/diff/tmu_texture_cache_stimulus.py``).  Same
   configuration as the Scala: ``texFillCacheSlots = 2``,
   ``texFillRequestWindow = 4`` on top of ``Config.voodoo1()``.
2. ``test/TmuTextureCacheFormalTest.scala``: the ``cmd`` shape assertion
   (READ, ``last``, and here also ``length == lineWords * 4 - 1`` and a
   line-aligned address) is checked on every cycle of every test by
   :func:`invariants`, together with the stream hold rule on both output
   streams and ``io.busy``; the tracked-request covers become a directed
   miss -> fill -> response test, and the hot-init covers become
   ``test_formal_hot_init_hits_without_a_fill`` (``formal_hot_init=True``
   writes a valid+ready line at set 0 on the first cycle, so the tracked
   request must complete with no ``texRead.cmd`` ever and accept / respond
   on consecutive cycles).
3. A hit / miss / fill reference model (:class:`CacheModel`) that predicts,
   request by request, the exact sequence of line fills the cache issues --
   first-free way, else the set's rotating victim, one fill per unresolved
   tap in tap order, taps of the same line resolved by one fill, the epoch
   stamp -- and a deterministic address-hashed texture memory, so every
   ``sampleFetch`` beat is checked texel by texel against what the tap
   addresses hold, whatever the cache did.  Random traffic is drawn from a
   small working set so hits, set conflicts and victim rotation all occur.

Also here: the geometry constants for ``Config.voodoo1()``, invalidation
(the epoch stamp forces a refill of a line that is still present), and the
registered ``reset_less`` audit for the plain ``Reg`` registers of the Scala.

Run from ``amaranth/``: ``.venv/bin/python -m pytest -o addopts="" tests/test_tmu_texture_cache.py``.
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
from avoodoo.texture.tmu_texture_cache import TmuTextureCache, TmuTextureCacheGeometry
from avoodoo.texture.tmu_types import SampleRequest

# tests/diff/tmu_texture_cache_stimulus.py (the diff row's responder) holds
# the pure-Python packed-layout model; it imports tools.diff, so amaranth/
# has to be importable, as tests/diff/conftest.py arranges for the diff tests.
AM_ROOT = Path(__file__).resolve().parents[1]
if str(AM_ROOT) not in sys.path:
    sys.path.insert(0, str(AM_ROOT))
from tests.diff.tmu_texture_cache_stimulus import PackedLayout  # noqa: E402

C = Config.voodoo1()
#: the TmuTextureCacheReuseTest configuration
C_REUSE = replace(C, tex_fill_cache_slots=2, tex_fill_request_window=4)
CLK = 1e-6


# ===========================================================================
# request construction (what Tmu would hand the cache)
# ===========================================================================


@dataclass
class Req:
    """One ``SampleRequest`` plus what the model needs to know about it."""

    layout: PackedLayout
    lod: int
    s: int
    t: int
    bilinear: bool
    format: int = 0
    ds: int = 0
    dt: int = 0
    zero_ends: tuple[int, ...] = ()
    high: int = 0
    taps: list[int] = field(default_factory=list)

    def __post_init__(self):
        lay = self.layout
        n = 1 << lay.tex_shift[self.lod]

        def texel(ss, tt):
            return lay.tex_base[self.lod] + ((tt % n) * n + (ss % n)) * lay.bpp

        s, t = self.s, self.t
        self.taps = [a | self.high for a in
                     (texel(s, t), texel(s + 1, t), texel(s, t + 1), texel(s + 1, t + 1))]

    @property
    def active_taps(self) -> list[int]:
        return self.taps if self.bilinear else [self.taps[0]]

    def const(self, c: Config):
        lay = self.layout
        startup = []
        banks = []
        pairs = []
        for i, tap in enumerate(self.taps):
            _in, bank, pair = lay.location(tap)
            assert _in, (self.lod, self.s, self.t, i)
            banks.append(bank)
            pairs.append(pair)
            line = (tap >> 5) << 5
            v = p = 0
            bk = []
            for byte in range(4):
                vv, b_, pp = lay.location(line + byte)
                v |= vv << byte
                p |= pp << byte
                bk.append(b_)
            startup.append({"valid": v, "bank": bk, "pair": p})
        return SampleRequest(c).const({
            "point_addr": self.taps[0],
            "bi_addr0": self.taps[0],
            "bi_addr1": self.taps[1],
            "bi_addr2": self.taps[2],
            "bi_addr3": self.taps[3],
            "lod_base": lay.lod_base,
            "lod_shift": lay.lod_shift,
            "is16_bit": int(lay.is16_bit),
            "tex_tables": {
                "tex_base": list(lay.tex_base),
                "tex_end": [0 if i in self.zero_ends else e for i, e in enumerate(lay.tex_end)],
                "tex_shift": list(lay.tex_shift),
            },
            "tap_startup_decode": startup,
            "tap_packed_bank": banks,
            "tap_packed_pair": pairs,
            "bilinear": int(self.bilinear),
            "passthrough": {
                "format": self.format,
                "bilinear": int(self.bilinear),
                "ncc_table_select": 0,
                "ds": self.ds,
                "dt": self.dt,
            },
        })

    def aliased(self) -> bool:
        """``noTapBankAlias`` violated: two active taps in one bank at different addresses."""
        taps = self.active_taps
        banks = [self.layout.location(a)[1] for a in taps]
        for i in range(len(taps)):
            for j in range(i + 1, len(taps)):
                if banks[i] == banks[j] and taps[i] != taps[j]:
                    return True
        return False


LAY16 = PackedLayout(base=0x000000, is16_bit=True)
LAY8 = PackedLayout(base=0x100000, is16_bit=False)


# ===========================================================================
# texture memory + cache reference model
# ===========================================================================


def mem_word(word_addr: int) -> int:
    """Deterministic contents of the 32-bit word at ``word_addr`` (4-aligned)."""
    return ((word_addr >> 2) * 0x9E3779B1 + 0x2545F491) & 0xFFFFFFFF


def texel_at(addr: int, is16_bit: bool) -> int:
    """What ``bankTexel`` yields for a tap at ``addr`` after its line was filled."""
    word = mem_word(addr & ~3)
    if is16_bit:
        return (word >> 16) & 0xFFFF if addr & 2 else word & 0xFFFF
    byte = (word >> (8 * (addr & 3))) & 0xFF
    return byte | (byte << 8)


@dataclass
class Way:
    base: int = 0
    valid: bool = False
    ready: bool = False
    epoch: int = 0


class CacheModel:
    """Request-level model of the fill policy and of the response texels.

    Fills: one per unresolved active tap in tap order, first-free way else
    the set's rotating victim, taps of an already-filled line resolved by
    that fill, lines matched on (base, epoch).

    Texels: a tap that hit at compare, or whose word arrived on a beat
    *before* the response was emitted, carries the texture data.  The
    response is emitted on the beat that resolves the last outstanding tap
    (``fillFetchEmit = ownerFillCaptureMask.orR && continueDoneAll``) and its
    payload is ``ownerRspCommitted`` -- the ``owner.texels`` *registers*,
    loaded with 0 for every missed tap at ``compareConsumeMiss`` and only
    updated by that beat's ``fillRsp.fire`` -- so every tap resolved on the
    emitting beat goes out as 0.  For a point sample that is every miss.
    That is what the Scala does (``ownerRsp``, the version with the capture
    mux, is dead code there) and what ``TmuTextureCache_spinal.v``'s depth-1
    ``StreamFifo`` (an ``m2sPipe`` loading its payload on ``ready``) confirms;
    the lockstep diff pins it and the port reproduces it 1:1.
    """

    STALE = 0

    def __init__(self, c: Config) -> None:
        self.g = TmuTextureCacheGeometry(c)
        g = self.g
        self.sets = [[Way() for _ in range(g.way_count)] for _ in range(g.set_count)]
        self.victim = [0] * g.set_count
        self.epoch = 0
        self.fills: list[int] = []
        self.hits = 0
        self.misses = 0
        self.stale_taps = 0

    def invalidate(self) -> None:
        self.epoch = (self.epoch + 1) & 0xFF

    def _present(self, base: int) -> bool:
        s = self.g.set_int(base)
        return any(w.valid and w.ready and w.epoch == self.epoch and w.base == base
                   for w in self.sets[s])

    def _fill(self, base: int) -> None:
        s = self.g.set_int(base)
        ways = self.sets[s]
        free = [i for i, w in enumerate(ways) if not w.valid]
        way = free[0] if free else self.victim[s]
        ways[way] = Way(base=base, valid=True, ready=True, epoch=self.epoch)
        self.victim[s] = (way + 1) % (1 << self.g.way_bits)
        self.fills.append(base)

    def request(self, req: Req) -> list[int]:
        """Apply ``req``; return the four texels the response must carry."""
        g = self.g
        taps = req.active_taps
        lines = [g.line_base_int(a) for a in taps]
        entries = [g.bank_entry_int(a, b) for a, b in zip(taps, lines)]
        resolved = [self._present(b) for b in lines]
        when: list[tuple[int, int] | None] = [None] * len(taps)
        if all(resolved):
            self.hits += 1
        else:
            self.misses += 1
            fill_idx = 0
            for i, base in enumerate(lines):
                if resolved[i]:
                    continue
                self._fill(base)
                for j in range(i, len(lines)):
                    if lines[j] == base and not resolved[j]:
                        resolved[j] = True
                        when[j] = (fill_idx, entries[j])
                fill_idx += 1
        emit = max((t for t in when if t is not None), default=None)
        texels = []
        for i in range(4):
            if i >= len(taps):
                texels.append(0)
            elif when[i] is not None and when[i] == emit:
                texels.append(self.STALE)
                self.stale_taps += 1
            else:
                texels.append(texel_at(taps[i], req.layout.is16_bit))
        return texels


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
    fetches: list = field(default_factory=list)
    accept_cycles: list[int] = field(default_factory=list)
    fetch_cycles: list[int] = field(default_factory=list)
    cycle: int = 0


def invariants(dut: TmuTextureCache, trace: Trace, *, c: Config):
    """Per-cycle checks: the FormalTest asserts plus the stream hold rule."""
    g = TmuTextureCacheGeometry(c)
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
            if cv:
                assert opcode == CMD_READ
                assert last == 1
                assert length == g.fill_length
                assert address % g.line_bytes == 0, hex(address)
                assert busy, "a fill command with busy low"
                beat = (opcode, last, length, address)
                if last_cmd is not None:
                    assert beat == last_cmd, "texRead.cmd payload changed while stalled"
                last_cmd = None if cr else beat
                if cr:
                    trace.cmds.append(address)
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


def line_responder(dut: TmuTextureCache, *, c: Config, latency=lambda: 0, gap=lambda: 0):
    """Answer each ``texRead.cmd`` with ``lineWords`` beats of :func:`mem_word`.

    Two coroutines: a watcher that queues every accepted command (``cmd.ready``
    is held high, so a beat fires whenever ``valid`` is sampled high -- and it
    can fire while the driver is still pacing the previous burst, which is why
    the watcher is separate), and a driver that plays the bursts back in order
    with ``latency()`` idle cycles before each and ``gap()`` between beats.
    """
    g = TmuTextureCacheGeometry(c)
    cmd = dut.tex_read.cmd
    rsp = dut.tex_read.rsp
    pending: list[int] = []

    async def watcher(ctx):
        ctx.set(cmd.ready, 1)
        async for _clk, _rst, valid, address in ctx.tick().sample(
                cmd.valid, cmd.payload.fragment.address):
            if valid:
                pending.append(address)

    async def driver(ctx):
        while True:
            if not pending:
                await ctx.tick()
                continue
            address = pending.pop(0)
            for _ in range(latency()):
                await ctx.tick()
            for k in range(g.line_words):
                ctx.set(rsp.payload.fragment.data, mem_word(address + 4 * k))
                ctx.set(rsp.payload.last, int(k == g.line_words - 1))
                ctx.set(rsp.valid, 1)
                await ctx.tick().until(rsp.ready)
                ctx.set(rsp.valid, 0)
                for _ in range(gap()):
                    await ctx.tick()

    return watcher, driver


class Board:
    """Runs :class:`CacheModel` alongside the DUT and remembers what to expect."""

    def __init__(self, c: Config) -> None:
        self.c = c
        self.model = CacheModel(c)
        self.expect: list[tuple[Req, list[int]]] = []

    def add(self, req: Req) -> None:
        self.expect.append((req, self.model.request(req)))

    def check(self, trace: Trace, *, count: int | None = None) -> None:
        n = len(self.expect) if count is None else count
        assert len(trace.fetches) == n, (len(trace.fetches), n)
        for f, (r, texels) in zip(trace.fetches, self.expect):
            check_fetch(f, r, texels)
        assert trace.cmds == self.model.fills[:len(trace.cmds)], (trace.cmds, self.model.fills)
        assert len(trace.cmds) == len(self.model.fills)


async def push(ctx, dut: TmuTextureCache, req: Req, *, c: Config, board: Board | None = None):
    if board is not None:
        board.add(req)
    ctx.set(dut.sample_request.payload, req.const(c))
    ctx.set(dut.sample_request.valid, 1)
    await ctx.tick().until(dut.sample_request.ready)
    ctx.set(dut.sample_request.valid, 0)


async def idle(ctx, dut: TmuTextureCache, *, limit: int = 2000):
    """Wait until ``busy`` has been low for three cycles.

    ``io.busy`` is ``reqStream.valid | ...``, and the request FIFO (Spinal
    ``StreamFifo``, latency 2) raises ``pop.valid`` two cycles after a push
    into an empty queue, so ``busy`` drops for one cycle right after an
    accept; the lockstep diff pins that (``io_busy`` is compared), so the
    helper tolerates it instead of the port hiding it.
    """
    low = 0
    for _ in range(limit):
        low = low + 1 if not ctx.get(dut.busy) else 0
        if low >= 3:
            return
        await ctx.tick()
    raise AssertionError("cache did not go idle")


def run(dut, *procs, deadline: int = 20_000):
    sim = Simulator(_Clocked(dut))
    sim.add_clock(CLK)
    for p in procs:
        for q in (p if isinstance(p, tuple) else (p,)):
            sim.add_testbench(q)
    sim.run_until(deadline * CLK)


def check_fetch(fetch, req: Req, texels: list[int]) -> None:
    """A ``sampleFetch`` beat carries the request's passthrough and the modelled texels."""
    assert fetch.bilinear == int(req.bilinear)
    assert fetch.passthrough.format == req.format
    assert fetch.passthrough.ds == req.ds and fetch.passthrough.dt == req.dt
    assert fetch.passthrough.bilinear == int(req.bilinear)
    got = list(fetch.texels)
    assert got == texels, ([hex(t) for t in got], [hex(t) for t in texels],
                           [hex(a) for a in req.taps])


# ===========================================================================
# geometry
# ===========================================================================


def test_geometry_voodoo1():
    g = TmuTextureCacheGeometry(C)
    assert (g.line_words, g.line_bytes, g.line_byte_shift) == (8, 32, 5)
    assert (g.slot_count, g.slot_bits, g.way_count, g.way_bits) == (16, 4, 2, 1)
    assert (g.set_count, g.set_bits, g.bank_entry_width) == (8, 3, 3)
    assert g.fill_length == 31 and g.rsp_count_width == 4
    assert not g.xor_index
    # set index = line number's low 3 bits; entries = word within the line
    assert g.set_int(0x20 * 9) == 1 and g.set_int(0x20 * 16) == 0
    assert g.bank_entry_int(0x2C, 0x20) == 3
    assert g.slot_int(5, 1) == 11
    assert g.line_base_int(0x12345) == 0x12340


def test_geometry_rejects_bad_configs():
    with pytest.raises(ValueError):
        TmuTextureCacheGeometry(replace(C, use_tex_fill_cache=False))
    with pytest.raises(ValueError):
        TmuTextureCacheGeometry(replace(C, tex_fill_line_words=6))
    with pytest.raises(ValueError):
        TmuTextureCacheGeometry(replace(C, tex_fill_cache_slots=3))


# ===========================================================================
# TmuTextureCacheReuseTest
# ===========================================================================


def test_reuse_point_word_hits_second_time():
    """``packed cache reuses a filled point word``: two identical point requests, one fill."""
    dut = TmuTextureCache(C_REUSE)
    trace = Trace()
    board = Board(C_REUSE)
    req = Req(LAY16, 0, 0, 0, False, format=3)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C_REUSE, board=board)
        await idle(ctx, dut)
        assert len(trace.cmds) == 1 and len(trace.fetches) == 1
        await push(ctx, dut, req, c=C_REUSE, board=board)
        await idle(ctx, dut)
        assert len(trace.cmds) == 1, f"second request should hit, saw {len(trace.cmds)} fills"
        board.check(trace)
        # the miss answered with the stale register (see CacheModel), the hit
        # with the texel the fill stored
        assert list(trace.fetches[0].texels)[0] == CacheModel.STALE
        assert list(trace.fetches[1].texels)[0] == texel_at(req.taps[0], True)

    run(dut, tb, line_responder(dut, c=C_REUSE), invariants(dut, trace, c=C_REUSE))


def test_reuse_bilinear_hit_after_point_fills():
    """``packed cache returns bilinear sample in one response on hit``.

    Four point requests at the four bilinear tap addresses of (0, 0) fill the
    two lines involved (rows 0 and 1 of a 256-wide 16-bit texture are 512
    bytes apart); the bilinear request then hits on all four taps with no
    further fill and returns all four texels in one beat.
    """
    dut = TmuTextureCache(C_REUSE)
    trace = Trace()
    board = Board(C_REUSE)
    points = [Req(LAY16, 0, s, t, False) for s, t in ((0, 0), (1, 0), (0, 1), (1, 1))]
    bil = Req(LAY16, 0, 0, 0, True, format=5, ds=7, dt=9)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        for r in points:
            await push(ctx, dut, r, c=C_REUSE, board=board)
            await idle(ctx, dut)
        assert len(trace.cmds) == 2, trace.cmds
        await push(ctx, dut, bil, c=C_REUSE, board=board)
        await idle(ctx, dut)
        assert len(trace.cmds) == 2, f"bilinear hit took {len(trace.cmds) - 2} extra fills"
        board.check(trace)
        assert list(trace.fetches[4].texels) == [texel_at(a, True) for a in bil.taps]

    run(dut, tb, line_responder(dut, c=C_REUSE), invariants(dut, trace, c=C_REUSE))


def test_reuse_bilinear_hits_back_to_back():
    """``keeps bilinear hit path ready across consecutive cycles``: two accepts in a row."""
    dut = TmuTextureCache(C_REUSE)
    trace = Trace()
    board = Board(C_REUSE)
    points = [Req(LAY16, 0, s, t, False) for s, t in ((0, 0), (1, 0), (0, 1), (1, 1))]
    bil = Req(LAY16, 0, 0, 0, True)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        for r in points:
            await push(ctx, dut, r, c=C_REUSE, board=board)
            await idle(ctx, dut)
        board.add(bil)
        board.add(bil)
        ctx.set(dut.sample_request.payload, bil.const(C_REUSE))
        ctx.set(dut.sample_request.valid, 1)
        await ctx.tick()
        first = ctx.get(dut.sample_request.ready)
        await ctx.tick()
        second = ctx.get(dut.sample_request.ready)
        ctx.set(dut.sample_request.valid, 0)
        assert first and second, (first, second)
        await idle(ctx, dut)
        assert len(trace.cmds) == 2
        board.check(trace)
        # the two hits came out on consecutive cycles too
        assert trace.fetch_cycles[5] == trace.fetch_cycles[4] + 1

    run(dut, tb, line_responder(dut, c=C_REUSE), invariants(dut, trace, c=C_REUSE))


# ===========================================================================
# TmuTextureCacheFormalTest
# ===========================================================================


def test_formal_tracked_request_miss_fill_response():
    """The tracked-request covers: accept, miss, cmd, rsp, last rsp, complete."""
    dut = TmuTextureCache(C)
    trace = Trace()
    board = Board(C)
    req = Req(LAY8, 0, 3, 5, False, format=1)
    rsp = dut.tex_read.rsp
    beats = []

    async def watch(ctx):
        async for _clk, _rst, v, r, last in ctx.tick().sample(rsp.valid, rsp.ready,
                                                              rsp.payload.last):
            if v and r:
                beats.append(last)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C, board=board)
        await idle(ctx, dut)
        assert trace.cmds == [TmuTextureCacheGeometry(C).line_base_int(req.taps[0])]
        assert beats == [0] * 7 + [1]
        board.check(trace)

    run(dut, tb, watch, line_responder(dut, c=C, latency=lambda: 3), invariants(dut, trace, c=C))


def test_formal_hot_init_hits_without_a_fill():
    """The hit-throughput covers: ``formalHotInit`` seeds set 0 / way 0 with a
    valid+ready line at base 0, epoch 0, so requests at address 0 hit from the
    first cycle, never raise ``texRead.cmd``, and are accepted and answered on
    consecutive cycles (point and bilinear alike)."""
    c = replace(C, address_width=5, mem_burst_length_width=4, tex_fill_line_words=4,
                tex_fill_cache_slots=2, tex_fill_request_window=1)
    lay = PackedLayout(base=0, is16_bit=True, top_log2=2)  # 4x4 texels = 32 bytes = one line
    for bilinear in (False, True):
        dut = TmuTextureCache(c, formal_strong=False, formal_hot_init=True)
        trace = Trace()
        req = Req(lay, 0, 0, 0, bilinear)

        async def tb(ctx, dut=dut, req=req, trace=trace):
            ctx.set(dut.sample_fetch.ready, 1)
            ctx.set(dut.tex_read.cmd.ready, 1)
            await ctx.tick()
            ctx.set(dut.sample_request.payload, req.const(c))
            ctx.set(dut.sample_request.valid, 1)
            for _ in range(3):
                await ctx.tick()
            ctx.set(dut.sample_request.valid, 0)
            await idle(ctx, dut)
            assert trace.cmds == [], "hot-init line must hit without a fill"
            assert len(trace.accept_cycles) == 3
            assert trace.accept_cycles[1] == trace.accept_cycles[0] + 1
            assert len(trace.fetches) == 3
            assert trace.fetch_cycles[1] == trace.fetch_cycles[0] + 1
            # never filled, so the data is whatever the zeroed bank RAM holds
            for f in trace.fetches:
                assert f.bilinear == int(req.bilinear)
                assert all(t == 0 for t in f.texels)

        run(dut, tb, invariants(dut, trace, c=c))


# ===========================================================================
# invalidation and replacement
# ===========================================================================


def test_invalidate_bumps_epoch_and_forces_refill():
    dut = TmuTextureCache(C)
    trace = Trace()
    board = Board(C)
    req = Req(LAY16, 2, 4, 4, True)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        await push(ctx, dut, req, c=C, board=board)
        await idle(ctx, dut)
        n = len(trace.cmds)
        assert n == 2, trace.cmds  # two rows of a 64-wide 16-bit LOD: two lines
        await push(ctx, dut, req, c=C, board=board)
        await idle(ctx, dut)
        assert len(trace.cmds) == n, "re-request must hit"
        ctx.set(dut.invalidate, 1)
        await ctx.tick()
        ctx.set(dut.invalidate, 0)
        board.model.invalidate()
        await push(ctx, dut, req, c=C, board=board)
        await idle(ctx, dut)
        assert len(trace.cmds) == 2 * n, "after invalidate every line must be refilled"
        assert trace.cmds[n:] == trace.cmds[:n]
        board.check(trace)
        # taps 0/1 (first line, resolved by the first fill) carry data on the
        # miss; taps 2/3 (second line) are resolved on the emitting beat: stale
        miss, hit = list(trace.fetches[0].texels), list(trace.fetches[1].texels)
        assert miss[:2] == hit[:2] and miss[2:] == [CacheModel.STALE] * 2
        assert hit == [texel_at(a, True) for a in req.taps]

    run(dut, tb, line_responder(dut, c=C), invariants(dut, trace, c=C))


def test_victim_rotation_in_one_set():
    """Three lines in one set of two ways: free way 0, free way 1, then the
    victim pointer (way 0), then way 1 again; the two most recent stay."""
    dut = TmuTextureCache(C)
    trace = Trace()
    board = Board(C)
    g = TmuTextureCacheGeometry(C)
    # rows of the 16-bit LOD 0 texture are 512 bytes = 16 lines apart -> same set
    reqs = [Req(LAY16, 0, 0, t, False) for t in range(4)]
    assert len({g.set_int(g.line_base_int(r.taps[0])) for r in reqs}) == 1

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        for r in reqs:
            await push(ctx, dut, r, c=C, board=board)
            await idle(ctx, dut)
        assert len(trace.cmds) == 4
        # lines of t=2 and t=3 are resident, t=0 and t=1 were evicted
        for r, fills in ((reqs[3], 0), (reqs[2], 0), (reqs[0], 1), (reqs[1], 1)):
            before = len(trace.cmds)
            await push(ctx, dut, r, c=C, board=board)
            await idle(ctx, dut)
            assert len(trace.cmds) - before == fills, (r.t, trace.cmds)
        board.check(trace)

    run(dut, tb, line_responder(dut, c=C), invariants(dut, trace, c=C))


def test_tex_end_fallback_uses_next_lod_base():
    """A raw ``texEnd`` of 0 falls back to the next LOD's base (LOD 8: base +
    texel size), which for a contiguous chain is the true end, so the taps
    still decode and the request completes with correct texels."""
    dut = TmuTextureCache(C)
    trace = Trace()
    board = Board(C)
    reqs = [Req(LAY8, 1, 100, 60, True, zero_ends=(0, 1, 2)),
            Req(LAY16, 8, 0, 0, True, zero_ends=(8,)),
            Req(LAY8, 7, 1, 1, True, zero_ends=tuple(range(9)))]
    # (a 16-bit 2x2 LOD cannot be sampled bilinearly without two taps sharing
    # a bank -- `offs[shift] | offs[shift+1]` folds s and t together -- and
    # the hit path then ORs the two bank words; that is the formal
    # `assume(noTapBankAlias)`, so the directed cases stay alias-free)
    assert not any(r.aliased() for r in reqs)

    async def tb(ctx):
        ctx.set(dut.sample_fetch.ready, 1)
        await ctx.tick()
        for r in reqs:
            await push(ctx, dut, r, c=C, board=board)
            await idle(ctx, dut)
            # and once more, now a hit: the data the fill stored is right
            await push(ctx, dut, r, c=C, board=board)
            await idle(ctx, dut)
        board.check(trace)
        for f, r in zip(trace.fetches[1::2], reqs):
            assert list(f.texels) == [texel_at(a, r.layout.is16_bit) for a in r.taps]

    run(dut, tb, line_responder(dut, c=C), invariants(dut, trace, c=C))


# ===========================================================================
# random traffic against the model
# ===========================================================================


def _draw_requests(rng: random.Random, count: int) -> list[Req]:
    """A working set of alias-free requests over both layouts."""
    reqs = []
    hot = {}
    while len(reqs) < count:
        lay = rng.choice((LAY16, LAY8))
        key = id(lay)
        if key not in hot or rng.random() < 0.05:
            lod = rng.choices(range(9), weights=[12, 12, 12, 12, 12, 10, 10, 10, 10])[0]
            n = 1 << lay.tex_shift[lod]
            hot[key] = (lod, rng.randrange(n), rng.randrange(n))
        lod, hs, ht = hot[key]
        n = 1 << lay.tex_shift[lod]
        span = rng.choice((0, 1, 2, 3, 6))
        s = (hs + rng.randint(-span, span)) % n
        t = (ht + rng.randint(-span, span)) % n
        high = (rng.getrandbits(4) << 22) if rng.random() < 0.1 else 0
        zero_ends = tuple(i for i in range(9) if rng.random() < 0.15)
        req = Req(lay, lod, s, t, rng.random() < 0.5, format=rng.getrandbits(4),
                  ds=rng.getrandbits(4), dt=rng.getrandbits(4), zero_ends=zero_ends, high=high)
        # The formal assume(noTapBankAlias): a bilinear request whose taps
        # share a bank at different addresses reads an OR of two bank words
        # on a hit.  Tmu's caller-side layout avoids it; the model checks
        # texels, so skip such draws (the diff test covers them in lockstep).
        if req.aliased():
            continue
        reqs.append(req)
    return reqs


@pytest.mark.parametrize("seed,stall", [(1, False), (2, True)])
def test_random_traffic_matches_model(seed, stall):
    """120 requests from a working set against the model; ``stall`` adds
    random fill latency, gaps between beats and 50 % ``sampleFetch.ready``."""
    rng = random.Random(f"tmu-cache-{seed}-{stall}")
    reqs = _draw_requests(rng, 120)
    dut = TmuTextureCache(C)
    trace = Trace()
    board = Board(C)
    model = board.model
    invalidate_at = {rng.randrange(len(reqs)) for _ in range(3)}

    lat = (lambda: rng.randint(0, 4)) if stall else (lambda: 0)
    gap = (lambda: rng.choice((0, 0, 1, 3))) if stall else (lambda: 0)

    async def fetch_ready(ctx):
        # random back-pressure on the response stream
        while True:
            ctx.set(dut.sample_fetch.ready, 1 if (not stall or rng.random() < 0.5) else 0)
            await ctx.tick()

    async def tb(ctx):
        await ctx.tick()
        for i, r in enumerate(reqs):
            if i in invalidate_at:
                # only when idle, so the epoch stamp is unambiguous
                await idle(ctx, dut)
                ctx.set(dut.invalidate, 1)
                await ctx.tick()
                ctx.set(dut.invalidate, 0)
                model.invalidate()
            await push(ctx, dut, r, c=C, board=board)
            if rng.random() < 0.3:
                for _ in range(rng.randint(1, 6)):
                    await ctx.tick()
        await idle(ctx, dut, limit=20_000)

    run(dut, tb, fetch_ready, line_responder(dut, c=C, latency=lat, gap=gap),
        invariants(dut, trace, c=C), deadline=200_000)

    board.check(trace)
    assert model.hits > 10 and model.misses > 10, (model.hits, model.misses)
    assert model.stale_taps > 10


# ===========================================================================
# reset_less audit
# ===========================================================================


def test_reset_less_registers_match_spinal():
    """Plain ``Reg(...)`` in the Scala (no ``init``) must be ``reset_less``
    here and every ``RegInit`` / ``init`` must reset (plan §6); the lockstep
    driver resets before every seed, so a mismatch shows on seed 2."""
    from amaranth.hdl._ir import Fragment

    dut = TmuTextureCache(C)
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
    plain = ["compareHold", "lookupRspCtx", "owner_sample", "owner_tapMeta_0", "owner_texels_3",
             "fill_setMeta", "fill_req", "fill_passthrough", "fill_texBase_8", "fill_texEnd_0",
             "fill_texShift_4"]
    with_init = ["currentEpoch", "compareHoldValid", "continuePendingValid",
                 "continuePendingIdx", "owner_active", "owner_epoch", "owner_resolved",
                 "fill_activeReg", "fill_slot", "fill_set", "fill_way", "fill_rspCount",
                 "fill_decodeValid_0", "fill_decodeBank_1", "fill_decodePair_2",
                 "lookupRspValid", "initSweepActive", "initSweepSet", "formalHotInitDone"]
    for name in plain:
        assert name in regs, name
        assert regs[name], f"{name} is a plain Reg in the Scala and must be reset_less"
    for name in with_init:
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
