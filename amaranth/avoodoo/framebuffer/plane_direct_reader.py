"""``FramebufferPlaneDirectReader`` -- the uncached one-word-per-lane reader.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/framebuffer/FramebufferPlaneDirectReader.scala``
(SpinalHDL 1.15.0), instantiated by ``FramebufferMemSubsystem.scala:82`` as
``FramebufferPlaneDirectReader(c)`` -- the read path used when
``c.useFbReadCache`` is off, with the same ``readReq``/``readRsp``/``mem``
ports as :class:`~avoodoo.framebuffer.plane_reader.FramebufferPlaneReader`
so the subsystem can swap them.

Every ``readReq`` becomes one 4-byte read (``length = 3``, ``source = 0``)
of the word holding the lane; address bit 1 is remembered in the 8-deep
``laneQueue`` so the response can pick the high or low half.  Responses are
in order (single source), pass through ``rspInput`` and a 2-deep queue, and
``busy`` is "anything in flight".

No registers of its own: all state is inside the two ``StreamFifo``\\ s.

Memories (``latency = 2`` FIFOs, no ``syn_ramstyle`` -- both are below the
``width >= 24`` threshold of ``force_distributed_fifo_ram.py``):

===============  =====  =====
FIFO             width  depth
===============  =====  =====
``laneQueue``    1      8
``rspQueue``     16     2
===============  =====  =====
"""

from __future__ import annotations

from amaranth import Module, Mux, unsigned
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (FramebufferReadReq, FramebufferReadRsp,
                                     aligned_word_address)
from avoodoo.core.membus_params import plane_reader_params
from avoodoo.lib.membus import CMD_READ, MemBusSignature
from avoodoo.lib.stream import Fifo, Stream, connect_stream, fire, queue, spinal_fifo_ram_attrs

__all__ = ["FramebufferPlaneDirectReader"]


class FramebufferPlaneDirectReader(wiring.Component):
    """``case class FramebufferPlaneDirectReader(c: Config) extends Component``."""

    def __init__(self, c: Config | None = None) -> None:
        c = Config.voodoo1() if c is None else c
        self.c = c
        self.mem_params = plane_reader_params(c)
        super().__init__({
            "read_req": In(stream.Signature(FramebufferReadReq(c))),
            "read_rsp": Out(stream.Signature(FramebufferReadRsp())),
            "mem": Out(MemBusSignature(self.mem_params)),
            "busy": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        read_req = self.read_req
        mem = self.mem
        cmd, rsp = mem.cmd, mem.rsp
        cmd_frag, rsp_frag = cmd.payload.fragment, rsp.payload.fragment

        # `val laneQueue = StreamFifo(Bool(), 8)`
        lane_queue = Fifo(unsigned(1), 8, mem_attrs=spinal_fifo_ram_attrs(1, 8))
        m.submodules.laneQueue = lane_queue
        # `val rspInput = Stream(ReadRsp()); val rspQueue = rspInput.queue(2)`
        rsp_input = Stream(FramebufferReadRsp(), path=("rspInput",))
        rsp_queue = queue(m, rsp_input, 2, name="rspQueue",
                          mem_attrs=spinal_fifo_ram_attrs(16, 2))

        m.d.comb += [
            read_req.ready.eq(cmd.ready & lane_queue.push.ready),
            cmd.valid.eq(read_req.valid & lane_queue.push.ready),
            cmd_frag.address.eq(aligned_word_address(read_req.payload.address)),
            cmd_frag.opcode.eq(CMD_READ),
            cmd_frag.length.eq(3),
            cmd_frag.source.eq(0),
            cmd_frag.data.eq(0),
            cmd_frag.mask.eq(0),
            cmd.payload.last.eq(1),
            lane_queue.push.valid.eq(fire(cmd)),
            lane_queue.push.payload.eq(read_req.payload.address[1]),
            rsp_input.valid.eq(rsp.valid & lane_queue.pop.valid),
            rsp_input.payload.data.eq(Mux(lane_queue.pop.payload,
                                          rsp_frag.data[16:32], rsp_frag.data[0:16])),
            rsp.ready.eq(rsp_input.ready & lane_queue.pop.valid),
            lane_queue.pop.ready.eq(fire(rsp) & rsp_input.ready),
        ]
        connect_stream(m, self.read_rsp, rsp_queue)
        m.d.comb += self.busy.eq((lane_queue.occupancy != 0) | rsp_queue.valid)
        return m
