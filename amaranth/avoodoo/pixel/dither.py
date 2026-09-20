"""``Dither`` -- the ordered-dither ROM stage of the pixel pipeline.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/pixel/Dither.scala`` (118 lines,
SpinalHDL 1.15.0), instantiated by ``Write.scala`` between the pre-dither
colour and the framebuffer write.  Tables come from
:mod:`avoodoo.pixel.dither_tables` (plan Phase 4, "dither tables"), which
generates the 86Box ``vid_voodoo_dither.h`` data rather than pasting it.

What the module does
--------------------

8-bit R/G/B in, 5-6-5 out, by table lookup on the pixel's position inside a
4x4 or 2x2 Bayer tile.  Three independent synchronous ROMs (R, G, B; R and B
hold the same ``rb`` image but are separate ``Memory`` instances, exactly as
the Scala writes them) are addressed with one packed address space::

    4x4:  v(8) ## y(2) ## x(2)              -> [0, 4095]
    2x2:  1 ## (v(8) ## y(0) ## x(0))       -> [4096, 5119]

The 4x4 half is exactly 2**12 entries, so bit 12 selects the 2x2 half with no
adder.  :func:`avoodoo.pixel.dither_tables.packed_index` is the software twin
of :func:`Dither.packed_addr`.

Pipeline shape (read latency 1, checked against ``Dither_spinal.v``)
--------------------------------------------------------------------

The Scala builds the handshake out of Spinal's ``Mem.streamReadSync`` for the
R channel and bare ``Mem.readSync`` for G and B, which expands to:

* three ROM read-data registers, all clock-enabled by ``addrStream.fire``;
* ``retValid`` (``RegInit(False)``, so it *does* reset) and the four
  ``linked`` bypass registers (plain ``RegNextWhen``, so they do **not** --
  they are ``reset_less=True`` here per plan §6), all clock-enabled by
  ``addrStream.ready``;
* ``addrStream.ready = romRResult.isFree = !valid || ready``.

The two enables differ: ``ready & !valid`` refreshes ``linked`` and clears
``retValid`` but leaves the ROM outputs alone.  That is unobservable at the
ports (``retValid`` is low in exactly those cases) but it is what the Spinal
Verilog does, so it is what this does.

``enable`` low bypasses the ROM with a plain truncation (``r >> 3``,
``g >> 2``, ``b >> 3``) carried alongside the ROM read in ``linked``; the
output mux is per channel on the *registered* ``enable``.

Signal names follow the Scala (``linked``, ``addr_stream``,
``rom_r_result``, ``dit_g_rom``, ``dit_b_rom``) so the two waveforms line up.
Bundle field names are ``tools.diff.naming.camel_to_snake`` of the Scala ones
(``ditR`` -> ``dit_r``, ``truncG`` -> ``trunc_g``, ``use2x2`` -> ``use2x2``),
which is what the lockstep diff harness pairs against.
"""

from __future__ import annotations

from amaranth import Cat, Module, Mux, Signal, unsigned
from amaranth.hdl import Const
from amaranth.lib import data, memory, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.stream import Stream, fire, is_free
from avoodoo.pixel.dither_tables import g_packed, rb_packed

__all__ = [
    "INPUT_LAYOUT",
    "OUTPUT_LAYOUT",
    "LINKED_LAYOUT",
    "READ_RET_LINKED_LAYOUT",
    "ROM_DEPTH",
    "Dither",
    "input_from_pre_dither",
]


#: ``object Dither { case class Input() }``.  Declaration order is the Scala's,
#: which is also the Spinal bit packing (first field at the LSB).
INPUT_LAYOUT = data.StructLayout({
    "r": unsigned(8),
    "g": unsigned(8),
    "b": unsigned(8),
    "x": unsigned(2),
    "y": unsigned(2),
    "enable": unsigned(1),
    "use2x2": unsigned(1),
})

#: ``object Dither { case class Output() }``.
OUTPUT_LAYOUT = data.StructLayout({
    "dit_r": unsigned(5),
    "dit_g": unsigned(6),
    "dit_b": unsigned(5),
})

#: ``object Dither { case class Linked() }`` -- the bypass values piped through
#: the ROM pipeline register alongside the read.
LINKED_LAYOUT = data.StructLayout({
    "enable": unsigned(1),
    "trunc_r": unsigned(5),
    "trunc_g": unsigned(6),
    "trunc_b": unsigned(5),
})

#: ``spinal.lib.Mem.ReadRetLinked(wordType, linkedType)``, the payload of the
#: stream ``streamReadSync`` returns.  Internal only.
READ_RET_LINKED_LAYOUT = data.StructLayout({
    "value": unsigned(5),
    "linked": LINKED_LAYOUT,
})

#: 4096 entries of 4x4 table + 1024 of 2x2, per channel ROM.
ROM_DEPTH = 5120


def input_from_pre_dither(m, dst, pd):
    """``Dither.Input.fromPreDither`` (``Dither.scala:92``).

    ``pd`` is a ``Write.PreDither`` view (``pixel/write.py``, agent
    p4-fog-write).  ``PixelCoords`` (``Bundles.scala:4``) is a *Bundle* with an
    ``apply(idx)`` that maps 0 -> ``x`` and 1 -> ``y``, not a ``Vec``, so
    ``pd.coords(0)`` in the Scala is ``pd.coords.x`` here; both are signed and
    the Scala takes ``.asUInt.resize(2 bits)``, i.e. the low two bits.

    Kept in this module rather than in ``pixel/write.py`` because the Scala
    keeps it in ``object Dither``.  Nothing calls it yet, so it is the one
    thing here the lockstep diff does not cover.
    """
    m.d.comb += [
        dst.r.eq(pd.r),
        dst.g.eq(pd.g),
        dst.b.eq(pd.b),
        dst.x.eq(pd.coords.x[:2]),
        dst.y.eq(pd.coords.y[:2]),
        dst.enable.eq(pd.enable_dithering),
        dst.use2x2.eq(pd.dither_algorithm),
    ]


class Dither(wiring.Component):
    """``case class Dither() extends Component``.

    Attributes
    ----------
    input : In(stream.Signature(INPUT_LAYOUT))
        ``slave Stream(Dither.Input())``.
    output : Out(stream.Signature(OUTPUT_LAYOUT))
        ``master Stream(Dither.Output())``.

    The members are passed to ``super().__init__`` rather than declared as
    class annotations because this module uses ``from __future__ import
    annotations``, under which ``wiring.Component`` sees only strings.
    """

    def __init__(self):
        super().__init__({
            "input": In(stream.Signature(INPUT_LAYOUT)),
            "output": Out(stream.Signature(OUTPUT_LAYOUT)),
        })

    def elaborate(self, platform):
        m = Module()

        # Synchronous ROMs: 3 independent single-port Mems (R, G, B).
        # R and B are initialized with identical rb data but are separate Mems.
        # Layout: [0-4095] = 4x4 table, [4096-5119] = 2x2 table.
        m.submodules.rom_r = rom_r = memory.Memory(
            shape=unsigned(5), depth=ROM_DEPTH, init=rb_packed)
        m.submodules.rom_g = rom_g = memory.Memory(
            shape=unsigned(6), depth=ROM_DEPTH, init=g_packed)
        m.submodules.rom_b = rom_b = memory.Memory(
            shape=unsigned(5), depth=ROM_DEPTH, init=rb_packed)
        rd_r = rom_r.read_port(domain="sync")
        rd_g = rom_g.read_port(domain="sync")
        rd_b = rom_b.read_port(domain="sync")

        in_ = self.input.payload

        # Address computation: pack 4x4 and 2x2 into one address space.
        # 4x4: v(8) ## y(2) ## x(2) = 12 bits, range [0, 4095]
        # 2x2: 1 ## v(8) ## y(1) ## x(1) = bit12 set + 10 bits, [4096, 5119]
        def addr4x4(v):
            return Cat(in_.x, in_.y, v)              # 12 bits

        def addr2x2(v):
            return Cat(in_.x[0], in_.y[0], v)        # 10 bits

        def packed_addr(v):
            # Cat() is LSB-first, so these read backwards from the Scala's
            # MSB-first ``##``; the widths are the resize()s spelled out.
            return Mux(in_.use2x2,
                       Cat(addr2x2(v), Const(0, 2), Const(1, 1)),   # 13 bits
                       Cat(addr4x4(v), Const(0, 1)))                # 13 bits

        def trunc_if_disabled(enabled, dithered, truncated):
            return Mux(enabled, dithered, truncated)

        # Bypass values to link through the ROM pipeline (dithering disabled).
        linked = Signal(LINKED_LAYOUT)
        m.d.comb += [
            linked.enable.eq(in_.enable),
            linked.trunc_r.eq(in_.r >> 3),
            linked.trunc_g.eq(in_.g >> 2),
            linked.trunc_b.eq(in_.b >> 3),
        ]

        # R channel: streamReadSync manages the Stream handshake + backpressure.
        addr_stream = Stream(unsigned(13), path=("addr_stream",))
        m.d.comb += [
            addr_stream.valid.eq(self.input.valid),
            addr_stream.payload.eq(packed_addr(in_.r)),
            self.input.ready.eq(addr_stream.ready),
        ]

        # --- Mem.streamReadSync(addrStream, linked), expanded -------------
        rom_r_result = Stream(READ_RET_LINKED_LAYOUT, path=("rom_r_result",))
        # `val retValid = RegInit(False)` -- this one has a reset.
        ret_valid = Signal(init=0)
        # `RegNextWhen(linkedData, cmd.ready)` -- a plain Reg, no reset
        # (plan §6: otherwise the lockstep diff diverges after a mid-run reset).
        linked_reg_next_when = Signal(LINKED_LAYOUT, reset_less=True)

        m.d.comb += [
            rd_r.addr.eq(addr_stream.payload),
            rd_r.en.eq(fire(addr_stream)),
        ]
        with m.If(addr_stream.ready):
            m.d.sync += linked_reg_next_when.eq(linked)

        # when(ret.ready) { retValid := False } ; when(cmd.ready) { retValid := cmd.valid }
        # The second `when` subsumes the first (ret.ready -> isFree -> cmd.ready),
        # which is why Dither_spinal.v shows only one assignment; both are kept
        # here in the Scala's order, and Amaranth's last-wins matches Spinal's.
        with m.If(rom_r_result.ready):
            m.d.sync += ret_valid.eq(0)
        with m.If(addr_stream.ready):
            m.d.sync += ret_valid.eq(addr_stream.valid)

        m.d.comb += [
            addr_stream.ready.eq(is_free(rom_r_result)),
            rom_r_result.valid.eq(ret_valid),
            rom_r_result.payload.value.eq(rd_r.data),
            rom_r_result.payload.linked.eq(linked_reg_next_when),
        ]
        # ------------------------------------------------------------------

        # G and B channels: readSync piggy-backing on the same fire signal.
        m.d.comb += [
            rd_g.addr.eq(packed_addr(in_.g)),
            rd_g.en.eq(fire(addr_stream)),
            rd_b.addr.eq(packed_addr(in_.b)),
            rd_b.en.eq(fire(addr_stream)),
        ]
        dit_g_rom = rd_g.data
        dit_b_rom = rd_b.data

        # Output: mux between ROM and truncated bypass on the linked enable.
        r_linked = rom_r_result.payload.linked
        m.d.comb += [
            self.output.valid.eq(rom_r_result.valid),
            self.output.payload.dit_r.eq(trunc_if_disabled(
                r_linked.enable,
                rom_r_result.payload.value,
                r_linked.trunc_r,
            )),
            self.output.payload.dit_g.eq(trunc_if_disabled(
                r_linked.enable, dit_g_rom, r_linked.trunc_g)),
            self.output.payload.dit_b.eq(trunc_if_disabled(
                r_linked.enable, dit_b_rom, r_linked.trunc_b)),
            rom_r_result.ready.eq(self.output.ready),
        ]

        return m
