"""``MemBus`` -- the Amaranth clone of SpinalHDL's ``Bmb`` memory bus.

Transliteration of ``spinal/lib/bus/bmb/*`` (SpinalHDL **1.15.0**, read from
``spinalhdl-lib_2.12-1.15.0-sources.jar``), plan §2 "Bmb" row and Phase 1.2:

===========================  ==================================================
Scala                        here
===========================  ==================================================
``Bmb.scala``                :class:`MemBusParameter`, :class:`MemBusAccessParameter`,
                             :class:`MemBusSourceParameter`, :class:`BurstAlignment`,
                             :func:`cmd_layout`, :func:`rsp_layout`,
                             :class:`MemBusSignature`, :func:`MemBus`,
                             :func:`connect`, :func:`incr`, :func:`add_to_address`
``BmbArbiter.scala``         :class:`Arbiter`
``BmbSourceRemover.scala``   :class:`SourceRemover`
``BmbContextRemover.scala``  :class:`ContextRemover`
``BmbSpecificBridges.scala`` :class:`Aligner`, :class:`LengthFixer`,
                             :class:`AlignedSplitter`
===========================  ==================================================

Scope rule (plan §6): only what the core instantiates is ported, and what is
ported is ported *exactly* -- same registers, same combinational paths, same
context packing -- because each one is lockstep-diffed against the Spinal
Verilog (plan §4.2).  Census of ``ext/SpinalVoodoo/src/voodoo`` +
``fpga/src/voodoo`` (de10 wrapper included for completeness, 2026-09-20)::

    BmbArbiter         9   CoreSim.scala:37 (5 ports), CoreGowin/CoreArtix (5 ports),
                           FramebufferMemSubsystem.scala:37/45/52/63 (2/1/3/1 ports)
    BmbSourceRemover   4   De10MemBackend.scala:480/497 only
    BmbAlignedSpliter  2   FramebufferPlaneReader.scala:98 + :453 (parameter maths)
    BmbContextRemover  2   FramebufferPlaneReader.scala:100 + :457
    BmbLengthFixer     2   CoreArtix.scala:213/227 (BmbToAxi4Mem)
    BmbAligner         2   CoreArtix.scala:212/226 (BmbToAxi4Mem)
    BmbRouting        17   core-local, ported in avoodoo/bus/routing.py

Deliberately **not** ported (the design never reaches them): ``BmbDecoder``,
``BmbUnburstify``, ``BmbUpSizerBridge``/``BmbDownSizerBridge``,
``BmbExclusiveMonitor``, ``BmbInvalidateMonitor``, ``BmbWriteRetainer``,
``BmbSlaveFactory``, ``BmbOnChipRam``, ``BmbCc``, ``BmbSourceDecoder``,
``BmbSyncRemover``, the ``inv``/``ack``/``sync`` channels (every parameter in
the core has ``canInvalidate = canSync = false``), and ``canExclusive``.
Asking for them raises rather than silently building something different.

Semantics worth knowing before touching a port (measured, see
:data:`MEMBUS_NOTES` at the bottom for the long form):

* **rsp is out of order across sources, in order within one source.**  The
  ``Bmb`` comment says so and the arbiter relies on it: it routes responses by
  the low ``log2Up(portCount)`` bits of ``rsp.source``, with no reorder buffer.
* **The arbiter truncates the source.**  ``cmd.source := (source @@ chosen).resized``
  into a field that is *not* widened, so with five framebuffer masters
  (``sourceWidth = 4``, ``routeWidth = 3``) only the bottom bit of a master's
  own source survives.
* **``last`` is per output burst, not per input burst.**  ``LengthFixer`` and
  ``AlignedSplitter`` re-derive it and carry the *input's* last in the context.
* **A write burst carries a fresh address on every beat** in this design --
  ``FramebufferPlaneBuffer`` increments it and ``SimBmbMemory`` writes to the
  address of the current beat, so a constant-address burst (what ``Aligner``
  emits) would write every beat to the same word.
* **``mask`` is write-only** (``canWrite && canMask``); reads ignore it and
  always return the whole word.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace

from amaranth import Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.stream import (Arbiter as StreamArbiter, arbitration_from,
                                connect_stream, fire, fork2, halt_when, m2s_pipe,
                                queue, take_when, throw_when, translate_with)

__all__ = [
    "BurstAlignment",
    "MemBusSourceParameter",
    "MemBusAccessParameter",
    "MemBusInvalidationParameter",
    "MemBusParameter",
    "CMD_READ",
    "CMD_WRITE",
    "RSP_SUCCESS",
    "RSP_ERROR",
    "BOUNDARY_WIDTH",
    "BOUNDARY_SIZE",
    "log2_up",
    "cmd_fragment_layout",
    "rsp_fragment_layout",
    "cmd_layout",
    "rsp_layout",
    "MemBusSignature",
    "MemBus",
    "connect",
    "incr",
    "add_to_address",
    "transfer_beat_count_minus_one",
    "first_flag",
    "Arbiter",
    "SourceRemover",
    "ContextRemover",
    "Aligner",
    "LengthFixer",
    "AlignedSplitter",
    "MEMBUS_NOTES",
]


# --------------------------------------------------------------------------
# constants (Bmb.scala `object Bmb`)
# --------------------------------------------------------------------------

#: ``Bmb.boundaryWidth`` -- a burst may not cross a 4 KiB boundary.
BOUNDARY_WIDTH = 12
BOUNDARY_SIZE = 1 << BOUNDARY_WIDTH

CMD_READ = 0
CMD_WRITE = 1
RSP_SUCCESS = 0
RSP_ERROR = 1


def log2_up(value: int) -> int:
    """``spinal.core.log2Up``: ``0`` for 0 and 1, else ``ceil(log2(value))``."""
    if value < 0:
        raise ValueError(f"log2Up({value}) is negative")
    if value <= 1:
        return 0
    return (value - 1).bit_length()


def _is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


# --------------------------------------------------------------------------
# parameters (Bmb.scala)
# --------------------------------------------------------------------------


class BurstAlignment(enum.Enum):
    """``BmbParameter.BurstAlignement.Kind``.

    ``BYTE`` allows any start address, ``WORD`` requires word alignment,
    ``LENGTH`` requires the burst to be aligned to its own length.  Only the
    two ``allow*`` predicates are ever queried.
    """

    BYTE = "BYTE"
    WORD = "WORD"
    LENGTH = "LENGTH"

    @property
    def allow_byte(self) -> bool:
        return self is BurstAlignment.BYTE

    @property
    def allow_word(self) -> bool:
        return self in (BurstAlignment.BYTE, BurstAlignment.WORD)


_INT_MAX = 2 ** 31 - 1  # Scala's Int.MaxValue, used as "unbounded"


@dataclass(frozen=True)
class MemBusSourceParameter:
    """``BmbSourceParameter``.  One entry per source id."""

    context_width: int
    length_width: int
    alignment: BurstAlignment = BurstAlignment.WORD
    alignment_min: int = 0
    access_latency_min: int = 1
    can_read: bool = True
    can_write: bool = True
    can_mask: bool = True
    can_exclusive: bool = False
    with_cached_read: bool = False
    maximum_pending_transaction: int = _INT_MAX
    can_invalidate: bool = False
    can_sync: bool = False

    @staticmethod
    def aggregate(sources) -> "MemBusSourceParameter":
        """``BmbSourceParameter.aggregate`` -- the widest of every field.

        The alignment fold is the odd one: ``LENGTH`` is the identity, and a
        ``WORD`` accumulator is replaced by anything that is not ``LENGTH``,
        so the result is the *loosest* alignment any source asks for.
        """
        sources = list(sources)
        context_width = 0
        length_width = 0
        alignment = BurstAlignment.LENGTH
        alignment_min = _INT_MAX
        access_latency_min = _INT_MAX
        can_read = can_write = can_mask = can_exclusive = False
        with_cached_read = can_invalidate = can_sync = False
        maximum_pending_transaction = 0

        for s in sources:
            context_width = max(context_width, s.context_width)
            length_width = max(length_width, s.length_width)
            if alignment is BurstAlignment.LENGTH:
                alignment = s.alignment
            elif alignment is BurstAlignment.WORD and s.alignment is not BurstAlignment.LENGTH:
                alignment = s.alignment
            alignment_min = min(alignment_min, s.alignment_min)
            access_latency_min = min(access_latency_min, s.access_latency_min)
            can_read |= s.can_read
            can_write |= s.can_write
            can_mask |= s.can_write and s.can_mask
            can_exclusive |= s.can_exclusive
            with_cached_read |= s.with_cached_read
            can_invalidate |= s.can_invalidate
            can_sync |= s.can_sync
            maximum_pending_transaction = max(maximum_pending_transaction,
                                              s.maximum_pending_transaction)

        return MemBusSourceParameter(
            context_width=context_width,
            length_width=length_width,
            alignment=alignment,
            alignment_min=alignment_min,
            access_latency_min=access_latency_min,
            can_read=can_read,
            can_write=can_write,
            can_mask=can_mask,
            can_exclusive=can_exclusive,
            with_cached_read=with_cached_read,
            maximum_pending_transaction=maximum_pending_transaction,
            can_invalidate=can_invalidate,
            can_sync=can_sync,
        )


@dataclass(frozen=True)
class MemBusAccessParameter:
    """``BmbAccessParameter``.

    ``sources`` is Scala's ``LinkedHashMap[Int, BmbSourceParameter]``; a plain
    ``dict`` keeps the same insertion order.  It is stored as a tuple of pairs
    so the dataclass stays hashable and frozen (Scala's is mutable, but the
    core only ever builds one and derives copies from it).
    """

    address_width: int
    data_width: int
    _sources: tuple = ()

    def __post_init__(self) -> None:
        if self.data_width % 8:
            raise ValueError(f"dataWidth {self.data_width} is not a multiple of 8")
        if not _is_pow2(self.data_width // 8):
            raise ValueError(f"byteCount {self.data_width // 8} is not a power of two")

    # -- construction ------------------------------------------------------

    @staticmethod
    def make(address_width: int, data_width: int, sources=None) -> "MemBusAccessParameter":
        items = tuple(sources.items()) if isinstance(sources, dict) else tuple(sources or ())
        return MemBusAccessParameter(address_width, data_width, items)

    @property
    def sources(self) -> dict:
        return dict(self._sources)

    def add_sources(self, count: int, p: MemBusSourceParameter) -> "MemBusAccessParameter":
        """``addSources`` -- ids ``0 .. count-1`` all get ``p``."""
        return replace(self, _sources=tuple((i, p) for i in range(count)))

    def with_single_source(self, source: MemBusSourceParameter) -> "MemBusAccessParameter":
        return replace(self, _sources=((0, source),))

    def sources_transform(self, f) -> "MemBusAccessParameter":
        return replace(self, _sources=tuple((k, f(v)) for k, v in self._sources))

    def sources_remap(self, f) -> "MemBusAccessParameter":
        return replace(self, _sources=tuple((f(k), v) for k, v in self._sources))

    def to_mem_bus_parameter(self) -> "MemBusParameter":
        return MemBusParameter(self, MemBusInvalidationParameter())

    # -- aggregated view ---------------------------------------------------

    @property
    def aggregated(self) -> MemBusSourceParameter:
        return MemBusSourceParameter.aggregate(v for _, v in self._sources)

    @property
    def context_width(self) -> int:
        return self.aggregated.context_width

    @property
    def length_width(self) -> int:
        return self.aggregated.length_width

    @property
    def alignment(self) -> BurstAlignment:
        return self.aggregated.alignment

    @property
    def alignment_min(self) -> int:
        return self.aggregated.alignment_min

    @property
    def access_latency_min(self) -> int:
        return self.aggregated.access_latency_min

    @property
    def can_read(self) -> bool:
        return self.aggregated.can_read

    @property
    def can_write(self) -> bool:
        return self.aggregated.can_write

    @property
    def can_mask(self) -> bool:
        return self.aggregated.can_mask

    @property
    def can_exclusive(self) -> bool:
        return self.aggregated.can_exclusive

    @property
    def can_invalidate(self) -> bool:
        return self.aggregated.can_invalidate

    @property
    def can_sync(self) -> bool:
        return self.aggregated.can_sync

    @property
    def maximum_pending_transaction(self) -> int:
        return self.aggregated.maximum_pending_transaction

    # -- derived widths ----------------------------------------------------

    @property
    def source_width(self) -> int:
        """``log2Up(sources.keys.max + 1)`` -- **0 for a single source**."""
        return log2_up(max(k for k, _ in self._sources) + 1)

    @property
    def byte_count(self) -> int:
        return self.data_width // 8

    @property
    def word_mask(self) -> int:
        return self.byte_count - 1

    @property
    def word_range_length(self) -> int:
        return log2_up(self.byte_count)

    @property
    def mask_width(self) -> int:
        return self.byte_count

    @property
    def allow_burst(self) -> bool:
        return self.length_width > self.word_range_length

    @property
    def _any_allow_byte(self) -> bool:
        return any(v.alignment.allow_byte for _, v in self._sources)

    @property
    def beat_counter_width(self) -> int:
        return self.length_width - self.word_range_length + (1 if self._any_allow_byte else 0)

    @property
    def transfer_beat_count(self) -> int:
        return ((1 << self.length_width) // self.byte_count
                + (1 if self._any_allow_byte else 0))


@dataclass(frozen=True)
class MemBusInvalidationParameter:
    """``BmbInvalidationParameter``.  Present for shape only; unused."""

    invalidate_length: int = 0
    invalidate_alignment: BurstAlignment = BurstAlignment.WORD


@dataclass(frozen=True)
class MemBusParameter:
    """``BmbParameter(access, invalidation)``."""

    access: MemBusAccessParameter
    invalidation: MemBusInvalidationParameter = field(
        default_factory=MemBusInvalidationParameter)

    @staticmethod
    def simple(*, address_width: int, data_width: int, source_width: int,
               context_width: int, length_width: int,
               alignment: BurstAlignment = BurstAlignment.WORD,
               alignment_min: int = 0, access_latency_min: int = 1,
               can_read: bool = True, can_write: bool = True,
               can_exclusive: bool = False,
               maximum_pending_transaction: int = _INT_MAX) -> "MemBusParameter":
        """``BmbParameter.apply(addressWidth, dataWidth, sourceWidth, ...)``.

        This is the constructor every ``*BmbParams`` in the core uses: it
        creates ``1 << sourceWidth`` identical sources, so ``sourceWidth`` round
        trips (``log2Up(2**w) == w``) -- **except** for ``sourceWidth = 0``,
        which yields one source and stays 0.
        """
        src = MemBusSourceParameter(
            context_width=context_width,
            length_width=length_width,
            alignment=alignment,
            alignment_min=alignment_min,
            access_latency_min=access_latency_min,
            can_read=can_read,
            can_write=can_write,
            can_exclusive=can_exclusive,
            maximum_pending_transaction=maximum_pending_transaction,
        )
        access = MemBusAccessParameter(address_width, data_width).add_sources(
            1 << source_width, src)
        return MemBusParameter(access, MemBusInvalidationParameter())

    def with_access(self, access: MemBusAccessParameter) -> "MemBusParameter":
        return replace(self, access=access)


# --------------------------------------------------------------------------
# payload layouts (BmbCmd / BmbRsp / Fragment)
# --------------------------------------------------------------------------
#
# `Fragment[T]` (spinal/lib/Fragment.scala:475) declares `last` FIRST and
# `fragment` second, and `MultiData.asBits` puts the first element at the LSB,
# so a Fragment packs as {fragment, last} with `last` at bit 0.  `BmbCmd`
# declares source, opcode, exclusive, address, length, data, mask, context --
# `source` therefore sits at the LSB of the fragment.  Amaranth's
# `data.StructLayout` also lays fields out LSB-first in declaration order, so
# transcribing the order verbatim reproduces the Spinal bit packing.
#
# A field whose width is 0, or whose Scala `generate` guard is false, is
# omitted: Spinal emits no Verilog port for a zero-width signal, and the
# differential harness rejects an Amaranth leaf with no Spinal twin.
# `sourceWidth == 0` is the common case here -- every `withSingleSource`
# output parameter has it.


def _opt(fields: dict, name: str, width: int, present: bool = True) -> None:
    if present and width > 0:
        fields[name] = unsigned(width)


def cmd_fragment_layout(p: MemBusParameter) -> data.StructLayout:
    """``BmbCmd(p)`` -- field order exactly as the Scala bundle declares it."""
    a = p.access
    f: dict = {}
    _opt(f, "source", a.source_width)
    f["opcode"] = unsigned(1)
    _opt(f, "exclusive", 1, a.can_exclusive)
    _opt(f, "address", a.address_width)
    _opt(f, "length", a.length_width)
    _opt(f, "data", a.data_width, a.can_write)
    _opt(f, "mask", a.mask_width, a.can_write and a.can_mask)
    _opt(f, "context", a.context_width)
    return data.StructLayout(f)


def rsp_fragment_layout(p: MemBusParameter) -> data.StructLayout:
    """``BmbRsp(p)``."""
    a = p.access
    f: dict = {}
    _opt(f, "source", a.source_width)
    f["opcode"] = unsigned(1)
    _opt(f, "exclusive", 1, a.can_exclusive)
    _opt(f, "data", a.data_width, a.can_read)
    _opt(f, "context", a.context_width)
    return data.StructLayout(f)


def cmd_layout(p: MemBusParameter) -> data.StructLayout:
    """``Fragment(BmbCmd(p))``."""
    return data.StructLayout({"last": unsigned(1), "fragment": cmd_fragment_layout(p)})


def rsp_layout(p: MemBusParameter) -> data.StructLayout:
    """``Fragment(BmbRsp(p))``."""
    return data.StructLayout({"last": unsigned(1), "fragment": rsp_fragment_layout(p)})


def _has(view, name: str) -> bool:
    return name in view.shape().members


def _get(view, name: str, width: int | None = None):
    """Field value, or a zero constant when the field was optimised away."""
    if _has(view, name):
        return view[name]
    return Const(0, 0 if width is None else width)


# --------------------------------------------------------------------------
# signature
# --------------------------------------------------------------------------


class MemBusSignature(wiring.Signature):
    """``Bmb(p)`` seen from the **master**: ``cmd`` out, ``rsp`` in.

    ``inv``/``ack``/``sync`` are not ported -- every parameter set in the core
    has ``canInvalidate = canSync = false``, and building half a coherency
    channel would be worse than refusing.
    """

    def __init__(self, p: MemBusParameter):
        if p.access.can_invalidate or p.access.can_sync:
            raise NotImplementedError(
                "MemBus does not port the inv/ack/sync channels; no parameter "
                "set in SpinalVoodoo enables them")
        if not (p.access.can_read or p.access.can_write):
            raise ValueError("a MemBus must be readable or writable")
        self._p = p
        super().__init__({
            "cmd": Out(stream.Signature(cmd_layout(p))),
            "rsp": In(stream.Signature(rsp_layout(p))),
        })

    @property
    def parameter(self) -> MemBusParameter:
        return self._p

    def __eq__(self, other):
        return type(other) is type(self) and other._p == self._p

    def __hash__(self):
        return hash((type(self), self._p))

    def __repr__(self):
        return f"MemBusSignature({self._p!r})"


def MemBus(p: MemBusParameter, *, path=None):  # noqa: N802 - mirrors `Bmb(p)`
    """A free-standing master-side ``MemBus`` bundle, for internal wiring."""
    return MemBusSignature(p).create(path=path, src_loc_at=1)


# --------------------------------------------------------------------------
# WeakConnector / `<<`
# --------------------------------------------------------------------------

# (name, allow_up_size, allow_down_size, allow_drop, default) -- Bmb.scala's
# `BmbCmd.weakAssignFrom` / `BmbRsp.weakAssignFrom` tables, verbatim.  The
# widths are checked here rather than silently resized, because a resize the
# Scala forbids means the port was parameterised wrongly.
_CMD_WEAK = (
    ("source", True, False, False, 0),
    ("opcode", False, False, False, None),
    ("address", True, True, False, None),
    ("length", True, False, False, None),
    ("data", False, False, True, 0),
    ("mask", False, False, True, 0),
    ("context", True, False, False, 0),
    ("exclusive", False, False, False, 0),
)

_RSP_WEAK = (
    ("source", False, True, False, 0),
    ("opcode", False, False, False, None),
    ("data", False, False, True, 0),
    ("context", False, True, False, 0),
    ("exclusive", False, False, True, None),
)


def _assign_some_by_name(m, dst, src, *, skip=()):
    """``Data.assignSomeByName`` -- copy every field the two share.

    Spinal's ``:=`` between two ``Bundle``\\ s resolves element by *name*, not
    by bit position.  Reproducing that matters here because the adapters
    connect buses whose parameters differ: ``ContextRemover``'s output has no
    ``source`` and no ``context``, so a packed bit copy would shift ``opcode``
    and ``data`` into the wrong place.
    """
    for name in src.shape().members:
        if name in skip or not _has(dst, name):
            continue
        m.d.comb += dst[name].eq(src[name])


def _weak_assign(m, dst, src, table, *, what: str, skip=()):
    """``weakAssignFrom``: connect by name, resizing only where allowed."""
    for name, allow_up, allow_down, allow_drop, default in table:
        if name in skip:
            continue
        in_dst, in_src = _has(dst, name), _has(src, name)
        if not in_dst and not in_src:
            continue
        if in_dst and not in_src:
            if default is None:
                raise ValueError(f"{what}: source has no {name!r} to drive the sink's")
            m.d.comb += dst[name].eq(default)
            continue
        if in_src and not in_dst:
            if not allow_drop:
                raise ValueError(f"{what}: sink has no {name!r} to take the source's")
            continue
        dw, sw = len(dst[name]), len(src[name])
        if dw < sw and not allow_down:
            raise ValueError(f"{what}: {name!r} may not narrow {sw} -> {dw}")
        if dw > sw and not allow_up:
            raise ValueError(f"{what}: {name!r} may not widen {sw} -> {dw}")
        m.d.comb += dst[name].eq(src[name])


def connect(m: Module, sink, source) -> None:
    """``sink << source`` for two ``MemBus`` interfaces (``Bmb.<<``).

    ``sink`` is the downstream (slave-facing) bus, ``source`` the upstream one:
    commands flow sink-wards, responses back.  Field widths may differ; the
    ``weakAssignFrom`` table above says which direction each field may resize.
    """
    arbitration_from(m, sink.cmd, source.cmd)
    arbitration_from(m, source.rsp, sink.rsp)
    m.d.comb += sink.cmd.payload.last.eq(source.cmd.payload.last)
    m.d.comb += source.rsp.payload.last.eq(sink.rsp.payload.last)
    _weak_assign(m, sink.cmd.payload.fragment, source.cmd.payload.fragment,
                 _CMD_WEAK, what="cmd")
    _weak_assign(m, source.rsp.payload.fragment, sink.rsp.payload.fragment,
                 _RSP_WEAK, what="rsp")


# --------------------------------------------------------------------------
# address helpers (object Bmb)
# --------------------------------------------------------------------------


def _uadd(a, b, width: int):
    """Spinal ``UInt + UInt``: ``max(wa, wb)`` bits, wrapping."""
    return (a + b)[:width]


def _usub(a, b, width: int):
    """Spinal ``UInt - UInt``: ``max(wa, wb)`` bits, wrapping."""
    return (a - b)[:width]


def incr(address, p: MemBusParameter):
    """``Bmb.incr`` -- next word address, wrapping inside the 4 KiB boundary."""
    aw = len(address)
    high = address[BOUNDARY_WIDTH:] if aw > BOUNDARY_WIDTH else Const(0, 0)
    base = address[:min(BOUNDARY_WIDTH, aw)]
    byte_count = p.access.byte_count
    stepped = _uadd(base, byte_count, BOUNDARY_WIDTH) & Const(
        (~(byte_count - 1)) & ((1 << BOUNDARY_WIDTH) - 1), BOUNDARY_WIDTH)
    return Cat(stepped, high)[:aw]


def add_to_address(address, value, p: MemBusParameter):
    """``Bmb.addToAddress`` -- add inside the 4 KiB boundary, high bits intact."""
    aw = len(address)
    high = address[BOUNDARY_WIDTH:] if aw > BOUNDARY_WIDTH else Const(0, 0)
    base = address[:min(BOUNDARY_WIDTH, aw)]
    width = max(BOUNDARY_WIDTH, len(value))
    return Cat(_uadd(base, value, width)[:BOUNDARY_WIDTH], high)[:aw]


def transfer_beat_count_minus_one(p: MemBusParameter, cmd_fragment):
    """``BmbCmd.transferBeatCountMinusOne``.

    For a word/length-aligned bus it is just the high part of ``length``; for a
    byte-aligned one the start offset inside the word has to be added first,
    which is why the result is one bit wider.
    """
    a = p.access
    lw = a.length_width
    wrl = a.word_range_length
    if not a.alignment.allow_byte:
        if lw < wrl:
            return Const(0, 0)
        return cmd_fragment.length[wrl:]
    widened = Cat(cmd_fragment.length, Const(0, 1))            # U"0" @@ length
    summed = _uadd(widened, cmd_fragment.address[:wrl], lw + 1)
    return summed[wrl:lw + 1]


def first_flag(m: Module, s, *, name: str = "first"):
    """``Stream[Fragment[T]].first`` = ``RegNextWhen(last, fire, init = True)``."""
    reg = Signal(init=1, name=name)
    with m.If(fire(s)):
        m.d.sync += reg.eq(s.payload.last)
    return reg


# --------------------------------------------------------------------------
# BmbArbiter.scala
# --------------------------------------------------------------------------


class Arbiter(wiring.Component):
    """``spinal.lib.bus.bmb.BmbArbiter`` (``BmbArbiter.scala``).

    One master out of ``len(inputs_parameter)``; ``lower_first_priority``
    selects ``StreamArbiterFactory().fragmentLock.lowerFirst`` over
    ``.roundRobin``.  ``fragmentLock`` is what keeps a write burst's beats from
    interleaving: the grant is held until a beat with ``last`` fires.

    Response routing has no storage.  The chosen port index is stuffed into the
    *low* ``log2Up(portCount)`` bits of ``cmd.source``, and ``rsp.source``'s low
    bits pick the input the response goes back to.  Two consequences:

    * the slave must echo ``source`` unchanged (``SimBmbMemory`` does);
    * ``cmd.source := (source @@ chosen).resized`` is **truncating** whenever
      ``inputSourceWidth + routeWidth > outputSourceWidth``, which is the case
      for the core's five-way framebuffer arbiter (4 + 3 -> 4).  Masters
      therefore cannot rely on their own source surviving the arbiter; the core
      does not, it only uses source for routing.

    ``pending_inv_max`` and the invalidate/sync areas are not ported (see the
    module docstring).
    """

    def __init__(self, inputs_parameter, output_parameter: MemBusParameter,
                 lower_first_priority: bool, pending_inv_max: int = 0):
        self.inputs_parameter = list(inputs_parameter)
        self.output_parameter = output_parameter
        self.lower_first_priority = bool(lower_first_priority)
        self.port_count = len(self.inputs_parameter)
        if self.port_count < 1:
            raise ValueError("BmbArbiter needs at least one input")
        if pending_inv_max:
            raise NotImplementedError("the invalidation area is not ported")
        self.source_route_width = log2_up(self.port_count)
        if self.port_count > 1 and output_parameter.access.source_width < self.source_route_width:
            raise ValueError(
                f"outputParameter.sourceWidth {output_parameter.access.source_width} "
                f"cannot hold a {self.source_route_width}-bit route index")
        if any(q != self.inputs_parameter[0] for q in self.inputs_parameter):
            raise NotImplementedError(
                "BmbArbiter with heterogeneous input parameters is not ported; "
                "every call site in SpinalVoodoo passes structurally identical "
                "ones (FramebufferMemSubsystem.scala:52's three entries agree "
                "field for field)")
        super().__init__({
            "inputs": In(MemBusSignature(self.inputs_parameter[0])).array(self.port_count),
            "output": Out(MemBusSignature(output_parameter)),
        })

    def elaborate(self, platform):
        m = Module()
        n = self.port_count
        op = self.output_parameter

        if n == 1:
            # `val bypass = (portCount == 1) generate { io.output << io.inputs(0) }`
            connect(m, self.output, self.inputs[0])
            return m

        route_w = self.source_route_width
        arb = StreamArbiter(
            cmd_layout(op), n,
            arbitration="lowerFirst" if self.lower_first_priority else "roundRobin",
            lock="fragmentLock")
        m.submodules.arbiter = arb

        # Connect arbiter inputs: arbitrationFrom + last + weakAssignFrom.
        for port, inp in zip(arb.inputs, self.inputs):
            arbitration_from(m, port, inp.cmd)
            m.d.comb += port.payload.last.eq(inp.cmd.payload.last)
            _weak_assign(m, port.payload.fragment, inp.cmd.payload.fragment,
                         _CMD_WEAK, what="arbiter cmd")

        # io.output.cmd << arbiter.io.output, with `source` re-driven.
        connect_stream(m, self.output.cmd, arb.o)
        out_frag = self.output.cmd.payload.fragment
        arb_src = _get(arb.o.payload.fragment, "source")
        m.d.comb += out_frag.source.eq(Cat(arb.chosen, arb_src))

        # Responses: pure combinational demux on the route bits.
        rsp_frag = self.output.rsp.payload.fragment
        rsp_sel = rsp_frag.source[:route_w]
        for index, inp in enumerate(self.inputs):
            m.d.comb += [
                inp.rsp.valid.eq(self.output.rsp.valid & (rsp_sel == index)),
                inp.rsp.payload.last.eq(self.output.rsp.payload.last),
            ]
            _weak_assign(m, inp.rsp.payload.fragment, rsp_frag, _RSP_WEAK,
                         what="arbiter rsp", skip=("source",))
            if _has(inp.rsp.payload.fragment, "source"):
                m.d.comb += inp.rsp.payload.fragment.source.eq(rsp_frag.source[route_w:])
        # `io.inputs.map(_.rsp.ready).read(rspSel)`: Spinal's Vec.read emits a
        # case statement whose `default` arm is the LAST element, so an
        # out-of-range route index (5..7 for five ports) selects input 4.
        # Amaranth's `Array` would return 0 there; a Switch reproduces the
        # Verilog exactly.  Measured on BmbArbiter_spinal.v, caught by the
        # lockstep diff (the driver randomises rsp.source, so it does present
        # such indices).
        with m.Switch(rsp_sel):
            for index, inp in enumerate(self.inputs[:-1]):
                with m.Case(index):
                    m.d.comb += self.output.rsp.ready.eq(inp.rsp.ready)
            with m.Default():
                m.d.comb += self.output.rsp.ready.eq(self.inputs[-1].rsp.ready)
        return m


# --------------------------------------------------------------------------
# BmbSourceRemover.scala
# --------------------------------------------------------------------------


class SourceRemover(wiring.Component):
    """``spinal.lib.bus.bmb.BmbSourceRemover`` (``BmbSourceRemover.scala``).

    Collapses a multi-source bus to a single source by moving ``source`` into
    the *low* bits of ``context``; the response path unpacks it again.  Purely
    combinational -- no storage, no reordering, so it changes nothing about
    response ordering, it only renames the tag.

    Used by ``De10MemBackend.scala:480/497`` (the Avalon-MM bridge insists on
    one source).  The Gowin/Artix path reaches the same shape through
    ``BmbAligner``/``BmbLengthFixer`` instead.
    """

    @staticmethod
    def output_parameter(p: MemBusParameter) -> MemBusParameter:
        """``BmbSourceRemover.getOutputParameter``."""
        aggregated = replace(
            p.access.aggregated,
            context_width=p.access.aggregated.context_width + p.access.source_width)
        return p.with_access(p.access.with_single_source(aggregated))

    def __init__(self, p: MemBusParameter):
        self.p = p
        self.op = SourceRemover.output_parameter(p)
        super().__init__({
            "input": In(MemBusSignature(p)),
            "output": Out(MemBusSignature(self.op)),
        })

    def _context_layout(self) -> data.StructLayout:
        """``case class Context { source; context }`` -- source at the LSB."""
        f: dict = {}
        _opt(f, "source", self.p.access.source_width)
        _opt(f, "context", self.p.access.context_width)
        return data.StructLayout(f)

    def elaborate(self, platform):
        m = Module()
        layout = self._context_layout()
        in_cmd = self.input.cmd.payload.fragment
        out_cmd = self.output.cmd.payload.fragment

        cmd_context = Signal(layout, name="cmdContext")
        if _has(cmd_context, "source"):
            m.d.comb += cmd_context.source.eq(in_cmd.source)
        if _has(cmd_context, "context"):
            m.d.comb += cmd_context.context.eq(in_cmd.context)

        arbitration_from(m, self.output.cmd, self.input.cmd)
        m.d.comb += self.output.cmd.payload.last.eq(self.input.cmd.payload.last)
        # assignSomeByName: every field present on both sides, then the two
        # overrides below.
        for name in ("opcode", "address", "length", "data", "mask", "exclusive"):
            if _has(out_cmd, name) and _has(in_cmd, name):
                m.d.comb += out_cmd[name].eq(in_cmd[name])
        if _has(out_cmd, "context"):
            m.d.comb += out_cmd.context.eq(cmd_context.as_value())

        in_rsp = self.input.rsp.payload.fragment
        out_rsp = self.output.rsp.payload.fragment
        rsp_context = Signal(layout, name="rspContext")
        if _has(out_rsp, "context"):
            m.d.comb += rsp_context.as_value().eq(out_rsp.context)

        arbitration_from(m, self.input.rsp, self.output.rsp)
        m.d.comb += self.input.rsp.payload.last.eq(self.output.rsp.payload.last)
        for name in ("opcode", "data", "exclusive"):
            if _has(in_rsp, name) and _has(out_rsp, name):
                m.d.comb += in_rsp[name].eq(out_rsp[name])
        if _has(in_rsp, "source"):
            m.d.comb += in_rsp.source.eq(rsp_context.source)
        if _has(in_rsp, "context"):
            m.d.comb += in_rsp.context.eq(rsp_context.context)
        return m


# --------------------------------------------------------------------------
# BmbContextRemover.scala
# --------------------------------------------------------------------------


class ContextRemover(wiring.Component):
    """``spinal.lib.bus.bmb.BmbContextRemover`` (``BmbContextRemover.scala``).

    Strips ``source`` *and* ``context`` off the wire and keeps them in a local
    FIFO instead, restoring them on the response.  Unlike
    :class:`SourceRemover` this one has state, and it therefore **assumes
    responses come back in command order** -- the FIFO is not indexed by
    anything.  That is safe here only because the output has a single source.

    ``FramebufferPlaneReader.scala:100`` builds it with
    ``pendingMax = issueQueueDepth = 64``.  Note that with ``Config.voodoo1()``
    ``FramebufferPlaneReader.needBurstSplitter`` is **false**
    (``((800+1)/2)*4 = 1600 <= 2048``), so neither this nor
    :class:`AlignedSplitter` is actually elaborated in the shipping core; both
    are ported because the plan's parameter tables and the DE10/1280-wide
    configurations reach them.
    """

    @staticmethod
    def output_parameter(p: MemBusParameter) -> MemBusParameter:
        """``BmbContextRemover.getOutputParameter``."""
        return p.with_access(p.access.with_single_source(
            replace(p.access.aggregated, context_width=0)))

    def __init__(self, p: MemBusParameter, pending_max: int):
        self.p = p
        self.pending_max = pending_max
        self.op = ContextRemover.output_parameter(p)
        super().__init__({
            "input": In(MemBusSignature(p)),
            "output": Out(MemBusSignature(self.op)),
        })

    def _ctx_layout(self) -> data.StructLayout:
        f: dict = {}
        _opt(f, "source", self.p.access.source_width)
        _opt(f, "context", self.p.access.context_width)
        return data.StructLayout(f)

    def elaborate(self, platform):
        m = Module()
        # `val (fifoFork, cmdFork) = StreamFork2(io.input.cmd)` -- the default
        # StreamFork2 is *asynchronous*, so the two branches may consume the
        # beat on different cycles.
        fifo_fork, cmd_fork = fork2(m, self.input.cmd, synchronous=False,
                                    name="cmdFork")

        # `io.output.cmd << cmdFork` then `context`/`source` removeAssignments.
        # Both are zero-width on the output parameter, so the removals leave
        # nothing behind -- but the copy must still go by NAME, because the two
        # payloads have different shapes (see `_assign_some_by_name`).
        arbitration_from(m, self.output.cmd, cmd_fork)
        m.d.comb += self.output.cmd.payload.last.eq(cmd_fork.payload.last)
        _assign_some_by_name(m, self.output.cmd.payload.fragment,
                             cmd_fork.payload.fragment,
                             skip=("source", "context"))

        ctx_layout = self._ctx_layout()
        push_ctx = Signal(ctx_layout, name="pushCtx")
        fork_frag = fifo_fork.payload.fragment
        if _has(push_ctx, "source"):
            m.d.comb += push_ctx.source.eq(fork_frag.source)
        if _has(push_ctx, "context"):
            m.d.comb += push_ctx.context.eq(fork_frag.context)

        first = first_flag(m, fifo_fork, name="fifoFork_first")
        head = throw_when(m, fifo_fork, ~first)
        pop_ctx = m2s_pipe(m, queue(m, translate_with(m, head, push_ctx),
                                    self.pending_max, name="ctxFifo"),
                           name="ctxPipe")

        m.d.comb += pop_ctx.ready.eq(self.output.rsp.valid
                                     & self.output.rsp.payload.last
                                     & self.input.rsp.ready)

        held = halt_when(m, self.output.rsp, ~pop_ctx.valid)
        arbitration_from(m, self.input.rsp, held)
        m.d.comb += self.input.rsp.payload.last.eq(self.output.rsp.payload.last)
        _assign_some_by_name(m, self.input.rsp.payload.fragment,
                             self.output.rsp.payload.fragment,
                             skip=("source", "context"))
        in_rsp = self.input.rsp.payload.fragment
        pop = pop_ctx.payload
        if _has(in_rsp, "context"):
            m.d.comb += in_rsp.context.eq(_get(pop, "context", len(in_rsp.context)))
        if _has(in_rsp, "source"):
            m.d.comb += in_rsp.source.eq(_get(pop, "source", len(in_rsp.source)))
        return m


# --------------------------------------------------------------------------
# BmbSpecificBridges.scala -- BmbAligner
# --------------------------------------------------------------------------


class Aligner(wiring.Component):
    """``spinal.lib.bus.bmb.BmbAligner`` (``BmbSpecificBridges.scala``).

    Grows every access so it starts and ends on a ``1 << alignment_width``
    boundary, and hides the growth again on the way back:

    * **writes** gain padding beats whose ``mask`` is forced to 0 (``prePadding``
      before the real data, ``postPadding`` after);
    * **reads** issue the whole aligned burst and the response path *drops* the
      leading ``paddings`` beats and every beat past ``transfers``.

    Both the padding count and the true beat count ride in ``context``, which is
    why the output parameter is ``contextWidth + 1 + log2Up(beatCount) +
    log2Up(transferCount) + sourceWidth`` bits wide.

    ``CoreArtix.scala:226`` / ``CoreGowin.scala:44`` build it as the first half
    of ``BmbToAxi4Mem(p, burstLog2 = 8)``, i.e. 256-byte alignment.

    The output burst carries a **constant address** across its beats (only
    ``last`` moves), unlike the per-beat addresses the framebuffer masters
    generate -- the AXI bridge downstream is what turns it into a burst.
    """

    @staticmethod
    def bypass(ip: MemBusAccessParameter, alignment_width: int) -> bool:
        """``BmbAligner.bypass`` -- already aligned by construction."""
        return (1 << alignment_width) <= ip.byte_count and not ip.alignment.allow_byte

    @staticmethod
    def output_access_parameter(ip: MemBusAccessParameter,
                                alignment_width: int) -> MemBusAccessParameter:
        """``BmbAligner.outputParameter``."""
        beat_count = (1 << alignment_width) // ip.byte_count
        transfer_count = ip.transfer_beat_count
        read_context = (log2_up(beat_count) + log2_up(transfer_count)) if ip.can_read else 0
        aggregated = ip.aggregated
        if not Aligner.bypass(ip, alignment_width):
            op = ip.with_single_source(replace(
                aggregated,
                length_width=aggregated.length_width + 1,
                context_width=aggregated.context_width + 1 + read_context + ip.source_width))
        else:
            op = ip
        return op.sources_transform(lambda s: replace(s, alignment_min=alignment_width))

    def __init__(self, ip: MemBusParameter, alignment_width: int):
        self.ip = ip
        self.alignment_width = alignment_width
        self.op = MemBusParameter(
            Aligner.output_access_parameter(ip.access, alignment_width),
            ip.invalidation)
        super().__init__({
            "input": In(MemBusSignature(ip)),
            "output": Out(MemBusSignature(self.op)),
        })

    def _context_layout(self) -> data.StructLayout:
        """``case class Context { write; paddings; transfers; source; input }``."""
        a = self.ip.access
        beat_count = (1 << self.alignment_width) // a.byte_count
        f: dict = {"write": unsigned(1)}
        if a.can_read:
            _opt(f, "paddings", log2_up(beat_count))
            _opt(f, "transfers", log2_up(a.transfer_beat_count))
        _opt(f, "source", a.source_width)
        _opt(f, "input", a.context_width)
        return data.StructLayout(f)

    def elaborate(self, platform):
        m = Module()
        a = self.ip.access
        aw = self.alignment_width
        in_cmd = self.input.cmd.payload.fragment
        out_cmd = self.output.cmd.payload.fragment

        if Aligner.bypass(a, aw):
            connect(m, self.output, self.input)
            m.d.comb += out_cmd.address[:aw].eq(0)
            m.d.comb += out_cmd.length[:aw].eq((1 << aw) - 1)
            return m

        beat_count = (1 << aw) // a.byte_count
        transfer_count = a.transfer_beat_count
        ctx_layout = self._context_layout()
        beat_w = log2_up(beat_count)

        # ---------------- cmdLogic ----------------
        context = Signal(ctx_layout, name="context")
        paddings = in_cmd.address[a.word_range_length:aw]
        is_write = in_cmd.opcode == CMD_WRITE
        is_read = in_cmd.opcode == CMD_READ

        m.d.comb += [
            self.output.cmd.valid.eq(self.input.cmd.valid),
            out_cmd.address.eq(Cat(Const(0, aw), in_cmd.address[aw:])),
            out_cmd.opcode.eq(in_cmd.opcode),
            out_cmd.length.eq(
                _uadd(in_cmd.address[:aw], in_cmd.length, self.op.access.length_width)
                | Const((1 << aw) - 1, self.op.access.length_width)),
            self.output.cmd.payload.last.eq(0),
            context.write.eq(is_write),
            out_cmd.context.eq(context.as_value()),
        ]
        if _has(context, "input"):
            m.d.comb += context.input.eq(in_cmd.context)
        if _has(context, "source"):
            m.d.comb += context.source.eq(in_cmd.source)

        input_ready_ok = Signal(name="inputReadyOk")
        m.d.comb += input_ready_ok.eq(0)
        m.d.comb += self.input.cmd.ready.eq(self.output.cmd.ready & input_ready_ok)

        cmd_first = first_flag(m, self.input.cmd, name="inputCmd_first")

        if a.can_write:
            beat_counter = Signal(beat_w, name="cmd_beatCounter")
            m.d.sync += beat_counter.eq(
                _uadd(beat_counter, (fire(self.output.cmd) & is_write), beat_w))
            pre_padding = Signal(name="prePadding")
            m.d.comb += pre_padding.eq(is_write & cmd_first & (beat_counter < paddings))
            post_padding = Signal(init=0, name="postPadding")
            with m.If(~pre_padding & fire(self.output.cmd) & self.input.cmd.payload.last):
                m.d.sync += post_padding.eq(1)
            with m.If(self.input.cmd.ready):
                m.d.sync += post_padding.eq(0)
            with m.If(self.input.cmd.payload.last & (beat_counter == beat_count - 1)):
                m.d.comb += self.output.cmd.payload.last.eq(1)
            m.d.comb += out_cmd.data.eq(in_cmd.data)
            m.d.comb += out_cmd.mask.eq(Mux(~(pre_padding | post_padding), in_cmd.mask, 0))
            with m.If(~pre_padding & ~(self.input.cmd.payload.last
                                       & (beat_counter != beat_count - 1))):
                m.d.comb += input_ready_ok.eq(1)
        if a.can_read:
            with m.If(is_read):
                m.d.comb += self.output.cmd.payload.last.eq(1)
                m.d.comb += input_ready_ok.eq(1)
            m.d.comb += context.paddings.eq(paddings)
            m.d.comb += context.transfers.eq(
                transfer_beat_count_minus_one(self.ip, in_cmd))

        # ---------------- rspLogic ----------------
        out_rsp = self.output.rsp.payload.fragment
        in_rsp = self.input.rsp.payload.fragment
        rsp_ctx = Signal(ctx_layout, name="rspContext")
        m.d.comb += rsp_ctx.as_value().eq(out_rsp.context)

        drop = Signal(name="drop")
        m.d.comb += drop.eq(0)
        kept = throw_when(m, self.output.rsp, drop)
        arbitration_from(m, self.input.rsp, kept)
        m.d.comb += [
            self.input.rsp.payload.last.eq(0),
            in_rsp.opcode.eq(out_rsp.opcode),
        ]
        if _has(in_rsp, "context"):
            m.d.comb += in_rsp.context.eq(rsp_ctx.input)
        if _has(in_rsp, "source"):
            m.d.comb += in_rsp.source.eq(rsp_ctx.source)

        if a.can_read:
            rsp_beat = Signal(beat_w, name="rsp_beatCounter")
            with m.If(fire(self.output.rsp)):
                m.d.sync += rsp_beat.eq(_uadd(rsp_beat, ~rsp_ctx.write, beat_w))
            tc_w = log2_up(transfer_count + 1)
            transfer_counter = Signal(tc_w, name="transferCounter")
            with m.If(fire(self.input.rsp)):
                m.d.sync += transfer_counter.eq(_uadd(transfer_counter, 1, tc_w))
            with m.If(fire(self.output.rsp) & self.output.rsp.payload.last):
                m.d.sync += transfer_counter.eq(0)
            rsp_first = first_flag(m, self.input.rsp, name="inputRsp_first")
            with m.If(~rsp_ctx.write
                      & ((rsp_first & (rsp_beat[:len(rsp_ctx.paddings)] < rsp_ctx.paddings))
                         | (transfer_counter > rsp_ctx.transfers))):
                m.d.comb += drop.eq(1)
            with m.If(transfer_counter == rsp_ctx.transfers):
                m.d.comb += self.input.rsp.payload.last.eq(1)
            m.d.comb += in_rsp.data.eq(out_rsp.data)
        if a.can_write:
            with m.If(rsp_ctx.write):
                m.d.comb += self.input.rsp.payload.last.eq(1)
        return m


# --------------------------------------------------------------------------
# BmbSpecificBridges.scala -- BmbLengthFixer
# --------------------------------------------------------------------------


class LengthFixer(wiring.Component):
    """``spinal.lib.bus.bmb.BmbLengthFixer`` (``BmbSpecificBridges.scala``).

    Chops a burst into fixed ``1 << fixed_width``-byte bursts.  **Requires the
    input to already be aligned to that size** -- which is exactly why
    ``BmbToAxi4Mem`` puts an :class:`Aligner` in front of it
    (``CoreArtix.scala:226-227``).

    Two behaviours to preserve:

    * on a read the output command can fire *before* the input one does
      (``io.input.cmd.ready := io.output.cmd.ready && (isWrite || context.last)``),
      so a single input read beat is replayed as ``splitCount`` output bursts;
    * the input's ``last`` is carried in ``context.last`` and only the final
      sub-burst's response is forwarded with ``last`` set, while non-final
      *write* responses are swallowed entirely
      (``takeWhen(!context.write || context.last && rsp.last)``).
    """

    @staticmethod
    def output_access_parameter(ip: MemBusAccessParameter,
                                fixed_width: int) -> MemBusAccessParameter:
        """``BmbLengthFixer.outputParameter``."""
        return ip.with_single_source(replace(
            ip.aggregated,
            length_width=fixed_width,
            alignment_min=fixed_width,
            context_width=ip.context_width + 2 + ip.source_width))

    def __init__(self, ip: MemBusParameter, fixed_width: int):
        self.ip = ip
        self.fixed_width = fixed_width
        self.op = MemBusParameter(
            LengthFixer.output_access_parameter(ip.access, fixed_width),
            ip.invalidation)
        self.beat_count = (1 << fixed_width) // ip.access.byte_count
        self.split_count = ip.access.transfer_beat_count // self.beat_count
        super().__init__({
            "input": In(MemBusSignature(ip)),
            "output": Out(MemBusSignature(self.op)),
            "output_burst_last": Out(1),
        })

    def _context_layout(self) -> data.StructLayout:
        """``case class Context { last; write; source; input }``."""
        a = self.ip.access
        f: dict = {"last": unsigned(1), "write": unsigned(1)}
        _opt(f, "source", a.source_width)
        _opt(f, "input", a.context_width)
        return data.StructLayout(f)

    def elaborate(self, platform):
        m = Module()
        a = self.ip.access
        opa = self.op.access
        fw = self.fixed_width
        in_cmd = self.input.cmd.payload.fragment
        out_cmd = self.output.cmd.payload.fragment
        beat_w = log2_up(self.beat_count)
        split_w = log2_up(self.split_count)

        fixed_address = in_cmd.address[BOUNDARY_WIDTH:]
        base_address = in_cmd.address[opa.length_width:BOUNDARY_WIDTH]

        beat_counter = Signal(beat_w, name="beatCounter")
        split_counter = Signal(split_w, name="splitCounter")

        context = Signal(self._context_layout(), name="context")
        is_write = in_cmd.opcode == CMD_WRITE
        m.d.comb += [
            context.last.eq(split_counter == in_cmd.length[fw:]),
            context.write.eq(is_write),
        ]
        if _has(context, "input"):
            m.d.comb += context.input.eq(in_cmd.context)
        if _has(context, "source"):
            m.d.comb += context.source.eq(in_cmd.source)

        m.d.comb += [
            self.output.cmd.valid.eq(self.input.cmd.valid),
            self.output.cmd.payload.last.eq(
                self.input.cmd.payload.last | (beat_counter == self.beat_count - 1)),
            out_cmd.address.eq(Cat(
                Const(0, opa.length_width),
                _uadd(base_address, split_counter, len(base_address)),
                fixed_address)),
            out_cmd.context.eq(context.as_value()),
            out_cmd.opcode.eq(in_cmd.opcode),
            out_cmd.length.eq((1 << fw) - 1),
            self.output_burst_last.eq(context.last),
            self.input.cmd.ready.eq(self.output.cmd.ready & (is_write | context.last)),
        ]
        if _has(out_cmd, "source"):
            m.d.comb += out_cmd.source.eq(0)
        if a.can_write:
            m.d.comb += [out_cmd.data.eq(in_cmd.data), out_cmd.mask.eq(in_cmd.mask)]

        with m.If(fire(self.output.cmd)):
            m.d.sync += beat_counter.eq(_uadd(beat_counter, is_write, beat_w))
            with m.If(self.output.cmd.payload.last):
                m.d.sync += split_counter.eq(_uadd(split_counter, 1, split_w))
        with m.If(fire(self.input.cmd) & self.input.cmd.payload.last):
            m.d.sync += split_counter.eq(0)

        # ---------------- rspLogic ----------------
        out_rsp = self.output.rsp.payload.fragment
        in_rsp = self.input.rsp.payload.fragment
        rsp_ctx = Signal(self._context_layout(), name="rspContext")
        m.d.comb += rsp_ctx.as_value().eq(out_rsp.context)

        kept = take_when(m, self.output.rsp,
                         ~rsp_ctx.write | (rsp_ctx.last & self.output.rsp.payload.last))
        arbitration_from(m, self.input.rsp, kept)
        m.d.comb += [
            self.input.rsp.payload.last.eq(self.output.rsp.payload.last & rsp_ctx.last),
            in_rsp.opcode.eq(out_rsp.opcode),
        ]
        if _has(in_rsp, "source"):
            m.d.comb += in_rsp.source.eq(_get(rsp_ctx, "source", len(in_rsp.source)))
        if _has(in_rsp, "data"):
            m.d.comb += in_rsp.data.eq(out_rsp.data)
        if _has(in_rsp, "context"):
            m.d.comb += in_rsp.context.eq(rsp_ctx.input)
        return m


# --------------------------------------------------------------------------
# BmbSpecificBridges.scala -- BmbAlignedSpliter
# --------------------------------------------------------------------------


class AlignedSplitter(wiring.Component):
    """``spinal.lib.bus.bmb.BmbAlignedSpliter`` (``BmbSpecificBridges.scala``).

    Note the Scala spelling ``Spliter``.  Breaks a burst into pieces no longer
    than ``length_max`` bytes that never cross a ``length_max`` address
    boundary.  Unlike :class:`LengthFixer` the pieces have *different* lengths:
    a head (from the start offset to the boundary), zero or more full-length
    bodies, and a tail -- selected by the ``firstSplit ## lastSplit`` mux.

    ``FramebufferPlaneReader.scala:98`` builds it with
    ``lengthMax = 1 << c.memBurstLengthWidth`` when a framebuffer span could
    exceed one burst.  With ``Config.voodoo1()`` that predicate is false
    (``((800+1)/2)*4 = 1600 <= 2048``), so the shipping core does not contain
    one; see :class:`ContextRemover`.
    """

    @staticmethod
    def output_access_parameter(ip: MemBusAccessParameter,
                                length_max: int) -> MemBusAccessParameter:
        """``BmbAlignedSpliter.outputParameter``."""
        return ip.with_single_source(replace(
            ip.aggregated,
            length_width=log2_up(length_max),
            context_width=ip.context_width + 2 + ip.source_width))

    def __init__(self, ip: MemBusParameter, length_max: int):
        self.ip = ip
        self.length_max = length_max
        self.op = MemBusParameter(
            AlignedSplitter.output_access_parameter(ip.access, length_max),
            ip.invalidation)
        a = ip.access
        self.beat_count_max = length_max // a.byte_count
        self.split_count_max = (a.transfer_beat_count // self.beat_count_max
                                + (1 if a.alignment.allow_word else 0))
        super().__init__({
            "input": In(MemBusSignature(ip)),
            "output": Out(MemBusSignature(self.op)),
            "output_burst_last": Out(1),
        })

    def _context_layout(self) -> data.StructLayout:
        """``case class Context { source; last; write; input }``.

        Field order differs from :class:`LengthFixer`'s -- ``source`` first
        here, ``last`` first there -- so the two packings are *not*
        interchangeable even though the widths match.
        """
        a = self.ip.access
        f: dict = {}
        _opt(f, "source", a.source_width)
        f["last"] = unsigned(1)
        f["write"] = unsigned(1)
        _opt(f, "input", a.context_width)
        return data.StructLayout(f)

    def elaborate(self, platform):
        m = Module()
        a = self.ip.access
        opa = self.op.access
        in_cmd = self.input.cmd.payload.fragment
        out_cmd = self.output.cmd.payload.fragment

        split_bits = log2_up(self.length_max)          # splitRange = split_bits-1 downto 0
        address_low = split_bits                       # addressRange.low
        beat_w = log2_up(self.beat_count_max)
        split_w = log2_up(self.split_count_max)

        beat_counter = Signal(beat_w, name="beatCounter")
        split_counter = Signal(split_w, name="splitCounter")

        addr_split = in_cmd.address[:split_bits]
        head_length_max = _usub(Const(self.length_max - 1, split_bits), addr_split,
                                max(split_bits, len(in_cmd.length)))
        last_address_w = max(split_bits, a.length_width + 1)
        last_address = _uadd(addr_split, Cat(in_cmd.length, Const(0, 1)), last_address_w)
        tail_length = last_address[:split_bits]
        split_count = last_address[split_bits:]

        first_split = Signal(init=1, name="firstSplit")
        with m.If(fire(self.output.cmd) & self.output.cmd.payload.last):
            m.d.sync += first_split.eq(0)
        last_split = Signal(name="lastSplit")
        m.d.comb += last_split.eq(split_counter == split_count)

        address_base = Signal(a.address_width, name="addressBase")
        m.d.comb += address_base.eq(in_cmd.address)
        with m.If(~first_split):
            m.d.comb += address_base[:split_bits].eq(0)

        beats_w = max(log2_up(self.beat_count_max + 1),
                      split_bits - a.word_range_length)
        beats_in_split = _usub(
            Const(self.length_max // a.byte_count, beats_w),
            Mux(first_split, in_cmd.address[a.word_range_length:split_bits], 0),
            beats_w)

        context = Signal(self._context_layout(), name="context")
        is_write = in_cmd.opcode == CMD_WRITE
        m.d.comb += [context.last.eq(last_split), context.write.eq(is_write)]
        if _has(context, "input"):
            m.d.comb += context.input.eq(in_cmd.context)
        if _has(context, "source"):
            m.d.comb += context.source.eq(in_cmd.source)

        length = Signal(opa.length_width, name="outLength")
        with m.Switch(Cat(last_split, first_split)):       # firstSplit ## lastSplit
            with m.Case(0b10):
                m.d.comb += length.eq(head_length_max)
            with m.Case(0b00):
                m.d.comb += length.eq(self.length_max - 1)
            with m.Case(0b01):
                m.d.comb += length.eq(tail_length)
            with m.Case(0b11):
                m.d.comb += length.eq(in_cmd.length)

        m.d.comb += [
            self.output.cmd.valid.eq(self.input.cmd.valid),
            self.output.cmd.payload.last.eq(
                self.input.cmd.payload.last
                | (beat_counter == _usub(beats_in_split, 1, beats_w))),
            out_cmd.address.eq(add_to_address(
                address_base, Cat(Const(0, address_low), split_counter), self.ip)),
            out_cmd.context.eq(context.as_value()),
            out_cmd.opcode.eq(in_cmd.opcode),
            out_cmd.length.eq(length),
            self.output_burst_last.eq(context.last),
            self.input.cmd.ready.eq(self.output.cmd.ready & (is_write | context.last)),
        ]
        if _has(out_cmd, "source"):
            m.d.comb += out_cmd.source.eq(0)
        if a.can_write:
            m.d.comb += [out_cmd.data.eq(in_cmd.data), out_cmd.mask.eq(in_cmd.mask)]

        with m.If(fire(self.output.cmd)):
            m.d.sync += beat_counter.eq(_uadd(beat_counter, is_write, beat_w))
            with m.If(self.output.cmd.payload.last):
                m.d.sync += split_counter.eq(_uadd(split_counter, 1, split_w))
                m.d.sync += beat_counter.eq(0)
        with m.If(fire(self.input.cmd) & self.input.cmd.payload.last):
            m.d.sync += split_counter.eq(0)
            m.d.sync += first_split.eq(1)

        # ---------------- rspLogic ----------------
        out_rsp = self.output.rsp.payload.fragment
        in_rsp = self.input.rsp.payload.fragment
        rsp_ctx = Signal(self._context_layout(), name="rspContext")
        m.d.comb += rsp_ctx.as_value().eq(out_rsp.context)

        kept = take_when(m, self.output.rsp,
                         ~rsp_ctx.write | (rsp_ctx.last & self.output.rsp.payload.last))
        arbitration_from(m, self.input.rsp, kept)
        m.d.comb += [
            self.input.rsp.payload.last.eq(self.output.rsp.payload.last & rsp_ctx.last),
            in_rsp.opcode.eq(out_rsp.opcode),
        ]
        if _has(in_rsp, "source"):
            m.d.comb += in_rsp.source.eq(_get(rsp_ctx, "source", len(in_rsp.source)))
        if a.can_read and _has(in_rsp, "data"):
            m.d.comb += in_rsp.data.eq(out_rsp.data)
        if _has(in_rsp, "context"):
            m.d.comb += in_rsp.context.eq(rsp_ctx.input)
        return m


# --------------------------------------------------------------------------

MEMBUS_NOTES = """
Bmb semantics the Amaranth ports must honour (measured against SpinalHDL
1.15.0 sources and the SpinalVoodoo call sites, 2026-09-20).

1. Response ordering.  `Bmb.rsp` is documented "Out of order across source" and
   in order within one source.  Nothing in the bus enforces it; every consumer
   that needs ordering either uses a single source (`ContextRemover`,
   `LengthFixer`, `AlignedSplitter` all emit `withSingleSource` parameters) or
   tags with `source` (`Arbiter`).  A slave that reorders within a source
   breaks `ContextRemover`'s FIFO, which is index-free.

2. `sourceWidth` is derived, not stored: `log2Up(max(sourceId) + 1)`.  A single
   source therefore means a **zero-bit** source field and no Verilog port at
   all.  Same for `contextWidth = 0`, which is what every `*BmbParams` in the
   core declares -- so on the core's own buses neither `source`-in-context nor
   `context` exists until an adapter creates one.

3. `Arbiter` truncates.  `cmd.source := (source @@ chosen).resized` targets a
   field of `outputParameter.sourceWidth` bits.  CoreSim's five-way arbiter has
   sourceWidth 4 and routeWidth 3, so a master's own 4-bit source is cut to its
   bottom bit.  Responses are demuxed on `rsp.source(routeWidth-1 downto 0)`
   with `Vec.read`, whose generated `case` has the LAST input as its `default`
   arm -- so a route index >= portCount selects the last input, not nothing.
   The Amaranth port uses a Switch with the same default; the lockstep diff
   caught the first version, which used `Array` and returned 0 there.

4. `last` alignment.  On the input side `last` marks the end of the master's
   burst; `LengthFixer`/`AlignedSplitter` retime it to the end of each
   *sub-burst* and stash the input's own last in `context.last`.  `Aligner`
   sets `output.cmd.last` unconditionally for reads (one beat) but counts beats
   for writes.  Getting this wrong deadlocks the arbiter, whose `fragmentLock`
   holds the grant until a `last` beat fires.

5. `mask` is a *write* field: it exists only when `canWrite && canMask`, and
   reads always return the full data word.  `Aligner` forces it to 0 on padding
   beats rather than suppressing the beat, so a slave must honour a zero mask
   as "write nothing".

6. Write-burst addressing in this design.  Bmb itself carries `address` on
   every command beat.  `FramebufferPlaneBuffer` increments it per beat and
   `SimBmbMemory` writes to the current beat's address, so the effective
   contract here is "address per beat".  `Aligner` and `LengthFixer` emit a
   *constant* address across a burst instead, matching the AXI bridge that
   follows them -- do not put `SimBmbMemory` directly behind either of them and
   expect a burst write to land correctly.

7. Context packing.  `B(context)` uses `MultiData.asBits`, which puts the
   FIRST declared field at the LSB.  The four context bundles are:
     SourceRemover   {source, context}
     ContextRemover  {source, context}       (FIFO only, never on the wire)
     Aligner         {write, paddings, transfers, source, input}
     LengthFixer     {last, write, source, input}
     AlignedSpliter  {source, last, write, input}
   `LengthFixer` and `AlignedSpliter` have the same width and different order.

8. `Fragment` declares `last` before `fragment`, so a packed cmd/rsp payload has
   `last` at bit 0.  (`avoodoo/lib/fragment.py` currently spells the layout the
   other way round; it does not matter for port naming, only for `asBits`.)
"""
