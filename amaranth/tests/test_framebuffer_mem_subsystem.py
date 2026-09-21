"""Phase 6 unit tests for :mod:`avoodoo.core.framebuffer_mem_subsystem` and the
``FramebufferLayout`` / ``FramebufferMemStats`` layouts in ``core_types``.

Upstream has no ``FramebufferMemSubsystemTest.scala``; the module is a wiring
harness and is covered through the Glide suite and, here, the lockstep diff
(``tests/diff/test_diff_framebuffer_mem_subsystem.py``).  These tests pin
what the Scala source *promises* rather than what the diff merely agrees on:

1. **Routing.**  Each master reaches the right ``fbMem`` port with the right
   route tag: colour/aux writes come out of ``fbColorWriteMem`` /
   ``fbAuxWriteMem`` (``source = 0`` widened through the one-input bridge,
   single-beat, ``length = 3``), the colour read port carries scanout
   (route 0), the colour reader (1) and ``lfbReadBus`` (2) in
   ``source[1:0]``, and responses demux back by the same bits -- the LFB
   read's data and the readers' lanes come out where they went in.
2. **Arbitration.**  ``fragmentLock.lowerFirst`` on the colour read port:
   the grant locks on whoever was offered first until its ``last`` beat
   fires, then scanout (0) beats the colour reader (1) beats the LFB (2).
3. **Tie-offs and flush.**  ``Config.voodoo1()`` (no write buffer) ties
   ``fbMemWrite`` off (``valid = 0``, ``opcode = WRITE``, ``last``,
   ``rsp.ready``) and feeds the plane buffers ``flush = False`` whatever
   ``io.flush`` does; the write-buffer configuration does the opposite (both
   per-plane write ports tied off, ``flush`` passed through, both writers on
   ``fbMemWrite`` with the chosen input in ``source[0]``).
4. **Counters and sums, per cycle.**  A Python model of the ten
   ``mem*CmdCount`` / ``mem*BlockedCycles`` registers is stepped from the
   masters' ``cmd.valid``/``ready`` every cycle and compared with
   ``io.stats``; every ``stats.*`` sum equals the two components' ports
   added mod 2**32; the write-buffer-only and cache-only fields are zero in
   the configurations that do not build them; ``readMaxQueueOccupancy`` is
   the larger ``maxOccupancy``.
5. **Configurations.**  All four ``(useFbWriteBuffer, useFbReadCache)``
   combinations elaborate with the same port list; the direct-reader one
   drops the prefetch streams (``ready = True``) and zeroes the cache stats.
6. **``FramebufferLayout.fromRegisterBank``** against an integer model over
   random register values, including the ``|<< 12`` and ``resized`` wraps.
"""

from __future__ import annotations

import random
from functools import partial
from collections import deque
from dataclasses import replace
from types import SimpleNamespace

import pytest
from amaranth.hdl import ClockDomain, Elaboratable, Fragment, Module, Signal
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.core.core_types import (FRAMEBUFFER_MEM_STAT_NAMES, FbRouting, FramebufferLayout,
                                     FramebufferMemStats, FramebufferMemStatus,
                                     framebuffer_layout_from_register_bank)
from avoodoo.core.framebuffer_mem_subsystem import (STAT_SUMS_CACHED, STAT_SUMS_WRITE_BUFFER,
                                                    FramebufferMemSubsystem)
from avoodoo.core.membus_params import fb_mem_bmb_params
from avoodoo.lib.membus import CMD_READ, CMD_WRITE, RSP_SUCCESS, cmd_layout, rsp_layout

C = Config.voodoo1()
CLK = 1e-6
FB_P = fb_mem_bmb_params(C)
AW = C.address_width
ROUTE_SCANOUT, ROUTE_COLOR, ROUTE_LFB = 0, 1, 2


# ===========================================================================
# helpers
# ===========================================================================


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


class Memory:
    """A little-endian byte image read as 32-bit words and 16-bit lanes."""

    def __init__(self, size: int, rng: random.Random):
        self.mem = bytearray(rng.randbytes(size))

    def word(self, addr: int) -> int:
        a = addr & ~3
        return int.from_bytes(self.mem[a:a + 4], "little")

    def lane(self, addr: int) -> int:
        a = addr & ~1
        return int.from_bytes(self.mem[a:a + 2], "little")

    def write(self, addr: int, data: int, mask: int) -> None:
        a = addr & ~3
        for lane in range(4):
            if mask >> lane & 1:
                self.mem[a + lane] = (data >> (8 * lane)) & 0xFF


class MemSlave:
    """In-order ``MemBus`` slave (reads and writes) on one ``fbMem`` port.

    Mirrors ``SimBmbMemory``: a read command yields its beats, a write burst
    is consumed beat by beat and acknowledged once after ``last``.  ``source``
    is echoed, which is what the arbiters route on.
    """

    def __init__(self, mem: Memory, p, rng: random.Random, *, cmd_ready_density=1.0,
                 rsp_valid_density=1.0, size_mask: int | None = None):
        self.mem = mem
        self.p = p
        self.rng = rng
        self.cmd_ready_density = cmd_ready_density
        self.rsp_valid_density = rsp_valid_density
        self.size_mask = (len(mem.mem) - 1) if size_mask is None else size_mask
        self.cmd_log: list[dict] = []
        self.rsp_log: list[dict] = []
        self.cmd_members = list(cmd_layout(p)["fragment"].shape.members)
        self.rsp_layout = rsp_layout(p)
        self._in_write = None

    def _rsp(self, **kw):
        has = self.rsp_layout["fragment"].shape.members
        return self.rsp_layout.const({
            "last": kw.pop("last"),
            "fragment": {k: v for k, v in kw.items() if k in has},
        })

    def on(self, bus):
        """A background testbench serving ``bus``."""

        async def tb(ctx):
            await self.run(ctx, bus)

        return tb

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
            _clk, _rst, cmd_valid, cmd_last, rsp_ready, *frag = await ctx.tick().sample(
                bus.cmd.valid, bus.cmd.payload.last, bus.rsp.ready,
                *[bus.cmd.payload.fragment[k] for k in self.cmd_members])
            if cmd_valid and rdy:
                beat = dict(zip(self.cmd_members, frag))
                beat["last"] = cmd_last
                self.cmd_log.append(beat)
                addr = beat["address"] & self.size_mask
                if beat["opcode"] == CMD_READ:
                    base = addr & ~3
                    beats = ((addr & 3) + beat.get("length", 0)) // 4 + 1
                    for i in range(beats):
                        pending.append(dict(last=int(i == beats - 1), opcode=RSP_SUCCESS,
                                            data=self.mem.word((base + 4 * i) & self.size_mask),
                                            source=beat.get("source", 0)))
                else:
                    if self._in_write is None:
                        self._in_write = beat.get("source", 0)
                    self.mem.write(addr, beat["data"], beat["mask"])
                    if cmd_last:
                        pending.append(dict(last=1, opcode=RSP_SUCCESS, data=0,
                                            source=self._in_write))
                        self._in_write = None
            if holding is not None and rsp_ready:
                self.rsp_log.append(holding)
                holding = None
                ctx.set(bus.rsp.valid, 0)


async def _stream_push(ctx, s, payloads):
    """Push a list of payload consts through an input stream, one per beat."""
    for p in payloads:
        ctx.set(s.payload, p)
        ctx.set(s.valid, 1)
        await ctx.tick().until(s.ready)
    ctx.set(s.valid, 0)


def _collect(s, out, *, ready=1):
    """A background testbench accepting every beat of ``s`` into ``out``."""

    async def tb(ctx):
        ctx.set(s.ready, ready)
        async for _clk, _rst, valid, rdy, payload in ctx.tick().sample(s.valid, s.ready, s.payload):
            if valid and rdy:
                out.append(payload)

    return tb


def _write_req(addr, data, mask):
    from avoodoo.core.core_types import FramebufferWriteReq
    return FramebufferWriteReq(C).const({"address": addr, "data": data, "mask": mask})


def _read_req(addr):
    from avoodoo.core.core_types import FramebufferReadReq
    return FramebufferReadReq(C).const({"address": addr})


def _prefetch(start, end):
    from avoodoo.core.core_types import FramebufferPrefetchReq
    return FramebufferPrefetchReq(C).const({"start_address": start, "end_address": end})


def _lfb_cmd(p, *, opcode, address, length=3, source=0, data=0, mask=0, last=1):
    lay = cmd_layout(p)
    has = lay["fragment"].shape.members
    frag = {"source": source, "opcode": opcode, "address": address, "length": length,
            "data": data, "mask": mask}
    return lay.const({"last": last, "fragment": {k: v for k, v in frag.items() if k in has}})


def _configs():
    return {
        "voodoo1": C,
        "wb": replace(C, use_fb_write_buffer=True, fb_write_buffer_line_words=16),
        "direct": replace(C, use_fb_read_cache=False),
        "wb_direct": replace(C, use_fb_write_buffer=True, fb_write_buffer_line_words=16,
                             use_fb_read_cache=False),
    }


# ===========================================================================
# 5. configurations and the port surface
# ===========================================================================


EXPECTED_PORTS = {
    "color_write", "aux_write", "color_read_req", "color_read_rsp", "scanout_prefetch_req",
    "scanout_read_req", "scanout_read_rsp", "aux_read_req", "aux_read_rsp", "prefetch_color",
    "prefetch_aux", "lfb_read_bus", "flush", "fb_mem_write", "fb_color_write_mem",
    "fb_aux_write_mem", "fb_color_read_mem", "fb_aux_read_mem", "status", "stats",
    "scanout_cache_debug", "scanout_cache_read_addr", "scanout_cache_expected_addr",
    "scanout_cache_remaining", "scanout_cache_occupancy", "scanout_fill_hits",
    "scanout_fill_misses", "scanout_fill_burst_count", "scanout_fill_burst_beats",
    "scanout_fill_stall_cycles",
}


@pytest.mark.parametrize("name", list(_configs()))
def test_every_configuration_elaborates_with_the_same_ports(name):
    cfg = _configs()[name]
    dut = FramebufferMemSubsystem(cfg)
    assert set(dut.signature.members) == EXPECTED_PORTS
    assert len(dut.stats.as_value()) == FramebufferMemStats().size
    assert len(dut.scanout_cache_remaining) == 10          # log2Up(800 + 1)
    Fragment.get(dut, None)
    assert (dut.fb_write_arbiter is not None) == cfg.use_fb_write_buffer
    assert (dut.fb_color_write_bridge is None) == cfg.use_fb_write_buffer
    assert dut.fb_color_read_arbiter.port_count == 3
    assert dut.fb_aux_read_arbiter.port_count == 1
    assert dut.scanout_reader.allow_direct_miss is False
    from avoodoo.framebuffer.plane_direct_reader import FramebufferPlaneDirectReader
    from avoodoo.framebuffer.plane_reader import FramebufferPlaneReader
    kind = FramebufferPlaneReader if cfg.use_fb_read_cache else FramebufferPlaneDirectReader
    assert isinstance(dut.color_reader, kind) and isinstance(dut.aux_reader, kind)
    if cfg.use_fb_read_cache:
        assert dut.color_reader.allow_direct_miss and dut.aux_reader.allow_direct_miss


def test_stats_layout_matches_the_scala_declaration():
    assert len(FRAMEBUFFER_MEM_STAT_NAMES) == 39
    assert FramebufferMemStats().size == 38 * 32 + 8
    assert FramebufferMemStatus().size == 2
    names = list(FramebufferMemStats().members)
    assert names == list(FRAMEBUFFER_MEM_STAT_NAMES)
    assert names[0] == "fill_hits" and names[-1] == "read_max_queue_occupancy"
    summed = {f for f, _ in STAT_SUMS_CACHED} | set(STAT_SUMS_WRITE_BUFFER)
    assert summed <= set(names)
    assert FramebufferLayout(C).size == 3 * AW + 3 * FbRouting(C).size + 2 + 3


# ===========================================================================
# 1 / 2 / 4. the voodoo1 configuration end to end
# ===========================================================================


class _Counters:
    """The ten ``mem*`` registers, stepped once per cycle from the masters."""

    NAMES = ("mem_color_write_cmd_count", "mem_aux_write_cmd_count",
             "mem_color_read_cmd_count", "mem_aux_read_cmd_count", "mem_lfb_read_cmd_count",
             "mem_color_write_blocked_cycles", "mem_aux_write_blocked_cycles",
             "mem_color_read_blocked_cycles", "mem_aux_read_blocked_cycles",
             "mem_lfb_read_blocked_cycles")

    def __init__(self):
        self.v = dict.fromkeys(self.NAMES, 0)

    def step(self, *, cw, aw_, cr, ar, lfb):
        """Each argument is ``(master_valid, master_ready, arbiter_input_ready)``."""
        for tag, (valid, ready, arb_ready) in (("color_write", cw), ("aux_write", aw_),
                                               ("color_read", cr), ("aux_read", ar),
                                               ("lfb_read", lfb)):
            if valid and not arb_ready:
                self.v[f"mem_{tag}_blocked_cycles"] = (self.v[f"mem_{tag}_blocked_cycles"] + 1) & 0xFFFFFFFF
            if valid and ready:
                self.v[f"mem_{tag}_cmd_count"] = (self.v[f"mem_{tag}_cmd_count"] + 1) & 0xFFFFFFFF


def _invariants_tb(dut, cfg, log):
    """Per-cycle: the counters against the model, the sums, the tie-offs."""
    cw = dut.color_write_port.mem.cmd
    aw_ = dut.aux_write_port.mem.cmd
    cr = dut.color_reader.mem.cmd
    ar = dut.aux_reader.mem.cmd
    lfb = dut.lfb_read_bus.cmd
    if cfg.use_fb_write_buffer:
        cw_in, aw_in = dut.fb_write_arbiter.inputs[0].cmd, dut.fb_write_arbiter.inputs[1].cmd
    else:
        cw_in, aw_in = dut.fb_color_write_bridge.inputs[0].cmd, dut.fb_aux_write_bridge.inputs[0].cmd
    cr_in = dut.fb_color_read_arbiter.inputs[1].cmd
    ar_in = dut.fb_aux_read_arbiter.inputs[0].cmd
    lfb_in = dut.fb_color_read_arbiter.inputs[2].cmd
    cached = cfg.use_fb_read_cache
    wb = cfg.use_fb_write_buffer

    async def tb(ctx):
        model = _Counters()
        # sampled at the *rising edge*: what the registers will count this cycle
        hs = [cw.valid, cw.ready, cw_in.ready, aw_.valid, aw_.ready, aw_in.ready,
              cr.valid, cr.ready, cr_in.ready, ar.valid, ar.ready, ar_in.ready,
              lfb.valid, lfb.ready, lfb_in.ready]
        stat_sigs = [dut.stats[n] for n in FRAMEBUFFER_MEM_STAT_NAMES]
        reader_ports = [(getattr(dut.color_reader, p), getattr(dut.aux_reader, p))
                        for _, p in STAT_SUMS_CACHED] if cached else []
        wb_ports = [(getattr(dut.color_write_port, f), getattr(dut.aux_write_port, f))
                    for f in STAT_SUMS_WRITE_BUFFER]
        misc = [dut.color_write_port.write_stall_cycles, dut.aux_write_port.write_stall_cycles,
                dut.color_write_port.busy, dut.aux_write_port.busy,
                dut.status.color_busy, dut.status.aux_busy,
                dut.color_write_port.flush, dut.aux_write_port.flush, dut.flush]
        if cached:
            misc += [dut.color_reader.max_occupancy, dut.aux_reader.max_occupancy]
        tie = [dut.fb_mem_write.cmd.valid, dut.fb_mem_write.cmd.payload.fragment.opcode,
               dut.fb_mem_write.cmd.payload.last, dut.fb_mem_write.rsp.ready,
               dut.fb_color_write_mem.cmd.valid, dut.fb_color_write_mem.cmd.payload.fragment.opcode,
               dut.fb_color_write_mem.cmd.payload.last, dut.fb_color_write_mem.rsp.ready,
               dut.fb_aux_write_mem.cmd.valid, dut.fb_aux_write_mem.rsp.ready]
        flat_pairs = [s for pair in reader_ports + wb_ports for s in pair]
        n_hs, n_st, n_rp, n_wb, n_misc = len(hs), len(stat_sigs), 2 * len(reader_ports), 2 * len(wb_ports), len(misc)
        cycle = 0
        async for _clk, _rst, *vals in ctx.tick().sample(*hs, *stat_sigs, *flat_pairs, *misc, *tie):
            vals = [int(v) for v in vals]
            h = vals[:n_hs]
            st = dict(zip(FRAMEBUFFER_MEM_STAT_NAMES, vals[n_hs:n_hs + n_st]))
            rp = vals[n_hs + n_st:n_hs + n_st + n_rp]
            wbv = vals[n_hs + n_st + n_rp:n_hs + n_st + n_rp + n_wb]
            mv = vals[n_hs + n_st + n_rp + n_wb:n_hs + n_st + n_rp + n_wb + n_misc]
            tv = vals[n_hs + n_st + n_rp + n_wb + n_misc:]
            # the registers hold the model's value *before* this edge's step
            for n in _Counters.NAMES:
                want = model.v[n]
                if not cached and n in ("mem_color_read_cmd_count", "mem_aux_read_cmd_count",
                                        "mem_color_read_blocked_cycles",
                                        "mem_aux_read_blocked_cycles"):
                    want = 0          # the register counts, the output is U(0)
                assert st[n] == want, f"cycle {cycle}: {n}: dut {st[n]} model {want}"
            model.step(cw=h[0:3], aw_=h[3:6], cr=h[6:9], ar=h[9:12], lfb=h[12:15])
            # sums
            for i, (field, _) in enumerate(STAT_SUMS_CACHED):
                want = ((rp[2 * i] + rp[2 * i + 1]) & 0xFFFFFFFF) if cached else 0
                assert st[field] == want, f"cycle {cycle}: stats.{field}"
            for i, field in enumerate(STAT_SUMS_WRITE_BUFFER):
                want = ((wbv[2 * i] + wbv[2 * i + 1]) & 0xFFFFFFFF) if wb else 0
                assert st[field] == want, f"cycle {cycle}: stats.{field}"
            assert st["write_stall_cycles"] == (mv[0] + mv[1]) & 0xFFFFFFFF
            assert (mv[4], mv[5]) == (mv[2], mv[3])              # status = busy
            assert mv[6] == mv[7] == (mv[8] if wb else 0)         # flush pass-through / False
            if cached:
                assert st["read_max_queue_occupancy"] == max(mv[9], mv[10]) & 0xFF
            else:
                assert st["read_max_queue_occupancy"] == 0
            # tie-offs
            if wb:
                assert tv[4:8] == [0, CMD_WRITE, 1, 1] and tv[8:10] == [0, 1]
            else:
                assert tv[0:4] == [0, CMD_WRITE, 1, 1]
            cycle += 1
            log["cycles"] = cycle

    return tb


def _random_flush_tb(dut, rng):
    async def tb(ctx):
        while True:
            ctx.set(dut.flush, int(rng.random() < 0.3))
            await ctx.tick()

    return tb


def test_voodoo1_routing_and_counters():
    """Every master through its port, route tags, data back where it belongs,
    the ten counters and every sum right on every cycle."""
    rng = random.Random(1)
    dut = FramebufferMemSubsystem(C)
    size = 1 << 16
    color_mem = Memory(size, rng)
    aux_mem = Memory(size, rng)
    # the write ports get their own images so a read is never racing a write
    cw_mem, aw_mem = Memory(size, rng), Memory(size, rng)
    color_expect = Memory(size, random.Random(0))
    color_expect.mem[:] = cw_mem.mem
    aux_expect = Memory(size, random.Random(0))
    aux_expect.mem[:] = aw_mem.mem
    slaves = {
        "cw": MemSlave(cw_mem, FB_P, random.Random(rng.random()), cmd_ready_density=0.7),
        "aw": MemSlave(aw_mem, FB_P, random.Random(rng.random()), cmd_ready_density=0.7),
        "cr": MemSlave(color_mem, FB_P, random.Random(rng.random()), cmd_ready_density=0.7,
                       rsp_valid_density=0.8),
        "ar": MemSlave(aux_mem, FB_P, random.Random(rng.random()), cmd_ready_density=0.7,
                       rsp_valid_density=0.8),
    }
    color_rsp, aux_rsp, scan_rsp, lfb_rsp = [], [], [], []
    log = {}

    writes_c = [(rng.randrange(0, size, 4) | rng.getrandbits(2), rng.getrandbits(32),
                 rng.getrandbits(4)) for _ in range(40)]
    writes_a = [(rng.randrange(0, size, 4) | rng.getrandbits(2), rng.getrandbits(32),
                 rng.getrandbits(4)) for _ in range(40)]
    # spans: (start, lanes) for the three readers; reads follow each span in order
    spans_c = [(rng.randrange(0, size - 4096, 2), rng.randint(1, 24)) for _ in range(6)]
    spans_a = [(rng.randrange(0, size - 4096, 2), rng.randint(1, 24)) for _ in range(6)]
    spans_s = [(rng.randrange(0, size - 4096, 2), rng.randint(1, 40)) for _ in range(6)]
    lfb_addrs = [rng.randrange(0, size, 4) for _ in range(12)]

    async def writers(ctx):
        await _stream_push(ctx, dut.color_write, [_write_req(*w) for w in writes_c])

    async def aux_writers(ctx):
        await _stream_push(ctx, dut.aux_write, [_write_req(*w) for w in writes_a])

    async def reader(prefetch, read, spans, ctx):
        for start, lanes in spans:
            end = start + 2 * (lanes - 1)
            await _stream_push(ctx, prefetch, [_prefetch(start, end)])
            await ctx.tick().repeat(3)
            await _stream_push(ctx, read, [_read_req(start + 2 * i) for i in range(lanes)])

    async def lfb(ctx):
        await _stream_push(ctx, dut.lfb_read_bus.cmd,
                           [_lfb_cmd(dut.lfb_read_params, opcode=CMD_READ, address=a)
                            for a in lfb_addrs])

    async def slave(key, bus, ctx):
        await slaves[key].run(ctx, bus)

    run(dut, writers, aux_writers,
        partial(reader, dut.prefetch_color, dut.color_read_req, spans_c),
        partial(reader, dut.prefetch_aux, dut.aux_read_req, spans_a),
        partial(reader, dut.scanout_prefetch_req, dut.scanout_read_req, spans_s),
        lfb,
        processes=[
            partial(slave, "cw", dut.fb_color_write_mem),
            partial(slave, "aw", dut.fb_aux_write_mem),
            partial(slave, "cr", dut.fb_color_read_mem),
            partial(slave, "ar", dut.fb_aux_read_mem),
            _collect(dut.color_read_rsp, color_rsp),
            _collect(dut.aux_read_rsp, aux_rsp),
            _collect(dut.scanout_read_rsp, scan_rsp),
            _collect(dut.lfb_read_bus.rsp, lfb_rsp),
            _invariants_tb(dut, C, log),
            _random_flush_tb(dut, random.Random(9)),
        ],
        deadline=6000)

    # -- writes: every accepted write reached its own port as one 4-byte beat
    for key, writes, expect in (("cw", writes_c, color_expect), ("aw", writes_a, aux_expect)):
        beats = slaves[key].cmd_log
        assert len(beats) == len(writes)
        for beat, (addr, data, mask) in zip(beats, writes):
            assert beat["opcode"] == CMD_WRITE and beat["last"] == 1
            assert beat["length"] == 3 and beat["source"] == 0
            assert beat["address"] == addr and beat["data"] == data and beat["mask"] == mask
            expect.write(addr, data, mask)
        assert slaves[key].mem.mem == expect.mem
        assert len(slaves[key].rsp_log) == len(writes)
    # -- reads: route tags, and the lanes come back in order on the right stream
    routes = [b["source"] & 3 for b in slaves["cr"].cmd_log]
    assert routes.count(ROUTE_SCANOUT) == len(spans_s)       # one burst per span, no misses
    assert routes.count(ROUTE_LFB) == len(lfb_addrs)
    assert routes.count(ROUTE_COLOR) >= len(spans_c)
    assert all(b["opcode"] == CMD_READ for b in slaves["cr"].cmd_log)
    # the aux port is a one-input bypass: the reader's own source, no route bits
    assert all(b["source"] >> 1 == 0 for b in slaves["ar"].cmd_log)
    assert len(slaves["ar"].cmd_log) >= len(spans_a)
    for got, spans, mem in ((color_rsp, spans_c, color_mem), (aux_rsp, spans_a, aux_mem),
                            (scan_rsp, spans_s, color_mem)):
        want = [mem.lane(start + 2 * i) for start, lanes in spans for i in range(lanes)]
        assert [int(v.data) for v in got] == want
    lfb_data = [int(v.fragment.data) for v in lfb_rsp]
    assert lfb_data == [color_mem.word(a) for a in lfb_addrs]
    assert all(int(v.last) == 1 for v in lfb_rsp)
    assert log["cycles"] > 1000


def test_aux_read_port_route_bits():
    """A one-input arbiter is the bypass: the aux reader's own ``source`` lands
    in ``source[0]`` and no route bits are added (the widened field's upper
    bits are zero)."""
    rng = random.Random(4)
    dut = FramebufferMemSubsystem(C)
    mem = Memory(1 << 14, rng)
    slave = MemSlave(mem, FB_P, random.Random(2))
    out = []

    async def tb(ctx):
        # a read with nothing prefetched: a direct miss, source = 1
        await _stream_push(ctx, dut.aux_read_req, [_read_req(0x100)])
        await ctx.tick().repeat(40)
        await _stream_push(ctx, dut.prefetch_aux, [_prefetch(0x200, 0x20E)])
        await ctx.tick().repeat(40)

    run(dut, tb, processes=[slave.on(dut.fb_aux_read_mem),
                            _collect(dut.aux_read_rsp, out)], deadline=400)
    sources = [b["source"] for b in slave.cmd_log]
    assert sources == [1, 0]                       # direct miss, then the prefetch fill
    assert [int(v.data) for v in out] == [mem.lane(0x100)]


def test_color_read_arbiter_is_lower_first_with_fragment_lock():
    """``StreamArbiterFactory().fragmentLock.lowerFirst``: the grant locks on
    the first master whose beat is offered (the colour reader here, whose
    fill arrives first) until that beat's ``last`` fires, even while the port
    is stalled; among the two then pending, scanout (0) beats the LFB (2)."""
    rng = random.Random(5)
    dut = FramebufferMemSubsystem(C)
    mem = Memory(1 << 14, rng)
    slave = MemSlave(mem, FB_P, random.Random(3))
    seen = []

    async def tb(ctx):
        ctx.set(dut.fb_color_read_mem.cmd.ready, 0)
        # hold the port: prefetch on colour and scanout (one fill each), then
        # an LFB read, which is unpiped and so only "accepted" when granted
        await _stream_push(ctx, dut.prefetch_color, [_prefetch(0x1000, 0x1006)])
        await _stream_push(ctx, dut.scanout_prefetch_req, [_prefetch(0x2000, 0x2006)])
        ctx.set(dut.lfb_read_bus.cmd.payload,
                _lfb_cmd(dut.lfb_read_params, opcode=CMD_READ, address=0x40))
        ctx.set(dut.lfb_read_bus.cmd.valid, 1)
        await ctx.tick().repeat(10)
        # all three sit in their s2mPipes / at the arbiter inputs now
        assert ctx.get(dut.fb_color_read_arbiter.inputs[0].cmd.valid)
        assert ctx.get(dut.fb_color_read_arbiter.inputs[1].cmd.valid)
        assert ctx.get(dut.fb_color_read_arbiter.inputs[2].cmd.valid)
        assert ctx.get(dut.fb_color_read_mem.cmd.valid)
        # locked on the colour reader: it was valid first, and the lock is
        # taken on output.valid, not output.fire
        assert ctx.get(dut.fb_color_read_mem.cmd.payload.fragment.source) & 3 == ROUTE_COLOR
        # release one beat at a time and record the winner
        for _ in range(3):
            ctx.set(dut.fb_color_read_mem.cmd.ready, 1)
            _c, _r, v, src = await ctx.tick().sample(
                dut.fb_color_read_mem.cmd.valid, dut.fb_color_read_mem.cmd.payload.fragment.source)
            assert v
            seen.append(src & 3)
            ctx.set(dut.fb_color_read_mem.cmd.ready, 0)
            await ctx.tick().repeat(2)
        ctx.set(dut.lfb_read_bus.cmd.valid, 0)
        ctx.set(dut.fb_color_read_mem.cmd.ready, 1)

    run(dut, tb, deadline=200)
    assert seen == [ROUTE_COLOR, ROUTE_SCANOUT, ROUTE_LFB]


# ===========================================================================
# 3. the write-buffer configuration: fbMemWrite carries both writers
# ===========================================================================


def test_write_buffer_configuration_shares_fb_mem_write():
    cfg = _configs()["wb"]
    rng = random.Random(7)
    dut = FramebufferMemSubsystem(cfg)
    mem = Memory(1 << 14, rng)
    slave = MemSlave(mem, FB_P, random.Random(8), cmd_ready_density=0.8)
    cr = MemSlave(mem, FB_P, random.Random(9))
    ar = MemSlave(mem, FB_P, random.Random(10))
    log = {}
    writes_c = [(0x400 + 4 * i, rng.getrandbits(32), 0xF) for i in range(16)]   # one full line
    writes_a = [(0x800 + 4 * i, rng.getrandbits(32), 0xF) for i in range(5)]

    async def tb(ctx):
        ctx.set(dut.flush, 0)
        await _stream_push(ctx, dut.color_write, [_write_req(*w) for w in writes_c])
        await _stream_push(ctx, dut.aux_write, [_write_req(*w) for w in writes_a])
        await ctx.tick().repeat(20)
        ctx.set(dut.flush, 1)                       # drains the partial aux line
        await ctx.tick().repeat(60)
        ctx.set(dut.flush, 0)
        await ctx.tick().repeat(20)

    run(dut, tb, processes=[slave.on(dut.fb_mem_write),
                            cr.on(dut.fb_color_read_mem),
                            ar.on(dut.fb_aux_read_mem),
                            _invariants_tb(dut, cfg, log)], deadline=400)
    beats = slave.cmd_log
    assert beats, "nothing drained"
    # route bit 0 = chosen input (0 colour, 1 aux); the drain's own source (1) above it
    colour = [b for b in beats if b["source"] & 1 == 0]
    aux = [b for b in beats if b["source"] & 1 == 1]
    assert [b["address"] for b in colour] == [a for a, _, _ in writes_c]
    assert [b["address"] for b in aux] == [a for a, _, _ in writes_a]
    assert all(b["source"] >> 1 == 1 for b in beats)
    assert all(b["opcode"] == CMD_WRITE for b in beats)
    assert colour[-1]["last"] == 1 and aux[-1]["last"] == 1
    assert all(b["last"] == 0 for b in colour[:-1])
    for a, d, _ in writes_c + writes_a:
        assert mem.word(a) == d
    assert log["cycles"] > 300


# ===========================================================================
# 5. direct readers
# ===========================================================================


def test_direct_reader_configuration():
    cfg = _configs()["direct"]
    rng = random.Random(11)
    dut = FramebufferMemSubsystem(cfg)
    mem = Memory(1 << 14, rng)
    cr = MemSlave(mem, FB_P, random.Random(12), cmd_ready_density=0.7)
    ar = MemSlave(mem, FB_P, random.Random(13), cmd_ready_density=0.7)
    out_c, out_a = [], []
    log = {}
    addrs_c = [rng.randrange(0, 1 << 14, 2) for _ in range(20)]
    addrs_a = [rng.randrange(0, 1 << 14, 2) for _ in range(20)]

    async def tb(ctx):
        # prefetches are swallowed: ready is constant True
        assert ctx.get(dut.prefetch_color.ready) == 1 and ctx.get(dut.prefetch_aux.ready) == 1
        ctx.set(dut.prefetch_color.valid, 1)
        ctx.set(dut.prefetch_color.payload, _prefetch(0, 0x10))
        await _stream_push(ctx, dut.color_read_req, [_read_req(a) for a in addrs_c])
        await _stream_push(ctx, dut.aux_read_req, [_read_req(a) for a in addrs_a])
        await ctx.tick().repeat(100)
        assert ctx.get(dut.prefetch_color.ready) == 1

    run(dut, tb, processes=[cr.on(dut.fb_color_read_mem),
                            ar.on(dut.fb_aux_read_mem),
                            _collect(dut.color_read_rsp, out_c),
                            _collect(dut.aux_read_rsp, out_a),
                            _invariants_tb(dut, cfg, log)], deadline=600)
    assert [int(v.data) for v in out_c] == [mem.lane(a) for a in addrs_c]
    assert [int(v.data) for v in out_a] == [mem.lane(a) for a in addrs_a]
    assert [b["address"] for b in cr.cmd_log] == [a & ~3 for a in addrs_c]
    assert all(b["source"] & 3 == ROUTE_COLOR for b in cr.cmd_log)
    assert log["cycles"] > 400


# ===========================================================================
# 6. FramebufferLayout.fromRegisterBank
# ===========================================================================


class _LayoutDut(Elaboratable):
    """A stub register bank / swap buffer with the attribute paths the helper reads."""

    def __init__(self):
        self.fb_base_addr = Signal(AW)
        self.video_tiles_x = Signal(4)
        self.buffer_offset = Signal(9)
        self.draw_buffer = Signal(2)
        self.write_buffer_select = Signal(2)
        self.read_buffer_select = Signal(2)
        self.swap_count = Signal(2)
        self.swaps_pending = Signal(3)
        self.layout = Signal(FramebufferLayout(C))
        self.reg_bank = SimpleNamespace(
            init=SimpleNamespace(
                fbi_init1=SimpleNamespace(video_tiles_x=self.video_tiles_x),
                fbi_init2=SimpleNamespace(buffer_offset=self.buffer_offset)),
            render_config=SimpleNamespace(
                fbz_mode=SimpleNamespace(draw_buffer=self.draw_buffer),
                lfb_mode=SimpleNamespace(write_buffer_select=self.write_buffer_select,
                                         read_buffer_select=self.read_buffer_select)))
        self.swap_buffer = SimpleNamespace(swap_count=self.swap_count,
                                           swaps_pending=self.swaps_pending)

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.layout.eq(framebuffer_layout_from_register_bank(
            m, C, self.fb_base_addr, self.reg_bank, self.swap_buffer))
        return m


def _layout_model(fb_base, tiles, offset, draw, wsel, rsel, swap_count, pending):
    mask = (1 << AW) - 1
    stride = ((tiles << 6) & 0x3FF) & 0x7FF
    offset_bytes = (offset << 12) & mask
    b0 = fb_base & mask
    b1 = (fb_base + offset_bytes) & mask
    aux = (fb_base + ((offset_bytes << 1) & mask)) & mask
    displayed = swap_count & 1
    front, back = (b1, b0) if displayed else (b0, b1)
    return {
        "front": front, "back": back, "aux": aux,
        "draw": {"color_base_addr": back if draw & 1 else front, "aux_base_addr": aux,
                 "pixel_stride": stride},
        "lfb_write": {"color_base_addr": back if wsel & 1 else front, "aux_base_addr": aux,
                      "pixel_stride": stride},
        "lfb_read": {"color_base_addr": back if rsel & 1 else front, "aux_base_addr": aux,
                     "pixel_stride": stride},
        "displayed_buffer": displayed, "swaps_pending": pending,
    }


def test_register_bank_has_the_fields_the_layout_reads():
    """The stub's widths are the real bank's (``fbiInit1_videoTilesX`` 4 bits,
    ``fbiInit2_bufferOffset`` 9, the three buffer selects 2)."""
    from avoodoo.frontend.register_bank import RegisterBank

    rb = RegisterBank(C)
    assert len(rb.init.fbi_init1.video_tiles_x) == 4
    assert len(rb.init.fbi_init2.buffer_offset) == 9
    assert len(rb.render_config.fbz_mode.draw_buffer) == 2
    assert len(rb.render_config.lfb_mode.write_buffer_select) == 2
    assert len(rb.render_config.lfb_mode.read_buffer_select) == 2
    Fragment.get(rb, None)


def test_framebuffer_layout_from_register_bank():
    dut = _LayoutDut()
    rng = random.Random(21)
    cases = [(rng.getrandbits(AW), rng.getrandbits(4), rng.getrandbits(9), rng.getrandbits(2),
              rng.getrandbits(2), rng.getrandbits(2), rng.getrandbits(2), rng.getrandbits(3))
             for _ in range(200)]
    cases += [((1 << AW) - 4, 0xF, 0x1FF, 1, 1, 1, 1, 7), (0, 0, 0, 0, 0, 0, 0, 0)]

    async def tb(ctx):
        for case in cases:
            for sig, v in zip((dut.fb_base_addr, dut.video_tiles_x, dut.buffer_offset,
                               dut.draw_buffer, dut.write_buffer_select,
                               dut.read_buffer_select, dut.swap_count, dut.swaps_pending), case):
                ctx.set(sig, v)
            await ctx.delay(1e-9)
            got = ctx.get(dut.layout)
            want = _layout_model(*case)
            for k, v in want.items():
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        assert int(getattr(getattr(got, k), kk)) == vv, (case, k, kk)
                else:
                    assert int(getattr(got, k)) == v, (case, k)

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
