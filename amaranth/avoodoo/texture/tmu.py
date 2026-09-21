"""Texture Mapping Unit -- the TMU front end, request path and output merge.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/texture/Tmu.scala`` (SpinalHDL
1.15.0, 1180 lines): the ``Tmu`` Component (lines 22-630).  ``object Tmu``'s
bundles, constant ROMs and pure helpers are already ported in
``tmu_types.py`` and ``tmu_tables.py``; the two sub-Components it
instantiates are ``tmu_texel_decoder.py`` (``TmuTexelDecoder``) and
``tmu_texture_cache.py`` / ``tmu_no_texture_cache.py`` (``TmuTextureCache``
or ``TmuNoTextureCache``, the ``texturePath``, chosen by ``c.useTexFillCache``).

Structure map (Scala ``val`` -> here), in pipeline order.  Every arrow is a
``translateWith { ... }`` followed by the named register stage; ``stage()``
is ``m2sPipe()`` under another name, so the front end is nine ``M2SPipe``s
in a row, then the FIFO, then the ``>/->`` (``s2mPipe().m2sPipe()``) pairs
around the texture path::

    io.input                                                  Tmu.Input
      |  frontEnd.stageRecipPrep   abs(1/w), clz64, ROM reads   .stage()
      v                                                       FrontRecipPrep
      |  frontEnd.stageRecip       recip interp, perspLodAdjust .stage()
      v                                                       FrontRecip
      |  frontEnd.stageA           S*recip, T*recip (14.4)     .m2sPipe()
      v                                                       FrontA
      |  lodPipeline.stageGrad     max(|dX|^2, |dY|^2)         .stage()
      v                                                       FrontGrad
      |  lodPipeline.stageLodPrep  clz32 + log2 ROM -> 8.8 LOD .stage()
      v                                                       FrontLodPrep
      |  lodPipeline.stageLod      bias, clamp, level, aspect  .stage()
      v                                                       FrontLod
      |  coordGen.stageB           point / bilinear coords     .stage()
      v                                                       FrontB
      |  addressGen + sampleRequestPrep  wrap/clamp, texelAddr .stage()
      v                                                       SampleRequestPrep
      |  sampleRequestBase   packed bank/pair decode  .m2sPipe().queue(16)
      v                                                       SampleRequest
      +--> bypassOutput   (textureEnable low: (0,0,0,255) straight to output)
      +--> sampleRequest  (canAllocate)  >/->  texturePath.io.sampleRequest
                                              texturePath.io.sampleFetch
                                        >/->  texelDecoder.io.sampleFetch
                                              texelDecoder.io.output
    io.output  = bypassOutput | texelDecoder.io.output  (bypass wins)
    inFlightCount (RegInit 0, 3 bits, max 7): +1 on sampleRequest/bypass
      fire, -1 on io.output fire; io.busy = inFlight | any stage valid

Scala -> Python names (the rest is ``camel_to_snake``):

=====================================  =========================================
``Tmu.scala``                          here
=====================================  =========================================
``lookupTables.recipTable`` (l. 54)    ``recipTable`` (``Memory``, 2 comb ports)
``lookupTables.logTable`` (l. 61)      ``logTable`` (``Memory``, 2 comb ports)
``texMode`` (l. 87)                    ``tex_mode`` (a ``TextureMode`` view)
``frontEnd.stageRecipPrep`` (l. 92)    ``stage_recip_prep``
``frontEnd.stageRecip`` (l. 124)       ``stage_recip``
``frontEnd.stageA`` (l. 155)           ``stage_a``
``texS`` / ``texT`` (l. 192)           ``tex_s`` / ``tex_t`` (kept for tests)
``lodPipeline.stageGrad`` (l. 210)     ``stage_grad``
``lodPipeline.baseLod_8_8`` (l. 224)   ``base_lod_8_8``
``lodPipeline.stageLodPrep`` (l. 245)  ``stage_lod_prep``
``lodPipeline.tLOD`` (l. 259)          ``t_lod`` (a ``TLOD`` view)
``lodPipeline.lodLevel`` (l. 279)      ``lod_level``
``lodPipeline.stageLod`` (l. 309)      ``stage_lod``
``coordGen.bilinearEnable`` (l. 331)   ``bilinear_enable``
``coordGen.stageB`` (l. 356)           ``stage_b``
``addressGen.texelAddr`` (l. 398)      ``texel_addr`` (closure)
``addressGen.pointAddr`` .. ``biAddr3`` ``point_addr`` .. ``bi_addr3``
``inputPassthrough`` (l. 474)          ``input_passthrough``
``sampleRequestPrep`` (l. 485)         ``sample_request_prep``
``sampleRequestBase`` (l. 519)         ``sample_request_base``
``bypassOutput`` (l. 578)              ``bypass_output``
``sampleRequest`` (l. 592)             ``sample_request``
``texturePath`` (l. 596)               ``texturePath`` (submodule)
``texelDecoder`` (l. 601)              ``texelDecoder`` (submodule)
``inFlightCount`` (l. 46)              ``in_flight_count``
=====================================  =========================================

Arithmetic is on ``raw`` bits throughout -- the Scala never does AFix
arithmetic here (afix-semantics.md §7), it only carries ``s``/``t``/``w`` and
the four gradients as AFix bundle members.  So the port's job is Spinal's
*integer* width rules, which ``lib/intops.py`` spells out: ``+``/``-`` keep
``max(w_a, w_b)`` bits, ``+^`` grows, ``>>`` by an ``Int`` drops bits while
``>>`` by a ``UInt`` keeps the width, ``|<<`` keeps the width, ``<<`` by an
``Int`` grows, ``resize`` truncates or (sign-/zero-)extends by the source
type.  The stages, in Scala widths:

``stageRecipPrep`` (1/w -> table index)
    ``oowRaw`` = ``w.raw`` (48-bit SInt); ``absOow`` = ``oowRaw.abs``
    (48 bits: ``Mux(msb, ~x, x) + msb``, so ``-2^47`` stays ``-2^47``) zero-
    extended to 64; ``clz = clz64(absOow)`` (7 bits, 63 for zero);
    ``msbPos = 63 - clz``; ``norm = absOow |<< clz`` (64 bits); ``index =
    norm[62:55]``, ``frac = norm[54:47]``; ``base = recip[index]``, ``diff =
    base - recip[index + 1]`` (17-bit wrap); ``logFrac = log[index]``;
    ``validOow = !msb && absOow != 0``; ``clampToZero = clampW && msb``.
``stageRecip``
    ``interp = base - ((diff * frac) >> 8)`` (25-bit product, ``>> 8`` to
    17, 17-bit subtract) when ``validOow`` else 0.  ``perspLodAdjust`` (16
    bits) = ``((27 - msbPos) << 8) - logFrac - 1`` when ``validOow`` else 0,
    where ``27 - msbPos`` is an 8-bit SInt subtract.
``stageA``
    No perspective: ``texS = (s.raw >> 14).resized`` -- an ``Int`` shift, so
    34 bits, then the low 18.  Perspective: ``productS = s.raw * (0 ##
    interp).asSInt`` (48 x 18 = 66 bits); ``roundBias = S(1, 66) |<<
    (msbPos - 1)`` (7-bit wrapping subtract, 64 -> 127 -> shifted out);
    ``texS = ((productS + roundBias) >> msbPos).resized`` (66-bit add and
    arithmetic shift, low 18 bits).
``stageGrad``
    ``tempdx = (dSdX.raw^2).asUInt + (dTdX.raw^2).asUInt`` (32 x 32 = 64,
    64-bit wrapping add), same for ``dy``; ``tempLOD = max``.
``stageLodPrep``
    ``baseLod_8_8`` = 0 when ``tempLOD == 0``, else with ``msbPos64`` from
    ``clz32`` of the non-zero half: ``shifted = (tempLOD >> max(msbPos64 -
    8, 0))[15:0]``, ``lodIndex = shifted[7:0]``, ``rawLod = (msbPos64 << 8)
    + log[lodIndex] - 36*256`` (16-bit wrapping), ``baseLod = (rawLod >>
    2).resize(16)`` (14-bit arithmetic shift, sign-extended).
``stageLod``
    ``lod_8_8 = baseLod + perspLodAdjust + (lodbias.asSInt << 6)`` (16-bit
    wrap); clamped to ``[lodmin << 6, min(lodmax << 6, 0x800)]``; ``lodInt =
    lodClamped >> 8``; ``lodLevel`` = ``lodInt`` clamped to 0..8 (4 bits);
    ``lodDimBits = 8 - lodLevel``; ``narrowDimBits = max(lodDimBits -
    aspect, 0)``; ``texWidthBits``/``texHeightBits`` per ``lodSIsWider``.
``stageB``
    ``sPoint = (texS >> (4 + lodLevel))[13:0]`` (``U(4)`` is 3 bits, the
    add is 4 bits); ``adjS = texS - (S(1, 18) |<< (3 + lodLevel))``; ``sScaled
    = adjS >> lodLevel`` (18 bits kept); ``ds = sScaled[3:0]``; ``si =
    (sScaled >> 4)[13:0]``; all six forced to 0 by ``clampToZero``.
``addressGen``
    ``wrapOrClamp`` on the point and the four bilinear taps (``si + 1`` is a
    14-bit wrapping add), then ``texelAddr``: ``texBase(lodLevel) + (x << 1)
    + (y << (texShift(lodLevel) + 1))`` for 16-bit formats, ``texBase + x +
    (y << texShift)`` for 8-bit, everything in 22 bits, zero-extended to
    ``addressWidth``.  ``texBase(lodLevel)`` is a 9-entry ``Vec`` read with
    a 4-bit index; Spinal's ``case`` default sends 9..15 to entry 8 (never
    reached: ``lodLevel <= 8``), reproduced by :func:`_vec_read`.

Three cross-stage reads the Scala does and this port keeps, because they
are what the hardware does:

* ``bilinearEnable`` comes from ``stageLod``'s ``textureMode`` but is folded
  into ``stageB``'s ``translateWith`` (``sampleRequestPrep``), one stage
  later -- so a bilinear/point change on consecutive pixels applies one
  pixel late to ``tapLineBase(0)``, ``bilinear`` and ``passthrough``.
* ``bypassTexture = !stageB.payload.meta.config.textureEnable`` gates
  ``sampleRequestBase`` (the FIFO's pop) although ``SampleRequest`` does
  not carry ``textureEnable``: the bypass decision for the request at the
  FIFO head is taken from whatever pixel is sitting in ``stageB``.
* ``tLOD.tmultibaseaddr`` for the non-packed ``texelAddr`` branch is
  decoded from ``stageLodPrep``, not ``stageB`` -- moot here, see below.

Only ``c.packedTexLayout`` is supported.  ``sampleRequestBase`` indexes
``texTables`` unconditionally in the Scala, so the linear (``texBaseAddr``)
branch of ``texelAddr`` cannot elaborate upstream either; it is not ported.
``c.useTexFillCache`` selects the texture path: ``TmuTextureCache``
(``tmu_texture_cache.py``, the ``Config.voodoo1()`` default) or
``TmuNoTextureCache`` (``tmu_no_texture_cache.py``, what the Console builds
-- plan Phase 8.0).  ``packedLodEnd`` /
``lodSizeShift`` (l. 384-394) are computed and never read in the Scala, and
Spinal prunes them; they are not written here.

The Scala's ``assert(!(bypassOutput.valid && texelDecoder.io.output.valid))``
is a simulation-only check; it is not emitted (the lockstep diff would flag
any divergence the assert could).

``reset_less``: every pipeline register is inside ``lib/stream.py``'s
``M2SPipe`` / ``S2MPipe`` / ``Fifo``, which already apply the plan's §6
rule.  The only register declared here is ``inFlightCount``, a ``RegInit``
(reset to 0).  The two ROMs carry real ``init`` data, so they must *not*
go through ``strip_meminit``.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import memory, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.membus_params import tmu_bmb_params
from avoodoo.lib.membus import MemBusSignature, log2_up
from avoodoo.lib.intops import s_add, s_sub, sar, sext, u_add, u_sub, zext
from avoodoo.lib.stream import (
    FlowSignature,
    Stream,
    connect_stream,
    fire,
    m2s_pipe,
    queue,
    s2m_pipe,
    stage,
    translate_with,
)
from avoodoo.texture.tmu_tables import (
    LOG_TABLE_BITS,
    LOG_TABLE_SIZE,
    RECIP_TABLE_BITS,
    RECIP_TABLE_SIZE,
    log_table,
    recip_table,
)
from avoodoo.texture.tmu_texel_decoder import TmuTexelDecoder
from avoodoo.texture.tmu_types import (
    REQUEST_ID_WIDTH,
    TLOD,
    FrontA,
    FrontB,
    FrontGrad,
    FrontLod,
    FrontLodPrep,
    FrontRecip,
    FrontRecipPrep,
    NccTables,
    PaletteWrite,
    SampleRequest,
    SampleRequestPrep,
    TextureFormat,
    TextureMode,
    TmuInput,
    TmuOutput,
    TmuPassthrough,
    clz32,
    clz64,
    corrected_tex_end,
    line_base22_of,
    packed_location22,
    packed_startup_decode4,
    wrap_or_clamp,
)

__all__ = ["Tmu", "MAX_OUTSTANDING"]

#: ``val maxOutstanding = 7`` -- ``inFlightCount`` is ``log2Up(8) = 3`` bits.
MAX_OUTSTANDING = 7


def _vec_read(m, vec, index, width: int, *, name: str):
    """Spinal ``Vec(idx)`` with an index that can exceed the last entry.

    Spinal emits a ``case`` over the valid indices with the *last* entry as
    ``default``; Amaranth's ``Array`` read would be undefined there.  (Same
    helper as ``texture_mem.py``'s; kept local so the two modules stay
    independent.)
    """
    out = Signal(width, name=name)
    with m.Switch(index):
        for i in range(len(vec) - 1):
            with m.Case(i):
                m.d.comb += out.eq(vec[i])
        with m.Default():
            m.d.comb += out.eq(vec[len(vec) - 1])
    return out


def _abs_sint(v):
    """``SInt.abs`` -- ``Mux(msb, ~x, x).asUInt + msb.asUInt``, same width.

    The most negative value maps onto itself (``~x + 1`` wraps), exactly as
    Spinal's does; the caller zero-extends afterwards.
    """
    v = v.as_signed() if not v.shape().signed else v
    w = len(v)
    msb = v[w - 1]
    flipped = Mux(msb, ~v.as_unsigned(), v.as_unsigned())
    return u_add(flipped, zext(msb, 1), w)


class Tmu(wiring.Component):
    """``case class Tmu(c: voodoo.Config) extends Component``.

    Ports are the Scala ``io`` bundle: ``input`` (slave ``Stream[Tmu.Input]``),
    ``output`` (master ``Stream[Tmu.Output]``), ``tex_read`` (master
    ``Bmb(Tmu.bmbParams(c))``), ``palette_write`` (slave ``Flow``),
    ``send_config``, ``ncc_tables``, ``invalidate`` in, ``busy`` out.  The
    diff harness's mechanical rule pairs every Spinal leaf with a slice of
    these without an override.
    """

    def __init__(self, c: Config | None = None) -> None:
        self.c = c if c is not None else Config.voodoo1()
        if not self.c.packed_tex_layout:
            raise NotImplementedError(
                "Tmu: only packedTexLayout is ported (the linear texelAddr branch "
                "cannot elaborate upstream either -- sampleRequestBase indexes "
                "texTables unconditionally)")
        self.bmb_params = tmu_bmb_params(self.c)
        self.request_id_width = REQUEST_ID_WIDTH
        self.max_outstanding = MAX_OUTSTANDING
        super().__init__({
            "input": In(stream.Signature(TmuInput(self.c))),
            "output": Out(stream.Signature(TmuOutput(self.c))),
            "tex_read": Out(MemBusSignature(self.bmb_params)),
            "palette_write": In(FlowSignature(PaletteWrite())),
            "send_config": In(1),
            "ncc_tables": In(NccTables()),
            "invalidate": In(1),
            "busy": Out(1),
        })

    # ------------------------------------------------------------------
    def elaborate(self, platform):
        # The cache is being ported concurrently (plan Phase 5); importing it
        # here rather than at module level keeps `Tmu`'s layouts and helpers
        # importable while that file is in flux, and makes the dependency a
        # clean port boundary.  Plan 8.0: `Tmu.scala:597` picks the texture
        # path on the config flag -- `TmuTextureCache(c)` when
        # `c.useTexFillCache`, else `TmuNoTextureCache(c)` (what the Console
        # builds, CoreGowinGen.scala:73).
        from avoodoo.texture.tmu_no_texture_cache import TmuNoTextureCache
        from avoodoo.texture.tmu_texture_cache import TmuTextureCache

        m = Module()
        c = self.c
        aw = c.address_width
        trace = c.trace.enabled

        # -- lookupTables ------------------------------------------------
        #
        #   val recipTable = Mem(UInt(17 bits), recipTableValues)   (512)
        #   val logTable   = Mem(UInt(8 bits), logTableValues)      (256)
        #
        # Both are only ever `readAsync`: two ports each.
        m.submodules.recipTable = recip_mem = memory.Memory(
            shape=unsigned(RECIP_TABLE_BITS), depth=RECIP_TABLE_SIZE, init=recip_table)
        m.submodules.logTable = log_mem = memory.Memory(
            shape=unsigned(LOG_TABLE_BITS), depth=LOG_TABLE_SIZE, init=log_table)
        recip_rd_base = recip_mem.read_port(domain="comb")
        recip_rd_next = recip_mem.read_port(domain="comb")
        log_rd_front = log_mem.read_port(domain="comb")
        log_rd_lod = log_mem.read_port(domain="comb")

        # -- inFlightCount ------------------------------------------------
        in_flight_count = Signal(log2_up(MAX_OUTSTANDING + 1), name="inFlightCount")

        # -- input decode --------------------------------------------------
        input_for_stage = self.input
        in_p = input_for_stage.payload
        tex_mode = TextureMode()(in_p.config.texture_mode)
        oow = in_p.w.raw       # 1/W, 2.30 in the 48-bit accumulator (SInt)
        sow = in_p.s           # S/W, 14.18 in 48 bits (AFix, carried as-is)
        tow = in_p.t

        # ==================================================================
        # frontEnd
        # ==================================================================

        # -- stageRecipPrep --------------------------------------------------
        oow_raw = oow.as_signed()
        abs_oow = Signal(64, name="absOow")
        m.d.comb += abs_oow.eq(zext(_abs_sint(oow_raw), 64))
        clz = Signal(7, name="clz")
        m.d.comb += clz.eq(clz64(abs_oow))
        msb_pos = Signal(7, name="msbPos")
        m.d.comb += msb_pos.eq(u_sub(Const(63, 7), clz, 7))
        # `absOow |<< clz`: shift by a UInt, width kept at 64.
        norm = Signal(64, name="norm")
        m.d.comb += norm.eq((abs_oow << clz)[:64])
        index = norm[55:63]
        frac = norm[47:55]
        m.d.comb += [
            recip_rd_base.addr.eq(zext(index, 9)),
            # `index +^ U(1)`: the growing add, 9 bits, so 255 reads entry 256.
            recip_rd_next.addr.eq(zext(index, 9) + Const(1, 1)),
            log_rd_front.addr.eq(index),
        ]
        base = recip_rd_base.data
        nxt = recip_rd_next.data

        recip_prep = Signal(FrontRecipPrep(c), name="recipPrep")
        m.d.comb += [
            recip_prep.s.eq(sow),
            recip_prep.t.eq(tow),
            recip_prep.t_lod.eq(in_p.config.t_lod),
            recip_prep.base.eq(base),
            recip_prep.diff.eq(u_sub(base, nxt, 17)),
            recip_prep.frac.eq(frac),
            recip_prep.log_frac.eq(log_rd_front.data),
            recip_prep.msb_pos.eq(msb_pos),
            recip_prep.valid_oow.eq(~oow_raw[47] & (abs_oow != 0)),
            recip_prep.perspective_enable.eq(tex_mode.perspective_enable),
            recip_prep.clamp_to_zero.eq(tex_mode.clamp_w & oow_raw[47]),
            recip_prep.d_sd_x.eq(in_p.d_sd_x),
            recip_prep.d_td_x.eq(in_p.d_td_x),
            recip_prep.d_sd_y.eq(in_p.d_sd_y),
            recip_prep.d_td_y.eq(in_p.d_td_y),
            recip_prep.meta.config.eq(in_p.config),
        ]
        if trace:
            m.d.comb += recip_prep.meta.trace.eq(in_p.trace)
        stage_recip_prep = stage(m, translate_with(m, input_for_stage, recip_prep),
                                 name="stageRecipPrep")
        rp = stage_recip_prep.payload

        # -- stageRecip -----------------------------------------------------
        #   interp = base - ((diff * frac) >> 8).resize(17)
        interp = Signal(17, name="interp")
        m.d.comb += interp.eq(u_sub(rp.base, zext((rp.diff * rp.frac)[8:25], 17), 17))
        #   adjSInt = S(27, 8 bits) - (False ## msbPos).asSInt.resize(8)
        #   perspLodAdjustPre = ((adjSInt << 8).resize(16)
        #                        - (False ## U(0,8) ## logFrac).asSInt.resize(16)
        #                        - S(1, 16))
        adj_sint = s_sub(Const(27, signed(8)), Cat(rp.msb_pos, Const(0, 1)).as_signed(), 8)
        adj_shifted = sext(adj_sint << 8, 16)
        log_frac_16 = Cat(rp.log_frac, Const(0, 8)).as_signed()
        persp_lod_adjust_pre = Signal(signed(16), name="perspLodAdjustPre")
        m.d.comb += persp_lod_adjust_pre.eq(0)
        with m.If(rp.valid_oow):
            m.d.comb += persp_lod_adjust_pre.eq(
                s_sub(s_sub(adj_shifted, log_frac_16, 16), Const(1, signed(16)), 16))

        recip = Signal(FrontRecip(c), name="recip")
        m.d.comb += [
            recip.s.eq(rp.s),
            recip.t.eq(rp.t),
            recip.t_lod.eq(rp.t_lod),
            recip.interp.eq(Mux(rp.valid_oow, interp, Const(0, 17))),
            recip.msb_pos.eq(rp.msb_pos),
            recip.perspective_enable.eq(rp.perspective_enable),
            recip.clamp_to_zero.eq(rp.clamp_to_zero),
            recip.persp_lod_adjust.eq(persp_lod_adjust_pre),
            recip.d_sd_x.eq(rp.d_sd_x),
            recip.d_td_x.eq(rp.d_td_x),
            recip.d_sd_y.eq(rp.d_sd_y),
            recip.d_td_y.eq(rp.d_td_y),
            recip.meta.eq(rp.meta),
        ]
        stage_recip = stage(m, translate_with(m, stage_recip_prep, recip), name="stageRecip")
        rc = stage_recip.payload

        # -- stageA -------------------------------------------------------
        tex_s_a = Signal(signed(18), name="texS_a")
        tex_t_a = Signal(signed(18), name="texT_a")
        s_raw = rc.s.raw.as_signed()
        t_raw = rc.t.raw.as_signed()
        interp_signed = Cat(rc.interp, Const(0, 1)).as_signed()          # 18 bits
        product_s = Signal(signed(66), name="productS")
        product_t = Signal(signed(66), name="productT")
        m.d.comb += [product_s.eq(s_raw * interp_signed), product_t.eq(t_raw * interp_signed)]
        #   roundBias = S(1, 66 bits) |<< (msbPos - 1).resize(7 bits)
        round_shift = u_sub(rc.msb_pos, Const(1, 1), 7)
        round_bias = Signal(signed(66), name="roundBias")
        m.d.comb += round_bias.eq((Const(1, unsigned(66)) << round_shift)[:66])
        with m.If(~rc.perspective_enable):
            m.d.comb += [
                tex_s_a.eq(sext(sar(s_raw, 14), 18)),
                tex_t_a.eq(sext(sar(t_raw, 14), 18)),
            ]
        with m.Else():
            m.d.comb += [
                tex_s_a.eq(sext((s_add(product_s, round_bias, 66) >> rc.msb_pos), 18)),
                tex_t_a.eq(sext((s_add(product_t, round_bias, 66) >> rc.msb_pos), 18)),
            ]

        front_a = Signal(FrontA(c), name="frontA")
        m.d.comb += [
            front_a.tex_s.eq(tex_s_a),
            front_a.tex_t.eq(tex_t_a),
            front_a.t_lod.eq(rc.t_lod),
            front_a.persp_lod_adjust.eq(rc.persp_lod_adjust),
            front_a.clamp_to_zero.eq(rc.clamp_to_zero),
            front_a.d_sd_x.eq(rc.d_sd_x),
            front_a.d_td_x.eq(rc.d_td_x),
            front_a.d_sd_y.eq(rc.d_sd_y),
            front_a.d_td_y.eq(rc.d_td_y),
            front_a.meta.eq(rc.meta),
        ]
        stage_a = m2s_pipe(m, translate_with(m, stage_recip, front_a), name="stageA")
        pa = stage_a.payload

        # `val texS = stageA.payload.texS.setName("texS")` -- simPublic'd for
        # the Scala test; kept as named signals for ours.
        self.tex_s = tex_s = Signal(signed(18), name="texS")
        self.tex_t = tex_t = Signal(signed(18), name="texT")
        m.d.comb += [tex_s.eq(pa.tex_s), tex_t.eq(pa.tex_t)]

        # ==================================================================
        # lodPipeline
        # ==================================================================
        d_sd_x_raw = pa.d_sd_x.raw.as_signed()
        d_td_x_raw = pa.d_td_x.raw.as_signed()
        d_sd_y_raw = pa.d_sd_y.raw.as_signed()
        d_td_y_raw = pa.d_td_y.raw.as_signed()

        def sq64(v):
            return sext(v * v, 64).as_unsigned()

        tempdx = Signal(64, name="tempdx")
        tempdy = Signal(64, name="tempdy")
        temp_lod = Signal(64, name="tempLOD")
        m.d.comb += [
            tempdx.eq(u_add(sq64(d_sd_x_raw), sq64(d_td_x_raw), 64)),
            tempdy.eq(u_add(sq64(d_sd_y_raw), sq64(d_td_y_raw), 64)),
            temp_lod.eq(Mux(tempdx > tempdy, tempdx, tempdy)),
        ]

        # -- stageGrad ----------------------------------------------------
        front_grad = Signal(FrontGrad(c), name="frontGrad")
        m.d.comb += [
            front_grad.tex_s.eq(pa.tex_s),
            front_grad.tex_t.eq(pa.tex_t),
            front_grad.t_lod.eq(pa.t_lod),
            front_grad.clamp_to_zero.eq(pa.clamp_to_zero),
            front_grad.temp_lod.eq(temp_lod),
            front_grad.persp_lod_adjust.eq(pa.persp_lod_adjust),
            front_grad.meta.eq(pa.meta),
        ]
        stage_grad = stage(m, translate_with(m, stage_a, front_grad), name="stageGrad")
        pg = stage_grad.payload

        # -- baseLod_8_8 ----------------------------------------------------
        base_lod_8_8 = Signal(signed(16), name="baseLod_8_8")
        temp_lod32hi = pg.temp_lod[32:64]
        temp_lod32lo = pg.temp_lod[0:32]
        use_hi = temp_lod32hi != 0
        clz_input = Signal(32, name="clzInput")
        m.d.comb += clz_input.eq(Mux(use_hi, temp_lod32hi, temp_lod32lo))
        clz32val = Signal(6, name="clz32val")
        m.d.comb += clz32val.eq(clz32(clz_input))
        msb_pos64 = Signal(7, name="msbPos64")
        m.d.comb += msb_pos64.eq(Mux(use_hi,
                                     u_sub(Const(63, 7), zext(clz32val, 7), 7),
                                     u_sub(Const(31, 7), zext(clz32val, 7), 7)))
        shift_amt = Signal(6, name="shiftAmt")
        m.d.comb += shift_amt.eq(Mux(msb_pos64 >= 8,
                                     zext(u_sub(msb_pos64, Const(8, 4), 7), 6),
                                     Const(0, 6)))
        shifted = Signal(16, name="shifted")
        m.d.comb += shifted.eq((pg.temp_lod >> shift_amt)[:16])
        lod_index = shifted[0:8]
        m.d.comb += log_rd_lod.addr.eq(lod_index)
        #   rawLod = ((False ## msbPos64).asSInt.resize(16) << 8).resize(16)
        #          + (False ## U(0, 8) ## log(lodIndex)).asSInt.resize(16)
        #          - S(36 * 256, 16)
        msb_pos64_16 = sext(Cat(msb_pos64, Const(0, 1)).as_signed(), 16)
        raw_lod = Signal(signed(16), name="rawLod")
        m.d.comb += raw_lod.eq(s_sub(
            s_add(sext(msb_pos64_16 << 8, 16),
                  Cat(log_rd_lod.data, Const(0, 8)).as_signed(), 16),
            Const(36 * 256, signed(16)), 16))
        with m.If(pg.temp_lod == 0):
            m.d.comb += base_lod_8_8.eq(0)
        with m.Else():
            m.d.comb += base_lod_8_8.eq(sext(sar(raw_lod, 2), 16))

        # -- stageLodPrep ---------------------------------------------------
        front_lod_prep = Signal(FrontLodPrep(c), name="frontLodPrep")
        m.d.comb += [
            front_lod_prep.tex_s.eq(pg.tex_s),
            front_lod_prep.tex_t.eq(pg.tex_t),
            front_lod_prep.t_lod.eq(pg.t_lod),
            front_lod_prep.clamp_to_zero.eq(pg.clamp_to_zero),
            front_lod_prep.base_lod_8_8.eq(base_lod_8_8),
            front_lod_prep.persp_lod_adjust.eq(pg.persp_lod_adjust),
            front_lod_prep.meta.eq(pg.meta),
        ]
        stage_lod_prep = stage(m, translate_with(m, stage_grad, front_lod_prep),
                               name="stageLodPrep")
        pl = stage_lod_prep.payload

        # -- LOD bias / clamp / level / aspect --------------------------------
        t_lod = TLOD()(pl.t_lod)
        lodbias_8_8 = sext(t_lod.lodbias.as_signed() << 6, 16)
        lod_8_8 = Signal(signed(16), name="lod_8_8")
        m.d.comb += lod_8_8.eq(s_add(s_add(pl.base_lod_8_8, pl.persp_lod_adjust, 16),
                                     lodbias_8_8, 16))
        lod_min_8_8 = zext(t_lod.lodmin << 6, 12)
        lod_max_8_8_raw = zext(t_lod.lodmax << 6, 12)
        lod_max_8_8 = Mux(lod_max_8_8_raw > Const(0x800, 12), Const(0x800, 12), lod_max_8_8_raw)
        lod_min_s = Cat(lod_min_8_8, Const(0, 4)).as_signed()
        lod_max_s = Cat(lod_max_8_8, Const(0, 4)).as_signed()

        lod_clamped_8_8 = Signal(signed(16), name="lodClamped_8_8")
        with m.If(lod_8_8 < lod_min_s):
            m.d.comb += lod_clamped_8_8.eq(lod_min_s)
        with m.Elif(lod_8_8 > lod_max_s):
            m.d.comb += lod_clamped_8_8.eq(lod_max_s)
        with m.Else():
            m.d.comb += lod_clamped_8_8.eq(lod_8_8)

        lod_int = sext(sar(lod_clamped_8_8, 8), 16)
        lod_level = Signal(4, name="lodLevel")
        with m.If(lod_int < 0):
            m.d.comb += lod_level.eq(0)
        with m.Elif(lod_int > 8):
            m.d.comb += lod_level.eq(8)
        with m.Else():
            m.d.comb += lod_level.eq(lod_int.as_unsigned()[0:4])

        base_dim_bits = Const(8, 4)
        lod_dim_bits = u_sub(base_dim_bits, lod_level, 4)
        aspect_ratio = t_lod.lod_aspect
        s_is_wider = t_lod.lod_s_is_wider
        tex_width_bits = Signal(4, name="texWidthBits")
        tex_height_bits = Signal(4, name="texHeightBits")
        narrow_dim_bits = Signal(4, name="narrowDimBits")
        with m.If(lod_dim_bits >= aspect_ratio):
            m.d.comb += narrow_dim_bits.eq(u_sub(lod_dim_bits, aspect_ratio, 4))
        with m.Else():
            m.d.comb += narrow_dim_bits.eq(0)
        m.d.comb += [tex_width_bits.eq(lod_dim_bits), tex_height_bits.eq(lod_dim_bits)]
        with m.If(s_is_wider):
            m.d.comb += tex_height_bits.eq(narrow_dim_bits)
        with m.Elif(aspect_ratio != 0):
            m.d.comb += tex_width_bits.eq(narrow_dim_bits)

        # -- stageLod -------------------------------------------------------
        front_lod = Signal(FrontLod(c), name="frontLod")
        m.d.comb += [
            front_lod.tex_s.eq(pl.tex_s),
            front_lod.tex_t.eq(pl.tex_t),
            front_lod.clamp_to_zero.eq(pl.clamp_to_zero),
            front_lod.lod_level.eq(lod_level),
            front_lod.tex_width_bits.eq(tex_width_bits),
            front_lod.tex_height_bits.eq(tex_height_bits),
            front_lod.meta.eq(pl.meta),
        ]
        stage_lod = stage(m, translate_with(m, stage_lod_prep, front_lod), name="stageLod")
        pd = stage_lod.payload

        # ==================================================================
        # coordGen
        # ==================================================================
        stage_lod_tex_mode = TextureMode()(pd.meta.config.texture_mode)
        bilinear_enable = Signal(name="bilinearEnable")
        m.d.comb += bilinear_enable.eq(stage_lod_tex_mode.min_filter
                                       | stage_lod_tex_mode.mag_filter)

        # `U(4) + lodLevel`: a 3-bit literal plus 4 bits -> 4-bit wrapping add;
        # `U(3) + lodLevel` likewise (2-bit literal).  `SInt >> UInt` keeps
        # the 18-bit width; the trailing `.resize(14)` takes the low 14 bits.
        point_shift = u_add(Const(4, 3), pd.lod_level, 4)
        adj_shift = u_add(Const(3, 2), pd.lod_level, 4)
        s_point = sext(pd.tex_s >> point_shift, 14)
        t_point = sext(pd.tex_t >> point_shift, 14)
        half_texel = (Const(1, unsigned(18)) << adj_shift)[:18].as_signed()   # S(1,18) |<< n
        adj_s = s_sub(pd.tex_s, half_texel, 18)
        adj_t = s_sub(pd.tex_t, half_texel, 18)
        s_scaled = Signal(signed(18), name="sScaled")
        t_scaled = Signal(signed(18), name="tScaled")
        m.d.comb += [s_scaled.eq(adj_s >> pd.lod_level), t_scaled.eq(adj_t >> pd.lod_level)]
        ds = s_scaled[0:4]
        dt = t_scaled[0:4]
        si = sext(sar(s_scaled, 4), 14)
        ti = sext(sar(t_scaled, 4), 14)

        zero14 = Const(0, signed(14))
        front_b = Signal(FrontB(c), name="frontB")
        m.d.comb += [
            front_b.final_s_point.eq(Mux(pd.clamp_to_zero, zero14, s_point)),
            front_b.final_t_point.eq(Mux(pd.clamp_to_zero, zero14, t_point)),
            front_b.final_si.eq(Mux(pd.clamp_to_zero, zero14, si)),
            front_b.final_ti.eq(Mux(pd.clamp_to_zero, zero14, ti)),
            front_b.final_ds.eq(Mux(pd.clamp_to_zero, Const(0, 4), ds)),
            front_b.final_dt.eq(Mux(pd.clamp_to_zero, Const(0, 4), dt)),
            front_b.lod_level.eq(pd.lod_level),
            front_b.tex_width_bits.eq(pd.tex_width_bits),
            front_b.tex_height_bits.eq(pd.tex_height_bits),
            front_b.meta.eq(pd.meta),
        ]
        stage_b = stage(m, translate_with(m, stage_lod, front_b), name="stageB")
        pb = stage_b.payload

        # ==================================================================
        # addressGen
        # ==================================================================
        stage_b_tex_mode = TextureMode()(pb.meta.config.texture_mode)
        is16_bit_format = Signal(name="is16BitFormat")
        m.d.comb += is16_bit_format.eq(stage_b_tex_mode.format >= TextureFormat.ARGB8332)

        packed_tables = pb.meta.config.tex_tables
        packed_lod_base = Signal(aw, name="packedLodBase")
        packed_lod_shift = Signal(4, name="packedLodShift")
        lod_base22 = _vec_read(m, packed_tables.tex_base, pb.lod_level, 22, name="lodBase22")
        m.d.comb += [
            packed_lod_base.eq(zext(lod_base22, aw)),
            packed_lod_shift.eq(_vec_read(m, packed_tables.tex_shift, pb.lod_level, 4,
                                          name="lodShift4")),
        ]

        def texel_addr(x, y, *, name):
            """``def texelAddr(x: UInt, y: UInt): UInt`` -- the packed branch.

            ``lodBase + (x << 1).resize(22) + (y.resize(22) << (lodShift +^ 1).resize(5)).resize(22)``
            for 16-bit texels, ``lodBase + x.resize(22) + (y.resize(22) << lodShift).resize(22)``
            for 8-bit; 22-bit wrapping adds, zero-extended to ``addressWidth``.
            """
            out = Signal(aw, name=name)
            x22 = zext(x, 22)
            y22 = zext(y, 22)
            shift16 = zext(u_add(zext(packed_lod_shift, 5), Const(1, 1), 5), 5)
            addr16 = u_add(u_add(lod_base22, zext(x << 1, 22), 22),
                           (y22 << shift16)[:22], 22)
            addr8 = u_add(u_add(lod_base22, x22, 22),
                          (y22 << packed_lod_shift)[:22], 22)
            m.d.comb += out.eq(zext(Mux(is16_bit_format, addr16, addr8), aw))
            return out

        point_texel_x = wrap_or_clamp(pb.final_s_point, pb.tex_width_bits,
                                      stage_b_tex_mode.clamp_s)
        point_texel_y = wrap_or_clamp(pb.final_t_point, pb.tex_height_bits,
                                      stage_b_tex_mode.clamp_t)
        point_addr = texel_addr(point_texel_x, point_texel_y, name="pointAddr")

        one_s2 = Const(1, signed(2))
        bi_x0 = wrap_or_clamp(pb.final_si, pb.tex_width_bits, stage_b_tex_mode.clamp_s)
        bi_x1 = wrap_or_clamp(s_add(pb.final_si, one_s2, 14), pb.tex_width_bits,
                              stage_b_tex_mode.clamp_s)
        bi_y0 = wrap_or_clamp(pb.final_ti, pb.tex_height_bits, stage_b_tex_mode.clamp_t)
        bi_y1 = wrap_or_clamp(s_add(pb.final_ti, one_s2, 14), pb.tex_height_bits,
                              stage_b_tex_mode.clamp_t)
        bi_addr0 = texel_addr(bi_x0, bi_y0, name="biAddr0")
        bi_addr1 = texel_addr(bi_x1, bi_y0, name="biAddr1")
        bi_addr2 = texel_addr(bi_x0, bi_y1, name="biAddr2")
        bi_addr3 = texel_addr(bi_x1, bi_y1, name="biAddr3")

        # -- inputPassthrough (assignPassthrough) ----------------------------
        input_passthrough = Signal(TmuPassthrough(c), name="inputPassthrough")
        m.d.comb += [
            input_passthrough.format.eq(stage_b_tex_mode.format),
            input_passthrough.bilinear.eq(bilinear_enable),
            input_passthrough.ncc_table_select.eq(pb.meta.config.texture_mode[5]),
            input_passthrough.ds.eq(pb.final_ds),
            input_passthrough.dt.eq(pb.final_dt),
        ]
        if trace:
            m.d.comb += input_passthrough.trace.eq(pb.meta.trace)

        # -- sampleRequestPrep ----------------------------------------------
        line_byte_shift = log2_up(c.tex_fill_line_words * 4)
        req_prep = Signal(SampleRequestPrep(c), name="reqPrep")
        m.d.comb += [
            req_prep.tap_line_base[0].eq(Mux(bilinear_enable,
                                             line_base22_of(bi_addr0, line_byte_shift),
                                             line_base22_of(point_addr, line_byte_shift))),
            req_prep.tap_line_base[1].eq(line_base22_of(bi_addr1, line_byte_shift)),
            req_prep.tap_line_base[2].eq(line_base22_of(bi_addr2, line_byte_shift)),
            req_prep.tap_line_base[3].eq(line_base22_of(bi_addr3, line_byte_shift)),
            req_prep.point_addr.eq(point_addr),
            req_prep.bi_addr0.eq(bi_addr0),
            req_prep.bi_addr1.eq(bi_addr1),
            req_prep.bi_addr2.eq(bi_addr2),
            req_prep.bi_addr3.eq(bi_addr3),
            req_prep.lod_base.eq(packed_lod_base),
            req_prep.lod_shift.eq(packed_lod_shift),
            req_prep.is16_bit.eq(is16_bit_format),
            req_prep.tex_tables.eq(pb.meta.config.tex_tables),
            req_prep.bilinear.eq(bilinear_enable),
            req_prep.passthrough.eq(input_passthrough),
        ]
        sample_request_prep = stage(m, translate_with(m, stage_b, req_prep),
                                    name="sampleRequestPrep")
        pp = sample_request_prep.payload

        # -- sampleRequestBase ----------------------------------------------
        startup_tex_base = [pp.tex_tables.tex_base[i] for i in range(9)]
        startup_tex_end = corrected_tex_end(
            startup_tex_base, [pp.tex_tables.tex_end[i] for i in range(9)], pp.is16_bit)
        startup_tex_shift = [pp.tex_tables.tex_shift[i] for i in range(9)]
        tap_addr = [
            Mux(pp.bilinear, pp.bi_addr0, pp.point_addr),
            pp.bi_addr1, pp.bi_addr2, pp.bi_addr3,
        ]
        req = Signal(SampleRequest(c), name="req")
        m.d.comb += [
            req.point_addr.eq(pp.point_addr),
            req.bi_addr0.eq(pp.bi_addr0),
            req.bi_addr1.eq(pp.bi_addr1),
            req.bi_addr2.eq(pp.bi_addr2),
            req.bi_addr3.eq(pp.bi_addr3),
            req.lod_base.eq(pp.lod_base),
            req.lod_shift.eq(pp.lod_shift),
            req.is16_bit.eq(pp.is16_bit),
            req.tex_tables.eq(pp.tex_tables),
            req.bilinear.eq(pp.bilinear),
            req.passthrough.eq(pp.passthrough),
        ]
        for i in range(4):
            decode = packed_startup_decode4(
                m, pp.tap_line_base[i], pp.is16_bit, pp.lod_base, pp.lod_shift,
                startup_tex_base, startup_tex_end, startup_tex_shift,
                name=f"startupDecode{i}")
            _, bank, pair = packed_location22(
                zext(tap_addr[i], 22), pp.is16_bit, zext(pp.lod_base, 22), pp.lod_shift,
                startup_tex_base, startup_tex_end, startup_tex_shift)
            m.d.comb += [
                req.tap_startup_decode[i].eq(decode),
                req.tap_packed_bank[i].eq(bank),
                req.tap_packed_pair[i].eq(pair),
            ]
        sample_request_base = queue(
            m, m2s_pipe(m, translate_with(m, sample_request_prep, req),
                        name="sampleRequestPipe"),
            16, name="sampleRequestFifo")

        # ==================================================================
        # bypass / allocate split
        # ==================================================================
        bypass_output = Stream(TmuOutput(c), path=("bypassOutput",))
        bypass_texture = Signal(name="bypassTexture")
        bypass_safe = Signal(name="bypassSafe")
        m.d.comb += [
            bypass_texture.eq(~pb.meta.config.texture_enable),
            bypass_safe.eq(bypass_texture & (in_flight_count == 0)),
            bypass_output.valid.eq(sample_request_base.valid & bypass_safe),
            bypass_output.payload.texture.r.eq(0),
            bypass_output.payload.texture.g.eq(0),
            bypass_output.payload.texture.b.eq(0),
            bypass_output.payload.texture_alpha.eq(255),
        ]
        if trace:
            m.d.comb += bypass_output.payload.trace.eq(
                sample_request_base.payload.passthrough.trace)

        can_allocate = Signal(name="canAllocate")
        sample_request = Stream(SampleRequest(c), path=("sampleRequest",))
        m.d.comb += [
            can_allocate.eq(in_flight_count != MAX_OUTSTANDING),
            sample_request.valid.eq(sample_request_base.valid & ~bypass_texture & can_allocate),
            # copySampleRequest: a field-by-field copy of the whole bundle.
            sample_request.payload.eq(sample_request_base.payload),
            sample_request_base.ready.eq(Mux(bypass_texture,
                                             bypass_output.ready & bypass_safe,
                                             sample_request.ready & can_allocate)),
        ]
        # Exposed for the unit tests (the Scala test reads these via simPublic).
        self.sample_request = sample_request
        self.sample_request_base = sample_request_base

        # ==================================================================
        # texturePath + texelDecoder
        # ==================================================================
        #   val texturePath = if (c.useTexFillCache) TmuTextureCache(c) else TmuNoTextureCache(c)
        m.submodules.texturePath = texture_path = (
            TmuTextureCache(c) if c.use_tex_fill_cache else TmuNoTextureCache(c))
        #   sampleRequest >/-> texturePath.io.sampleRequest
        connect_stream(m, texture_path.sample_request,
                       m2s_pipe(m, s2m_pipe(m, sample_request, name="sampleRequest_s2mPipe"),
                                name="sampleRequest_s2mPipe_m2sPipe"))
        m.d.comb += texture_path.invalidate.eq(self.invalidate)
        #   io.texRead <> texturePath.io.texRead
        connect_stream(m, self.tex_read.cmd, texture_path.tex_read.cmd)
        connect_stream(m, texture_path.tex_read.rsp, self.tex_read.rsp)

        m.submodules.texelDecoder = texel_decoder = TmuTexelDecoder(c)
        #   texturePath.io.sampleFetch >/-> texelDecoder.io.sampleFetch
        connect_stream(m, texel_decoder.sample_fetch,
                       m2s_pipe(m, s2m_pipe(m, texture_path.sample_fetch,
                                            name="sampleFetch_s2mPipe"),
                                name="sampleFetch_s2mPipe_m2sPipe"))
        m.d.comb += [
            texel_decoder.palette_write.valid.eq(self.palette_write.valid),
            texel_decoder.palette_write.payload.eq(self.palette_write.payload),
            texel_decoder.send_config.eq(self.send_config),
            texel_decoder.ncc_tables.eq(self.ncc_tables),
        ]

        # ==================================================================
        # output merge
        # ==================================================================
        m.d.comb += [
            texel_decoder.output.ready.eq(self.output.ready & ~bypass_output.valid),
            bypass_output.ready.eq(self.output.ready),
            self.output.valid.eq(bypass_output.valid | texel_decoder.output.valid),
            self.output.payload.eq(texel_decoder.output.payload),
        ]
        with m.If(bypass_output.valid):
            m.d.comb += self.output.payload.eq(bypass_output.payload)

        alloc_fire = fire(sample_request) | fire(bypass_output)
        out_fire = fire(self.output)
        with m.If(alloc_fire & ~out_fire):
            m.d.sync += in_flight_count.eq(in_flight_count + 1)
        with m.Elif(~alloc_fire & out_fire):
            m.d.sync += in_flight_count.eq(in_flight_count - 1)

        m.d.comb += self.busy.eq(
            (in_flight_count != 0) | stage_recip_prep.valid | stage_recip.valid
            | stage_a.valid | stage_grad.valid | stage_lod_prep.valid | stage_lod.valid
            | stage_b.valid | sample_request_base.valid)

        # Handy handles for tests / waveform probes.
        self.stages = {
            "stageRecipPrep": stage_recip_prep, "stageRecip": stage_recip, "stageA": stage_a,
            "stageGrad": stage_grad, "stageLodPrep": stage_lod_prep, "stageLod": stage_lod,
            "stageB": stage_b, "sampleRequestPrep": sample_request_prep,
        }
        self.in_flight_count = in_flight_count
        self.texture_path = texture_path
        self.texel_decoder = texel_decoder
        return m
