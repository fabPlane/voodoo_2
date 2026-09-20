"""Texel format decode and bilinear blend -- the TMU's back end.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/texture/TmuTexelDecoder.scala``
(SpinalHDL 1.15.0, 235 lines).

Three registered stages, exactly as the Scala chains them::

    io.sampleFetch --m2sPipe--> fetchPipe
                   --translateWith(decode x4)--m2sPipe--> decodedPipe
                   --translateWith(blend + sendConfig)--m2sPipe--> io.output

so the output is three clocks behind the accepted request, and the palette
RAM's ``readSync`` (enabled by ``io.sampleFetch.fire``) lands in time for the
first ``translateWith``.

What the module actually does, per stage:

1. **Palette RAM.** Four *replicated* 256 x 24-bit memories, one per bilinear
   tap, written together from the ``paletteWrite`` Flow.  The replication is
   not an optimisation: the Scala comment says Quartus mis-infers the 1W/4R
   shape on the DE10 build, so each copy is a plain 1W/1R RAM.  The write and
   read ports land in separate ``always`` blocks in the Spinal Verilog, i.e.
   **read-before-write** with no transparency, which is what
   ``Memory.read_port(domain="sync")`` gives by default.  The RAMs have no
   ``init`` in Spinal; Amaranth must attach one, so they come up all-zero
   (Verilator zero-fills the Spinal side too, so the lockstep diff agrees).

2. **Decode.** ``decodeTexelWord`` for each of the four taps: a 13-way
   ``switch`` on ``tformat`` covering every SST-1 texture format, with the
   three unassigned codes (6, 7, 15) falling through to RGB565.  Channel
   expansion is ``Bundles.expandTo8`` (MSB replication, 1-bit -> 0x00/0xFF);
   the two YIQ formats run the NCC decode below; P8/AP88 read the palette.

3. **Blend.** ``Tmu.blendChannel`` on all four channels when ``bilinear``,
   else tap 0 verbatim.  ``io.sendConfig`` -- a level input, not part of the
   payload -- overrides the result with the "config" marker colour
   ``(0, 0, 1, 255)``; being sampled at the *last* stage it applies to
   whatever is in flight, not to the request it arrived with.

NCC (Voodoo's YIQ compression), which is the only arithmetic here worth a
second look::

    y  = ncc.y(texByte(7 downto 4))                # UInt(8)
    iE = ncc.i(texByte(3 downto 2))                # Bits(27): [26:18]=R
    qE = ncc.q(texByte(1 downto 0))                #           [17:9]=G [8:0]=B
    rRaw = (False ## y).asSInt.resize(11) + iR.resize(11) + qR.resize(11)
    r = clampToU8(rRaw)

The adds are Spinal ``SInt + SInt``, which keeps the wider operand's width
rather than growing, so the sum is 11-bit wrapping arithmetic.  With 8-bit Y
and 9-bit signed I/Q the true range is -512..765, inside 11 bits, so no
wrap is reachable -- the port slices to 11 bits anyway so it stays a
transliteration.

``reset_less``: every register in this module lives inside
``avoodoo.lib.stream.M2SPipe``, which already applies the plan's §6 rule
(payload registers ``reset_less=True``, ``rValid`` reset to 0).  The memory
read-port registers have no reset in either HDL.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import data, memory, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import clamp_to_u8, expand_to_8
from avoodoo.lib.stream import FlowSignature, connect_stream, fire, m2s_pipe, translate_with
from avoodoo.texture.tmu_types import (
    BilinearWeights,
    NccTableData,
    NccTables,
    PaletteWrite,
    SampleFetch,
    TextureFormat,
    TmuOutput,
    TmuPassthrough,
    bilinear_weights,
    blend_channel,
)

__all__ = ["DecodedRgba", "DecodedFetch", "ncc_decode", "decode_texel_word",
           "TmuTexelDecoder"]

#: ``Seq.fill(4)(Mem(Bits(24 bits), 256))``.
PALETTE_DEPTH = 256
PALETTE_WIDTH = 24

#: The four bilinear taps.  Named because the Scala hard-codes ``4`` in five
#: places and the replication count of the palette RAM has to track it.
TAPS = 4


class DecodedRgba(data.StructLayout):
    """``case class DecodedRgba()`` -- one decoded texel, 8 bits per channel."""

    def __init__(self) -> None:
        super().__init__({
            "r": unsigned(8),
            "g": unsigned(8),
            "b": unsigned(8),
            "a": unsigned(8),
        })


class DecodedFetch(data.StructLayout):
    """``case class DecodedFetch()`` -- the payload between the two inner pipes."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "passthrough": TmuPassthrough(c),
            "bilinear": unsigned(1),
            "weights": BilinearWeights(),
            "texels": data.ArrayLayout(DecodedRgba(), TAPS),
        })


def ncc_decode(tex_byte, ncc):
    """``def nccDecode(texByte: Bits, nccData: Tmu.NccTableData)``.

    Returns the ``(r, g, b)`` expressions; alpha is the caller's business
    (0xFF for YIQ422, the high byte for AYIQ8422).
    """
    y_val = ncc.y[tex_byte[4:8]]
    i_entry = ncc.i[tex_byte[2:4]]
    q_entry = ncc.q[tex_byte[0:2]]
    # `False ## yVal` widens Y to 9 bits so `.asSInt` cannot read it negative.
    y_signed = Cat(y_val, Const(0, 1)).as_signed()

    def channel(lo: int, hi: int):
        i_c = i_entry[lo:hi].as_signed()
        q_c = q_entry[lo:hi].as_signed()
        # Spinal's SInt + SInt keeps the wider width: 11-bit wrapping adds.
        raw = (y_signed + i_c + q_c)[0:11].as_signed()
        return clamp_to_u8(raw)

    return channel(18, 27), channel(9, 18), channel(0, 9)


def decode_texel_word(m, decoded, texel_data, fmt, ncc, palette_color):
    """``def decodeTexelWord(texelData, format, ncc, paletteColor): DecodedRgba``.

    Drives ``decoded`` (a :class:`DecodedRgba` view) combinationally.  Written
    as a procedure rather than a function returning a value because Amaranth's
    ``m.Switch`` needs the target up front, which is also how the Scala reads
    (it declares ``val decoded`` then assigns inside the ``switch``).
    """
    texel_byte = texel_data[0:8]
    tf = TextureFormat

    with m.Switch(fmt):
        with m.Case(tf.RGB332):
            m.d.comb += [
                decoded.r.eq(expand_to_8(texel_byte[5:8], 3)),
                decoded.g.eq(expand_to_8(texel_byte[2:5], 3)),
                decoded.b.eq(expand_to_8(texel_byte[0:2], 2)),
                decoded.a.eq(255),
            ]
        with m.Case(tf.YIQ422):
            r, g, b = ncc_decode(texel_byte, ncc)
            m.d.comb += [
                decoded.r.eq(r), decoded.g.eq(g), decoded.b.eq(b),
                decoded.a.eq(255),
            ]
        with m.Case(tf.A8):
            m.d.comb += [
                decoded.r.eq(texel_byte), decoded.g.eq(texel_byte),
                decoded.b.eq(texel_byte), decoded.a.eq(texel_byte),
            ]
        with m.Case(tf.I8):
            m.d.comb += [
                decoded.r.eq(texel_byte), decoded.g.eq(texel_byte),
                decoded.b.eq(texel_byte), decoded.a.eq(255),
            ]
        with m.Case(tf.AI44):
            alpha = texel_byte[4:8]
            intensity = texel_byte[0:4]
            m.d.comb += [
                decoded.r.eq(expand_to_8(intensity, 4)),
                decoded.g.eq(expand_to_8(intensity, 4)),
                decoded.b.eq(expand_to_8(intensity, 4)),
                decoded.a.eq(expand_to_8(alpha, 4)),
            ]
        with m.Case(tf.P8):
            m.d.comb += [
                decoded.r.eq(palette_color[16:24]),
                decoded.g.eq(palette_color[8:16]),
                decoded.b.eq(palette_color[0:8]),
                decoded.a.eq(255),
            ]
        with m.Case(tf.ARGB8332):
            m.d.comb += [
                decoded.a.eq(texel_data[8:16]),
                decoded.r.eq(expand_to_8(texel_data[5:8], 3)),
                decoded.g.eq(expand_to_8(texel_data[2:5], 3)),
                decoded.b.eq(expand_to_8(texel_data[0:2], 2)),
            ]
        with m.Case(tf.AYIQ8422):
            r, g, b = ncc_decode(texel_data[0:8], ncc)
            m.d.comb += [
                decoded.r.eq(r), decoded.g.eq(g), decoded.b.eq(b),
                decoded.a.eq(texel_data[8:16]),
            ]
        with m.Case(tf.RGB565):
            m.d.comb += [
                decoded.r.eq(expand_to_8(texel_data[11:16], 5)),
                decoded.g.eq(expand_to_8(texel_data[5:11], 6)),
                decoded.b.eq(expand_to_8(texel_data[0:5], 5)),
                decoded.a.eq(255),
            ]
        with m.Case(tf.ARGB1555):
            m.d.comb += [
                decoded.a.eq(expand_to_8(texel_data[15:16], 1)),
                decoded.r.eq(expand_to_8(texel_data[10:15], 5)),
                decoded.g.eq(expand_to_8(texel_data[5:10], 5)),
                decoded.b.eq(expand_to_8(texel_data[0:5], 5)),
            ]
        with m.Case(tf.ARGB4444):
            m.d.comb += [
                decoded.a.eq(expand_to_8(texel_data[12:16], 4)),
                decoded.r.eq(expand_to_8(texel_data[8:12], 4)),
                decoded.g.eq(expand_to_8(texel_data[4:8], 4)),
                decoded.b.eq(expand_to_8(texel_data[0:4], 4)),
            ]
        with m.Case(tf.AI88):
            alpha = texel_data[8:16]
            intensity = texel_data[0:8]
            m.d.comb += [
                decoded.r.eq(intensity), decoded.g.eq(intensity),
                decoded.b.eq(intensity), decoded.a.eq(alpha),
            ]
        with m.Case(tf.AP88):
            m.d.comb += [
                decoded.r.eq(palette_color[16:24]),
                decoded.g.eq(palette_color[8:16]),
                decoded.b.eq(palette_color[0:8]),
                decoded.a.eq(texel_data[8:16]),
            ]
        with m.Default():
            # Codes 6, 7 and 15 are unassigned; the Scala treats them as RGB565.
            m.d.comb += [
                decoded.r.eq(expand_to_8(texel_data[11:16], 5)),
                decoded.g.eq(expand_to_8(texel_data[5:11], 6)),
                decoded.b.eq(expand_to_8(texel_data[0:5], 5)),
                decoded.a.eq(255),
            ]


class TmuTexelDecoder(wiring.Component):
    """``case class TmuTexelDecoder(c: voodoo.Config) extends Component``.

    Port names follow the diff harness's mechanical rule, so
    ``io_sampleFetch_payload_passthrough_nccTableSelect`` pairs with
    ``sample_fetch__payload`` bit-sliced at ``passthrough.ncc_table_select``
    without an entry in ``tools/diff/registry.py``'s override table.
    """

    def __init__(self, c: Config | None = None) -> None:
        self.c = c if c is not None else Config.voodoo1()
        super().__init__({
            "sample_fetch": In(stream.Signature(SampleFetch(self.c))),
            "output": Out(stream.Signature(TmuOutput(self.c))),
            "palette_write": In(FlowSignature(PaletteWrite())),
            "send_config": In(1),
            "ncc_tables": In(NccTables()),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c

        # -- the four replicated palette RAMs ------------------------------
        #
        #   val paletteRam = Seq.fill(4)(Mem(Bits(24 bits), 256))
        #   when(io.paletteWrite.valid) { for (ram <- paletteRam) ram.write(...) }
        #   paletteRam(idx).readSync(texels(idx)(7 downto 0), enable = fire)
        palette_colors = []
        for idx in range(TAPS):
            ram = memory.Memory(shape=unsigned(PALETTE_WIDTH),
                                depth=PALETTE_DEPTH, init=[])
            m.submodules[f"paletteRam_{idx}"] = ram
            write = ram.write_port()
            m.d.comb += [
                write.addr.eq(self.palette_write.payload.address),
                write.data.eq(self.palette_write.payload.data),
                write.en.eq(self.palette_write.valid),
            ]
            read = ram.read_port(domain="sync")
            m.d.comb += [
                read.addr.eq(self.sample_fetch.payload.texels[idx][0:8]),
                read.en.eq(fire(self.sample_fetch)),
            ]
            palette_colors.append(read.data)

        # -- stage 1: val fetchPipe = io.sampleFetch.m2sPipe() -------------
        fetch_pipe = m2s_pipe(m, self.sample_fetch, name="fetchPipe")
        fetch = fetch_pipe.payload
        passthrough = fetch.passthrough

        # -- stage 2: fetchPipe.translateWith { ... }.m2sPipe() ------------
        ncc = Signal(NccTableData(), name="ncc")
        m.d.comb += ncc.eq(Mux(passthrough.ncc_table_select,
                               self.ncc_tables.table1.as_value(),
                               self.ncc_tables.table0.as_value()))

        decoded_fetch = Signal(DecodedFetch(c), name="decodedFetch")
        weights = bilinear_weights(m, passthrough.ds, passthrough.dt)
        m.d.comb += [
            decoded_fetch.passthrough.eq(passthrough),
            decoded_fetch.bilinear.eq(fetch.bilinear),
            decoded_fetch.weights.eq(weights),
        ]
        for idx in range(TAPS):
            decode_texel_word(m, decoded_fetch.texels[idx], fetch.texels[idx],
                              passthrough.format, ncc, palette_colors[idx])

        decoded_pipe = m2s_pipe(m, translate_with(m, fetch_pipe, decoded_fetch),
                                name="decodedPipe")

        # -- stage 3: decodedPipe.translateWith { ... }.m2sPipe() ----------
        decoded = decoded_pipe.payload
        result = Signal(TmuOutput(c), name="result")

        def channel(idx: int, name: str):
            return getattr(decoded.texels[idx], name)

        def blended(name: str):
            return blend_channel(decoded.weights, channel(0, name),
                                 channel(1, name), channel(2, name),
                                 channel(3, name))

        m.d.comb += [
            result.texture.r.eq(Mux(decoded.bilinear, blended("r"), channel(0, "r"))),
            result.texture.g.eq(Mux(decoded.bilinear, blended("g"), channel(0, "g"))),
            result.texture.b.eq(Mux(decoded.bilinear, blended("b"), channel(0, "b"))),
            result.texture_alpha.eq(
                Mux(decoded.bilinear, blended("a"), channel(0, "a"))),
        ]
        # `when(io.sendConfig)` comes last in the Scala, and last write wins.
        with m.If(self.send_config):
            m.d.comb += [
                result.texture.r.eq(0),
                result.texture.g.eq(0),
                result.texture.b.eq(1),
                result.texture_alpha.eq(255),
            ]
        if c.trace.enabled:
            m.d.comb += result.trace.eq(decoded.passthrough.trace)

        out_pipe = m2s_pipe(m, translate_with(m, decoded_pipe, result),
                            name="outputPipe")
        connect_stream(m, self.output, out_pipe)
        return m
