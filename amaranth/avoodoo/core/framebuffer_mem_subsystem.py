"""``FramebufferMemSubsystem`` -- the framebuffer planes' memory front end.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/core/FramebufferMemSubsystem.scala``
(442 lines with the local ``amaranth/spinal-ref/upstream-local-edits.patch``
applied: the ``scanout*`` diagnostic outputs and the
``allowDirectMiss = false`` scanout reader), instantiated by
``Core.scala:111`` as ``FramebufferMemSubsystem(c)``.

It composes the four Phase 6 framebuffer modules and the five ``fbMem``
masters ``Core`` exports, with no arithmetic of its own beyond the
statistics sums:

=====================  ===============================================================
Scala                  here
=====================  ===============================================================
``fbColorReader``      ``FramebufferPlaneReader(c)`` (``useFbReadCache``) or
                       ``FramebufferPlaneDirectReader(c)`` -- ``plane_reader.py`` /
                       ``plane_direct_reader.py``
``fbAuxReader``        same choice
``fbScanoutReader``    ``FramebufferPlaneReader(c, allowDirectMiss = false)``, always cached
``fbColorBuffer``      ``FramebufferPlaneBuffer(c)`` -- ``plane_buffer.py``
``fbAuxBuffer``        same
``fbWriteArbiter``     ``BmbArbiter`` 2 -> 1 (``useFbWriteBuffer`` only) -- ``lib/membus.py``
``fbColorWriteBridge`` ``BmbArbiter`` 1 -> 1, the ``portCount == 1`` bypass
``fbAuxWriteBridge``   same
``fbColorReadArbiter`` ``BmbArbiter`` 3 -> 1: scanout (0), colour reader (1), ``lfbReadBus`` (2)
``fbAuxReadArbiter``   ``BmbArbiter`` 1 -> 1
=====================  ===============================================================

Ports (Spinal names ``camel_to_snake``'d, every leaf pairs mechanically in the
diff harness):

* ``color_write`` / ``aux_write``: ``slave Stream(WriteReq(c))``, each
  ``s2mPipe``'d into its plane buffer.
* ``color_read_req`` / ``aux_read_req`` / ``scanout_read_req``: ``slave
  Stream(ReadReq(c))``, ``s2mPipe``'d into the readers; ``*_read_rsp`` come
  straight back out.
* ``prefetch_color`` / ``prefetch_aux`` / ``scanout_prefetch_req``: ``slave
  Stream(PrefetchReq(c))``, wired directly (no pipe); with direct readers the
  two colour/aux prefetches are ``ready := True`` and dropped.
* ``lfb_read_bus``: ``slave Bmb(Lfb.fbReadBmbParams(c))`` -- input 2 of the
  colour read arbiter, its ``cmd`` unpiped, its ``rsp`` ``s2mPipe``'d.
* ``flush``: to both plane buffers when ``useFbWriteBuffer``, else they get
  ``False``.
* ``fb_mem_write``, ``fb_color_write_mem``, ``fb_aux_write_mem``,
  ``fb_color_read_mem``, ``fb_aux_read_mem``: ``master Bmb(Core.fbMemBmbParams(c))``.
  With ``useFbWriteBuffer`` the two writers share ``fb_mem_write`` through
  ``fbWriteArbiter`` and the per-plane write ports are tied off (``valid =
  False``, ``opcode = WRITE``, ``last = True``, ``rsp.ready = True``);
  otherwise (``Config.voodoo1()``) each writer has its own port through a
  one-input arbiter and ``fb_mem_write`` is the one tied off.
* ``status`` (``FramebufferMemStatus``: the two plane buffers' ``busy``),
  ``stats`` (``FramebufferMemStats``: 38 x 32-bit sums/counters plus the
  8-bit ``readMaxQueueOccupancy``) -- ``core_types.py``.
* the ten ``scanout_*`` diagnostics: the scanout reader's ``cacheDebug*`` and
  ``fill*`` outputs, exported for the Console JTAG mailbox.

Every memory-side link is ``s2mPipe``'d on **both** ``cmd`` and ``rsp`` when
the master is a plane buffer or a cached reader; a direct reader is wired
without pipes; ``lfbReadBus`` is piped on ``rsp`` only.  That is the whole
timing structure of the module and the reason its registers are what they
are: sixteen ``S2MPipe`` stages (``rValidN`` init 1, ``rData`` a plain
``Reg`` and therefore ``reset_less`` -- ``lib/stream.py``) on the five
``io`` request streams and the eleven bus links, plus the ten 32-bit
``mem*CmdCount`` / ``mem*BlockedCycles`` counters, all ``init(0)``.

Statistics: each ``stats.*`` sum is ``(colour + aux).resized`` -- a wrapping
32-bit add -- gated to ``U(0)`` when the branch that produces it is not
built (``fill*``/``read*`` need cached readers, ``writeDrain*``/``writeRotate*``/
``writeSingleWord*`` need the write buffer); ``readMaxQueueOccupancy`` is
the larger of the two readers' ``maxOccupancy``.  The five ``mem*BlockedCycles``
count cycles a master holds ``cmd.valid`` against a low ``ready`` at the
arbiter input (i.e. *after* the ``s2mPipe``), and ``mem*CmdCount`` count the
master's own ``cmd.fire`` (before it).  The Scala repeats each ``when`` in
both branches of ``if (c.useFbWriteBuffer)`` with the arbiter renamed; here
the arbiter input is picked once and the ``when`` written once.

``exposeToSim()`` is a Verilator-visibility hint and has no RTL.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, Value
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (FramebufferMemStats, FramebufferMemStatus,
                                     FramebufferPrefetchReq, FramebufferReadReq,
                                     FramebufferReadRsp, FramebufferWriteReq)
from avoodoo.core.membus_params import (fb_mem_bmb_params, lfb_fb_read_params,
                                        plane_buffer_params, plane_reader_params)
from avoodoo.framebuffer.plane_buffer import FramebufferPlaneBuffer
from avoodoo.framebuffer.plane_direct_reader import FramebufferPlaneDirectReader
from avoodoo.framebuffer.plane_reader import FramebufferPlaneReader
from avoodoo.lib.membus import CMD_WRITE, Arbiter, MemBusSignature, connect as connect_membus, log2_up
from avoodoo.lib.stream import connect_stream, fire, s2m_pipe

__all__ = ["FramebufferMemSubsystem", "STAT_SUMS_CACHED", "STAT_SUMS_WRITE_BUFFER"]

#: ``stats.<name> := (colorReader.<src> + auxReader.<src>).resized`` -- only
#: with cached readers, else ``U(0, 32 bits)``.  (stats field, reader port)
STAT_SUMS_CACHED = (
    ("fill_hits", "fill_hits"),
    ("fill_misses", "fill_misses"),
    ("fill_burst_count", "fill_burst_count"),
    ("fill_burst_beats", "fill_burst_beats"),
    ("fill_stall_cycles", "fill_stall_cycles"),
    ("read_req_count", "req_count"),
    ("read_req_forward_step_count", "req_forward_step_count"),
    ("read_req_backward_step_count", "req_backward_step_count"),
    ("read_req_same_word_count", "req_same_word_count"),
    ("read_req_same_line_count", "req_same_line_count"),
    ("read_req_other_count", "req_other_count"),
    ("read_single_beat_burst_count", "single_beat_burst_count"),
    ("read_multi_beat_burst_count", "multi_beat_burst_count"),
)

#: ``stats.<name> := (colorWritePort.<name> + auxWritePort.<name>).resized`` --
#: only with the write buffer, else ``U(0, 32 bits)``.  ``writeStallCycles``
#: is summed unconditionally and is not in this list.
STAT_SUMS_WRITE_BUFFER = (
    "write_drain_count",
    "write_full_drain_count",
    "write_partial_drain_count",
    "write_drain_reason_full_count",
    "write_drain_reason_rotate_count",
    "write_drain_reason_flush_count",
    "write_drain_dirty_word_total",
    "write_rotate_blocked_cycles",
    "write_single_word_drain_count",
    "write_single_word_drain_start_at_zero_count",
    "write_single_word_drain_start_at_last_count",
    "write_rotate_adjacent_line_count",
    "write_rotate_same_line_gap_count",
    "write_rotate_other_line_count",
)


def _resized(v, width: int):
    """Spinal ``.resized`` / ``.resize(width)``: truncate or zero-extend."""
    v = Value.cast(v)
    if len(v) >= width:
        return v[:width]
    return Cat(v, Const(0, width - len(v)))


def _sum32(a, b):
    """``(a + b).resized`` into a 32-bit target: Spinal ``UInt + UInt`` keeps
    ``max(wa, wb)`` bits and wraps, so the carry out is dropped."""
    return _resized(a + b, 32)


class FramebufferMemSubsystem(wiring.Component):
    """``case class FramebufferMemSubsystem(c: Config) extends Component``."""

    def __init__(self, c: Config | None = None) -> None:
        c = Config.voodoo1() if c is None else c
        self.c = c
        self.fb_mem_params = fb_mem_bmb_params(c)
        self.lfb_read_params = lfb_fb_read_params(c)
        self.plane_buffer_params = plane_buffer_params(c)
        self.plane_reader_params = plane_reader_params(c)
        # private val useCachedReaders = c.useFbReadCache
        self.use_cached_readers = bool(c.use_fb_read_cache)
        self.use_fb_write_buffer = bool(c.use_fb_write_buffer)
        aw = c.address_width
        super().__init__({
            "color_write": In(stream.Signature(FramebufferWriteReq(c))),
            "aux_write": In(stream.Signature(FramebufferWriteReq(c))),
            "color_read_req": In(stream.Signature(FramebufferReadReq(c))),
            "color_read_rsp": Out(stream.Signature(FramebufferReadRsp())),
            "scanout_prefetch_req": In(stream.Signature(FramebufferPrefetchReq(c))),
            "scanout_read_req": In(stream.Signature(FramebufferReadReq(c))),
            "scanout_read_rsp": Out(stream.Signature(FramebufferReadRsp())),
            "aux_read_req": In(stream.Signature(FramebufferReadReq(c))),
            "aux_read_rsp": Out(stream.Signature(FramebufferReadRsp())),
            "prefetch_color": In(stream.Signature(FramebufferPrefetchReq(c))),
            "prefetch_aux": In(stream.Signature(FramebufferPrefetchReq(c))),
            "lfb_read_bus": In(MemBusSignature(self.lfb_read_params)),
            "flush": In(1),
            "fb_mem_write": Out(MemBusSignature(self.fb_mem_params)),
            "fb_color_write_mem": Out(MemBusSignature(self.fb_mem_params)),
            "fb_aux_write_mem": Out(MemBusSignature(self.fb_mem_params)),
            "fb_color_read_mem": Out(MemBusSignature(self.fb_mem_params)),
            "fb_aux_read_mem": Out(MemBusSignature(self.fb_mem_params)),
            "status": Out(FramebufferMemStatus()),
            "stats": Out(FramebufferMemStats()),
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
        })

        # The sub-components are the Scala's `val`s (lines 91-102, 147-151):
        # built here so tests can reach them before elaboration, attached
        # under their Scala `setName`s in `elaborate`.
        cached, wb = self.use_cached_readers, self.use_fb_write_buffer
        reader = FramebufferPlaneReader if cached else FramebufferPlaneDirectReader
        #: ``fbColorReader`` / ``fbAuxReader``: cached or direct per ``c.useFbReadCache``.
        self.color_reader = reader(c)
        self.aux_reader = reader(c)
        #: ``fbScanoutReader = FramebufferPlaneReader(c, allowDirectMiss = false)``.
        self.scanout_reader = FramebufferPlaneReader(c, allow_direct_miss=False)
        #: ``fbColorBuffer`` / ``fbAuxBuffer``.
        self.color_write_port = FramebufferPlaneBuffer(c)
        self.aux_write_port = FramebufferPlaneBuffer(c)
        self.fb_write_arbiter = self._make_fb_write_arbiter() if wb else None
        self.fb_color_write_bridge = None if wb else self._make_fb_single_write_bridge()
        self.fb_aux_write_bridge = None if wb else self._make_fb_single_write_bridge()
        self.fb_color_read_arbiter = self._make_fb_color_read_arbiter()
        self.fb_aux_read_arbiter = self._make_fb_aux_read_arbiter()

    # -- the four `makeFb*` helpers -----------------------------------------

    def _make_fb_write_arbiter(self) -> Arbiter:
        """``makeFbWriteArbiter()``: two plane buffers -> ``fbMemWrite``."""
        return Arbiter([self.plane_buffer_params, self.plane_buffer_params],
                       self.fb_mem_params, lower_first_priority=True)

    def _make_fb_single_write_bridge(self) -> Arbiter:
        """``makeFbSingleWriteBridge()``: the ``portCount == 1`` bypass."""
        return Arbiter([self.plane_buffer_params], self.fb_mem_params,
                       lower_first_priority=True)

    def _make_fb_color_read_arbiter(self) -> Arbiter:
        """``makeFbColorReadArbiter()``: scanout, colour reader, ``lfbReadBus``."""
        return Arbiter([self.plane_reader_params, self.plane_reader_params,
                        self.lfb_read_params],
                       self.fb_mem_params, lower_first_priority=True)

    def _make_fb_aux_read_arbiter(self) -> Arbiter:
        """``makeFbAuxReadArbiter()``: the aux reader alone."""
        return Arbiter([self.plane_reader_params], self.fb_mem_params,
                       lower_first_priority=True)

    @staticmethod
    def _disable_read_port(m: Module, port: FramebufferPlaneBuffer) -> None:
        """``disableReadPort(port)``: the plane buffers never read."""
        m.d.comb += [
            port.read_req.valid.eq(0),
            port.read_req.payload.address.eq(0),
            port.read_rsp.ready.eq(1),
        ]

    @staticmethod
    def _tie_off_write_master(m: Module, bus) -> None:
        """The eight ``io.fb*.cmd.* := ...`` lines plus ``rsp.ready := True``
        for a ``master(Bmb)`` this configuration does not use."""
        frag = bus.cmd.payload.fragment
        m.d.comb += [
            bus.cmd.valid.eq(0),
            frag.address.eq(0),
            frag.opcode.eq(CMD_WRITE),
            frag.length.eq(0),
            frag.data.eq(0),
            frag.mask.eq(0),
            bus.cmd.payload.last.eq(1),
            frag.source.eq(0),
            bus.rsp.ready.eq(1),
        ]

    def elaborate(self, platform):
        m = Module()
        c = self.c
        cached = self.use_cached_readers
        wb = self.use_fb_write_buffer

        # ---- components (Scala lines 89-102, `setName`d) -------------------
        color_reader, aux_reader = self.color_reader, self.aux_reader
        scanout_reader = self.scanout_reader
        color_write_port, aux_write_port = self.color_write_port, self.aux_write_port
        m.submodules.fbColorReader = color_reader
        m.submodules.fbAuxReader = aux_reader
        m.submodules.fbScanoutReader = scanout_reader
        m.submodules.fbColorBuffer = color_write_port
        m.submodules.fbAuxBuffer = aux_write_port

        # ---- flush (104-110) ---------------------------------------------
        if wb:
            m.d.comb += [color_write_port.flush.eq(self.flush),
                         aux_write_port.flush.eq(self.flush)]
        else:
            m.d.comb += [color_write_port.flush.eq(0), aux_write_port.flush.eq(0)]

        # ---- request / response streams (112-130) ------------------------
        # io.colorReadReq.s2mPipe() >> reader.io.readReq ; io.colorReadRsp << reader.io.readRsp
        connect_stream(m, color_reader.read_req,
                       s2m_pipe(m, self.color_read_req, name="io_colorReadReq_s2mPipe"))
        connect_stream(m, self.color_read_rsp, color_reader.read_rsp)
        connect_stream(m, aux_reader.read_req,
                       s2m_pipe(m, self.aux_read_req, name="io_auxReadReq_s2mPipe"))
        connect_stream(m, self.aux_read_rsp, aux_reader.read_rsp)
        if cached:
            connect_stream(m, color_reader.prefetch_req, self.prefetch_color)
            connect_stream(m, aux_reader.prefetch_req, self.prefetch_aux)
        else:
            m.d.comb += [self.prefetch_color.ready.eq(1), self.prefetch_aux.ready.eq(1)]

        connect_stream(m, scanout_reader.prefetch_req, self.scanout_prefetch_req)
        connect_stream(m, scanout_reader.read_req,
                       s2m_pipe(m, self.scanout_read_req, name="io_scanoutReadReq_s2mPipe"))
        connect_stream(m, self.scanout_read_rsp, scanout_reader.read_rsp)
        # (131-140) the scanout reader's diagnostics, straight out.
        m.d.comb += [
            self.scanout_cache_debug.eq(scanout_reader.cache_debug),
            self.scanout_cache_read_addr.eq(scanout_reader.cache_debug_read_addr),
            self.scanout_cache_expected_addr.eq(scanout_reader.cache_debug_expected_addr),
            self.scanout_cache_remaining.eq(scanout_reader.cache_debug_remaining),
            self.scanout_cache_occupancy.eq(scanout_reader.cache_debug_occupancy),
            self.scanout_fill_hits.eq(scanout_reader.fill_hits),
            self.scanout_fill_misses.eq(scanout_reader.fill_misses),
            self.scanout_fill_burst_count.eq(scanout_reader.fill_burst_count),
            self.scanout_fill_burst_beats.eq(scanout_reader.fill_burst_beats),
            self.scanout_fill_stall_cycles.eq(scanout_reader.fill_stall_cycles),
        ]

        # (142-145)
        self._disable_read_port(m, color_write_port)
        self._disable_read_port(m, aux_write_port)
        connect_stream(m, color_write_port.write_req,
                       s2m_pipe(m, self.color_write, name="io_colorWrite_s2mPipe"))
        connect_stream(m, aux_write_port.write_req,
                       s2m_pipe(m, self.aux_write, name="io_auxWrite_s2mPipe"))

        # ---- arbiters (147-151) ------------------------------------------
        fb_write_arbiter = self.fb_write_arbiter
        fb_color_write_bridge = self.fb_color_write_bridge
        fb_aux_write_bridge = self.fb_aux_write_bridge
        fb_color_read_arbiter = self.fb_color_read_arbiter
        fb_aux_read_arbiter = self.fb_aux_read_arbiter
        if wb:
            m.submodules.fbWriteArbiter = fb_write_arbiter
        else:
            m.submodules.fbColorWriteBridge = fb_color_write_bridge
            m.submodules.fbAuxWriteBridge = fb_aux_write_bridge
        m.submodules.fbColorReadArbiter = fb_color_read_arbiter
        m.submodules.fbAuxReadArbiter = fb_aux_read_arbiter

        def piped_link(arb_input, master_bus, tag: str):
            """``arb.cmd << master.cmd.s2mPipe(); master.rsp << arb.rsp.s2mPipe()``."""
            connect_stream(m, arb_input.cmd,
                           s2m_pipe(m, master_bus.cmd, name=f"{tag}_cmd_s2mPipe"))
            connect_stream(m, master_bus.rsp,
                           s2m_pipe(m, arb_input.rsp, name=f"{tag}_rsp_s2mPipe"))

        def direct_link(arb_input, master_bus):
            """``arb.cmd << master.cmd; master.rsp << arb.rsp`` (direct readers)."""
            connect_stream(m, arb_input.cmd, master_bus.cmd)
            connect_stream(m, master_bus.rsp, arb_input.rsp)

        # The write side: the two branches of `if (c.useFbWriteBuffer)`
        # (153-235) differ only in which arbiter the plane buffers feed and
        # which output ports are tied off; the read side is identical in both.
        if wb:
            piped_link(fb_write_arbiter.inputs[0], color_write_port.mem, "fbColorBuffer")
            piped_link(fb_write_arbiter.inputs[1], aux_write_port.mem, "fbAuxBuffer")
            color_write_input = fb_write_arbiter.inputs[0]
            aux_write_input = fb_write_arbiter.inputs[1]
        else:
            self._tie_off_write_master(m, self.fb_mem_write)
            piped_link(fb_color_write_bridge.inputs[0], color_write_port.mem, "fbColorBuffer")
            piped_link(fb_aux_write_bridge.inputs[0], aux_write_port.mem, "fbAuxBuffer")
            color_write_input = fb_color_write_bridge.inputs[0]
            aux_write_input = fb_aux_write_bridge.inputs[0]

        if cached:
            piped_link(fb_color_read_arbiter.inputs[1], color_reader.mem, "fbColorReader")
            piped_link(fb_aux_read_arbiter.inputs[0], aux_reader.mem, "fbAuxReader")
        else:
            direct_link(fb_color_read_arbiter.inputs[1], color_reader.mem)
            direct_link(fb_aux_read_arbiter.inputs[0], aux_reader.mem)
        piped_link(fb_color_read_arbiter.inputs[0], scanout_reader.mem, "fbScanoutReader")
        # fbColorReadArbiter.io.inputs(2).cmd << io.lfbReadBus.cmd
        # io.lfbReadBus.rsp << fbColorReadArbiter.io.inputs(2).rsp.s2mPipe()
        connect_stream(m, fb_color_read_arbiter.inputs[2].cmd, self.lfb_read_bus.cmd)
        connect_stream(m, self.lfb_read_bus.rsp,
                       s2m_pipe(m, fb_color_read_arbiter.inputs[2].rsp,
                                name="io_lfbReadBus_rsp_s2mPipe"))

        if wb:
            connect_membus(m, self.fb_mem_write, fb_write_arbiter.output)
            self._tie_off_write_master(m, self.fb_color_write_mem)
            self._tie_off_write_master(m, self.fb_aux_write_mem)
        else:
            connect_membus(m, self.fb_color_write_mem, fb_color_write_bridge.output)
            connect_membus(m, self.fb_aux_write_mem, fb_aux_write_bridge.output)
        connect_membus(m, self.fb_color_read_mem, fb_color_read_arbiter.output)
        connect_membus(m, self.fb_aux_read_mem, fb_aux_read_arbiter.output)

        # ---- the ten counters (237-332) ----------------------------------
        mem_color_write_cmd_count = Signal(32, name="memColorWriteCmdCount")
        mem_aux_write_cmd_count = Signal(32, name="memAuxWriteCmdCount")
        mem_color_read_cmd_count = Signal(32, name="memColorReadCmdCount")
        mem_aux_read_cmd_count = Signal(32, name="memAuxReadCmdCount")
        mem_lfb_read_cmd_count = Signal(32, name="memLfbReadCmdCount")
        mem_color_write_blocked_cycles = Signal(32, name="memColorWriteBlockedCycles")
        mem_aux_write_blocked_cycles = Signal(32, name="memAuxWriteBlockedCycles")
        mem_color_read_blocked_cycles = Signal(32, name="memColorReadBlockedCycles")
        mem_aux_read_blocked_cycles = Signal(32, name="memAuxReadBlockedCycles")
        mem_lfb_read_blocked_cycles = Signal(32, name="memLfbReadBlockedCycles")

        def count_blocked(counter, master_cmd, arb_input_cmd):
            # when(master.cmd.valid && !arb.inputs(i).cmd.ready) { counter := counter + 1 }
            with m.If(master_cmd.valid & ~arb_input_cmd.ready):
                m.d.sync += counter.eq(counter + 1)

        def count_fires(counter, master_cmd):
            with m.If(fire(master_cmd)):
                m.d.sync += counter.eq(counter + 1)

        count_blocked(mem_color_write_blocked_cycles, color_write_port.mem.cmd,
                      color_write_input.cmd)
        count_blocked(mem_aux_write_blocked_cycles, aux_write_port.mem.cmd,
                      aux_write_input.cmd)
        count_fires(mem_color_write_cmd_count, color_write_port.mem.cmd)
        count_fires(mem_aux_write_cmd_count, aux_write_port.mem.cmd)
        count_blocked(mem_lfb_read_blocked_cycles, self.lfb_read_bus.cmd,
                      fb_color_read_arbiter.inputs[2].cmd)
        count_fires(mem_lfb_read_cmd_count, self.lfb_read_bus.cmd)
        # (288-332): the same four `when`s for whichever reader pair is built.
        count_blocked(mem_color_read_blocked_cycles, color_reader.mem.cmd,
                      fb_color_read_arbiter.inputs[1].cmd)
        count_blocked(mem_aux_read_blocked_cycles, aux_reader.mem.cmd,
                      fb_aux_read_arbiter.inputs[0].cmd)
        count_fires(mem_color_read_cmd_count, color_reader.mem.cmd)
        count_fires(mem_aux_read_cmd_count, aux_reader.mem.cmd)

        # ---- status / stats (334-438) ------------------------------------
        m.d.comb += [
            self.status.color_busy.eq(color_write_port.busy),
            self.status.aux_busy.eq(aux_write_port.busy),
        ]
        stats = self.stats
        for field, port in STAT_SUMS_CACHED:
            if cached:
                m.d.comb += stats[field].eq(_sum32(getattr(color_reader, port),
                                                   getattr(aux_reader, port)))
            else:
                m.d.comb += stats[field].eq(0)
        m.d.comb += stats.write_stall_cycles.eq(
            _sum32(color_write_port.write_stall_cycles, aux_write_port.write_stall_cycles))
        for field in STAT_SUMS_WRITE_BUFFER:
            if wb:
                m.d.comb += stats[field].eq(_sum32(getattr(color_write_port, field),
                                                   getattr(aux_write_port, field)))
            else:
                m.d.comb += stats[field].eq(0)
        m.d.comb += [
            stats.mem_color_write_cmd_count.eq(mem_color_write_cmd_count),
            stats.mem_aux_write_cmd_count.eq(mem_aux_write_cmd_count),
            stats.mem_color_read_cmd_count.eq(mem_color_read_cmd_count if cached else 0),
            stats.mem_aux_read_cmd_count.eq(mem_aux_read_cmd_count if cached else 0),
            stats.mem_lfb_read_cmd_count.eq(mem_lfb_read_cmd_count),
            stats.mem_color_write_blocked_cycles.eq(mem_color_write_blocked_cycles),
            stats.mem_aux_write_blocked_cycles.eq(mem_aux_write_blocked_cycles),
            stats.mem_color_read_blocked_cycles.eq(
                mem_color_read_blocked_cycles if cached else 0),
            stats.mem_aux_read_blocked_cycles.eq(
                mem_aux_read_blocked_cycles if cached else 0),
            stats.mem_lfb_read_blocked_cycles.eq(mem_lfb_read_blocked_cycles),
        ]
        if cached:
            # Mux(color.maxOccupancy > aux.maxOccupancy, color, aux).resize(8 bits)
            m.d.comb += stats.read_max_queue_occupancy.eq(_resized(
                Mux(color_reader.max_occupancy > aux_reader.max_occupancy,
                    color_reader.max_occupancy, aux_reader.max_occupancy), 8))
        else:
            m.d.comb += stats.read_max_queue_occupancy.eq(0)

        return m
