"""``TextureMemSubsystem`` -- three texture-memory masters onto one port.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/core/TextureMemSubsystem.scala``
(SpinalHDL 1.15.0), instantiated by ``Core.scala:112`` as
``TextureMemSubsystem(c)``.

Ports (Spinal names, ``camel_to_snake``'d):

===================  ==========================================================
``cpu_tex_drain``    ``slave(Stream(TexWritePayload()))`` -- ``PciFifo.texDrain``,
                     the host's raw texture-download writes
``cpu_tex_read``     ``slave(Bmb(Core.cpuTexBmbParams))`` -- CPU reads of texture
                     memory through the frontdoor (26-bit, source 4, length 6)
``tmu_tex_read``     ``slave(Bmb(Tmu.bmbParams(c)))`` -- the TMU's read-only port
                     (source 1, no data/mask)
``download_config``  ``in(TextureMem.DownloadConfig(c))`` -- the TMU registers
``tex_mem``          ``master(Bmb(Core.texMemBmbParams(c)))`` -- the one port to
                     texture SRAM/DDR, ``sourceWidth = 4 + log2Up(3) = 6``
===================  ==========================================================

Structure, in the Scala's order:

1. ``cpuTexWriteCmd = TextureMem.WriteCmd.fromPciDrain(...)`` (two
   ``m2sPipe`` stages) and ``toBmb(..., source = 0)`` turn the drain into a
   write-only Bmb master ``cpuTexWriteBus``.
2. ``tmuTexCmd = io.tmuTexRead.cmd.s2mPipe()`` and
   ``cpuTexReadCmd = io.cpuTexRead.cmd.queue(2)`` decouple the two readers.
3. **Fixed priority, no lock**: TMU reads beat CPU writes beat CPU reads,
   re-evaluated every cycle (``useTmuTex = tmuTexCmd.valid`` and so on).  A
   multi-beat CPU write could in principle be interleaved with TMU reads;
   here every CPU write is a single beat (``length = 3, last = True``) so it
   cannot happen, and TMU bursts are single commands anyway.
4. ``routedSource``: the chosen master's ``source`` resized to 4 bits, with a
   2-bit route tag in the **low** bits (``0`` TMU, ``1`` CPU write, ``2`` CPU
   read).  The response demux reads the tag back from ``rsp.source[1:0]``
   and hands ``rsp.source >> 2`` to the master, resized to its own width.
   The slave must therefore echo ``source`` (``SimBmbMemory`` and the Gowin
   DDR adapter both do).
5. ``texMem.cmd`` fields default to the TMU's; ``data``/``mask`` are 0 for
   both readers.  ``texMem.rsp.ready`` is the selected master's; with no
   valid response or an unknown tag (3) it is ``False``, i.e. such a beat
   would stall forever -- same as the Scala.

Registers: ``S2MPipe.rValidN`` (init 1), the two ``M2SPipe.rValid`` (init 0)
and the FIFO pointers reset; every payload register is a plain Spinal ``Reg``
and therefore ``reset_less`` on this side (plan §6).
"""

from __future__ import annotations

from amaranth.hdl import Cat, Const, Module, Signal
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import TexWritePayload
from avoodoo.core.membus_params import cpu_tex_bmb_params, tex_mem_bmb_params, tmu_bmb_params
from avoodoo.lib.membus import MemBusSignature, log2_up
from avoodoo.lib.stream import queue, s2m_pipe
from avoodoo.texture.texture_mem import (DownloadConfig, write_cmd_from_pci_drain,
                                         write_cmd_to_membus)

__all__ = ["TextureMemSubsystem", "ROUTE_TMU", "ROUTE_CPU_WRITE", "ROUTE_CPU_READ"]

#: The route tags ``routedSource`` packs into ``texMem.cmd.source[1:0]``.
ROUTE_TMU = 0
ROUTE_CPU_WRITE = 1
ROUTE_CPU_READ = 2


class TextureMemSubsystem(wiring.Component):
    """``case class TextureMemSubsystem(c: Config) extends Component``."""

    def __init__(self, c: Config | None = None) -> None:
        # `c` defaults to Config.voodoo1() so the diff registry and
        # gen_module can build it without parameters, like the other core
        # modules.
        c = Config.voodoo1() if c is None else c
        self.c = c
        self.cpu_tex_params = cpu_tex_bmb_params()
        self.tmu_params = tmu_bmb_params(c)
        self.tex_mem_params = tex_mem_bmb_params(c)
        # private val cmdRouteWidth = log2Up(3)
        self.cmd_route_width = log2_up(3)
        self.tex_mem_source_width = self.tex_mem_params.access.source_width
        super().__init__({
            "cpu_tex_drain": In(stream.Signature(TexWritePayload())),
            "cpu_tex_read": In(MemBusSignature(self.cpu_tex_params)),
            "tmu_tex_read": In(MemBusSignature(self.tmu_params)),
            "download_config": In(DownloadConfig(c)),
            "tex_mem": Out(MemBusSignature(self.tex_mem_params)),
        })

    def _routed_source(self, cmd, route: int):
        """``routedSource(cmd, route)`` = ``(cmd.source.resize(w - rw) ## U(route, rw)).asUInt``."""
        rw = self.cmd_route_width
        top_w = self.tex_mem_source_width - rw
        frag = cmd.payload.fragment
        if "source" in frag.shape().members:
            src = frag.source
            top = src[:top_w] if len(src) >= top_w else Cat(src, Const(0, top_w - len(src)))
        else:
            top = Const(0, top_w)
        return Cat(Const(route, rw), top)

    def elaborate(self, platform):
        m = Module()
        c = self.c
        tp = self.tex_mem_params.access
        rw = self.cmd_route_width

        # val cpuTexWriteCmd = TextureMem.WriteCmd.fromPciDrain(c, io.cpuTexDrain, io.downloadConfig)
        cpu_tex_write_cmd = write_cmd_from_pci_drain(
            m, c, self.cpu_tex_drain, self.download_config, name="cpuTexWrite")
        # val cpuTexWriteBus = TextureMem.WriteCmd.toBmb(cpuTexWriteCmd, cpuTexBmbParams, source = 0)
        cpu_tex_write_bus = write_cmd_to_membus(
            m, cpu_tex_write_cmd, self.cpu_tex_params, source=0, name="cpuTexWriteBus")

        # private val tmuTexCmd = io.tmuTexRead.cmd.s2mPipe()
        tmu_tex_cmd = s2m_pipe(m, self.tmu_tex_read.cmd, name="tmuTexCmd")
        # private val cpuTexReadCmd = io.cpuTexRead.cmd.queue(2)
        cpu_tex_read_cmd = queue(m, self.cpu_tex_read.cmd, 2, name="cpuTexReadCmd")

        # Arbitration: TMU first, then CPU write, then CPU read.
        use_tmu_tex = Signal(name="useTmuTex")
        use_cpu_tex_write = Signal(name="useCpuTexWrite")
        use_cpu_tex_read = Signal(name="useCpuTexRead")
        m.d.comb += [
            use_tmu_tex.eq(tmu_tex_cmd.valid),
            use_cpu_tex_write.eq(~use_tmu_tex & cpu_tex_write_bus.cmd.valid),
            use_cpu_tex_read.eq(~use_tmu_tex & ~use_cpu_tex_write & cpu_tex_read_cmd.valid),
        ]

        out_cmd = self.tex_mem.cmd
        out = out_cmd.payload.fragment
        tmu_frag = tmu_tex_cmd.payload.fragment
        wr_frag = cpu_tex_write_bus.cmd.payload.fragment
        rd_frag = cpu_tex_read_cmd.payload.fragment

        # Defaults: the TMU command, data/mask zero.
        m.d.comb += [
            out_cmd.valid.eq(0),
            out.opcode.eq(tmu_frag.opcode),
            out.address.eq(tmu_frag.address),
            out.data.eq(0),
            out.mask.eq(0),
            out.length.eq(tmu_frag.length),
            out_cmd.payload.last.eq(tmu_tex_cmd.payload.last),
            out.source.eq(self._routed_source(tmu_tex_cmd, ROUTE_TMU)),
        ]
        if tp.context_width > 0:
            m.d.comb += out.context.eq(0)

        with m.If(use_tmu_tex):
            m.d.comb += out_cmd.valid.eq(1)
        with m.If(use_cpu_tex_write):
            m.d.comb += [
                out_cmd.valid.eq(1),
                out.opcode.eq(wr_frag.opcode),
                out.address.eq(wr_frag.address),
                out.data.eq(wr_frag.data),
                out.mask.eq(wr_frag.mask),
                out.length.eq(wr_frag.length),
                out_cmd.payload.last.eq(cpu_tex_write_bus.cmd.payload.last),
                out.source.eq(self._routed_source(cpu_tex_write_bus.cmd, ROUTE_CPU_WRITE)),
            ]
            if tp.context_width > 0:
                m.d.comb += out.context.eq(wr_frag.context)
        with m.If(use_cpu_tex_read):
            m.d.comb += [
                out_cmd.valid.eq(1),
                out.opcode.eq(rd_frag.opcode),
                out.address.eq(rd_frag.address),
                out.data.eq(0),
                out.mask.eq(0),
                out.length.eq(rd_frag.length),
                out_cmd.payload.last.eq(cpu_tex_read_cmd.payload.last),
                out.source.eq(self._routed_source(cpu_tex_read_cmd, ROUTE_CPU_READ)),
            ]
            if tp.context_width > 0:
                m.d.comb += out.context.eq(rd_frag.context)

        m.d.comb += [
            tmu_tex_cmd.ready.eq(use_tmu_tex & out_cmd.ready),
            cpu_tex_write_bus.cmd.ready.eq(use_cpu_tex_write & out_cmd.ready),
            cpu_tex_read_cmd.ready.eq(use_cpu_tex_read & out_cmd.ready),
        ]

        # ---- responses: demux on the low route bits of rsp.source ----------
        in_rsp = self.tex_mem.rsp
        in_frag = in_rsp.payload.fragment
        # val rspRoute = io.texMem.rsp.source(cmdRouteWidth - 1 downto 0)
        rsp_route = Signal(rw, name="rspRoute")
        # val rspSource = (io.texMem.rsp.source >> cmdRouteWidth).resized
        rsp_source = Signal(self.tex_mem_source_width - rw, name="rspSource")
        m.d.comb += [
            rsp_route.eq(in_frag.source[:rw]),
            rsp_source.eq(in_frag.source[rw:]),
        ]

        def drive_rsp(rsp):
            frag = rsp.payload.fragment
            m.d.comb += [
                rsp.valid.eq(0),
                rsp.payload.last.eq(in_rsp.payload.last),
                frag.opcode.eq(in_frag.opcode),
                frag.data.eq(in_frag.data),
            ]
            if "source" in frag.shape().members:
                m.d.comb += frag.source.eq(rsp_source[:len(frag.source)]
                                           if len(frag.source) <= len(rsp_source)
                                           else rsp_source)

        drive_rsp(self.tmu_tex_read.rsp)
        drive_rsp(cpu_tex_write_bus.rsp)
        drive_rsp(self.cpu_tex_read.rsp)

        m.d.comb += in_rsp.ready.eq(0)
        with m.If(in_rsp.valid & (rsp_route == ROUTE_TMU)):
            m.d.comb += [
                self.tmu_tex_read.rsp.valid.eq(1),
                in_rsp.ready.eq(self.tmu_tex_read.rsp.ready),
            ]
        with m.Elif(in_rsp.valid & (rsp_route == ROUTE_CPU_WRITE)):
            m.d.comb += [
                cpu_tex_write_bus.rsp.valid.eq(1),
                in_rsp.ready.eq(cpu_tex_write_bus.rsp.ready),
            ]
        with m.Elif(in_rsp.valid & (rsp_route == ROUTE_CPU_READ)):
            m.d.comb += [
                self.cpu_tex_read.rsp.valid.eq(1),
                in_rsp.ready.eq(self.cpu_tex_read.rsp.ready),
            ]

        return m
