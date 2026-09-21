"""``FramebufferPlaneReader`` -- the span-prefetching cached plane reader.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/framebuffer/FramebufferPlaneReader.scala``
(461 lines, SpinalHDL 1.15.0), instantiated by
``FramebufferMemSubsystem.scala:78`` as ``FramebufferPlaneReader(c)``.

How it works, in the Scala's own terms:

* ``prefetchReq`` carries an inclusive ``[startAddress, endAddress]`` byte span
  of 16-bit lanes.  :func:`make_span` turns it into a ``SpanCmd`` (word-aligned
  first/last word, word and lane counts) that is pushed into *two* 64-deep
  FIFOs at once: ``spanIssueQueue`` (what still has to be fetched) and
  ``consumeQueue`` (what the consumer is expected to ask for, in order).
* ``spanIssueQueue`` drains into one read burst per span on ``internalMem``
  (``source = 0``); the span moves to the 4-deep ``issuedSpanQueue`` while its
  beats come back.  Each returned word yields its low and/or high lane into
  ``laneFifo`` (801 deep); a word that yields both parks the high lane in
  ``pendingLane*`` for one cycle because the FIFO takes one lane per cycle.
* ``readReq`` is served from ``laneFifo`` when its address equals the next
  expected lane (``consumeCurrentAddr``).  A request *ahead* of the expected
  lane discards cached lanes until they line up (``skipCachedLane``, the
  pipeline may kill pixels).  A request *behind* it, or with nothing
  prefetched, is a **direct miss**: a one-word read with ``source = 1`` whose
  lane select is remembered in ``directMissLaneQueue``; the answer is spliced
  into the same ordered ``readRspFifo``, and cached hits wait while a miss is
  outstanding so responses stay in request order.
* The 14 counters and the two ``cacheDebug*`` vectors are diagnostics read by
  the CoreSim harness; they are ported exactly because they are ports.

Register map (every one is ``init(0)`` / ``RegInit(False)`` in the Scala, so
none is ``reset_less``):
``fillHits``, ``fillMisses``, ``fillBurstCount``, ``fillBurstBeats``,
``fillStallCycles``, ``reqCount``, ``reqForwardStepCount``,
``reqBackwardStepCount``, ``reqSameWordCount``, ``reqSameLineCount`` (never
incremented -- ``reqSameLineCount := reqSameLineCount``), ``reqOtherCount``,
``singleBeatBurstCount``, ``multiBeatBurstCount``, ``maxOccupancy``,
``prevReqValid``, ``prevReqAddr``, ``consumeActive``, ``consumeExpectedAddr``,
``consumeRemainingLanes``, ``activeFillWordIndex``, ``pendingLaneValid``,
``pendingLaneData``.

Memories (all inside :class:`~avoodoo.lib.stream.Fifo`, ``latency = 2``, i.e.
synchronous read plus an ``m2sPipe``, exactly ``StreamFifo``'s default):

=====================  =========  =====  ==============================
FIFO                   width      depth  ``syn_ramstyle``
=====================  =========  =====  ==============================
``spanIssueQueue``     124        64     ``distributed_ram`` (124 >= 24, 64 <= 64)
``issuedSpanQueue``    124        4      ``distributed_ram``
``consumeQueue``       124        64     ``distributed_ram``
``laneFifo``           16         801    none (16 < 24)
``directMissLaneQueue``  1        8      none
``readRspFifo``        16         16     none
=====================  =========  =====  ==============================

The attribute column is ``fpga/hw/gowin/console/force_distributed_fifo_ram.py``'s
``should_force(width, depth)`` rule (``width >= 24 and depth <= 64``) applied
to the Spinal build's arrays, via :func:`~avoodoo.lib.stream.spinal_fifo_ram_attrs`,
so the Amaranth bitstream keeps the Spinal one's BSRAM/SSRAM split.  Widths
are for ``Config.voodoo1()`` (26-bit addresses, 10-bit lane counts); the rule
is evaluated on the actual widths.

``GenerationFlags.formal { assert(...) }`` (Scala lines 323-343) is not RTL
and is not ported here; ``tests/test_plane_reader.py`` checks the same four
invariants every cycle instead.

Burst splitting: ``needBurstSplitter(c)`` is **false** for
``Config.voodoo1()`` (``((800+1)/2)*4 = 1600 <= 1 << 11``), so the shipping
core wires ``internalMem`` straight to ``io.mem`` and never elaborates the
``BmbAlignedSpliter`` + ``BmbContextRemover`` chain.  The conditional is
ported exactly, and a ``Config`` with a wider framebuffer than its burst
(``tests/test_plane_reader.py::test_length_fixer_splits_long_span_bursts``)
does build the chain.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (FramebufferPrefetchReq, FramebufferReadReq,
                                     FramebufferReadRsp, aligned_word_address)
from avoodoo.core.membus_params import (plane_reader_internal_params,
                                        plane_reader_need_burst_splitter,
                                        plane_reader_params,
                                        plane_reader_splitter_params)
from avoodoo.lib.membus import (CMD_READ, AlignedSplitter, ContextRemover, MemBus,
                                MemBusSignature, connect as connect_membus, log2_up)
from avoodoo.lib.stream import Fifo, connect_stream, fire, spinal_fifo_ram_attrs

__all__ = [
    "ISSUE_QUEUE_DEPTH",
    "SpanCmd",
    "exact_lane_address",
    "FramebufferPlaneReader",
]

#: ``val issueQueueDepth = 64`` -- also the ``ContextRemover``'s ``pendingMax``.
ISSUE_QUEUE_DEPTH = 64


class SpanCmd(data.StructLayout):
    """``case class SpanCmd()`` (Scala line 17) -- one prefetch span, resolved.

    ``startAddress``/``endAddress`` are the lane-exact (bit 0 cleared)
    inclusive byte addresses; ``firstWordAddress``/``lastWordAddress`` the
    word-aligned ones; ``wordCount`` / ``laneCount`` the inclusive counts.
    """

    def __init__(self, addr_width: int, span_lane_count_width: int) -> None:
        self.addr_width = addr_width
        self.span_lane_count_width = span_lane_count_width
        super().__init__({
            "start_address": unsigned(addr_width),
            "end_address": unsigned(addr_width),
            "first_word_address": unsigned(addr_width),
            "last_word_address": unsigned(addr_width),
            "word_count": unsigned(span_lane_count_width),
            "lane_count": unsigned(span_lane_count_width),
        })


def exact_lane_address(address):
    """``def exactLaneAddress(address: UInt)`` -- clear bit 0."""
    return Cat(Const(0, 1), address[1:])


def _resize(value, width: int):
    """Spinal ``.resize(width)`` / ``.resized``: truncate or zero-extend."""
    if len(value) >= width:
        return value[:width]
    return Cat(value, Const(0, width - len(value)))


def _fifo(m: Module, name: str, shape, depth: int) -> Fifo:
    """``StreamFifo(HardType(shape), depth)`` with the Spinal build's RAM attrs."""
    width = shape.size if hasattr(shape, "size") else len(Signal(shape))
    f = Fifo(shape, depth, mem_attrs=spinal_fifo_ram_attrs(width, depth))
    m.submodules[name] = f
    return f


class FramebufferPlaneReader(wiring.Component):
    """``case class FramebufferPlaneReader(c: Config) extends Component``.

    Ports (Scala ``io``): ``prefetchReq`` / ``readReq`` slave streams,
    ``readRsp`` master stream, ``mem`` master ``Bmb`` with
    ``FramebufferPlaneReader.bmbParams(c)``, ``busy``, fourteen 32-bit
    counters, ``maxOccupancy`` and the four ``cacheDebug*`` outputs.
    """

    def __init__(self, c: Config | None = None) -> None:
        c = Config.voodoo1() if c is None else c
        self.c = c
        self.addr_width = c.address_width
        self.max_span_pixels = c.max_fb_dims[0]
        self.span_lane_count_width = log2_up(self.max_span_pixels + 1)
        self.line_buffer_lanes = max(self.max_span_pixels + 1, 16)
        self.issue_queue_depth = ISSUE_QUEUE_DEPTH
        self.span_cmd = SpanCmd(self.addr_width, self.span_lane_count_width)
        self.need_burst_splitter = plane_reader_need_burst_splitter(c)
        self.internal_params = plane_reader_internal_params(c)
        self.mem_params = plane_reader_params(c)
        aw = self.addr_width
        super().__init__({
            "prefetch_req": In(stream.Signature(FramebufferPrefetchReq(c))),
            "read_req": In(stream.Signature(FramebufferReadReq(c))),
            "read_rsp": Out(stream.Signature(FramebufferReadRsp())),
            "mem": Out(MemBusSignature(self.mem_params)),
            "busy": Out(1),
            "fill_hits": Out(32),
            "fill_misses": Out(32),
            "fill_burst_count": Out(32),
            "fill_burst_beats": Out(32),
            "fill_stall_cycles": Out(32),
            "req_count": Out(32),
            "req_forward_step_count": Out(32),
            "req_backward_step_count": Out(32),
            "req_same_word_count": Out(32),
            "req_same_line_count": Out(32),
            "req_other_count": Out(32),
            "single_beat_burst_count": Out(32),
            "multi_beat_burst_count": Out(32),
            "max_occupancy": Out(8),
            "cache_debug": Out(64),
            "cache_debug_read_addr": Out(aw),
            "cache_debug_expected_addr": Out(aw),
            "cache_debug_remaining": Out(self.span_lane_count_width),
            "cache_debug_occupancy": Out(32),
        })

    # -- `def makeSpan(req: PrefetchReq): SpanCmd` ---------------------------

    def make_span(self, m: Module, req, *, name: str = "incomingSpan"):
        aw, lw = self.addr_width, self.span_lane_count_width
        span = Signal(self.span_cmd, name=name)
        start_exact = exact_lane_address(req.start_address)
        end_exact = exact_lane_address(req.end_address)
        first_word = aligned_word_address(start_exact)
        last_word = aligned_word_address(end_exact)
        word_delta = (last_word - first_word)[:aw]          # UInt - UInt: aw bits
        lane_delta = (end_exact - start_exact)[:aw]
        m.d.comb += [
            span.start_address.eq(start_exact),
            span.end_address.eq(end_exact),
            span.first_word_address.eq(first_word),
            span.last_word_address.eq(last_word),
            # ((delta >> 2).resize(lw) + 1).resized
            span.word_count.eq((_resize(word_delta[2:], lw) + 1)[:lw]),
            span.lane_count.eq((_resize(lane_delta[1:], lw) + 1)[:lw]),
        ]
        return span

    # -- `def classifyReqStep(curr, prev): UInt` (3 bits) ---------------------

    def classify_req_step(self, curr, prev):
        aw = self.addr_width
        same_word = aligned_word_address(curr) == aligned_word_address(prev)
        forward = curr == (prev + Const(2, aw))[:aw]
        backward = prev == (curr + Const(2, aw))[:aw]
        return Mux(same_word, Const(0, 3),
                   Mux(forward, Const(1, 3),
                       Mux(backward, Const(2, 3), Const(4, 3))))

    def elaborate(self, platform):
        m = Module()
        c = self.c
        aw, lw = self.addr_width, self.span_lane_count_width
        span_cmd = self.span_cmd

        # -- FIFOs (Scala lines 69-74, 166) ---------------------------------
        span_issue_queue = _fifo(m, "spanIssueQueue", span_cmd, self.issue_queue_depth)
        issued_span_queue = _fifo(m, "issuedSpanQueue", span_cmd, 4)
        lane_fifo = _fifo(m, "laneFifo", unsigned(16), self.line_buffer_lanes)
        direct_miss_lane_queue = _fifo(m, "directMissLaneQueue", unsigned(1), 8)
        read_rsp_fifo = _fifo(m, "readRspFifo", FramebufferReadRsp(), 16)
        consume_queue = _fifo(m, "consumeQueue", span_cmd, self.issue_queue_depth)

        # -- counters (Scala lines 76-91) ------------------------------------
        fill_hits = Signal(32, name="fillHits")
        fill_misses = Signal(32, name="fillMisses")
        fill_burst_count = Signal(32, name="fillBurstCount")
        fill_burst_beats = Signal(32, name="fillBurstBeats")
        fill_stall_cycles = Signal(32, name="fillStallCycles")
        req_count = Signal(32, name="reqCount")
        req_forward_step_count = Signal(32, name="reqForwardStepCount")
        req_backward_step_count = Signal(32, name="reqBackwardStepCount")
        req_same_word_count = Signal(32, name="reqSameWordCount")
        req_same_line_count = Signal(32, name="reqSameLineCount")
        req_other_count = Signal(32, name="reqOtherCount")
        single_beat_burst_count = Signal(32, name="singleBeatBurstCount")
        multi_beat_burst_count = Signal(32, name="multiBeatBurstCount")
        max_occupancy = Signal(8, name="maxOccupancy")
        prev_req_valid = Signal(name="prevReqValid")
        prev_req_addr = Signal(aw, name="prevReqAddr")

        # `reqSameLineCount := reqSameLineCount` -- declared, never advanced.
        m.d.sync += req_same_line_count.eq(req_same_line_count)

        # -- internalMem and the optional splitter chain (lines 95-106) -------
        if self.need_burst_splitter:
            internal_mem = MemBus(self.internal_params, path=("internalMem",))
            burst_splitter = AlignedSplitter(self.internal_params,
                                             1 << c.mem_burst_length_width)
            context_remover = ContextRemover(plane_reader_splitter_params(c),
                                             pending_max=self.issue_queue_depth)
            m.submodules.burstSplitter = burst_splitter
            m.submodules.contextRemover = context_remover
            connect_membus(m, burst_splitter.input, internal_mem)
            connect_membus(m, context_remover.input, burst_splitter.output)
            connect_membus(m, self.mem, context_remover.output)
        else:
            internal_mem = self.mem            # `internalMem <> io.mem`

        prefetch_req = self.prefetch_req
        read_req = self.read_req
        read_addr = read_req.payload.address

        # -- incoming span, fillMisses (lines 156-159) -----------------------
        incoming_span = self.make_span(m, prefetch_req.payload)
        with m.If(fire(prefetch_req)):
            m.d.sync += fill_misses.eq(fill_misses + 1)

        # -- consume tracking (lines 161-203) --------------------------------
        consume_active = Signal(name="consumeActive")
        consume_expected_addr = Signal(aw, name="consumeExpectedAddr")
        consume_remaining_lanes = Signal(lw, name="consumeRemainingLanes")
        consume_boot = Signal(name="consumeBoot")
        consume_current_addr = Signal(aw, name="consumeCurrentAddr")
        consume_current_remaining = Signal(lw, name="consumeCurrentRemaining")
        consume_expected_valid = Signal(name="consumeExpectedValid")
        consume_addr_matches = Signal(name="consumeAddrMatches")
        consume_addr_ahead = Signal(name="consumeAddrAhead")
        consume_invariant_ok = Signal(name="consumeInvariantOk")

        m.d.comb += [
            consume_boot.eq(~consume_active & consume_queue.pop.valid),
            consume_current_addr.eq(consume_expected_addr),
            consume_current_remaining.eq(consume_remaining_lanes),
        ]
        with m.If(~consume_active):
            m.d.comb += [
                consume_current_addr.eq(consume_queue.pop.payload.start_address),
                consume_current_remaining.eq(consume_queue.pop.payload.lane_count),
            ]
        m.d.comb += [
            consume_expected_valid.eq(consume_active | consume_boot),
            consume_addr_matches.eq(read_addr == consume_current_addr),
            consume_addr_ahead.eq(read_addr > consume_current_addr),
            consume_invariant_ok.eq(~read_req.valid
                                    | (consume_expected_valid & consume_addr_matches)),
        ]

        m.d.comb += [
            span_issue_queue.push.valid.eq(prefetch_req.valid & consume_queue.push.ready),
            span_issue_queue.push.payload.eq(incoming_span),
            consume_queue.push.valid.eq(prefetch_req.valid & span_issue_queue.push.ready),
            consume_queue.push.payload.eq(incoming_span),
            prefetch_req.ready.eq(span_issue_queue.push.ready & consume_queue.push.ready),
            consume_queue.pop.ready.eq(0),
        ]

        # -- active fill (lines 204-213) -------------------------------------
        active_fill_valid = Signal(name="activeFillValid")
        active_fill_span = issued_span_queue.pop.payload
        active_fill_word_index = Signal(lw, name="activeFillWordIndex")
        m.d.comb += active_fill_valid.eq(issued_span_queue.pop.valid)

        pending_lane_valid = Signal(name="pendingLaneValid")
        pending_lane_data = Signal(16, name="pendingLaneData")

        # -- command issue (lines 215-251) -----------------------------------
        direct_miss_needed = Signal(name="directMissNeeded")
        direct_miss_cmd_valid = Signal(name="directMissCmdValid")
        prefetch_cmd_valid = Signal(name="prefetchCmdValid")
        direct_miss_cmd_selected = Signal(name="directMissCmdSelected")
        prefetch_cmd_selected = Signal(name="prefetchCmdSelected")
        m.d.comb += [
            direct_miss_needed.eq(~consume_expected_valid
                                  | (consume_expected_valid & ~consume_addr_matches
                                     & ~consume_addr_ahead)),
            direct_miss_cmd_valid.eq(read_req.valid & direct_miss_needed
                                     & direct_miss_lane_queue.push.ready),
            prefetch_cmd_valid.eq(span_issue_queue.pop.valid & issued_span_queue.push.ready),
            direct_miss_cmd_selected.eq(direct_miss_cmd_valid),
            prefetch_cmd_selected.eq(prefetch_cmd_valid & ~direct_miss_cmd_selected),
        ]

        cmd = internal_mem.cmd
        cmd_frag = cmd.payload.fragment
        issue_span = span_issue_queue.pop.payload
        length_width = len(cmd_frag.length)
        span_length = (issue_span.last_word_address - issue_span.first_word_address)[:aw]
        span_length = (span_length + Const(3, aw))[:aw]         # UInt + Int: aw bits
        cmd_fire = Signal(name="internalMem_cmd_fire")
        m.d.comb += [
            cmd.valid.eq(direct_miss_cmd_selected | prefetch_cmd_selected),
            cmd_frag.address.eq(Mux(direct_miss_cmd_selected,
                                    aligned_word_address(read_addr),
                                    issue_span.first_word_address)),
            cmd_frag.opcode.eq(CMD_READ),
            cmd_frag.length.eq(Mux(direct_miss_cmd_selected,
                                   Const(3, length_width),
                                   _resize(span_length, length_width))),
            cmd_frag.source.eq(direct_miss_cmd_selected),
            cmd_frag.data.eq(0),
            cmd_frag.mask.eq(0),
            cmd.payload.last.eq(1),
            cmd_fire.eq(fire(cmd)),
            span_issue_queue.pop.ready.eq(cmd.ready & issued_span_queue.push.ready
                                          & ~direct_miss_cmd_selected),
            issued_span_queue.push.valid.eq(cmd_fire & prefetch_cmd_selected),
            issued_span_queue.push.payload.eq(issue_span),
            direct_miss_lane_queue.push.valid.eq(cmd_fire & direct_miss_cmd_selected),
            direct_miss_lane_queue.push.payload.eq(read_addr[1]),
            issued_span_queue.pop.ready.eq(0),
        ]
        with m.If(cmd_fire & prefetch_cmd_selected):
            m.d.sync += [
                fill_burst_count.eq(fill_burst_count + 1),
                fill_burst_beats.eq(fill_burst_beats + _resize(issue_span.word_count, 32)),
            ]
            with m.If(issue_span.word_count == 1):
                m.d.sync += single_beat_burst_count.eq(single_beat_burst_count + 1)
            with m.Else():
                m.d.sync += multi_beat_burst_count.eq(multi_beat_burst_count + 1)

        # -- fill responses (lines 253-290) ----------------------------------
        rsp = internal_mem.rsp
        rsp_frag = rsp.payload.fragment
        current_fill_word_address = Signal(aw, name="currentFillWordAddress")
        emit_lo = Signal(name="emitLo")
        emit_hi = Signal(name="emitHi")
        read_data_lo = rsp_frag.data[0:16]
        read_data_hi = rsp_frag.data[16:32]
        prefetch_mem_rsp_valid = Signal(name="prefetchMemRspValid")
        direct_miss_mem_rsp_valid = Signal(name="directMissMemRspValid")
        can_accept_mem_rsp = Signal(name="canAcceptMemRsp")
        rsp_fire = Signal(name="internalMem_rsp_fire")
        m.d.comb += [
            current_fill_word_address.eq(
                (active_fill_span.first_word_address
                 + Cat(Const(0, 2), active_fill_word_index))[:aw]),
            emit_lo.eq((current_fill_word_address != active_fill_span.first_word_address)
                       | ~active_fill_span.start_address[1]),
            emit_hi.eq((current_fill_word_address != active_fill_span.last_word_address)
                       | active_fill_span.end_address[1]),
            prefetch_mem_rsp_valid.eq(rsp.valid & (rsp_frag.source == 0)),
            direct_miss_mem_rsp_valid.eq(rsp.valid & (rsp_frag.source == 1)),
            can_accept_mem_rsp.eq(active_fill_valid & ~pending_lane_valid
                                  & lane_fifo.push.ready),
            rsp_fire.eq(fire(rsp)),
        ]

        m.d.comb += [
            rsp.ready.eq((can_accept_mem_rsp & (rsp_frag.source == 0))
                         | (direct_miss_mem_rsp_valid & direct_miss_lane_queue.pop.valid
                            & read_rsp_fifo.push.ready)),
            lane_fifo.push.valid.eq(pending_lane_valid
                                    | (prefetch_mem_rsp_valid & active_fill_valid & emit_lo)),
            lane_fifo.push.payload.eq(Mux(pending_lane_valid, pending_lane_data, read_data_lo)),
        ]

        with m.If(pending_lane_valid & lane_fifo.push.ready):
            m.d.sync += pending_lane_valid.eq(0)

        with m.If(rsp_fire & (rsp_frag.source == 0)):
            with m.If(~emit_lo & emit_hi):
                m.d.comb += [
                    lane_fifo.push.valid.eq(1),
                    lane_fifo.push.payload.eq(read_data_hi),
                ]
            with m.Elif(emit_lo & emit_hi):
                m.d.sync += [
                    pending_lane_valid.eq(1),
                    pending_lane_data.eq(read_data_hi),
                ]
            # `rsp.last` may mark the end of a splitter fragment rather than
            # the end of the original fill span: retire on the word count.
            with m.If(active_fill_word_index == (active_fill_span.word_count - 1)[:lw]):
                m.d.comb += issued_span_queue.pop.ready.eq(1)
                m.d.sync += active_fill_word_index.eq(0)
            with m.Else():
                m.d.sync += active_fill_word_index.eq(active_fill_word_index + 1)

        # -- read serving (lines 292-321) ------------------------------------
        direct_miss_outstanding = Signal(name="directMissOutstanding")
        read_can_serve = Signal(name="readCanServe")
        skip_cached_lane = Signal(name="skipCachedLane")
        direct_miss_read_ready = Signal(name="directMissReadReady")
        cached_read_fire = Signal(name="cachedReadFire")
        direct_miss_rsp_fire = Signal(name="directMissRspFire")
        m.d.comb += [
            direct_miss_outstanding.eq(direct_miss_lane_queue.occupancy != 0),
            read_can_serve.eq(lane_fifo.pop.valid & consume_expected_valid
                              & consume_addr_matches & ~direct_miss_outstanding),
            skip_cached_lane.eq(read_req.valid & consume_expected_valid
                                & ~consume_addr_matches & consume_addr_ahead
                                & lane_fifo.pop.valid),
            direct_miss_read_ready.eq(direct_miss_cmd_selected & cmd.ready),
            cached_read_fire.eq(read_req.valid & read_rsp_fifo.push.ready & read_can_serve),
            direct_miss_rsp_fire.eq(direct_miss_mem_rsp_valid
                                    & direct_miss_lane_queue.pop.valid
                                    & read_rsp_fifo.push.ready),
            read_req.ready.eq((read_rsp_fifo.push.ready & read_can_serve)
                              | direct_miss_read_ready),
            read_rsp_fifo.push.valid.eq(cached_read_fire | direct_miss_rsp_fire),
            read_rsp_fifo.push.payload.data.eq(
                Mux(direct_miss_rsp_fire,
                    Mux(direct_miss_lane_queue.pop.payload, read_data_hi, read_data_lo),
                    lane_fifo.pop.payload)),
            lane_fifo.pop.ready.eq(cached_read_fire | skip_cached_lane),
            direct_miss_lane_queue.pop.ready.eq(direct_miss_rsp_fire),
        ]

        # `advanceConsume(!consumeActive)`
        with m.If(cached_read_fire | skip_cached_lane):
            with m.If(consume_current_remaining == 1):
                m.d.sync += [
                    consume_active.eq(0),
                    consume_expected_addr.eq(0),
                    consume_remaining_lanes.eq(0),
                ]
            with m.Else():
                m.d.sync += [
                    consume_active.eq(1),
                    consume_expected_addr.eq((consume_current_addr + Const(2, aw))[:aw]),
                    consume_remaining_lanes.eq(consume_current_remaining - 1),
                ]
            m.d.comb += consume_queue.pop.ready.eq(~consume_active)

        with m.If(read_req.valid & ~read_req.ready):
            m.d.sync += fill_stall_cycles.eq(fill_stall_cycles + 1)

        # `recordReadRequest(io.readReq.address)` + fillHits
        with m.If(fire(read_req)):
            m.d.sync += req_count.eq(req_count + 1)
            with m.If(prev_req_valid):
                with m.Switch(self.classify_req_step(read_addr, prev_req_addr)):
                    with m.Case(0):
                        m.d.sync += req_same_word_count.eq(req_same_word_count + 1)
                    with m.Case(1):
                        m.d.sync += req_forward_step_count.eq(req_forward_step_count + 1)
                    with m.Case(2):
                        m.d.sync += req_backward_step_count.eq(req_backward_step_count + 1)
                    with m.Default():
                        m.d.sync += req_other_count.eq(req_other_count + 1)
            m.d.sync += [
                prev_req_valid.eq(1),
                prev_req_addr.eq(read_addr),
                fill_hits.eq(fill_hits + 1),
            ]

        # (Scala lines 323-343: GenerationFlags.formal asserts -- not RTL.)

        # -- cacheDebug (lines 345-398) --------------------------------------
        cache_debug = Signal(64, name="cacheDebug")
        cache_debug_occupancy = Signal(32, name="cacheDebugOccupancy")
        m.d.comb += [
            cache_debug.eq(0),
            cache_debug[0].eq(read_req.valid),
            cache_debug[1].eq(read_req.ready),
            cache_debug[2].eq(consume_expected_valid),
            cache_debug[3].eq(consume_addr_matches),
            cache_debug[4].eq(consume_invariant_ok),
            cache_debug[5].eq(lane_fifo.pop.valid),
            cache_debug[6].eq(read_rsp_fifo.push.ready),
            cache_debug[7].eq(fire(prefetch_req)),
            cache_debug[8].eq(cmd_fire),
            cache_debug[9].eq(rsp_fire),
            cache_debug[10].eq(consume_active),
            cache_debug[11].eq(consume_boot),
            cache_debug[12].eq(span_issue_queue.pop.valid),
            cache_debug[13].eq(issued_span_queue.pop.valid),
            cache_debug[14].eq(pending_lane_valid),
            cache_debug[15].eq(skip_cached_lane),
            cache_debug[16].eq(direct_miss_cmd_valid),
            cache_debug[17].eq(direct_miss_cmd_selected),
            cache_debug[18].eq(direct_miss_read_ready),
            cache_debug[19].eq(direct_miss_outstanding),
            cache_debug[20].eq(direct_miss_lane_queue.pop.valid),
            cache_debug[21].eq(direct_miss_mem_rsp_valid),
            cache_debug[22].eq(lane_fifo.pop.ready),
            self.cache_debug.eq(cache_debug),
            self.cache_debug_read_addr.eq(read_addr),
            self.cache_debug_expected_addr.eq(consume_current_addr),
            self.cache_debug_remaining.eq(consume_current_remaining),
            cache_debug_occupancy.eq(0),
            cache_debug_occupancy[0:7].eq(_resize(consume_queue.occupancy, 7)),
            cache_debug_occupancy[8:16].eq(_resize(lane_fifo.occupancy, 8)),
            cache_debug_occupancy[16:19].eq(_resize(issued_span_queue.occupancy, 3)),
            cache_debug_occupancy[19:24].eq(_resize(read_rsp_fifo.occupancy, 5)),
            cache_debug_occupancy[24:28].eq(_resize(direct_miss_lane_queue.occupancy, 4)),
            self.cache_debug_occupancy.eq(cache_debug_occupancy),
        ]

        # -- maxOccupancy (lines 400-402): note the resize(8) TRUNCATES the
        # 10-bit laneFifo occupancy before the compare, as the Scala does.
        lane_occupancy8 = _resize(lane_fifo.occupancy, 8)
        with m.If(lane_occupancy8 > max_occupancy):
            m.d.sync += max_occupancy.eq(lane_occupancy8)

        # -- outputs (lines 404-422) -----------------------------------------
        connect_stream(m, self.read_rsp, read_rsp_fifo.pop)
        m.d.comb += [
            self.busy.eq((span_issue_queue.occupancy != 0)
                         | (issued_span_queue.occupancy != 0)
                         | pending_lane_valid
                         | (lane_fifo.occupancy != 0)
                         | (read_rsp_fifo.occupancy != 0)),
            self.fill_hits.eq(fill_hits),
            self.fill_misses.eq(fill_misses),
            self.fill_burst_count.eq(fill_burst_count),
            self.fill_burst_beats.eq(fill_burst_beats),
            self.fill_stall_cycles.eq(fill_stall_cycles),
            self.req_count.eq(req_count),
            self.req_forward_step_count.eq(req_forward_step_count),
            self.req_backward_step_count.eq(req_backward_step_count),
            self.req_same_word_count.eq(req_same_word_count),
            self.req_same_line_count.eq(req_same_line_count),
            self.req_other_count.eq(req_other_count),
            self.single_beat_burst_count.eq(single_beat_burst_count),
            self.multi_beat_burst_count.eq(multi_beat_burst_count),
            self.max_occupancy.eq(max_occupancy),
        ]
        return m
