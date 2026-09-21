"""``TmuNoTextureCache`` -- the TMU's uncached direct-read texel path.

1:1 port of ``case class TmuNoTextureCache`` in
``ext/SpinalVoodoo/src/voodoo/texture/TmuTextureCache.scala`` (l. 865-996,
SpinalHDL 1.15.0), the ``TmuTexturePathBase`` variant ``Tmu.scala:597`` builds
when ``!c.useTexFillCache``.  That is what the Tang Console builds
(``fpga/src/voodoo/gowin/CoreGowinGen.scala:73``: ``useTexFillCache = false,
texFillRequestWindow = 2``); ``Config.voodoo1()`` and ``CoreSim`` build the
cache (``tmu_texture_cache.py``) instead.  Same ``TmuTexturePathIo`` ports as
the cache, so ``Tmu`` swaps the two on the config flag alone.

What it does
------------

Every sample request is turned into one 4-byte texture-memory read per
*distinct word* among its active taps (one tap for a point sample, four for
bilinear), the responses are reassembled into a ``SampleFetch`` and handed
on.  No state survives a request: no hits, no lines, no epoch --
``io.invalidate`` is unused (it stays on the port for the shared io bundle).

Structure map (Scala names, kept as Amaranth signal names)
----------------------------------------------------------

::

   io.sampleRequest --queue(c.texFillRequestWindow)--> reqStream
                                                          |
     expanded      running / hold / pending: the request being expanded.
                   req = running ? hold : reqStream.payload
                   tapAddrs[4] (tap 0 = biAddr0 when bilinear, else pointAddr)
                   tapWordAddrs[i] = tapAddrs[i] & ~3
                   activePending = running ? pending : (bilinear ? 1111 : 0001)
                   currentIdx = pendingFirst(activePending)   -- lowest set bit
                   currentWord = tapWordAddrs[currentIdx]
                   currentMask[i] = activePending[i] & (tapWordAddrs[i] == currentWord)
                   nextPending[i] = activePending[i] & (tapWordAddrs[i] != currentWord)
                   stream (DirectWordReq): address = currentWord, firstWord = !running,
                     lastWord = nextPending == 0, tapMask = currentMask, tapHalf/tapByte
                     = address bits [1] / [0] of every tap, is16Bit / bilinear /
                     passthrough of req
                   reqStream.ready = !running & stream.ready
                   on stream.fire: !running & !lastWord -> hold, pending, running := 1
                                   running & !lastWord  -> pending := nextPending
                                   running & lastWord   -> running := 0, pending := 0
     StreamFork2(expanded.stream)      (asynchronous fork, one linkEnable per branch)
        toMem -> io.texRead.cmd   READ, length 3 (one word), source 0, last
        toQ   -> queued = toQ.queue(16)
     rsp = io.texRead.rsp.takeWhen(last).translateWith(data)
     raw = StreamJoin(rsp, queued);  raw.ready = !outValid
        on raw.fire: firstWord -> assembled.bilinear/passthrough := meta, texels := 0
                     tapMask[i] -> assembled.texels[i] := texel16(data, tapHalf[i],
                                                                  tapByte[i], is16Bit)
                     lastWord  -> outValid := 1
     io.sampleFetch: valid = outValid, payload = assembled; fire -> outValid := 0
     io.busy = reqStream.valid | running | queued.valid | outValid

Behaviour worth knowing (all reproduced 1:1, pinned by the lockstep diff and
``tests/test_tmu_no_texture_cache.py``)
------------------------------------------------------------------------

* **One outstanding response at a time.**  ``raw.ready = !outValid`` and
  ``outValid`` only clears on ``sampleFetch.fire``, so while the consumer
  holds a response the join stalls: the 16-deep ``queued`` FIFO and the
  memory's response stream back up behind it.  Throughput is bounded by the
  ``sampleFetch`` consumer plus one cycle per word, not by memory latency.
* **Only the ``last`` beat of a response burst is used**; every command asks
  for ``length = 3`` (one word) so a well-formed slave answers each with
  exactly one beat, and ``takeWhen(last)`` silently drops any earlier beats
  a longer burst would carry.
* **Taps sharing a word are served by one read** (``currentMask`` has several
  bits); taps in different words go out as separate commands in ascending
  tap order, and ``firstWord`` (``!running``) zeroes the texels before the
  first word's taps are written, so a bilinear fetch carries 0 in the taps
  it did not resolve (there are none unless the slave misbehaves).
* ``io.busy`` drops for one cycle after a push into the empty request FIFO
  (Spinal ``StreamFifo`` latency 2), as the cache's does.

Registers: Spinal ``Reg(...)`` without ``init`` is ``reset_less=True`` here
(plan §6): ``hold`` and ``assembled``.  ``running``, ``pending`` and
``outValid`` are ``RegInit`` / ``init 0`` and reset.  The two FIFOs are
:class:`avoodoo.lib.stream.Fifo`; both qualify for the Spinal build's
``force_distributed_fifo_ram`` rule (``spinal_fifo_ram_attrs``), so the
Gowin build spends no BSRAM on them.

Dropped: the ``GenerationFlags.formal`` block (READ / ``last`` / ``length
== 3`` asserts on the command -- checked per cycle by the unit tests).
"""

from __future__ import annotations

from amaranth import Array, Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.membus_params import tmu_bmb_params
from avoodoo.lib.membus import CMD_READ, MemBusSignature
from avoodoo.lib.stream import (Stream, fire, fork2, join, queue, spinal_fifo_ram_attrs,
                                take_when, translate_with)
from avoodoo.texture.tmu_texture_cache import texel16
from avoodoo.texture.tmu_types import SampleFetch, SampleRequest, TmuPassthrough

__all__ = [
    "TmuTextureCacheDirectWordReq",
    "TmuNoTextureCache",
    "DIRECT_READ_LENGTH",
    "DIRECT_QUEUE_DEPTH",
]

TAP_COUNT = 4

#: ``cmd.fragment.length := 3``: one 32-bit word per read.
DIRECT_READ_LENGTH = 3
#: ``toQ.queue(16)``: word requests in flight to memory (a literal in the Scala).
DIRECT_QUEUE_DEPTH = 16


class TmuTextureCacheDirectWordReq(data.StructLayout):
    """``case class TmuTextureCacheDirectWordReq(c)`` -- one word read plus which taps it serves."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "address": unsigned(c.address_width),
            "is16_bit": unsigned(1),
            "first_word": unsigned(1),
            "last_word": unsigned(1),
            "bilinear": unsigned(1),
            "tap_mask": unsigned(TAP_COUNT),
            "tap_half": data.ArrayLayout(unsigned(1), TAP_COUNT),
            "tap_byte": data.ArrayLayout(unsigned(1), TAP_COUNT),
            "passthrough": TmuPassthrough(c),
        })


def _pending_first(m, mask, fallback, *, name):
    """``pendingFirst(mask, fallback)``: lowest set bit's index, else ``fallback``.

    Scala: ``idx := fallback.resized; when(mask(0)) 0 elsewhen(mask(1)) 1 ...``.
    """
    idx = Signal(2, name=name)
    m.d.comb += idx.eq(fallback)
    with m.If(mask[0]):
        m.d.comb += idx.eq(0)
    with m.Elif(mask[1]):
        m.d.comb += idx.eq(1)
    with m.Elif(mask[2]):
        m.d.comb += idx.eq(2)
    with m.Elif(mask[3]):
        m.d.comb += idx.eq(3)
    return idx


class TmuNoTextureCache(wiring.Component):
    """``case class TmuNoTextureCache(c) extends TmuTexturePathBase(c)``.

    Ports are ``TmuTexturePathIo``, identical to :class:`TmuTextureCache`:
    ``sample_request`` (slave stream of ``Tmu.SampleRequest``),
    ``sample_fetch`` (master stream of ``Tmu.SampleFetch``), ``tex_read``
    (``Bmb(Tmu.bmbParams(c))`` master), ``invalidate`` in (unused here),
    ``busy`` out.
    """

    def __init__(self, c: Config | None = None) -> None:
        self.c = c if c is not None else Config.voodoo1()
        if self.c.use_tex_fill_cache:
            raise ValueError("TmuNoTextureCache requires !c.useTexFillCache "
                             "(Tmu.scala:597 builds TmuTextureCache otherwise)")
        if self.c.tex_fill_request_window < 0:
            raise ValueError("texFillRequestWindow must be >= 0")
        self.bmb_params = tmu_bmb_params(self.c)
        super().__init__({
            "sample_request": In(stream.Signature(SampleRequest(self.c))),
            "sample_fetch": Out(stream.Signature(SampleFetch(self.c))),
            "tex_read": Out(MemBusSignature(self.bmb_params)),
            "invalidate": In(1),
            "busy": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        aw = c.address_width
        word_req_l = TmuTextureCacheDirectWordReq(c)

        # ---- val reqStream = io.sampleRequest.queue(c.texFillRequestWindow) ----
        window = c.tex_fill_request_window
        req_stream = queue(m, self.sample_request, window,
                           mem_attrs=spinal_fifo_ram_attrs(SampleRequest(c).size, window),
                           name="io_sampleRequest_fifo")

        # ==================================================================
        # expanded: one request -> one word read per distinct tap word
        # ==================================================================
        expanded_stream = Stream(word_req_l, path=("expanded_stream",))
        running = Signal(name="expanded_running")
        hold = Signal(SampleRequest(c), name="expanded_hold", reset_less=True)
        pending = Signal(TAP_COUNT, name="expanded_pending")

        #   val req = running ? hold | reqStream.payload
        req = Signal(SampleRequest(c), name="expanded_req")
        m.d.comb += req.eq(Mux(running, hold, req_stream.payload))
        #   val initPending = Mux(reqStream.payload.bilinear, B"1111", B"0001")
        init_pending = Signal(TAP_COUNT, name="expanded_initPending")
        m.d.comb += init_pending.eq(Mux(req_stream.payload.bilinear,
                                        Const(0b1111, TAP_COUNT), Const(0b0001, TAP_COUNT)))
        active_pending = Signal(TAP_COUNT, name="expanded_activePending")
        m.d.comb += active_pending.eq(Mux(running, pending, init_pending))

        #   tapAddrs = Vec(pointAddr, biAddr1, biAddr2, biAddr3); when(bilinear) tapAddrs(0) := biAddr0
        tap_addrs = [Signal(aw, name=f"expanded_tapAddrs_{i}") for i in range(TAP_COUNT)]
        m.d.comb += [
            tap_addrs[0].eq(Mux(req.bilinear, req.bi_addr0, req.point_addr)),
            tap_addrs[1].eq(req.bi_addr1),
            tap_addrs[2].eq(req.bi_addr2),
            tap_addrs[3].eq(req.bi_addr3),
        ]
        #   tapWordAddrs(i) := ((tapAddrs(i) >> 2) << 2).resize(aw)
        tap_word_addrs = [Signal(aw, name=f"expanded_tapWordAddrs_{i}")
                          for i in range(TAP_COUNT)]
        for i in range(TAP_COUNT):
            m.d.comb += [
                tap_word_addrs[i].eq(Cat(Const(0, 2), tap_addrs[i][2:])),
                expanded_stream.payload.tap_half[i].eq(tap_addrs[i][1]),
                expanded_stream.payload.tap_byte[i].eq(tap_addrs[i][0]),
            ]

        current_idx = _pending_first(m, active_pending, Const(0, 2), name="expanded_currentIdx")
        current_word = Signal(aw, name="expanded_currentWord")
        m.d.comb += current_word.eq(Array(tap_word_addrs)[current_idx])
        current_mask = Signal(TAP_COUNT, name="expanded_currentMask")
        next_pending = Signal(TAP_COUNT, name="expanded_nextPending")
        for i in range(TAP_COUNT):
            same = tap_word_addrs[i] == current_word
            m.d.comb += [
                current_mask[i].eq(active_pending[i] & same),
                next_pending[i].eq(active_pending[i] & ~same),
            ]

        m.d.comb += [
            expanded_stream.valid.eq(running | req_stream.valid),
            expanded_stream.payload.address.eq(current_word),
            expanded_stream.payload.is16_bit.eq(req.is16_bit),
            expanded_stream.payload.first_word.eq(~running),
            expanded_stream.payload.last_word.eq(next_pending == 0),
            expanded_stream.payload.bilinear.eq(req.bilinear),
            expanded_stream.payload.tap_mask.eq(current_mask),
            expanded_stream.payload.passthrough.eq(req.passthrough),
            req_stream.ready.eq(~running & expanded_stream.ready),
        ]

        last_word = expanded_stream.payload.last_word
        with m.If(fire(expanded_stream)):
            with m.If(~running & ~last_word):
                m.d.sync += [
                    hold.eq(req_stream.payload),
                    pending.eq(next_pending),
                    running.eq(1),
                ]
            with m.Elif(running & ~last_word):
                m.d.sync += pending.eq(next_pending)
            with m.Elif(running):
                m.d.sync += [running.eq(0), pending.eq(0)]

        # ==================================================================
        # val (toMem, toQ) = StreamFork2(expanded.stream)
        # ==================================================================
        to_mem, to_q = fork2(m, expanded_stream, name="expanded_stream_fork")

        #   io.texRead.cmd << toMem.translateWith { READ, address, length 3, source 0, last }
        cmd = self.tex_read.cmd
        m.d.comb += [
            cmd.valid.eq(to_mem.valid),
            to_mem.ready.eq(cmd.ready),
            cmd.payload.fragment.address.eq(to_mem.payload.address),
            cmd.payload.fragment.opcode.eq(CMD_READ),
            cmd.payload.fragment.length.eq(Const(DIRECT_READ_LENGTH, c.mem_burst_length_width)),
            cmd.payload.last.eq(1),
        ]
        if "source" in cmd.payload.fragment.shape().members:
            m.d.comb += cmd.payload.fragment.source.eq(0)

        #   val queued = toQ.queue(16)
        queued = queue(m, to_q, DIRECT_QUEUE_DEPTH,
                       mem_attrs=spinal_fifo_ram_attrs(word_req_l.size, DIRECT_QUEUE_DEPTH),
                       name="toQ_fifo")

        # ==================================================================
        # rsp / raw / assembled
        # ==================================================================
        #   val rsp = io.texRead.rsp.takeWhen(io.texRead.rsp.last).translateWith(rsp.fragment.data)
        rsp_last = take_when(m, self.tex_read.rsp, self.tex_read.rsp.payload.last)
        rsp = translate_with(m, rsp_last, self.tex_read.rsp.payload.fragment.data)
        #   val raw = StreamJoin(rsp, queued)
        raw = join(m, rsp, queued, name="raw_join")

        assembled = Signal(SampleFetch(c), name="assembled", reset_less=True)
        out_valid = Signal(name="outValid")

        m.d.comb += raw.ready.eq(~out_valid)

        with m.If(fire(raw)):
            word = raw.payload["_1"]
            meta = raw.payload["_2"]
            with m.If(meta.first_word):
                m.d.sync += [
                    assembled.bilinear.eq(meta.bilinear),
                    assembled.passthrough.eq(meta.passthrough),
                ]
                m.d.sync += [assembled.texels[i].eq(0) for i in range(TAP_COUNT)]
            for i in range(TAP_COUNT):
                with m.If(meta.tap_mask[i]):
                    m.d.sync += assembled.texels[i].eq(
                        texel16(word, meta.tap_half[i], meta.tap_byte[i], meta.is16_bit))
            with m.If(meta.last_word):
                m.d.sync += out_valid.eq(1)

        m.d.comb += [
            self.sample_fetch.valid.eq(out_valid),
            self.sample_fetch.payload.eq(assembled),
        ]
        with m.If(fire(self.sample_fetch)):
            m.d.sync += out_valid.eq(0)

        #   io.busy := reqStream.valid || expanded.running || queued.valid || outValid
        m.d.comb += self.busy.eq(req_stream.valid | running | queued.valid | out_valid)

        # Handy handles for tests / waveform probes.
        self.req_stream = req_stream
        self.expanded_stream = expanded_stream
        self.running = running
        self.pending = pending
        self.queued = queued
        self.out_valid = out_valid
        return m
