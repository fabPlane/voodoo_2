"""``CoreSim`` -- the Verilator top: ``Core`` plus its two simulation RAMs.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/CoreSim.scala`` (plan Phase 7.2;
this file replaces the Phase 0.5 stub of the same name)::

    case class CoreSim(
        c: Config = Config.voodoo1().copy(enableHdmiScanout = false),
        memTiming: SimMemoryTiming = SimMemoryTiming()
    ) extends Component

Wraps :class:`avoodoo.core.Core` with:

* ``fbWriteRam`` -- :class:`~avoodoo.sim_membus_memory.SimMemBusMemory`
  (``SimBmbMemory``), 4 MiB, ``Core.fbMemBmbParams(c)``, behind ``fbArbiter``,
  a five-input ``BmbArbiter`` (``lowerFirstPriority = true``) over
  ``fbMemWrite``, ``fbColorWriteMem``, ``fbAuxWriteMem``, ``fbColorReadMem``,
  ``fbAuxReadMem`` in that order;
* ``texRam`` -- the same, 8 MiB, ``Core.texMemBmbParams(c)``, straight on
  ``texMem``;
* the unified 24-bit ``cpuBus`` (address decode inside ``Core``);
* ``vRetrace`` from the harness, ``memFifoFree = 0xffff``, ``pciInterrupt =
  False``, ``fbBaseAddr = 0``, ``flushFbCaches`` from the harness;
* the HDMI domain held in reset with its clock low (``hdmi.clock := False``,
  ``hdmi.reset := True``): trace replay does not need scanout, and the
  ``enableHdmiScanout = false`` config clears ``framebufferEnable`` so the
  prefetcher cannot fill the CDC FIFO.

What is *not* in the Scala (all of it from :mod:`avoodoo.core_sim_ports`,
which owns the contract; see ``amaranth/README.md`` "Phase 0.5"):

* the flat ``io_*`` port spelling and the explicit ``clk``/``reset`` ports.
  The harness drives ``top->clk`` / ``top->reset`` and binds ``io_cpuBus_*``
  by name, so the signature is the Spinal port list verbatim and the ``sync``
  domain is built by hand from those two ports;
* the ``dbg_*`` bundle, forwarded from ``Core(dbg=True)``: the five live
  hierarchical taps the upstream harness reads, and the ``swapCountReg``
  load port (``dbg_swap_count_set`` / ``dbg_swap_count_we``);
* the two RAM submodule names (``fbWriteRam``, ``texRam``) with the
  ``Memory`` bound as ``m.submodules.mem`` inside each, which is what makes
  ``CoreSim__DOT__fbWriteRam__DOT__mem`` the Verilator path the C harness
  spells.

Reset is **asynchronous, active high** by default, matching the upstream
sim build the golden screenshots came from (``CoreSim.json`` records
``reset_kind: async``; ``GenOne``'s ``CoreSim`` row is SYNC unless
``--async-reset``, and the diff registry builds this side to match).

Both RAMs are ``Memory(init=[])``; ``gen/gen_core_sim.py`` strips their
``$meminit`` cells (and every other uninitialised memory's -- the CDC FIFO
in the scanout, the plane readers' lane FIFOs) before Verilog emission, or
the 4 MiB + 8 MiB become three million ``initial`` lines.
"""

from __future__ import annotations

from dataclasses import replace

from amaranth import ClockDomain, ClockSignal, Const, Module, ResetSignal
from amaranth.lib import wiring

from avoodoo.config import Config
from avoodoo.core import membus_params as _mp
from avoodoo.core_sim_ports import (
    DBG_PORTS,
    FB_BYTES,
    FB_RAM_INSTANCE,
    TEX_BYTES,
    TEX_RAM_INSTANCE,
    core_sim_signature,
)
from avoodoo.lib import membus
from avoodoo.lib.membus import Arbiter
from avoodoo.sim_membus_memory import SimMemBusMemory, SimMemoryTiming

__all__ = ["CoreSim", "core_sim_config"]


def core_sim_config() -> Config:
    """``Config.voodoo1().copy(enableHdmiScanout = false)`` -- the default ``c``."""
    return replace(Config.voodoo1(), enable_hdmi_scanout=False)


class CoreSim(wiring.Component):
    """``case class CoreSim(c, memTiming) extends Component``.

    Parameters
    ----------
    c : Config
        Defaults to :func:`core_sim_config`.
    mem_timing : SimMemoryTiming
        ``SimBmbMemory`` latency model, the zero-latency default.
    async_reset : bool
        ``sync`` domain reset kind; ``True`` is the upstream sim build.
    simulation : bool
        Forwarded to :class:`~avoodoo.core.Core` (``GenerationFlags.simulation``
        for the register bank); ``False`` like ``CoreSimGen``.
    """

    def __init__(self, c: Config | None = None, mem_timing: SimMemoryTiming | None = None,
                 *, async_reset: bool = True, simulation: bool = False):
        from avoodoo.core.core import Core

        self.c = core_sim_config() if c is None else c
        self.mem_timing = SimMemoryTiming() if mem_timing is None else mem_timing
        self._async_reset = bool(async_reset)
        super().__init__(core_sim_signature())

        # The Scala `val`s, built here so tests can reach them; attached
        # under the Scala names in `elaborate`.
        self.core = Core(self.c, simulation=simulation, dbg=True)
        self.fb_write_ram = self._sim_ram(_mp.fb_mem_bmb_params(self.c), FB_BYTES)
        self.tex_ram = self._sim_ram(_mp.tex_mem_bmb_params(self.c), TEX_BYTES)
        fb_p = _mp.fb_mem_bmb_params(self.c)
        self.fb_arbiter = Arbiter([fb_p] * 5, fb_p, lower_first_priority=True)

    def _sim_ram(self, p, size: int) -> SimMemBusMemory:
        """``def simRam(p, size) = SimBmbMemory(p = p, size = size, timing = memTiming)``."""
        return SimMemBusMemory(p=p, size=size, timing=self.mem_timing)

    def elaborate(self, platform):
        m = Module()

        # The harness drives `clk`/`reset` as ordinary ports, so build the
        # `sync` domain by hand rather than letting Amaranth append its own
        # `clk`/`rst` pair (which would collide on the first name and use the
        # wrong spelling for the second).
        m.domains += ClockDomain("sync", async_reset=self._async_reset)
        m.d.comb += ClockSignal("sync").eq(self.clk)
        m.d.comb += ResetSignal("sync").eq(self.reset)

        core = self.core
        fb_write_ram = self.fb_write_ram
        tex_ram = self.tex_ram
        fb_arbiter = self.fb_arbiter
        m.submodules.core = core
        setattr(m.submodules, FB_RAM_INSTANCE, fb_write_ram)
        setattr(m.submodules, TEX_RAM_INSTANCE, tex_ram)
        m.submodules.fbArbiter = fb_arbiter

        # core.io.cpuBus <> io.cpuBus  -- the flat Spinal port list onto the
        # structured MemBus.
        cmd, rsp = core.cpu_bus.cmd, core.cpu_bus.rsp
        cf, rf = cmd.payload.fragment, rsp.payload.fragment
        m.d.comb += [
            cmd.valid.eq(self.io_cpuBus_cmd_valid),
            self.io_cpuBus_cmd_ready.eq(cmd.ready),
            cmd.payload.last.eq(self.io_cpuBus_cmd_payload_last),
            cf.source.eq(self.io_cpuBus_cmd_payload_fragment_source),
            cf.opcode.eq(self.io_cpuBus_cmd_payload_fragment_opcode),
            cf.address.eq(self.io_cpuBus_cmd_payload_fragment_address),
            cf.length.eq(self.io_cpuBus_cmd_payload_fragment_length),
            cf.data.eq(self.io_cpuBus_cmd_payload_fragment_data),
            cf.mask.eq(self.io_cpuBus_cmd_payload_fragment_mask),
            self.io_cpuBus_rsp_valid.eq(rsp.valid),
            rsp.ready.eq(self.io_cpuBus_rsp_ready),
            self.io_cpuBus_rsp_payload_last.eq(rsp.payload.last),
            self.io_cpuBus_rsp_payload_fragment_source.eq(rf.source),
            self.io_cpuBus_rsp_payload_fragment_opcode.eq(rf.opcode),
            self.io_cpuBus_rsp_payload_fragment_data.eq(rf.data),
        ]

        # fbArbiter.io.inputs(0..4) <> core.io.fb*  ;  fbArbiter.io.output <> fbWriteRam.io.bus
        membus.connect(m, fb_arbiter.inputs[0], core.fb_mem_write)
        membus.connect(m, fb_arbiter.inputs[1], core.fb_color_write_mem)
        membus.connect(m, fb_arbiter.inputs[2], core.fb_aux_write_mem)
        membus.connect(m, fb_arbiter.inputs[3], core.fb_color_read_mem)
        membus.connect(m, fb_arbiter.inputs[4], core.fb_aux_read_mem)
        membus.connect(m, fb_write_ram.bus, fb_arbiter.output)
        # core.io.texMem <> texRam.io.bus
        membus.connect(m, tex_ram.bus, core.tex_mem)

        m.d.comb += [
            core.status_inputs.v_retrace.eq(self.io_vRetrace),
            core.status_inputs.mem_fifo_free.eq(0xFFFF),
            core.status_inputs.pci_interrupt.eq(0),
            core.fb_base_addr.eq(0),
            core.flush_fb_caches.eq(self.io_flushFbCaches),
            # Trace replay does not need scanout. Hold the HDMI domain in reset.
            core.hdmi.clock.eq(Const(0, 1)),
            core.hdmi.reset.eq(Const(1, 1)),
        ]

        # --- dbg bundle: straight through from Core(dbg=True) --------------
        for name, _width, flow in DBG_PORTS:
            if flow is wiring.Out:
                m.d.comb += getattr(self, name).eq(getattr(core, name))
            else:
                m.d.comb += getattr(core, name).eq(getattr(self, name))

        return m
