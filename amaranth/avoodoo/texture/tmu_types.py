"""``object Tmu``'s record layouts and small combinational helpers.

Port of the bundle declarations and pure functions of
``ext/SpinalVoodoo/src/voodoo/texture/Tmu.scala`` (SpinalHDL 1.15.0) that the
texture path shares -- *not* the ``Tmu`` Component itself, which plan Phase 5
gives its own module (``avoodoo/texture/tmu.py``).  Only the pieces
``TmuTexelDecoder`` needs are here so far; ``tmu.py`` and
``tmu_texture_cache.py`` append the rest.

Scala lines, in this file's order:

===========================================  ======================
``Tmu.scala``                                here
===========================================  ======================
``requestIdWidth`` (l. 726)                  :data:`REQUEST_ID_WIDTH`
``case class PaletteWrite`` (l. 854)         :class:`PaletteWrite`
``case class TmuPassthrough`` (l. 860)       :class:`TmuPassthrough`
``case class SampleFetch`` (l. 1008)         :class:`SampleFetch`
``object TextureFormat`` (l. 1044)           :class:`TextureFormat`
``case class NccTableData`` (l. 1097)        :class:`NccTableData`
``case class NccTables`` (l. 1103)           :class:`NccTables`
``case class Output`` (l. 1163)              :class:`TmuOutput`
``case class BilinearWeights`` (l. 728)      :class:`BilinearWeights`
``def bilinearWeights`` (l. 739)             :func:`bilinear_weights`
``def blendChannel`` (l. 752)                :func:`blend_channel`
``case class PackedStartupDecode`` (l. 638)  :class:`PackedStartupDecode`
``packedStartupOffsetLowWidth`` (l. 643)     :data:`PACKED_STARTUP_OFFSET_LOW_WIDTH`
``def lineBase22Of`` (l. 645)                :func:`line_base22_of`
``def correctedTexEnd`` (l. 648)             :func:`corrected_tex_end`
``def packedLocation22`` (l. 661)            :func:`packed_location22`
``def packedStartupDecode4`` (l. 700)        :func:`packed_startup_decode4`
``def clz32`` / ``def clz64`` (l. 762, 788)  :func:`clz32` / :func:`clz64`
``def wrapOrClamp`` (l. 798)                 :func:`wrap_or_clamp`
``case class FrontRecipPrep`` (l. 869)       :class:`FrontRecipPrep`
``case class FrontA`` (l. 887)               :class:`FrontA`
``case class FrontRecip`` (l. 900)           :class:`FrontRecip`
``case class FrontB`` (l. 916)               :class:`FrontB`
``case class FrontLodPrep`` (l. 929)         :class:`FrontLodPrep`
``case class FrontLod`` (l. 939)             :class:`FrontLod`
``case class FrontGrad`` (l. 949)            :class:`FrontGrad`
``case class FrontMeta`` (l. 959)            :class:`FrontMeta`
``case class SampleRequest`` (l. 964)        :class:`SampleRequest`
``case class SampleRequestPrep`` (l. 980)    :class:`SampleRequestPrep`
``case class CachedReq`` (l. 995)            :class:`CachedReq`
``case class TextureMode`` (l. 1015)         :class:`TextureMode`
``case class TLOD`` (l. 1061)                :class:`TLOD`
``case class TmuConfig`` (l. 1110)           :class:`TmuConfig`
``case class Input`` (l. 1122)               :class:`TmuInput`
===========================================  ======================

``TextureMode.decode(bits)`` and ``TLOD.decode(bits)`` need no function: the
two Bundles list their fields in exactly the register's bit order, so
``TextureMode()(texture_mode)`` / ``TLOD()(t_lod)`` -- a ``data.View`` over
the raw register -- *is* the decode.  ``Tmu.TmuConfig`` is a different bundle
from ``TriangleSetup.TmuConfig`` (``core_types.tmu_config_layout``): it drops
the four gradients, which ``Tmu.Input`` carries at top level instead.

Conventions are ``core/core_types.py``'s: field names are
``tools.diff.naming.camel_to_snake`` of the Spinal names (``nccTableSelect``
-> ``ncc_table_select``, ``textureAlpha`` -> ``texture_alpha``, ``table0``
stays ``table0``) so the lockstep harness pairs ports without an override,
and declaration order is Spinal's so the packed bit layout matches.

The two arithmetic helpers are transliterations down to the *widths*, because
both of them wrap:

* ``bilinearWeights`` subtracts in 5 bits (``U(16, 5 bits) - ds5``) and keeps
  the 10-bit product;
* ``blendChannel`` sums four 28-bit products in 28 bits (Spinal's ``UInt +
  UInt`` keeps the wider operand's width, it does not grow) and returns
  ``sum(15 downto 8)``.
"""

from __future__ import annotations

from amaranth.hdl import Cat, Const, Mux, Signal, signed, unsigned
from amaranth.lib import data

from avoodoo.config import Config
from avoodoo.core.core_types import (
    TRACE_PIXEL_KEY_LAYOUT,
    Color,
    tex_layout_tables_layout,
)
from avoodoo.lib.fixed import AFixShape
from avoodoo.lib.intops import sext, u_add, u_sub, zext

__all__ = [
    "REQUEST_ID_WIDTH",
    "TextureFormat",
    "PaletteWrite",
    "TmuPassthrough",
    "SampleFetch",
    "BilinearWeights",
    "NccTableData",
    "NccTables",
    "TmuOutput",
    "bilinear_weights",
    "blend_channel",
    # -- appended for tmu.py / tmu_texture_cache.py ---------------------
    "PACKED_STARTUP_OFFSET_LOW_WIDTH",
    "PackedStartupDecode",
    "TextureMode",
    "TLOD",
    "TmuConfig",
    "FrontMeta",
    "TmuInput",
    "FrontRecipPrep",
    "FrontRecip",
    "FrontA",
    "FrontGrad",
    "FrontLodPrep",
    "FrontLod",
    "FrontB",
    "SampleRequestPrep",
    "SampleRequest",
    "CachedReq",
    "line_base22_of",
    "corrected_tex_end",
    "packed_location22",
    "packed_startup_decode4",
    "clz32",
    "clz64",
    "wrap_or_clamp",
]

#: ``val requestIdWidth = 3``.
REQUEST_ID_WIDTH = 3


class TextureFormat:
    """``object Tmu.TextureFormat`` -- the ``tformat`` field, textureMode[11:8].

    6, 7 and 15 are unassigned; the decoder's ``default`` branch treats them
    as RGB565.
    """

    RGB332 = 0
    YIQ422 = 1
    A8 = 2
    I8 = 3
    AI44 = 4
    P8 = 5
    ARGB8332 = 8
    AYIQ8422 = 9
    RGB565 = 10
    ARGB1555 = 11
    ARGB4444 = 12
    AI88 = 13
    AP88 = 14

    #: Every assigned code, low to high -- handy for test sweeps.
    ALL = (RGB332, YIQ422, A8, I8, AI44, P8, ARGB8332, AYIQ8422,
           RGB565, ARGB1555, ARGB4444, AI88, AP88)

    #: The codes that fall through to the ``default`` (RGB565) branch.
    UNASSIGNED = (6, 7, 15)


class PaletteWrite(data.StructLayout):
    """``case class PaletteWrite()`` -- one 24-bit RGB888 palette entry."""

    def __init__(self) -> None:
        super().__init__({
            "address": unsigned(8),
            "data": unsigned(24),
        })


class TmuPassthrough(data.StructLayout):
    """``case class TmuPassthrough(c)`` -- metadata carried alongside a sample.

    ``trace`` exists only when ``c.trace.enabled``; Spinal drops the ``null``
    Bundle field otherwise.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        fields = {
            "format": unsigned(4),
            "bilinear": unsigned(1),
            "ncc_table_select": unsigned(1),
            "ds": unsigned(4),
            "dt": unsigned(4),
        }
        if c.trace.enabled:
            fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(fields)


class SampleFetch(data.StructLayout):
    """``case class SampleFetch(c)`` -- the four raw texel words plus metadata.

    ``texels(0)`` is the point-sampled texel; 1..3 are the +s, +t and +s+t
    neighbours the bilinear blend needs.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "bilinear": unsigned(1),
            "texels": data.ArrayLayout(unsigned(16), 4),
            "passthrough": TmuPassthrough(c),
        })


class BilinearWeights(data.StructLayout):
    """``case class BilinearWeights()`` -- the four 4.4-ish blend weights."""

    def __init__(self) -> None:
        super().__init__({
            "w0": unsigned(10),
            "w1": unsigned(10),
            "w2": unsigned(10),
            "w3": unsigned(10),
        })


class NccTableData(data.StructLayout):
    """``case class NccTableData()`` -- one NCC (YIQ) decompression table.

    ``y`` holds 16 unsigned 8-bit luminance levels; ``i`` and ``q`` hold four
    27-bit chrominance entries each, packed ``[26:18]=R [17:9]=G [8:0]=B`` as
    9-bit *signed* offsets.  The Bundle keeps them as raw ``Bits(27)``, so the
    layout does too and the decoder does the sign extraction.
    """

    def __init__(self) -> None:
        super().__init__({
            "y": data.ArrayLayout(unsigned(8), 16),
            "i": data.ArrayLayout(unsigned(27), 4),
            "q": data.ArrayLayout(unsigned(27), 4),
        })


class NccTables(data.StructLayout):
    """``case class NccTables()`` -- the two tables ``tnccselect`` picks between."""

    def __init__(self) -> None:
        super().__init__({
            "table0": NccTableData(),
            "table1": NccTableData(),
        })


class TmuOutput(data.StructLayout):
    """``case class Tmu.Output(c)`` -- the decoded texel handed to the pixel pipe.

    Named ``TmuOutput`` rather than ``Output`` so it does not collide with
    ``amaranth.lib.wiring.Out`` at a glance, or with the several other
    ``Output`` bundles the core has.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        fields = {
            "texture": Color.u8(),
            "texture_alpha": unsigned(8),
        }
        if c.trace.enabled:
            fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(fields)


# ---------------------------------------------------------------------------
# object Tmu -- combinational helpers
# ---------------------------------------------------------------------------


def _zext(v, width):
    """``UInt.resize(width)`` -- zero-extend or truncate, never sign-extend."""
    v = v.as_unsigned()
    if len(v) >= width:
        return v[:width]
    return Cat(v, Const(0, width - len(v)))


def bilinear_weights(m, ds, dt, *, name="weights"):
    """``def bilinearWeights(ds: UInt, dt: UInt): BilinearWeights``.

    ::

        ds5 = ds.resize(5 bits) ; invDs = U(16, 5 bits) - ds5
        w0 = (invDs * invDt).resize(10 bits)   w1 = (ds5 * invDt).resize(10)
        w2 = (invDs * dt5).resize(10 bits)     w3 = (ds5 * dt5).resize(10)

    ``ds``/``dt`` are 4 bits, so ``invDs`` is 1..16 and the 5-bit subtraction
    never wraps in practice -- but it is written as a 5-bit subtraction
    anyway, because the Scala is, and ``Tmu`` may hand it a wider value later.
    The products are 10 bits wide by construction (5 x 5), so ``resize(10)``
    is a no-op; the slice is spelled out so a wider ``ds`` would truncate the
    same way Spinal does.
    """
    out = Signal(BilinearWeights(), name=name)
    ds5 = _zext(ds, 5)
    dt5 = _zext(dt, 5)
    inv_ds = (Const(16, 5) - ds5)[:5]
    inv_dt = (Const(16, 5) - dt5)[:5]
    m.d.comb += [
        out.w0.eq((inv_ds * inv_dt)[:10]),
        out.w1.eq((ds5 * inv_dt)[:10]),
        out.w2.eq((inv_ds * dt5)[:10]),
        out.w3.eq((ds5 * dt5)[:10]),
    ]
    return out


def blend_channel(weights, t0, t1, t2, t3):
    """``def blendChannel(weights, t0, t1, t2, t3): UInt``.

    ::

        sum = t0.resize(18) * w0 + t1.resize(18) * w1
            + t2.resize(18) * w2 + t3.resize(18) * w3
        (sum >> 8).resize(8 bits)

    Each product is 28 bits (18 + 10) and Spinal's ``UInt + UInt`` keeps the
    wider width, so the running sum stays 28 bits.  With 8-bit texels the
    maximum is ``4 * 255 * 256 = 261120``, well inside 28 bits, so nothing
    wraps; the result is ``sum(15 downto 8)``, i.e. a divide by 256 where the
    weights sum to 256.
    """
    total = (_zext(t0, 18) * weights.w0 + _zext(t1, 18) * weights.w1
             + _zext(t2, 18) * weights.w2 + _zext(t3, 18) * weights.w3)
    return total[8:16]


# ---------------------------------------------------------------------------
# object Tmu -- the rest of the bundles (appended for tmu.py, plan Phase 5)
# ---------------------------------------------------------------------------

#: ``val packedStartupOffsetLowWidth = 10``.
PACKED_STARTUP_OFFSET_LOW_WIDTH = 10


class PackedStartupDecode(data.StructLayout):
    """``case class PackedStartupDecode()`` -- per-byte bank/pair of a line start."""

    def __init__(self) -> None:
        super().__init__({
            "valid": unsigned(4),
            "bank": data.ArrayLayout(unsigned(2), 4),
            "pair": unsigned(4),
        })


class TextureMode(data.StructLayout):
    """``case class TextureMode()`` == ``TextureMode.decode(textureMode)``.

    The fields sit at exactly the register bits ``decode`` reads (0, 1, 2, 3,
    4, 5, 6, 7 and 11:8), so viewing the 12-bit ``textureMode`` through this
    layout is the decode.
    """

    def __init__(self) -> None:
        super().__init__({
            "perspective_enable": unsigned(1),  # bit 0: tpersp_st
            "min_filter": unsigned(1),          # bit 1: tminfilter
            "mag_filter": unsigned(1),          # bit 2: tmagfilter
            "clamp_w": unsigned(1),             # bit 3: tclampw
            "lod_dither": unsigned(1),          # bit 4: tloddither
            "ncc_select": unsigned(1),          # bit 5: tnccselect
            "clamp_s": unsigned(1),             # bit 6: tclamps
            "clamp_t": unsigned(1),             # bit 7: tclampt
            "format": unsigned(4),              # bits 11:8: tformat
        })


class TLOD(data.StructLayout):
    """``case class TLOD()`` == ``TLOD.decode(tLOD)`` -- same trick, 27 bits."""

    def __init__(self) -> None:
        super().__init__({
            "lodmin": unsigned(6),           # 5:0
            "lodmax": unsigned(6),           # 11:6
            "lodbias": unsigned(6),          # 17:12 (Bits; read as signed by Tmu)
            "lod_odd": unsigned(1),          # 18
            "lod_split": unsigned(1),        # 19
            "lod_s_is_wider": unsigned(1),   # 20
            "lod_aspect": unsigned(2),       # 22:21
            "lod_zero_frac": unsigned(1),    # 23
            "tmultibaseaddr": unsigned(1),   # 24
            "tdata_swizzle": unsigned(1),    # 25
            "tdata_swap": unsigned(1),       # 26
        })


class TmuConfig(data.StructLayout):
    """``case class Tmu.TmuConfig(c)`` -- the per-triangle TMU register capture.

    Not ``TriangleSetup.TmuConfig``: that one also carries ``dSdX``..``dTdY``,
    which ``Tmu.Input`` keeps at top level.  ``texTables`` only when
    ``c.packedTexLayout``.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        fields = {
            "texture_mode": unsigned(12),
            "tex_base_addr": unsigned(24),
            "tex_base_addr1": unsigned(24),
            "tex_base_addr2": unsigned(24),
            "tex_base_addr38": unsigned(24),
            "t_lod": unsigned(27),
            "texture_enable": unsigned(1),
        }
        if c.packed_tex_layout:
            fields["tex_tables"] = tex_layout_tables_layout()
        super().__init__(fields)


class FrontMeta(data.StructLayout):
    """``case class FrontMeta(c)`` -- config (+ trace) carried down the front end."""

    def __init__(self, c: Config) -> None:
        self.c = c
        fields = {"config": TmuConfig(c)}
        if c.trace.enabled:
            fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(fields)


def _tex_accum(c: Config) -> AFixShape:
    return AFixShape.from_q(c.tex_coords_accum_format)


def _tex(c: Config) -> AFixShape:
    return AFixShape.from_q(c.tex_coords_format)


def _grads(c: Config) -> dict:
    tex = _tex(c)
    return {"d_sd_x": tex, "d_td_x": tex, "d_sd_y": tex, "d_td_y": tex}


class TmuInput(data.StructLayout):
    """``case class Tmu.Input(c)`` -- what the Rasterizer (or TMU0) sends."""

    def __init__(self, c: Config) -> None:
        self.c = c
        fields = {
            "s": _tex_accum(c),
            "t": _tex_accum(c),
            "w": AFixShape.from_q(c.w_accum_format),
            "c_other": Color.u8(),
            "a_other": unsigned(8),
            "config": TmuConfig(c),
            **_grads(c),
        }
        if c.trace.enabled:
            fields["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(fields)


class FrontRecipPrep(data.StructLayout):
    """``case class FrontRecipPrep(c)`` -- after the ROM lookups."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "s": _tex_accum(c),
            "t": _tex_accum(c),
            "t_lod": unsigned(27),
            "base": unsigned(17),
            "diff": unsigned(17),
            "frac": unsigned(8),
            "log_frac": unsigned(8),
            "msb_pos": unsigned(7),
            "valid_oow": unsigned(1),
            "perspective_enable": unsigned(1),
            "clamp_to_zero": unsigned(1),
            **_grads(c),
            "meta": FrontMeta(c),
        })


class FrontRecip(data.StructLayout):
    """``case class FrontRecip(c)`` -- after the reciprocal interpolation."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "s": _tex_accum(c),
            "t": _tex_accum(c),
            "t_lod": unsigned(27),
            "interp": unsigned(17),
            "msb_pos": unsigned(7),
            "perspective_enable": unsigned(1),
            "clamp_to_zero": unsigned(1),
            "persp_lod_adjust": signed(16),
            **_grads(c),
            "meta": FrontMeta(c),
        })


class FrontA(data.StructLayout):
    """``case class FrontA(c)`` -- perspective-divided S/T (14.4 in 18 bits)."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "tex_s": signed(18),
            "tex_t": signed(18),
            "t_lod": unsigned(27),
            "persp_lod_adjust": signed(16),
            "clamp_to_zero": unsigned(1),
            **_grads(c),
            "meta": FrontMeta(c),
        })


class FrontGrad(data.StructLayout):
    """``case class FrontGrad(c)`` -- with the squared-gradient magnitude."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "tex_s": signed(18),
            "tex_t": signed(18),
            "t_lod": unsigned(27),
            "clamp_to_zero": unsigned(1),
            "temp_lod": unsigned(64),
            "persp_lod_adjust": signed(16),
            "meta": FrontMeta(c),
        })


class FrontLodPrep(data.StructLayout):
    """``case class FrontLodPrep(c)`` -- with the log2 base LOD (8.8)."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "tex_s": signed(18),
            "tex_t": signed(18),
            "t_lod": unsigned(27),
            "clamp_to_zero": unsigned(1),
            "base_lod_8_8": signed(16),
            "persp_lod_adjust": signed(16),
            "meta": FrontMeta(c),
        })


class FrontLod(data.StructLayout):
    """``case class FrontLod(c)`` -- clamped LOD level and mip dimensions."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "tex_s": signed(18),
            "tex_t": signed(18),
            "clamp_to_zero": unsigned(1),
            "lod_level": unsigned(4),
            "tex_width_bits": unsigned(4),
            "tex_height_bits": unsigned(4),
            "meta": FrontMeta(c),
        })


class FrontB(data.StructLayout):
    """``case class FrontB(c)`` -- point / bilinear texel coordinates."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "final_s_point": signed(14),
            "final_t_point": signed(14),
            "final_si": signed(14),
            "final_ti": signed(14),
            "final_ds": unsigned(4),
            "final_dt": unsigned(4),
            "lod_level": unsigned(4),
            "tex_width_bits": unsigned(4),
            "tex_height_bits": unsigned(4),
            "meta": FrontMeta(c),
        })


def _sample_request_head(c: Config) -> dict:
    aw = c.address_width
    fields = {
        "point_addr": unsigned(aw),
        "bi_addr0": unsigned(aw),
        "bi_addr1": unsigned(aw),
        "bi_addr2": unsigned(aw),
        "bi_addr3": unsigned(aw),
        "lod_base": unsigned(aw),
        "lod_shift": unsigned(4),
        "is16_bit": unsigned(1),
    }
    if c.packed_tex_layout:
        fields["tex_tables"] = tex_layout_tables_layout()
    return fields


class SampleRequestPrep(data.StructLayout):
    """``case class SampleRequestPrep(c)`` -- addresses plus the 4 line bases."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            **_sample_request_head(c),
            "tap_line_base": data.ArrayLayout(unsigned(22), 4),
            "bilinear": unsigned(1),
            "passthrough": TmuPassthrough(c),
        })


class SampleRequest(data.StructLayout):
    """``case class SampleRequest(c)`` -- what the texture path is handed."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            **_sample_request_head(c),
            "tap_startup_decode": data.ArrayLayout(PackedStartupDecode(), 4),
            "tap_packed_bank": data.ArrayLayout(unsigned(2), 4),
            "tap_packed_pair": data.ArrayLayout(unsigned(1), 4),
            "bilinear": unsigned(1),
            "passthrough": TmuPassthrough(c),
        })


class CachedReq(data.StructLayout):
    """``case class CachedReq(c, bankEntryWidth)`` -- one cache-line fill request."""

    def __init__(self, c: Config, bank_entry_width: int) -> None:
        self.c = c
        self.bank_entry_width = bank_entry_width
        aw = c.address_width
        fields = {
            "line_base": unsigned(aw),
            "bank_sel": unsigned(2),
            "bank_entry": unsigned(bank_entry_width),
            "lod_base": unsigned(aw),
            "lod_shift": unsigned(4),
            "is16_bit": unsigned(1),
        }
        if c.packed_tex_layout:
            fields["tex_tables"] = tex_layout_tables_layout()
        fields["startup_decode"] = PackedStartupDecode()
        super().__init__(fields)


# ---------------------------------------------------------------------------
# object Tmu -- the address / packed-layout helpers
# ---------------------------------------------------------------------------


def line_base22_of(addr, line_byte_shift: int):
    """``def lineBase22Of(addr: UInt, lineByteShift: Int): UInt``.

    ``((addr.resize(22) >> n) << n).resize(22)`` -- the 22-bit address with
    its low ``n`` bits cleared.
    """
    a22 = zext(addr, 22)
    return Cat(Const(0, line_byte_shift), a22[line_byte_shift:22])


def corrected_tex_end(tex_base, tex_end_raw, is16_bit):
    """``def correctedTexEnd(texBase, texEndRaw, is16Bit): Vec[UInt]``.

    For every LOD, the raw end if it lies past the base, else the next LOD's
    base -- and for the last LOD, ``base + (is16Bit ? 2 : 1)`` in 22 bits.
    """
    out = []
    n = len(tex_base)
    for lod in range(n):
        base = tex_base[lod]
        raw_end = tex_end_raw[lod]
        if lod < n - 1:
            fallback = tex_base[lod + 1]
        else:
            fallback = u_add(base, Mux(is16_bit, Const(2, 22), Const(1, 22)), 22)
        out.append(Mux(raw_end > base, raw_end, fallback))
    return out


def _dyn_bit(value, index):
    """``value(index)`` with a UInt index -- 0 when ``index >= len(value)``.

    Spinal emits a plain Verilog ``value[index]``; an out-of-range variable
    bit-select reads as X in the LRM and as 0 in Verilator, and the lockstep
    diff reaches it (``lodShift`` is a free input).  A logical right shift
    followed by bit 0 is 0 there on every simulator and synthesiser.
    """
    return (value.as_unsigned() >> index)[0]


def packed_location22(addr, is16_bit, lod_base, lod_shift, tex_base, tex_end,
                      tex_shift):
    """``def packedLocation22(...)``: ``(in, bank, pair)`` for one byte address.

    The ``for (lod <- texBase.indices) when(...)`` chain is *last match
    wins*: a later LOD range that also contains ``addr`` overrides an earlier
    one, which a Mux chain built in the same order reproduces.
    """
    addr22 = zext(addr, 22)
    in_ = Const(0, 1)
    base = zext(lod_base, 22)
    shift = lod_shift
    for lod in range(len(tex_base)):
        hit = (addr22 >= tex_base[lod]) & (addr22 < tex_end[lod])
        in_ = Mux(hit, Const(1, 1), in_)
        base = Mux(hit, tex_base[lod], base)
        shift = Mux(hit, tex_shift[lod], shift)
    low = PACKED_STARTUP_OFFSET_LOW_WIDTH
    byte_offs_low = u_sub(addr22[0:low], base[0:low], low)
    bank_msb = _dyn_bit(byte_offs_low, shift)
    bank_lsb = byte_offs_low[0]
    # `(shift + 1).resized`: a 4-bit wrapping add, so shift 15 reads bit 0.
    shift_p1 = u_add(shift, Const(1, 1), 4)
    bank_msb = Mux(is16_bit, bank_msb | _dyn_bit(byte_offs_low, shift_p1), bank_msb)
    bank_lsb = Mux(is16_bit, bank_lsb | byte_offs_low[1], bank_lsb)
    # `(bankMsb ## bankLsb).asUInt`: msb is the high bit.
    bank = Cat(bank_lsb, bank_msb)
    pair = ~is16_bit & byte_offs_low[1]
    return in_, bank, pair


def packed_startup_decode4(m, line_base, is16_bit, lod_base, lod_shift, tex_base,
                           tex_end, tex_shift, *, name="startupDecode"):
    """``def packedStartupDecode4(...)``: the 4 bytes at ``lineBase + 0..3``.

    Drives and returns a :class:`PackedStartupDecode` signal.
    """
    out = Signal(PackedStartupDecode(), name=name)
    lb22 = zext(line_base, 22)
    for byte_idx in range(4):
        in_, bank, pair = packed_location22(
            u_add(lb22, Const(byte_idx, 22), 22), is16_bit, lod_base, lod_shift,
            tex_base, tex_end, tex_shift)
        m.d.comb += [
            out.valid[byte_idx].eq(in_),
            out.bank[byte_idx].eq(bank),
            out.pair[byte_idx].eq(pair),
        ]
    return out


# ---------------------------------------------------------------------------
# object Tmu -- clz / wrapOrClamp
# ---------------------------------------------------------------------------


def clz32(v):
    """``def clz32(v: UInt): UInt`` -- 6-bit binary-search leading-zero count.

    Note ``clz32(0)`` is 31, not 32: the last step tests ``!s3(31)`` only.
    """
    v0 = zext(v, 32)

    def step(s, n, hi_lo, shift, add):
        test = s[hi_lo:32] == 0
        n_next = u_add(n, Mux(test, Const(add, 6), Const(0, 6)), 6)
        s_next = Mux(test, zext(s << shift, 32), s)
        return s_next, n_next

    test0 = v0[16:32] == 0
    n0 = Mux(test0, Const(16, 6), Const(0, 6))
    s0 = Mux(test0, zext(v0 << 16, 32), v0)
    s1, n1 = step(s0, n0, 24, 8, 8)
    s2, n2 = step(s1, n1, 28, 4, 4)
    s3, n3 = step(s2, n2, 30, 2, 2)
    test4 = ~s3[31]
    return u_add(n3, Mux(test4, Const(1, 6), Const(0, 6)), 6)


def clz64(v):
    """``def clz64(v: UInt): UInt`` -- 7 bits, ``32 + clz32(lo)`` when hi == 0."""
    v0 = zext(v, 64)
    hi = v0[32:64]
    lo = v0[0:32]
    hi_zero = hi == 0
    clz_hi = clz32(hi)
    clz_lo = clz32(lo)
    return Mux(hi_zero, u_add(Const(32, 7), zext(clz_lo, 7), 7), zext(clz_hi, 7))


def wrap_or_clamp(coord, size_bits, clamp_enable):
    """``def wrapOrClamp(coord: SInt, sizeBits: UInt, clampEnable: Bool): UInt``.

    ``size = (1 << sizeBits)`` in 10 bits; wrap is ``coord & (size - 1)``,
    clamp is ``0`` below zero, ``size - 1`` at or past ``size`` (compared as
    the *full-width* unsigned ``coord.asUInt``), else the low 10 bits.
    """
    coord = coord.as_signed() if not coord.shape().signed else coord
    size = zext(Const(1, 10) << size_bits, 10)
    max_val = u_sub(size, Const(1, 1), 10)
    wrapped = zext(coord, 10) & max_val
    coord_u = coord.as_unsigned()
    clamped = Mux(coord < 0, Const(0, 10),
                  Mux(coord_u >= size, max_val, zext(coord, 10)))
    return Mux(clamp_enable, clamped, wrapped)
