"""``FramebufferAccess``: read the framebuffer for the depth test and the alpha blend.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/pixel/FramebufferAccess.scala``
(493 lines, SpinalHDL 1.15.0), instantiated by ``PixelPipeline.scala:226``
between ``Fog``/alpha test and ``Dither``/``Write``.

Structure map (Scala line -> here)
----------------------------------

=========================================  ==================================
Scala                                      Amaranth
=========================================  ==================================
``io.input: Stream(Fog.Output(c))``        ``input`` (:class:`FogOutput`)
``io.output: Stream(Output(c))``           ``output`` (:class:`FramebufferAccessOutput`)
``io.fbReadColorReq/Rsp``, ``Aux``         ``fb_read_color_req`` ... (``FramebufferReadReq``/``Rsp``)
``io.busy``, ``io.zFuncFail``, ``io.debug`` ``busy``, ``z_func_fail``, ``debug``
``inFlightCount = Reg(UInt(5)) init 0``    ``in_flight_count`` (has a reset)
Step 1 ``zDepth``/``biasedDepth``/...      ``z_depth``, ``biased_depth``, ``comp_depth``
``Passthrough()``, ``Internal()``          :class:`Passthrough`, :class:`Internal`
``FetchResult()``, ``BlendPrep()``         :class:`FetchResult`, :class:`BlendPrep`
``requestStream = io.input.translateWith`` ``request_stream``
``StreamFork(requestStream, 5, false)``    ``fork(..., synchronous=False)`` -> ``forked[0..4]``
``forked(2..4).queue(32)``                 ``color_ctl_base``, ``aux_ctl_base``, ``pass_meta``
``io.fbReadColorReq << ...throwWhen``      ``throw_when`` + ``translate_with``
``io.fbReadColorRsp.queue(4)``             ``color_rsp_buf`` / ``aux_rsp_buf``
``StreamDemux(colorCtlBase, need, 2)``     ``demux`` -> ``color_ctl_routes``
``StreamJoin(routes(1).queue(32), rspBuf)`` ``join`` -> ``color_fetched``
``colorBypass`` / ``colorRsp`` mux         ``color_bypass`` / ``color_rsp`` (hand-written mux)
``fetchedRaw = StreamJoin(StreamJoin(..))`` ``fetched_raw``
``fetched = fetchedRaw.queue(32)``         ``fetched``
Step 3 ``depthPassed``/``depthKill``       ``depth_passed`` / ``depth_kill``
``afterDepthTest.m2sPipe()``               ``after_depth_test_piped``
Step 4 ``destColor``/``destDsub``          ``dest_color`` / ``dest_dsub`` (two comb-read ROMs)
``computeBlendScale``/``srcScale``...      :func:`_compute_blend_scale` -> ``src_scale``/``dst_scale``
``blendPrep = ...translateWith.m2sPipe()`` ``blend_prep``
``applyBlendScale``/``clampAdd``/``blended`` ``src_factor``/``dst_factor``/``blended``
Step 5 ``io.output.translateFrom``         the final ``m.d.comb`` block
=========================================  ==================================

What the module does
--------------------

1. **newDepth** (combinational, before the fork): the Z-buffer depth is the
   SQ(32,12) ``depth`` shifted right 12 and clamped to 16 bits, the W-buffer
   depth comes in precomputed from ``Fog``; ``depthSourceSelect`` swaps in
   ``zaColor[15:0]``, and ``enableDepthBias`` adds ``zaColor[15:0]`` as a
   signed bias and clamps again.
2. **Fork-queue-join**: the request is forked five ways (asynchronous
   ``StreamFork``, so each leg advances on its own): two legs issue the colour
   and aux plane reads (dropped with ``throwWhen`` when the pixel does not
   need them), three legs queue the pixel data.  A ``StreamDemux`` per plane
   routes the queued control record either to a join with the read response
   or to a bypass that supplies ``data = 0``; the two planes and the
   passthrough are then joined and queued (``fetched``).
3. **Depth test** on the aux data, per-pixel ``depthFunction``; a failing
   pixel is thrown away (``zFuncFail`` pulses).
4. **Alpha blend**: the old colour is expanded from RGB565, optionally
   dither-subtracted through the two ``DitherTables.dsub*Packed`` ROMs (same
   ``ditherAddr`` packing as ``Dither.scala`` -- ``dither_tables.packed_index``
   is the software twin), the source and destination scale factors are
   selected per ``rgbSrcFact``/``rgbDstFact``, and after another pipeline
   register the two products are summed with ``/255`` and clamped.
5. **Output**: the blended (or, with ``alphaBlendEnable`` low, the source)
   colour plus ``newDepth`` and the write-mask bits pulled out of ``fbzMode``.

Latency: fork (0) + ctl queue (2) + join + ``fetched`` queue (2) +
``afterDepthTestPiped`` (1) + ``blendPrep`` (1) = 6 cycles at full rate with
no reads outstanding; a read adds the plane-buffer round trip plus the
4-deep response queue (2).

Registers
---------

* ``inFlightCount`` is ``Reg(UInt(5 bits)) init 0``, so it resets here too.
  It counts ``io.input.fire`` up and ``fetched.fire`` down -- and it is
  **5 bits wide** while up to 32 (ctl queue) + 32 (route queue) + 4 + 32
  (``fetched``) + fork records can be in flight, so it wraps mod 32 under
  back-pressure and ``busy`` reads low with pixels inside.  Ported as-is; the
  back-pressure diff run reaches the wrap on both sides.
* Everything else is inside ``Fifo``/``M2SPipe``/``Fork``
  (``avoodoo/lib/stream.py``), which already carry Spinal's reset rules
  (FIFO pointers and ``rValid`` reset, payload registers ``reset_less``,
  ``linkEnable`` init true).  No plain ``Reg`` of this module's own.

The two ROMs
------------

``dsubRbRom``/``dsubGRom`` are ``Vec(...)`` of 5120 constants read with a
dynamic index, i.e. *combinational* lookups -- unlike ``Dither.scala``'s
``Mem.readSync``.  Spinal emits them as a 5120-way ``case``; here they are
``amaranth.lib.memory.Memory`` instances with ``read_port(domain="comb")``,
which yosys writes as ``assign data = rom[addr]`` over an ``initial`` block.
The ``rb`` image serves both R and B through two read ports of one
``Memory`` (the Scala reads one ``Vec`` twice).  The address is always in
[0, 5119] (4x4 = ``v ## y ## x`` < 4096, 2x2 = ``1 ## 00 ## v ## y0 ## x0``),
so Spinal's ``default`` arm (entry 5119) is unreachable and the unconstrained
out-of-range read here never happens.  The comb read leaves the ROMs out of
the ``streamReadSync`` clock-enable subtlety ``pixel/dither.py`` documents.

Widths worth knowing (Spinal's ``+``/``-`` do not grow)
---------------------------------------------------------

* ``zDepthShifted = (depthRaw >> 12).resize(20)``: SInt(32) ``>>`` 12 is
  SInt(20) in Spinal, and the two clamp compares are on that 20-bit value.
* ``biasedDepthRaw = compDepthPreBias.resize(17).asSInt + zaColorSigned.resize(17)``
  is a **17-bit wrapping** add: 0xFFFF + 0x7FFF = 98302 does not fit SInt(17)
  and wraps negative, clamping to 0 rather than 0xFFFF.  Reproduced with an
  explicit ``[0:17].as_signed()``; ``tests/test_framebuffer_access.py`` pins
  it.
* ``divBy255``: ``v.resize(17)`` of the 32-bit product, 17-bit adds (never
  wrap: max 65025 + 1 + 254), ``>> 8`` -> 9 bits, ``resize(8)``.
* ``clampAdd``: 9-bit sum (max 510, no wrap), ``min(255)``, ``resize(8)``.
* ``oneMinusU8``: ``U(255, 8) - x`` in 8 bits, never wraps.

Ordering hazard, ported as-is
-----------------------------

``colorRsp`` gives ``colorFetched`` priority but lets ``colorBypass`` go
first whenever the fetched join is not yet valid.  The bypass leg is the
*head* of ``colorCtlBase`` while ``colorCtlRoutes(1).queue(32)`` holds the
older pixels that are still waiting for their read, so a pixel with
``needColorRead = 0`` that follows one with ``needColorRead = 1`` can
overtake it while the response is outstanding, and the ``passMeta`` join
then pairs the wrong data with each.  The same holds for the aux plane.
Within one triangle ``alphaMode``/``fbzMode`` are constant so it does not
arise there; the pysim test shows it directly and the Python model in
``tests/test_framebuffer_access.py`` therefore keeps the read-needs constant
per batch.  The port reproduces it exactly (the lockstep diff flips the
mode bits per pixel).

Chroma key
----------

``chromaKey`` is a pure pass-through here (it is applied in
``PixelPipeline``, after the colour combine); no chroma-key logic lives in
this module on either side.

Port names follow ``tools.diff.naming.camel_to_snake`` of the Scala
(``io.fbReadColorReq`` -> ``fb_read_color_req``, ``io.zFuncFail`` ->
``z_func_fail``); internal signal names follow the Scala where Amaranth lets
them (``requestStream_fork``, ``colorCtlBase``, ``passMeta``, ``fetched``,
``afterDepthTest``, ``blendPrep``) so the two waveforms line up.  The
members go to ``super().__init__`` because of ``from __future__ import
annotations`` (see ``pixel/dither.py``).
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, memory, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (
    ALPHA_MODE_LAYOUT,
    TRACE_PIXEL_KEY_LAYOUT,
    Color,
    FbRouting,
    FbzMode,
    FramebufferReadReq,
    FramebufferReadRsp,
    PixelCoords,
    expand_rgb565,
    plane_address,
)
from avoodoo.lib.stream import (
    Stream,
    demux,
    fire,
    fork,
    join,
    m2s_pipe,
    queue,
    throw_when,
    translate_with,
)
from avoodoo.pixel.dither_tables import dsub_g_packed, dsub_rb_packed
from avoodoo.pixel.fog import FogOutput

__all__ = [
    "FramebufferAccessOutput",
    "Passthrough",
    "Internal",
    "FetchResult",
    "BlendPrep",
    "ROM_DEPTH",
    "FramebufferAccess",
]


#: 4096 entries of 4x4 table + 1024 of 2x2, per ``dsub*Packed`` ROM.
ROM_DEPTH = 5120


# ---------------------------------------------------------------------------
# object FramebufferAccess { case class Output(c) }
# ---------------------------------------------------------------------------


class FramebufferAccessOutput(data.StructLayout):
    """``FramebufferAccess.Output(c)``.

    The Scala ``def``s ``fbBaseAddr`` / ``auxBaseAddr`` / ``fbPixelStride``
    are ``view.routing.color_base_addr`` and friends.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        members = {
            "coords": PixelCoords(c),
            "color": Color.u8(),
            "alpha": unsigned(8),
            "color_before_fog": Color.u8(),
            "new_depth": unsigned(16),
            "alpha_mode": ALPHA_MODE_LAYOUT,
            "fbz_mode": FbzMode(),
            "chroma_key": unsigned(32),
            "rgb_write": unsigned(1),
            "aux_write": unsigned(1),
            "enable_alpha_planes": unsigned(1),
            "enable_dithering": unsigned(1),
            "dither_algorithm": unsigned(1),
            "routing": FbRouting(c),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(members)


# ---------------------------------------------------------------------------
# the four internal bundles (case classes inside the Component)
# ---------------------------------------------------------------------------


class Passthrough(data.StructLayout):
    """``case class Passthrough()`` -- the pixel data carried around the reads."""

    def __init__(self, c: Config) -> None:
        self.c = c
        members = {
            "coords": PixelCoords(c),
            "color": Color.u8(),
            "alpha": unsigned(8),
            "color_before_fog": Color.u8(),
            "chroma_key": unsigned(32),
            "new_depth": unsigned(16),
            "alpha_mode": ALPHA_MODE_LAYOUT,
            "fbz_mode": FbzMode(),
            "routing": FbRouting(c),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(members)


class Internal(data.StructLayout):
    """``case class Internal()`` -- the forked request record."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "color_address": unsigned(c.address_width),
            "aux_address": unsigned(c.address_width),
            "need_color_read": unsigned(1),
            "need_aux_read": unsigned(1),
            "passthrough": Passthrough(c),
        })


class FetchResult(data.StructLayout):
    """``case class FetchResult()`` -- both plane words plus the passthrough."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "color_data": unsigned(16),
            "aux_data": unsigned(16),
            "passthrough": Passthrough(c),
        })


class BlendPrep(data.StructLayout):
    """``case class BlendPrep()`` -- the payload of the second pipeline register."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "passthrough": Passthrough(c),
            "blend_dest_color": Color.u8(),
            "src_scale": Color.u8(),
            "dst_scale": Color.u8(),
        })


# ---------------------------------------------------------------------------
# helpers (the Scala `def`s inside the Component)
# ---------------------------------------------------------------------------


def _min(a, b):
    """Spinal's ``UInt.min``."""
    return Mux(a < b, a, b)


def _dither_addr(v, use2x2, x, y):
    """``def ditherAddr(v, use2x2, x, y): UInt`` -- 13-bit packed ROM address.

    ``Cat`` is LSB-first, so these read backwards from the Scala's MSB-first
    ``##``; the widths are the ``resize``\\ s spelled out (4x4: 12 bits zero-
    extended to 13; 2x2: 10 bits zero-extended to 12 with a leading 1).
    """
    addr4x4 = Cat(x, y, v)              # (v ## y ## x).asUInt, 12 bits
    addr2x2 = Cat(x[0], y[0], v)        # (v ## y(0) ## x(0)).asUInt, 10 bits
    return Mux(use2x2,
               Cat(addr2x2, Const(0, 2), Const(1, 1)),
               Cat(addr4x4, Const(0, 1)))


def _div_by_255(m, v):
    """``def divBy255(v: UInt): UInt`` -- ``(v + 1 + (v >> 8)) >> 8`` in 17 bits.

    Exact ``a*b/255`` for ``0 <= a*b <= 65025``.  ``wide`` is
    ``v.resize(17 bits)``; both adds stay 17 bits wide (Spinal ``+`` does not
    grow), ``>> 8`` narrows to 9, ``resize(8)`` keeps the low byte.
    """
    wide = Signal(17)
    m.d.comb += wide.eq(v)          # resize(17): zero-extend or truncate
    total = (wide + 1)[0:17]
    total = (total + (wide >> 8))[0:17]
    return (total >> 8)[0:8]


def _blend_mul(m, factor, color):
    """``def blendMul(factor, color): UInt`` -- ``factor * color / 255``.

    ``factor.resize(16) * color.resize(16)`` is a UInt(32) product of two
    bytes, i.e. at most 65025; the ``resize(17)`` inside ``divBy255`` then
    keeps it whole.
    """
    return _div_by_255(m, factor[0:8].as_unsigned() * color[0:8].as_unsigned())


def _one_minus_u8(x):
    """``def oneMinusU8(x: UInt): UInt`` -- ``255 - x`` in 8 bits."""
    return (Const(255, 8) - x)[0:8]


def _compute_blend_scale(m, factor_sel, src_alpha, dest_alpha, color_factor, *,
                         is_src: bool, col_before_fog, name: str):
    """``def computeBlendScale(...)``: one ``switch(factorSel)`` driving a ``Color.u8()``.

    ``color_factor`` and ``col_before_fog`` are ``Color`` views; the returned
    signal is a fresh ``Color.u8()`` view named ``name``.
    """
    scale = Signal(Color.u8(), name=name)

    def each(value):
        return [scale.r.eq(value), scale.g.eq(value), scale.b.eq(value)]

    with m.Switch(factor_sel):
        with m.Case(0x0):                                   # ZERO
            m.d.comb += each(Const(0, 8))
        with m.Case(0x1):                                   # SRC_ALPHA
            m.d.comb += each(src_alpha)
        with m.Case(0x2):                                   # COLOR
            m.d.comb += scale.eq(color_factor)
        with m.Case(0x3):                                   # DST_ALPHA
            m.d.comb += each(dest_alpha)
        with m.Case(0x4):                                   # ONE
            m.d.comb += each(Const(255, 8))
        with m.Case(0x5):                                   # ONE_MINUS_SRC_ALPHA
            m.d.comb += each(_one_minus_u8(src_alpha))
        with m.Case(0x6):                                   # ONE_MINUS_COLOR
            m.d.comb += [
                scale.r.eq(_one_minus_u8(color_factor.r)),
                scale.g.eq(_one_minus_u8(color_factor.g)),
                scale.b.eq(_one_minus_u8(color_factor.b)),
            ]
        with m.Case(0x7):                                   # ONE_MINUS_DST_ALPHA
            m.d.comb += each(_one_minus_u8(dest_alpha))
        with m.Case(0xF):
            if is_src:                                      # SATURATE
                m.d.comb += each(_min(src_alpha, _one_minus_u8(dest_alpha)))
            else:                                           # ACOLORBEFOREFOG
                m.d.comb += scale.eq(col_before_fog)
        with m.Default():
            m.d.comb += each(Const(255 if is_src else 0, 8))
    return scale


def _apply_blend_scale(m, self_color, scale, *, name: str):
    """``def applyBlendScale(selfColor, scale): Color[UInt]`` -- per-channel ``blendMul``."""
    result = Signal(Color.u8(), name=name)
    m.d.comb += [
        result.r.eq(_blend_mul(m, scale.r, self_color.r)),
        result.g.eq(_blend_mul(m, scale.g, self_color.g)),
        result.b.eq(_blend_mul(m, scale.b, self_color.b)),
    ]
    return result


def _clamp_add(a, b):
    """``def clampAdd(a, b): UInt`` -- 9-bit sum, ``min(255)``, ``resize(8)``."""
    total = (a[0:8].as_unsigned() + b[0:8].as_unsigned())[0:9]
    return _min(total, Const(255, 9))[0:8]


# ---------------------------------------------------------------------------
# case class FramebufferAccess(c: Config) extends Component
# ---------------------------------------------------------------------------


class FramebufferAccess(wiring.Component):
    """``case class FramebufferAccess(c: Config) extends Component``.

    Attributes
    ----------
    input : In(stream.Signature(FogOutput(c)))
        ``slave Stream(Fog.Output(c))``.
    output : Out(stream.Signature(FramebufferAccessOutput(c)))
        ``master Stream(FramebufferAccess.Output(c))``.
    fb_read_color_req, fb_read_aux_req : Out(stream.Signature(FramebufferReadReq(c)))
        ``master Stream(FramebufferPlaneBuffer.ReadReq(c))``.
    fb_read_color_rsp, fb_read_aux_rsp : In(stream.Signature(FramebufferReadRsp()))
        ``slave Stream(FramebufferPlaneBuffer.ReadRsp())``.
    busy : Out(1)
        ``inFlightCount =/= 0``.
    z_func_fail : Out(1)
        ``fetched.fire && depthKill``.
    debug : Out(32)
        the 32 handshake/occupancy bits of ``io.debug``.
    """

    def __init__(self, c: Config | None = None) -> None:
        c = c if c is not None else Config.voodoo1()
        self.c = c
        super().__init__({
            "input": In(stream.Signature(FogOutput(c))),
            "output": Out(stream.Signature(FramebufferAccessOutput(c))),
            "fb_read_color_req": Out(stream.Signature(FramebufferReadReq(c))),
            "fb_read_color_rsp": In(stream.Signature(FramebufferReadRsp())),
            "fb_read_aux_req": Out(stream.Signature(FramebufferReadReq(c))),
            "fb_read_aux_rsp": In(stream.Signature(FramebufferReadRsp())),
            "busy": Out(1),
            "z_func_fail": Out(1),
            "debug": Out(32),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        aw = c.address_width

        # `val inFlightCount = Reg(UInt(5 bits)) init 0` -- has a reset.
        in_flight_count = Signal(5, name="inFlightCount")

        payload = self.input.payload
        fbz_mode = payload.fbz_mode

        # ==================================================================
        # Step 1: Compute newDepth (combinational, before fork)
        # ==================================================================

        # Depth source selection
        za_depth = Signal(16, name="zaDepth")
        m.d.comb += za_depth.eq(payload.za_color[0:16])

        # Z-buffer depth: clamp SQ(32,12) >> 12 to 16 bits.
        # `(depthRaw >> 12).resize(20 bits)`: SInt(32) >> 12 is SInt(20).
        depth_raw = Signal(signed(32), name="depthRaw")
        z_depth_shifted = Signal(signed(20), name="zDepthShifted")
        z_depth = Signal(16, name="zDepth")
        m.d.comb += [
            depth_raw.eq(payload.depth.as_value()),
            z_depth_shifted.eq(depth_raw >> 12),
        ]
        with m.If(z_depth_shifted < 0):
            m.d.comb += z_depth.eq(0)
        with m.Elif(z_depth_shifted > 0xFFFF):
            m.d.comb += z_depth.eq(0xFFFF)
        with m.Else():
            m.d.comb += z_depth.eq(z_depth_shifted[0:16])

        # Select base depth: Z or W buffer
        base_depth = Signal(16, name="baseDepth")
        m.d.comb += base_depth.eq(Mux(fbz_mode.w_buffer_select, payload.w_depth, z_depth))

        # Depth source: normal pipeline or zaColor register
        comp_depth_pre_bias = Signal(16, name="compDepthPreBias")
        m.d.comb += comp_depth_pre_bias.eq(
            Mux(fbz_mode.depth_source_select, za_depth, base_depth))

        # Depth bias: signed add of zaColor[15:0].  SInt(17) + SInt(17) stays
        # SInt(17) in Spinal, so the sum WRAPS before the clamp.
        za_color_signed = Signal(signed(16), name="zaColorSigned")
        biased_depth_raw = Signal(signed(17), name="biasedDepthRaw")
        biased_depth = Signal(16, name="biasedDepth")
        m.d.comb += [
            za_color_signed.eq(payload.za_color[0:16]),
            biased_depth_raw.eq(
                (Cat(comp_depth_pre_bias, Const(0, 1)).as_signed()
                 + za_color_signed)[0:17].as_signed()),
        ]
        with m.If(biased_depth_raw < 0):
            m.d.comb += biased_depth.eq(0)
        with m.Elif(biased_depth_raw > 0xFFFF):
            m.d.comb += biased_depth.eq(0xFFFF)
        with m.Else():
            m.d.comb += biased_depth.eq(biased_depth_raw[0:16])

        comp_depth = Signal(16, name="compDepth")
        m.d.comb += comp_depth.eq(
            Mux(fbz_mode.enable_depth_bias, biased_depth, comp_depth_pre_bias))

        # ==================================================================
        # Step 2: Framebuffer fetch adapter
        # ==================================================================

        request = Signal(Internal(c), name="request")
        # `payload.coords(0).asUInt`: the SInt(12) coordinates reinterpreted.
        pixel_x = payload.coords.x.as_unsigned()
        pixel_y = payload.coords.y.as_unsigned()
        color_plane_address = Signal(aw, name="colorPlaneAddress")
        aux_plane_address = Signal(aw, name="auxPlaneAddress")
        m.d.comb += [
            color_plane_address.eq(plane_address(
                payload.routing.color_base_addr, pixel_x, pixel_y,
                payload.routing.pixel_stride)),
            aux_plane_address.eq(plane_address(
                payload.routing.aux_base_addr, pixel_x, pixel_y,
                payload.routing.pixel_stride)),
            # `(planeAddress(aw-1 downto 1) ## U"1'b0").asUInt`
            request.color_address.eq(Cat(Const(0, 1), color_plane_address[1:aw])),
            request.aux_address.eq(Cat(Const(0, 1), aux_plane_address[1:aw])),
            request.need_color_read.eq(
                payload.alpha_mode.alpha_blend_enable | fbz_mode.enable_dither_subtract),
            request.need_aux_read.eq(fbz_mode.enable_depth_buffer),
            request.passthrough.coords.eq(payload.coords),
            request.passthrough.color.eq(payload.color),
            request.passthrough.alpha.eq(payload.alpha),
            request.passthrough.color_before_fog.eq(payload.color_before_fog),
            request.passthrough.chroma_key.eq(payload.chroma_key),
            request.passthrough.new_depth.eq(comp_depth),
            request.passthrough.alpha_mode.eq(payload.alpha_mode),
            request.passthrough.fbz_mode.eq(fbz_mode),
            request.passthrough.routing.eq(payload.routing),
        ]
        if c.trace.enabled:
            m.d.comb += request.passthrough.trace.eq(payload.trace)

        request_stream = translate_with(m, self.input, request)

        # `StreamFork(requestStream, portCount = 5, synchronous = false)`
        forked = fork(m, request_stream, 5, synchronous=False, name="requestStream_fork")
        color_req_base = forked[0]
        aux_req_base = forked[1]
        color_ctl_base = queue(m, forked[2], 32, name="io_outputs_2_fifo")
        aux_ctl_base = queue(m, forked[3], 32, name="io_outputs_3_fifo")
        pass_meta = queue(m, forked[4], 32, name="io_outputs_4_fifo")

        # io.fbReadColorReq << colorReqBase.throwWhen(!needColorRead).translateWith{...}
        color_req = Signal(FramebufferReadReq(c), name="colorReq")
        m.d.comb += color_req.address.eq(color_req_base.payload.color_address)
        color_req_stream = translate_with(
            m, throw_when(m, color_req_base, ~color_req_base.payload.need_color_read),
            color_req)
        m.d.comb += [
            self.fb_read_color_req.valid.eq(color_req_stream.valid),
            color_req_stream.ready.eq(self.fb_read_color_req.ready),
            self.fb_read_color_req.payload.eq(color_req_stream.payload),
        ]

        aux_req = Signal(FramebufferReadReq(c), name="auxReq")
        m.d.comb += aux_req.address.eq(aux_req_base.payload.aux_address)
        aux_req_stream = translate_with(
            m, throw_when(m, aux_req_base, ~aux_req_base.payload.need_aux_read),
            aux_req)
        m.d.comb += [
            self.fb_read_aux_req.valid.eq(aux_req_stream.valid),
            aux_req_stream.ready.eq(self.fb_read_aux_req.ready),
            self.fb_read_aux_req.payload.eq(aux_req_stream.payload),
        ]

        # `val colorRspBuf = io.fbReadColorRsp.queue(4)`
        color_rsp_buf = queue(m, self.fb_read_color_rsp, 4, name="io_fbReadColorRsp_fifo")
        aux_rsp_buf = queue(m, self.fb_read_aux_rsp, 4, name="io_fbReadAuxRsp_fifo")

        def plane_response(ctl_base, need, rsp_buf, *, tag: str):
            """The demux / join / bypass / priority-mux block, once per plane."""
            # `StreamDemux(ctlBase, ctlBase.payload.needXRead.asUInt, 2)`
            routes = demux(m, ctl_base, need, 2, name=f"{tag}CtlRoutes")
            # `StreamJoin(routes(1).queue(32), rspBuf).translateWith{ data := rspBuf.data }`
            fetched_rsp = Signal(FramebufferReadRsp(), name=f"{tag}FetchedRsp")
            m.d.comb += fetched_rsp.data.eq(rsp_buf.payload.data)
            fetched = translate_with(
                m, join(m, queue(m, routes[1], 32, name=f"{tag}CtlQueue"), rsp_buf,
                        name=f"{tag}FetchedJoin"),
                fetched_rsp)
            # `routes(0).translateWith{ data := 0 }`
            bypass_rsp = Signal(FramebufferReadRsp(), name=f"{tag}BypassRsp")
            m.d.comb += bypass_rsp.data.eq(0)
            bypass = translate_with(m, routes[0], bypass_rsp)
            # the hand-written priority mux, fetched first
            rsp = Stream(FramebufferReadRsp(), path=(f"{tag}Rsp",))
            m.d.comb += [
                rsp.valid.eq(fetched.valid | bypass.valid),
                rsp.payload.eq(bypass.payload),
            ]
            with m.If(fetched.valid):
                m.d.comb += rsp.payload.eq(fetched.payload)
            m.d.comb += [
                fetched.ready.eq(rsp.ready),
                bypass.ready.eq(rsp.ready & ~fetched.valid),
            ]
            return rsp, fetched, bypass

        color_rsp, color_fetched, color_bypass = plane_response(
            color_ctl_base, color_ctl_base.payload.need_color_read, color_rsp_buf,
            tag="color")
        aux_rsp, aux_fetched, aux_bypass = plane_response(
            aux_ctl_base, aux_ctl_base.payload.need_aux_read, aux_rsp_buf,
            tag="aux")

        # `fetchedRaw = StreamJoin(StreamJoin(colorRsp, auxRsp), passMeta).translateWith{...}`
        fetch_result = Signal(FetchResult(c), name="fetchResult")
        m.d.comb += [
            fetch_result.color_data.eq(color_rsp.payload.data),
            fetch_result.aux_data.eq(aux_rsp.payload.data),
            fetch_result.passthrough.eq(pass_meta.payload.passthrough),
        ]
        fetched_raw = translate_with(
            m,
            join(m, join(m, color_rsp, aux_rsp, name="planeJoin"), pass_meta,
                 name="fetchedRawJoin"),
            fetch_result)

        # `val fetched = fetchedRaw.queue(32)`
        fetched = queue(m, fetched_raw, 32, name="fetchedRaw_fifo")

        exit_fire = Signal(name="exitFire")
        m.d.comb += exit_fire.eq(fire(fetched))
        with m.If(fire(self.input) & ~exit_fire):
            m.d.sync += in_flight_count.eq(in_flight_count + 1)
        with m.Elif(~fire(self.input) & exit_fire):
            m.d.sync += in_flight_count.eq(in_flight_count - 1)
        m.d.comb += self.busy.eq(in_flight_count != 0)

        # ==================================================================
        # Step 3: Depth test (per-pixel fbzMode)
        # ==================================================================

        pdata = fetched.payload.passthrough
        old_depth = Signal(16, name="oldDepth")
        m.d.comb += old_depth.eq(fetched.payload.aux_data)

        depth_passed = Signal(name="depthPassed")
        new_depth = pdata.new_depth
        with m.Switch(pdata.fbz_mode.depth_function):
            with m.Case(0):
                m.d.comb += depth_passed.eq(0)
            with m.Case(1):
                m.d.comb += depth_passed.eq(new_depth < old_depth)
            with m.Case(2):
                m.d.comb += depth_passed.eq(new_depth == old_depth)
            with m.Case(3):
                m.d.comb += depth_passed.eq(new_depth <= old_depth)
            with m.Case(4):
                m.d.comb += depth_passed.eq(new_depth > old_depth)
            with m.Case(5):
                m.d.comb += depth_passed.eq(new_depth != old_depth)
            with m.Case(6):
                m.d.comb += depth_passed.eq(new_depth >= old_depth)
            with m.Case(7):
                m.d.comb += depth_passed.eq(1)

        depth_kill = Signal(name="depthKill")
        m.d.comb += [
            depth_kill.eq(pdata.fbz_mode.enable_depth_buffer & ~depth_passed),
            self.z_func_fail.eq(fire(fetched) & depth_kill),
        ]
        after_depth_test = throw_when(m, fetched, depth_kill)
        after_depth_test_piped = m2s_pipe(m, after_depth_test, name="afterDepthTest")

        # ==================================================================
        # Step 4: Alpha blend
        # ==================================================================

        after_color_data = after_depth_test_piped.payload.color_data
        after_pdata = after_depth_test_piped.payload.passthrough

        old_color565 = Signal(16, name="oldColor565")
        dest_color = Signal(Color.u8(), name="destColor")
        m.d.comb += old_color565.eq(after_color_data)
        er, eg, eb = expand_rgb565(old_color565)
        m.d.comb += [dest_color.r.eq(er), dest_color.g.eq(eg), dest_color.b.eq(eb)]

        # `val dsubRbRom = Vec(DitherTables.dsubRbPacked.map(U(_, 8 bits)))`, read
        # combinationally with a dynamic index: a comb-read ROM here, one
        # image serving both R and B through two read ports.
        m.submodules.dsub_rb_rom = dsub_rb_rom = memory.Memory(
            shape=unsigned(8), depth=ROM_DEPTH, init=dsub_rb_packed)
        m.submodules.dsub_g_rom = dsub_g_rom = memory.Memory(
            shape=unsigned(8), depth=ROM_DEPTH, init=dsub_g_packed)
        rd_r = dsub_rb_rom.read_port(domain="comb")
        rd_g = dsub_g_rom.read_port(domain="comb")
        rd_b = dsub_rb_rom.read_port(domain="comb")

        dsub_x = Signal(2, name="dsubX")
        dsub_y = Signal(2, name="dsubY")
        m.d.comb += [
            dsub_x.eq(after_pdata.coords.x[0:2]),
            dsub_y.eq(after_pdata.coords.y[0:2]),
        ]
        use2x2 = after_pdata.fbz_mode.dither_algorithm
        dest_dsub = Signal(Color.u8(), name="destDsub")
        m.d.comb += [
            rd_r.addr.eq(_dither_addr(dest_color.r, use2x2, dsub_x, dsub_y)),
            rd_g.addr.eq(_dither_addr(dest_color.g, use2x2, dsub_x, dsub_y)),
            rd_b.addr.eq(_dither_addr(dest_color.b, use2x2, dsub_x, dsub_y)),
            dest_dsub.r.eq(rd_r.data),
            dest_dsub.g.eq(rd_g.data),
            dest_dsub.b.eq(rd_b.data),
        ]
        blend_dest_color = Signal(Color.u8(), name="blendDestColor")
        m.d.comb += blend_dest_color.eq(
            Mux(after_pdata.fbz_mode.enable_dither_subtract, dest_dsub, dest_color))
        dest_a = Const(0xFF, 8)

        src_color = after_pdata.color
        src_a = after_pdata.alpha
        color_before_fog = after_pdata.color_before_fog

        src_scale = _compute_blend_scale(
            m, after_pdata.alpha_mode.rgb_src_fact, src_a, dest_a, blend_dest_color,
            is_src=True, col_before_fog=color_before_fog, name="srcScale")
        dst_scale = _compute_blend_scale(
            m, after_pdata.alpha_mode.rgb_dst_fact, src_a, dest_a, src_color,
            is_src=False, col_before_fog=color_before_fog, name="dstScale")

        prep = Signal(BlendPrep(c), name="prep")
        m.d.comb += [
            prep.passthrough.eq(after_pdata),
            prep.blend_dest_color.eq(blend_dest_color),
            prep.src_scale.eq(src_scale),
            prep.dst_scale.eq(dst_scale),
        ]
        blend_prep = m2s_pipe(m, translate_with(m, after_depth_test_piped, prep),
                              name="blendPrep")

        # `io.debug := 0; io.debug(0) := ...`
        m.d.comb += self.debug.eq(Cat(
            self.input.valid,                   # 0
            self.input.ready,                   # 1
            self.output.valid,                  # 2
            self.output.ready,                  # 3
            self.busy,                          # 4
            self.fb_read_color_req.valid,       # 5
            self.fb_read_color_req.ready,       # 6
            self.fb_read_color_rsp.valid,       # 7
            self.fb_read_color_rsp.ready,       # 8
            self.fb_read_aux_req.valid,         # 9
            self.fb_read_aux_req.ready,         # 10
            self.fb_read_aux_rsp.valid,         # 11
            self.fb_read_aux_rsp.ready,         # 12
            request_stream.valid,               # 13
            request_stream.ready,               # 14
            color_ctl_base.valid,               # 15
            color_ctl_base.ready,               # 16
            aux_ctl_base.valid,                 # 17
            aux_ctl_base.ready,                 # 18
            pass_meta.valid,                    # 19
            pass_meta.ready,                    # 20
            color_fetched.valid,                # 21
            color_bypass.valid,                 # 22
            aux_fetched.valid,                  # 23
            aux_bypass.valid,                   # 24
            fetched.valid,                      # 25
            fetched.ready,                      # 26
            in_flight_count,                    # 31 downto 27
        ))

        blend_pdata = blend_prep.payload.passthrough
        src_factor = _apply_blend_scale(
            m, blend_pdata.color, blend_prep.payload.src_scale, name="srcFactor")
        dst_factor = _apply_blend_scale(
            m, blend_prep.payload.blend_dest_color, blend_prep.payload.dst_scale,
            name="dstFactor")

        # Sum and clamp to [0, 255]
        blended = Signal(Color.u8(), name="blended")
        m.d.comb += [
            blended.r.eq(_clamp_add(src_factor.r, dst_factor.r)),
            blended.g.eq(_clamp_add(src_factor.g, dst_factor.g)),
            blended.b.eq(_clamp_add(src_factor.b, dst_factor.b)),
        ]

        # ==================================================================
        # Step 5: Output -- `io.output.translateFrom(blendPrep) { (out, in) => ... }`
        # ==================================================================

        out = self.output.payload
        pd = blend_pdata
        m.d.comb += [
            self.output.valid.eq(blend_prep.valid),
            blend_prep.ready.eq(self.output.ready),
            out.coords.eq(pd.coords),
            out.color.eq(Mux(pd.alpha_mode.alpha_blend_enable, blended, pd.color)),
            out.alpha.eq(pd.alpha),
            out.color_before_fog.eq(pd.color_before_fog),
            out.new_depth.eq(pd.new_depth),
            out.alpha_mode.eq(pd.alpha_mode),
            out.fbz_mode.eq(pd.fbz_mode),
            out.chroma_key.eq(pd.chroma_key),
            out.rgb_write.eq(pd.fbz_mode.rgb_buffer_mask),
            out.aux_write.eq(pd.fbz_mode.aux_buffer_mask),
            out.enable_alpha_planes.eq(pd.fbz_mode.enable_alpha_planes),
            out.enable_dithering.eq(pd.fbz_mode.enable_dithering),
            out.dither_algorithm.eq(pd.fbz_mode.dither_algorithm),
            out.routing.eq(pd.routing),
        ]
        if c.trace.enabled:
            m.d.comb += out.trace.eq(pd.trace)

        return m
