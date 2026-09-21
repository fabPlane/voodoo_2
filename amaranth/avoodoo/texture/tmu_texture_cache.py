"""``TmuTextureCache`` -- the TMU's line-fill texel cache.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/texture/TmuTextureCache.scala``
(SpinalHDL 1.15.0, 996 lines; the second-largest module of the core).  The
Scala file also holds ``TmuNoTextureCache`` (the uncached direct-read path
built when ``!c.useTexFillCache``); ``Config.voodoo1()`` never builds it, so
it is not ported here (``Tmu`` refuses ``use_tex_fill_cache=False``).

Parameters, as ``Config.voodoo1()`` fixes them
----------------------------------------------

=========================  =====  =========================================
``c.texFillLineWords``     8      one cache line = 8 words = 32 bytes
``c.texFillCacheSlots``    16     slots (lines) in total
``c.texFillWayCount``      2      -> ``setCount = 8``, ``setBits = 3``,
                                  ``wayBits = 1``, ``slotBits = 4``
``c.texFillXorIndex``      False  set index = line address bits [5:8]
``c.texFillRequestWindow`` 16     depth of the request FIFO
``c.memBurstLengthWidth``  11     ``fillLength = 31`` (bytes - 1)
``c.addressWidth``         26
=========================  =====  =========================================

The packed texture layout (``packedTexLayout``) spreads a texel's four
bilinear neighbours over four *banks* so that one lookup can read all four
taps in one cycle: each bank holds a 32-bit word per (set, entry), two
16-bit texels per word (``pair`` selects the half).  ``Tmu`` computes the
tap addresses, the bank each tap lives in (``tapPackedBank``) and its half
(``tapPackedPair``); this module only stores and looks up.

Structure map (Scala names, kept as Amaranth signal names)
----------------------------------------------------------

::

   io.sampleRequest --queue(16)--> reqStream --translateWith(+epoch)--> lookupSource
                                                                          |
      stage L (lookup)   lookupCtx: per tap active/bank/set/lineBase/entry |
                         metaMem[tap].readSync(tapSet)   x4                 |  fires when
                         bankMem[bank][way].readSync(bankIndex) x4x2       |  !lookupBlocked
                         lookupRspValid = RegNext(fire), lookupRspCtx      |
      stage C (compare)  arrivedCompare: per tap hit (valid&ready&epoch&base)
                         compareHold / compareHoldValid  (a one-entry skid)
                         compareStage/compareValid = hold ? hold : arrived
                         allHits -> hitRsp -> io.sampleFetch        (hit path, 0 extra cycles)
                         anyMiss -> compareFillStart -> owner + fill (miss path)
      owner              the one request being resolved by fills: sample, epoch,
                         resolved[4], tapMeta[4], texels[4]
      fill               the one line fill in flight: set/way/slot/setMeta, req
                         (CachedReq), rspCount, texBase/End/Shift[9], decode*[4]
                         io.texRead.cmd <- fillStart (compareFillStart | continueFillStart)
                         io.texRead.rsp -> fillRsp (halted while !fill.activeReg)
                           -> bankMem writes (packedLocation22 per byte)
                           -> owner texel capture (ownerFillCaptureMask)
                           -> fillFetchRaw --queue(1)--> fillFetchRsp -> io.sampleFetch
                         last beat: metaMem <- readySetMeta; continuePending*
                         (next unresolved tap) or owner.active := False
      initSweep          after reset, one metaMem write per set clearing it
                         (``initSweepActive``; lookups are blocked meanwhile)
      currentEpoch       8-bit, +1 on io.invalidate; a line hits only when its
                         stored epoch equals the request's

Memories
--------

* ``metaMem`` x4 (one per tap): ``setCount`` x ``SetMeta`` (73 bits for
  voodoo1: 2 ways x {base 26, valid, ready, epoch 8} + victim 1).  All four
  are written together (``metaWrite*``) and read independently, one per tap.
* ``bankMem`` x4 banks x ``wayCount`` ways: ``setCount * lineWords`` (64) x
  32 bits.
* ``reqStream``: ``StreamFifo(SampleRequest, 16)`` -- inside
  :class:`avoodoo.lib.stream.Fifo`.
* ``fillFetchRsp``: ``queue(1)`` -- an ``m2sPipe`` with flush, no RAM.

Every RAM here falls under the Spinal build's
``force_distributed_fifo_ram.py`` rule (width >= 24, depth <= 64), applied
through :func:`avoodoo.lib.stream.spinal_fifo_ram_attrs`: 73x8, 32x64 and
the ~700x16 request FIFO all get ``syn_ramstyle = "distributed_ram"`` so the
Gowin build does not spend BSRAM on them (plan 8.3).  None has an ``init``
in Spinal; Amaranth attaches an all-zero one (``init=[]``), which
``gen/gen_core_sim.py``'s ``strip_meminit`` removes for the system build and
which the lockstep diff tolerates because Verilator zero-fills the Spinal
arrays too (13 small ``initial`` blocks in the module's own Verilog).  Reads
are ``readSync`` with an enable and **read-before-write** (separate
``always`` blocks in the Spinal Verilog), i.e. ``Memory.read_port(domain=
"sync")`` with no transparency.

Behaviour worth knowing before using this module (all reproduced 1:1, all
pinned by the lockstep diff and ``tests/test_tmu_texture_cache.py``)
-----------------------------------------------------------------------

* **A miss answers with the pre-capture texel for every tap resolved on the
  emitting beat.**  ``fillFetchRaw.payload := ownerRspCommitted`` sends the
  ``owner.texels`` *registers* (0 for each missed tap since
  ``compareConsumeMiss``) on the very beat whose ``fillRsp.fire`` would load
  them, and ``queue(1)`` is an ``m2sPipe`` that latches on ``ready``; so a
  point sample on a cold line returns texel 0 and the same sample hits with
  the real texel next time; a cold bilinear sample gets its earlier-word
  taps right and the last-resolved one(s) stale.  ``ownerRsp`` (the version
  with the capture mux) is dead code in the Scala.  The Console builds
  with ``useTexFillCache = false`` (``TmuNoTextureCache``); ``CoreSim``'s
  ``Config.voodoo1()`` default is ``true``.  Not fixed here (plan §1: port
  first).
* **A tap that decodes as outside every LOD livelocks the cache**: its
  (bank, pair) is never written, ``continuePendingValid`` re-arms a fill of
  the same line after every fill, and ``owner.active`` blocks all lookups.
  ``Tmu`` guarantees consistency by wrapping/clamping coordinates before
  forming addresses; test stimulus has to as well.
* **Two active taps in one bank at different addresses** (the formal
  ``assume(noTapBankAlias)``) read the OR of two bank indices on a hit.  The
  16-bit bank formula ``offs[shift] | offs[shift+1]`` folds the top s bit
  into the row parity, so e.g. every bilinear sample of a 2x2 16-bit LOD
  aliases.  Fills and hit/miss decisions are unaffected (per-tap meta RAMs).
* ``io.busy`` drops for one cycle after a push into the empty request FIFO
  (``StreamFifo`` latency 2 raises ``pop.valid`` two cycles after the push).

Registers: Spinal ``Reg(...)`` without ``init`` is ``reset_less=True`` here
(plan §6): ``compareHold``, ``lookupRspCtx``, ``owner.sample/tapMeta/texels``,
``fill.setMeta/req/passthrough/texBase/texEnd/texShift``.  Everything with
``init`` / ``RegInit`` resets.

Dropped: ``spinal.core.formal`` (the ``assume(noTapBankAlias)`` and the two
``cover``\\ s) and the ``markSimulationOnly``/``setName("dbg*")`` debug aliases.
One of those aliases is *visible at the ports*: ``io.sampleFetch.valid
.setName("dbgDataValid")`` renames the output itself in the Spinal Verilog,
so ``tools/diff/registry.py`` maps ``dbgDataValid`` -> ``sample_fetch__valid``
(the only override this module needs).
``formalStrong`` / ``formalHotInit`` are kept as constructor arguments
(``formal_hot_init`` changes the reset value of ``initSweepActive`` and
writes ``hotMeta()`` to set 0 on the first cycle), so the formal DUTs can be
rebuilt as unit tests.
"""

from __future__ import annotations

from amaranth import Array, Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import data, memory, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.membus_params import tmu_bmb_params
from avoodoo.lib.intops import u_add, u_sub, zext
from avoodoo.lib.membus import CMD_READ, MemBusSignature, log2_up
from avoodoo.lib.stream import (Stream, fire, halt_when, mux_oh, oh_to_uint, queue,
                                spinal_fifo_ram_attrs, translate_with)
from avoodoo.texture.tmu_types import (
    CachedReq,
    SampleFetch,
    SampleRequest,
    TmuPassthrough,
    line_base22_of,
    packed_location22,
)

__all__ = [
    "TmuTextureCacheSlotMeta",
    "TmuTextureCacheSetMeta",
    "TmuTextureCacheLookupCtx",
    "TmuTextureCacheCompareStage",
    "TmuTextureCacheReplayReq",
    "TmuTextureCacheFillStart",
    "TmuTextureCacheGeometry",
    "point_or_bilinear_addr",
    "point_or_bilinear_bank",
    "packed_pair_sel",
    "bank_texel",
    "texel16",
    "TmuTextureCache",
]

BANK_COUNT = 4
TAP_COUNT = 4


# ---------------------------------------------------------------------------
# bundles
# ---------------------------------------------------------------------------


class TmuTextureCacheSlotMeta(data.StructLayout):
    """``case class TmuTextureCacheSlotMeta(c)`` -- one way of one set."""

    def __init__(self, c: Config) -> None:
        super().__init__({
            "base": unsigned(c.address_width),
            "valid": unsigned(1),
            "ready": unsigned(1),
            "epoch": unsigned(8),
        })


class TmuTextureCacheSetMeta(data.StructLayout):
    """``case class TmuTextureCacheSetMeta(c, wayCount)`` -- one metaMem word."""

    def __init__(self, c: Config, way_count: int) -> None:
        self.way_count = way_count
        super().__init__({
            "ways": data.ArrayLayout(TmuTextureCacheSlotMeta(c), way_count),
            "victim": unsigned(max(1, log2_up(way_count))),
        })


class TmuTextureCacheLookupCtx(data.StructLayout):
    """``case class TmuTextureCacheLookupCtx(c, setBits, bankEntryWidth)``."""

    def __init__(self, c: Config, set_bits: int, bank_entry_width: int) -> None:
        super().__init__({
            "sample": SampleRequest(c),
            "epoch": unsigned(8),
            "tap_active": data.ArrayLayout(unsigned(1), TAP_COUNT),
            "tap_bank": data.ArrayLayout(unsigned(2), TAP_COUNT),
            "tap_set": data.ArrayLayout(unsigned(set_bits), TAP_COUNT),
            "tap_line_base": data.ArrayLayout(unsigned(c.address_width), TAP_COUNT),
            "tap_bank_entry": data.ArrayLayout(unsigned(bank_entry_width), TAP_COUNT),
        })


class TmuTextureCacheCompareStage(data.StructLayout):
    """``case class TmuTextureCacheCompareStage(c, setBits, bankEntryWidth, wayCount)``."""

    def __init__(self, c: Config, set_bits: int, bank_entry_width: int,
                 way_count: int) -> None:
        super().__init__({
            "sample": SampleRequest(c),
            "epoch": unsigned(8),
            "all_hits": unsigned(1),
            "any_miss": unsigned(1),
            "tap_hit": data.ArrayLayout(unsigned(1), TAP_COUNT),
            "tap_set_meta": data.ArrayLayout(TmuTextureCacheSetMeta(c, way_count), TAP_COUNT),
            "tap_data": data.ArrayLayout(unsigned(32), TAP_COUNT),
        })


class TmuTextureCacheReplayReq(data.StructLayout):
    """``case class TmuTextureCacheReplayReq(c)`` -- a request stamped with its epoch."""

    def __init__(self, c: Config) -> None:
        super().__init__({
            "sample": SampleRequest(c),
            "epoch": unsigned(8),
        })


class TmuTextureCacheFillStart(data.StructLayout):
    """``case class TmuTextureCacheFillStart(c, slotBits, setBits, bankEntryWidth, wayBits, wayCount)``."""

    def __init__(self, c: Config, slot_bits: int, set_bits: int, bank_entry_width: int,
                 way_bits: int, way_count: int) -> None:
        super().__init__({
            "set": unsigned(set_bits),
            "way": unsigned(way_bits),
            "slot": unsigned(slot_bits),
            "set_meta": TmuTextureCacheSetMeta(c, way_count),
            "req": CachedReq(c, bank_entry_width),
            "passthrough": TmuPassthrough(c),
        })


# ---------------------------------------------------------------------------
# TmuTexturePathBase helpers
# ---------------------------------------------------------------------------


def point_or_bilinear_addr(req, i: int):
    """``pointOrBilinearAddr``: tap 0 is ``biAddr0`` when bilinear else ``pointAddr``."""
    if i == 0:
        return Mux(req.bilinear, req.bi_addr0, req.point_addr)
    return (req.bi_addr1, req.bi_addr2, req.bi_addr3)[i - 1]


def point_or_bilinear_bank(req, i: int):
    """``pointOrBilinearBank`` = ``req.tapPackedBank(i)``."""
    return req.tap_packed_bank[i]


def packed_pair_sel(req, i: int):
    """``packedPairSel`` = ``req.tapPackedPair(i)``."""
    return req.tap_packed_pair[i]


def bank_texel(word, pair_sel):
    """``bankTexel``: the upper or lower 16-bit half of a bank word."""
    return Mux(pair_sel, word[16:32], word[0:16])


def texel16(word, half, byte, is16_bit):
    """``texel16`` (only ``TmuNoTextureCache`` uses it; kept with its siblings)."""
    half_word = Mux(half, word[16:32], word[0:16])
    tex_byte = Mux(byte, half_word[8:16], half_word[0:8])
    return Mux(is16_bit, half_word, Cat(tex_byte, tex_byte))


# ---------------------------------------------------------------------------
# geometry (the `val`s at the top of the case class)
# ---------------------------------------------------------------------------


class TmuTextureCacheGeometry:
    """The derived sizes of ``TmuTextureCache`` and its address helpers.

    Split out so the unit-test model and the responder can share the exact
    ``setOf`` / ``bankEntryOf`` / ``slotOf`` arithmetic without elaborating.
    """

    def __init__(self, c: Config) -> None:
        if not c.use_tex_fill_cache:
            raise ValueError("TmuTextureCache requires c.useTexFillCache")
        if not c.packed_tex_layout:
            raise ValueError("TmuTextureCache requires c.packedTexLayout")
        lw = c.tex_fill_line_words
        if not (lw > 0 and (lw & (lw - 1)) == 0):
            raise ValueError("texFillLineWords must be a power of two")
        if not (lw >= 4 and lw % 4 == 0):
            raise ValueError("texFillLineWords must be a multiple of 4, >= 4")
        if not (lw * 4 - 1) < (1 << c.mem_burst_length_width):
            raise ValueError("fill burst does not fit memBurstLengthWidth")
        if not c.tex_fill_cache_slots > 0:
            raise ValueError("texFillCacheSlots must be > 0")

        self.c = c
        self.address_width = c.address_width
        self.line_words = lw
        self.line_bytes = lw * 4
        self.line_byte_shift = log2_up(self.line_bytes)
        self.bank_count = BANK_COUNT
        self.tap_count = TAP_COUNT
        self.slot_count = c.tex_fill_cache_slots
        self.slot_bits = max(1, log2_up(self.slot_count))
        self.way_count = c.tex_fill_way_count if self.slot_count > 1 else 1
        if not self.way_count > 0:
            raise ValueError("texFillWayCount must be > 0")
        if self.slot_count % self.way_count:
            raise ValueError("texFillCacheSlots must be a multiple of texFillWayCount")
        self.set_count = self.slot_count // self.way_count
        self.set_bits = max(1, log2_up(self.set_count))
        self.way_bits = max(1, log2_up(self.way_count))
        self.bank_entries = lw
        self.bank_entry_width = log2_up(self.bank_entries)
        self.fill_length = lw * 4 - 1
        self.rsp_count_width = log2_up(lw + 1)
        self.xor_index = c.tex_fill_xor_index

    # -- hardware helpers (Value in, Value out) ------------------------------

    def slot_of(self, set_, way):
        """``slotOf``: ``((set.resize(slotBits) |<< wayBits) +^ way.resize(slotBits)).resize(slotBits)``."""
        if self.way_count == 1:
            return zext(set_, self.slot_bits)
        shifted = zext(zext(set_, self.slot_bits) << self.way_bits, self.slot_bits)
        return zext(shifted + zext(way, self.slot_bits), self.slot_bits)

    def bank_index(self, set_, entry):
        """``bankIndex``: ``(set ## U(0, bankEntryWidth)) | entry.resize(setBits + bankEntryWidth)``."""
        width = self.set_bits + self.bank_entry_width
        return (Cat(Const(0, self.bank_entry_width), zext(set_, self.set_bits))
                | zext(entry, width))

    def line_base_of(self, addr):
        """``lineBaseOf``: ``((addr >> lineByteShift) << lineByteShift).resize(addressWidth)``."""
        return zext(Cat(Const(0, self.line_byte_shift), addr[self.line_byte_shift:]),
                    self.address_width)

    def line_base22_of(self, addr):
        """``Tmu.lineBase22Of(addr, lineByteShift)``."""
        return line_base22_of(addr, self.line_byte_shift)

    def set_of(self, base):
        """``setOf``: the low ``setBits`` of the line number, XOR-folded when configured."""
        if self.set_count == 1:
            return Const(0, self.set_bits)
        line = base[self.line_byte_shift:]
        if self.xor_index:
            folded = (zext(line, self.set_bits)
                      ^ zext(line[self.set_bits:], self.set_bits)
                      ^ zext(line[self.set_bits * 2:], self.set_bits))
            return zext(folded, self.set_bits)
        return zext(line, self.set_bits)

    def bank_entry_of(self, addr, base):
        """``bankEntryOf``: ``((addr - base) >> 2).resize(bankEntryWidth)``."""
        return zext(u_sub(addr, base)[2:], self.bank_entry_width)

    # -- the same, on Python ints (for the test model) -------------------------

    def line_base_int(self, addr: int) -> int:
        return (addr >> self.line_byte_shift) << self.line_byte_shift

    def set_int(self, base: int) -> int:
        if self.set_count == 1:
            return 0
        line = base >> self.line_byte_shift
        mask = (1 << self.set_bits) - 1
        if self.xor_index:
            return (line ^ (line >> self.set_bits) ^ (line >> (2 * self.set_bits))) & mask
        return line & mask

    def bank_entry_int(self, addr: int, base: int) -> int:
        return (((addr - base) & ((1 << self.address_width) - 1)) >> 2) \
            & ((1 << self.bank_entry_width) - 1)

    def slot_int(self, set_: int, way: int) -> int:
        if self.way_count == 1:
            return set_ & ((1 << self.slot_bits) - 1)
        return ((set_ << self.way_bits) + way) & ((1 << self.slot_bits) - 1)


def _first_free(m, valid_bits, fallback, *, name):
    """``firstFree(valid, fallback)``: lowest index whose ``valid`` is clear, else ``fallback``.

    Scala iterates the indices in *reverse* with a plain ``when``, so the
    lowest clear index is the last assignment and wins.
    """
    idx = Signal(len(fallback), name=name)
    m.d.comb += idx.eq(fallback)
    for i in reversed(range(len(valid_bits))):
        with m.If(~valid_bits[i]):
            m.d.comb += idx.eq(Const(i, len(fallback)))
    return idx


# ---------------------------------------------------------------------------
# the component
# ---------------------------------------------------------------------------


class TmuTextureCache(wiring.Component):
    """``case class TmuTextureCache(c, formalStrong = true, formalHotInit = false)``.

    Ports are ``TmuTexturePathIo``: ``sample_request`` (slave stream of
    ``Tmu.SampleRequest``), ``sample_fetch`` (master stream of
    ``Tmu.SampleFetch``), ``tex_read`` (``Bmb(Tmu.bmbParams(c))`` master),
    ``invalidate`` in, ``busy`` out.
    """

    def __init__(self, c: Config | None = None, *, formal_strong: bool = True,
                 formal_hot_init: bool = False) -> None:
        self.c = c if c is not None else Config.voodoo1()
        self.g = TmuTextureCacheGeometry(self.c)
        self.formal_strong = formal_strong
        self.formal_hot_init = formal_hot_init
        self.bmb_params = tmu_bmb_params(self.c)
        super().__init__({
            "sample_request": In(stream.Signature(SampleRequest(self.c))),
            "sample_fetch": Out(stream.Signature(SampleFetch(self.c))),
            "tex_read": Out(MemBusSignature(self.bmb_params)),
            "invalidate": In(1),
            "busy": Out(1),
        })

    # -- layouts ---------------------------------------------------------------

    def set_meta_layout(self):
        return TmuTextureCacheSetMeta(self.c, self.g.way_count)

    def fill_start_layout(self):
        g = self.g
        return TmuTextureCacheFillStart(self.c, g.slot_bits, g.set_bits, g.bank_entry_width,
                                        g.way_bits, g.way_count)

    def compare_stage_layout(self):
        g = self.g
        return TmuTextureCacheCompareStage(self.c, g.set_bits, g.bank_entry_width, g.way_count)

    def lookup_ctx_layout(self):
        g = self.g
        return TmuTextureCacheLookupCtx(self.c, g.set_bits, g.bank_entry_width)

    # -- elaborate -------------------------------------------------------------

    def elaborate(self, platform):
        m = Module()
        c = self.c
        g = self.g
        aw = c.address_width
        way_count, way_bits = g.way_count, g.way_bits
        set_bits, set_count = g.set_bits, g.set_count
        tap_count, bank_count = g.tap_count, g.bank_count
        line_words = g.line_words

        set_meta_l = self.set_meta_layout()
        fill_start_l = self.fill_start_layout()
        compare_l = self.compare_stage_layout()
        lookup_ctx_l = self.lookup_ctx_layout()

        def zero_meta(m_, slot):
            m_.d.comb += [slot.base.eq(0), slot.valid.eq(0), slot.ready.eq(0), slot.epoch.eq(0)]

        # ---- val reqStream = io.sampleRequest.queue(c.texFillRequestWindow) ----
        req_width = SampleRequest(c).size
        req_stream = queue(m, self.sample_request, c.tex_fill_request_window,
                           mem_attrs=spinal_fifo_ram_attrs(req_width, c.tex_fill_request_window),
                           name="io_sampleRequest_fifo")

        # ---- val currentEpoch = Reg(UInt(8 bits)) init 0 ------------------------
        current_epoch = Signal(8, name="currentEpoch")

        # ---- metaMem x4 / bankMem x4 x wayCount -----------------------------------
        meta_read_data = []
        meta_read_en = []
        meta_read_addr = []
        meta_write_enable = Signal(name="metaWriteEnable")
        meta_write_set = Signal(set_bits, name="metaWriteSet")
        meta_write_data = Signal(set_meta_l, name="metaWriteData")
        # No `init` in Spinal for any RAM here; `init=[]` is the closest
        # Amaranth gets (an all-zero initialiser that gen_core_sim.py strips).
        # syn_ramstyle follows the Spinal build's force_distributed_fifo_ram
        # rule (width >= 24 and depth <= 64): all three RAM kinds qualify for
        # Config.voodoo1(), so no BSRAM is spent on the cache.
        for tap in range(tap_count):
            ram = memory.Memory(shape=set_meta_l, depth=set_count, init=[],
                                attrs=spinal_fifo_ram_attrs(set_meta_l.size, set_count))
            m.submodules[f"metaMem_{tap}"] = ram
            rd = ram.read_port(domain="sync")
            wr = ram.write_port()
            m.d.comb += [
                wr.en.eq(meta_write_enable),
                wr.addr.eq(meta_write_set),
                wr.data.eq(meta_write_data),
            ]
            meta_read_data.append(rd.data)
            meta_read_en.append(rd.en)
            meta_read_addr.append(rd.addr)

        bank_read_data = [[None] * way_count for _ in range(bank_count)]
        bank_read_en = [[None] * way_count for _ in range(bank_count)]
        bank_read_addr = [[None] * way_count for _ in range(bank_count)]
        bank_write = [[None] * way_count for _ in range(bank_count)]
        for b in range(bank_count):
            for way in range(way_count):
                depth = set_count * g.bank_entries
                ram = memory.Memory(shape=unsigned(32), depth=depth, init=[],
                                    attrs=spinal_fifo_ram_attrs(32, depth))
                m.submodules[f"bankMem_{b}_{way}"] = ram
                rd = ram.read_port(domain="sync")
                bank_read_data[b][way] = rd.data
                bank_read_en[b][way] = rd.en
                bank_read_addr[b][way] = rd.addr
                bank_write[b][way] = ram.write_port()

        def bank_rsp(sel, way):
            return Array([bank_read_data[b][way] for b in range(bank_count)])[sel]

        # metaWriteData defaults: zeroMeta() per way, victim 0
        for way in range(way_count):
            zero_meta(m, meta_write_data.ways[way])
        m.d.comb += [meta_write_enable.eq(0), meta_write_set.eq(0), meta_write_data.victim.eq(0)]

        # ---- compare hold / stage -----------------------------------------------
        compare_hold = Signal(compare_l, name="compareHold", reset_less=True)
        compare_hold_valid = Signal(name="compareHoldValid")
        compare_stage = Signal(compare_l, name="compareStage")
        compare_valid = Signal(name="compareValid")
        m.d.comb += [compare_stage.eq(compare_hold), compare_valid.eq(compare_hold_valid)]

        # ---- fillFetchRaw / fillFetchRsp / hitRsp / io.sampleFetch defaults ----
        fill_fetch_raw = Stream(SampleFetch(c), path=("fillFetchRaw",))
        fill_fetch_rsp = queue(m, fill_fetch_raw, 1, name="fillFetchRaw_fifo")
        continue_pending_valid = Signal(name="continuePendingValid")
        continue_pending_idx = Signal(2, name="continuePendingIdx")
        hit_rsp = Signal(SampleFetch(c), name="hitRsp")

        # ---- val owner = new Area -------------------------------------------------
        owner_active = Signal(name="owner_active")
        owner_sample = Signal(SampleRequest(c), name="owner_sample", reset_less=True)
        owner_epoch = Signal(8, name="owner_epoch")
        owner_resolved = Signal(tap_count, name="owner_resolved")
        owner_tap_meta = [Signal(set_meta_l, name=f"owner_tapMeta_{i}", reset_less=True)
                          for i in range(tap_count)]
        owner_texels = [Signal(16, name=f"owner_texels_{i}", reset_less=True)
                        for i in range(tap_count)]

        # ---- val fill = new Area --------------------------------------------------
        fill_set_meta = Signal(set_meta_l, name="fill_setMeta", reset_less=True)
        fill_active = Signal(name="fill_activeReg")
        fill_slot = Signal(g.slot_bits, name="fill_slot")
        fill_set = Signal(set_bits, name="fill_set")
        fill_way = Signal(way_bits, name="fill_way")
        fill_rsp_count = Signal(g.rsp_count_width, name="fill_rspCount")
        fill_req = Signal(CachedReq(c, g.bank_entry_width), name="fill_req", reset_less=True)
        fill_passthrough = Signal(TmuPassthrough(c), name="fill_passthrough", reset_less=True)
        fill_tex_base = [Signal(22, name=f"fill_texBase_{i}", reset_less=True) for i in range(9)]
        fill_tex_end = [Signal(22, name=f"fill_texEnd_{i}", reset_less=True) for i in range(9)]
        fill_tex_shift = [Signal(4, name=f"fill_texShift_{i}", reset_less=True) for i in range(9)]
        fill_decode_valid = [Signal(name=f"fill_decodeValid_{i}") for i in range(4)]
        fill_decode_bank = [Signal(2, name=f"fill_decodeBank_{i}") for i in range(4)]
        fill_decode_pair = [Signal(name=f"fill_decodePair_{i}") for i in range(4)]

        # ---- lookupSource = reqStream.translateWith { sample, epoch } -----------
        replay = Signal(TmuTextureCacheReplayReq(c), name="replay")
        m.d.comb += [replay.sample.eq(req_stream.payload), replay.epoch.eq(current_epoch)]
        lookup_source = translate_with(m, req_stream, replay)

        lookup_ctx = Signal(lookup_ctx_l, name="lookupCtx")
        lookup_sample = lookup_source.payload.sample
        m.d.comb += [
            lookup_ctx.sample.eq(lookup_sample),
            lookup_ctx.epoch.eq(lookup_source.payload.epoch),
        ]
        for i in range(tap_count):
            active = Const(1) if i == 0 else lookup_sample.bilinear
            addr = point_or_bilinear_addr(lookup_sample, i)
            bank = point_or_bilinear_bank(lookup_sample, i)
            base = g.line_base_of(addr)
            m.d.comb += [
                lookup_ctx.tap_active[i].eq(active),
                lookup_ctx.tap_bank[i].eq(bank),
                lookup_ctx.tap_line_base[i].eq(base),
                lookup_ctx.tap_set[i].eq(g.set_of(base)),
                lookup_ctx.tap_bank_entry[i].eq(g.bank_entry_of(addr, base)),
            ]

        init_sweep_active = Signal(name="initSweepActive", init=0 if self.formal_hot_init else 1)
        init_sweep_set = Signal(set_bits, name="initSweepSet")
        formal_hot_init_done = Signal(name="formalHotInitDone",
                                      init=0 if self.formal_hot_init else 1)

        # ---- def buildFillStart(sample, epoch, tapSetMeta, tapIdx) ---------------
        def build_fill_start(sample, epoch, tap_set_meta, tap_idx, *, name):
            start = Signal(fill_start_l, name=name)
            tap_addrs = Array([point_or_bilinear_addr(sample, i) for i in range(tap_count)])
            tap_banks = Array([point_or_bilinear_bank(sample, i) for i in range(tap_count)])
            tap_addr = Signal(aw, name=f"{name}_tapAddr")
            m.d.comb += tap_addr.eq(tap_addrs[tap_idx])
            tap_line_base = Signal(aw, name=f"{name}_tapLineBase")
            m.d.comb += tap_line_base.eq(g.line_base_of(tap_addr))
            tap_set = g.set_of(tap_line_base)
            tap_entry = g.bank_entry_of(tap_addr, tap_line_base)
            valid_vec = Cat(*[tap_set_meta.ways[way].valid for way in range(way_count)])
            chosen_way = _first_free(m, valid_vec, tap_set_meta.victim, name=f"{name}_chosenWay")

            m.d.comb += [
                start.set.eq(tap_set),
                start.way.eq(chosen_way),
                start.slot.eq(g.slot_of(tap_set, chosen_way)),
            ]
            for way in range(way_count):
                m.d.comb += start.set_meta.ways[way].eq(tap_set_meta.ways[way])
            # (chosenWay + 1).resize(victim width): UInt + Int keeps the UInt's width
            m.d.comb += start.set_meta.victim.eq(
                zext(u_add(chosen_way, Const(1, 1)), len(start.set_meta.victim)))
            for way in range(way_count):
                with m.If(chosen_way == Const(way, way_bits)):
                    m.d.comb += [
                        start.set_meta.ways[way].base.eq(tap_line_base),
                        start.set_meta.ways[way].valid.eq(1),
                        start.set_meta.ways[way].ready.eq(0),
                        start.set_meta.ways[way].epoch.eq(epoch),
                    ]
            m.d.comb += [
                start.req.line_base.eq(tap_line_base),
                start.req.bank_sel.eq(tap_banks[tap_idx]),
                start.req.bank_entry.eq(tap_entry),
                start.req.lod_base.eq(sample.lod_base),
                start.req.lod_shift.eq(sample.lod_shift),
                start.req.is16_bit.eq(sample.is16_bit),
                start.req.tex_tables.eq(sample.tex_tables),
                start.req.startup_decode.eq(sample.tap_startup_decode[tap_idx]),
                start.passthrough.eq(sample.passthrough),
            ]
            return start

        # ---- lookup launch --------------------------------------------------------
        compare_consume_hit = Signal(name="compareConsumeHit")
        compare_consume_miss = Signal(name="compareConsumeMiss")
        compare_consumes = compare_consume_hit | compare_consume_miss
        lookup_blocked = Signal(name="lookupBlocked")
        m.d.comb += lookup_source.ready.eq(~lookup_blocked)
        lookup_launch = fire(lookup_source)

        for tap in range(tap_count):
            m.d.comb += [
                meta_read_en[tap].eq(lookup_launch & lookup_ctx.tap_active[tap]),
                meta_read_addr[tap].eq(lookup_ctx.tap_set[tap]),
            ]

        for b in range(bank_count):
            bank_use = Cat(*[lookup_ctx.tap_active[i] & (lookup_ctx.tap_bank[i] == Const(b, 2))
                             for i in range(tap_count)])
            bank_set = mux_oh(bank_use, [lookup_ctx.tap_set[i] for i in range(tap_count)])
            bank_entry = mux_oh(bank_use, [lookup_ctx.tap_bank_entry[i] for i in range(tap_count)])
            for way in range(way_count):
                m.d.comb += [
                    bank_read_en[b][way].eq(lookup_launch & bank_use.any()),
                    bank_read_addr[b][way].eq(g.bank_index(bank_set, bank_entry)),
                ]

        lookup_rsp_valid = Signal(name="lookupRspValid")
        lookup_rsp_ctx = Signal(lookup_ctx_l, name="lookupRspCtx", reset_less=True)
        m.d.sync += lookup_rsp_valid.eq(lookup_launch)
        with m.If(lookup_launch):
            m.d.sync += lookup_rsp_ctx.eq(lookup_ctx)

        # ---- arrivedCompare --------------------------------------------------------
        arrived = Signal(compare_l, name="arrivedCompare")
        hit_ok = [Signal(name=f"hitOk_{i}") for i in range(tap_count)]
        hit_way = [Signal(way_bits, name=f"hitWay_{i}") for i in range(tap_count)]
        for i in range(tap_count):
            hits = Signal(way_count, name=f"hits_{i}")
            for way in range(way_count):
                meta = meta_read_data[i].ways[way]
                m.d.comb += hits[way].eq(
                    lookup_rsp_ctx.tap_active[i] & meta.valid & meta.ready
                    & (meta.epoch == lookup_rsp_ctx.epoch)
                    & (meta.base == lookup_rsp_ctx.tap_line_base[i]))
            m.d.comb += [
                hit_ok[i].eq(hits.any()),
                hit_way[i].eq(Const(0, way_bits) if way_count == 1 else oh_to_uint(hits)),
                arrived.tap_hit[i].eq(hit_ok[i]),
                arrived.tap_set_meta[i].eq(meta_read_data[i]),
                arrived.tap_data[i].eq(Mux(
                    hit_ok[i],
                    mux_oh(hits, [bank_rsp(lookup_rsp_ctx.tap_bank[i], way)
                                  for way in range(way_count)]),
                    Const(0, 32))),
            ]

        all_hits = Const(1)
        any_miss = Const(0)
        for i in range(tap_count):
            all_hits = all_hits & (~lookup_rsp_ctx.tap_active[i] | hit_ok[i])
            any_miss = any_miss | (lookup_rsp_ctx.tap_active[i] & ~hit_ok[i])
        m.d.comb += [
            arrived.sample.eq(lookup_rsp_ctx.sample),
            arrived.epoch.eq(lookup_rsp_ctx.epoch),
            arrived.all_hits.eq(all_hits),
            arrived.any_miss.eq(any_miss),
        ]

        with m.If(~compare_hold_valid):
            m.d.comb += [compare_stage.eq(arrived), compare_valid.eq(lookup_rsp_valid)]

        live_hit_can_consume = (lookup_rsp_valid & arrived.all_hits & ~fill_fetch_rsp.valid
                                & self.sample_fetch.ready)
        live_compare_blocks = lookup_rsp_valid & (~arrived.all_hits | ~live_hit_can_consume)
        m.d.comb += lookup_blocked.eq(init_sweep_active | owner_active | fill_active
                                      | compare_hold_valid | live_compare_blocks)
        m.d.comb += compare_consume_hit.eq(compare_valid & compare_stage.all_hits
                                           & ~fill_fetch_rsp.valid & self.sample_fetch.ready)

        compare_sample = compare_stage.sample
        compare_tap_active = [Const(1) if i == 0 else compare_sample.bilinear
                              for i in range(tap_count)]
        compare_tap_line_base = [g.line_base22_of(point_or_bilinear_addr(compare_sample, i))
                                 for i in range(tap_count)]
        compare_tap_set = [g.set_of(compare_tap_line_base[i]) for i in range(tap_count)]

        compare_miss_sel = Signal(tap_count, name="compareMissSel")
        m.d.comb += compare_miss_sel[0].eq(compare_tap_active[0] & ~compare_stage.tap_hit[0])
        m.d.comb += compare_miss_sel[1].eq(~compare_miss_sel[0] & compare_tap_active[1]
                                           & ~compare_stage.tap_hit[1])
        m.d.comb += compare_miss_sel[2].eq(~(compare_miss_sel[0] | compare_miss_sel[1])
                                           & compare_tap_active[2] & ~compare_stage.tap_hit[2])
        m.d.comb += compare_miss_sel[3].eq(
            ~(compare_miss_sel[0] | compare_miss_sel[1] | compare_miss_sel[2])
            & compare_tap_active[3] & ~compare_stage.tap_hit[3])
        compare_miss_idx = Signal(2, name="compareMissIdx")
        m.d.comb += compare_miss_idx.eq(oh_to_uint(compare_miss_sel))

        compare_fill_start = Stream(fill_start_l, path=("compareFillStart",))
        m.d.comb += compare_fill_start.valid.eq(compare_valid & compare_stage.any_miss
                                                & ~owner_active & ~fill_active)
        compare_miss_meta = Signal(set_meta_l, name="compareMissMeta")
        m.d.comb += compare_miss_meta.eq(
            Array([compare_stage.tap_set_meta[i] for i in range(tap_count)])[compare_miss_idx])
        compare_fill_payload = build_fill_start(compare_sample, compare_stage.epoch,
                                                compare_miss_meta, compare_miss_idx,
                                                name="compareFillStart_payload")
        m.d.comb += compare_fill_start.payload.eq(compare_fill_payload)

        m.d.comb += compare_consume_miss.eq(fire(compare_fill_start))
        with m.If(~compare_hold_valid & lookup_rsp_valid & ~compare_consumes):
            m.d.sync += [compare_hold.eq(arrived), compare_hold_valid.eq(1)]
        with m.If(compare_hold_valid & compare_consumes):
            m.d.sync += compare_hold_valid.eq(0)

        # ---- when(compareConsumeMiss) { owner := ... } -------------------------
        with m.If(compare_consume_miss):
            m.d.sync += [
                owner_active.eq(1),
                owner_sample.eq(compare_sample),
                owner_epoch.eq(compare_stage.epoch),
                owner_resolved.eq(Cat(*[compare_stage.tap_hit[i] for i in range(tap_count)])),
            ]
            for i in range(tap_count):
                m.d.sync += [
                    owner_tap_meta[i].eq(compare_stage.tap_set_meta[i]),
                    owner_texels[i].eq(Mux(
                        compare_stage.tap_hit[i],
                        bank_texel(compare_stage.tap_data[i], packed_pair_sel(compare_sample, i)),
                        Const(0, 16))),
                ]
                with m.If(compare_tap_set[i] == compare_fill_start.payload.set):
                    m.d.sync += owner_tap_meta[i].eq(compare_fill_start.payload.set_meta)

        # ---- owner tap geometry ---------------------------------------------------
        owner_tap_active = [Const(1) if i == 0 else owner_sample.bilinear
                            for i in range(tap_count)]
        owner_tap_bank = [point_or_bilinear_bank(owner_sample, i) for i in range(tap_count)]
        owner_tap_addr = [point_or_bilinear_addr(owner_sample, i) for i in range(tap_count)]
        owner_tap_line_base = [Signal(aw, name=f"ownerTapLineBase_{i}") for i in range(tap_count)]
        for i in range(tap_count):
            m.d.comb += owner_tap_line_base[i].eq(g.line_base_of(owner_tap_addr[i]))
        owner_tap_set = [g.set_of(owner_tap_line_base[i]) for i in range(tap_count)]
        owner_tap_entry = [g.bank_entry_of(owner_tap_addr[i], owner_tap_line_base[i])
                           for i in range(tap_count)]
        owner_tap_pair = [packed_pair_sel(owner_sample, i) for i in range(tap_count)]

        # ---- val fillRsp = io.texRead.rsp.haltWhen(!fill.activeReg) --------------
        fill_rsp = halt_when(m, self.tex_read.rsp, ~fill_active)
        fill_rsp_data = fill_rsp.payload.fragment.data
        fill_rsp_last = fill_rsp.payload.last

        word_addr = Signal(aw, name="wordAddr")
        m.d.comb += word_addr.eq(u_add(fill_req.line_base, zext(fill_rsp_count << 2, aw)))
        word_entry = zext(fill_rsp_count, g.bank_entry_width)

        def packed_location(addr):
            return packed_location22(addr, fill_req.is16_bit, fill_req.lod_base,
                                     fill_req.lod_shift, fill_tex_base, fill_tex_end,
                                     fill_tex_shift)

        # ---- write-data assembly ----------------------------------------------------
        mask = Signal(bank_count, name="mask")
        pair_mask = [Signal(2, name=f"pairMask_{b}") for b in range(bank_count)]
        write_data = [Signal(32, name=f"writeData_{b}") for b in range(bank_count)]
        m.d.comb += mask.eq(0)
        for b in range(bank_count):
            m.d.comb += [pair_mask[b].eq(0), write_data[b].eq(0)]
        for byte_idx in range(4):
            fmt = Const(1) if (byte_idx & 1) == 0 else ~fill_req.is16_bit
            loc_valid = fill_decode_valid[byte_idx]
            loc_bank = fill_decode_bank[byte_idx]
            loc_pair = fill_decode_pair[byte_idx]
            if byte_idx == 0:
                texel = fill_rsp_data[0:16]
            elif byte_idx == 2:
                texel = fill_rsp_data[16:32]
            else:
                texel = Const(0, 16)
            lane = fill_rsp_data[byte_idx * 8:byte_idx * 8 + 8]
            expanded = Cat(lane, lane)
            with m.If(fmt & loc_valid):
                texel_data = Mux(fill_req.is16_bit, texel, expanded)
                for b in range(bank_count):
                    with m.If(loc_bank == Const(b, 2)):
                        m.d.comb += mask[b].eq(1)
                        with m.If(loc_pair):
                            m.d.comb += [pair_mask[b][1].eq(1),
                                         write_data[b][16:32].eq(texel_data)]
                        with m.Else():
                            m.d.comb += [pair_mask[b][0].eq(1),
                                         write_data[b][0:16].eq(texel_data)]

        fill_rsp_fire = fire(fill_rsp)
        for b in range(bank_count):
            for way in range(way_count):
                wr = bank_write[b][way]
                m.d.comb += [
                    wr.addr.eq(g.bank_index(fill_set, word_entry)),
                    wr.data.eq(write_data[b]),
                    wr.en.eq(fill_rsp_fire & mask[b] & (fill_way == Const(way, way_bits))),
                ]

        # ---- owner capture / commit masks -------------------------------------------
        pair_mask_flat = Cat(*pair_mask)  # bank b, pair p -> bit 2*b + p

        def pair_hit(i):
            return pair_mask_flat.bit_select(Cat(owner_tap_pair[i], owner_tap_bank[i]), 1)

        owner_fill_capture_mask = Signal(tap_count, name="ownerFillCaptureMask")
        owner_fill_commit_mask = Signal(tap_count, name="ownerFillCommitMask")
        owner_resolved_potential = Signal(tap_count, name="ownerResolvedPotential")
        owner_resolved_next = Signal(tap_count, name="ownerResolvedNext")
        owner_active_mask = Signal(tap_count, name="ownerActiveMask")
        for i in range(tap_count):
            match = (owner_active & owner_tap_active[i] & ~owner_resolved[i]
                     & (owner_tap_line_base[i] == fill_req.line_base)
                     & (owner_tap_entry[i] == word_entry) & pair_hit(i))
            m.d.comb += [
                owner_fill_capture_mask[i].eq(fill_rsp.valid & match),
                owner_fill_commit_mask[i].eq(fill_rsp_fire & match),
                owner_resolved_potential[i].eq(owner_resolved[i] | owner_fill_capture_mask[i]),
                owner_resolved_next[i].eq(owner_resolved[i] | owner_fill_commit_mask[i]),
                owner_active_mask[i].eq(owner_active & owner_tap_active[i]),
            ]

        def owner_bank_word(i):
            return Array(write_data)[owner_tap_bank[i]]

        owner_rsp = Signal(SampleFetch(c), name="ownerRsp")
        m.d.comb += [
            owner_rsp.bilinear.eq(owner_sample.bilinear),
            owner_rsp.passthrough.eq(owner_sample.passthrough),
        ]
        for i in range(tap_count):
            next_texel = Mux(owner_fill_capture_mask[i],
                             bank_texel(owner_bank_word(i), owner_tap_pair[i]),
                             owner_texels[i])
            m.d.comb += owner_rsp.texels[i].eq(
                next_texel if i == 0 else Mux(owner_sample.bilinear, next_texel, Const(0, 16)))

        owner_rsp_committed = Signal(SampleFetch(c), name="ownerRspCommitted")
        m.d.comb += [
            owner_rsp_committed.bilinear.eq(owner_sample.bilinear),
            owner_rsp_committed.passthrough.eq(owner_sample.passthrough),
        ]
        for i in range(tap_count):
            m.d.comb += owner_rsp_committed.texels[i].eq(
                owner_texels[i] if i == 0
                else Mux(owner_sample.bilinear, owner_texels[i], Const(0, 16)))

        ready_set_meta = Signal(set_meta_l, name="readySetMeta")
        m.d.comb += ready_set_meta.eq(fill_set_meta)
        for way in range(way_count):
            with m.If(fill_way == Const(way, way_bits)):
                m.d.comb += ready_set_meta.ways[way].ready.eq(1)

        continue_done = Signal(tap_count, name="continueDone")
        continue_done_next = Signal(tap_count, name="continueDoneNext")
        for i in range(tap_count):
            m.d.comb += [
                continue_done[i].eq(~owner_active | ~owner_tap_active[i]
                                    | owner_resolved_potential[i]),
                continue_done_next[i].eq(~owner_active | ~owner_tap_active[i]
                                         | owner_resolved_next[i]),
            ]
        continue_idx = _first_free(m, continue_done, Const(0, 2), name="continueIdx")
        continue_idx_next = _first_free(m, continue_done_next, Const(0, 2),
                                        name="continueIdxNext")
        continue_done_all = continue_done.all()
        continue_done_next_all = continue_done_next.all()
        fill_fetch_emit = Signal(name="fillFetchEmit")
        m.d.comb += fill_fetch_emit.eq(owner_fill_capture_mask.any() & continue_done_all)

        continue_payload = [
            build_fill_start(owner_sample, owner_epoch, owner_tap_meta[i], Const(i, 2),
                             name=f"continuePayload_{i}")
            for i in range(tap_count)
        ]
        continue_fill_start = Stream(fill_start_l, path=("continueFillStart",))
        m.d.comb += [
            continue_fill_start.valid.eq(continue_pending_valid),
            continue_fill_start.payload.eq(Array(continue_payload)[continue_pending_idx]),
        ]

        fill_start = Stream(fill_start_l, path=("fillStart",))
        m.d.comb += [
            fill_start.valid.eq(continue_fill_start.valid | compare_fill_start.valid),
            fill_start.payload.eq(compare_fill_start.payload),
        ]
        with m.If(continue_fill_start.valid):
            m.d.comb += fill_start.payload.eq(continue_fill_start.payload)
        m.d.comb += [
            continue_fill_start.ready.eq(fill_start.ready),
            compare_fill_start.ready.eq(fill_start.ready & ~continue_fill_start.valid),
        ]

        # ---- io.texRead.cmd << fillStart.translateWith { READ, lineBase, fillLength }
        cmd = self.tex_read.cmd
        m.d.comb += [
            cmd.valid.eq(fill_start.valid),
            fill_start.ready.eq(cmd.ready),
            cmd.payload.fragment.address.eq(fill_start.payload.req.line_base),
            cmd.payload.fragment.opcode.eq(CMD_READ),
            cmd.payload.fragment.length.eq(Const(g.fill_length, c.mem_burst_length_width)),
            cmd.payload.last.eq(1),
        ]
        if "source" in cmd.payload.fragment.shape().members:
            m.d.comb += cmd.payload.fragment.source.eq(0)

        fill_start_fire = fire(fill_start)
        with m.If(fill_start_fire):
            m.d.sync += [
                fill_active.eq(1),
                fill_set.eq(fill_start.payload.set),
                fill_way.eq(fill_start.payload.way),
                fill_slot.eq(fill_start.payload.slot),
                fill_set_meta.eq(fill_start.payload.set_meta),
                fill_rsp_count.eq(0),
                fill_req.eq(fill_start.payload.req),
                fill_passthrough.eq(fill_start.payload.passthrough),
            ]
            m.d.comb += [
                meta_write_enable.eq(1),
                meta_write_set.eq(fill_start.payload.set),
                meta_write_data.eq(fill_start.payload.set_meta),
            ]

        # startTexEnd: `(rawEnd > base) ? rawEnd | fallback`
        start_tables = fill_start.payload.req.tex_tables
        start_tex_end = []
        for lod in range(9):
            base = start_tables.tex_base[lod]
            raw_end = start_tables.tex_end[lod]
            if lod < 8:
                fallback = start_tables.tex_base[lod + 1]
            else:
                fallback = u_add(base, Mux(fill_start.payload.req.is16_bit,
                                           Const(2, 22), Const(1, 22)))
            start_tex_end.append(Mux(raw_end > base, raw_end, fallback))

        with m.If(fill_start_fire):
            for lod in range(9):
                m.d.sync += [
                    fill_tex_base[lod].eq(start_tables.tex_base[lod]),
                    fill_tex_end[lod].eq(start_tex_end[lod]),
                    fill_tex_shift[lod].eq(start_tables.tex_shift[lod]),
                ]
            startup = fill_start.payload.req.startup_decode
            for byte_idx in range(4):
                m.d.sync += [
                    fill_decode_valid[byte_idx].eq(startup.valid[byte_idx]),
                    fill_decode_bank[byte_idx].eq(startup.bank[byte_idx]),
                    fill_decode_pair[byte_idx].eq(startup.pair[byte_idx]),
                ]

        with m.If(fire(continue_fill_start)):
            m.d.sync += continue_pending_valid.eq(0)
            for i in range(tap_count):
                with m.If(owner_tap_set[i] == continue_fill_start.payload.set):
                    m.d.sync += owner_tap_meta[i].eq(continue_fill_start.payload.set_meta)

        m.d.comb += [
            fill_rsp.ready.eq(~fill_fetch_emit | fill_fetch_raw.ready),
            fill_fetch_raw.valid.eq(fill_rsp.valid & fill_fetch_emit),
            fill_fetch_raw.payload.eq(owner_rsp_committed),
        ]

        with m.If(fill_rsp_fire):
            next_word_addr = u_add(fill_req.line_base,
                                   zext(u_add(fill_rsp_count, Const(1, 1)) << 2, aw))
            for byte_idx in range(4):
                next_addr = u_add(next_word_addr, Const(byte_idx, 2))
                loc_in, loc_bank, loc_pair = packed_location(next_addr)
                m.d.sync += [
                    fill_decode_valid[byte_idx].eq(loc_in),
                    fill_decode_bank[byte_idx].eq(loc_bank),
                    fill_decode_pair[byte_idx].eq(loc_pair),
                ]
            for i in range(tap_count):
                with m.If(owner_fill_commit_mask[i]):
                    m.d.sync += owner_texels[i].eq(
                        bank_texel(owner_bank_word(i), owner_tap_pair[i]))
            m.d.sync += owner_resolved.eq(owner_resolved_next)

        with m.If(fill_rsp_fire):
            m.d.sync += fill_rsp_count.eq(u_add(fill_rsp_count, Const(1, 1)))
            with m.If(fill_rsp_last):
                m.d.sync += [
                    fill_active.eq(0),
                    fill_set_meta.eq(ready_set_meta),
                    continue_pending_valid.eq(~continue_done_next_all),
                    continue_pending_idx.eq(continue_idx_next),
                ]
                m.d.comb += [
                    meta_write_enable.eq(1),
                    meta_write_set.eq(fill_set),
                    meta_write_data.eq(ready_set_meta),
                ]
                for i in range(tap_count):
                    with m.If(owner_tap_set[i] == fill_set):
                        m.d.sync += owner_tap_meta[i].eq(ready_set_meta)
                with m.If(continue_done_next_all):
                    m.d.sync += owner_active.eq(0)

        with m.If(self.invalidate):
            m.d.sync += current_epoch.eq(current_epoch + 1)

        # ---- when(!formalHotInitDone) { metaMem(0) := hotMeta() } ------------------
        with m.If(~formal_hot_init_done):
            m.d.comb += [meta_write_enable.eq(1), meta_write_set.eq(0)]
            for way in range(way_count):
                slot = meta_write_data.ways[way]
                m.d.comb += [slot.base.eq(0), slot.epoch.eq(0),
                             slot.valid.eq(1 if way == 0 else 0),
                             slot.ready.eq(1 if way == 0 else 0)]
            m.d.comb += meta_write_data.victim.eq(1 if way_count > 1 else 0)
            m.d.sync += formal_hot_init_done.eq(1)

        # ---- when(initSweepActive) { clear one set per cycle } ---------------------
        with m.If(init_sweep_active):
            m.d.comb += [meta_write_enable.eq(1), meta_write_set.eq(init_sweep_set)]
            for way in range(way_count):
                zero_meta(m, meta_write_data.ways[way])
            m.d.comb += meta_write_data.victim.eq(0)
            with m.If(init_sweep_set == Const(set_count - 1, set_bits)):
                m.d.sync += init_sweep_active.eq(0)
            with m.Else():
                m.d.sync += init_sweep_set.eq(init_sweep_set + 1)

        # ---- hitRsp / io.sampleFetch ----------------------------------------------
        m.d.comb += [
            hit_rsp.bilinear.eq(compare_sample.bilinear),
            hit_rsp.passthrough.eq(compare_sample.passthrough),
        ]
        for i in range(tap_count):
            texel = bank_texel(compare_stage.tap_data[i], packed_pair_sel(compare_sample, i))
            m.d.comb += hit_rsp.texels[i].eq(
                texel if i == 0 else Mux(compare_sample.bilinear, texel, Const(0, 16)))

        m.d.comb += fill_fetch_rsp.ready.eq(0)
        with m.If(fill_fetch_rsp.valid):
            m.d.comb += [
                self.sample_fetch.valid.eq(1),
                self.sample_fetch.payload.eq(fill_fetch_rsp.payload),
                fill_fetch_rsp.ready.eq(self.sample_fetch.ready),
            ]
        with m.Else():
            m.d.comb += [
                self.sample_fetch.valid.eq(compare_valid & compare_stage.all_hits),
                self.sample_fetch.payload.eq(hit_rsp),
            ]

        m.d.comb += self.busy.eq(req_stream.valid | lookup_rsp_valid | compare_valid
                                 | owner_active | fill_active | self.sample_fetch.valid
                                 | init_sweep_active)
        return m
