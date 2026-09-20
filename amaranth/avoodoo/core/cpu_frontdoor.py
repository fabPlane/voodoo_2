"""``CoreCpuFrontdoor`` -- the PCI BAR address decode.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/core/CoreCpuFrontdoor.scala``
(SpinalHDL 1.15.0), instantiated by ``Core.scala:110`` as
``CoreCpuFrontdoor(c)``.

The 16 MiB BAR (``Core.cpuBmbParams``, 24 address bits) is split on
``address[23:22]`` (:data:`avoodoo.frontend.regdef.DECODE_QUIRKS` has the
long form):

==========  ======================  =====================================
target      window                  goes to
==========  ======================  =====================================
``0``       ``0x000000-0x3fffff``   ``regBus`` -- the register set, with the
                                    address rebuilt as
                                    ``addr[21:14] ## 4'b0000 ## addr[9:0]``,
                                    i.e. the chip-select field forced to zero
``1``       ``0x400000-0x7fffff``   ``lfbBus`` -- the linear frame buffer,
                                    address truncated to 22 bits
``2``/``3`` ``0x800000-0xffffff``   texture memory: a **write** becomes a
                                    ``texWrite`` stream item carrying the raw
                                    23-bit PCI offset; a **read** goes to
                                    ``texReadBus`` relocated by
                                    ``(texBaseAddr[18:0] << 3) + offset``
==========  ======================  =====================================

Commands are fanned out with ``BmbRouting.driveCmdFrom``
(:func:`avoodoo.bus.routing.drive_cmd_from`); ``cpuBus.cmd.ready`` is the
selected destination's ready.  Responses come back through a fixed-priority
``when``/``elsewhen`` chain -- a pending texture-write acknowledgement first,
then ``regBus``, ``lfbBus``, ``texReadBus`` -- each via
``BmbRouting.routeRspTo`` *inside* its branch, so an unselected bus sees
``rsp.ready = 0``.

Texture writes have no downstream response: the frontdoor answers them
itself, exactly one cycle after ``cmd.fire``, with ``texWriteRspPending``
(``RegInit(False)``) and the source captured in ``texWriteRspSource`` (a plain
``Reg``, hence ``reset_less``).  Two quirks of that path are reproduced rather
than fixed, because the Verilog has them: the response is presented for one
cycle whether or not ``cpuBus.rsp.ready`` is high, and a second texture write
accepted on the very cycle the first is being answered clears
``texWriteRspPending`` (last assignment wins), so only one acknowledgement is
sent for the two.  ``io.invalidate`` pulses on every accepted texture write.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Signal
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.bus.routing import drive_cmd_from, route_rsp_to
from avoodoo.config import Config
from avoodoo.core.core_types import TexWritePayload
from avoodoo.core.membus_params import (cpu_bmb_params, cpu_tex_bmb_params,
                                         lfb_params, register_bank_external_params)
from avoodoo.lib.membus import MemBusSignature, RSP_SUCCESS
from avoodoo.lib.stream import fire

__all__ = ["CoreCpuFrontdoor"]


class CoreCpuFrontdoor(wiring.Component):
    """``case class CoreCpuFrontdoor(c: Config) extends Component``.

    Attributes
    ----------
    cpu_bus : In(MemBusSignature(cpu_bmb_params()))
        ``slave(Bmb(voodoo.Core.cpuBmbParams))`` -- 24-bit BAR window.
    reg_bus : Out(MemBusSignature(register_bank_external_params(c)))
        ``master(Bmb(RegisterBank.externalBmbParams(c)))`` -- 22 bits.
    lfb_bus : Out(MemBusSignature(lfb_params(c)))
        ``master(Bmb(Lfb.bmbParams(c)))`` -- 22 bits.
    tex_read_bus : Out(MemBusSignature(cpu_tex_bmb_params()))
        ``master(Bmb(voodoo.Core.cpuTexBmbParams))`` -- 26 bits, 6-bit length.
    tex_write : Out(stream.Signature(TexWritePayload()))
        ``master(Stream(TexWritePayload()))``.
    tex_base_addr : In(24)
        ``in UInt(24 bits)`` -- the TMU ``texBaseAddr`` register.
    invalidate : Out(1)
        ``out Bool()`` -- one pulse per accepted texture write.
    """

    def __init__(self, c: Config | None = None) -> None:
        c = c if c is not None else Config.voodoo1()
        self.c = c
        super().__init__({
            "cpu_bus": In(MemBusSignature(cpu_bmb_params())),
            "reg_bus": Out(MemBusSignature(register_bank_external_params(c))),
            "lfb_bus": Out(MemBusSignature(lfb_params(c))),
            "tex_read_bus": Out(MemBusSignature(cpu_tex_bmb_params())),
            "tex_write": Out(stream.Signature(TexWritePayload())),
            "tex_base_addr": In(24),
            "invalidate": Out(1),
        })

    def elaborate(self, platform):
        m = Module()

        # private val cpuCmd = io.cpuBus.cmd ; cpuRsp = io.cpuBus.rsp
        cpu_cmd = self.cpu_bus.cmd
        cpu_rsp = self.cpu_bus.rsp
        cmd = cpu_cmd.payload.fragment
        rsp = cpu_rsp.payload.fragment

        # private val target = cpuCmd.address(23 downto 22)
        target = Signal(2, name="target")
        is_reg = Signal(name="isReg")
        is_lfb = Signal(name="isLfb")
        is_tex = Signal(name="isTex")
        is_tex_write = Signal(name="isTexWrite")
        is_tex_read = Signal(name="isTexRead")
        m.d.comb += [
            target.eq(cmd.address[22:24]),
            is_reg.eq(target == 0),
            is_lfb.eq(target == 1),
            is_tex.eq(target >= 2),
            is_tex_write.eq(is_tex & cmd.opcode[0]),
            is_tex_read.eq(is_tex & ~cmd.opcode[0]),
        ]

        # private val regAddress =
        #   (cpuCmd.address(21 downto 14) ## U"4'0" ## cpuCmd.address(9 downto 0)).asUInt
        reg_address = Signal(22, name="regAddress")
        m.d.comb += reg_address.eq(
            Cat(cmd.address[0:10], Const(0, 4), cmd.address[14:22]))

        # routeCmd(io.regBus, isReg, regAddress)
        drive_cmd_from(m, self.reg_bus, self.cpu_bus, cpu_cmd.valid & is_reg, reg_address)
        # routeCmd(io.lfbBus, isLfb, cpuCmd.address.resize(22 bits))
        drive_cmd_from(m, self.lfb_bus, self.cpu_bus, cpu_cmd.valid & is_lfb,
                       cmd.address[0:22])

        # io.texWrite.valid := cpuCmd.valid && isTexWrite  (+ payload)
        m.d.comb += [
            self.tex_write.valid.eq(cpu_cmd.valid & is_tex_write),
            self.tex_write.payload.pci_addr.eq(cmd.address[0:23]),
            self.tex_write.payload.data.eq(cmd.data),
            self.tex_write.payload.mask.eq(cmd.mask),
        ]

        # private val texReadPciOffset = cpuCmd.address(22 downto 0)
        # private val texReadBaseAddr  = io.texBaseAddr(18 downto 0)
        # private val texReadSramAddr  = ((texReadBaseAddr << 3) +^ texReadPciOffset).resize(26 bits)
        tex_read_pci_offset = Signal(23, name="texReadPciOffset")
        tex_read_base_addr = Signal(19, name="texReadBaseAddr")
        tex_read_sram_addr = Signal(26, name="texReadSramAddr")
        m.d.comb += [
            tex_read_pci_offset.eq(cmd.address[0:23]),
            tex_read_base_addr.eq(self.tex_base_addr[0:19]),
            # `<< 3` widens to 22 bits, `+^` to 24, `.resize(26)` zero-extends.
            tex_read_sram_addr.eq(Cat(Const(0, 3), tex_read_base_addr) + tex_read_pci_offset),
        ]
        # routeCmd(io.texReadBus, isTexRead, texReadSramAddr)
        drive_cmd_from(m, self.tex_read_bus, self.cpu_bus, cpu_cmd.valid & is_tex_read,
                       tex_read_sram_addr)

        # private val texWriteRspPending = RegInit(False)
        # private val texWriteRspSource  = Reg(cpuCmd.source)      -- no reset
        tex_write_rsp_pending = Signal(name="texWriteRspPending")
        tex_write_rsp_source = Signal(len(cmd.source), name="texWriteRspSource",
                                      reset_less=True)
        tex_write_accepted = Signal(name="texWriteAccepted")
        m.d.comb += tex_write_accepted.eq(fire(cpu_cmd) & is_tex_write)

        with m.If(tex_write_accepted):
            m.d.sync += [
                tex_write_rsp_pending.eq(1),
                tex_write_rsp_source.eq(cmd.source),
            ]
        with m.If(tex_write_rsp_pending):
            m.d.sync += tex_write_rsp_pending.eq(0)

        # io.invalidate := texWriteAccepted
        m.d.comb += self.invalidate.eq(tex_write_accepted)

        # cpuCmd.ready := (isReg && regBus.cmd.ready) || ...
        m.d.comb += cpu_cmd.ready.eq(
            (is_reg & self.reg_bus.cmd.ready)
            | (is_lfb & self.lfb_bus.cmd.ready)
            | (is_tex_write & self.tex_write.ready)
            | (is_tex_read & self.tex_read_bus.cmd.ready))

        # Response defaults.
        m.d.comb += [
            cpu_rsp.valid.eq(0),
            rsp.data.eq(0),
            cpu_rsp.payload.last.eq(1),
            rsp.opcode.eq(RSP_SUCCESS),
            rsp.source.eq(0),
            self.reg_bus.rsp.ready.eq(0),
            self.lfb_bus.rsp.ready.eq(0),
            self.tex_read_bus.rsp.ready.eq(0),
        ]

        # when(texWriteRspPending) { ... } elsewhen (regBus.rsp.valid) { routeRsp(True, regBus) } ...
        with m.If(tex_write_rsp_pending):
            m.d.comb += [
                cpu_rsp.valid.eq(1),
                rsp.source.eq(tex_write_rsp_source),
            ]
        with m.Elif(self.reg_bus.rsp.valid):
            route_rsp_to(m, Const(1), self.reg_bus, self.cpu_bus)
        with m.Elif(self.lfb_bus.rsp.valid):
            route_rsp_to(m, Const(1), self.lfb_bus, self.cpu_bus)
        with m.Elif(self.tex_read_bus.rsp.valid):
            route_rsp_to(m, Const(1), self.tex_read_bus, self.cpu_bus)
        # otherwise: no downstream response is currently pending.

        return m
