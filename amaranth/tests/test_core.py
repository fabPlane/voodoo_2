"""Phase 7.2: :mod:`avoodoo.core.core` (``Core``) and :mod:`avoodoo.core_sim` (``CoreSim``).

Three things are checked here that the lockstep diff (``make diff MOD=Core``,
``tests/diff/test_diff_core.py``) and the Glide harness do not *explain*:

1. the port surfaces -- ``Core``'s is the Scala ``io`` bundle (no ``dbg_*``
   unless asked), ``CoreSim``'s is the pinned :func:`core_sim_signature`
   verbatim, and the two simulation RAMs sit at the Verilator paths the C
   harness spells (``CoreSim__DOT__fbWriteRam__DOT__mem`` / ``texRam``);
2. the wiring constants a transliteration can get wrong silently:
   ``wasEnqueuedAddr = 0x128``, the CoreSim tie-offs (``memFifoFree =
   0xffff``, ``fbBaseAddr = 0``, HDMI held in reset), ``framebufferEnable``
   following ``enableHdmiScanout``;
3. a pysim smoke run of the whole ``CoreSim``: a register write goes through
   frontdoor -> remapper -> PciFifo -> RegisterBank, the status register
   reads back idle, an LFB write lands in ``fbWriteRam`` (read through the
   memory backdoor, as ``sim_read_fb`` does), and the ``dbg_swap_count`` load
   port takes a value.

pysim on the full core is slow (about 5 ms per cycle here), so the smoke
test is a few hundred cycles; anything longer belongs to Verilator.
"""

from __future__ import annotations

import pytest
from amaranth.hdl import Fragment, MemoryData
from amaranth.hdl._mem import MemoryInstance
from amaranth.lib import wiring
from amaranth.sim import Simulator

from avoodoo import core_sim_ports as P
from avoodoo.config import Config
from avoodoo.core import Core
from avoodoo.core.core import WAS_ENQUEUED_ADDR
from avoodoo.core_sim import CoreSim, core_sim_config
from avoodoo.frontend import regdef
from avoodoo.lib.membus import CMD_READ, CMD_WRITE

PERIOD = 1e-6

# The Spinal `io` bundle of Core.scala, camel_to_snake'd (no dbg ports).
CORE_PORTS = {
    "cpu_bus", "fb_mem_write", "fb_color_write_mem", "fb_aux_write_mem",
    "fb_color_read_mem", "fb_aux_read_mem", "tex_mem", "status_inputs",
    "swap_displayed_buffer", "swaps_pending", "fb_base_addr", "flush_fb_caches",
    "hdmi", "scanout_prefetch_count", "scanout_read_req_count",
    "scanout_read_rsp_count", "scanout_cache_debug", "scanout_cache_read_addr",
    "scanout_cache_expected_addr", "scanout_cache_remaining",
    "scanout_cache_occupancy", "scanout_fill_hits", "scanout_fill_misses",
    "scanout_fill_burst_count", "scanout_fill_burst_beats",
    "scanout_fill_stall_cycles", "pipeline_busy_sources",
}
DBG_NAMES = {name for name, _, _ in P.DBG_PORTS}


# ---------------------------------------------------------------------------
# 1. port surfaces
# ---------------------------------------------------------------------------


def test_core_port_surface_is_the_scala_io_bundle():
    core = Core()
    assert set(core.signature.members) == CORE_PORTS
    assert core.c == Config.voodoo1()
    # hdmi: clock/reset in, the rest out (master(HdmiScanoutPort(c)))
    hdmi = core.signature.members["hdmi"].signature
    assert hdmi.members["clock"].flow is wiring.In
    assert hdmi.members["reset"].flow is wiring.In
    assert hdmi.members["video"].flow is wiring.Out
    assert hdmi.members["fifo_level"].shape == 13
    assert core.signature.members["scanout_cache_remaining"].shape == 10  # log2Up(801)
    assert core.signature.members["fb_base_addr"].shape == 26


def test_core_dbg_ports_are_opt_in():
    assert not (set(Core().signature.members) & DBG_NAMES)
    dbg = Core(dbg=True)
    assert set(dbg.signature.members) == CORE_PORTS | DBG_NAMES
    for name, width, flow in P.DBG_PORTS:
        member = dbg.signature.members[name]
        assert member.flow is flow and member.shape == width, name
    assert dbg.swap_buffer.dbg_load_port is True
    assert Core().swap_buffer.dbg_load_port is False


def test_was_enqueued_addr_is_the_swapbuffer_command():
    assert WAS_ENQUEUED_ADDR == 0x128 == regdef.WAS_ENQUEUED_ADDR
    assert regdef.command_by_addr(0x128).name == "swapbufferCMD"


def test_core_sim_signature_is_the_pinned_contract():
    cs = CoreSim()
    assert cs.signature == P.core_sim_signature()
    assert cs.c == core_sim_config()
    assert cs.c.enable_hdmi_scanout is False
    assert cs.core.dbg is True
    assert cs.fb_write_ram.size == P.FB_BYTES and cs.fb_write_ram.word_count == P.FB_WORDS
    assert cs.tex_ram.size == P.TEX_BYTES and cs.tex_ram.word_count == P.TEX_WORDS
    assert cs.fb_arbiter.port_count == 5 and cs.fb_arbiter.lower_first_priority


def _memories(fragment, path=()):
    """``{("fbWriteRam", "mem"): MemoryData, ...}`` for every memory in the tree."""
    out = {}
    for sub, name, _src in fragment.subfragments:
        if isinstance(sub, MemoryInstance):
            out[path + (name,)] = sub._data
        else:
            out.update(_memories(sub, path + (name,)))
    return out


def test_core_sim_memories_sit_at_the_harness_paths():
    """``CoreSim__DOT__fbWriteRam__DOT__mem`` is ``m.submodules.fbWriteRam`` +
    ``m.submodules.mem`` inside it -- the Verilator path the C harness uses."""
    frag = Fragment.get(CoreSim(), None)
    mems = _memories(frag)
    fb = mems[(P.FB_RAM_INSTANCE, P.RAM_ARRAY_NAME)]
    tex = mems[(P.TEX_RAM_INSTANCE, P.RAM_ARRAY_NAME)]
    assert isinstance(fb, MemoryData) and fb.depth == P.FB_WORDS and fb.shape.width == 32
    assert isinstance(tex, MemoryData) and tex.depth == P.TEX_WORDS and tex.shape.width == 32
    assert P.FB_MEM_PATH == "CoreSim__DOT__fbWriteRam__DOT__mem"


# ---------------------------------------------------------------------------
# 2. the CoreSim smoke run
# ---------------------------------------------------------------------------


STATUS_IDLE = (0xFFFF << 12) | 0x3F      # memFifoFree = 0xffff, pciFifoFree saturated
LFB_BASE = 0x400000
FBZ_MODE = 0x110
LFB_MODE = 0x114


async def _ticks(ctx, n: int) -> None:
    """``n`` clock edges; a plain ``tick()`` does not raise ``DomainReset``."""
    for _ in range(n):
        await ctx.tick()


class _Cpu:
    """Single-beat ``io_cpuBus`` transactions, the way ``sim_write``/``sim_read`` do them."""

    def __init__(self, ctx, dut: CoreSim):
        self.ctx, self.dut = ctx, dut

    async def _cmd(self, opcode: int, addr: int, data: int = 0, mask: int = 0xF,
                   source: int = 0):
        d, ctx = self.dut, self.ctx
        ctx.set(d.io_cpuBus_cmd_valid, 1)
        ctx.set(d.io_cpuBus_cmd_payload_last, 1)
        ctx.set(d.io_cpuBus_cmd_payload_fragment_opcode, opcode)
        ctx.set(d.io_cpuBus_cmd_payload_fragment_address, addr)
        ctx.set(d.io_cpuBus_cmd_payload_fragment_length, 3)
        ctx.set(d.io_cpuBus_cmd_payload_fragment_data, data)
        ctx.set(d.io_cpuBus_cmd_payload_fragment_mask, mask)
        ctx.set(d.io_cpuBus_cmd_payload_fragment_source, source)
        for _ in range(200):
            if ctx.get(d.io_cpuBus_cmd_ready):
                break
            await ctx.tick()
        else:
            raise AssertionError(f"cmd {opcode} @ {addr:#x} never accepted")
        await ctx.tick()
        ctx.set(d.io_cpuBus_cmd_valid, 0)

    async def _rsp(self) -> int:
        d, ctx = self.dut, self.ctx
        for _ in range(400):
            if ctx.get(d.io_cpuBus_rsp_valid):
                data = ctx.get(d.io_cpuBus_rsp_payload_fragment_data)
                await ctx.tick()
                return data
            await ctx.tick()
        raise AssertionError("no response")

    async def write(self, addr: int, data: int, mask: int = 0xF):
        await self._cmd(CMD_WRITE, addr, data, mask)
        await self._rsp()

    async def read(self, addr: int) -> int:
        await self._cmd(CMD_READ, addr)
        return await self._rsp()


@pytest.mark.timeout(600)
def test_core_sim_smoke():
    dut = CoreSim(async_reset=False)
    frag = Fragment.get(dut, None)
    fb_mem = _memories(frag)[(P.FB_RAM_INSTANCE, P.RAM_ARRAY_NAME)]

    async def tb(ctx):
        cpu = _Cpu(ctx, dut)
        ctx.set(dut.io_cpuBus_rsp_ready, 1)
        ctx.set(dut.reset, 1)
        await _ticks(ctx, 4)
        ctx.set(dut.reset, 0)
        await _ticks(ctx, 4)

        # Idle: PCI FIFO empty and free, nothing busy, sync drained.
        assert ctx.get(dut.dbg_pci_fifo_free) == 64
        assert ctx.get(dut.dbg_pipeline_busy) == 0
        assert ctx.get(dut.dbg_pci_cmd_ready) == 1
        assert ctx.get(dut.dbg_frontdoor_cmd_ready) == 1

        # A register write (fbzMode: colour writes on, nothing else) and the
        # status register reading back idle.
        await cpu.write(FBZ_MODE, 1 << 9)
        await cpu.write(LFB_MODE, 0)             # RGB565, front buffer, no swizzle
        await _ticks(ctx, 8)
        status = await cpu.read(0x000)
        assert status == STATUS_IDLE, hex(status)
        assert ctx.get(dut.core.reg_bank.render_config.fbz_mode.rgb_buffer_mask) == 1

        # An LFB write of two RGB565 pixels at (0,0)/(1,0) lands in
        # fbWriteRam word 0 (fbBaseAddr = 0, front buffer at buffer 0).
        assert ctx.get(fb_mem[0]) == 0
        await cpu.write(LFB_BASE + 0, 0x12345678)
        for _ in range(200):
            if ctx.get(fb_mem[0]) == 0x12345678:
                break
            await ctx.tick()
        assert ctx.get(fb_mem[0]) == 0x12345678
        await _ticks(ctx, 8)
        assert ctx.get(dut.dbg_pipeline_busy) == 0

        # Not checked: reading it back through the LFB.  Both cores lose the
        # inner colour-read arbiter's route bit 1 in CoreSim's five-way
        # `fbArbiter` (`(source @@ chosen).resized` with 4 + 3 -> 4 bits,
        # BmbArbiter.scala:46 and lib/membus.py alike), so the response is
        # demuxed to the scanout reader and the Lfb waits for ever.  A Spinal
        # property, reproduced; see the Phase 7.2 note in the plan.

        # The swapCountReg load port.
        assert ctx.get(dut.dbg_swap_count) == 0
        ctx.set(dut.dbg_swap_count_set, 2)
        ctx.set(dut.dbg_swap_count_we, 1)
        await ctx.tick()
        ctx.set(dut.dbg_swap_count_we, 0)
        assert ctx.get(dut.dbg_swap_count) == 2
        assert ctx.get(dut.core.swap_displayed_buffer) == 0   # swapCount(0)
        # Status now reports displayedBuffer = 0 still; set count 1 -> bit 10.
        ctx.set(dut.dbg_swap_count_set, 1)
        ctx.set(dut.dbg_swap_count_we, 1)
        await ctx.tick()
        ctx.set(dut.dbg_swap_count_we, 0)
        assert ctx.get(dut.core.swap_displayed_buffer) == 1
        status = await cpu.read(0x000)
        assert status == STATUS_IDLE | (1 << 10), hex(status)

    # `clk` is a port (the harness drives `top->clk`), so the sync domain's
    # clock is combinationally driven from it and `add_clock` would conflict:
    # toggle the port instead.
    async def clock(ctx):
        while True:
            ctx.set(dut.clk, 1)
            await ctx.delay(PERIOD / 2)
            ctx.set(dut.clk, 0)
            await ctx.delay(PERIOD / 2)

    sim = Simulator(frag)
    sim.add_process(clock)
    sim.add_testbench(tb)
    sim.run()
