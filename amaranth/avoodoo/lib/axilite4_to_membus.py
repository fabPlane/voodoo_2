"""``AxiLite4ToBmbBridge`` -- the host BAR: AXI4-Lite slave to ``MemBus`` master.

Transliteration of ``object AxiLite4ToBmbBridge`` / ``case class
AxiLite4ToBmbBridge`` in ``fpga/src/voodoo/artix/CoreArtix.scala`` (lines
22-190), plus ``CoreArtix.barAddressWidth``; plan Phase 1.5.  ``CoreGowin``
(``fpga/src/voodoo/gowin/CoreGowin.scala:37``) builds it as
``AxiLite4ToBmbBridge(CoreGowin.barAddressWidth, Core.cpuBmbParams,
timeoutLog2 = 30)`` -- the watchdog was widened from the default 20 so a
full-screen clear draining through the PCI FIFO is not mistaken for a wedged
host bus.  The differential rows use the default (``20``) and a ``4`` variant
that makes the timeout paths reachable; ``timeout_log2`` is a plain
constructor argument either way.

One transaction outstanding.  Like the DE10 bridge it never lets the host
hang: if the core does not accept a command, or does not answer, within
``2**timeout_log2`` cycles the AXI transaction completes with ``SLVERR``, the
bridge marks itself ``wedged``, drops further writes and answers further
reads with ``SLVERR``.  The status word at :data:`STATUS_ADDRESS` stays
readable so the host can see what happened.

Every register is a Spinal ``RegInit``/``init``, so nothing here is
``reset_less``.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from avoodoo.core.membus_params import cpu_bmb_params
from avoodoo.lib.axi import RESP_OKAY, RESP_SLVERR, AxiLite4Config, AxiLite4Signature
from avoodoo.lib.membus import (CMD_READ, CMD_WRITE, RSP_ERROR, MemBusParameter,
                                MemBusSignature)
from avoodoo.lib.stream import fire

__all__ = ["STATUS_ADDRESS", "axi_config", "bar_address_width", "AxiLite4ToMemBusBridge"]

#: ``AxiLite4ToBmbBridge.statusAddress``: byte offset of the bridge status
#: word, same as the DE10 H2f bridge so host tooling carries over.  bit 0
#: wedged, bit 1 request timeout seen, bit 2 response timeout seen, bit 4
#: command in flight, bit 5 read in flight, bits 15:8 dropped writes.
STATUS_ADDRESS = 0x254


def axi_config(address_width: int) -> AxiLite4Config:
    """``AxiLite4ToBmbBridge.axiConfig(addressWidth)`` -- 32-bit data."""
    return AxiLite4Config(address_width=address_width, data_width=32)


def bar_address_width() -> int:
    """``CoreArtix.barAddressWidth = Core.cpuBmbParams.access.addressWidth`` (24)."""
    return cpu_bmb_params().access.address_width


class AxiLite4ToMemBusBridge(wiring.Component):
    """``case class AxiLite4ToBmbBridge(addressWidth, bmbParams, timeoutLog2 = 20)``.

    Ports: ``axi`` (AXI4-Lite slave), ``cpu_bus`` (``MemBus`` master,
    ``io.cpuBus``), ``wedged``, ``request_timeout_seen``,
    ``response_timeout_seen`` -- the last three are the status-word bits 0..2
    exported as plain outputs (``CoreGowin`` forwards them as ``hostWedged``
    / ``hostRequestTimeout`` / ``hostResponseTimeout``).

    Request selection: a write needs both ``AW`` and ``W``; writes win over
    reads.  The command goes out as a single-beat ``length = 3`` word access
    with ``source = 0``; ``rsp.ready`` is permanently high.
    """

    def __init__(self, address_width: int, bmb_params: MemBusParameter,
                 timeout_log2: int = 20):
        self.address_width = address_width
        self.bmb_params = bmb_params
        self.timeout_log2 = timeout_log2
        self.timeout_limit = 1 << timeout_log2
        super().__init__({
            "axi": In(AxiLite4Signature(axi_config(address_width))),
            "cpu_bus": Out(MemBusSignature(bmb_params)),
            "wedged": Out(1),
            "request_timeout_seen": Out(1),
            "response_timeout_seen": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        axi, bus = self.axi, self.cpu_bus
        aw = self.address_width
        cmd = bus.cmd.payload.fragment
        rsp = bus.rsp.payload.fragment

        # AXI response holding registers.
        b_pending = Signal(name="bPending")
        b_error = Signal(name="bError")
        r_pending = Signal(name="rPending")
        r_error = Signal(name="rError")
        r_data = Signal(32, name="rData")

        busy = Signal(name="busy")
        busy_is_write = Signal(name="busyIsWrite")
        wedged = Signal(name="wedged")
        request_timeout_seen = Signal(name="requestTimeoutSeen")
        response_timeout_seen = Signal(name="responseTimeoutSeen")
        dropped_writes = Signal(8, name="droppedWrites")
        stall_cycles = Signal(self.timeout_log2 + 1, name="stallCycles")

        # Request selection. A write needs both AW and W; writes win over reads.
        write_req = Signal(name="writeReq")
        read_req = Signal(name="readReq")
        has_req = Signal(name="hasReq")
        req_address = Signal(aw, name="reqAddress")
        word_address = Signal(aw, name="wordAddress")
        can_accept = Signal(name="canAccept")
        m.d.comb += [
            write_req.eq(axi.aw.valid & axi.w.valid),
            read_req.eq(axi.ar.valid & ~write_req),
            has_req.eq(write_req | read_req),
            req_address.eq(Mux(write_req, axi.aw.payload.addr, axi.ar.payload.addr)),
            word_address.eq(Cat(Const(0, 2), req_address[2:])),
            can_accept.eq(~busy & ~b_pending & ~r_pending),
        ]

        status_read_req = Signal(name="statusReadReq")
        local_read_req = Signal(name="localReadReq")
        local_write_req = Signal(name="localWriteReq")
        local_req = Signal(name="localReq")
        m.d.comb += [
            status_read_req.eq(read_req & (word_address == STATUS_ADDRESS)),
            local_read_req.eq(status_read_req | (read_req & wedged)),
            local_write_req.eq(write_req & wedged),
            local_req.eq(local_read_req | local_write_req),
        ]

        issue = Signal(name="issue")
        stall_limit = Signal(name="stallLimit")
        request_timeout = Signal(name="requestTimeout")
        cmd_fire = Signal(name="cmdFire")
        m.d.comb += [
            issue.eq(has_req & can_accept & ~local_req),
            stall_limit.eq(stall_cycles == Const(self.timeout_limit - 1, len(stall_cycles))),
            request_timeout.eq(issue & stall_limit),
            cmd_fire.eq(fire(bus.cmd)),
        ]

        # Fields assigned under `when` go through a wire (`cmdOpcode`, `bResp`,
        # `rResp`): a conditional assignment straight into a packed payload
        # port makes yosys emit the port as a `reg` and each *constant* slice
        # (`cmd.last := True`, `length := 3`) as an `always @*` with an empty
        # sensitivity list, which Verilator never evaluates.
        cmd_opcode = Signal(name="cmdOpcode")
        m.d.comb += [
            bus.cmd.valid.eq(issue & ~stall_limit),
            bus.cmd.payload.last.eq(1),
            cmd.address.eq(word_address),
            cmd.length.eq(3),
            cmd.data.eq(axi.w.payload.data),
            cmd.mask.eq(axi.w.payload.strb),
            cmd_opcode.eq(CMD_READ),
            cmd.opcode.eq(cmd_opcode),
            bus.rsp.ready.eq(1),
        ]
        if "source" in cmd.shape().members:
            m.d.comb += cmd.source.eq(0)
        with m.If(write_req):
            m.d.comb += cmd_opcode.eq(CMD_WRITE)

        status = Signal(32, name="status")
        m.d.comb += [
            status[0].eq(wedged),
            status[1].eq(request_timeout_seen),
            status[2].eq(response_timeout_seen),
            status[4].eq(busy),
            status[5].eq(busy & ~busy_is_write),
            status[8:16].eq(dropped_writes),
        ]

        local_accept = Signal(name="localAccept")
        write_accept = Signal(name="writeAccept")
        read_accept = Signal(name="readAccept")
        m.d.comb += [
            local_accept.eq(has_req & can_accept & local_req),
            write_accept.eq(write_req & (cmd_fire | local_accept | request_timeout)),
            read_accept.eq(read_req & (cmd_fire | local_accept | request_timeout)),
            axi.aw.ready.eq(write_accept),
            axi.w.ready.eq(write_accept),
            axi.ar.ready.eq(read_accept),
        ]

        b_resp = Signal(2, name="bResp")
        r_resp = Signal(2, name="rResp")
        m.d.comb += [
            axi.b.valid.eq(b_pending),
            b_resp.eq(RESP_OKAY),
            axi.b.payload.resp.eq(b_resp),
        ]
        with m.If(b_error):
            m.d.comb += b_resp.eq(RESP_SLVERR)
        m.d.comb += [
            axi.r.valid.eq(r_pending),
            axi.r.payload.data.eq(r_data),
            r_resp.eq(RESP_OKAY),
            axi.r.payload.resp.eq(r_resp),
        ]
        with m.If(r_error):
            m.d.comb += r_resp.eq(RESP_SLVERR)
        m.d.comb += [
            self.wedged.eq(wedged),
            self.request_timeout_seen.eq(request_timeout_seen),
            self.response_timeout_seen.eq(response_timeout_seen),
        ]

        with m.If(fire(axi.b)):
            m.d.sync += b_pending.eq(0)
        with m.If(fire(axi.r)):
            m.d.sync += r_pending.eq(0)

        stalling = Signal(name="stalling")
        m.d.comb += stalling.eq((issue & ~cmd_fire) | (busy & ~fire(bus.rsp)))
        with m.If(stalling):
            with m.If(~stall_limit):
                m.d.sync += stall_cycles.eq(stall_cycles + 1)
        with m.Else():
            m.d.sync += stall_cycles.eq(0)

        def complete_with_error(is_write):
            with m.If(is_write):
                m.d.sync += [b_pending.eq(1), b_error.eq(1)]
            with m.Else():
                m.d.sync += [r_pending.eq(1), r_data.eq(0), r_error.eq(1)]

        def count_dropped_write():
            with m.If(dropped_writes != 0xFF):
                m.d.sync += dropped_writes.eq(dropped_writes + 1)

        with m.If(cmd_fire):
            m.d.sync += [busy.eq(1), busy_is_write.eq(write_req)]

        response_timeout = Signal(name="responseTimeout")
        m.d.comb += response_timeout.eq(busy & ~fire(bus.rsp) & stall_limit)
        with m.If(busy & fire(bus.rsp) & bus.rsp.payload.last):
            m.d.sync += busy.eq(0)
            with m.If(busy_is_write):
                m.d.sync += [b_pending.eq(1), b_error.eq(rsp.opcode == RSP_ERROR)]
            with m.Else():
                m.d.sync += [r_pending.eq(1), r_data.eq(rsp.data),
                             r_error.eq(rsp.opcode == RSP_ERROR)]
        with m.If(response_timeout):
            m.d.sync += [busy.eq(0), wedged.eq(1), response_timeout_seen.eq(1)]
            complete_with_error(busy_is_write)
        with m.If(request_timeout):
            m.d.sync += [wedged.eq(1), request_timeout_seen.eq(1)]
            with m.If(write_req):
                count_dropped_write()
            complete_with_error(write_req)
        with m.If(local_accept):
            with m.If(local_write_req):
                count_dropped_write()
                complete_with_error(Const(1))
            with m.If(status_read_req):
                m.d.sync += [r_pending.eq(1), r_data.eq(status), r_error.eq(0)]
            with m.Elif(local_read_req):
                complete_with_error(Const(0))
        return m
