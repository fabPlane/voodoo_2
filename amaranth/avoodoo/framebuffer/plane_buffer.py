"""``FramebufferPlaneBuffer`` -- the framebuffer write path: direct queue or write combiner.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/framebuffer/FramebufferPlaneBuffer.scala``
(592 lines, SpinalHDL 1.15.0; plan Phase 6 "plane_buffer").  Instantiated by
``FramebufferMemSubsystem.scala:86`` (once per plane: colour and aux) with
``FramebufferPlaneBuffer(c)``.  The companion object's bundles live in
:mod:`avoodoo.core.core_types` (``FramebufferWriteReq`` / ``FramebufferReadReq``
/ ``FramebufferReadRsp``, re-exported here under the Scala names) and its
``bmbParams`` is :func:`avoodoo.core.membus_params.plane_buffer_params`.

Despite the name it never reads: ``readReq.ready``, ``readRsp.valid`` and the
five ``fill*`` counters are constant zero in both branches, left over from an
earlier design that also cached reads.  The read side of the framebuffer is
``FramebufferPlaneReader`` / ``FramebufferPlaneDirectReader``.

Two elaborations, chosen by ``c.use_fb_write_buffer``
-----------------------------------------------------

``False`` (``Config.voodoo1()``, what ``CoreSim`` and the Console
``TangConsole138k`` build): ``writeReq.m2sPipe()`` into an 8-deep
``StreamFifo``, popped straight onto the bus as single-beat writes
(``length = 3``, ``source = 0``, ``last``).  Only ``writeStallCycles`` and
``busy`` are live.

``True`` (``CoreGowin`` Dock/Mega and ``CoreArtix``, ``fbWriteBufferLineWords
= 256``): a two-slot write combiner.  A *slot* is one physical line
(``runDepth`` words): the newest word sits in the slot's ``pending``
registers so a same-address write can byte-merge into it, and every earlier
word of the run has been pushed into the slot's ``StreamFifo``
(``runDepth - 1`` deep, so FIFO + pending = one full line).  Per accepted
write, in priority order:

* ``!activeValid``: open the active slot at this address;
* ``sameWordMerge``: byte-merge into ``pending``;
* ``sequentialAppend`` (address == start + count words, count < runDepth, and
  the slot FIFO can take a beat): push ``pending`` into the FIFO, this word
  becomes ``pending``;
* otherwise ``needsRotate``: only when ``canRotate`` (drain idle, other slot
  empty) -- the active slot starts draining and the write opens the other slot.

Drains are single Bmb write bursts with ``source = 1``, one beat per dirty
word, address incremented per beat (membus note 6), ``length = words*4 - 1``,
the FIFO supplying every beat but the last, which comes from ``pending`` and
carries ``last``.  Three things start one: a full line whose pending word is
byte-complete while nothing is arriving (reason 0 "full"), a rotate (reason 1)
and ``io.flush`` on a valid slot while nothing is arriving (reason 2).  The
``DrainState.Rsp`` state exists but is never entered -- ``Send`` returns to
``Idle`` on the last beat -- so a write response is accepted (``rsp.ready =
source == 1``) whenever it shows up.

``directWriteQueue`` is instantiated in **both** branches: the Scala declares
it before the ``if (c.useFbWriteBuffer)`` that decides whether anything is
ever pushed into it, so the combiner carries an 8-deep FIFO that is never
written and contributes a constant-false ``occupancy =/= 0`` to ``busy``.
Spinal keeps it (``streamFifo_3`` in ``FramebufferPlaneBufferWb_spinal.v``),
so this does too; a synthesiser removes it either way.

Registers and reset
-------------------

Every register in the Scala carries ``init``, so every ``Signal`` here resets;
the only reset-less state is inside ``avoodoo.lib.stream`` (the ``m2sPipe``
payload register and the FIFO RAMs), and that matches Spinal.  Vec-indexed
registers are ``Array``s of two ``Signal``s written through a dynamic index,
which lowers to the same per-element ``if (slot == i)`` the Spinal Verilog
shows.  Statement order follows the Scala because both languages resolve
multiple assignments in one cycle by last-wins: the ``Send``-state
``clearSlotMeta(drainSlot)`` comes first, the full/flush drain starts second,
the write-accept block last.

Memories and ``syn_ramstyle``
-----------------------------

Every memory belongs to an ``avoodoo.lib.stream.Fifo``: ``directWriteQueue``
(8 x (addr + 36) bits, both branches) and ``slot0Fifo``/``slot1Fifo``
(``runDepth - 1`` deep: 255 for the Dock/Artix config; for ``runDepth = 2``
the depth-1 FIFO is a single ``m2sPipe`` register and there is no RAM at
all).  The Spinal Console build's
``fpga/hw/gowin/console/force_distributed_fifo_ram.py`` stamps
``syn_ramstyle = "distributed_ram"`` on RAMs with width >= 24 and depth <= 64
so wide shallow FIFOs stay out of BSRAM; every ``Fifo`` here passes
``mem_attrs=spinal_fifo_ram_attrs(width, depth)``, which is that rule
verbatim, so ``directWriteQueue`` (62 x 8 with a 26-bit address) gets the
attribute and the 255-deep slot FIFOs do not -- the same split the Spinal
bitstream has, with the threshold kept in one place (``lib/stream.py``).

Formal properties (``GenerationFlags.formal``)
----------------------------------------------

Dropped from the RTL, as the plan asks.  ``tests/test_plane_buffer.py``
checks the same invariants every cycle in pysim through the ``dbg``
attributes this component exposes after elaboration (``slot_valid``,
``slot_draining``, ``slot_word_count``, ``slot_occupancy``, ``drain_state``,
``drain_slot``, ``write_req_pipe``, ``same_word_merge``,
``sequential_append``, ``needs_rotate``, ``can_rotate``,
``active_push_ready``).  ``formal_strong`` is accepted for signature parity
and changes nothing.
"""

from __future__ import annotations

import enum
from types import SimpleNamespace

from amaranth import Array, Cat, Const, Module, Mux, Signal, Value
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (FramebufferReadReq, FramebufferReadRsp,
                                     FramebufferWriteReq)
from avoodoo.core.membus_params import plane_buffer_params
from avoodoo.lib.membus import CMD_WRITE, MemBusSignature, log2_up
from avoodoo.lib.stream import (Fifo, connect_stream, fire, m2s_pipe,
                                spinal_fifo_ram_attrs)

__all__ = [
    "ReadReq",
    "ReadRsp",
    "WriteReq",
    "DrainState",
    "DRAIN_REASON_FULL",
    "DRAIN_REASON_ROTATE",
    "DRAIN_REASON_FLUSH",
    "STAT_NAMES",
    "FramebufferPlaneBuffer",
]

#: ``FramebufferPlaneBuffer.ReadReq(c)`` / ``ReadRsp()`` / ``WriteReq(c)``.
ReadReq = FramebufferReadReq
ReadRsp = FramebufferReadRsp
WriteReq = FramebufferWriteReq

#: ``recordDrain``'s ``reason`` values (``U(n, 2 bits)`` at the call sites).
DRAIN_REASON_FULL = 0
DRAIN_REASON_ROTATE = 1
DRAIN_REASON_FLUSH = 2

#: The twenty ``out UInt(32 bits)`` statistics, in ``io`` declaration order.
STAT_NAMES = (
    "fill_hits",
    "fill_misses",
    "fill_burst_count",
    "fill_burst_beats",
    "fill_stall_cycles",
    "write_stall_cycles",
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

#: The five that are constant zero in both branches (no fill cache any more).
_FILL_STATS = STAT_NAMES[:5]


class DrainState(enum.IntEnum):
    """``object DrainState extends SpinalEnum { Idle, Send, Rsp }`` -- binary, 2 bits."""

    IDLE = 0
    SEND = 1
    RSP = 2


def _fifo(shape, depth: int) -> Fifo:
    """``StreamFifo(shape, depth)`` with the Console build's RAM-style rule applied."""
    return Fifo(shape, depth, mem_attrs=spinal_fifo_ram_attrs(shape.size, depth))


def _resize(v, width: int):
    """``UInt.resize(width bits)``: truncate or zero-extend."""
    v = Value.cast(v).as_unsigned()
    if len(v) >= width:
        return v[0:width]
    return Cat(v, Const(0, width - len(v)))


class FramebufferPlaneBuffer(wiring.Component):
    """``case class FramebufferPlaneBuffer(c: Config, formalStrong: Boolean = true)``.

    Attributes
    ----------
    read_req : In(stream.Signature(ReadReq(c)))
        ``slave Stream(ReadReq(c))`` -- never ready.
    read_rsp : Out(stream.Signature(ReadRsp()))
        ``master Stream(ReadRsp())`` -- never valid.
    write_req : In(stream.Signature(WriteReq(c)))
    flush : In(1)
    mem : Out(MemBusSignature(plane_buffer_params(c)))
        ``master(Bmb(bmbParams(c)))``: 32-bit data, 1-bit source, byte-aligned
        bursts of ``c.mem_burst_length_width`` bits.
    busy : Out(1)
    <stat> : Out(32)
        one per :data:`STAT_NAMES`.
    """

    def __init__(self, c: Config | None = None, formal_strong: bool = True):
        if c is None:
            c = Config.voodoo1()
        if c.fb_write_buffer_line_words < 2:
            raise ValueError("fbWriteBufferLineWords must be >= 2")
        if c.fb_write_buffer_count != 2:
            raise ValueError("fbWriteBufferCount must be 2")
        self.c = c
        self.formal_strong = bool(formal_strong)
        self.params = plane_buffer_params(c)
        ports = {
            "read_req": In(stream.Signature(ReadReq(c))),
            "read_rsp": Out(stream.Signature(ReadRsp())),
            "write_req": In(stream.Signature(WriteReq(c))),
            "flush": In(1),
            "mem": Out(MemBusSignature(self.params)),
            "busy": Out(1),
        }
        for name in STAT_NAMES:
            ports[name] = Out(32)
        super().__init__(ports)
        #: internal state for tests; filled by :meth:`elaborate`.
        self.dbg = SimpleNamespace()

    # ------------------------------------------------------------------
    # shared pieces
    # ------------------------------------------------------------------

    def _drive_direct_cmd(self, m, direct_write_queue):
        """The eight ``io.mem.cmd.* := directWriteQueue.io.pop.*`` lines."""
        cmd = self.mem.cmd
        frag = cmd.payload.fragment
        pop = direct_write_queue.pop
        m.d.comb += [
            cmd.valid.eq(pop.valid),
            frag.address.eq(pop.payload.address),
            frag.opcode.eq(CMD_WRITE),
            frag.length.eq(3),
            frag.source.eq(0),
            frag.data.eq(pop.payload.data),
            frag.mask.eq(pop.payload.mask),
            cmd.payload.last.eq(1),
        ]

    def _zero_stats(self, m, names):
        for name in names:
            m.d.comb += getattr(self, name).eq(0)

    # ------------------------------------------------------------------

    def elaborate(self, platform):
        m = Module()
        c = self.c

        # The read side is vestigial in both branches.
        m.d.comb += [
            self.read_req.ready.eq(0),
            self.read_rsp.valid.eq(0),
            self.read_rsp.payload.data.eq(0),
        ]

        if not c.use_fb_write_buffer:
            self._elaborate_direct(m)
        else:
            self._elaborate_combiner(m)
        return m

    # ------------------------------------------------------------------
    # if (!c.useFbWriteBuffer)
    # ------------------------------------------------------------------

    def _elaborate_direct(self, m):
        c = self.c
        write_req_pipe = m2s_pipe(m, self.write_req, name="writeReqPipe")
        direct_write_queue = _fifo(WriteReq(c), 8)
        m.submodules.directWriteQueue = direct_write_queue
        write_stall_cycles = Signal(32, name="writeStallCycles")

        self._drive_direct_cmd(m, direct_write_queue)
        m.d.comb += self.mem.rsp.ready.eq(1)

        connect_stream(m, direct_write_queue.push, write_req_pipe)
        m.d.comb += [
            direct_write_queue.flush.eq(0),
            direct_write_queue.pop.ready.eq(self.mem.cmd.ready),
        ]

        with m.If(write_req_pipe.valid & ~write_req_pipe.ready):
            m.d.sync += write_stall_cycles.eq(write_stall_cycles + 1)

        self._zero_stats(m, [n for n in STAT_NAMES if n != "write_stall_cycles"])
        m.d.comb += self.write_stall_cycles.eq(write_stall_cycles)

        m.d.comb += self.busy.eq(
            write_req_pipe.valid | (direct_write_queue.occupancy != 0))

        self.dbg.write_req_pipe = write_req_pipe
        self.dbg.direct_write_queue = direct_write_queue

    # ------------------------------------------------------------------
    # else: the two-slot write combiner
    # ------------------------------------------------------------------

    def _elaborate_combiner(self, m):
        c = self.c
        run_depth = c.fb_write_buffer_line_words
        fifo_depth = run_depth - 1
        count_width = log2_up(run_depth + 1)
        slot_count = c.fb_write_buffer_count
        slot_index_width = log2_up(slot_count)
        addr_width = c.address_width
        physical_index_width = log2_up(run_depth)
        length_width = c.mem_burst_length_width
        last_physical_word_index = Const(run_depth - 1, physical_index_width)

        cmd = self.mem.cmd
        cmd_frag = cmd.payload.fragment
        rsp = self.mem.rsp
        rsp_frag = rsp.payload.fragment

        # -- the two slot FIFOs ------------------------------------------
        slot0_fifo = _fifo(WriteReq(c), fifo_depth)
        slot1_fifo = _fifo(WriteReq(c), fifo_depth)
        m.submodules.slot0Fifo = slot0_fifo
        m.submodules.slot1Fifo = slot1_fifo
        slot_fifos = (slot0_fifo, slot1_fifo)
        slot_flush = Array(Signal(name=f"slotFlush_{i}") for i in range(slot_count))
        m.d.comb += [slot0_fifo.flush.eq(slot_flush[0]), slot1_fifo.flush.eq(slot_flush[1])]
        slot0_push, slot1_push = slot0_fifo.push, slot1_fifo.push
        slot0_pop, slot1_pop = slot0_fifo.pop, slot1_fifo.pop

        def slot_push_ready(slot):
            return Mux(slot == 0, slot0_push.ready, slot1_push.ready)

        def slot_pop_valid(slot):
            return Mux(slot == 0, slot0_pop.valid, slot1_pop.valid)

        def slot_pop_data(slot):
            return Mux(slot == 0, slot0_pop.payload.data, slot1_pop.payload.data)

        def slot_pop_mask(slot):
            return Mux(slot == 0, slot0_pop.payload.mask, slot1_pop.payload.mask)

        def drive_slot_push(slot, address, data, mask):
            with m.If(slot == 0):
                m.d.comb += [
                    slot0_push.valid.eq(1),
                    slot0_push.payload.address.eq(address),
                    slot0_push.payload.data.eq(data),
                    slot0_push.payload.mask.eq(mask),
                ]
            with m.Else():
                m.d.comb += [
                    slot1_push.valid.eq(1),
                    slot1_push.payload.address.eq(address),
                    slot1_push.payload.data.eq(data),
                    slot1_push.payload.mask.eq(mask),
                ]

        def drive_slot_pop_ready(slot):
            with m.If(slot == 0):
                m.d.comb += slot0_pop.ready.eq(1)
            with m.Else():
                m.d.comb += slot1_pop.ready.eq(1)

        def addr_plus_words(address, words):
            # (address + ((words.resize(addrWidth) << 2).resized)).resized
            return (address + (_resize(words, addr_width) << 2)[0:addr_width])[0:addr_width]

        def physical_word_index(address):
            return _resize(address >> 2, physical_index_width)

        # -- slot metadata (Vec(Reg(...) init(...), slotCount)) -----------
        def regs(name, shape, init=0):
            return Array(Signal(shape, name=f"{name}_{i}", init=init) for i in range(slot_count))

        slot_valid = regs("slotValid", 1)
        slot_draining = regs("slotDraining", 1)
        slot_start_addr = regs("slotStartAddr", addr_width)
        slot_word_count = regs("slotWordCount", count_width)
        slot_pending_address = regs("slotPendingAddress", addr_width)
        slot_pending_data = regs("slotPendingData", 32)
        slot_pending_mask = regs("slotPendingMask", 4)
        active_slot = Signal(slot_index_width, name="activeSlot")

        stats = {name: Signal(32, name=name) for name in STAT_NAMES[5:]}
        write_stall_cycles = stats["write_stall_cycles"]
        write_drain_count = stats["write_drain_count"]
        write_full_drain_count = stats["write_full_drain_count"]
        write_partial_drain_count = stats["write_partial_drain_count"]
        write_drain_reason_full_count = stats["write_drain_reason_full_count"]
        write_drain_reason_rotate_count = stats["write_drain_reason_rotate_count"]
        write_drain_reason_flush_count = stats["write_drain_reason_flush_count"]
        write_drain_dirty_word_total = stats["write_drain_dirty_word_total"]
        write_rotate_blocked_cycles = stats["write_rotate_blocked_cycles"]
        write_single_word_drain_count = stats["write_single_word_drain_count"]
        write_single_word_drain_start_at_zero_count = \
            stats["write_single_word_drain_start_at_zero_count"]
        write_single_word_drain_start_at_last_count = \
            stats["write_single_word_drain_start_at_last_count"]
        write_rotate_adjacent_line_count = stats["write_rotate_adjacent_line_count"]
        write_rotate_same_line_gap_count = stats["write_rotate_same_line_gap_count"]
        write_rotate_other_line_count = stats["write_rotate_other_line_count"]

        self._zero_stats(m, _FILL_STATS)
        for name, sig in stats.items():
            m.d.comb += getattr(self, name).eq(sig)

        drain_state = Signal(DrainState, init=DrainState.IDLE, name="drainState")
        drain_slot = Signal(slot_index_width, name="drainSlot")
        drain_index = Signal(physical_index_width, name="drainIndex")
        drain_last_index = Signal(physical_index_width, name="drainLastIndex")
        drain_length = Signal(length_width, init=3, name="drainLength")

        write_req_pipe = m2s_pipe(m, self.write_req, name="writeReqPipe")
        wp = write_req_pipe.payload
        direct_write_queue = _fifo(WriteReq(c), 8)
        m.submodules.directWriteQueue = direct_write_queue

        # -- the Scala helper `def`s, as closures over `m` ----------------
        def clear_slot_meta(slot):
            m.d.comb += slot_flush[slot].eq(1)
            m.d.sync += [
                slot_valid[slot].eq(0),
                slot_draining[slot].eq(0),
                slot_start_addr[slot].eq(0),
                slot_word_count[slot].eq(0),
                slot_pending_address[slot].eq(0),
                slot_pending_data[slot].eq(0),
                slot_pending_mask[slot].eq(0),
            ]

        def init_slot_meta(slot, address, data, mask):
            clear_slot_meta(slot)
            m.d.sync += [
                slot_valid[slot].eq(1),
                slot_start_addr[slot].eq(address),
                slot_word_count[slot].eq(1),
                slot_pending_address[slot].eq(address),
                slot_pending_data[slot].eq(data),
                slot_pending_mask[slot].eq(mask),
            ]

        def record_drain(start_addr, word_count, reason: int):
            m.d.sync += [
                write_drain_count.eq(write_drain_count + 1),
                write_drain_dirty_word_total.eq(
                    write_drain_dirty_word_total + _resize(word_count, 32)),
            ]
            with m.If(word_count == Const(run_depth, count_width)):
                m.d.sync += write_full_drain_count.eq(write_full_drain_count + 1)
            with m.Else():
                m.d.sync += write_partial_drain_count.eq(write_partial_drain_count + 1)
            with m.If(word_count == 1):
                m.d.sync += write_single_word_drain_count.eq(write_single_word_drain_count + 1)
                with m.If(physical_word_index(start_addr) == 0):
                    m.d.sync += write_single_word_drain_start_at_zero_count.eq(
                        write_single_word_drain_start_at_zero_count + 1)
                with m.If(physical_word_index(start_addr) == last_physical_word_index):
                    m.d.sync += write_single_word_drain_start_at_last_count.eq(
                        write_single_word_drain_start_at_last_count + 1)
            # `switch(reason)` on a per-call-site constant.
            if reason == DRAIN_REASON_FULL:
                m.d.sync += write_drain_reason_full_count.eq(write_drain_reason_full_count + 1)
            elif reason == DRAIN_REASON_ROTATE:
                m.d.sync += write_drain_reason_rotate_count.eq(
                    write_drain_reason_rotate_count + 1)
            elif reason == DRAIN_REASON_FLUSH:
                m.d.sync += write_drain_reason_flush_count.eq(
                    write_drain_reason_flush_count + 1)
            else:
                raise ValueError(f"reason {reason} is not a drain reason")

        def start_drain(slot, reason: int):
            word_count = slot_word_count[slot]
            record_drain(slot_start_addr[slot], word_count, reason)
            m.d.sync += [
                slot_draining[slot].eq(1),
                drain_slot.eq(slot),
                drain_index.eq(0),
                drain_last_index.eq((word_count - 1)[0:physical_index_width]),
                drain_length.eq(
                    ((_resize(word_count, length_width) << 2) - 1)[0:length_width]),
                drain_state.eq(DrainState.SEND),
            ]

        # -- defaults (the block of `:= False` / `:= 0` in the Scala) ------
        m.d.comb += [
            slot0_push.valid.eq(0),
            slot1_push.valid.eq(0),
            slot0_pop.ready.eq(0),
            slot1_pop.ready.eq(0),
            cmd.valid.eq(0),
            cmd_frag.address.eq(0),
            cmd_frag.opcode.eq(CMD_WRITE),
            cmd_frag.length.eq(3),
            cmd_frag.source.eq(0),
            cmd_frag.data.eq(0),
            cmd_frag.mask.eq(0),
            cmd.payload.last.eq(1),
            rsp.ready.eq(0),
            write_req_pipe.ready.eq(0),
            direct_write_queue.push.valid.eq(0),
            direct_write_queue.pop.ready.eq(0),
            direct_write_queue.flush.eq(0),
        ]
        # (`slot0Push.payload.assignDontCare()` and the directWriteQueue push
        # payload: left undriven, i.e. zero, which Spinal's X never reaches a
        # port either.)

        # -- the active-slot view ------------------------------------------
        other_slot = Signal(slot_index_width, name="otherSlot")
        m.d.comb += other_slot.eq(active_slot ^ Const(1, slot_index_width))
        active_valid = Signal(name="activeValid")
        active_count = Signal(count_width, name="activeCount")
        active_start_addr = Signal(addr_width, name="activeStartAddr")
        active_pending_addr = Signal(addr_width, name="activePendingAddr")
        active_pending_data = Signal(32, name="activePendingData")
        active_pending_mask = Signal(4, name="activePendingMask")
        active_pending_word_complete = Signal(name="activePendingWordComplete")
        active_next_addr = Signal(addr_width, name="activeNextAddr")
        same_word_merge = Signal(name="sameWordMerge")
        sequential_append = Signal(name="sequentialAppend")
        needs_rotate = Signal(name="needsRotate")
        can_rotate = Signal(name="canRotate")
        active_push_ready = Signal(name="activePushReady")
        active_append_ready = Signal(name="activeAppendReady")
        m.d.comb += [
            active_valid.eq(slot_valid[active_slot]),
            active_count.eq(slot_word_count[active_slot]),
            active_start_addr.eq(slot_start_addr[active_slot]),
            active_pending_addr.eq(slot_pending_address[active_slot]),
            active_pending_data.eq(slot_pending_data[active_slot]),
            active_pending_mask.eq(slot_pending_mask[active_slot]),
            active_pending_word_complete.eq(active_pending_mask.all()),
            active_next_addr.eq(addr_plus_words(active_start_addr, active_count)),
            same_word_merge.eq(active_valid & (wp.address == active_pending_addr)),
            sequential_append.eq(
                active_valid & (active_count != Const(run_depth, count_width))
                & (wp.address == active_next_addr)),
            needs_rotate.eq(active_valid & ~(same_word_merge | sequential_append)),
            can_rotate.eq((drain_state == DrainState.IDLE) & ~slot_valid[other_slot]),
            active_push_ready.eq(slot_push_ready(active_slot)),
            # Only the formal assert reads this in the Scala; kept for waveforms.
            active_append_ready.eq(
                ~active_valid | same_word_merge | (sequential_append & active_push_ready)),
        ]

        with m.If(write_req_pipe.valid & ~write_req_pipe.ready):
            m.d.sync += write_stall_cycles.eq(write_stall_cycles + 1)
        with m.If(write_req_pipe.valid & needs_rotate & ~can_rotate):
            m.d.sync += write_rotate_blocked_cycles.eq(write_rotate_blocked_cycles + 1)

        # -- DrainState.Send: one beat per dirty word ----------------------
        drain_pending = Signal(name="drainPending")
        drain_address = Signal(addr_width, name="drainAddress")
        drain_data = Signal(32, name="drainData")
        drain_mask = Signal(4, name="drainMask")
        m.d.comb += [
            drain_pending.eq(drain_index == drain_last_index),
            drain_address.eq(addr_plus_words(slot_start_addr[drain_slot], drain_index)),
        ]
        with m.If(drain_pending):
            m.d.comb += [
                drain_data.eq(slot_pending_data[drain_slot]),
                drain_mask.eq(slot_pending_mask[drain_slot]),
            ]
        with m.Else():
            m.d.comb += [
                drain_data.eq(slot_pop_data(drain_slot)),
                drain_mask.eq(slot_pop_mask(drain_slot)),
            ]

        with m.If(drain_state == DrainState.SEND):
            m.d.comb += [
                cmd.valid.eq(drain_pending | slot_pop_valid(drain_slot)),
                cmd_frag.address.eq(drain_address),
                cmd_frag.opcode.eq(CMD_WRITE),
                cmd_frag.length.eq(drain_length),
                cmd_frag.source.eq(1),
                cmd_frag.data.eq(drain_data),
                cmd_frag.mask.eq(drain_mask),
                cmd.payload.last.eq(drain_pending),
            ]
            with m.If(fire(cmd)):
                with m.If(~drain_pending):
                    drive_slot_pop_ready(drain_slot)
                with m.If(drain_pending):
                    clear_slot_meta(drain_slot)
                    m.d.sync += drain_state.eq(DrainState.IDLE)
                with m.Else():
                    m.d.sync += drain_index.eq(drain_index + 1)

        # io.mem.rsp.ready := io.mem.rsp.source === 1   (allowOverride)
        m.d.comb += rsp.ready.eq(rsp_frag.source == 1)
        with m.If(drain_state == DrainState.RSP):
            with m.If(fire(rsp) & (rsp_frag.source == 1)):
                m.d.sync += drain_state.eq(DrainState.IDLE)

        # -- drain a full, byte-complete line while nothing is arriving ----
        with m.If((drain_state == DrainState.IDLE) & active_valid
                  & (active_count == Const(run_depth, count_width))
                  & active_pending_word_complete & ~slot_valid[other_slot]
                  & ~write_req_pipe.valid):
            clear_slot_meta(other_slot)
            start_drain(active_slot, DRAIN_REASON_FULL)
            m.d.sync += active_slot.eq(other_slot)

        # -- io.flush ------------------------------------------------------
        with m.If((drain_state == DrainState.IDLE) & self.flush & active_valid
                  & ~slot_valid[other_slot] & ~write_req_pipe.valid):
            clear_slot_meta(other_slot)
            start_drain(active_slot, DRAIN_REASON_FLUSH)
            m.d.sync += active_slot.eq(other_slot)

        # -- accept a write ------------------------------------------------
        # `mergeWord(...)`: computed unconditionally, used in one branch.
        merged_data = Signal(32, name="mergedData")
        merged_mask = Signal(4, name="mergedMask")
        m.d.comb += [
            merged_data.eq(active_pending_data),
            merged_mask.eq(active_pending_mask | wp.mask),
        ]
        for lane in range(4):
            with m.If(wp.mask[lane]):
                m.d.comb += merged_data[8 * lane:8 * lane + 8].eq(wp.data[8 * lane:8 * lane + 8])

        adjacent_physical_line = Signal(name="adjacentPhysicalLine")
        m.d.comb += adjacent_physical_line.eq(
            (physical_word_index(active_pending_addr) == last_physical_word_index)
            & (physical_word_index(wp.address) == 0))

        with m.If(write_req_pipe.valid):
            with m.If(~active_valid):
                m.d.comb += write_req_pipe.ready.eq(1)
            with m.Elif(same_word_merge):
                m.d.comb += write_req_pipe.ready.eq(1)
            with m.Elif(sequential_append & active_push_ready):
                m.d.comb += write_req_pipe.ready.eq(1)
            with m.Elif(needs_rotate & can_rotate):
                m.d.comb += write_req_pipe.ready.eq(1)

            with m.If(fire(write_req_pipe)):
                with m.If(~active_valid):
                    init_slot_meta(active_slot, wp.address, wp.data, wp.mask)
                with m.Elif(same_word_merge):
                    m.d.sync += [
                        slot_pending_data[active_slot].eq(merged_data),
                        slot_pending_mask[active_slot].eq(merged_mask),
                    ]
                with m.Elif(sequential_append):
                    drive_slot_push(active_slot, active_pending_addr,
                                    active_pending_data, active_pending_mask)
                    m.d.sync += [
                        slot_word_count[active_slot].eq(active_count + 1),
                        slot_pending_address[active_slot].eq(wp.address),
                        slot_pending_data[active_slot].eq(wp.data),
                        slot_pending_mask[active_slot].eq(wp.mask),
                    ]
                with m.Else():
                    with m.If(adjacent_physical_line):
                        m.d.sync += write_rotate_adjacent_line_count.eq(
                            write_rotate_adjacent_line_count + 1)
                    with m.Elif((wp.address > active_pending_addr)
                                & (wp.address < active_next_addr)):
                        m.d.sync += write_rotate_same_line_gap_count.eq(
                            write_rotate_same_line_gap_count + 1)
                    with m.Else():
                        m.d.sync += write_rotate_other_line_count.eq(
                            write_rotate_other_line_count + 1)

                    clear_slot_meta(other_slot)
                    start_drain(active_slot, DRAIN_REASON_ROTATE)
                    init_slot_meta(other_slot, wp.address, wp.data, wp.mask)
                    m.d.sync += active_slot.eq(other_slot)

        m.d.comb += self.busy.eq(
            (drain_state != DrainState.IDLE) | write_req_pipe.valid
            | (direct_write_queue.occupancy != 0) | (self.flush & active_valid))

        # -- what the tests look at ---------------------------------------
        d = self.dbg
        d.run_depth = run_depth
        d.slot_fifos = slot_fifos
        d.slot_occupancy = tuple(f.occupancy for f in slot_fifos)
        d.slot_valid = slot_valid
        d.slot_draining = slot_draining
        d.slot_word_count = slot_word_count
        d.slot_start_addr = slot_start_addr
        d.slot_pending_address = slot_pending_address
        d.slot_pending_data = slot_pending_data
        d.slot_pending_mask = slot_pending_mask
        d.active_slot = active_slot
        d.drain_state = drain_state
        d.drain_slot = drain_slot
        d.write_req_pipe = write_req_pipe
        d.direct_write_queue = direct_write_queue
        d.same_word_merge = same_word_merge
        d.sequential_append = sequential_append
        d.needs_rotate = needs_rotate
        d.can_rotate = can_rotate
        d.active_push_ready = active_push_ready
        d.active_append_ready = active_append_ready
