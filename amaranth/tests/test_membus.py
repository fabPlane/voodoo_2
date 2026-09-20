"""Unit tests for ``avoodoo.lib.membus`` / ``avoodoo.sim_membus_memory`` (plan L0).

Three layers:

1. **Parameter maths** -- the derived widths and the four ``outputParameter``
   functions, checked against values computed by hand from the SpinalHDL
   1.15.0 sources *and* against the parameterisations the core actually
   instantiates (``Core.scala``, ``RegisterBank.scala``,
   ``FramebufferPlaneBuffer/Reader.scala``, ``Lfb.scala``, ``CoreArtix.scala``).
2. **Payload layout** -- field presence and bit offsets, because the packed
   ``context`` fields and the differential harness both depend on them.
3. **Behaviour under pysim** -- directed and randomised traffic against Python
   models: a byte-addressed memory, and (for the arbiter) an order/lock
   observer.  Everything is kept to a few thousand cycles; the cycle-exact
   proof is the Verilator diff test, not this.
"""

from __future__ import annotations

import random
from functools import partial

import pytest
from amaranth import ClockDomain, Elaboratable, Module
from amaranth.lib import data
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.core import membus_params as mp
from avoodoo.lib.membus import (CMD_READ, CMD_WRITE, RSP_SUCCESS, Aligner,
                                AlignedSplitter, Arbiter, BurstAlignment,
                                ContextRemover, LengthFixer, MemBusAccessParameter,
                                MemBusParameter, MemBusSourceParameter, SourceRemover,
                                cmd_layout, log2_up, rsp_layout)
from avoodoo.sim_membus_memory import SimMemBusMemory, SimMemoryTiming

C = Config.voodoo1()


# ==========================================================================
# the parameterisations the core instantiates
# ==========================================================================
#
# All of them live in avoodoo/core/membus_params.py so the port, the diff
# registry and these tests cannot drift apart; the thin wrappers below just
# bind Config.voodoo1().


def cpu_params():
    return mp.cpu_bmb_params()


def fb_mem_params(c=C):
    return mp.fb_mem_bmb_params(c)


def tex_mem_params(c=C):
    return mp.tex_mem_bmb_params(c)


def cpu_tex_params():
    return mp.cpu_tex_bmb_params()


def register_bank_params():
    return mp.register_bank_params()


def register_bank_external_params():
    return mp.register_bank_external_params()


def plane_buffer_params(c=C):
    return mp.plane_buffer_params(c)


def plane_reader_need_splitter(c=C):
    return mp.plane_reader_need_burst_splitter(c)


def plane_reader_internal_params(c=C):
    return mp.plane_reader_internal_params(c)


# --- 1. parameter maths ---------------------------------------------------


def test_log2_up_matches_spinal():
    # spinal.core.log2Up: 0 for 0 and 1, then ceil(log2 n).
    assert [log2_up(n) for n in range(0, 10)] == [0, 0, 1, 2, 2, 3, 3, 3, 3, 4]
    assert log2_up(512) == 9 and log2_up(513) == 10 and log2_up(2048) == 11
    with pytest.raises(ValueError):
        log2_up(-1)


def test_core_parameter_derived_widths():
    p = fb_mem_params()
    a = p.access
    assert (a.address_width, a.data_width) == (26, 32)
    assert a.source_width == 4            # 1 + log2Up(5)
    assert a.length_width == 11
    assert a.context_width == 0
    assert a.byte_count == 4 and a.mask_width == 4 and a.word_range_length == 2
    assert a.alignment is BurstAlignment.BYTE and a.alignment.allow_byte
    assert a.allow_burst
    # transferBeatCount = (1 << 11)/4 + 1 (the +1 is the byte-alignment slack)
    assert a.transfer_beat_count == 513
    assert a.beat_counter_width == 10

    t = tex_mem_params().access
    assert t.source_width == 6            # 4 + log2Up(3)

    cpu = cpu_params().access
    assert (cpu.source_width, cpu.length_width, cpu.transfer_beat_count) == (4, 2, 1)
    assert not cpu.allow_burst            # lengthWidth 2 == wordRangeLength 2
    assert cpu.beat_counter_width == 0

    assert cpu_tex_params().access.transfer_beat_count == (1 << 6) // 4 + 1
    assert register_bank_params().access.address_width == 12
    assert register_bank_external_params().access.address_width == 22
    assert plane_buffer_params().access.source_width == 1


def test_voodoo1_does_not_need_the_burst_splitter():
    # ((800 + 1) / 2) * 4 = 1600 <= 2048, so FramebufferPlaneReader instantiates
    # neither BmbAlignedSpliter nor BmbContextRemover in the shipping core.
    assert plane_reader_need_splitter() is False
    assert plane_reader_internal_params().access.length_width == C.mem_burst_length_width


def test_single_source_has_a_zero_width_source_field():
    # sourceWidth = log2Up(max(id) + 1), so one source means no field at all.
    p = fb_mem_params()
    single = p.with_access(p.access.with_single_source(p.access.aggregated))
    assert single.access.source_width == 0
    assert "source" not in cmd_layout(single).members
    assert "source" not in rsp_layout(single).members


def test_source_parameter_aggregate():
    agg = MemBusSourceParameter.aggregate([
        MemBusSourceParameter(context_width=3, length_width=5,
                              alignment=BurstAlignment.LENGTH, alignment_min=4,
                              access_latency_min=2, can_read=True, can_write=False),
        MemBusSourceParameter(context_width=7, length_width=2,
                              alignment=BurstAlignment.BYTE, alignment_min=1,
                              access_latency_min=5, can_read=False, can_write=True),
    ])
    assert agg.context_width == 7 and agg.length_width == 5
    assert agg.alignment is BurstAlignment.BYTE      # LENGTH is the fold identity
    assert agg.alignment_min == 1 and agg.access_latency_min == 2
    assert agg.can_read and agg.can_write
    # canMask only counts for sources that can also write.
    assert agg.can_mask


@pytest.mark.parametrize("maker", [cpu_params, fb_mem_params, tex_mem_params,
                                   cpu_tex_params, register_bank_params,
                                   plane_buffer_params])
def test_source_remover_output_parameter(maker):
    p = maker()
    op = SourceRemover.output_parameter(p)
    assert op.access.source_width == 0
    assert op.access.context_width == p.access.context_width + p.access.source_width
    assert op.access.length_width == p.access.length_width
    assert op.access.address_width == p.access.address_width


def test_context_remover_output_parameter():
    p = plane_reader_internal_params()
    op = ContextRemover.output_parameter(p)
    assert op.access.source_width == 0 and op.access.context_width == 0
    assert op.access.length_width == p.access.length_width


def test_aligner_and_length_fixer_output_parameters_match_bmb_to_axi4mem():
    # CoreArtix.scala:211-213 / :226-228, axiBurstLog2 = 8 (256-byte bursts).
    burst_log2 = 8
    p = fb_mem_params()
    ap = Aligner.output_access_parameter(p.access, burst_log2)
    assert not Aligner.bypass(p.access, burst_log2)
    assert ap.source_width == 0
    assert ap.length_width == p.access.length_width + 1 == 12
    # 1 + log2Up(256/4) + log2Up(513) + sourceWidth = 1 + 6 + 10 + 4
    assert ap.context_width == 21
    assert ap.alignment_min == burst_log2

    fp = LengthFixer.output_access_parameter(ap, burst_log2)
    assert fp.length_width == burst_log2 and fp.alignment_min == burst_log2
    assert fp.context_width == 21 + 2 + 0 == 23
    assert fp.source_width == 0

    # Texture bus differs only in sourceWidth, which lands in the context.
    tap = Aligner.output_access_parameter(tex_mem_params().access, burst_log2)
    assert tap.context_width == 1 + 6 + 10 + 6 == 23


def test_aligner_bypass_condition():
    # Word-aligned and already inside one data word -> the Scala bypasses.
    word = MemBusParameter.simple(address_width=16, data_width=32, source_width=0,
                                  context_width=0, length_width=2,
                                  alignment=BurstAlignment.WORD)
    assert Aligner.bypass(word.access, 2)
    assert not Aligner.bypass(word.access, 3)
    byte = MemBusParameter.simple(address_width=16, data_width=32, source_width=0,
                                  context_width=0, length_width=2,
                                  alignment=BurstAlignment.BYTE)
    assert not Aligner.bypass(byte.access, 2)   # allowByte defeats the bypass


def test_aligned_splitter_output_parameter():
    p = plane_reader_internal_params()
    length_max = 1 << C.mem_burst_length_width
    op = AlignedSplitter.output_access_parameter(p.access, length_max)
    assert op.access.length_width if False else op.length_width == log2_up(length_max)
    assert op.context_width == p.access.context_width + 2 + p.access.source_width == 3
    assert op.source_width == 0


def test_arbiter_rejects_a_too_narrow_output_source():
    narrow = MemBusParameter.simple(address_width=16, data_width=32, source_width=1,
                                    context_width=0, length_width=4)
    with pytest.raises(ValueError, match="route index"):
        Arbiter([narrow] * 5, narrow, True)


def test_membus_refuses_the_unported_channels():
    from avoodoo.lib.membus import MemBusSignature
    src = MemBusSourceParameter(context_width=0, length_width=4, can_invalidate=True)
    access = MemBusAccessParameter(16, 32).add_sources(2, src)
    with pytest.raises(NotImplementedError):
        MemBusSignature(MemBusParameter(access))


# --- 2. payload layout ----------------------------------------------------


def test_cmd_layout_field_order_matches_the_scala_bundle():
    # Fragment declares `last` first, BmbCmd declares source/opcode/address/
    # length/data/mask/context; MultiData.asBits puts the first field at the
    # LSB, and so does data.StructLayout.
    p = fb_mem_params()
    lay = cmd_layout(p)
    assert list(lay.members) == ["last", "fragment"]
    assert lay["last"].offset == 0
    frag = lay["fragment"].shape
    assert list(frag.members) == ["source", "opcode", "address", "length", "data", "mask"]
    offset = 0
    for name, width in [("source", 4), ("opcode", 1), ("address", 26),
                        ("length", 11), ("data", 32), ("mask", 4)]:
        assert frag[name].offset == offset and frag[name].shape.width == width
        offset += width
    assert lay.size == 1 + offset


def test_rsp_layout_drops_the_write_only_fields():
    lay = rsp_layout(fb_mem_params())
    frag = lay["fragment"].shape
    assert list(frag.members) == ["source", "opcode", "data"]
    assert "address" not in frag.members and "mask" not in frag.members


def _layout_only(obj):
    """Silence ``UnusedElaboratable`` for a component built only for its maths."""
    obj._MustUse__used = True
    return obj


def test_context_layouts_pack_in_declaration_order():
    p = fb_mem_params()
    # Aligner: {write, paddings, transfers, source, input}
    al = _layout_only(Aligner(p, 8))
    assert list(al._context_layout().members) == ["write", "paddings", "transfers", "source"]
    assert al._context_layout().size == al.op.access.context_width
    # LengthFixer: {last, write, source, input}; AlignedSpliter: {source, last, write, input}
    lf = _layout_only(LengthFixer(al.op, 8))
    # The aligner's output carries a 21-bit context, so `input` is present too.
    assert list(lf._context_layout().members) == ["last", "write", "input"]
    assert lf._context_layout().size == lf.op.access.context_width
    sp = _layout_only(AlignedSplitter(p, 1 << 8))
    assert list(sp._context_layout().members) == ["source", "last", "write"]
    assert sp._context_layout().size == sp.op.access.context_width
    # Same width, different order -- they are not interchangeable.
    q = plane_reader_internal_params()
    assert (_layout_only(LengthFixer(q, 8))._context_layout().size
            == _layout_only(AlignedSplitter(q, 1 << 8))._context_layout().size)


# ==========================================================================
# pysim helpers
# ==========================================================================

CLK = 1e-6


def _pack(layout: data.Layout, fields: dict):
    return layout.const(fields)


class Master:
    """Drives one ``MemBus`` master port from a testbench."""

    def __init__(self, ctx, bus, p: MemBusParameter):
        self.ctx = ctx
        self.bus = bus
        self.p = p
        self.layout = cmd_layout(p)
        self.rsps: list[dict] = []

    def _frag(self, **kw) -> dict:
        members = self.layout["fragment"].shape.members
        return {k: v for k, v in kw.items() if k in members}

    async def send(self, *, opcode, address, length, data=0, mask=0xF,
                   source=0, context=0, last=1):
        ctx = self.ctx
        ctx.set(self.bus.cmd.payload, _pack(self.layout, {
            "last": last,
            "fragment": self._frag(source=source, opcode=opcode, address=address,
                                   length=length, data=data, mask=mask,
                                   context=context),
        }))
        ctx.set(self.bus.cmd.valid, 1)
        await ctx.tick().until(self.bus.cmd.ready)
        ctx.set(self.bus.cmd.valid, 0)


async def collect_responses(ctx, bus, p: MemBusParameter, out: list, *, ready=1):
    """Consume every response beat into ``out`` as dicts.

    Every payload read goes through ``ctx.tick().sample()``, never through a
    plain ``ctx.get()`` after the tick: the arbiter's output payload is a
    combinational mux of the *currently valid* inputs, so by the time the
    simulator has settled past the edge the beat that just transferred is
    already gone.
    """
    names = list(rsp_layout(p)["fragment"].shape.members)
    sampled = [bus.rsp.valid, bus.rsp.ready, bus.rsp.payload.last]
    sampled += [bus.rsp.payload.fragment[n] for n in names]
    ctx.set(bus.rsp.ready, ready)
    async for _clk, _rst, valid, rdy, last, *values in ctx.tick().sample(*sampled):
        if valid and rdy:
            beat = {"last": last}
            beat.update(zip(names, values))
            out.append(beat)


class BurstSlaveModel:
    """Python model of an AXI-style burst memory behind a ``MemBus``.

    Command semantics assumed (what ``BmbToAxi4SharedBridge`` presents):

    * a read command is a single beat; the slave returns
      ``((address & 3) + length) // 4 + 1`` data beats from consecutive words;
    * a write burst's address comes from its **first** beat and the following
      beats go to the following words;
    * ``source``/``context`` are echoed on every response beat.

    This is deliberately *not* the per-beat-address convention
    ``SimBmbMemory`` implements -- the two live on different sides of the
    Aligner/LengthFixer pair (see ``MEMBUS_NOTES`` item 6).
    """

    def __init__(self, size_bytes: int, seed_data: bytes | None = None):
        self.mem = bytearray(seed_data or bytes(size_bytes))
        self.cmd_log: list[dict] = []

    def word(self, byte_addr: int) -> int:
        return int.from_bytes(self.mem[byte_addr:byte_addr + 4], "little")

    def set_word(self, byte_addr: int, value: int, mask: int = 0xF) -> None:
        for i in range(4):
            if mask & (1 << i):
                self.mem[byte_addr + i] = (value >> (8 * i)) & 0xFF


async def burst_slave(ctx, bus, p: MemBusParameter, model: BurstSlaveModel, *,
                      stall=0, rng=None):
    """Service ``bus`` (a slave-side ``MemBus``) from ``model``."""
    cmd_members = list(cmd_layout(p)["fragment"].shape.members)
    rsp_lay = rsp_layout(p)
    has = rsp_lay["fragment"].shape.members

    def rsp(**kw):
        return _pack(rsp_lay, {
            "last": kw.pop("last"),
            "fragment": {k: v for k, v in kw.items() if k in has},
        })

    ctx.set(bus.cmd.ready, 0)
    ctx.set(bus.rsp.valid, 0)
    write_addr = None
    while True:
        # --- accept one command beat -------------------------------------
        if stall and rng is not None and rng.random() < stall:
            ctx.set(bus.cmd.ready, 0)
            await ctx.tick()
            continue
        ctx.set(bus.cmd.ready, 1)
        values = await ctx.tick().sample(
            bus.cmd.payload.last, *[bus.cmd.payload.fragment[k] for k in cmd_members]
        ).until(bus.cmd.valid)
        beat = dict(zip(["last", *cmd_members], values))
        ctx.set(bus.cmd.ready, 0)
        model.cmd_log.append(dict(beat))

        source = beat.get("source", 0)
        context = beat.get("context", 0)
        if beat["opcode"] == CMD_WRITE:
            if write_addr is None:
                write_addr = beat["address"] & ~3
            model.set_word(write_addr, beat.get("data", 0), beat.get("mask", 0xF))
            write_addr += 4
            if not beat["last"]:
                continue
            write_addr = None
            ctx.set(bus.rsp.payload, rsp(last=1, opcode=RSP_SUCCESS, data=0,
                                         source=source, context=context))
            ctx.set(bus.rsp.valid, 1)
            await ctx.tick().until(bus.rsp.ready)
            ctx.set(bus.rsp.valid, 0)
        else:
            base = beat["address"] & ~3
            beats = ((beat["address"] & 3) + beat.get("length", 0)) // 4 + 1
            for i in range(beats):
                ctx.set(bus.rsp.payload, rsp(last=int(i == beats - 1),
                                             opcode=RSP_SUCCESS,
                                             data=model.word(base + 4 * i),
                                             source=source, context=context))
                ctx.set(bus.rsp.valid, 1)
                await ctx.tick().until(bus.rsp.ready)
                ctx.set(bus.rsp.valid, 0)


class _Clocked(Elaboratable):
    """Wrap a DUT in a module that owns ``sync``.

    A one-port :class:`Arbiter` is a pure bypass with no registers at all, so
    without this the simulator refuses ``add_clock`` ("Domain 'sync' is not
    present in simulation").
    """

    def __init__(self, dut):
        self.dut = dut

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.submodules.dut = self.dut
        return m


def run(dut, *testbenches, processes=(), until=None, deadline=20000):
    sim = Simulator(_Clocked(dut))
    sim.add_clock(CLK)
    for tb in testbenches:
        sim.add_testbench(tb)
    for pr in processes:
        sim.add_testbench(pr, background=True)
    sim.run_until(deadline * CLK)


# ==========================================================================
# 3a. SimMemBusMemory
# ==========================================================================


SMALL = MemBusParameter.simple(address_width=16, data_width=32, source_width=2,
                               context_width=0, length_width=4,
                               alignment=BurstAlignment.BYTE)


def test_sim_memory_single_word_write_then_read():
    dut = SimMemBusMemory(SMALL, 1024, SimMemoryTiming())
    rsps: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.bus, SMALL)
        await mst.send(opcode=CMD_WRITE, address=0x40, length=3,
                       data=0xDEADBEEF, mask=0xF, source=2)
        await ctx.tick().repeat(4)
        await mst.send(opcode=CMD_READ, address=0x40, length=3, source=3)
        await ctx.tick().repeat(8)

    run(dut, main, processes=[
        partial(collect_responses, bus=dut.bus, p=SMALL, out=rsps)])
    assert len(rsps) == 2
    assert rsps[0]["last"] == 1 and rsps[0]["source"] == 2
    assert rsps[1]["data"] == 0xDEADBEEF and rsps[1]["source"] == 3
    assert rsps[1]["last"] == 1


def test_sim_memory_byte_mask():
    dut = SimMemBusMemory(SMALL, 1024, SimMemoryTiming())
    rsps: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.bus, SMALL)
        await mst.send(opcode=CMD_WRITE, address=0, length=3, data=0xFFFFFFFF)
        await ctx.tick().repeat(3)
        await mst.send(opcode=CMD_WRITE, address=0, length=3,
                       data=0x11223344, mask=0b0101)
        await ctx.tick().repeat(3)
        await mst.send(opcode=CMD_READ, address=0, length=3)
        await ctx.tick().repeat(6)

    run(dut, main, processes=[
        partial(collect_responses, bus=dut.bus, p=SMALL, out=rsps)])
    assert rsps[-1]["data"] == 0xFF22FF44


def test_sim_memory_read_burst_returns_consecutive_words():
    dut = SimMemBusMemory(SMALL, 1024, SimMemoryTiming())
    rsps: list[dict] = []
    words = [0x00010203, 0x04050607, 0x08090A0B, 0x0C0D0E0F]

    async def main(ctx):
        mst = Master(ctx, dut.bus, SMALL)
        for i, w in enumerate(words):
            # SimBmbMemory takes the write address from the current beat.
            await mst.send(opcode=CMD_WRITE, address=0x100 + 4 * i, length=3, data=w)
            await ctx.tick().repeat(3)
        await mst.send(opcode=CMD_READ, address=0x100, length=15, source=1)
        await ctx.tick().repeat(12)

    run(dut, main, processes=[
        partial(collect_responses, bus=dut.bus, p=SMALL, out=rsps)])
    reads = rsps[len(words):]
    assert [r["data"] for r in reads] == words
    assert [r["last"] for r in reads] == [0, 0, 0, 1]
    assert all(r["source"] == 1 for r in reads)


def test_sim_memory_write_burst_uses_the_per_beat_address():
    """The address on every beat is what is written -- MEMBUS_NOTES item 6."""
    dut = SimMemBusMemory(SMALL, 1024, SimMemoryTiming())
    rsps: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.bus, SMALL)
        await mst.send(opcode=CMD_WRITE, address=0x200, length=7, data=0xAAAA0000, last=0)
        await mst.send(opcode=CMD_WRITE, address=0x204, length=7, data=0xBBBB1111, last=1)
        await ctx.tick().repeat(4)
        await mst.send(opcode=CMD_READ, address=0x200, length=7)
        await ctx.tick().repeat(8)

    run(dut, main, processes=[
        partial(collect_responses, bus=dut.bus, p=SMALL, out=rsps)])
    reads = [r for r in rsps][1:]
    assert [r["data"] for r in reads] == [0xAAAA0000, 0xBBBB1111]


@pytest.mark.parametrize("timing", [
    SimMemoryTiming(),
    SimMemoryTiming(mode="fixed", fixed_read_latency=3, write_latency=2),
    SimMemoryTiming(mode="burst", burst_setup_latency=5, burst_beat_latency=1),
])
def test_sim_memory_random_traffic_matches_a_bytearray(timing):
    p = SMALL
    dut = SimMemBusMemory(p, 1024, timing)
    rng = random.Random(0xB1)
    model = bytearray(1024)
    rsps: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.bus, p)
        for _ in range(40):
            addr = rng.randrange(0, 1024 // 4) * 4
            if rng.random() < 0.5:
                data = rng.getrandbits(32)
                mask = rng.getrandbits(4)
                await mst.send(opcode=CMD_WRITE, address=addr, length=3,
                               data=data, mask=mask, source=rng.getrandbits(2))
                for i in range(4):
                    if mask & (1 << i):
                        model[addr + i] = (data >> (8 * i)) & 0xFF
                await ctx.tick().repeat(6)
            else:
                before = len(rsps)
                await mst.send(opcode=CMD_READ, address=addr, length=3,
                               source=rng.getrandbits(2))
                await ctx.tick().repeat(10)
                got = rsps[before:]
                assert len(got) == 1, f"read at {addr:#x} returned {len(got)} beats"
                assert got[0]["data"] == int.from_bytes(model[addr:addr + 4], "little")

    run(dut, main, processes=[
        partial(collect_responses, bus=dut.bus, p=p, out=rsps)], deadline=4000)


def test_sim_memory_keeps_the_harness_memory_path():
    """``m.submodules.mem`` is a contract with ``emu/sim/harness``."""
    from amaranth.back import verilog

    from avoodoo.core_sim_ports import FB_MEM_PATH, RAM_ARRAY_NAME
    v = verilog.convert(SimMemBusMemory(SMALL, 256, SimMemoryTiming()), name="SimMem")
    assert f"\\{RAM_ARRAY_NAME} " in v or f" {RAM_ARRAY_NAME} " in v or "mem[" in v
    assert FB_MEM_PATH.endswith(f"__DOT__{RAM_ARRAY_NAME}")


# ==========================================================================
# 3b. Arbiter
# ==========================================================================


def _arbiter_dut(port_count, *, lower_first=True, in_p=None, out_p=None):
    out_p = out_p or fb_mem_params()
    in_p = in_p or out_p
    return Arbiter([in_p] * port_count, out_p, lower_first)


def test_arbiter_single_port_is_a_transparent_bypass():
    in_p = plane_buffer_params()          # sourceWidth 1
    out_p = fb_mem_params()               # sourceWidth 4
    dut = _arbiter_dut(1, in_p=in_p, out_p=out_p)
    seen: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.inputs[0], in_p)
        ctx.set(dut.inputs[0].rsp.ready, 1)
        await mst.send(opcode=CMD_READ, address=0x1234, length=7, source=1)
        await ctx.tick().repeat(30)

    model = BurstSlaveModel(4096, bytes(range(256)) * 16)
    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=out_p, model=model),
        partial(collect_responses, bus=dut.inputs[0], p=in_p, out=seen)],
        deadline=2000)
    assert model.cmd_log and model.cmd_log[0]["address"] == 0x1234
    assert model.cmd_log[0]["source"] == 1      # widened 1 -> 4 bits, value kept
    assert len(seen) == 2 and seen[-1]["last"] == 1


def test_arbiter_routes_responses_back_to_the_right_input():
    n = 5
    p = fb_mem_params()
    dut = _arbiter_dut(n)
    route_w = log2_up(n)
    got = [[] for _ in range(n)]

    async def main(ctx):
        masters = [Master(ctx, dut.inputs[k], p) for k in range(n)]
        for k in range(n):
            ctx.set(dut.inputs[k].rsp.ready, 1)
        for k, mst in enumerate(masters):
            await mst.send(opcode=CMD_READ, address=0x40 * (k + 1), length=3,
                           source=k)
            await ctx.tick().repeat(12)
        await ctx.tick().repeat(30)

    model = BurstSlaveModel(4096, bytes((i * 7) & 0xFF for i in range(4096)))
    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=p, model=model)]
        + [(lambda k: (partial(collect_responses, bus=dut.inputs[k], p=p, out=got[k])))(k)
           for k in range(n)],
        deadline=4000)

    mask = (1 << p.access.source_width) - 1
    # (source @@ chosen).resized: chosen in the low routeWidth bits, the
    # master's own source above it, TRUNCATED to sourceWidth.  With 5 ports
    # (routeWidth 3) into a 4-bit source, master 2's source 2 comes out as 2,
    # not 0b10_010 -- three of its four bits are gone.  MEMBUS_NOTES item 3.
    wire = [((k << route_w) | k) & mask for k in range(n)]
    assert wire == [0, 9, 2, 11, 4]
    assert len(model.cmd_log) == n
    for k, beat in enumerate(model.cmd_log):
        assert beat["source"] == wire[k], f"input {k}"
        assert beat["address"] == 0x40 * (k + 1)
        # The route bits always survive -- that is what makes rsp routing work.
        assert beat["source"] & ((1 << route_w) - 1) == k
    for k in range(n):
        assert len(got[k]) == 1, f"input {k} got {len(got[k])} responses"
        assert got[k][0]["data"] == model.word(0x40 * (k + 1))
        # rsp.source is the wire source shifted back down by routeWidth, so
        # the master does NOT get its own source back.
        assert got[k][0]["source"] == (wire[k] >> route_w) & mask


def test_arbiter_lower_first_priority_and_fragment_lock():
    """Grant order is by index, and a burst is never interleaved."""
    n = 3
    p = fb_mem_params()
    dut = _arbiter_dut(n, lower_first=True)
    model = BurstSlaveModel(8192)

    async def main(ctx):
        for k in range(n):
            ctx.set(dut.inputs[k].rsp.ready, 1)
        layout = cmd_layout(p)

        def drive(k, *, data, last, address):
            ctx.set(dut.inputs[k].cmd.payload, layout.const({
                "last": last,
                "fragment": {"source": 0, "opcode": CMD_WRITE, "address": address,
                             "length": 11, "data": data, "mask": 0xF},
            }))
            ctx.set(dut.inputs[k].cmd.valid, 1)

        # Every input offers a 3-beat write burst at once.  lowerFirst must
        # drain input 0 completely, then 1, then 2.
        beat = [0, 0, 0]
        for k in range(n):
            drive(k, data=0x1000 * (k + 1), last=0, address=0x100 * (k + 1))
        handshake = []
        for k in range(n):
            handshake += [dut.inputs[k].cmd.valid, dut.inputs[k].cmd.ready]
        for _ in range(400):
            _clk, _rst, *hs = await ctx.tick().sample(*handshake)
            for k in range(n):
                if hs[2 * k] and hs[2 * k + 1]:
                    beat[k] += 1
                    if beat[k] >= 3:
                        ctx.set(dut.inputs[k].cmd.valid, 0)
                    else:
                        drive(k, data=0x1000 * (k + 1) + beat[k],
                              last=int(beat[k] == 2),
                              address=0x100 * (k + 1) + 4 * beat[k])
            if all(b >= 3 for b in beat):
                break
        await ctx.tick().repeat(40)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=p, model=model)], deadline=4000)

    # 9 command beats, grouped in threes, in input order.
    assert len(model.cmd_log) == 9
    groups = [model.cmd_log[i:i + 3] for i in (0, 3, 6)]
    for k, group in enumerate(groups):
        assert [b["data"] for b in group] == [0x1000 * (k + 1) + i for i in range(3)]
        assert [b["last"] for b in group] == [0, 0, 1]
        assert all(b["source"] & ((1 << log2_up(n)) - 1) == k for b in group), \
            "fragmentLock let another master in mid-burst"


def test_arbiter_round_robin_rotates():
    n = 4
    p = fb_mem_params()
    dut = _arbiter_dut(n, lower_first=False)
    model = BurstSlaveModel(8192)
    route_w = log2_up(n)

    async def main(ctx):
        layout = cmd_layout(p)
        for k in range(n):
            ctx.set(dut.inputs[k].rsp.ready, 1)
            ctx.set(dut.inputs[k].cmd.payload, layout.const({
                "last": 1,
                "fragment": {"source": 0, "opcode": CMD_WRITE, "address": 0x10 * k,
                             "length": 3, "data": k, "mask": 0xF},
            }))
            ctx.set(dut.inputs[k].cmd.valid, 1)
        await ctx.tick().repeat(300)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=p, model=model)], deadline=2000)

    chosen = [b["source"] & ((1 << route_w) - 1) for b in model.cmd_log]
    assert len(chosen) >= 8
    # Every window of n consecutive grants covers every input exactly once.
    for i in range(0, (len(chosen) // n) * n - n + 1, n):
        assert sorted(chosen[i:i + n]) == list(range(n)), chosen


def test_arbiter_random_traffic_round_trips_through_a_model_memory():
    n = 3
    p = fb_mem_params()
    dut = _arbiter_dut(n)
    rng = random.Random(7)
    model = BurstSlaveModel(4096, bytes(rng.getrandbits(8) for _ in range(4096)))
    got = [[] for _ in range(n)]
    expect = [[] for _ in range(n)]

    async def make_master(ctx, k):
        mst = Master(ctx, dut.inputs[k], p)
        local = random.Random(100 + k)
        for _ in range(8):
            addr = local.randrange(0, 1024 // 4) * 4
            expect[k].append(model.word(addr))
            await mst.send(opcode=CMD_READ, address=addr, length=3, source=k)
            for _ in range(local.randrange(1, 6)):
                await ctx.tick()
        await ctx.tick().repeat(60)

    run(dut,
        *[partial(make_master, k=k) for k in range(n)],
        processes=[partial(burst_slave, bus=dut.output, p=p, model=model)]
        + [(lambda k: (partial(collect_responses, bus=dut.inputs[k], p=p, out=got[k])))(k)
           for k in range(n)],
        deadline=6000)

    for k in range(n):
        assert [r["data"] for r in got[k]] == expect[k], f"input {k}"


# ==========================================================================
# 3c. SourceRemover
# ==========================================================================


def test_source_remover_round_trips_source_and_context():
    p = MemBusParameter.simple(address_width=16, data_width=32, source_width=3,
                               context_width=2, length_width=4,
                               alignment=BurstAlignment.BYTE)
    dut = SourceRemover(p)
    op = dut.op
    assert op.access.context_width == 5 and op.access.source_width == 0
    model = BurstSlaveModel(4096, bytes((i * 3) & 0xFF for i in range(4096)))
    got: list[dict] = []
    sent = [(s, c, 0x40 * (s + 1)) for s, c in [(0, 0), (5, 3), (7, 1), (2, 2)]]

    async def main(ctx):
        mst = Master(ctx, dut.input, p)
        for source, context, addr in sent:
            await mst.send(opcode=CMD_READ, address=addr, length=3,
                           source=source, context=context)
            await ctx.tick().repeat(12)
        await ctx.tick().repeat(20)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=op, model=model),
        partial(collect_responses, bus=dut.input, p=p, out=got)], deadline=4000)

    assert len(got) == len(sent)
    for (source, context, addr), beat in zip(sent, got):
        assert beat["source"] == source
        assert beat["context"] == context
        assert beat["data"] == model.word(addr)
    # The packed context is {source at the LSB, context above it}.
    for (source, context, _), cmd in zip(sent, model.cmd_log):
        assert cmd["context"] == source | (context << 3)


# ==========================================================================
# 3d. ContextRemover
# ==========================================================================


def test_context_remover_round_trips_through_a_fifo():
    p = MemBusParameter.simple(address_width=16, data_width=32, source_width=2,
                               context_width=3, length_width=4,
                               alignment=BurstAlignment.BYTE)
    dut = ContextRemover(p, pending_max=8)
    op = dut.op
    assert op.access.context_width == 0 and op.access.source_width == 0
    model = BurstSlaveModel(4096, bytes((i * 5) & 0xFF for i in range(4096)))
    got: list[dict] = []
    sent = [(1, 5, 0x20), (2, 2, 0x60), (3, 7, 0xA0)]

    async def main(ctx):
        mst = Master(ctx, dut.input, p)
        for source, context, addr in sent:
            await mst.send(opcode=CMD_READ, address=addr, length=7,
                           source=source, context=context)
            await ctx.tick().repeat(16)
        await ctx.tick().repeat(30)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=op, model=model),
        partial(collect_responses, bus=dut.input, p=p, out=got)], deadline=4000)

    assert len(got) == 2 * len(sent)          # two beats per 8-byte read
    for i, (source, context, addr) in enumerate(sent):
        pair = got[2 * i:2 * i + 2]
        assert [b["source"] for b in pair] == [source, source]
        assert [b["context"] for b in pair] == [context, context]
        assert [b["data"] for b in pair] == [model.word(addr), model.word(addr + 4)]
        assert [b["last"] for b in pair] == [0, 1]
    # The output bus carries neither source nor context.
    assert "context" not in cmd_layout(op)["fragment"].shape.members
    assert "source" not in cmd_layout(op)["fragment"].shape.members


# ==========================================================================
# 3e. Aligner / LengthFixer -- the BmbToAxi4Mem chain
# ==========================================================================

# A small stand-in for `BmbToAxi4Mem(fbMemBmbParams, burstLog2 = 8)`: same
# shape, 16-byte alignment instead of 256, so a burst is four beats and the
# tests stay in the hundreds of cycles.
CHAIN_IN = MemBusParameter.simple(address_width=16, data_width=32, source_width=2,
                                  context_width=0, length_width=4,
                                  alignment=BurstAlignment.BYTE)
ALIGN_LOG2 = 4


def test_aligner_read_pads_the_command_and_drops_the_padding_beats():
    dut = Aligner(CHAIN_IN, ALIGN_LOG2)
    op = dut.op
    words = [0x11111111, 0x22222222, 0x33333333, 0x44444444]
    seed = bytearray(4096)
    for i, w in enumerate(words):
        seed[0x30 + 4 * i:0x34 + 4 * i] = w.to_bytes(4, "little")
    model = BurstSlaveModel(4096, bytes(seed))
    got: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.input, CHAIN_IN)
        # Two words starting at 0x38 -- inside the 16-byte block at 0x30, so
        # the aligner must request 0x30..0x3F and drop the first two beats.
        await mst.send(opcode=CMD_READ, address=0x38, length=7, source=1)
        await ctx.tick().repeat(40)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=op, model=model),
        partial(collect_responses, bus=dut.input, p=CHAIN_IN, out=got)], deadline=2000)

    assert len(model.cmd_log) == 1
    assert model.cmd_log[0]["address"] == 0x30
    assert model.cmd_log[0]["length"] == 15
    assert [b["data"] for b in got] == words[2:]
    assert [b["last"] for b in got] == [0, 1]
    assert all(b["source"] == 1 for b in got)


@pytest.mark.parametrize("address,length", [
    (0x30, 15), (0x34, 11), (0x38, 7), (0x3C, 3), (0x30, 3), (0x34, 3),
])
def test_aligner_read_returns_exactly_the_requested_words(address, length):
    dut = Aligner(CHAIN_IN, ALIGN_LOG2)
    rng = random.Random(address * 31 + length)
    seed = bytes(rng.getrandbits(8) for _ in range(4096))
    model = BurstSlaveModel(4096, seed)
    got: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.input, CHAIN_IN)
        await mst.send(opcode=CMD_READ, address=address, length=length, source=2)
        await ctx.tick().repeat(48)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=dut.op, model=model),
        partial(collect_responses, bus=dut.input, p=CHAIN_IN, out=got)], deadline=2000)

    want = [model.word(address + 4 * i) for i in range((length + 1) // 4)]
    assert [b["data"] for b in got] == want
    assert [b["last"] for b in got] == [0] * (len(want) - 1) + [1]


def test_aligner_write_masks_the_padding_beats():
    dut = Aligner(CHAIN_IN, ALIGN_LOG2)
    model = BurstSlaveModel(4096, b"\xAA" * 4096)
    got: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.input, CHAIN_IN)
        # One word at 0x44: block 0x40..0x4F, so one pre-padding beat and two
        # post-padding beats, all with mask 0.
        await mst.send(opcode=CMD_WRITE, address=0x44, length=3,
                       data=0x01020304, mask=0xF, source=1, last=1)
        await ctx.tick().repeat(40)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=dut.op, model=model),
        partial(collect_responses, bus=dut.input, p=CHAIN_IN, out=got)], deadline=2000)

    assert len(model.cmd_log) == 4, [b["mask"] for b in model.cmd_log]
    assert [b["mask"] for b in model.cmd_log] == [0, 0xF, 0, 0]
    assert model.mem[0x40:0x44] == b"\xAA" * 4
    assert model.mem[0x44:0x48] == (0x01020304).to_bytes(4, "little")
    assert model.mem[0x48:0x50] == b"\xAA" * 8
    assert len(got) == 1 and got[0]["last"] == 1 and got[0]["source"] == 1


def test_length_fixer_splits_a_burst_into_fixed_pieces():
    aligner = _layout_only(Aligner(CHAIN_IN, ALIGN_LOG2))
    dut = LengthFixer(aligner.op, ALIGN_LOG2)
    p = aligner.op
    rng = random.Random(11)
    model = BurstSlaveModel(4096, bytes(rng.getrandbits(8) for _ in range(4096)))
    got: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.input, p)
        # A 32-byte aligned read: two 16-byte output bursts.
        await mst.send(opcode=CMD_READ, address=0x80, length=31, source=0, context=0)
        await ctx.tick().repeat(60)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=dut.op, model=model),
        partial(collect_responses, bus=dut.input, p=p, out=got)], deadline=2000)

    assert [b["address"] for b in model.cmd_log] == [0x80, 0x90]
    assert all(b["length"] == 15 for b in model.cmd_log)
    assert [b["data"] for b in got] == [model.word(0x80 + 4 * i) for i in range(8)]
    assert [b["last"] for b in got] == [0] * 7 + [1]


def test_aligner_then_length_fixer_read_golden_traffic():
    """The real ``BmbToAxi4Mem`` chain, scaled down."""
    aligner = Aligner(CHAIN_IN, ALIGN_LOG2)
    fixer = LengthFixer(aligner.op, ALIGN_LOG2)

    from amaranth import Module
    from amaranth.lib import wiring
    from avoodoo.lib.membus import MemBusSignature, connect

    class Chain(wiring.Component):
        def __init__(self):
            super().__init__({
                "input": wiring.In(MemBusSignature(CHAIN_IN)),
                "output": wiring.Out(MemBusSignature(fixer.op)),
            })

        def elaborate(self, platform):
            m = Module()
            m.submodules.aligner = aligner
            m.submodules.fixer = fixer
            connect(m, aligner.input, self.input)
            connect(m, fixer.input, aligner.output)
            connect(m, self.output, fixer.output)
            return m

    dut = Chain()
    rng = random.Random(23)
    model = BurstSlaveModel(4096, bytes(rng.getrandbits(8) for _ in range(4096)))
    cases = [(0x100, 3), (0x104, 7), (0x10C, 3), (0x108, 7), (0x100, 15)]
    got: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.input, CHAIN_IN)
        for addr, length in cases:
            await mst.send(opcode=CMD_READ, address=addr, length=length, source=1)
            await ctx.tick().repeat(60)
        await ctx.tick().repeat(40)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=fixer.op, model=model),
        partial(collect_responses, bus=dut.input, p=CHAIN_IN, out=got)], deadline=8000)

    want: list[int] = []
    for addr, length in cases:
        want += [model.word(addr + 4 * i) for i in range((length + 1) // 4)]
    assert [b["data"] for b in got] == want
    # Every command the memory saw is a full aligned 16-byte burst.
    assert all(b["length"] == 15 and b["address"] % 16 == 0 for b in model.cmd_log)


# ==========================================================================
# 3f. AlignedSplitter
# ==========================================================================

SPLIT_IN = MemBusParameter.simple(address_width=16, data_width=32, source_width=1,
                                  context_width=0, length_width=6,
                                  alignment=BurstAlignment.BYTE)
SPLIT_MAX = 16


@pytest.mark.parametrize("address,length", [
    (0x100, 15),      # exactly one piece
    (0x100, 31),      # two full pieces
    (0x104, 15),      # head + tail
    (0x108, 27),      # head + body + tail
    (0x10C, 3),       # one word at the end of a block
    (0x100, 63),      # four full pieces
])
def test_aligned_splitter_read_never_crosses_a_boundary(address, length):
    dut = AlignedSplitter(SPLIT_IN, SPLIT_MAX)
    rng = random.Random(address + length)
    model = BurstSlaveModel(4096, bytes(rng.getrandbits(8) for _ in range(4096)))
    got: list[dict] = []

    async def main(ctx):
        mst = Master(ctx, dut.input, SPLIT_IN)
        await mst.send(opcode=CMD_READ, address=address, length=length, source=1)
        await ctx.tick().repeat(120)

    run(dut, main, processes=[
        partial(burst_slave, bus=dut.output, p=dut.op, model=model),
        partial(collect_responses, bus=dut.input, p=SPLIT_IN, out=got)], deadline=4000)

    want = [model.word(address + 4 * i) for i in range((length + 1) // 4)]
    assert [b["data"] for b in got] == want, (address, length)
    assert [b["last"] for b in got] == [0] * (len(want) - 1) + [1]
    for beat in model.cmd_log:
        start = beat["address"]
        end = start + beat["length"]
        assert beat["length"] < SPLIT_MAX
        assert start // SPLIT_MAX == end // SPLIT_MAX, \
            f"piece {start:#x}..{end:#x} crosses a {SPLIT_MAX}-byte boundary"


# ==========================================================================
# 3g. avoodoo.bus.routing -- the core-local BmbRouting.scala
# ==========================================================================


def test_routing_splices_one_master_onto_two_slaves():
    """``driveCmdFrom`` + ``routeRspTo``, the way ``CoreCpuFrontdoor`` uses them.

    The wide master (24-bit address, 4-bit source) is spliced onto two narrow
    slaves (12-bit address, 4-bit source) chosen by address bit 12, with the
    address *resized* rather than width-checked -- which is the whole point of
    the Scala helper and the thing a port can silently get wrong.
    """
    from amaranth import Module, Mux, Signal
    from amaranth.lib import wiring

    from avoodoo.bus.routing import drive_cmd_from, route_rsp_to
    from avoodoo.lib.membus import MemBusSignature

    wide = MemBusParameter.simple(address_width=24, data_width=32, source_width=4,
                                  context_width=0, length_width=2,
                                  alignment=BurstAlignment.WORD)
    narrow = MemBusParameter.simple(address_width=12, data_width=32, source_width=4,
                                    context_width=0, length_width=2,
                                    alignment=BurstAlignment.WORD)

    class Splice(wiring.Component):
        def __init__(self):
            super().__init__({
                "cpu": wiring.In(MemBusSignature(wide)),
                "a": wiring.Out(MemBusSignature(narrow)),
                "b": wiring.Out(MemBusSignature(narrow)),
            })

        def elaborate(self, platform):
            m = Module()
            addr = self.cpu.cmd.payload.fragment.address
            sel_b = Signal()
            m.d.comb += sel_b.eq(addr[12])
            drive_cmd_from(m, self.a, self.cpu, self.cpu.cmd.valid & ~sel_b, addr)
            drive_cmd_from(m, self.b, self.cpu, self.cpu.cmd.valid & sel_b, addr)
            m.d.comb += self.cpu.cmd.ready.eq(
                Mux(sel_b, self.b.cmd.ready, self.a.cmd.ready))
            # Defaults, then the two mutually exclusive `when` overrides.
            m.d.comb += self.cpu.rsp.valid.eq(0)
            route_rsp_to(m, ~sel_b, self.a, self.cpu)
            route_rsp_to(m, sel_b, self.b, self.cpu)
            return m

    dut = Splice()
    model_a = BurstSlaveModel(4096, bytes((i * 11) & 0xFF for i in range(4096)))
    model_b = BurstSlaveModel(4096, bytes((i * 13) & 0xFF for i in range(4096)))
    got: list[dict] = []
    cases = [(0x000040, model_a), (0x001040, model_b), (0x000080, model_a)]

    async def main(ctx):
        mst = Master(ctx, dut.cpu, wide)
        for addr, _ in cases:
            await mst.send(opcode=CMD_READ, address=addr, length=3, source=5)
            await ctx.tick().repeat(12)
        await ctx.tick().repeat(20)

    from functools import partial as _p
    run(dut, main, processes=[
        _p(burst_slave, bus=dut.a, p=narrow, model=model_a),
        _p(burst_slave, bus=dut.b, p=narrow, model=model_b),
        _p(collect_responses, bus=dut.cpu, p=wide, out=got)], deadline=4000)

    assert len(got) == len(cases)
    for (addr, model), beat in zip(cases, got):
        # The 24-bit address is truncated to the slave's 12 bits, as .resize does.
        assert beat["data"] == model.word(addr & 0xFFF)
        assert beat["source"] == 5
        assert beat["last"] == 1
    assert len(model_a.cmd_log) == 2 and len(model_b.cmd_log) == 1
    assert [b["address"] for b in model_a.cmd_log] == [0x040, 0x080]
    assert model_b.cmd_log[0]["address"] == 0x040
