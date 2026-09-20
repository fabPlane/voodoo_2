"""``Lfb`` -- linear frame buffer writes and reads.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/frontend/Lfb.scala`` (SpinalHDL
1.15.0), instantiated by ``PixelPipeline.scala`` as ``Lfb(c)``.

Accepts CPU accesses to the 4 MiB LFB window (``Lfb.bmbParams``: 22 address
bits, one word per command) and turns a **write** into either

* ``writeOutput`` -- a ``Write.PreDither`` item for the shared ``Dither`` +
  ``Write`` instances in ``Core`` (``lfbMode.pixelPipelineEnable = 0``), or
* ``pipelineOutput`` -- a ``ColorCombine.Output`` item injected into the
  pixel pipeline after colour combine (``pixelPipelineEnable = 1``),

and a **read** into two 16-bit framebuffer-plane fetches on ``fbReadBus``
(pixel ``x`` and ``x+1``) packed back into one 32-bit response.

Write formats (``lfbMode.writeFormat``), as the Scala ``switch`` has them:

======  ==================  ======  =============================================
format  name                pixels  decode
======  ==================  ======  =============================================
0       RGB565              2       ``expandRgb565(pixelData16)``
1       RGB555              2       ``expandRgb555(pixelData16)``
2       ARGB1555            2       ``expandRgb555``; alpha from bit 15
3       (undecoded)         2       RGB 0, ``rgbWrite`` -- the ``switch`` has no
                                    ``is(3)``, so two black pixels are emitted
4       XRGB8888            1       ``rgbaLanes`` swizzle of the 32-bit word
5       ARGB8888            1       same, plus 8-bit alpha
12      depth + RGB565      1       ``lo16`` colour, ``hi16`` depth, ``auxWrite``
13      depth + RGB555      1       same
14      depth + ARGB1555    1       same; alpha from ``lo16[15]``
15      depth only          2       ``pixelData16`` is the depth; ``rgbWrite = 0``
other                       1       as format 3 but single pixel
======  ==================  ======  =============================================

A dual-pixel write with a half-word mask (``0011`` / ``1100``) emits one pixel
only, at ``x`` or ``x + 1`` respectively; a 32-bit-stride format (4/5) decodes
``x = addr >> 2``, ``y = addr >> 12``, everything else ``x = addr >> 1``,
``y = addr >> 11``.  ``wordSwapAndSwizzle`` is applied to the write data on
capture and to the read response on the way out.

Reads (``stateRwait``) block until ``pciFifoEmpty && !pipelineBusy`` (the
SST-1 rule that an LFB read sees every preceding write), which is also why
``io.busy`` counts only the two write states -- ``pipelineBusy`` includes it,
and a read state contributing would deadlock the wait.

State register (``RegInit(U(0, 3 bits))``): ``0`` idle, ``1``/``2`` pixel
1/2, ``3``/``4`` fetch 1/2, ``5`` read response, ``6`` read wait.

Reset split, read off the ``if(reset)`` block of ``Lfb_spinal.v``:
``capturedMask``, ``capturedIsRead``, ``rspPending``, ``capturedRead1LaneHi``,
``capturedRead2LaneHi``, ``fbReadPlaneAddr``, ``state`` and
``fbReadCmdPending`` are ``RegInit``/``init(...)``; ``capturedAddr``,
``capturedData``, ``capturedRead1``, ``capturedRead2``, ``fbReadAddr`` and
``capturedSource`` are plain ``Reg``s and therefore ``reset_less`` here.
Register writes are kept in the Scala's statement order because the sync
block relies on last-assignment-wins in two places (``state`` in the read
path, ``fbReadCmdPending`` across cmd/rsp fire).

``readPixelX``/``readPixelY`` are computed in the Scala and never used (the
read path recomputes them from ``io.bus.cmd.address`` at capture time); they
are kept as named signals so the two netlists read alike.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (ColorCombineOutput, LfbRegs, TraceOrigin,
                                     expand_rgb555, expand_rgb565, plane_address,
                                     word_swap_and_swizzle)
from avoodoo.core.membus_params import lfb_fb_read_params, lfb_params
from avoodoo.lib.membus import CMD_READ, MemBusSignature, RSP_SUCCESS
from avoodoo.lib.stream import fire
from avoodoo.pixel.write import WritePreDither

__all__ = [
    "Lfb",
    "STATE_IDLE",
    "STATE_PIXEL1",
    "STATE_PIXEL2",
    "STATE_RFETCH1",
    "STATE_RFETCH2",
    "STATE_RRESP",
    "STATE_RWAIT",
    "DUAL_PIXEL_FORMATS",
    "STRIDE32_FORMATS",
]

# val stateIdle = U(0, 3 bits) ... stateRwait = U(6, 3 bits)
STATE_IDLE = 0
STATE_PIXEL1 = 1
STATE_PIXEL2 = 2
STATE_RFETCH1 = 3
STATE_RFETCH2 = 4
STATE_RRESP = 5
STATE_RWAIT = 6

#: ``isDualPixel`` -- the 16-bit formats.
DUAL_PIXEL_FORMATS = (0, 1, 2, 3, 15)
#: ``is32bitStride`` -- the formats addressed as 32-bit pixels.
STRIDE32_FORMATS = (4, 5)


def _origin_pixel_key(m: Module, view, origin: int) -> None:
    """``Trace.originPixelKey(origin)`` assigned into ``view`` (a ``PixelKey``)."""
    m.d.comb += [
        view.primitive.valid.eq(1),
        view.primitive.origin.eq(origin),
        view.primitive.draw_id.eq(0),
        view.primitive.primitive_id.eq(0),
        view.pixel_seq.eq(0),
    ]


class Lfb(wiring.Component):
    """``case class Lfb(c: Config) extends Component``.

    Attributes
    ----------
    bus : In(MemBusSignature(lfb_params(c)))
        ``slave(Bmb(Lfb.bmbParams(c)))``.
    write_output : Out(stream.Signature(WritePreDither(c)))
        ``master Stream(Write.PreDither(c))`` -- bypass mode.
    pipeline_output : Out(stream.Signature(ColorCombineOutput(c)))
        ``master Stream(ColorCombine.Output(c))`` -- pipeline mode.
    busy : Out(1)
    regs : In(LfbRegs(c))
        ``in(Lfb.Regs(c))``.
    fb_read_bus : Out(MemBusSignature(lfb_fb_read_params(c)))
        ``master(Bmb(Lfb.fbReadBmbParams(c)))``.
    pci_fifo_empty, pipeline_busy : In(1)
    """

    def __init__(self, c: Config | None = None) -> None:
        c = c if c is not None else Config.voodoo1()
        self.c = c
        super().__init__({
            "bus": In(MemBusSignature(lfb_params(c))),
            "write_output": Out(stream.Signature(WritePreDither(c))),
            "pipeline_output": Out(stream.Signature(ColorCombineOutput(c))),
            "busy": Out(1),
            "regs": In(LfbRegs(c)),
            "fb_read_bus": Out(MemBusSignature(lfb_fb_read_params(c))),
            "pci_fifo_empty": In(1),
            "pipeline_busy": In(1),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        aw = c.address_width

        bus_cmd = self.bus.cmd
        bus_rsp = self.bus.rsp
        cmd = bus_cmd.payload.fragment
        rsp = bus_rsp.payload.fragment
        regs = self.regs
        lfb_mode = regs.lfb_mode
        fbz_mode = regs.fbz_mode
        write_format = lfb_mode.write_format

        # ------------------------------------------------------------------
        # Capture BMB command data
        # ------------------------------------------------------------------
        captured_addr = Signal(22, name="capturedAddr", reset_less=True)
        captured_data = Signal(32, name="capturedData", reset_less=True)
        captured_mask = Signal(4, name="capturedMask")
        captured_is_read = Signal(name="capturedIsRead")
        rsp_pending = Signal(name="rspPending")

        # Captured FB read results for read path
        captured_read1 = Signal(32, name="capturedRead1", reset_less=True)
        captured_read2 = Signal(32, name="capturedRead2", reset_less=True)
        captured_read1_lane_hi = Signal(name="capturedRead1LaneHi")
        captured_read2_lane_hi = Signal(name="capturedRead2LaneHi")
        fb_read_plane_addr = Signal(aw, name="fbReadPlaneAddr")

        # val isDualPixel = writeFormat === 0 || === 1 || === 2 || === 3 || === 15
        is_dual_pixel = Signal(name="isDualPixel")
        m.d.comb += is_dual_pixel.eq(
            (write_format == 0) | (write_format == 1) | (write_format == 2)
            | (write_format == 3) | (write_format == 15))

        # val state = RegInit(U(0, 3 bits))
        state = Signal(3, name="state")

        # io.bus.cmd.ready := (state === stateIdle) && !rspPending
        m.d.comb += bus_cmd.ready.eq((state == STATE_IDLE) & ~rsp_pending)

        # val afterByteSwizzle = wordSwapAndSwizzle(cmd.data, wordSwapWrites, byteSwizzleWrites)
        after_byte_swizzle = Signal(32, name="afterByteSwizzle")
        m.d.comb += after_byte_swizzle.eq(word_swap_and_swizzle(
            cmd.data, lfb_mode.word_swap_writes, lfb_mode.byte_swizzle_writes))

        bus_cmd_fire = Signal(name="io_bus_cmd_fire")
        m.d.comb += bus_cmd_fire.eq(fire(bus_cmd))
        cmd_is_read = cmd.opcode == CMD_READ

        # when(io.bus.cmd.fire) { ... }
        with m.If(bus_cmd_fire):
            m.d.sync += [
                captured_addr.eq(cmd.address),
                rsp_pending.eq(1),
            ]
            with m.If(cmd_is_read):
                m.d.sync += [
                    captured_is_read.eq(1),
                    state.eq(STATE_RWAIT),
                ]
            with m.Else():
                m.d.sync += [
                    captured_is_read.eq(0),
                    captured_data.eq(after_byte_swizzle),
                    captured_mask.eq(cmd.mask),
                    state.eq(STATE_PIXEL1),
                ]

        # ------------------------------------------------------------------
        # Address decode: depends on format stride (for writes)
        # ------------------------------------------------------------------
        is_32bit_stride = Signal(name="is32bitStride")
        m.d.comb += is_32bit_stride.eq((write_format == 4) | (write_format == 5))
        pixel_x = Signal(10, name="pixelX")
        pixel_y = Signal(10, name="pixelY")
        with m.If(is_32bit_stride):
            m.d.comb += [
                pixel_x.eq((captured_addr >> 2)[0:10]),
                pixel_y.eq((captured_addr >> 12)[0:10]),
            ]
        with m.Else():
            m.d.comb += [
                pixel_x.eq((captured_addr >> 1)[0:10]),
                pixel_y.eq((captured_addr >> 11)[0:10]),
            ]

        # Read address decode: always 16-bit stride (unused, see module doc)
        read_pixel_x = Signal(10, name="readPixelX")
        read_pixel_y = Signal(10, name="readPixelY")
        m.d.comb += [
            read_pixel_x.eq((captured_addr >> 1)[0:10]),
            read_pixel_y.eq((captured_addr >> 11)[0:10]),
        ]

        # For PIXEL2: x+1
        pixel2_x = Signal(10, name="pixel2X")
        m.d.comb += pixel2_x.eq((pixel_x + 1)[0:10])

        # Select 16-bit pixel data: PIXEL1 = lo16, PIXEL2 = hi16
        lo16 = Signal(16, name="lo16")
        hi16 = Signal(16, name="hi16")
        m.d.comb += [lo16.eq(captured_data[0:16]), hi16.eq(captured_data[16:32])]
        is_half_word_write = Signal(name="isHalfWordWrite")
        use_hi_half_for_pixel1 = Signal(name="useHiHalfForPixel1")
        m.d.comb += [
            is_half_word_write.eq((captured_mask == 0b0011) | (captured_mask == 0b1100)),
            use_hi_half_for_pixel1.eq(captured_mask == 0b1100),
        ]

        pixel_data16 = Signal(16, name="pixelData16")
        m.d.comb += pixel_data16.eq(
            Mux((state == STATE_PIXEL2) | use_hi_half_for_pixel1, hi16, lo16))

        # Full 32-bit data for XRGB8888/ARGB8888
        pixel_data32 = captured_data

        # ------------------------------------------------------------------
        # Format decode: expand to 8-bit RGB + depth + write masks
        # ------------------------------------------------------------------
        decoded_r = Signal(8, name="decodedR")
        decoded_g = Signal(8, name="decodedG")
        decoded_b = Signal(8, name="decodedB")
        decoded_depth = Signal(16, name="decodedDepth")
        decoded_rgb_write = Signal(name="decodedRgbWrite")
        decoded_aux_write = Signal(name="decodedAuxWrite")

        # Default assignments
        m.d.comb += [
            decoded_r.eq(0),
            decoded_g.eq(0),
            decoded_b.eq(0),
            # Default depth from zaColor when no explicit depth
            decoded_depth.eq(regs.za_color[0:16]),
            decoded_rgb_write.eq(1),
            decoded_aux_write.eq(0),
        ]

        def decode_argb8888(data_, lanes):
            """``decodeArgb8888(data, lanes)`` -> ``(r, g, b)`` signals."""
            r = Signal(8)
            g = Signal(8)
            b = Signal(8)
            with m.Switch(lanes):
                with m.Case(0):  # ARGB
                    m.d.comb += [r.eq(data_[16:24]), g.eq(data_[8:16]), b.eq(data_[0:8])]
                with m.Case(1):  # ABGR
                    m.d.comb += [r.eq(data_[0:8]), g.eq(data_[8:16]), b.eq(data_[16:24])]
                with m.Case(2):  # RGBA
                    m.d.comb += [r.eq(data_[24:32]), g.eq(data_[16:24]), b.eq(data_[8:16])]
                with m.Default():  # 3: BGRA
                    m.d.comb += [r.eq(data_[8:16]), g.eq(data_[16:24]), b.eq(data_[24:32])]
            return r, g, b

        def decode_alpha8888(data_, lanes):
            """``decodeAlpha8888(data, lanes)``."""
            a = Signal(8)
            with m.Switch(lanes):
                with m.Case(0, 1):  # ARGB / ABGR: A = [31:24]
                    m.d.comb += a.eq(data_[24:32])
                with m.Default():   # RGBA / BGRA: A = [7:0]
                    m.d.comb += a.eq(data_[0:8])
            return a

        def assign_rgb(rgb):
            r, g, b = rgb
            m.d.comb += [decoded_r.eq(r), decoded_g.eq(g), decoded_b.eq(b)]

        # Format decode logic
        with m.Switch(write_format):
            with m.Case(0):  # RGB565 - dual pixel
                assign_rgb(expand_rgb565(pixel_data16))
                m.d.comb += [decoded_rgb_write.eq(1),
                             decoded_aux_write.eq(fbz_mode.enable_alpha_planes)]
            with m.Case(1):  # RGB555 - dual pixel
                assign_rgb(expand_rgb555(pixel_data16))
                m.d.comb += [decoded_rgb_write.eq(1),
                             decoded_aux_write.eq(fbz_mode.enable_alpha_planes)]
            with m.Case(2):  # ARGB1555 - dual pixel
                assign_rgb(expand_rgb555(pixel_data16))
                m.d.comb += [decoded_rgb_write.eq(1),
                             decoded_aux_write.eq(fbz_mode.enable_alpha_planes)]
            with m.Case(4):  # XRGB8888 - single pixel
                assign_rgb(decode_argb8888(pixel_data32, lfb_mode.rgba_lanes))
                m.d.comb += [decoded_rgb_write.eq(1),
                             decoded_aux_write.eq(fbz_mode.enable_alpha_planes)]
            with m.Case(5):  # ARGB8888 - single pixel
                assign_rgb(decode_argb8888(pixel_data32, lfb_mode.rgba_lanes))
                m.d.comb += [decoded_rgb_write.eq(1),
                             decoded_aux_write.eq(fbz_mode.enable_alpha_planes)]
            with m.Case(12):  # Depth+RGB565 - single pixel (hi16=depth, lo16=RGB565)
                assign_rgb(expand_rgb565(lo16))
                m.d.comb += [decoded_depth.eq(hi16),
                             decoded_rgb_write.eq(1), decoded_aux_write.eq(1)]
            with m.Case(13):  # Depth+RGB555 - single pixel
                assign_rgb(expand_rgb555(lo16))
                m.d.comb += [decoded_depth.eq(hi16),
                             decoded_rgb_write.eq(1), decoded_aux_write.eq(1)]
            with m.Case(14):  # Depth+ARGB1555 - single pixel
                assign_rgb(expand_rgb555(lo16))
                m.d.comb += [decoded_depth.eq(hi16),
                             decoded_rgb_write.eq(1), decoded_aux_write.eq(1)]
            with m.Case(15):  # Depth-only - dual pixel
                m.d.comb += [decoded_depth.eq(pixel_data16),
                             decoded_rgb_write.eq(0), decoded_aux_write.eq(1)]

        # Decode alpha for pipeline mode
        decoded_alpha = Signal(8, name="decodedAlpha")
        m.d.comb += decoded_alpha.eq(0xFF)  # Default: fully opaque
        with m.Switch(write_format):
            with m.Case(5):  # ARGB8888: extract alpha
                m.d.comb += decoded_alpha.eq(
                    decode_alpha8888(pixel_data32, lfb_mode.rgba_lanes))
            with m.Case(2):  # ARGB1555 dual pixel: bit 15
                m.d.comb += decoded_alpha.eq(Mux(pixel_data16[15], 0xFF, 0))
            with m.Case(14):  # Depth+ARGB1555: bit 15 of lo16
                m.d.comb += decoded_alpha.eq(Mux(lo16[15], 0xFF, 0))

        # ------------------------------------------------------------------
        # Bypass mode: output Write.PreDither (dithering done by shared instance in Core)
        # ------------------------------------------------------------------
        write_pixel_active = Signal(name="writePixelActive")
        pixel1_x = Signal(10, name="pixel1X")
        current_x = Signal(10, name="currentX")
        m.d.comb += [
            write_pixel_active.eq((state == STATE_PIXEL1) | (state == STATE_PIXEL2)),
            pixel1_x.eq(Mux(is_half_word_write & use_hi_half_for_pixel1, pixel2_x, pixel_x)),
            current_x.eq(Mux(state == STATE_PIXEL2, pixel2_x, pixel1_x)),
        ]

        wo = self.write_output
        m.d.comb += wo.valid.eq(write_pixel_active & ~lfb_mode.pixel_pipeline_enable)
        # buildWritePreDither(currentX, pixelY)
        m.d.comb += [
            wo.payload.r.eq(decoded_r),
            wo.payload.g.eq(decoded_g),
            wo.payload.b.eq(decoded_b),
            # pixelCoords: (False ## x).asSInt.resize(nonFraction bits)
            wo.payload.coords.x.eq(current_x),
            wo.payload.coords.y.eq(pixel_y),
            wo.payload.enable_dithering.eq(fbz_mode.enable_dithering),
            wo.payload.dither_algorithm.eq(fbz_mode.dither_algorithm),
            wo.payload.depth_alpha.eq(decoded_depth),
            wo.payload.rgb_write.eq(decoded_rgb_write),
            wo.payload.aux_write.eq(decoded_aux_write),
            wo.payload.routing.eq(regs.write_routing),
        ]
        if c.trace.enabled:
            _origin_pixel_key(m, wo.payload.trace, TraceOrigin.lfb)

        # ------------------------------------------------------------------
        # Pipeline mode: ColorCombine.Output (no dither, no delay)
        # ------------------------------------------------------------------
        po = self.pipeline_output
        m.d.comb += po.valid.eq(write_pixel_active & lfb_mode.pixel_pipeline_enable)
        # buildPipelineOutput(currentX, pixelY)
        m.d.comb += [
            po.payload.coords.x.eq(current_x),
            po.payload.coords.y.eq(pixel_y),
            po.payload.color.r.eq(decoded_r),
            po.payload.color.g.eq(decoded_g),
            po.payload.color.b.eq(decoded_b),
            po.payload.alpha.eq(decoded_alpha),
            # out.depth.raw := (decodedDepth.asUInt.resize(32 bits) |<< 12).asBits
            po.payload.depth.raw.eq(Cat(Const(0, 12), decoded_depth)),
            po.payload.iterated_alpha.eq(decoded_alpha),
            po.payload.raw_w.eq(0),
            po.payload.fog_color.eq(regs.fog_color),
            po.payload.chroma_key.eq(0),
            po.payload.za_color.eq(regs.za_color),
            po.payload.alpha_mode.eq(regs.alpha_mode),
            po.payload.fog_mode.eq(regs.fog_mode),
            po.payload.fbz_mode.eq(regs.fbz_mode),
            po.payload.routing.eq(regs.write_routing),
        ]
        if c.trace.enabled:
            _origin_pixel_key(m, po.payload.trace, TraceOrigin.lfb)

        # Active output fire: bypass fires via downstream Stream handshake,
        # pipeline fires immediately
        bypass_fire = Signal(name="bypassFire")
        pipeline_fire = Signal(name="pipelineFire")
        active_output_fire = Signal(name="activeOutputFire")
        m.d.comb += [
            bypass_fire.eq(fire(wo)),
            pipeline_fire.eq(write_pixel_active & lfb_mode.pixel_pipeline_enable & po.ready),
            active_output_fire.eq(bypass_fire | pipeline_fire),
        ]

        # State transitions on active output fire
        with m.If(active_output_fire):
            with m.If(state == STATE_PIXEL1):
                with m.If(is_dual_pixel & ~is_half_word_write):
                    m.d.sync += state.eq(STATE_PIXEL2)
                with m.Else():
                    m.d.sync += state.eq(STATE_IDLE)
            with m.If(state == STATE_PIXEL2):
                m.d.sync += state.eq(STATE_IDLE)

        # ------------------------------------------------------------------
        # Read path: FB memory reads
        # ------------------------------------------------------------------
        fb_read_cmd_pending = Signal(name="fbReadCmdPending")  # True while waiting for cmd accept
        fb_read_addr = Signal(aw, name="fbReadAddr", reset_less=True)

        fb_cmd = self.fb_read_bus.cmd
        fb_rsp = self.fb_read_bus.rsp
        fb_frag = fb_cmd.payload.fragment
        m.d.comb += [
            fb_cmd.valid.eq(fb_read_cmd_pending),
            fb_frag.address.eq(fb_read_addr),
            fb_frag.opcode.eq(CMD_READ),
            fb_frag.length.eq(3),  # 4 bytes
            fb_frag.source.eq(0),
            fb_frag.data.eq(0),
            fb_frag.mask.eq(0),
            fb_cmd.payload.last.eq(1),
            fb_rsp.ready.eq((state == STATE_RFETCH1) | (state == STATE_RFETCH2)),
        ]

        # On accepting a read command, compute the first FB address but don't
        # issue yet.  The read waits in stateRwait until the FIFO drains and
        # the pipeline is idle.
        cmd_addr = cmd.address
        rx = (cmd_addr >> 1)[0:10]
        ry = (cmd_addr >> 11)[0:10]
        read_base = Mux(lfb_mode.read_buffer_select == 2,
                        regs.read_routing.aux_base_addr,
                        regs.read_routing.color_base_addr)
        plane_addr1 = plane_address(read_base, rx, ry, regs.read_routing.pixel_stride)
        with m.If(bus_cmd_fire & cmd_is_read):
            m.d.sync += [
                captured_read1_lane_hi.eq(plane_addr1[1]),
                fb_read_plane_addr.eq(plane_addr1),
                fb_read_addr.eq(Cat(Const(0, 2), plane_addr1[2:aw])),
            ]

        # Wait for FIFO empty + pipeline idle before issuing FB reads (SST-1 spec)
        with m.If(state == STATE_RWAIT):
            with m.If(self.pci_fifo_empty & ~self.pipeline_busy):
                m.d.sync += [
                    fb_read_cmd_pending.eq(1),
                    state.eq(STATE_RFETCH1),
                ]

        # Read state machine
        plane_addr2 = (fb_read_plane_addr + 2)[0:aw]
        with m.If(state == STATE_RFETCH1):
            with m.If(fire(fb_cmd)):
                m.d.sync += fb_read_cmd_pending.eq(0)
            with m.If(fire(fb_rsp)):
                m.d.sync += [
                    captured_read1.eq(fb_rsp.payload.fragment.data),
                    state.eq(STATE_RFETCH2),
                    captured_read2_lane_hi.eq(plane_addr2[1]),
                    fb_read_plane_addr.eq(plane_addr2),
                    fb_read_addr.eq(Cat(Const(0, 2), plane_addr2[2:aw])),
                    fb_read_cmd_pending.eq(1),
                ]

        with m.If(state == STATE_RFETCH2):
            with m.If(fire(fb_cmd)):
                m.d.sync += fb_read_cmd_pending.eq(0)
            with m.If(fire(fb_rsp)):
                m.d.sync += [
                    captured_read2.eq(fb_rsp.payload.fragment.data),
                    state.eq(STATE_RRESP),
                ]

        # Build read response data from selected plane (always low 16 bits)
        read_data1 = Signal(16, name="readData1")
        read_data2 = Signal(16, name="readData2")
        m.d.comb += [
            read_data1.eq(Mux(captured_read1_lane_hi, captured_read1[16:32], captured_read1[0:16])),
            read_data2.eq(Mux(captured_read2_lane_hi, captured_read2[16:32], captured_read2[0:16])),
        ]

        # Pack: lo16 = pixel(x), hi16 = pixel(x+1)
        read_response_raw = Signal(32, name="readResponseRaw")
        m.d.comb += read_response_raw.eq(Cat(read_data1, read_data2))

        # Apply word swap and byte swizzle to read result
        read_response_data = Signal(32, name="readResponseData")
        m.d.comb += read_response_data.eq(word_swap_and_swizzle(
            read_response_raw, lfb_mode.word_swap_reads, lfb_mode.byte_swizzle_reads))

        # RRESP state: wait for bus response handshake
        with m.If((state == STATE_RRESP) & fire(bus_rsp)):
            m.d.sync += state.eq(STATE_IDLE)

        # ------------------------------------------------------------------
        # BMB response
        # ------------------------------------------------------------------
        captured_source = Signal(len(cmd.source), name="capturedSource", reset_less=True)
        with m.If(bus_cmd_fire):
            m.d.sync += captured_source.eq(cmd.source)

        # For writes: respond immediately (rspPending set on cmd.fire)
        # For reads: respond only when data is ready (stateRresp)
        m.d.comb += [
            bus_rsp.valid.eq(rsp_pending & (~captured_is_read | (state == STATE_RRESP))),
            rsp.source.eq(captured_source),
            rsp.opcode.eq(RSP_SUCCESS),
            bus_rsp.payload.last.eq(1),
            rsp.data.eq(Mux(captured_is_read, read_response_data, 0)),
        ]

        with m.If(fire(bus_rsp)):
            m.d.sync += rsp_pending.eq(0)

        # Busy signal: only write-side activity contributes to pipeline busy.
        m.d.comb += self.busy.eq((state == STATE_PIXEL1) | (state == STATE_PIXEL2))

        return m
