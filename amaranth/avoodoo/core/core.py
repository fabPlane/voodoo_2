"""``Core`` -- the whole Voodoo core behind one CPU bus and six memory buses.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/Core.scala`` (SpinalHDL 1.15.0,
the working-tree version with ``amaranth/spinal-ref/upstream-local-edits.patch``
applied: the integrated ``HdmiCdcFramebufferScanout``, the ``scanout*``
bring-up counters and ``pipelineBusySources``), instantiated by
``CoreSim.scala:29`` as ``Core(c)`` and by ``CoreGowin.scala`` / ``CoreArtix.scala``
for the boards.  Plan Phase 7.2.

Everything here is *composition*; every sub-component is its own, lockstep-
diffed module and this file owns only the wiring.  The Scala's ``val``\\ s map
onto Amaranth submodules under the same names, so a waveform reader sees
``regBank``, ``pciFifo``, ``frontdoor`` ... on both sides:

=====================================  ==========================================
``Core.scala``                          here
=====================================  ==========================================
``Core.cpuBmbParams`` and friends       :mod:`avoodoo.core.membus_params`
``addressRemapper``                     :class:`~avoodoo.frontend.address_remapper.AddressRemapper`
``regBank``                             :class:`~avoodoo.frontend.register_bank.RegisterBank`
``pciFifo``                             :class:`~avoodoo.bus.pci_fifo.PciFifo`
``frontdoor``                           :class:`~avoodoo.core.cpu_frontdoor.CoreCpuFrontdoor`
``pixelPipeline``                       :class:`~avoodoo.core.pixel_pipeline.PixelPipeline`
``framebufferMem``                      :class:`~avoodoo.core.framebuffer_mem_subsystem.FramebufferMemSubsystem`
``textureMem``                          :class:`~avoodoo.core.texture_mem_subsystem.TextureMemSubsystem`
``hdmiScanout``                         :class:`~avoodoo.hdmi.scanout.HdmiCdcFramebufferScanout`
``controlPlane.swapBuffer``             :class:`~avoodoo.frontend.swap_buffer.SwapBuffer`
                                        (submodule ``controlPlane_swapBuffer``)
``controlPlane.framebufferLayout``      :func:`~avoodoo.core.core_types.framebuffer_layout_from_register_bank`
``PixelPipeline.Controls.fromRegisterBank``  :func:`~avoodoo.core.pixel_pipeline.controls_from_register_bank`
``PixelPipeline.ExternalBusy.fromCore`` :func:`~avoodoo.core.pixel_pipeline.external_busy_from_core`
``TextureMem.DownloadConfig.fromRegisterBank`` :meth:`~avoodoo.texture.texture_mem.DownloadConfig.from_register_bank`
=====================================  ==========================================

The three ``PciFifo`` tables (``regBank.busif.getCategories`` /
``getFloatAliases`` / ``getCommandStreamReady.keys``) are, in the Scala,
plain values that only exist once ``RegisterBank`` has elaborated; here they
come from :mod:`avoodoo.frontend.regdef` (``categories()``,
``float_aliases()``, ``command_addresses()``), which
``tests/test_regdef.py`` pins against the elaborated ground truth.

Ports (Spinal ``io`` names, ``camel_to_snake``'d; every leaf pairs
mechanically in the diff harness):

=========================  =======================================================
``cpu_bus``                ``slave(Bmb(Core.cpuBmbParams))`` -- the 16 MiB BAR
``fb_mem_write``, ``fb_color_write_mem``, ``fb_aux_write_mem``,
``fb_color_read_mem``, ``fb_aux_read_mem``
                           ``master(Bmb(Core.fbMemBmbParams(c)))`` x 5
``tex_mem``                ``master(Bmb(Core.texMemBmbParams(c)))``
``status_inputs``          ``in`` bundle: ``vRetrace``, ``memFifoFree``, ``pciInterrupt``
``swap_displayed_buffer``  ``out UInt(2)`` / ``swaps_pending`` ``out UInt(3)``
``fb_base_addr``           ``in UInt(c.addressWidth)``
``flush_fb_caches``        ``in Bool`` (simulation-only cache flush)
``hdmi``                   ``master(HdmiScanoutPort(c))``: ``clock``/``reset`` in,
                           ``video``/``status``/``underflow``/``fifo_level`` out
``scanout_prefetch_count``, ``scanout_read_req_count``, ``scanout_read_rsp_count``
                           the three 16-bit ``Reg(...) init(0)`` fire counters
``scanout_cache_*``, ``scanout_fill_*``
                           ``framebufferMem``'s ten scanout diagnostics, straight out
``pipeline_busy_sources``  ``pixelPipeline.io.debug.pipelineBusySources``
=========================  =======================================================

Two constructor flags the Scala does not have:

``simulation``
    ``GenerationFlags.simulation`` for the register bank (the two read-only
    debug registers at ``0x240``/``0x250``).  ``False`` by default because
    neither ``CoreSimGen`` (plain ``SpinalConfig``) nor ``GenOne`` set the
    flag -- only a SpinalSim compile does -- so the Verilog the golden
    screenshots and the lockstep diff come from has them absent.
``dbg``
    Adds the ``dbg_*`` ports of :data:`avoodoo.core_sim_ports.DBG_PORTS`
    (plan §4.3): the five live hierarchical taps the upstream Verilator
    harness reads, plus the ``swapCountReg`` load port (``SwapBuffer`` is
    then built ``dbg_load_port=True``).  Off by default so the port list is
    the Scala one; ``core_sim.py`` turns it on and forwards the ports.

The nine ``CoreDebug`` words ``PixelPipeline`` never drives (``fbAccess``,
``fb{Color,Aux}Cache{,Req,Expected,Occupancy}``; ``PixelPipelineDebug`` leaves
them out) are filled with zero on the way into ``regBank.io.debug``, which
is what Spinal's pruning of an unassigned output amounts to.

Registers of this file's own: the three scanout fire counters, ``init(0)``.
Everything else lives inside a sub-component under its own rules.
"""

from __future__ import annotations

from amaranth import Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from avoodoo.bus.pci_fifo import PciFifo
from avoodoo.config import Config
from avoodoo.core import membus_params as _mp
from avoodoo.core.core_types import StatusInputs, framebuffer_layout_from_register_bank
from avoodoo.core.cpu_frontdoor import CoreCpuFrontdoor
from avoodoo.core.framebuffer_mem_subsystem import FramebufferMemSubsystem
from avoodoo.core.texture_mem_subsystem import TextureMemSubsystem
from avoodoo.core_sim_ports import DBG_PORTS
from avoodoo.frontend import regdef
from avoodoo.frontend.address_remapper import AddressRemapper
from avoodoo.frontend.register_bank import RegisterBank
from avoodoo.frontend.swap_buffer import SwapBuffer
from avoodoo.lib import membus
from avoodoo.lib.membus import MemBusSignature, log2_up
from avoodoo.lib.stream import connect_stream, fire
from avoodoo.texture.texture_mem import DownloadConfig

__all__ = [
    "Core",
    "WAS_ENQUEUED_ADDR",
    "cpu_bmb_params",
    "fb_mem_bmb_params",
    "tex_mem_bmb_params",
    "cpu_tex_bmb_params",
]

#: ``pciFifo.io.wasEnqueuedAddr := U(0x128, ...)`` (Core.scala:265) -- the
#: ``swapbufferCMD`` register.  ``regdef.WAS_ENQUEUED_ADDR`` says the same.
WAS_ENQUEUED_ADDR = 0x128

# ``object Core`` (Core.scala:15-61): re-exported from membus_params so a
# reader of this file finds them under the Scala names.
cpu_bmb_params = _mp.cpu_bmb_params
fb_mem_bmb_params = _mp.fb_mem_bmb_params
tex_mem_bmb_params = _mp.tex_mem_bmb_params
cpu_tex_bmb_params = _mp.cpu_tex_bmb_params


def _hdmi_scanout_port_signature(c: Config):
    """``master(HdmiScanoutPort(c))`` -- imported lazily (plan Phase 7.1 lands
    ``hdmi/scanout.py`` in parallel with this file)."""
    from avoodoo.hdmi.scanout import HdmiScanoutPortSignature

    return HdmiScanoutPortSignature(c)


class Core(wiring.Component):
    """``case class Core(c: Config) extends Component``."""

    def __init__(self, c: Config | None = None, *, simulation: bool = False,
                 dbg: bool = False) -> None:
        c = Config.voodoo1() if c is None else c
        self.c = c
        self.simulation = bool(simulation)
        self.dbg = bool(dbg)
        aw = c.address_width
        fb_p = fb_mem_bmb_params(c)
        members = {
            # Unified CPU bus (24-bit address covers 16MB PCI BAR)
            "cpu_bus": In(MemBusSignature(cpu_bmb_params())),
            # Framebuffer memory buses
            "fb_mem_write": Out(MemBusSignature(fb_p)),
            "fb_color_write_mem": Out(MemBusSignature(fb_p)),
            "fb_aux_write_mem": Out(MemBusSignature(fb_p)),
            "fb_color_read_mem": Out(MemBusSignature(fb_p)),
            "fb_aux_read_mem": Out(MemBusSignature(fb_p)),
            # Texture memory bus
            "tex_mem": Out(MemBusSignature(tex_mem_bmb_params(c))),
            # Status inputs (hardware state)
            "status_inputs": In(StatusInputs()),
            # SwapBuffer outputs
            "swap_displayed_buffer": Out(2),
            "swaps_pending": Out(3),
            # Framebuffer base address
            "fb_base_addr": In(aw),
            # Simulation-only framebuffer cache flush request
            "flush_fb_caches": In(1),
            # Integrated scanout path
            "hdmi": Out(_hdmi_scanout_port_signature(c)),
            # Board-level bring-up counters for the scanout prefill path
            "scanout_prefetch_count": Out(16),
            "scanout_read_req_count": Out(16),
            "scanout_read_rsp_count": Out(16),
            "scanout_cache_debug": Out(64),
            "scanout_cache_read_addr": Out(aw),
            "scanout_cache_expected_addr": Out(aw),
            "scanout_cache_remaining": Out(log2_up(c.max_fb_dims[0] + 1)),
            "scanout_cache_occupancy": Out(32),
            "scanout_fill_hits": Out(32),
            "scanout_fill_misses": Out(32),
            "scanout_fill_burst_count": Out(32),
            "scanout_fill_burst_beats": Out(32),
            "scanout_fill_stall_cycles": Out(32),
            # One bit per ordered-pipeline busy source
            "pipeline_busy_sources": Out(32),
        }
        if self.dbg:
            for name, width, flow in DBG_PORTS:
                members[name] = flow(width)
        super().__init__(members)

        # The sub-components are the Scala's `val`s (Core.scala:100-137),
        # built here so tests and `CoreSim` can reach them before
        # elaboration; attached under the Scala names in `elaborate`.
        from avoodoo.core.pixel_pipeline import PixelPipeline
        from avoodoo.hdmi.scanout import CEA_720X480P, DMT_640X480, HdmiCdcFramebufferScanout

        self.address_remapper = AddressRemapper(_mp.register_bank_external_params(c),
                                                _mp.register_bank_params(c))
        self.reg_bank = RegisterBank(c, simulation=self.simulation)
        self.pci_fifo = PciFifo(
            bus_params=_mp.register_bank_params(c),
            categories=regdef.categories(),
            float_aliases=regdef.float_aliases(),
            command_addresses=regdef.command_addresses(),
        )
        self.frontdoor = CoreCpuFrontdoor(c)
        self.pixel_pipeline = PixelPipeline(c)
        self.framebuffer_mem = FramebufferMemSubsystem(c)
        self.texture_mem = TextureMemSubsystem(c)
        self.hdmi_scanout = HdmiCdcFramebufferScanout(
            c,
            timing=DMT_640X480 if c.hdmi_scanout_dmt640x480 else CEA_720X480P,
            pixel_repeat_x=c.hdmi_scanout_pixel_repeat_x,
        )
        # val controlPlane = new Area { val swapBuffer = SwapBuffer() ... }
        self.swap_buffer = SwapBuffer(dbg_load_port=self.dbg)

    def elaborate(self, platform):
        m = Module()
        c = self.c

        address_remapper = self.address_remapper
        reg_bank = self.reg_bank
        pci_fifo = self.pci_fifo
        frontdoor = self.frontdoor
        pixel_pipeline = self.pixel_pipeline
        framebuffer_mem = self.framebuffer_mem
        texture_mem = self.texture_mem
        hdmi_scanout = self.hdmi_scanout
        swap_buffer = self.swap_buffer

        m.submodules.addressRemapper = address_remapper
        m.submodules.regBank = reg_bank
        m.submodules.pciFifo = pci_fifo
        m.submodules.frontdoor = frontdoor
        m.submodules.pixelPipeline = pixel_pipeline
        m.submodules.framebufferMem = framebuffer_mem
        m.submodules.textureMem = texture_mem
        m.submodules.hdmiScanout = hdmi_scanout
        m.submodules.controlPlane_swapBuffer = swap_buffer

        # io.pipelineBusySources := pixelPipeline.io.debug.pipelineBusySources
        m.d.comb += self.pipeline_busy_sources.eq(pixel_pipeline.debug.pipeline_busy_sources)

        # ---- val controlPlane = new Area { ... } (Core.scala:118-171) -----
        # val framebufferLayout = FramebufferLayout.fromRegisterBank(c, io.fbBaseAddr, regBank, swapBuffer)
        framebuffer_layout = framebuffer_layout_from_register_bank(
            m, c, self.fb_base_addr, reg_bank, swap_buffer, name="controlPlane_framebufferLayout")

        # frontdoor.io.cpuBus <> io.cpuBus
        membus.connect(m, frontdoor.cpu_bus, self.cpu_bus)
        # frontdoor.io.regBus <> addressRemapper.io.input
        membus.connect(m, address_remapper.input, frontdoor.reg_bus)
        # frontdoor.io.lfbBus <> pixelPipeline.io.lfbBus
        membus.connect(m, pixel_pipeline.lfb_bus, frontdoor.lfb_bus)
        # frontdoor.io.texReadBus <> textureMem.io.cpuTexRead
        membus.connect(m, texture_mem.cpu_tex_read, frontdoor.tex_read_bus)
        # pciFifo.io.texWrite << frontdoor.io.texWrite
        connect_stream(m, pci_fifo.tex_write, frontdoor.tex_write)
        # frontdoor.io.texBaseAddr := regBank.tmuConfig.texBaseAddr
        m.d.comb += frontdoor.tex_base_addr.eq(reg_bank.tmu_config.tex_base_addr)

        # addressRemapper.io.output <> pciFifo.io.cpuSide
        membus.connect(m, pci_fifo.cpu_side, address_remapper.output)
        # pciFifo.io.regSide <> regBank.io.bus
        membus.connect(m, reg_bank.bus, pci_fifo.reg_side)
        # regBank.io.floatShadow <> pciFifo.io.floatShadow   (a Flow)
        m.d.comb += [
            reg_bank.float_shadow.valid.eq(pci_fifo.float_shadow.valid),
            reg_bank.float_shadow.payload.eq(pci_fifo.float_shadow.payload),
        ]
        # regBank.io.statusInputs <> io.statusInputs
        m.d.comb += reg_bank.status_inputs.eq(self.status_inputs)
        # regBank.commands.nopCmd.ready := True
        m.d.comb += reg_bank.commands.nop_cmd_stream.ready.eq(1)

        # swapBuffer.io.cmd << regBank.commands.swapbufferCmd
        connect_stream(m, swap_buffer.cmd, reg_bank.commands.swapbuffer_cmd_stream)
        m.d.comb += [
            swap_buffer.v_retrace.eq(self.status_inputs.v_retrace),
            swap_buffer.vsync_enable.eq(reg_bank.commands.swap_vsync_enable),
            swap_buffer.swap_interval.eq(reg_bank.commands.swap_interval),
            swap_buffer.swap_cmd_enqueued.eq(pci_fifo.was_enqueued),
        ]

        m.d.comb += [
            reg_bank.swap_displayed_buffer.eq(framebuffer_layout.displayed_buffer),
            reg_bank.swaps_pending.eq(framebuffer_layout.swaps_pending),
            reg_bank.draw_routing.eq(framebuffer_layout.draw),
            self.swap_displayed_buffer.eq(framebuffer_layout.displayed_buffer),
            self.swaps_pending.eq(framebuffer_layout.swaps_pending),
        ]

        regs = hdmi_scanout.regs
        m.d.comb += [
            regs.front_base.eq(framebuffer_layout.front),
            regs.back_base.eq(framebuffer_layout.back),
            regs.pixel_stride.eq(framebuffer_layout.draw.pixel_stride),
            regs.display_width.eq(640),
            regs.display_height.eq(480),
            # Bool(c.enableHdmiScanout)
            regs.framebuffer_enable.eq(Const(1 if c.enable_hdmi_scanout else 0, 1)),
            regs.test_pattern_enable.eq(0),
        ]
        for i in range(33):
            m.d.comb += regs.gamma_lut[i].eq(reg_bank.gamma_lut[i])

        # pixelPipeline.io.controls := PixelPipeline.Controls.fromRegisterBank(c, regBank, framebufferLayout)
        from avoodoo.core.pixel_pipeline import (controls_from_register_bank,
                                                  external_busy_from_core)
        controls = controls_from_register_bank(m, c, reg_bank, framebuffer_layout,
                                               name="controlPlane_controls")
        m.d.comb += pixel_pipeline.controls.eq(controls)
        # pixelPipeline.io.externalBusy := PixelPipeline.ExternalBusy.fromCore(regBank, swapBuffer)
        external_busy = external_busy_from_core(m, reg_bank, swap_buffer,
                                                name="controlPlane_externalBusy")
        m.d.comb += pixel_pipeline.external_busy.eq(external_busy)
        # textureMem.io.downloadConfig := TextureMem.DownloadConfig.fromRegisterBank(c, regBank)
        download_config = DownloadConfig.from_register_bank(
            m, c, reg_bank.tmu_config, name="controlPlane_downloadConfig")
        m.d.comb += texture_mem.download_config.eq(download_config)

        # ---- pixel pipeline commands (173-180) ----------------------------
        connect_stream(m, pixel_pipeline.triangle_cmd, reg_bank.commands.triangle_cmd_stream)
        connect_stream(m, pixel_pipeline.ftriangle_cmd, reg_bank.commands.ftriangle_cmd_stream)
        connect_stream(m, pixel_pipeline.fastfill_cmd, reg_bank.commands.fastfill_cmd_stream)
        # pixelPipeline.io.paletteWrite << regBank.io.paletteWrite   (a Flow)
        m.d.comb += [
            pixel_pipeline.palette_write.valid.eq(reg_bank.palette_write.valid),
            pixel_pipeline.palette_write.payload.eq(reg_bank.palette_write.payload),
        ]
        m.d.comb += [
            pixel_pipeline.tmu_invalidate.eq(frontdoor.invalidate),
            pixel_pipeline.pci_fifo_empty.eq(pci_fifo.fifo_empty),
            pixel_pipeline.fb_status.eq(framebuffer_mem.status),
            pixel_pipeline.fb_stats.eq(framebuffer_mem.stats),
        ]

        # ---- framebuffer memory subsystem (182-188, 217-231) -------------
        connect_stream(m, framebuffer_mem.color_write, pixel_pipeline.color_write)
        connect_stream(m, framebuffer_mem.aux_write, pixel_pipeline.aux_write)
        connect_stream(m, framebuffer_mem.color_read_req, pixel_pipeline.color_read_req)
        connect_stream(m, pixel_pipeline.color_read_rsp, framebuffer_mem.color_read_rsp)
        connect_stream(m, framebuffer_mem.scanout_prefetch_req, hdmi_scanout.prefetch_req)
        connect_stream(m, framebuffer_mem.scanout_read_req, hdmi_scanout.read_req)
        connect_stream(m, hdmi_scanout.read_rsp, framebuffer_mem.scanout_read_rsp)

        # ---- the three scanout fire counters (190-201) -------------------
        scanout_prefetch_count = Signal(16, name="scanoutPrefetchCount")
        scanout_read_req_count = Signal(16, name="scanoutReadReqCount")
        scanout_read_rsp_count = Signal(16, name="scanoutReadRspCount")
        with m.If(fire(hdmi_scanout.prefetch_req)):
            m.d.sync += scanout_prefetch_count.eq(scanout_prefetch_count + 1)
        with m.If(fire(hdmi_scanout.read_req)):
            m.d.sync += scanout_read_req_count.eq(scanout_read_req_count + 1)
        with m.If(fire(hdmi_scanout.read_rsp)):
            m.d.sync += scanout_read_rsp_count.eq(scanout_read_rsp_count + 1)
        m.d.comb += [
            self.scanout_prefetch_count.eq(scanout_prefetch_count),
            self.scanout_read_req_count.eq(scanout_read_req_count),
            self.scanout_read_rsp_count.eq(scanout_read_rsp_count),
            self.scanout_cache_debug.eq(framebuffer_mem.scanout_cache_debug),
            self.scanout_cache_read_addr.eq(framebuffer_mem.scanout_cache_read_addr),
            self.scanout_cache_expected_addr.eq(framebuffer_mem.scanout_cache_expected_addr),
            self.scanout_cache_remaining.eq(framebuffer_mem.scanout_cache_remaining),
            self.scanout_cache_occupancy.eq(framebuffer_mem.scanout_cache_occupancy),
            self.scanout_fill_hits.eq(framebuffer_mem.scanout_fill_hits),
            self.scanout_fill_misses.eq(framebuffer_mem.scanout_fill_misses),
            self.scanout_fill_burst_count.eq(framebuffer_mem.scanout_fill_burst_count),
            self.scanout_fill_burst_beats.eq(framebuffer_mem.scanout_fill_burst_beats),
            self.scanout_fill_stall_cycles.eq(framebuffer_mem.scanout_fill_stall_cycles),
        ]
        connect_stream(m, framebuffer_mem.aux_read_req, pixel_pipeline.aux_read_req)
        connect_stream(m, pixel_pipeline.aux_read_rsp, framebuffer_mem.aux_read_rsp)
        connect_stream(m, framebuffer_mem.prefetch_color, pixel_pipeline.prefetch_color)
        connect_stream(m, framebuffer_mem.prefetch_aux, pixel_pipeline.prefetch_aux)
        membus.connect(m, framebuffer_mem.lfb_read_bus, pixel_pipeline.lfb_read_bus)
        m.d.comb += framebuffer_mem.flush.eq(self.flush_fb_caches)
        membus.connect(m, self.fb_mem_write, framebuffer_mem.fb_mem_write)
        membus.connect(m, self.fb_color_write_mem, framebuffer_mem.fb_color_write_mem)
        membus.connect(m, self.fb_aux_write_mem, framebuffer_mem.fb_aux_write_mem)
        membus.connect(m, self.fb_color_read_mem, framebuffer_mem.fb_color_read_mem)
        membus.connect(m, self.fb_aux_read_mem, framebuffer_mem.fb_aux_read_mem)

        # ---- texture memory subsystem (233-235) --------------------------
        connect_stream(m, texture_mem.cpu_tex_drain, pci_fifo.tex_drain)
        membus.connect(m, texture_mem.tmu_tex_read, pixel_pipeline.tex_read)
        membus.connect(m, self.tex_mem, texture_mem.tex_mem)

        # ---- HDMI port (237-242) -----------------------------------------
        m.d.comb += [
            self.hdmi.video.eq(hdmi_scanout.video),
            self.hdmi.status.eq(hdmi_scanout.status),
            self.hdmi.underflow.eq(hdmi_scanout.underflow),
            # io.hdmi.fifoLevel := hdmiScanout.io.fifoPushOccupancy.resized
            self.hdmi.fifo_level.eq(hdmi_scanout.fifo_push_occupancy),
            hdmi_scanout.hdmi_clock.eq(self.hdmi.clock),
            hdmi_scanout.hdmi_reset.eq(self.hdmi.reset),
        ]

        # ---- register bank feedback (244-252) ----------------------------
        m.d.comb += reg_bank.statistics.eq(pixel_pipeline.stats)
        # regBank.io.debug := pixelPipeline.io.debug -- the four driven words;
        # the nine CoreDebug words nothing drives upstream read as zero.
        dbg_src = pixel_pipeline.debug
        dbg_dst = reg_bank.debug
        m.d.comb += [
            dbg_dst.pipeline_busy.eq(dbg_src.pipeline_busy),
            dbg_dst.busy.eq(dbg_src.busy),
            dbg_dst.write_path.eq(dbg_src.write_path),
            dbg_dst.pipeline_busy_sources.eq(dbg_src.pipeline_busy_sources),
        ]
        for name in ("fb_access", "fb_color_cache", "fb_color_cache_req",
                     "fb_color_cache_expected", "fb_color_cache_occupancy",
                     "fb_aux_cache", "fb_aux_cache_req", "fb_aux_cache_expected",
                     "fb_aux_cache_occupancy"):
            m.d.comb += dbg_dst[name].eq(0)

        m.d.comb += pci_fifo.pipeline_busy.eq(pixel_pipeline.debug.pipeline_busy)
        # pciFifo.io.commandReady := regBank.io.commandReady   (Vec(Bool, 5))
        for i in range(len(pci_fifo.command_addresses)):
            m.d.comb += pci_fifo.command_ready[i].eq(reg_bank.command_ready[i])
        # pciFifo.io.wasEnqueuedAddr := U(0x128, pciFifo.busAddrWidth bits)
        m.d.comb += pci_fifo.was_enqueued_addr.eq(
            Const(WAS_ENQUEUED_ADDR, pci_fifo.bus_addr_width))
        m.d.comb += [
            reg_bank.pci_fifo_empty.eq(pci_fifo.fifo_empty),
            reg_bank.pci_fifo_free.eq(pci_fifo.pci_fifo_free),
            reg_bank.swap_cmd_enqueued.eq(pci_fifo.was_enqueued),
            reg_bank.sync_drained.eq(pci_fifo.sync_drained),
        ]

        # ---- the dbg bundle (not in the Scala; plan §4.3) ----------------
        if self.dbg:
            m.d.comb += [
                self.dbg_pci_cmd_ready.eq(pci_fifo.cpu_side.cmd.ready),
                self.dbg_frontdoor_cmd_ready.eq(frontdoor.cpu_bus.cmd.ready),
                self.dbg_pci_fifo_free.eq(pci_fifo.pci_fifo_free),
                self.dbg_sync_drained.eq(pci_fifo.sync_drained),
                self.dbg_pipeline_busy.eq(pixel_pipeline.debug.pipeline_busy),
                self.dbg_swap_count.eq(swap_buffer.swap_count),
                swap_buffer.swap_count_set.eq(self.dbg_swap_count_set),
                swap_buffer.swap_count_we.eq(self.dbg_swap_count_we),
            ]

        return m
