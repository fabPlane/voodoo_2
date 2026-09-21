"""Unit tests for the Phase 1.5 AXI library (plan L0).

Three layers, as in ``tests/test_membus.py``:

1. **Configs and layouts** -- field presence, order and widths against the
   SpinalHDL 1.15.0 bundles (``Axi4Channel.scala``, ``AxiLite4.scala``) and
   against the port lists Spinal actually emitted for the Console
   (``fpga/rtl/gowin/TangConsole138k.v``: ``AxiLite4ToBmbBridge``,
   ``BmbToAxi4Mem``, ``BmbToAxi4SharedBridge``, ``Axi4AddressRelocate``).
2. **Bridges under pysim** against small Python models: AXI-Lite write/read
   -> ``MemBus`` cmd/rsp (including the wedged / timeout paths with a short
   ``timeout_log2``), ``MemBus`` bursts -> AXI4 bursts with the right
   ``len``/``size``/``last``/``strb``, AXI4 -> AXI4-Lite single beats, and
   the address relocation.
3. **Clock crossing** with two genuinely different clocks (the lockstep diff
   can only run it single-clock): every beat pushed in comes out exactly
   once, in order, on every channel.

The cycle-exact proof is ``make diff MOD=<row>`` (``tests/diff/test_diff_axi.py``).
"""

from __future__ import annotations

import random
from functools import partial

import pytest
from amaranth import ClockDomain, Elaboratable, Module, Signal
from amaranth.back import verilog
from amaranth.lib import data
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.core import membus_params as mp
from avoodoo.gowin.axi4_address_relocate import Axi4AddressRelocate
from avoodoo.gowin.axilite4_clock_crossing import AxiLite4ClockCrossing
from avoodoo.lib import axi
from avoodoo.lib.axi import (RESP_OKAY, RESP_SLVERR, BURST_INCR, Axi4Config,
                             AxiLite4Config, axi4_ar_layout, axi4_arw_layout,
                             axi4_aw_layout, axi4_b_layout, axi4_r_layout,
                             axi4_w_layout, axilite4_ax_layout, axilite4_r_layout,
                             axilite4_w_layout)
from avoodoo.lib.axi4_to_axilite4 import Axi4ToAxiLite4
from avoodoo.lib.axi4_to_axilite4 import axi_config as a2l_config
from avoodoo.lib.axilite4_to_membus import (STATUS_ADDRESS, AxiLite4ToMemBusBridge,
                                            bar_address_width)
from avoodoo.lib.axilite4_to_membus import axi_config as lite_config
from avoodoo.lib.membus import (CMD_READ, CMD_WRITE, RSP_ERROR, RSP_SUCCESS,
                                cmd_layout, rsp_layout)
from avoodoo.lib.membus_to_axi4_mem import (MemBusToAxi4Mem, MemBusToAxi4SharedBridge,
                                            aligned_parameter, fb_axi_config,
                                            shared_bridge_axi_config, tex_axi_config)

C = Config.voodoo1()
CLK = 1e-6


# ==========================================================================
# 1. configs and layouts
# ==========================================================================


def _fields(layout: data.StructLayout) -> list[tuple[str, int, int]]:
    return [(k, f.offset, f.shape.width) for k, f in layout]


def test_bar_address_width_is_the_cpu_bus_width():
    assert bar_address_width() == 24 == mp.cpu_bmb_params().access.address_width


def test_axilite4_layouts_match_the_scala_bundles():
    c = lite_config(24)
    assert c == AxiLite4Config(24, 32)
    assert _fields(axilite4_ax_layout(c)) == [("addr", 0, 24), ("prot", 24, 3)]
    assert _fields(axilite4_w_layout(c)) == [("data", 0, 32), ("strb", 32, 4)]
    assert _fields(axilite4_r_layout(c)) == [("data", 0, 32), ("resp", 32, 2)]
    with pytest.raises(ValueError):
        AxiLite4Config(24, 16)


def test_bmb_to_axi4_shared_config_matches_tangconsole_ports():
    """``module BmbToAxi4Mem`` in TangConsole138k.v: aw = addr[25:0], len[7:0],
    size[2:0], burst[1:0], cache[3:0], prot[2:0]; w = data, strb, last;
    b = resp; r = data, resp, last -- no id (idWidth 0), no qos/region/lock."""
    cfg = fb_axi_config(C)
    assert cfg.address_width == 26 and cfg.data_width == 32 and cfg.id_width == 0
    assert _fields(axi4_aw_layout(cfg)) == [
        ("addr", 0, 26), ("len", 26, 8), ("size", 34, 3), ("burst", 37, 2),
        ("cache", 39, 4), ("prot", 43, 3)]
    assert _fields(axi4_ar_layout(cfg)) == _fields(axi4_aw_layout(cfg))
    assert _fields(axi4_arw_layout(cfg))[-1] == ("write", 46, 1)
    assert _fields(axi4_w_layout(cfg)) == [("data", 0, 32), ("strb", 32, 4), ("last", 36, 1)]
    assert _fields(axi4_b_layout(cfg)) == [("resp", 0, 2)]
    assert _fields(axi4_r_layout(cfg)) == [("data", 0, 32), ("resp", 32, 2), ("last", 34, 1)]
    assert tex_axi_config(C) == cfg          # same widths, only the Bmb source differs
    assert shared_bridge_axi_config(aligned_parameter(mp.fb_mem_bmb_params(C), 8)) == cfg


def test_axi4_to_axilite4_config_field_order():
    """``Axi4ToAxiLite4.axiConfig``: id 4, no lock/qos/region/cache."""
    cfg = a2l_config(24)
    assert _fields(axi4_aw_layout(cfg)) == [
        ("addr", 0, 24), ("id", 24, 4), ("len", 28, 8), ("size", 36, 3),
        ("burst", 39, 2), ("prot", 41, 3)]
    assert _fields(axi4_w_layout(cfg)) == [("data", 0, 32), ("strb", 32, 4), ("last", 36, 1)]
    assert _fields(axi4_b_layout(cfg)) == [("id", 0, 4), ("resp", 4, 2)]
    assert _fields(axi4_r_layout(cfg)) == [
        ("data", 0, 32), ("id", 32, 4), ("resp", 36, 2), ("last", 38, 1)]


def test_axi4_config_derived_widths_and_checks():
    full = Axi4Config(32, 64, id_width=3)
    assert full.len_width == 8 and full.size_width == 3 and full.lock_width == 1
    assert full.byte_per_word == 8 and full.symbol_width == 3
    a3 = Axi4Config(32, 32, id_width=1, with_axi3=True)
    assert a3.len_width == 4 and a3.size_width == 4 and a3.lock_width == 2 and a3.use_wid
    assert _fields(axi4_w_layout(a3))[-1] == ("id", 37, 1)
    with pytest.raises(ValueError):
        Axi4Config(32, 32)                   # use_id without id_width
    with pytest.raises(ValueError):
        Axi4Config(32, 24, id_width=1)       # bad data width
    lite = Axi4Config(32, 32, use_id=False, use_region=False, use_burst=False,
                      use_lock=False, use_cache=False, use_size=False, use_qos=False,
                      use_len=False, use_last=False, use_resp=False, use_prot=False,
                      use_strb=False)
    assert _fields(axi4_aw_layout(lite)) == [("addr", 0, 32)]
    assert _fields(axi4_b_layout(lite)) == []
    assert lite.to_full_config().use_id and lite.to_full_config().id_width == 1


def test_signature_port_names_follow_the_spinal_flattening():
    """``io_bar_aw_valid`` / ``io_bar_aw_payload_addr`` <-> ``bar__aw__valid`` /
    ``bar__aw__payload`` (addr at bit 0, prot at bit 24)."""
    from amaranth.lib import wiring
    from amaranth.lib.wiring import In

    cfg = lite_config(24)

    class T(wiring.Component):
        def __init__(self):
            super().__init__({"bar": In(axi.AxiLite4Signature(cfg))})

        def elaborate(self, platform):
            m = Module()
            m.d.comb += self.bar.aw.ready.eq(self.bar.aw.payload.prot[0])
            m.d.comb += self.bar.r.payload.data.eq(self.bar.w.payload.data)
            return m

    v = verilog.convert(T(), name="T")
    ports = {l.strip().rstrip(";").split()[-1] for l in v.splitlines()
             if l.strip().startswith(("input", "output"))}
    for ch in ("aw", "w", "b", "ar", "r"):
        assert {f"bar__{ch}__valid", f"bar__{ch}__ready", f"bar__{ch}__payload"} <= ports
    assert "input [26:0] bar__aw__payload;" in v


# ==========================================================================
# pysim plumbing
# ==========================================================================


class _Clocked(Elaboratable):
    def __init__(self, dut):
        self.dut = dut
        self.rst = Signal()

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain()
        m.d.comb += cd.rst.eq(self.rst)
        m.submodules.dut = self.dut
        return m


def run(dut, *testbenches, processes=(), deadline=20000):
    top = _Clocked(dut)
    sim = Simulator(top)
    sim.add_clock(CLK)
    for tb in testbenches:
        sim.add_testbench(tb)
    for pr in processes:
        sim.add_testbench(pr, background=True)
    sim.run_until(deadline * CLK)


async def _send(ctx, stream, payload_value):
    ctx.set(stream.payload, payload_value)
    ctx.set(stream.valid, 1)
    await ctx.tick().until(stream.ready)
    ctx.set(stream.valid, 0)


async def _recv(ctx, stream, *, ready=1):
    """Hold ``ready``, return the payload as it was on the edge that fired.

    Sampling *at* the edge matters: a source may change its payload right
    after the handshake, so a ``get`` after ``until(valid)`` is one delta
    too late.
    """
    ctx.set(stream.ready, ready)
    async for _clk, _rst, valid, payload in ctx.tick().sample(stream.valid, stream.payload):
        if valid:
            break
    ctx.set(stream.ready, 0)
    return payload


def _lay(layout, **fields):
    members = layout.members
    return layout.const({k: v for k, v in fields.items() if k in members})


def _int(v) -> int:
    """A ``data.Const``/``View`` sample or a plain int, as an int."""
    if hasattr(v, "as_value"):
        return int(v.as_value().value)
    return int(v)


# ==========================================================================
# 2a. AxiLite4ToMemBusBridge
# ==========================================================================


CPU = mp.cpu_bmb_params()
LITE = lite_config(24)


class MemBusSlaveModel:
    """A word memory behind the bridge's ``cpu_bus``; can be told to stall."""

    def __init__(self, *, accept_delay=0, rsp_delay=0, stall_forever=False,
                 error_addresses=()):
        self.mem: dict[int, int] = {}
        self.cmds: list[dict] = []
        self.accept_delay = accept_delay
        self.rsp_delay = rsp_delay
        self.stall_forever = stall_forever
        self.error_addresses = set(error_addresses)

    async def serve(self, ctx, bus):
        cl, rl = cmd_layout(CPU), rsp_layout(CPU)
        ctx.set(bus.cmd.ready, 0)
        ctx.set(bus.rsp.valid, 0)
        while True:
            await ctx.tick().until(bus.cmd.valid)
            if self.stall_forever:
                await ctx.tick().repeat(10_000)
            for _ in range(self.accept_delay):
                await ctx.tick()
            v = await _recv(ctx, bus.cmd)
            frag = {k: int(v.fragment[k]) for k in cl["fragment"].shape.members}
            frag["last"] = int(v.last)
            self.cmds.append(frag)
            addr = frag["address"]
            if frag["opcode"] == CMD_WRITE:
                old = self.mem.get(addr, 0)
                mask = frag["mask"]
                new = old
                for i in range(4):
                    if mask >> i & 1:
                        new = (new & ~(0xFF << (8 * i))) | (frag["data"] & (0xFF << (8 * i)))
                self.mem[addr] = new
            for _ in range(self.rsp_delay):
                await ctx.tick()
            ctx.set(bus.rsp.payload, _lay(
                rl, last=1,
                fragment=_lay(rl["fragment"].shape, source=frag.get("source", 0),
                              opcode=RSP_ERROR if addr in self.error_addresses else RSP_SUCCESS,
                              data=self.mem.get(addr, 0))))
            ctx.set(bus.rsp.valid, 1)
            await ctx.tick().until(bus.rsp.ready)
            ctx.set(bus.rsp.valid, 0)


class LiteMaster:
    def __init__(self, ctx, bus, cfg=LITE):
        self.ctx, self.bus, self.cfg = ctx, bus, cfg

    async def write(self, addr, data, strb=0xF):
        ctx, b = self.ctx, self.bus
        ctx.set(b.aw.payload, _lay(axilite4_ax_layout(self.cfg), addr=addr, prot=0))
        ctx.set(b.w.payload, _lay(axilite4_w_layout(self.cfg), data=data, strb=strb))
        ctx.set(b.aw.valid, 1)
        ctx.set(b.w.valid, 1)
        # AW and W are accepted together by this bridge, but do not rely on it.
        aw_done = w_done = False
        async for _clk, _rst, aw_r, w_r in ctx.tick().sample(b.aw.ready, b.w.ready):
            if aw_r and not aw_done:
                aw_done = True
                ctx.set(b.aw.valid, 0)
            if w_r and not w_done:
                w_done = True
                ctx.set(b.w.valid, 0)
            if aw_done and w_done:
                break
        resp = await _recv(ctx, b.b)
        return int(resp.resp)

    async def read(self, addr):
        ctx, b = self.ctx, self.bus
        await _send(ctx, b.ar, _lay(axilite4_ax_layout(self.cfg), addr=addr, prot=0))
        r = await _recv(ctx, b.r)
        return int(r.data), int(r.resp)


def test_lite_bridge_write_then_read_round_trip():
    dut = AxiLite4ToMemBusBridge(24, CPU)
    model = MemBusSlaveModel(accept_delay=2, rsp_delay=3)
    done = []

    async def main(ctx):
        m = LiteMaster(ctx, dut.axi)
        assert await m.write(0x1234, 0xDEADBEEF) == RESP_OKAY
        assert await m.write(0x1237, 0x000000AA, strb=0b0001) == RESP_OKAY   # unaligned -> word
        assert await m.read(0x1234) == (0xDEADBEAA, RESP_OKAY)
        assert await m.read(0x5678) == (0, RESP_OKAY)
        assert ctx.get(dut.wedged) == 0
        # every command was a single word beat, source 0, length 3, word-aligned
        for c in model.cmds:
            assert c["last"] == 1 and c["length"] == 3 and c["source"] == 0
            assert c["address"] & 3 == 0
        assert [c["opcode"] for c in model.cmds] == [CMD_WRITE, CMD_WRITE, CMD_READ, CMD_READ]
        assert model.cmds[1]["mask"] == 0b0001 and model.cmds[1]["address"] == 0x1234
        done.append(True)

    run(dut, main, processes=[partial(model.serve, bus=dut.cpu_bus)])
    assert done


def test_lite_bridge_write_wins_over_a_simultaneous_read():
    dut = AxiLite4ToMemBusBridge(24, CPU)
    model = MemBusSlaveModel()
    done = []

    async def main(ctx):
        b = dut.axi
        ctx.set(b.aw.payload, _lay(axilite4_ax_layout(LITE), addr=0x100, prot=0))
        ctx.set(b.w.payload, _lay(axilite4_w_layout(LITE), data=1, strb=0xF))
        ctx.set(b.ar.payload, _lay(axilite4_ax_layout(LITE), addr=0x200, prot=0))
        ctx.set(b.aw.valid, 1)
        ctx.set(b.w.valid, 1)
        ctx.set(b.ar.valid, 1)
        ctx.set(b.b.ready, 1)
        ctx.set(b.r.ready, 1)
        await ctx.tick().until(b.aw.ready)
        ctx.set(b.aw.valid, 0)
        ctx.set(b.w.valid, 0)
        await ctx.tick().until(b.ar.ready)
        ctx.set(b.ar.valid, 0)
        await ctx.tick().repeat(10)
        assert [c["opcode"] for c in model.cmds] == [CMD_WRITE, CMD_READ]
        assert [c["address"] for c in model.cmds] == [0x100, 0x200]
        done.append(True)

    run(dut, main, processes=[partial(model.serve, bus=dut.cpu_bus)])
    assert done


def test_lite_bridge_status_word_is_local_and_reports_error_responses():
    dut = AxiLite4ToMemBusBridge(24, CPU)
    model = MemBusSlaveModel(error_addresses={0x40})
    done = []

    async def main(ctx):
        m = LiteMaster(ctx, dut.axi)
        assert await m.read(STATUS_ADDRESS) == (0, RESP_OKAY)
        assert model.cmds == []                       # never reached the core
        assert await m.write(0x40, 5) == RESP_SLVERR  # slave error -> SLVERR, not wedged
        data, resp = await m.read(0x40)
        assert resp == RESP_SLVERR and ctx.get(dut.wedged) == 0
        assert await m.read(STATUS_ADDRESS) == (0, RESP_OKAY)
        done.append(True)

    run(dut, main, processes=[partial(model.serve, bus=dut.cpu_bus)])
    assert done


def test_lite_bridge_request_timeout_wedges_and_drops_writes():
    """The core never accepts: after 2**4 stalled cycles the bridge answers
    SLVERR, sets wedged + requestTimeoutSeen, counts the dropped write, and
    from then on serves writes/reads locally with SLVERR while the status
    word stays readable."""
    dut = AxiLite4ToMemBusBridge(24, CPU, timeout_log2=4)
    done = []

    async def main(ctx):
        m = LiteMaster(ctx, dut.axi)
        ctx.set(dut.cpu_bus.cmd.ready, 0)
        ctx.set(dut.cpu_bus.rsp.valid, 0)
        assert await m.write(0x10, 1) == RESP_SLVERR
        assert ctx.get(dut.wedged) == 1
        status, resp = await m.read(STATUS_ADDRESS)
        assert resp == RESP_OKAY
        assert status & 1 and status & 2 and not status & 4     # wedged, requestTimeout
        assert (status >> 8) & 0xFF == 1                          # one dropped write
        assert await m.write(0x14, 2) == RESP_SLVERR              # dropped locally
        assert await m.read(0x14) == (0, RESP_SLVERR)
        status, _ = await m.read(STATUS_ADDRESS)
        assert (status >> 8) & 0xFF == 2
        done.append(True)

    run(dut, main)
    assert done


def test_lite_bridge_response_timeout_wedges():
    dut = AxiLite4ToMemBusBridge(24, CPU, timeout_log2=4)
    done = []

    async def slave(ctx):
        # accept the command, never answer
        ctx.set(dut.cpu_bus.cmd.ready, 1)
        ctx.set(dut.cpu_bus.rsp.valid, 0)
        await ctx.tick().repeat(10_000)

    async def main(ctx):
        m = LiteMaster(ctx, dut.axi)
        assert await m.read(0x20) == (0, RESP_SLVERR)
        assert ctx.get(dut.wedged) == 1
        status, resp = await m.read(STATUS_ADDRESS)
        assert resp == RESP_OKAY and status & 0b101 == 0b101       # wedged + responseTimeout
        assert (status >> 8) & 0xFF == 0
        done.append(True)

    run(dut, main, processes=[slave])
    assert done


def test_lite_bridge_dropped_write_counter_saturates():
    dut = AxiLite4ToMemBusBridge(24, CPU, timeout_log2=2)
    done = []

    async def main(ctx):
        m = LiteMaster(ctx, dut.axi)
        ctx.set(dut.cpu_bus.cmd.ready, 0)
        for _ in range(260):
            await m.write(0x10, 1)
        status, _ = await m.read(STATUS_ADDRESS)
        assert (status >> 8) & 0xFF == 0xFF
        done.append(True)

    run(dut, main, deadline=60000)
    assert done


# ==========================================================================
# 2b. MemBusToAxi4Mem / MemBusToAxi4SharedBridge
# ==========================================================================


FB = mp.fb_mem_bmb_params(C)
FB_AXI = fb_axi_config(C)


class Axi4SlaveModel:
    """A byte-addressed memory on the AXI4 side, checking burst legality.

    Every ``AW``/``AR`` must be ``INCR`` with ``size = 2``; the ``W`` beats
    that follow must total ``len + 1`` with ``last`` on exactly the final
    one; reads return ``len + 1`` beats with the same rule.
    """

    def __init__(self, cfg=FB_AXI, *, seed=1, w_ready_density=1.0, r_valid_density=1.0):
        self.cfg = cfg
        self.mem = bytearray(1 << 20)
        self.aws: list[dict] = []
        self.ars: list[dict] = []
        self.wbeats: list[dict] = []
        self.rng = random.Random(seed)
        self.w_ready_density = w_ready_density
        self.r_valid_density = r_valid_density
        self.errors: list[str] = []

    def _check_ax(self, ax):
        if int(ax.burst) != BURST_INCR:
            self.errors.append("burst not INCR")
        if int(ax.size) != 2:
            self.errors.append(f"size {int(ax.size)}")
        if int(ax.prot) != axi.PROT_DEFAULT or int(ax.cache) != axi.CACHE_DEFAULT:
            self.errors.append("prot/cache not the BmbToAxi4SharedBridge constants")
        end = int(ax.addr) + 4 * (int(ax.len) + 1) - 1
        if end >> 12 != int(ax.addr) >> 12:
            self.errors.append(f"burst crosses 4 KiB: {int(ax.addr):#x} len {int(ax.len)}")

    async def _take(self, ctx, stream, density):
        """Hold ``ready`` at ``density`` until a beat fires; return its payload."""
        while True:
            rdy = int(self.rng.random() < density)
            ctx.set(stream.ready, rdy)
            async for _clk, _rst, valid, payload in ctx.tick().sample(stream.valid,
                                                                       stream.payload):
                break
            if rdy and valid:
                ctx.set(stream.ready, 0)
                return payload

    async def write_side(self, ctx, a):
        ctx.set(a.aw.ready, 0)
        ctx.set(a.w.ready, 0)
        ctx.set(a.b.valid, 0)
        while True:
            aw = await self._take(ctx, a.aw, 1.0)
            self._check_ax(aw)
            addr, n = int(aw.addr), int(aw.len) + 1
            self.aws.append({"addr": addr, "len": int(aw.len)})
            for i in range(n):
                w = await self._take(ctx, a.w, self.w_ready_density)
                self.wbeats.append({"data": int(w.data), "strb": int(w.strb), "last": int(w.last)})
                if int(w.last) != (i == n - 1):
                    self.errors.append(f"w.last wrong at beat {i}/{n}")
                for k in range(4):
                    if int(w.strb) >> k & 1:
                        self.mem[(addr + 4 * i + k) & 0xFFFFF] = (int(w.data) >> (8 * k)) & 0xFF
            ctx.set(a.b.payload, _lay(axi4_b_layout(self.cfg), resp=RESP_OKAY))
            ctx.set(a.b.valid, 1)
            await ctx.tick().until(a.b.ready)
            ctx.set(a.b.valid, 0)

    async def read_side(self, ctx, a):
        ctx.set(a.ar.ready, 0)
        ctx.set(a.r.valid, 0)
        while True:
            ar = await self._take(ctx, a.ar, 1.0)
            self._check_ax(ar)
            addr, n = int(ar.addr), int(ar.len) + 1
            self.ars.append({"addr": addr, "len": int(ar.len)})
            for i in range(n):
                while self.rng.random() >= self.r_valid_density:
                    await ctx.tick()
                lo = (addr + 4 * i) & 0xFFFFF
                word = int.from_bytes(self.mem[lo:lo + 4], "little")
                ctx.set(a.r.payload, _lay(axi4_r_layout(self.cfg), data=word, resp=RESP_OKAY,
                                          last=int(i == n - 1)))
                ctx.set(a.r.valid, 1)
                await ctx.tick().until(a.r.ready)
                ctx.set(a.r.valid, 0)

    def processes(self, a):
        return [partial(self.write_side, a=a), partial(self.read_side, a=a)]


class MemBusMaster:
    def __init__(self, ctx, bus, p):
        self.ctx, self.bus, self.p = ctx, bus, p
        self.cl, self.rl = cmd_layout(p), rsp_layout(p)

    async def write_burst(self, address, words, *, source=0, mask=0xF):
        """A Bmb write burst: one beat per word, the burst's start address on
        every beat.

        The address must be *held* across the beats here: ``BmbAligner``
        recomputes its output ``length`` from the current beat's address, and
        ``BmbLengthFixer`` derives ``context.last`` from that, so a beat whose
        address has moved past a 256-byte boundary would make the fixer
        swallow the final response (the Amaranth port does exactly what the
        Spinal does -- see the lockstep diff).  The framebuffer masters that
        feed ``BmbToAxi4Mem`` in the core never cross one within a burst.
        """
        ctx = self.ctx
        length = 4 * len(words) - 1
        for i, w in enumerate(words):
            await _send(ctx, self.bus.cmd, _lay(
                self.cl, last=int(i == len(words) - 1),
                fragment=_lay(self.cl["fragment"].shape, source=source, opcode=CMD_WRITE,
                              address=address, length=length, data=w, mask=mask)))
        return await self.collect(1)

    async def read_burst(self, address, count, *, source=0):
        """One read command beat; response beats are collected *while* the
        command is still pending, because ``BmbLengthFixer`` holds a read
        command until its last sub-burst has been issued and the AXI side may
        not issue that before the earlier sub-bursts' data has drained."""
        ctx, bus = self.ctx, self.bus
        ctx.set(bus.cmd.payload, _lay(
            self.cl, last=1,
            fragment=_lay(self.cl["fragment"].shape, source=source, opcode=CMD_READ,
                          address=address, length=4 * count - 1)))
        ctx.set(bus.cmd.valid, 1)
        ctx.set(bus.rsp.ready, 1)
        out = []
        async for _clk, _rst, cmd_ready, valid, v in ctx.tick().sample(
                bus.cmd.ready, bus.rsp.valid, bus.rsp.payload):
            if cmd_ready:
                ctx.set(bus.cmd.valid, 0)
            if valid:
                out.append({"last": int(v.last), "opcode": int(v.fragment.opcode),
                            "data": int(v.fragment.data), "source": int(v.fragment.source)})
            if len(out) >= count:
                break
        ctx.set(bus.rsp.ready, 0)
        assert out[-1]["last"] == 1 and all(not o["last"] for o in out[:-1])
        return out

    async def collect(self, beats):
        ctx = self.ctx
        out = []
        ctx.set(self.bus.rsp.ready, 1)
        async for _clk, _rst, valid, v in ctx.tick().sample(self.bus.rsp.valid,
                                                            self.bus.rsp.payload):
            if valid:
                out.append({"last": int(v.last), "opcode": int(v.fragment.opcode),
                            "data": int(v.fragment.data), "source": int(v.fragment.source)})
            if len(out) >= beats:
                break
        ctx.set(self.bus.rsp.ready, 0)
        assert out[-1]["last"] == 1 and all(not o["last"] for o in out[:-1])
        return out


def test_mem_bridge_aligned_write_becomes_one_full_axi_burst():
    """A 256-byte, 256-aligned write -> one AXI burst of 64 beats, all strb 0xF."""
    dut = MemBusToAxi4Mem(FB, 8)
    model = Axi4SlaveModel()
    done = []

    async def main(ctx):
        mb = MemBusMaster(ctx, dut.input, FB)
        words = [0x1000_0000 + i for i in range(64)]
        rsp = await mb.write_burst(0x1100, words, source=5)
        assert rsp == [{"last": 1, "opcode": RSP_SUCCESS, "data": 0, "source": 5}]
        await ctx.tick().repeat(4)
        assert model.aws == [{"addr": 0x1100, "len": 63}]
        assert [b["data"] for b in model.wbeats] == words
        assert all(b["strb"] == 0xF for b in model.wbeats)
        assert model.errors == []
        done.append(True)

    run(dut, main, processes=model.processes(dut.output))
    assert done


def test_mem_bridge_unaligned_write_is_padded_with_zero_strobes():
    """A 3-word write at +0x1F8 straddles a 256-byte boundary: the aligner
    pads both ends, the fixer splits it into two 64-beat bursts; the padding
    beats carry strb 0 so memory is untouched around the data."""
    dut = MemBusToAxi4Mem(FB, 8)
    model = Axi4SlaveModel()
    for i in range(len(model.mem)):
        model.mem[i] = 0xEE
    done = []

    async def main(ctx):
        mb = MemBusMaster(ctx, dut.input, FB)
        rsp = await mb.write_burst(0x21F8, [0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC])
        assert rsp[0]["opcode"] == RSP_SUCCESS
        await ctx.tick().repeat(4)
        assert model.aws == [{"addr": 0x2100, "len": 63}, {"addr": 0x2200, "len": 63}]
        assert len(model.wbeats) == 128
        strbs = [b["strb"] for b in model.wbeats]
        assert strbs[62:66] == [0xF, 0xF, 0xF, 0]
        assert sum(strbs) == 3 * 0xF
        assert model.errors == []
        mem = model.mem
        assert mem[0x21F4:0x21F8] == b"\xee" * 4
        assert int.from_bytes(mem[0x21F8:0x21FC], "little") == 0xAAAAAAAA
        assert int.from_bytes(mem[0x21FC:0x2200], "little") == 0xBBBBBBBB
        assert int.from_bytes(mem[0x2200:0x2204], "little") == 0xCCCCCCCC
        assert mem[0x2204:0x2208] == b"\xee" * 4
        done.append(True)

    run(dut, main, processes=model.processes(dut.output))
    assert done


def test_mem_bridge_read_returns_only_the_requested_words():
    dut = MemBusToAxi4Mem(FB, 8)
    model = Axi4SlaveModel(w_ready_density=0.5, r_valid_density=0.6)
    for i in range(0x4000, 0x4400, 4):
        model.mem[i:i + 4] = (i * 3 & 0xFFFFFFFF).to_bytes(4, "little")
    done = []

    async def main(ctx):
        mb = MemBusMaster(ctx, dut.input, FB)
        # 5 words starting 3 words before a 256-byte boundary
        rsp = await mb.read_burst(0x40F4, 5, source=9)
        assert [r["data"] for r in rsp] == [(a * 3) & 0xFFFFFFFF
                                            for a in range(0x40F4, 0x40F4 + 20, 4)]
        assert all(r["opcode"] == RSP_SUCCESS and r["source"] == 9 for r in rsp)
        await ctx.tick().repeat(4)
        assert model.ars == [{"addr": 0x4000, "len": 63}, {"addr": 0x4100, "len": 63}]
        assert model.errors == []
        # a whole 2 KiB burst (the Bmb maximum, 512 words) -> 8 AXI bursts
        rsp = await mb.read_burst(0x4000, 512)
        await ctx.tick().repeat(4)
        assert len(rsp) == 512 and len(model.ars) == 2 + 8
        assert [r["data"] for r in rsp][:256] == [(a * 3) & 0xFFFFFFFF
                                                  for a in range(0x4000, 0x4400, 4)]
        done.append(True)

    run(dut, main, processes=model.processes(dut.output), deadline=40000)
    assert done


def test_shared_bridge_holds_a_command_that_would_mix_read_and_write():
    """``hazard``: with a read in flight a write is held at the input until the
    read has fully responded (and vice versa)."""
    p = aligned_parameter(FB, 8)
    dut = MemBusToAxi4SharedBridge(p)
    cl = cmd_layout(p)
    done = []

    async def main(ctx):
        bus, out = dut.input, dut.output
        ctx.set(out.arw.ready, 1)
        ctx.set(out.w.ready, 1)
        ctx.set(bus.rsp.ready, 1)
        # a read command, accepted immediately by the AXI side
        await _send(ctx, bus.cmd, _lay(cl, last=1, fragment=_lay(
            cl["fragment"].shape, opcode=CMD_READ, address=0x100, length=255, context=7)))
        arw = ctx.get(out.arw.payload)
        # the arw beat was on the bus during the accepting edge
        # now a write: must stall while the read is pending
        ctx.set(bus.cmd.payload, _lay(cl, last=1, fragment=_lay(
            cl["fragment"].shape, opcode=CMD_WRITE, address=0x200, length=255,
            data=1, mask=0xF, context=9)))
        ctx.set(bus.cmd.valid, 1)
        for _ in range(10):
            await ctx.tick()
            assert ctx.get(bus.cmd.ready) == 0
        # answer the read: 64 beats
        for i in range(64):
            ctx.set(out.r.payload, _lay(axi4_r_layout(dut.axi_config), data=i, resp=RESP_OKAY,
                                        last=int(i == 63)))
            ctx.set(out.r.valid, 1)
            await ctx.tick().until(out.r.ready)
            ctx.set(out.r.valid, 0)
        await ctx.tick().repeat(2)
        assert ctx.get(bus.cmd.ready) == 1
        ctx.set(bus.cmd.valid, 0)
        done.append(True)

    run(dut, main)
    assert done


def test_shared_bridge_len_size_and_response_mapping():
    """``len = transferBeatCountMinusOne``, ``size = 2``, ``INCR``; SLVERR on
    ``b``/``r`` becomes a Bmb ERROR response carrying the command's context."""
    p = aligned_parameter(FB, 8)
    dut = MemBusToAxi4SharedBridge(p)
    cl = cmd_layout(p)
    done = []

    async def main(ctx):
        bus, out = dut.input, dut.output
        ctx.set(out.arw.ready, 1)
        ctx.set(out.w.ready, 1)
        ctx.set(bus.rsp.ready, 1)
        seen = []

        async def watch_arw():
            while True:
                if ctx.get(out.arw.valid) and ctx.get(out.arw.ready):
                    seen.append(ctx.get(out.arw.payload))
                await ctx.tick()

        # a 2-beat write burst (length 7)
        for i in range(2):
            await _send(ctx, bus.cmd, _lay(cl, last=int(i == 1), fragment=_lay(
                cl["fragment"].shape, opcode=CMD_WRITE, address=0x300, length=7,
                data=0x10 + i, mask=0xF, context=0x5A)))
        await ctx.tick().repeat(2)
        ctx.set(out.b.payload, _lay(axi4_b_layout(dut.axi_config), resp=RESP_SLVERR))
        ctx.set(out.b.valid, 1)
        await ctx.tick().until(bus.rsp.valid)
        v = ctx.get(bus.rsp.payload)
        assert int(v.last) == 1 and int(v.fragment.opcode) == RSP_ERROR
        assert int(v.fragment.context) == 0x5A
        await ctx.tick()
        ctx.set(out.b.valid, 0)
        done.append(True)

    async def arw_watch(ctx):
        while True:
            if ctx.get(dut.output.arw.valid) and ctx.get(dut.output.arw.ready):
                v = ctx.get(dut.output.arw.payload)
                assert int(v.len) == 1 and int(v.size) == 2 and int(v.burst) == BURST_INCR
                assert int(v.write) == 1 and int(v.addr) == 0x300
                done.append("arw")
            await ctx.tick()

    run(dut, main, processes=[arw_watch])
    assert "arw" in done and True in done


# ==========================================================================
# 2c. Axi4ToAxiLite4
# ==========================================================================


def test_axi4_to_axilite4_single_beats_with_ids():
    dut = Axi4ToAxiLite4(24)
    acfg, lcfg = dut.axi_config, dut.lite_config
    lite_mem: dict[int, int] = {}
    done = []

    async def lite_slave(ctx):
        l = dut.lite
        ctx.set(l.aw.ready, 0)
        ctx.set(l.w.ready, 0)
        ctx.set(l.ar.ready, 0)
        ctx.set(l.b.valid, 0)
        ctx.set(l.r.valid, 0)
        while True:
            await ctx.tick()
            if ctx.get(l.aw.valid) and ctx.get(l.w.valid):
                aw, w = ctx.get(l.aw.payload), ctx.get(l.w.payload)
                ctx.set(l.aw.ready, 1)
                ctx.set(l.w.ready, 1)
                await ctx.tick()
                ctx.set(l.aw.ready, 0)
                ctx.set(l.w.ready, 0)
                lite_mem[int(aw.addr)] = int(w.data)
                ctx.set(l.b.payload, _lay(axi.axilite4_b_layout(lcfg),
                                          resp=RESP_SLVERR if int(aw.addr) == 0xBAD else RESP_OKAY))
                ctx.set(l.b.valid, 1)
                await ctx.tick().until(l.b.ready)
                ctx.set(l.b.valid, 0)
            elif ctx.get(l.ar.valid):
                ar = ctx.get(l.ar.payload)
                ctx.set(l.ar.ready, 1)
                await ctx.tick()
                ctx.set(l.ar.ready, 0)
                ctx.set(l.r.payload, _lay(axilite4_r_layout(lcfg),
                                          data=lite_mem.get(int(ar.addr), 0),
                                          resp=axi.RESP_DECERR if int(ar.addr) == 0xBAD else RESP_OKAY))
                ctx.set(l.r.valid, 1)
                await ctx.tick().until(l.r.ready)
                ctx.set(l.r.valid, 0)

    async def main(ctx):
        a = dut.axi
        ctx.set(a.aw.payload, _lay(axi4_aw_layout(acfg), addr=0x10, id=0xA, len=0, size=2,
                                   burst=BURST_INCR, prot=0))
        ctx.set(a.w.payload, _lay(axi4_w_layout(acfg), data=0x12345678, strb=0xF, last=1))
        ctx.set(a.aw.valid, 1)
        ctx.set(a.w.valid, 1)
        await ctx.tick().until(a.aw.ready)
        ctx.set(a.aw.valid, 0)
        ctx.set(a.w.valid, 0)
        b = await _recv(ctx, a.b)
        assert int(b.id) == 0xA and int(b.resp) == RESP_OKAY
        await _send(ctx, a.ar, _lay(axi4_ar_layout(acfg), addr=0x10, id=0x3, len=0, size=2,
                                    burst=BURST_INCR, prot=0))
        r = await _recv(ctx, a.r)
        assert (int(r.data), int(r.id), int(r.resp), int(r.last)) == (0x12345678, 3, RESP_OKAY, 1)
        # SLVERR / DECERR both come back as SLVERR
        ctx.set(a.aw.payload, _lay(axi4_aw_layout(acfg), addr=0xBAD, id=1, len=0, size=2,
                                   burst=BURST_INCR, prot=0))
        ctx.set(a.aw.valid, 1)
        ctx.set(a.w.valid, 1)
        await ctx.tick().until(a.aw.ready)
        ctx.set(a.aw.valid, 0)
        ctx.set(a.w.valid, 0)
        assert int((await _recv(ctx, a.b)).resp) == RESP_SLVERR
        await _send(ctx, a.ar, _lay(axi4_ar_layout(acfg), addr=0xBAD, id=2, len=0, size=2,
                                    burst=BURST_INCR, prot=0))
        assert int((await _recv(ctx, a.r)).resp) == RESP_SLVERR
        done.append(True)

    run(dut, main, processes=[lite_slave])
    assert done


# ==========================================================================
# 2d. Axi4AddressRelocate
# ==========================================================================


def test_axi4_address_relocate_adds_the_offset_and_passes_everything_else():
    cfg = tex_axi_config(C)
    dut = Axi4AddressRelocate(cfg, 4 * 1024 * 1024)
    done = []

    async def main(ctx):
        i, o = dut.input, dut.output
        ctx.set(i.aw.payload, _lay(axi4_aw_layout(cfg), addr=0x1234, len=63, size=2,
                                   burst=BURST_INCR, cache=0xF, prot=2))
        ctx.set(i.ar.payload, _lay(axi4_ar_layout(cfg), addr=0x3FF_FF00, len=1, size=2,
                                   burst=BURST_INCR, cache=0xF, prot=2))
        ctx.set(i.aw.valid, 1)
        ctx.set(o.aw.ready, 1)
        ctx.set(i.w.payload, _lay(axi4_w_layout(cfg), data=7, strb=3, last=1))
        ctx.set(o.b.payload, _lay(axi4_b_layout(cfg), resp=RESP_SLVERR))
        ctx.set(o.b.valid, 1)
        ctx.set(o.r.payload, _lay(axi4_r_layout(cfg), data=9, resp=RESP_OKAY, last=1))
        await ctx.delay(CLK / 4)
        aw, ar = ctx.get(o.aw.payload), ctx.get(o.ar.payload)
        assert int(aw.addr) == 0x40_1234 and int(aw.len) == 63 and int(aw.cache) == 0xF
        assert int(ar.addr) == (0x3FF_FF00 + 0x40_0000) & 0x3FF_FFFF     # wraps at 26 bits
        assert ctx.get(o.aw.valid) == 1 and ctx.get(i.aw.ready) == 1
        assert int(ctx.get(o.w.payload).strb) == 3
        assert int(ctx.get(i.b.payload).resp) == RESP_SLVERR and ctx.get(i.b.valid) == 1
        assert int(ctx.get(i.r.payload).data) == 9
        done.append(True)

    sim = Simulator(dut)
    sim.add_testbench(main)
    sim.run()
    assert done


# ==========================================================================
# 3. AxiLite4ClockCrossing with two real clocks
# ==========================================================================


class _TwoClocks(Elaboratable):
    def __init__(self, dut):
        self.dut = dut

    def elaborate(self, platform):
        m = Module()
        m.domains.pcie = ClockDomain("pcie")
        m.domains.core = ClockDomain("core")
        m.submodules.dut = self.dut
        return m


@pytest.mark.parametrize("in_period,out_period", [(1e-6, 3.3e-6), (3.1e-6, 1e-6)])
def test_clock_crossing_conserves_every_beat_on_every_channel(in_period, out_period):
    cfg = lite_config(24)
    dut = AxiLite4ClockCrossing(cfg, input_domain="pcie", output_domain="core")
    N = 40
    rng = random.Random(3)
    sent = {ch: [] for ch in ("aw", "w", "ar", "b", "r")}
    got = {ch: [] for ch in sent}

    def payloads(ch):
        out = []
        for k in range(N):
            if ch in ("aw", "ar"):
                out.append(_lay(axilite4_ax_layout(cfg), addr=rng.getrandbits(24), prot=k & 7))
            elif ch == "w":
                out.append(_lay(axilite4_w_layout(cfg), data=rng.getrandbits(32), strb=k & 0xF))
            elif ch == "b":
                out.append(_lay(axi.axilite4_b_layout(cfg), resp=k & 3))
            else:
                out.append(_lay(axilite4_r_layout(cfg), data=rng.getrandbits(32), resp=k & 3))
        return out

    def producer(ch, stream, domain):
        async def tb(ctx):
            for v in payloads(ch):
                while rng.random() < 0.3:
                    await ctx.tick(domain)
                ctx.set(stream.payload, v)
                ctx.set(stream.valid, 1)
                await ctx.tick(domain).until(stream.ready)
                ctx.set(stream.valid, 0)
                sent[ch].append(_int(v))
        return tb

    def consumer(ch, stream, domain):
        async def tb(ctx):
            while len(got[ch]) < N:
                rdy = int(rng.random() < 0.7)
                ctx.set(stream.ready, rdy)
                await ctx.tick(domain)
                # sample what the edge consumed
            ctx.set(stream.ready, 0)
        return tb

    def sampler(ch, stream, domain):
        async def tb(ctx):
            async for _clk, _rst, valid, ready, payload in ctx.tick(domain).sample(
                    stream.valid, stream.ready, stream.payload):
                if valid and ready:
                    got[ch].append(_int(payload))
        return tb

    sim = Simulator(_TwoClocks(dut))
    sim.add_clock(in_period, domain="pcie")
    sim.add_clock(out_period, domain="core")
    i, o = dut.input, dut.output
    plan = [("aw", i.aw, "pcie", o.aw, "core"), ("w", i.w, "pcie", o.w, "core"),
            ("ar", i.ar, "pcie", o.ar, "core"), ("b", o.b, "core", i.b, "pcie"),
            ("r", o.r, "core", i.r, "pcie")]
    for ch, src, sdom, dst, ddom in plan:
        sim.add_testbench(producer(ch, src, sdom))
        sim.add_testbench(consumer(ch, dst, ddom), background=True)
        sim.add_testbench(sampler(ch, dst, ddom), background=True)
    sim.run_until(N * 40 * max(in_period, out_period))
    for ch in sent:
        assert got[ch] == sent[ch], ch
        assert len(got[ch]) == N


def test_clock_crossing_pop_side_stays_quiet_through_the_buffered_reset():
    """The pop side runs off a synchronised copy of the push reset: nothing
    comes out for at least two output clocks after reset release, and the
    FIFO is four deep (``depth = 4``) on every channel."""
    cfg = lite_config(24)
    dut = AxiLite4ClockCrossing(cfg)
    done = []

    async def main(ctx):
        i, o = dut.input, dut.output
        ctx.set(o.aw.ready, 0)
        # push four AW beats back to back -> the fifth is refused
        for k in range(4):
            ctx.set(i.aw.payload, _lay(axilite4_ax_layout(cfg), addr=k, prot=0))
            ctx.set(i.aw.valid, 1)
            await ctx.tick().until(i.aw.ready)
        ctx.set(i.aw.payload, _lay(axilite4_ax_layout(cfg), addr=4, prot=0))
        await ctx.tick().repeat(6)
        assert ctx.get(i.aw.ready) == 0
        ctx.set(i.aw.valid, 0)
        ctx.set(o.aw.ready, 1)
        seen = []
        for _ in range(20):
            if ctx.get(o.aw.valid):
                seen.append(int(ctx.get(o.aw.payload).addr))
            await ctx.tick()
        assert seen == [0, 1, 2, 3]
        done.append(True)

    run(dut, main)
    assert done
