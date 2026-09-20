"""Phase 5 unit tests for :mod:`avoodoo.texture.texture_mem` and
:mod:`avoodoo.core.texture_mem_subsystem`.

Upstream has no ``TextureMemSubsystemTest.scala``; the module is only covered
through the Glide suite.  These tests pin the three things the Scala source
promises and the lockstep diff (``tests/diff/test_diff_texture_mem_subsystem.py``)
does not *explain*:

1. **Relocation** -- :class:`TextureMemModel.write_address` is an independent
   Python model of ``WriteCmd.fromPciDrain`` (packed layout), written from
   the Scala and from 86Box's ``voodoo_tex_writel`` decode, and every drained
   write that reaches ``texMem`` must carry the address it predicts.  Writes
   outside the TMU0 window or at LOD 9..15 must never reach ``texMem``.
   The hardware ``compute_tables`` is also checked against the pure-Python
   ``tex_layout_tables.compute`` over random configurations.
2. **Arbitration order** -- with all three masters pending, ``texMem`` sees
   the TMU first, the CPU write second and the CPU read last, and a TMU that
   keeps ``valid`` high starves the other two.
3. **Response routing by source** -- ``rsp.source[1:0]`` picks the slave,
   ``rsp.source >> 2`` is handed back resized, ``texMem.rsp.ready`` is the
   selected slave's (the write bus always accepts; an unmapped tag stalls).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pytest
from amaranth.hdl import ClockDomain, Elaboratable, Fragment, Module, Signal
from amaranth.lib import data
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.core.core_types import TexWritePayload, tex_layout_tables_layout
from avoodoo.core.membus_params import cpu_tex_bmb_params, tex_mem_bmb_params, tmu_bmb_params
from avoodoo.core.texture_mem_subsystem import (ROUTE_CPU_READ, ROUTE_CPU_WRITE, ROUTE_TMU,
                                                 TextureMemSubsystem)
from avoodoo.lib.membus import CMD_READ, CMD_WRITE, RSP_SUCCESS, cmd_layout, rsp_layout
from avoodoo.texture import tex_layout_tables as tlt
from avoodoo.texture.texture_mem import DownloadConfig, WriteCmd

C = Config.voodoo1()
CLK = 1e-6

TEX_MEM_P = tex_mem_bmb_params(C)
CPU_TEX_P = cpu_tex_bmb_params()
TMU_P = tmu_bmb_params(C)
ROUTE_W = 2
SRC_TOP_W = TEX_MEM_P.access.source_width - ROUTE_W


# ===========================================================================
# the Python model
# ===========================================================================


@dataclass(frozen=True)
class DlCfg:
    """One ``DownloadConfig`` value."""

    texture_mode: int = 0
    tex_base_addr: int = 0
    tex_base_addr1: int = 0
    tex_base_addr2: int = 0
    tex_base_addr38: int = 0
    t_lod: int = 0

    def tex_config(self) -> tlt.TexConfig:
        return tlt.TexConfig(
            tex_base_addr=self.tex_base_addr & 0x7FFFF,
            tex_base_addr1=self.tex_base_addr1 & 0x7FFFF,
            tex_base_addr2=self.tex_base_addr2 & 0x7FFFF,
            tex_base_addr38=self.tex_base_addr38 & 0x7FFFF,
            tformat=(self.texture_mode >> 8) & 0xF,
            tlod_aspect=(self.t_lod >> 21) & 3,
            tlod_s_is_wider=bool((self.t_lod >> 20) & 1),
            tlod_multibase=bool((self.t_lod >> 24) & 1),
        )


class TextureMemModel:
    """Functional model of ``TextureMemSubsystem`` (packed layout)."""

    @staticmethod
    def write_address(cfg: DlCfg, pci_addr: int) -> int | None:
        """``WriteCmd.fromPciDrain``: the SRAM byte address, or ``None`` when
        the write is thrown away."""
        pci_addr &= (1 << 23) - 1
        if (pci_addr >> 21) & 3:                      # not the TMU0 window
            return None
        lod = (pci_addr >> 17) & 0xF
        if lod > 8:
            return None
        tables = tlt.compute(cfg.tex_config())
        is16 = ((cfg.texture_mode >> 8) & 0xF) >= tlt.ARGB8332
        seq8 = (cfg.texture_mode >> 31) & 1
        t = (pci_addr >> 9) & 0xFF
        if is16:
            s = ((pci_addr >> 1) & 0xFE) & 0xFF
        elif seq8:
            s = (pci_addr & 0xFC) & 0xFF
        else:
            s = ((pci_addr >> 1) & 0xFC) & 0xFF
        base = tables.tex_base[lod]
        shift = tables.tex_shift[lod]
        if is16:
            addr = base + (s << 1) + ((t << ((shift + 1) & 0x1F)) & tlt.ADDR_MASK)
        else:
            addr = base + s + ((t << shift) & tlt.ADDR_MASK)
        return addr & tlt.ADDR_MASK

    @staticmethod
    def routed_source(source: int, route: int) -> int:
        return ((source & ((1 << SRC_TOP_W) - 1)) << ROUTE_W) | route

    @staticmethod
    def rsp_route(source: int) -> int:
        return source & ((1 << ROUTE_W) - 1)

    @staticmethod
    def rsp_source(source: int, width: int) -> int:
        return (source >> ROUTE_W) & ((1 << width) - 1)

    @staticmethod
    def arbitrate(tmu: bool, write: bool, read: bool) -> int | None:
        if tmu:
            return ROUTE_TMU
        if write:
            return ROUTE_CPU_WRITE
        if read:
            return ROUTE_CPU_READ
        return None


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


def run(dut, *testbenches, deadline=5000):
    sim = Simulator(_Clocked(dut))
    sim.add_clock(CLK)
    for tb in testbenches:
        sim.add_testbench(tb)
    sim.run_until(deadline * CLK)


def _cfg_value(cfg: DlCfg):
    return DownloadConfig(C).const({
        "texture_mode": cfg.texture_mode & 0xFFFFFFFF,
        "tex_base_addr": cfg.tex_base_addr & 0xFFFFFF,
        "tex_base_addr1": cfg.tex_base_addr1 & 0xFFFFFF,
        "tex_base_addr2": cfg.tex_base_addr2 & 0xFFFFFF,
        "tex_base_addr38": cfg.tex_base_addr38 & 0xFFFFFF,
        "t_lod": cfg.t_lod & 0xFFFFFFFF,
    })


def _cmd(p, *, opcode, address, length, source=0, data=0, mask=0, last=1):
    lay = cmd_layout(p)
    has = lay["fragment"].shape.members
    frag = {"source": source, "opcode": opcode, "address": address, "length": length,
            "data": data, "mask": mask}
    return lay.const({"last": last, "fragment": {k: v for k, v in frag.items() if k in has}})


def _rsp(p, *, source, data=0, last=1, opcode=RSP_SUCCESS):
    lay = rsp_layout(p)
    has = lay["fragment"].shape.members
    frag = {"source": source, "opcode": opcode, "data": data}
    return lay.const({"last": last, "fragment": {k: v for k, v in frag.items() if k in has}})


async def _push_drain(ctx, dut, writes):
    """Push ``(pci_addr, data, mask)`` triples into ``cpu_tex_drain``."""
    for pci_addr, wdata, mask in writes:
        ctx.set(dut.cpu_tex_drain.payload, TexWritePayload().const(
            {"pci_addr": pci_addr, "data": wdata, "mask": mask}))
        ctx.set(dut.cpu_tex_drain.valid, 1)
        await ctx.tick().until(dut.cpu_tex_drain.ready)
    ctx.set(dut.cpu_tex_drain.valid, 0)


def _collect_tex_mem(dut, out, *, ready=1):
    """A testbench that accepts every ``tex_mem.cmd`` beat into ``out`` (dicts)."""

    async def tb(ctx):
        await _collect_tex_mem_tb(ctx, dut, out, ready=ready)

    return tb


async def _collect_tex_mem_tb(ctx, dut, out, *, ready=1):
    frag = dut.tex_mem.cmd.payload.fragment
    names = list(cmd_layout(TEX_MEM_P)["fragment"].shape.members)
    sampled = [dut.tex_mem.cmd.valid, dut.tex_mem.cmd.ready, dut.tex_mem.cmd.payload.last]
    sampled += [frag[n] for n in names]
    ctx.set(dut.tex_mem.cmd.ready, ready)
    async for _clk, _rst, valid, rdy, last, *values in ctx.tick().sample(*sampled):
        if valid and rdy:
            beat = {"last": last}
            beat.update(zip(names, values))
            out.append(beat)


# ===========================================================================
# 1. compute_tables (hardware) vs compute (model)
# ===========================================================================


class _TablesDut(Elaboratable):
    def __init__(self):
        self.cfg = Signal(tlt.TexConfigLayout())
        self.tables = Signal(tex_layout_tables_layout())

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.tables.eq(tlt.compute_tables(m, self.cfg))
        return m


def _random_tex_config(rng: random.Random) -> tlt.TexConfig:
    return tlt.TexConfig(
        tex_base_addr=rng.getrandbits(19),
        tex_base_addr1=rng.getrandbits(19),
        tex_base_addr2=rng.getrandbits(19),
        tex_base_addr38=rng.getrandbits(19),
        tformat=rng.getrandbits(4),
        tlod_aspect=rng.getrandbits(2),
        tlod_s_is_wider=bool(rng.getrandbits(1)),
        tlod_multibase=bool(rng.getrandbits(1)),
    )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_compute_tables_matches_model(seed):
    rng = random.Random(seed)
    dut = _TablesDut()
    cases = [_random_tex_config(rng) for _ in range(64)]
    # the corner every LOD table depends on: 16-bit + multibase + each aspect
    for aspect in range(4):
        cases.append(tlt.TexConfig(tex_base_addr=0x7FFFF, tex_base_addr38=0x7FFF0,
                                   tformat=tlt.ARGB8332, tlod_aspect=aspect,
                                   tlod_s_is_wider=False, tlod_multibase=True))

    async def tb(ctx):
        for cfg in cases:
            ctx.set(dut.cfg, tlt.TexConfigLayout().const({
                "tex_base_addr": cfg.tex_base_addr,
                "tex_base_addr1": cfg.tex_base_addr1,
                "tex_base_addr2": cfg.tex_base_addr2,
                "tex_base_addr38": cfg.tex_base_addr38,
                "tformat": cfg.tformat,
                "t_lod_aspect": cfg.tlod_aspect,
                "t_lod_s_is_wider": int(cfg.tlod_s_is_wider),
                "t_lod_multibase": int(cfg.tlod_multibase),
            }))
            await ctx.delay(1e-7)
            expect = tlt.compute(cfg)
            for lod in range(tlt.LOD_COUNT):
                assert ctx.get(dut.tables.tex_base[lod]) == expect.tex_base[lod], (cfg, lod)
                assert ctx.get(dut.tables.tex_end[lod]) == expect.tex_end[lod], (cfg, lod)
                assert ctx.get(dut.tables.tex_shift[lod]) == expect.tex_shift[lod], (cfg, lod)

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()


# ===========================================================================
# 2. relocation: the CPU write path
# ===========================================================================


def _random_dlcfg(rng: random.Random) -> DlCfg:
    return DlCfg(
        texture_mode=rng.getrandbits(32),
        tex_base_addr=rng.getrandbits(24),
        tex_base_addr1=rng.getrandbits(24),
        tex_base_addr2=rng.getrandbits(24),
        tex_base_addr38=rng.getrandbits(24),
        t_lod=rng.getrandbits(32),
    )


@pytest.mark.parametrize("seed", [11, 12])
def test_cpu_write_relocation(seed):
    """Random downloads reach ``texMem`` at the model's address, or not at all."""
    rng = random.Random(seed)
    dut = TextureMemSubsystem(C)
    cfg = _random_dlcfg(rng)
    writes = []
    for _ in range(48):
        pci = rng.getrandbits(23)
        if rng.random() < 0.7:                         # mostly inside the TMU0 window
            pci &= ~(3 << 21)
        if rng.random() < 0.7:                         # mostly LOD 0..8
            pci = (pci & ~(0xF << 17)) | (rng.randint(0, 8) << 17)
        writes.append((pci, rng.getrandbits(32), rng.getrandbits(4)))
    expected = [(a, d, k) for (p, d, k) in writes
                if (a := TextureMemModel.write_address(cfg, p)) is not None]
    assert expected, "the stimulus should keep some writes"
    assert len(expected) < len(writes), "the stimulus should drop some writes"
    got: list[dict] = []

    async def stim(ctx):
        ctx.set(dut.download_config, _cfg_value(cfg))
        await _push_drain(ctx, dut, writes)
        for _ in range(8):
            await ctx.tick()

    run(dut, stim, _collect_tex_mem(dut, got), deadline=len(writes) + 40)

    assert [(b["address"], b["data"], b["mask"]) for b in got] == expected
    for b in got:
        assert b["opcode"] == CMD_WRITE
        assert b["length"] == 3
        assert b["last"] == 1
        assert b["source"] == TextureMemModel.routed_source(0, ROUTE_CPU_WRITE)


def test_cpu_write_relocation_directed():
    """Hand-checked cases: LOD 0 origin, an 8-bit texel row, a 16-bit texel,
    multibase LOD 1 redirect, the sequential-8 packing."""
    # tformat RGB565 (10) -> 16 bit; tLOD aspect 0, sIsWider 0, multibase 0
    cfg16 = DlCfg(texture_mode=10 << 8, tex_base_addr=0x100, t_lod=0)
    # LOD 0, t = 3, low bits 0x10: s = ((0x10 >> 1) & 0xfe) = 8
    #   base = 0x100 << 3 = 0x800, shift = 8 -> addr = 0x800 + 16 + (3 << 9)
    assert TextureMemModel.write_address(cfg16, (3 << 9) | 0x10) == 0x800 + 16 + (3 << 9)
    # 8-bit (I8 = 3), seq8 off: s = ((0x14 >> 1) & 0xfc) = 8; addr = base + 8 + (t << 8)
    cfg8 = DlCfg(texture_mode=3 << 8, tex_base_addr=0x100)
    assert TextureMemModel.write_address(cfg8, (5 << 9) | 0x14) == 0x800 + 8 + (5 << 8)
    # seq8: s = 0x14 & 0xfc = 0x14
    cfg8s = DlCfg(texture_mode=(1 << 31) | (3 << 8), tex_base_addr=0x100)
    assert TextureMemModel.write_address(cfg8s, (5 << 9) | 0x14) == 0x800 + 0x14 + (5 << 8)
    # multibase: LOD 1 comes from texBaseAddr1, shift 7
    cfgmb = DlCfg(texture_mode=3 << 8, tex_base_addr=0x100, tex_base_addr1=0x200,
                  t_lod=1 << 24)
    assert TextureMemModel.write_address(cfgmb, (1 << 17) | (2 << 9)) == (0x200 << 3) + (2 << 7)
    # dropped: LOD 9, and the TMU1 window
    assert TextureMemModel.write_address(cfg8, 9 << 17) is None
    assert TextureMemModel.write_address(cfg8, 1 << 21) is None

    dut = TextureMemSubsystem(C)
    got: list[dict] = []
    seq = [
        (cfg16, (3 << 9) | 0x10),
        (cfg8, (5 << 9) | 0x14),
        (cfg8s, (5 << 9) | 0x14),
        (cfgmb, (1 << 17) | (2 << 9)),
        (cfg8, 9 << 17),
        (cfg8, 1 << 21),
    ]

    async def stim(ctx):
        for i, (cfg, pci) in enumerate(seq):
            ctx.set(dut.download_config, _cfg_value(cfg))
            await _push_drain(ctx, dut, [(pci, 0x1000 + i, 0xF)])
            for _ in range(4):                          # let the two stages drain
                await ctx.tick()

    run(dut, stim, _collect_tex_mem(dut, got), deadline=200)
    assert [(b["address"], b["data"]) for b in got] == [
        (TextureMemModel.write_address(cfg, pci), 0x1000 + i)
        for i, (cfg, pci) in enumerate(seq)
        if TextureMemModel.write_address(cfg, pci) is not None
    ]


def test_cpu_write_latency_and_throughput():
    """Two ``m2sPipe`` stages: a drained write shows up on ``texMem`` two
    cycles later, and back-to-back writes stream at one per cycle."""
    dut = TextureMemSubsystem(C)
    cfg = DlCfg(texture_mode=3 << 8)
    n = 8
    writes = [(i << 2, i, 0xF) for i in range(n)]
    seen_cycle: list[int] = []
    pushed_cycle: list[int] = []

    async def stim(ctx):
        ctx.set(dut.download_config, _cfg_value(cfg))
        cycle = 0
        for pci, d, k in writes:
            ctx.set(dut.cpu_tex_drain.payload, TexWritePayload().const(
                {"pci_addr": pci, "data": d, "mask": k}))
            ctx.set(dut.cpu_tex_drain.valid, 1)
            while True:
                rdy = ctx.get(dut.cpu_tex_drain.ready)
                await ctx.tick()
                cycle += 1
                if rdy:
                    pushed_cycle.append(cycle)
                    break
        ctx.set(dut.cpu_tex_drain.valid, 0)

    async def mon(ctx):
        ctx.set(dut.tex_mem.cmd.ready, 1)
        cycle = 0
        async for _c, _r, v, r, d in ctx.tick().sample(
                dut.tex_mem.cmd.valid, dut.tex_mem.cmd.ready,
                dut.tex_mem.cmd.payload.fragment.data):
            cycle += 1
            if v and r:
                seen_cycle.append(cycle)
                if len(seen_cycle) == n:
                    return

    run(dut, stim, mon, deadline=100)
    assert len(seen_cycle) == n
    assert [s - p for s, p in zip(seen_cycle, pushed_cycle)] == [2] * n
    assert [b - a for a, b in zip(seen_cycle, seen_cycle[1:])] == [1] * (n - 1)


# ===========================================================================
# 3. arbitration
# ===========================================================================


def test_arbitration_order_tmu_write_read():
    """All three pending behind a stalled ``texMem``: TMU, then CPU write, then CPU read."""
    dut = TextureMemSubsystem(C)
    got: list[dict] = []

    async def stim(ctx):
        ctx.set(dut.download_config, _cfg_value(DlCfg(texture_mode=3 << 8)))
        ctx.set(dut.tex_mem.cmd.ready, 0)
        # one CPU read into the queue (accepted by the FIFO even while stalled)
        ctx.set(dut.cpu_tex_read.cmd.payload, _cmd(CPU_TEX_P, opcode=CMD_READ, address=0x300,
                                                   length=3, source=5))
        ctx.set(dut.cpu_tex_read.cmd.valid, 1)
        await ctx.tick().until(dut.cpu_tex_read.cmd.ready)
        ctx.set(dut.cpu_tex_read.cmd.valid, 0)
        # one CPU write through the two pipe stages
        await _push_drain(ctx, dut, [(0x40, 0xABCD, 0xF)])
        for _ in range(3):
            await ctx.tick()
        # the TMU arrives last but wins: its s2mPipe register takes the beat
        # (rValidN was 1) even though texMem is stalled
        ctx.set(dut.tmu_tex_read.cmd.payload, _cmd(TMU_P, opcode=CMD_READ, address=0x1000,
                                                   length=31, source=1))
        ctx.set(dut.tmu_tex_read.cmd.valid, 1)
        await ctx.tick().until(dut.tmu_tex_read.cmd.ready)
        ctx.set(dut.tmu_tex_read.cmd.valid, 0)
        assert ctx.get(dut.tmu_tex_read.cmd.ready) == 0, "s2mPipe holds one beat"
        ctx.set(dut.tex_mem.cmd.ready, 1)
        for _ in range(6):
            await ctx.tick()

    async def mon(ctx):
        frag = dut.tex_mem.cmd.payload.fragment
        async for _c, _r, v, r, src, addr, op, ln in ctx.tick().sample(
                dut.tex_mem.cmd.valid, dut.tex_mem.cmd.ready, frag.source, frag.address,
                frag.opcode, frag.length):
            if v and r:
                got.append({"source": src, "address": addr, "opcode": op, "length": ln})

    run(dut, stim, mon, deadline=60)
    assert [TextureMemModel.rsp_route(b["source"]) for b in got] == [
        ROUTE_TMU, ROUTE_CPU_WRITE, ROUTE_CPU_READ]
    tmu, wr, rd = got
    assert (tmu["address"], tmu["opcode"], tmu["length"]) == (0x1000, CMD_READ, 31)
    assert tmu["source"] == TextureMemModel.routed_source(1, ROUTE_TMU)
    assert (wr["address"], wr["opcode"], wr["length"]) == (
        TextureMemModel.write_address(DlCfg(texture_mode=3 << 8), 0x40), CMD_WRITE, 3)
    assert (rd["address"], rd["opcode"], rd["length"]) == (0x300, CMD_READ, 3)
    assert rd["source"] == TextureMemModel.routed_source(5, ROUTE_CPU_READ)


def test_arbitration_tmu_starves_others():
    """A TMU holding ``valid`` takes every cycle; the others wait for it to stop."""
    dut = TextureMemSubsystem(C)
    got: list[dict] = []
    tmu_beats = 5

    async def stim(ctx):
        ctx.set(dut.download_config, _cfg_value(DlCfg(texture_mode=3 << 8)))
        ctx.set(dut.tex_mem.cmd.ready, 1)
        # a CPU write and a CPU read are already waiting
        ctx.set(dut.tex_mem.cmd.ready, 0)
        ctx.set(dut.cpu_tex_read.cmd.payload, _cmd(CPU_TEX_P, opcode=CMD_READ, address=0x300,
                                                   length=3, source=2))
        ctx.set(dut.cpu_tex_read.cmd.valid, 1)
        await ctx.tick().until(dut.cpu_tex_read.cmd.ready)
        ctx.set(dut.cpu_tex_read.cmd.valid, 0)
        await _push_drain(ctx, dut, [(0x40, 0x1, 0xF)])
        for _ in range(3):
            await ctx.tick()
        # TMU streams `tmu_beats` commands back to back
        ctx.set(dut.tmu_tex_read.cmd.valid, 1)
        ctx.set(dut.tex_mem.cmd.ready, 1)
        for i in range(tmu_beats):
            ctx.set(dut.tmu_tex_read.cmd.payload, _cmd(TMU_P, opcode=CMD_READ,
                                                       address=0x1000 + 4 * i, length=3))
            await ctx.tick().until(dut.tmu_tex_read.cmd.ready)
        ctx.set(dut.tmu_tex_read.cmd.valid, 0)
        for _ in range(8):
            await ctx.tick()

    async def mon(ctx):
        frag = dut.tex_mem.cmd.payload.fragment
        async for _c, _r, v, r, src, addr in ctx.tick().sample(
                dut.tex_mem.cmd.valid, dut.tex_mem.cmd.ready, frag.source, frag.address):
            if v and r:
                got.append({"source": src, "address": addr})

    run(dut, stim, mon, deadline=80)
    routes = [TextureMemModel.rsp_route(b["source"]) for b in got]
    assert routes == [ROUTE_TMU] * tmu_beats + [ROUTE_CPU_WRITE, ROUTE_CPU_READ]
    assert [b["address"] for b in got[:tmu_beats]] == [0x1000 + 4 * i for i in range(tmu_beats)]


def test_cpu_read_queue_depth_two():
    """``cpuTexRead.cmd.queue(2)``: two reads are accepted while ``texMem``
    stalls, the third is not; the ``source`` survives the round trip."""
    dut = TextureMemSubsystem(C)
    got: list[dict] = []
    accepted: list[int] = []

    async def stim(ctx):
        ctx.set(dut.download_config, _cfg_value(DlCfg()))
        ctx.set(dut.tex_mem.cmd.ready, 0)
        for i in range(3):
            ctx.set(dut.cpu_tex_read.cmd.payload, _cmd(CPU_TEX_P, opcode=CMD_READ,
                                                       address=0x100 * (i + 1), length=3,
                                                       source=i + 1))
            ctx.set(dut.cpu_tex_read.cmd.valid, 1)
            accepted_now = 0
            for _ in range(4):
                if ctx.get(dut.cpu_tex_read.cmd.ready):
                    accepted_now = 1
                    await ctx.tick()
                    break
                await ctx.tick()
            accepted.append(accepted_now)
            ctx.set(dut.cpu_tex_read.cmd.valid, 0)
        # release and drain
        ctx.set(dut.tex_mem.cmd.ready, 1)
        for _ in range(6):
            await ctx.tick()

    async def mon(ctx):
        frag = dut.tex_mem.cmd.payload.fragment
        async for _c, _r, v, r, src, addr in ctx.tick().sample(
                dut.tex_mem.cmd.valid, dut.tex_mem.cmd.ready, frag.source, frag.address):
            if v and r:
                got.append({"source": src, "address": addr})

    run(dut, stim, mon, deadline=60)
    assert accepted == [1, 1, 0]
    assert [(b["address"], b["source"]) for b in got] == [
        (0x100, TextureMemModel.routed_source(1, ROUTE_CPU_READ)),
        (0x200, TextureMemModel.routed_source(2, ROUTE_CPU_READ)),
    ]


# ===========================================================================
# 4. response routing
# ===========================================================================


def test_rsp_routing_by_source():
    """``rsp.source[1:0]`` selects the slave, ``>> 2`` is what it sees."""
    dut = TextureMemSubsystem(C)
    tmu_w = TMU_P.access.source_width
    cpu_w = CPU_TEX_P.access.source_width
    observations: list[tuple] = []

    async def tb(ctx):
        ctx.set(dut.download_config, _cfg_value(DlCfg()))
        ctx.set(dut.tmu_tex_read.rsp.ready, 1)
        ctx.set(dut.cpu_tex_read.rsp.ready, 0)
        for src in range(1 << TEX_MEM_P.access.source_width):
            for tmu_rdy in (0, 1):
                for cpu_rdy in (0, 1):
                    ctx.set(dut.tmu_tex_read.rsp.ready, tmu_rdy)
                    ctx.set(dut.cpu_tex_read.rsp.ready, cpu_rdy)
                    ctx.set(dut.tex_mem.rsp.payload, _rsp(TEX_MEM_P, source=src,
                                                          data=0xC0DE0000 | src, last=src & 1))
                    ctx.set(dut.tex_mem.rsp.valid, 1)
                    await ctx.delay(1e-8)
                    route = TextureMemModel.rsp_route(src)
                    tmu_v = ctx.get(dut.tmu_tex_read.rsp.valid)
                    cpu_v = ctx.get(dut.cpu_tex_read.rsp.valid)
                    mem_rdy = ctx.get(dut.tex_mem.rsp.ready)
                    assert (tmu_v, cpu_v) == (int(route == ROUTE_TMU), int(route == ROUTE_CPU_READ))
                    expect_rdy = {ROUTE_TMU: tmu_rdy, ROUTE_CPU_WRITE: 1,
                                  ROUTE_CPU_READ: cpu_rdy}.get(route, 0)
                    assert mem_rdy == expect_rdy, (src, tmu_rdy, cpu_rdy)
                    # payload fan-out is unconditional
                    assert ctx.get(dut.tmu_tex_read.rsp.payload.fragment.data) == 0xC0DE0000 | src
                    assert ctx.get(dut.cpu_tex_read.rsp.payload.fragment.data) == 0xC0DE0000 | src
                    assert ctx.get(dut.tmu_tex_read.rsp.payload.last) == (src & 1)
                    assert ctx.get(dut.tmu_tex_read.rsp.payload.fragment.source) == \
                        TextureMemModel.rsp_source(src, tmu_w)
                    assert ctx.get(dut.cpu_tex_read.rsp.payload.fragment.source) == \
                        TextureMemModel.rsp_source(src, cpu_w)
                    observations.append((src, tmu_rdy, cpu_rdy))
            # with valid low nothing is selected and nothing is ready
            ctx.set(dut.tex_mem.rsp.valid, 0)
            await ctx.delay(1e-8)
            assert ctx.get(dut.tmu_tex_read.rsp.valid) == 0
            assert ctx.get(dut.cpu_tex_read.rsp.valid) == 0
            assert ctx.get(dut.tex_mem.rsp.ready) == 0
        await ctx.tick()

    run(dut, tb, deadline=10)
    assert len(observations) == (1 << TEX_MEM_P.access.source_width) * 4


def test_write_rsp_is_swallowed():
    """A CPU write's response (tag 1) is accepted immediately and reaches no port."""
    dut = TextureMemSubsystem(C)

    async def tb(ctx):
        ctx.set(dut.download_config, _cfg_value(DlCfg()))
        ctx.set(dut.tmu_tex_read.rsp.ready, 0)
        ctx.set(dut.cpu_tex_read.rsp.ready, 0)
        ctx.set(dut.tex_mem.rsp.payload, _rsp(TEX_MEM_P, source=ROUTE_CPU_WRITE))
        ctx.set(dut.tex_mem.rsp.valid, 1)
        await ctx.delay(1e-8)
        assert ctx.get(dut.tex_mem.rsp.ready) == 1
        assert ctx.get(dut.tmu_tex_read.rsp.valid) == 0
        assert ctx.get(dut.cpu_tex_read.rsp.valid) == 0
        await ctx.tick()

    run(dut, tb, deadline=5)


# ===========================================================================
# 5. layouts / parameters
# ===========================================================================


def test_layouts_and_params():
    assert list(DownloadConfig(C).members) == [
        "texture_mode", "tex_base_addr", "tex_base_addr1", "tex_base_addr2",
        "tex_base_addr38", "t_lod"]
    assert DownloadConfig(C).size == 32 + 4 * 24 + 32
    assert list(WriteCmd(C).members) == ["address", "data", "mask"]
    assert WriteCmd(C).size == C.address_width + 32 + 4
    # Tmu.bmbParams: read-only, source 1 -> no data/mask in the command
    assert "data" not in cmd_layout(TMU_P)["fragment"].shape.members
    assert "mask" not in cmd_layout(TMU_P)["fragment"].shape.members
    assert TMU_P.access.source_width == 1
    # Core.texMemBmbParams: 4 + log2Up(3)
    assert TEX_MEM_P.access.source_width == 6
    dut = TextureMemSubsystem(C)
    assert dut.cmd_route_width == 2
    assert isinstance(dut.signature.members["download_config"].shape, data.StructLayout)
    Fragment.get(dut, None)  # elaborate once so the object is not "unused"
