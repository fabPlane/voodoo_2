"""``PixelPipeline`` -- the assembly of the whole ordered draw pipeline.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/core/PixelPipeline.scala`` (SpinalHDL
1.15.0, 714 lines), instantiated by ``Core.scala:110`` as ``PixelPipeline(c)``.
Everything here is *composition*: every arithmetic stage is its own,
individually lockstep-diffed module, and this file only owns the glue --
the arbiter, forks, joins, queues, pipes, merges, the two span prefetchers,
the two chroma/alpha kill filters, the five statistics counters and the
debug/busy fan-out.

Structure map (Scala ``val`` -> here), in the Scala's order.  ``stage()`` is
``m2sPipe()``; ``>/->`` is ``s2mPipe().m2sPipe()``; every ``StreamFork2`` is
``synchronous = true``::

    io.triangleCmd  ----+
    io.ftriangleCmd ----+-- StreamArbiterFactory.assumeOhInput  triangleSetupInput
                            -> triangleSetup -> rasterizer (clip regs from controls)
    io.fastfillCmd ------- fastfillWriter (regs / clip from controls)
    io.lfbBus <> lfb.io.bus ; io.lfbReadBus <> lfb.io.fbReadBus

    rasterizer.o --map(y origin swap)--> rasterYTransformed --stage--> rasterYPipe
      --StreamFork2--> rasterFork._1 --stage--> Tmu.Input.fromRasterizer
      |                 --> tmuInput >/-> tmu.io.input ; tmuOutput << tmu.io.output
      +---------------> rasterFork._2 --PostTmu.fromOutput--> queue(64) --stage--stage
                        --> postTmuQueue
    tmuJoined = StreamJoin(tmuOutput.stage(), postTmuQueue)
      --stage--> ColorCombine.Input.fromTmuAndRaster --> colorCombineInput
      >/-> colorCombine.io.input

    rasterizer.prefetchSpan --StreamFork2--> spanPrefetchFork
      ._1 -> colorPrefetcher (SpanPrefetcher, colorPlane) -> io.prefetchColor
      ._2 -> auxPrefetcher   (SpanPrefetcher, aux plane)  -> io.prefetchAux

    fogArbiterInput = colorCombine.io.output  |  lfb.io.pipelineOutput   (CC wins)
      >/-> fog.io.input ; fogOutput << fog.io.output
    fogOutput >/-> fbAccess.io.input
      fbAccess.io.fbRead{Color,Aux}Rsp << io.{color,aux}ReadRsp
      io.{color,aux}ReadReq << fbAccess.io.fbRead{Color,Aux}Req.s2mPipe()
    afterFbKills = fbAccess.io.output.throwWhen(chromaKill || alphaKill).stage()
    trianglePreDither = afterFbKills --PreDither.fromFramebufferAccess--> .m2sPipe()

    preDitherRaw = trianglePreDither  |  lfb.io.writeOutput   (triangle wins)
    preDitherMerged = preDitherRaw.stage()
      --StreamFork2--> (forDither, forPipe)
      forDither --Dither.Input.fromPreDither--> dither ; ditherOutput << dither.io.output
      preDitherPiped = forPipe.m2sPipe()
    ditherJoined = StreamJoin(ditherOutput, preDitherPiped)
      --StreamFork2--> (forColorWrite, forAuxWrite)
      colorWriteInput = forColorWrite.throwWhen(!rgbWrite) --Write.Input.colorFromDither-->
        >/-> writeColor.i.fromPipeline
      auxWriteInput   = forAuxWrite.throwWhen(!auxWrite)   --Write.Input.auxFromPreDither-->
        >/-> writeAux.i.fromPipeline

    fastfill{Color,Aux}Write = fastfillWriter.io.{color,aux}Write.s2mPipe()
    {color,aux}WriteMerged = fastfill{Color,Aux}Write | write{Color,Aux}.o.fbWrite  (fastfill wins)
    io.{color,aux}Write << {color,aux}WriteMerged

    pixelsIn / chromaFail / zFuncFail / aFuncFail / pixelsOut counters -> io.stats
    io.debug.busy / io.debug.writePath / pipelineBusySignal / pipelineBusySources
    lfb.io.pipelineBusy := pipelineBusySignal || io.fbStatus.colorBusy || auxBusy

Scala -> Python names (the rest is ``camel_to_snake``; sub-Components keep
the Scala ``val`` name as their Amaranth submodule name):

===================================  ===========================================
``PixelPipeline.scala``              here
===================================  ===========================================
``ClipRect`` / ``PaletteRegs``       :class:`ClipRect` / :class:`PaletteRegs`
``SyncRender(c)`` / ``Controls(c)``  :class:`SyncRender` / :class:`Controls`
``Controls.fromRegisterBank``        :func:`controls_from_register_bank`
``ExternalBusy`` / ``.fromCore``     :class:`ExternalBusy` / :func:`external_busy_from_core`
``SpanPrefetcher(c, colorPlane)``    :class:`SpanPrefetcher`
``buildPaletteWriteFlow`` (l. 197)   :func:`build_palette_write_flow` -- **never
                                     called upstream either**; see its docstring
``Tmu.Input.fromRasterizer``         :func:`tmu_input_from_rasterizer`
``ColorCombine.Input.fromTmuAndRaster`` :func:`color_combine_input_from_tmu_and_raster`
``Write.PreDither.fromFramebufferAccess`` :func:`pre_dither_from_framebuffer_access`
``Write.Input.colorFromDither``      :func:`write_input_color_from_dither`
``io.stats`` (``CoreStats``)         ``stats``
``io.debug`` (``CoreDebug``)         ``debug`` (:class:`PixelPipelineDebug`, below)
===================================  ===========================================

Three things the transliteration deliberately does *not* carry over:

1. **Nine ``CoreDebug`` words are not ports here.**  ``CoreDebug`` declares
   ``fbAccess`` and the eight ``fbColorCache*`` / ``fbAuxCache*`` words, and
   nothing in the design ever assigns them (``docs/amaranth/spinal-naming-
   and-harness.md`` §1.7).  A bare Spinal ``PixelPipeline`` top therefore
   does not elaborate; ``GenOne`` wraps it in ``DiffHierWrap``, which drops
   exactly those nine outputs and re-exports the rest -- ``pipelineBusy``,
   ``busy``, ``writePath`` and (with the working tree's one-line local edit
   ``io.debug.pipelineBusySources := pipelineBusySources``)
   ``pipelineBusySources``.  ``debug`` is :class:`PixelPipelineDebug`, that
   surviving subset; ``Core`` fills the nine words of ``RegisterBank``'s
   full ``CoreDebug`` with zero.
2. ``chromaKill`` (Scala l. 373, on ``colorCombine.io.output``) and
   ``alphaKill`` (l. 443, on ``fogOutput``) are computed upstream and never
   read -- only the ``fbAccess*`` pair after ``FramebufferAccess`` feeds the
   ``throwWhen``.  Spinal prunes them; they are not written out here.
3. ``buildPaletteWriteFlow`` is a ``private def`` no line of the Component
   calls, so ``io.controls.paletteRegs`` is eight 32-bit inputs that reach
   nothing.  The function is ported (it is the documented route for
   register-driven palette loads) but, as upstream, not instantiated.

Registers: the five statistics counters are ``Reg(...) init(0)``; every other
register is inside the ``lib/stream.py`` helpers (``M2SPipe.rValid`` /
``S2MPipe.rValidN`` / FIFO pointers reset, payload registers ``reset_less``)
or inside a sub-Component, which keeps its own rules.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, Value, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (
    ALPHA_MODE_LAYOUT,
    FOG_MODE_LAYOUT,
    CoreBusyMap,
    CoreStats,
    CoreWritePathMap,
    FbRouting,
    FbzMode,
    FramebufferMemStats,
    FramebufferMemStatus,
    FramebufferPrefetchReq,
    FramebufferReadReq,
    FramebufferReadRsp,
    FramebufferWriteReq,
    LfbMode,
    LfbRegs,
    PaletteWrite,
    plane_address,
    triangle_setup_input_layout,
)
from avoodoo.core.membus_params import lfb_fb_read_params, lfb_params, tmu_bmb_params
from avoodoo.frontend.lfb import Lfb
from avoodoo.lib import membus
from avoodoo.lib.membus import MemBusSignature
from avoodoo.lib.stream import (
    Flow,
    FlowSignature,
    Stream,
    arbiter_on,
    arbitration_from,
    connect_stream,
    fire,
    fork2,
    join,
    m2s_pipe,
    queue,
    s2m_pipe,
    stage,
    throw_when,
    translate_with,
)
from avoodoo.pixel.color_combine import ColorCombine, ColorCombineConfig, ColorCombineInput
from avoodoo.pixel.dither import INPUT_LAYOUT as DITHER_INPUT_LAYOUT
from avoodoo.pixel.dither import OUTPUT_LAYOUT as DITHER_OUTPUT_LAYOUT
from avoodoo.pixel.dither import Dither, input_from_pre_dither
from avoodoo.pixel.fog import Fog, FogOutput
from avoodoo.pixel.framebuffer_access import FramebufferAccess
from avoodoo.pixel.write import Write, WriteInput, WritePreDither, aux_from_pre_dither
from avoodoo.raster.fastfill_word_writer import FastfillWordWriter
from avoodoo.raster.fastfill_write import FastfillWriteRegs
from avoodoo.raster.rasterizer import (
    Rasterizer,
    output_layout as rasterizer_output_layout,
    post_tmu_from_output,
    post_tmu_layout,
    prefetch_span_layout,
)
from avoodoo.raster.triangle_setup import TriangleSetup
from avoodoo.texture.tmu import Tmu
from avoodoo.texture.tmu_types import NccTables, PaletteWrite as TmuPaletteWrite, TmuInput, TmuOutput


__all__ = [
    "ClipRect",
    "PaletteRegs",
    "SyncRender",
    "Controls",
    "ExternalBusy",
    "PixelPipelineDebug",
    "controls_from_register_bank",
    "external_busy_from_core",
    "lfb_regs_from_register_bank",
    "fastfill_regs_from_register_bank",
    "SpanPrefetcher",
    "build_palette_write_flow",
    "tmu_input_from_rasterizer",
    "color_combine_input_from_tmu_and_raster",
    "pre_dither_from_framebuffer_access",
    "write_input_color_from_dither",
    "PixelPipeline",
]


# ---------------------------------------------------------------------------
# object PixelPipeline -- the bundles
# ---------------------------------------------------------------------------


class ClipRect(data.StructLayout):
    """``case class ClipRect()`` -- four 10-bit clip edges."""

    def __init__(self) -> None:
        super().__init__({
            "left": unsigned(10),
            "right": unsigned(10),
            "low_y": unsigned(10),
            "high_y": unsigned(10),
        })


class PaletteRegs(data.StructLayout):
    """``case class PaletteRegs()`` -- the eight NCC table-0 I/Q registers.

    ``table0I0`` -> ``table0_i0`` (``camel_to_snake`` puts the boundary
    before the capital only).
    """

    FIELDS = ("table0_i0", "table0_i1", "table0_i2", "table0_i3",
              "table0_q0", "table0_q1", "table0_q2", "table0_q3")

    def __init__(self) -> None:
        super().__init__({name: unsigned(32) for name in self.FIELDS})


class SyncRender(data.StructLayout):
    """``case class SyncRender(c)`` -- the register-bank view the pixel path
    samples when a pixel enters ``ColorCombine``."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "fbz_mode": FbzMode(),
            "color0": unsigned(32),
            "color1": unsigned(32),
            "fog_color": unsigned(32),
            "chroma_key": unsigned(32),
            "za_color": unsigned(32),
            "routing": FbRouting(c),
            "tmu_send_config": unsigned(1),
        })


class Controls(data.StructLayout):
    """``case class Controls(c)`` -- ``io.controls``, everything the pipeline
    reads from the register bank."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "clip": ClipRect(),
            "lfb": LfbRegs(c),
            "fastfill": FastfillWriteRegs(c),
            "sync_render": SyncRender(c),
            "ncc_tables": NccTables(),
            "fog_table": data.ArrayLayout(unsigned(16), 64),
            "palette_regs": PaletteRegs(),
        })


class ExternalBusy(data.StructLayout):
    """``case class ExternalBusy()`` -- the ``Core``-level busy sources folded
    into ``pipelineBusy``."""

    def __init__(self) -> None:
        super().__init__({
            "nop_cmd": unsigned(1),
            "nop_cmd_reset": unsigned(1),
            "fastfill_cmd": unsigned(1),
            "swapbuffer_cmd": unsigned(1),
            "swap_waiting": unsigned(1),
        })


class PixelPipelineDebug(data.StructLayout):
    """``CoreDebug`` as ``PixelPipeline`` actually drives it.

    The four members the Scala assigns (``pipelineBusy``, ``busy``,
    ``writePath``, ``pipelineBusySources``), in ``CoreDebug``'s declaration
    order; the nine never-assigned words (``fbAccess``, ``fb{Color,Aux}Cache``
    ``{,Req,Expected,Occupancy}``) are left out -- see the module docstring.
    """

    def __init__(self) -> None:
        super().__init__({
            "pipeline_busy": unsigned(1),
            "busy": CoreBusyMap(),
            "write_path": CoreWritePathMap(),
            "pipeline_busy_sources": unsigned(32),
        })


# ---------------------------------------------------------------------------
# Controls.fromRegisterBank / ExternalBusy.fromCore and the two Regs builders
# they call (Lfb.Regs.fromRegisterBank, FastfillWrite.Regs.fromRegisterBank)
# ---------------------------------------------------------------------------


def _copy_fields(dst, src, layout: data.StructLayout):
    """``b.x := reg.x`` for every member of ``layout`` -- the ``fbzModeBundle`` /
    ``lfbModeBundle`` idiom (``RegisterBank.scala:908/931``): the register
    view carries a trailing ``reserved`` field the bundle does not."""
    return [getattr(dst, name).eq(getattr(src, name)) for name in layout.members]


def _resize_u(v, width: int):
    """``UInt.resize(width)``: zero-extend or truncate."""
    v = Value.cast(v).as_unsigned()
    return v[:width] if len(v) >= width else Cat(v, Const(0, width - len(v)))


def lfb_regs_from_register_bank(m: Module, c: Config, reg_bank, layout, dst=None, *,
                                name: str = "lfbRegs"):
    """``Lfb.Regs.fromRegisterBank(c, regBank, layout)`` (``frontend/Lfb.scala:460``).

    Drives ``dst`` (a :class:`~avoodoo.core.core_types.LfbRegs` view; a fresh
    ``Signal`` when ``None``) combinationally and returns it.  ``reg_bank`` is
    :class:`~avoodoo.frontend.register_bank.RegisterBank` (or anything with
    ``render_config.{lfb_mode,fbz_mode,alpha_mode,fog_mode,fog_color,
    za_color}``) and ``layout`` a
    :class:`~avoodoo.core.core_types.FramebufferLayout` view.
    """
    if dst is None:
        dst = Signal(LfbRegs(c), name=name)
    rc = reg_bank.render_config
    m.d.comb += [
        *_copy_fields(dst.lfb_mode, rc.lfb_mode, LfbMode()),      # lfbModeBundle
        *_copy_fields(dst.fbz_mode, rc.fbz_mode, FbzMode()),      # fbzModeBundle
        # alphaModeBundle: b.assignFromBits(alphaMode); fogModeBundle: (fogMode.resized)
        dst.alpha_mode.as_value().eq(Value.cast(rc.alpha_mode)[:ALPHA_MODE_LAYOUT.size]),
        dst.fog_mode.as_value().eq(Value.cast(rc.fog_mode)[:FOG_MODE_LAYOUT.size]),
        dst.fog_color.eq(rc.fog_color),
        dst.za_color.eq(rc.za_color),
        dst.write_routing.eq(layout.lfb_write),
        dst.read_routing.eq(layout.lfb_read),
    ]
    return dst


def fastfill_regs_from_register_bank(m: Module, c: Config, reg_bank, routing, dst=None, *,
                                     name: str = "fastfillRegs"):
    """``FastfillWrite.Regs.fromRegisterBank(c, regBank, routing)``
    (``raster/FastfillWrite.scala:71``); ``routing`` is ``layout.draw``."""
    if dst is None:
        dst = Signal(FastfillWriteRegs(c), name=name)
    rc = reg_bank.render_config
    m.d.comb += [
        dst.color1.eq(rc.color1),
        dst.za_color.eq(rc.za_color),
        *_copy_fields(dst.fbz_mode, rc.fbz_mode, FbzMode()),
        dst.routing.eq(routing),
        dst.y_origin_swap_value.eq(reg_bank.init.fbi_init3.y_origin_swap),
    ]
    return dst


def controls_from_register_bank(m: Module, c: Config, reg_bank, layout, *,
                                name: str = "controls"):
    """``PixelPipeline.Controls.fromRegisterBank(c, regBank, layout)`` (l. 52).

    Returns a ``Signal`` of :class:`Controls` driven in ``m.d.comb``.
    ``reg_bank`` is the :class:`~avoodoo.frontend.register_bank.RegisterBank`
    (``render_config.*``, ``tmu_config.trex_init1``, ``ncc_table.table<N>_<k>``,
    ``fog_table[64]``, ``init.fbi_init3``), ``layout`` a
    :class:`~avoodoo.core.core_types.FramebufferLayout` view.
    """
    ctrl = Signal(Controls(c), name=name)
    rc = reg_bank.render_config
    m.d.comb += [
        ctrl.clip.left.eq(rc.clip_left_x),
        ctrl.clip.right.eq(rc.clip_right_x),
        ctrl.clip.low_y.eq(rc.clip_low_y),
        ctrl.clip.high_y.eq(rc.clip_high_y),
    ]
    lfb_regs_from_register_bank(m, c, reg_bank, layout, ctrl.lfb)
    fastfill_regs_from_register_bank(m, c, reg_bank, layout.draw, ctrl.fastfill)
    sync = ctrl.sync_render
    m.d.comb += [
        *_copy_fields(sync.fbz_mode, rc.fbz_mode, FbzMode()),
        sync.color0.eq(rc.color0),
        sync.color1.eq(rc.color1),
        sync.fog_color.eq(rc.fog_color),
        sync.chroma_key.eq(rc.chroma_key),
        sync.za_color.eq(rc.za_color),
        sync.routing.eq(layout.draw),
        # regBank.tmuConfig.trexInit1(18)
        sync.tmu_send_config.eq(Value.cast(reg_bank.tmu_config.trex_init1)[18]),
    ]
    ncc = reg_bank.ncc_table
    for table in (0, 1):
        y_regs = ncc.table0_y if table == 0 else ncc.table1_y
        i_regs = ncc.table0_i if table == 0 else ncc.table1_i
        q_regs = ncc.table0_q if table == 0 else ncc.table1_q
        dst = ctrl.ncc_tables.table0 if table == 0 else ctrl.ncc_tables.table1
        for r in range(4):
            for b in range(4):
                # dst.y(r * 4 + b) := yRegs(r)((b + 1) * 8 - 1 downto b * 8).asUInt
                m.d.comb += dst.y[r * 4 + b].eq(Value.cast(y_regs[r])[b * 8:(b + 1) * 8])
        for idx in range(4):
            m.d.comb += [
                dst.i[idx].eq(Value.cast(i_regs[idx])[0:27]),
                dst.q[idx].eq(Value.cast(q_regs[idx])[0:27]),
            ]
    # for ((entry, (dfog, fog)) <- ctrl.fogTable.zip(regBank.fogTable.fogTable))
    #   entry(15 downto 8) := dfog ; entry(7 downto 0) := fog
    for k in range(64):
        e = reg_bank.fog_table[k]
        m.d.comb += ctrl.fog_table[k].eq(Cat(e.fog, e.dfog))
    # regBank.nccTable.table0I0 is table0I(0), ... table0Q3 is table0Q(3)
    for k in range(4):
        m.d.comb += [
            getattr(ctrl.palette_regs, f"table0_i{k}").eq(ncc.table0_i[k]),
            getattr(ctrl.palette_regs, f"table0_q{k}").eq(ncc.table0_q[k]),
        ]
    return ctrl


def external_busy_from_core(m: Module, reg_bank, swap_buffer, *, name: str = "externalBusy"):
    """``PixelPipeline.ExternalBusy.fromCore(regBank, swapBuffer)`` (l. 108).

    ``reg_bank.commands.{nop,fastfill,swapbuffer}_cmd_stream`` are the
    ``master(...)`` command streams (``regBank.commands.nopCmd`` and friends),
    ``swap_buffer.waiting`` is ``swapBuffer.io.waiting``.
    """
    busy = Signal(ExternalBusy(), name=name)
    cmds = reg_bank.commands
    nop = cmds.nop_cmd_stream
    m.d.comb += [
        busy.nop_cmd.eq(nop.valid),
        busy.nop_cmd_reset.eq(nop.valid & Value.cast(nop.payload)[0]),
        busy.fastfill_cmd.eq(cmds.fastfill_cmd_stream.valid),
        busy.swapbuffer_cmd.eq(cmds.swapbuffer_cmd_stream.valid),
        busy.swap_waiting.eq(swap_buffer.waiting),
    ]
    return busy


# ---------------------------------------------------------------------------
# case class SpanPrefetcher(c, colorPlane) extends Component
# ---------------------------------------------------------------------------


class SpanPrefetcher(wiring.Component):
    """``PixelPipeline.SpanPrefetcher(c, colorPlane)`` (l. 120).

    Turns each rasterizer span into one inclusive byte-address prefetch
    request for the plane reader, when that plane will be read back for the
    span's pixels (colour: alpha blend or dither subtract; aux: depth
    buffer).  Purely combinational.

    Ports: ``span`` (``slave Stream(Rasterizer.PrefetchSpan(c))``),
    ``read_req`` (``master Stream(FramebufferPlaneReader.PrefetchReq(c))``),
    ``routing``, ``fbz_mode``, ``y_origin_enable``, ``y_origin_swap_value``
    (``UInt(vertexFormat.nonFraction bits)``).
    """

    def __init__(self, c: Config | None = None, *, color_plane: bool = True) -> None:
        c = Config.voodoo1() if c is None else c
        self.c = c
        self.color_plane = bool(color_plane)
        super().__init__({
            "span": In(stream.Signature(prefetch_span_layout(c))),
            "read_req": Out(stream.Signature(FramebufferPrefetchReq(c))),
            "routing": In(FbRouting(c)),
            "fbz_mode": In(FbzMode()),
            "y_origin_enable": In(1),
            "y_origin_swap_value": In(c.vertex_format.non_fraction),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        nf = c.vertex_format.non_fraction
        aw = c.address_width
        span = self.span.payload

        def pixel_x(value):
            """``value.floor(0).asUInt.resize(nonFraction bits)``.

            ``AFix.asUInt`` on a signed value **clamps negatives to zero**
            (``AFix.scala:1203``; :meth:`AFixValue.as_unsigned`), so a span
            left of x = 0 or above y = 0 addresses column / row 0 -- not the
            two's-complement reinterpretation ``coords(0).asUInt`` does in
            ``Write`` on a plain ``SInt``.
            """
            return _resize_u(value.floor(0).as_unsigned(), nf)

        span_y = Signal(nf, name="spanY")
        transformed_y = Signal(nf, name="transformedY")
        m.d.comb += [
            span_y.eq(pixel_x(span.y)),
            transformed_y.eq(span_y),
        ]
        with m.If(self.y_origin_enable):
            # io.yOriginSwapValue.resize(nonFraction bits) - spanY   (UInt, wrapping)
            m.d.comb += transformed_y.eq((_resize_u(self.y_origin_swap_value, nf) - span_y)[:nf])

        plane_base = self.routing.color_base_addr if self.color_plane else self.routing.aux_base_addr
        start_address = Signal(aw, name="startAddress")
        end_address = Signal(aw, name="endAddress")
        m.d.comb += [
            start_address.eq(plane_address(plane_base, pixel_x(span.x_start), transformed_y,
                                           self.routing.pixel_stride)),
            end_address.eq(plane_address(plane_base, pixel_x(span.x_end), transformed_y,
                                         self.routing.pixel_stride)),
        ]

        plane_read_needed = Signal(name="planeReadNeeded")
        if self.color_plane:
            m.d.comb += plane_read_needed.eq(
                span.pixel_config.alpha_mode.alpha_blend_enable
                | self.fbz_mode.enable_dither_subtract)
        else:
            m.d.comb += plane_read_needed.eq(self.fbz_mode.enable_depth_buffer)

        m.d.comb += [
            self.read_req.valid.eq(self.span.valid & plane_read_needed),
            self.span.ready.eq(~plane_read_needed | self.read_req.ready),
            self.read_req.payload.start_address.eq(start_address[:aw]),
            self.read_req.payload.end_address.eq(end_address[:aw]),
        ]
        return m


# ---------------------------------------------------------------------------
# the payload builders the assembly calls (companion-object `def`s upstream)
# ---------------------------------------------------------------------------


def build_palette_write_flow(m: Module, c: Config, regs, *, name: str = "paletteWriteFlow"):
    """``private def buildPaletteWriteFlow(regs: PaletteRegs): Flow[Tmu.PaletteWrite]`` (l. 197).

    **Dead code upstream**: the Component never calls it, so ``PixelPipeline``
    has no register-driven palette path -- ``io.paletteWrite`` (from
    ``RegisterBank``'s own YIQ decode) is the only one.  Ported so the
    behaviour is on record; :class:`PixelPipeline` does not instantiate it,
    and doing so would add eight 32-bit ``RegNext`` registers the Spinal
    Verilog does not have.

    Each of the eight registers is compared with its previous value; a change
    with bit 31 set emits one flow beat: ``address = (reg[30:23] & 0xfe) | (1
    if odd)``, ``data = reg[23:0]``.  Later entries override earlier ones in
    the same cycle (``when`` order), as in the Scala.
    """
    flow = Flow(TmuPaletteWrite(), path=(name,))
    m.d.comb += [flow.valid.eq(0), flow.payload.address.eq(0), flow.payload.data.eq(0)]
    entries = [(getattr(regs, f), f.endswith(("1", "3"))) for f in PaletteRegs.FIELDS]
    for reg, is_odd in entries:
        reg = Value.cast(reg)
        prev = Signal(32, name=f"{name}_prev", reset_less=True)
        m.d.sync += prev.eq(reg)
        with m.If((reg != prev) & reg[31]):
            idx = reg[23:31] & 0xFE
            m.d.comb += [
                flow.valid.eq(1),
                flow.payload.address.eq((idx | 1) if is_odd else idx),
                flow.payload.data.eq(reg[0:24]),
            ]
    return flow


def tmu_input_from_rasterizer(m: Module, c: Config, out, src):
    """``Tmu.Input.fromRasterizer(c, in)`` (``texture/Tmu.scala:1136``):
    drive ``out`` (a :class:`~avoodoo.texture.tmu_types.TmuInput` view) from
    a ``Rasterizer.Output`` view."""
    m.d.comb += [
        out.s.eq(src.grads.s_grad),
        out.t.eq(src.grads.t_grad),
        out.w.eq(src.grads.w_grad),
        out.c_other.r.eq(0),
        out.c_other.g.eq(0),
        out.c_other.b.eq(0),
        out.a_other.eq(0),
        out.config.texture_mode.eq(src.tmu_config.texture_mode),
        out.config.tex_base_addr.eq(src.tmu_config.tex_base_addr),
        out.config.tex_base_addr1.eq(src.tmu_config.tex_base_addr1),
        out.config.tex_base_addr2.eq(src.tmu_config.tex_base_addr2),
        out.config.tex_base_addr38.eq(src.tmu_config.tex_base_addr38),
        out.config.t_lod.eq(src.tmu_config.t_lod),
        out.config.texture_enable.eq(src.tmu_config.texture_enable),
        out.d_sd_x.eq(src.tmu_config.d_sd_x),
        out.d_td_x.eq(src.tmu_config.d_td_x),
        out.d_sd_y.eq(src.tmu_config.d_sd_y),
        out.d_td_y.eq(src.tmu_config.d_td_y),
    ]
    if c.packed_tex_layout:
        m.d.comb += out.config.tex_tables.eq(src.tmu_config.tex_tables)
    if c.trace.enabled:
        m.d.comb += out.trace.eq(src.trace)


def _u8_from_sat(v):
    """``private def u8FromSat(v: AFix): UInt = (v.asBits >> 12).asUInt.resize(8 bits)``."""
    return _resize_u(v.raw.as_unsigned() >> 12, 8)


def color_combine_input_from_tmu_and_raster(m: Module, c: Config, out, tmu_out, raster_out, sync):
    """``ColorCombine.Input.fromTmuAndRaster(c, tmuOut, rasterOut, sync)``
    (``pixel/ColorCombine.scala:98``): ``out`` is a
    :class:`~avoodoo.pixel.color_combine.ColorCombineInput` view, ``tmu_out``
    a ``Tmu.Output``, ``raster_out`` a ``Rasterizer.PostTmu`` and ``sync`` a
    :class:`SyncRender` view."""
    grads = raster_out.grads
    red_sat = grads.red_grad.sat(sat_max=255, sat_min=0, exp=0)
    green_sat = grads.green_grad.sat(sat_max=255, sat_min=0, exp=0)
    blue_sat = grads.blue_grad.sat(sat_max=255, sat_min=0, exp=0)
    alpha_sat = raster_out.alpha_grad_hi.sat(sat_max=255, sat_min=0, exp=0)
    depth_sat = grads.depth_grad.sat(sat_max=255, sat_min=0, exp=12)
    color0 = Value.cast(sync.color0)
    color1 = Value.cast(sync.color1)
    m.d.comb += [
        # out.coords.assignFromVec(rasterOut.coords)
        out.coords.x.eq(raster_out.coords[0]),
        out.coords.y.eq(raster_out.coords[1]),
        out.iterated.r.eq(_u8_from_sat(red_sat)),
        out.iterated.g.eq(_u8_from_sat(green_sat)),
        out.iterated.b.eq(_u8_from_sat(blue_sat)),
        # (alphaSat.asBits >> 30).asUInt.resize(8 bits)
        out.iterated_alpha.eq(_resize_u(alpha_sat.raw.as_unsigned() >> 30, 8)),
        out.iterated_z.eq(_u8_from_sat(depth_sat)),
        out.depth.eq(grads.depth_grad),
        # rasterOut.grads.wGrad.asSInt.resize(32 bits)
        out.raw_w.eq(grads.w_grad.raw.as_signed()[:32]),
        out.texture.eq(tmu_out.texture),
        out.texture_alpha.eq(tmu_out.texture_alpha),
        # assignRgb888(out.color0, sync.color0); color0Alpha := color0(31 downto 24)
        out.color0.r.eq(color0[16:24]),
        out.color0.g.eq(color0[8:16]),
        out.color0.b.eq(color0[0:8]),
        out.color0_alpha.eq(color0[24:32]),
        out.color1.r.eq(color1[16:24]),
        out.color1.g.eq(color1[8:16]),
        out.color1.b.eq(color1[0:8]),
        out.color1_alpha.eq(color1[24:32]),
        out.fog_color.eq(sync.fog_color),
        out.chroma_key.eq(sync.chroma_key),
        out.za_color.eq(sync.za_color),
        # out.config := Config.fromFbzColorPath(rasterOut.config.fbzColorPath)
        *ColorCombineConfig.from_fbz_color_path(out.config, raster_out.config.fbz_color_path),
        out.alpha_mode.eq(raster_out.config.alpha_mode),
        out.fog_mode.eq(raster_out.config.fog_mode),
        out.fbz_mode.eq(sync.fbz_mode),
        out.routing.eq(sync.routing),
    ]
    if c.trace.enabled:
        m.d.comb += out.trace.eq(raster_out.trace)


def pre_dither_from_framebuffer_access(m: Module, c: Config, out, fb_in):
    """``Write.PreDither.fromFramebufferAccess(c, fbIn)`` (``pixel/Write.scala:92``):
    ``out`` is a :class:`~avoodoo.pixel.write.WritePreDither` view, ``fb_in``
    a ``FramebufferAccess.Output`` view."""
    m.d.comb += [
        out.r.eq(fb_in.color.r),
        out.g.eq(fb_in.color.g),
        out.b.eq(fb_in.color.b),
        out.coords.eq(fb_in.coords),
        out.enable_dithering.eq(fb_in.enable_dithering),
        out.dither_algorithm.eq(fb_in.dither_algorithm),
        # (fbIn.enableAlphaPlanes ? fbIn.alpha.resize(16 bits) | fbIn.newDepth).asBits
        out.depth_alpha.eq(Mux(fb_in.enable_alpha_planes,
                               _resize_u(fb_in.alpha, 16), fb_in.new_depth)),
        out.rgb_write.eq(fb_in.rgb_write),
        out.aux_write.eq(fb_in.aux_write),
        out.routing.eq(fb_in.routing),
    ]
    if c.trace.enabled:
        m.d.comb += out.trace.eq(fb_in.trace)


def write_input_color_from_dither(m: Module, c: Config, out, pd, dit_out, trace=None):
    """``Write.Input.colorFromDither(c, pd, ditOut, trace)`` (``pixel/Write.scala:43``):
    ``out`` is a :class:`~avoodoo.pixel.write.WriteInput` view."""
    m.d.comb += [
        out.coords.eq(pd.coords),
        # (ditOut.ditR ## ditOut.ditG ## ditOut.ditB).asBits -- R in the high bits
        out.data.eq(Cat(dit_out.dit_b, dit_out.dit_g, dit_out.dit_r)),
        out.fb_base_addr.eq(pd.routing.color_base_addr),
        out.fb_pixel_stride.eq(pd.routing.pixel_stride),
    ]
    if c.trace.enabled and trace is not None:
        m.d.comb += out.trace.eq(trace)


def _alpha_passed(m: Module, alpha_func, src_alpha, alpha_ref, *, name: str):
    """The eight-way ``alphaFunc.mux(...)`` (l. 432 / l. 462)."""
    passed = Signal(name=name)
    with m.Switch(alpha_func):
        with m.Case(0):
            m.d.comb += passed.eq(0)
        with m.Case(1):
            m.d.comb += passed.eq(src_alpha < alpha_ref)
        with m.Case(2):
            m.d.comb += passed.eq(src_alpha == alpha_ref)
        with m.Case(3):
            m.d.comb += passed.eq(src_alpha <= alpha_ref)
        with m.Case(4):
            m.d.comb += passed.eq(src_alpha > alpha_ref)
        with m.Case(5):
            m.d.comb += passed.eq(src_alpha != alpha_ref)
        with m.Case(6):
            m.d.comb += passed.eq(src_alpha >= alpha_ref)
        with m.Case(7):
            m.d.comb += passed.eq(1)
    return passed


def _pipe_s2m_m2s(m: Module, source, *, name: str):
    """``source >/-> sink`` -- ``sink << source.s2mPipe().m2sPipe()``."""
    return m2s_pipe(m, s2m_pipe(m, source, name=f"{name}_s2mPipe"), name=f"{name}_m2sPipe")


def _merge_first_wins(m: Module, first, second, *, name: str):
    """The three hand-written two-way merges (``fogArbiterInput``,
    ``preDitherRaw``, ``colorWriteMerged`` / ``auxWriteMerged``)::

        out.valid := first.valid || second.valid
        out.payload := second.payload ; when(first.valid) { out.payload := first.payload }
        first.ready := out.ready
        second.ready := out.ready && !first.valid
    """
    out = Stream(first.payload.shape(), path=(name,))
    m.d.comb += [
        out.valid.eq(first.valid | second.valid),
        out.payload.eq(second.payload),
    ]
    with m.If(first.valid):
        m.d.comb += out.payload.eq(first.payload)
    m.d.comb += [
        first.ready.eq(out.ready),
        second.ready.eq(out.ready & ~first.valid),
    ]
    return out


# ---------------------------------------------------------------------------
# case class PixelPipeline(c: Config) extends Component
# ---------------------------------------------------------------------------


class PixelPipeline(wiring.Component):
    """``case class PixelPipeline(c: Config) extends Component``.

    Ports (Spinal names, ``camel_to_snake``'d; every payload is one packed
    ``data.Layout``):

    =====================  =====================================================
    ``triangle_cmd``       ``slave Stream(TriangleSetup.Input(c))``
    ``ftriangle_cmd``      idem, the float-shadow path; one-hot with the above
    ``fastfill_cmd``       ``slave Stream(NoData)``
    ``palette_write``      ``slave Flow(RegisterBank.PaletteWrite())``
    ``lfb_bus``            ``slave(Bmb(Lfb.bmbParams(c)))``
    ``controls``           ``in(Controls(c))``
    ``external_busy``      ``in(ExternalBusy())``
    ``tmu_invalidate``, ``pci_fifo_empty``   ``in Bool``
    ``fb_status``          ``in(FramebufferMemStatus())``
    ``fb_stats``           ``in(FramebufferMemStats())``
    ``tex_read``           ``master(Bmb(Tmu.bmbParams(c)))``
    ``lfb_read_bus``       ``master(Bmb(Lfb.fbReadBmbParams(c)))``
    ``color_read_req`` / ``aux_read_req``    ``master Stream(ReadReq(c))``
    ``color_read_rsp`` / ``aux_read_rsp``    ``slave Stream(ReadRsp())``
    ``prefetch_color`` / ``prefetch_aux``    ``master Stream(PrefetchReq(c))``
    ``color_write`` / ``aux_write``          ``master Stream(WriteReq(c))``
    ``stats``              ``out(CoreStats())``
    ``debug``              ``out(CoreDebug())`` minus the nine undriven words
    =====================  =====================================================
    """

    def __init__(self, c: Config | None = None) -> None:
        c = Config.voodoo1() if c is None else c
        self.c = c
        tri = triangle_setup_input_layout(c)
        super().__init__({
            "triangle_cmd": In(stream.Signature(tri)),
            "ftriangle_cmd": In(stream.Signature(tri)),
            "fastfill_cmd": In(stream.Signature(unsigned(0))),
            "palette_write": In(FlowSignature(PaletteWrite())),
            "lfb_bus": In(MemBusSignature(lfb_params(c))),
            "controls": In(Controls(c)),
            "external_busy": In(ExternalBusy()),
            "tmu_invalidate": In(1),
            "pci_fifo_empty": In(1),
            "fb_status": In(FramebufferMemStatus()),
            "fb_stats": In(FramebufferMemStats()),
            "tex_read": Out(MemBusSignature(tmu_bmb_params(c))),
            "lfb_read_bus": Out(MemBusSignature(lfb_fb_read_params(c))),
            "color_read_req": Out(stream.Signature(FramebufferReadReq(c))),
            "color_read_rsp": In(stream.Signature(FramebufferReadRsp())),
            "aux_read_req": Out(stream.Signature(FramebufferReadReq(c))),
            "aux_read_rsp": In(stream.Signature(FramebufferReadRsp())),
            "prefetch_color": Out(stream.Signature(FramebufferPrefetchReq(c))),
            "prefetch_aux": Out(stream.Signature(FramebufferPrefetchReq(c))),
            "color_write": Out(stream.Signature(FramebufferWriteReq(c))),
            "aux_write": Out(stream.Signature(FramebufferWriteReq(c))),
            "stats": Out(CoreStats()),
            "debug": Out(PixelPipelineDebug()),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        nf = c.vertex_format.non_fraction
        controls = self.controls

        m.submodules.triangleSetup = triangle_setup = TriangleSetup(c)
        m.submodules.rasterizer = rasterizer = Rasterizer(c)
        m.submodules.fastfillWriter = fastfill_writer = FastfillWordWriter(c)
        m.submodules.lfb = lfb = Lfb(c)
        m.submodules.colorCombine = color_combine = ColorCombine(c)
        m.submodules.fbAccess = fb_access = FramebufferAccess(c)
        m.submodules.writeColor = write_color = Write(c)
        m.submodules.writeAux = write_aux = Write(c)

        # val triangleSetupInput = StreamArbiterFactory.assumeOhInput
        #   .on(Seq(io.triangleCmd, io.ftriangleCmd))
        triangle_setup_input = arbiter_on(
            m, [self.triangle_cmd, self.ftriangle_cmd],
            arbitration="assumeOhInput", name="triangleSetupInput")
        connect_stream(m, triangle_setup.i, triangle_setup_input)

        connect_stream(m, rasterizer.i, triangle_setup.o)
        m.d.comb += [
            rasterizer.enable_clipping.eq(controls.fastfill.fbz_mode.enable_clipping),
            rasterizer.clip_left.eq(controls.clip.left),
            rasterizer.clip_right.eq(controls.clip.right),
            rasterizer.clip_low_y.eq(controls.clip.low_y),
            rasterizer.clip_high_y.eq(controls.clip.high_y),
        ]

        connect_stream(m, fastfill_writer.cmd, self.fastfill_cmd)
        m.d.comb += [
            fastfill_writer.regs.eq(controls.fastfill),
            fastfill_writer.clip_left.eq(controls.clip.left),
            fastfill_writer.clip_right.eq(controls.clip.right),
            fastfill_writer.clip_low_y.eq(controls.clip.low_y),
            fastfill_writer.clip_high_y.eq(controls.clip.high_y),
        ]

        # lfb.io.bus <> io.lfbBus ; io.lfbReadBus <> lfb.io.fbReadBus
        membus.connect(m, lfb.bus, self.lfb_bus)
        m.d.comb += [
            lfb.regs.eq(controls.lfb),
            lfb.pci_fifo_empty.eq(self.pci_fifo_empty),
        ]
        membus.connect(m, self.lfb_read_bus, lfb.fb_read_bus)

        # ------------------------------------------------------------------
        # y-origin swap on the rasterizer output, then fork to TMU / post-TMU
        # ------------------------------------------------------------------
        y_origin_enable = controls.fastfill.fbz_mode.y_origin
        y_origin_swap_value = controls.fastfill.y_origin_swap_value

        # val rasterYTransformed = rasterizer.o.map { out => ... }
        raster_y_transformed = Stream(rasterizer_output_layout(c), path=("rasterYTransformed",))
        arbitration_from(m, raster_y_transformed, rasterizer.o)
        m.d.comb += raster_y_transformed.payload.eq(rasterizer.o.payload)
        with m.If(y_origin_enable):
            # yOriginSwapValue.resize(nonFraction bits).asSInt - out.coords(1)
            m.d.comb += raster_y_transformed.payload.coords[1].eq(
                (_resize_u(y_origin_swap_value, nf).as_signed()
                 - rasterizer.o.payload.coords[1])[:nf].as_signed())

        raster_y_pipe = stage(m, raster_y_transformed, name="rasterYPipe")
        raster_fork_1, raster_fork_2 = fork2(m, raster_y_pipe, synchronous=True,
                                             name="rasterFork")

        # val postTmuQueue = rasterFork._2.translateWith(PostTmu.fromOutput(...))
        #   .queue(64).stage().stage()
        post_tmu = Signal(post_tmu_layout(c), name="postTmu")
        post_tmu_from_output(m, c, post_tmu, raster_fork_2.payload)
        post_tmu_queue = stage(
            m, stage(m, queue(m, translate_with(m, raster_fork_2, post_tmu), 64,
                              name="postTmuQueue_fifo"),
                     name="postTmuQueue_stage1"),
            name="postTmuQueue_stage2")

        tmu_output = Stream(TmuOutput(c), path=("tmuOutput",))
        tmu_input_valid = Signal(name="tmuInputValid")
        tmu_busy = Signal(name="tmuBusy")

        if c.enable_tmu:
            m.submodules.tmu = tmu = Tmu(c)
            # tmu.io.paletteWrite << io.paletteWrite.translateWith { ... }
            m.d.comb += [
                tmu.palette_write.valid.eq(self.palette_write.valid),
                tmu.palette_write.payload.address.eq(self.palette_write.payload.address),
                tmu.palette_write.payload.data.eq(self.palette_write.payload.data),
                tmu.send_config.eq(controls.sync_render.tmu_send_config),
                tmu.ncc_tables.eq(controls.ncc_tables),
                tmu.invalidate.eq(self.tmu_invalidate),
            ]
            # io.texRead <> tmu.io.texRead
            membus.connect(m, self.tex_read, tmu.tex_read)

            # tmuInput.translateFrom(rasterFork._1.stage())(Tmu.Input.fromRasterizer)
            raster_fork_1_stage = stage(m, raster_fork_1, name="rasterFork1Stage")
            tmu_in_payload = Signal(TmuInput(c), name="tmuInputPayload")
            tmu_input_from_rasterizer(m, c, tmu_in_payload, raster_fork_1_stage.payload)
            tmu_input = translate_with(m, raster_fork_1_stage, tmu_in_payload)
            # tmuInput >/-> tmu.io.input
            connect_stream(m, tmu.input, _pipe_s2m_m2s(m, tmu_input, name="tmuInput"))
            connect_stream(m, tmu_output, tmu.output)
            m.d.comb += [
                tmu_input_valid.eq(tmu.input.valid),
                tmu_busy.eq(tmu.busy),
            ]
        else:
            # tmuOutput.translateFrom(rasterFork._1.stage()) { zero texel }
            raster_fork_1_stage = stage(m, raster_fork_1, name="rasterFork1Stage")
            arbitration_from(m, tmu_output, raster_fork_1_stage)
            m.d.comb += [
                tmu_output.payload.texture.r.eq(0),
                tmu_output.payload.texture.g.eq(0),
                tmu_output.payload.texture.b.eq(0),
                tmu_output.payload.texture_alpha.eq(0),
            ]
            if c.trace.enabled:
                m.d.comb += tmu_output.payload.trace.eq(raster_fork_1_stage.payload.trace)
            cmd = self.tex_read.cmd
            frag = cmd.payload.fragment
            m.d.comb += [
                cmd.valid.eq(0),
                frag.opcode.eq(membus.CMD_READ),
                frag.address.eq(0),
                frag.length.eq(0),
                cmd.payload.last.eq(1),
                frag.source.eq(0),
                self.tex_read.rsp.ready.eq(1),
                tmu_input_valid.eq(0),
                tmu_busy.eq(0),
            ]

        # val tmuJoined = StreamJoin(tmuOutput.stage(), postTmuQueue)
        tmu_joined = join(m, stage(m, tmu_output, name="tmuOutputStage"), post_tmu_queue,
                          name="tmuJoined")

        # colorCombineInput.translateFrom(tmuJoined.stage())(fromTmuAndRaster)
        tmu_joined_stage = stage(m, tmu_joined, name="tmuJoinedStage")
        cc_in_payload = Signal(ColorCombineInput(c), name="colorCombineInputPayload")
        color_combine_input_from_tmu_and_raster(
            m, c, cc_in_payload, tmu_joined_stage.payload["_1"], tmu_joined_stage.payload["_2"],
            controls.sync_render)
        color_combine_input = translate_with(m, tmu_joined_stage, cc_in_payload)
        # colorCombineInput >/-> colorCombine.io.input
        connect_stream(m, color_combine.input,
                       _pipe_s2m_m2s(m, color_combine_input, name="colorCombineInput"))

        # ------------------------------------------------------------------
        # span prefetchers
        # ------------------------------------------------------------------
        span_prefetch_1, span_prefetch_2 = fork2(m, rasterizer.prefetch_span, synchronous=True,
                                                 name="spanPrefetchFork")
        m.submodules.colorPrefetcher = color_prefetcher = SpanPrefetcher(c, color_plane=True)
        m.submodules.auxPrefetcher = aux_prefetcher = SpanPrefetcher(c, color_plane=False)
        connect_stream(m, color_prefetcher.span, span_prefetch_1)
        connect_stream(m, aux_prefetcher.span, span_prefetch_2)
        for pf in (color_prefetcher, aux_prefetcher):
            m.d.comb += [
                pf.routing.eq(controls.sync_render.routing),
                pf.fbz_mode.eq(controls.sync_render.fbz_mode),
                pf.y_origin_enable.eq(y_origin_enable),
                pf.y_origin_swap_value.eq(_resize_u(y_origin_swap_value, nf)),
            ]
        connect_stream(m, self.prefetch_color, color_prefetcher.read_req)
        connect_stream(m, self.prefetch_aux, aux_prefetcher.read_req)

        # (Scala l. 366-377: `chromaKill` on colorCombine.io.output is computed
        #  and never read -- pruned upstream, not reproduced.)

        # ------------------------------------------------------------------
        # colour combine / LFB pipeline-output merge -> fog
        # ------------------------------------------------------------------
        fog_arbiter_input = _merge_first_wins(m, color_combine.output, lfb.pipeline_output,
                                              name="fogArbiterInput")

        fog_output = Stream(FogOutput(c), path=("fogOutput",))
        fog_busy = Signal(name="fogBusy")

        if c.enable_fog:
            m.submodules.fog = fog = Fog(c)
            m.d.comb += fog.fog_table.eq(controls.fog_table)
            # fogArbiterInput >/-> fog.io.input
            connect_stream(m, fog.input, _pipe_s2m_m2s(m, fog_arbiter_input,
                                                       name="fogArbiterInput"))
            connect_stream(m, fog_output, fog.output)
            m.d.comb += fog_busy.eq(fog.busy)
        else:
            # fogOutput.translateFrom(fogArbiterInput.stage())(assignFogBypass)
            src = stage(m, fog_arbiter_input, name="fogArbiterInputStage")
            arbitration_from(m, fog_output, src)
            sp = src.payload
            dst = fog_output.payload
            m.d.comb += [
                dst.coords.eq(sp.coords),
                dst.color.eq(sp.color),
                dst.alpha.eq(sp.alpha),
                dst.depth.eq(sp.depth),
                dst.w_depth.eq(0),
                dst.color_before_fog.eq(sp.color),
                dst.chroma_key.eq(sp.chroma_key),
                dst.za_color.eq(sp.za_color),
                dst.alpha_mode.eq(sp.alpha_mode),
                dst.fbz_mode.eq(sp.fbz_mode),
                dst.routing.eq(sp.routing),
                fog_busy.eq(0),
            ]
            if c.trace.enabled:
                m.d.comb += dst.trace.eq(sp.trace)

        # (Scala l. 426-443: `alphaPassed` / `alphaKill` on fogOutput are
        #  computed and never read -- pruned upstream, not reproduced.)

        # ------------------------------------------------------------------
        # framebuffer access, then the chroma-key / alpha-test kills
        # ------------------------------------------------------------------
        # fogOutput >/-> fbAccess.io.input
        connect_stream(m, fb_access.input, _pipe_s2m_m2s(m, fog_output, name="fogOutput"))
        connect_stream(m, fb_access.fb_read_color_rsp, self.color_read_rsp)
        connect_stream(m, fb_access.fb_read_aux_rsp, self.aux_read_rsp)
        connect_stream(m, self.color_read_req,
                       s2m_pipe(m, fb_access.fb_read_color_req, name="colorReadReq_s2mPipe"))
        connect_stream(m, self.aux_read_req,
                       s2m_pipe(m, fb_access.fb_read_aux_req, name="auxReadReq_s2mPipe"))

        fbo = fb_access.output.payload
        fb_access_chroma_key = Value.cast(fbo.chroma_key)
        fb_access_chroma_kill = Signal(name="fbAccessChromaKill")
        if c.enable_chroma_key:
            m.d.comb += fb_access_chroma_kill.eq(
                fbo.fbz_mode.enable_chroma_key
                & (fbo.color_before_fog.r == fb_access_chroma_key[16:24])
                & (fbo.color_before_fog.g == fb_access_chroma_key[8:16])
                & (fbo.color_before_fog.b == fb_access_chroma_key[0:8]))
        else:
            m.d.comb += fb_access_chroma_kill.eq(0)
        fb_access_alpha_bits = fbo.alpha_mode
        fb_access_alpha_passed = _alpha_passed(
            m, fb_access_alpha_bits.alpha_func, fbo.alpha, fb_access_alpha_bits.alpha_ref,
            name="fbAccessAlphaPassed")
        fb_access_alpha_kill = Signal(name="fbAccessAlphaKill")
        if c.enable_alpha_test:
            m.d.comb += fb_access_alpha_kill.eq(
                fb_access_alpha_bits.alpha_test_enable & ~fb_access_alpha_passed)
        else:
            m.d.comb += fb_access_alpha_kill.eq(0)
        after_fb_kills = stage(
            m, throw_when(m, fb_access.output, fb_access_chroma_kill | fb_access_alpha_kill),
            name="afterFbKills")

        # val trianglePreDither = afterFbKills.translateWith(PreDither.fromFramebufferAccess)
        #   .m2sPipe()
        tri_pd_payload = Signal(WritePreDither(c), name="trianglePreDitherPayload")
        pre_dither_from_framebuffer_access(m, c, tri_pd_payload, after_fb_kills.payload)
        triangle_pre_dither = m2s_pipe(m, translate_with(m, after_fb_kills, tri_pd_payload),
                                       name="trianglePreDither")

        # ------------------------------------------------------------------
        # statistics counters
        # ------------------------------------------------------------------
        pixels_in_counter = Signal(24, name="pixelsInCounter")
        chroma_fail_counter = Signal(24, name="chromaFailCounter")
        z_func_fail_counter = Signal(24, name="zFuncFailCounter")
        a_func_fail_counter = Signal(24, name="aFuncFailCounter")
        pixels_out_counter = Signal(24, name="pixelsOutCounter")
        clear_perf_counters = self.external_busy.nop_cmd_reset

        with m.If(clear_perf_counters):
            m.d.sync += [
                pixels_in_counter.eq(0),
                chroma_fail_counter.eq(0),
                z_func_fail_counter.eq(0),
                a_func_fail_counter.eq(0),
                pixels_out_counter.eq(0),
            ]
        with m.Else():
            with m.If(fire(fb_access.output) & fb_access_chroma_kill):
                m.d.sync += chroma_fail_counter.eq(chroma_fail_counter + 1)
            with m.If(fire(fb_access.output) & fb_access_alpha_kill):
                m.d.sync += a_func_fail_counter.eq(a_func_fail_counter + 1)
            with m.If(fb_access.z_func_fail):
                m.d.sync += z_func_fail_counter.eq(z_func_fail_counter + 1)
            # four 3-bit terms, Spinal `+` keeps 3 bits (wrapping), then resize(24)
            pixels_in_delta = (
                _resize_u(fire(color_combine.output), 3)
                + _resize_u(fire(lfb.pipeline_output), 3)
                + _resize_u(fire(lfb.write_output), 3)
                + _resize_u(fastfill_writer.generated_pixels, 3))[:3]
            m.d.sync += pixels_in_counter.eq(pixels_in_counter + pixels_in_delta)

        # ------------------------------------------------------------------
        # pre-dither merge (triangle wins over LFB bypass), dither, write
        # ------------------------------------------------------------------
        pre_dither_raw = _merge_first_wins(m, triangle_pre_dither, lfb.write_output,
                                           name="preDitherRaw")
        pre_dither_merged = stage(m, pre_dither_raw, name="preDitherMerged")

        for_dither, for_pipe = fork2(m, pre_dither_merged, synchronous=True, name="preDitherFork")
        dither_output = Stream(DITHER_OUTPUT_LAYOUT, path=("ditherOutput",))
        if c.enable_dither:
            m.submodules.dither = dither = Dither()
            dither_in = Signal(DITHER_INPUT_LAYOUT, name="ditherInputPayload")
            input_from_pre_dither(m, dither_in, for_dither.payload)
            connect_stream(m, dither.input, translate_with(m, for_dither, dither_in))
            connect_stream(m, dither_output, dither.output)
        else:
            src = m2s_pipe(m, for_dither, name="forDitherPipe")
            arbitration_from(m, dither_output, src)
            m.d.comb += [
                dither_output.payload.dit_r.eq(src.payload.r >> 3),
                dither_output.payload.dit_g.eq(src.payload.g >> 2),
                dither_output.payload.dit_b.eq(src.payload.b >> 3),
            ]

        pre_dither_piped = m2s_pipe(m, for_pipe, name="preDitherPiped")
        dither_joined = join(m, dither_output, pre_dither_piped, name="ditherJoined")
        for_color_write, for_aux_write = fork2(m, dither_joined, synchronous=True,
                                               name="ditherJoinedFork")

        # colorWriteInput = forColorWrite.throwWhen(!_2.rgbWrite).translateWith(colorFromDither)
        cw_pd = for_color_write.payload["_2"]
        cw_payload = Signal(WriteInput(c), name="colorWriteInputPayload")
        write_input_color_from_dither(
            m, c, cw_payload, cw_pd, for_color_write.payload["_1"],
            cw_pd.trace if c.trace.enabled else None)
        color_write_input = translate_with(
            m, throw_when(m, for_color_write, ~cw_pd.rgb_write), cw_payload)
        connect_stream(m, write_color.i.from_pipeline,
                       _pipe_s2m_m2s(m, color_write_input, name="colorWriteInput"))

        # auxWriteInput = forAuxWrite.throwWhen(!_2.auxWrite).translateWith(auxFromPreDither)
        aw_pd = for_aux_write.payload["_2"]
        aw_payload = aux_from_pre_dither(m, c, aw_pd, aw_pd.trace if c.trace.enabled else None)
        aux_write_input = translate_with(
            m, throw_when(m, for_aux_write, ~aw_pd.aux_write), aw_payload)
        connect_stream(m, write_aux.i.from_pipeline,
                       _pipe_s2m_m2s(m, aux_write_input, name="auxWriteInput"))

        fastfill_color_write = s2m_pipe(m, fastfill_writer.color_write, name="fastfillColorWrite")
        fastfill_aux_write = s2m_pipe(m, fastfill_writer.aux_write, name="fastfillAuxWrite")

        color_write_merged = _merge_first_wins(m, fastfill_color_write, write_color.o.fb_write,
                                               name="colorWriteMerged")
        aux_write_merged = _merge_first_wins(m, fastfill_aux_write, write_aux.o.fb_write,
                                             name="auxWriteMerged")
        connect_stream(m, self.color_write, color_write_merged)
        connect_stream(m, self.aux_write, aux_write_merged)

        # The two pixelsOut `when`s come after the clear block in the Scala, so
        # they override it: a fastfill write landing on a clear cycle still adds.
        with m.If(~clear_perf_counters & fire(write_color.i.from_pipeline)):
            m.d.sync += pixels_out_counter.eq(pixels_out_counter + 1)
        with m.If(fire(fastfill_writer.color_write)):
            m.d.sync += pixels_out_counter.eq(
                pixels_out_counter + _resize_u(fastfill_writer.color_written_pixels, 24))

        m.d.comb += [
            self.stats.pixels_in.eq(pixels_in_counter),
            self.stats.chroma_fail.eq(chroma_fail_counter),
            self.stats.z_func_fail.eq(z_func_fail_counter),
            self.stats.a_func_fail.eq(a_func_fail_counter),
            self.stats.pixels_out.eq(pixels_out_counter),
        ]

        # ------------------------------------------------------------------
        # debug maps
        # ------------------------------------------------------------------
        fastfill_write_valid = fastfill_writer.color_write.valid | fastfill_writer.aux_write.valid
        fastfill_write_ready = fastfill_writer.color_write.ready & fastfill_writer.aux_write.ready
        busy = self.debug.busy
        m.d.comb += [
            busy.triangle_setup_valid.eq(triangle_setup.o.valid),
            busy.rasterizer_running.eq(rasterizer.running),
            busy.tmu_input_valid.eq(tmu_input_valid),
            busy.tmu_busy.eq(tmu_busy),
            busy.fb_access_busy.eq(fb_access.busy),
            busy.color_combine_input_valid.eq(color_combine.input.valid),
            busy.fog_busy.eq(fog_busy),
            busy.fb_access_input_valid.eq(fb_access.input.valid),
            busy.write_color_input_valid.eq(write_color.i.from_pipeline.valid),
            busy.write_aux_input_valid.eq(write_aux.i.from_pipeline.valid),
            busy.fastfill_running.eq(fastfill_writer.running),
            busy.swap_waiting.eq(self.external_busy.swap_waiting),
            busy.lfb_busy.eq(lfb.busy),
            busy.fastfill_output_valid.eq(fastfill_writer.word_valid),
            busy.fastfill_output_ready.eq(fastfill_writer.word_ready),
            busy.fastfill_write_valid.eq(fastfill_write_valid),
            busy.fastfill_write_ready.eq(fastfill_write_ready),
            busy.pre_dither_merged_valid.eq(pre_dither_merged.valid),
            busy.pre_dither_merged_ready.eq(pre_dither_merged.ready),
            busy.pre_dither_piped_valid.eq(pre_dither_piped.valid),
            busy.pre_dither_piped_ready.eq(pre_dither_piped.ready),
            busy.dither_output_valid.eq(dither_output.valid),
            busy.dither_output_ready.eq(dither_output.ready),
            busy.dither_joined_valid.eq(dither_joined.valid),
            busy.dither_joined_ready.eq(dither_joined.ready),
            busy.color_fork_valid.eq(for_color_write.valid),
            busy.color_fork_ready.eq(for_color_write.ready),
            busy.aux_fork_valid.eq(for_aux_write.valid),
            busy.aux_fork_ready.eq(for_aux_write.ready),
            busy.write_color_ready.eq(write_color.i.from_pipeline.ready),
            busy.write_aux_ready.eq(write_aux.i.from_pipeline.ready),
            busy.fastfill_write_aux_write.eq(fastfill_writer.aux_write.valid),
        ]

        wp = self.debug.write_path
        st = self.fb_stats
        m.d.comb += [
            wp.fastfill_running.eq(fastfill_writer.running),
            wp.fastfill_output_valid.eq(fastfill_writer.word_valid),
            wp.fastfill_output_ready.eq(fastfill_writer.word_ready),
            wp.fastfill_write_valid.eq(fastfill_write_valid),
            wp.fastfill_write_ready.eq(fastfill_write_ready),
            wp.pre_dither_merged_valid.eq(pre_dither_merged.valid),
            wp.pre_dither_merged_ready.eq(pre_dither_merged.ready),
            wp.dither_joined_valid.eq(dither_joined.valid),
            wp.dither_joined_ready.eq(dither_joined.ready),
            wp.color_fork_valid.eq(for_color_write.valid),
            wp.color_fork_ready.eq(for_color_write.ready),
            wp.color_write_input_valid.eq(color_write_input.valid),
            wp.color_write_input_ready.eq(color_write_input.ready),
            wp.color_write_fb_valid.eq(write_color.o.fb_write.valid),
            wp.color_write_fb_ready.eq(write_color.o.fb_write.ready),
            wp.fb_color_busy.eq(self.fb_status.color_busy),
            wp.fb_aux_busy.eq(self.fb_status.aux_busy),
            wp.aux_fork_valid.eq(for_aux_write.valid),
            wp.aux_fork_ready.eq(for_aux_write.ready),
            wp.aux_write_input_valid.eq(aux_write_input.valid),
            wp.aux_write_input_ready.eq(aux_write_input.ready),
            wp.aux_write_fb_valid.eq(write_aux.o.fb_write.valid),
            wp.aux_write_fb_ready.eq(write_aux.o.fb_write.ready),
            wp.fb_fill_hits_non_zero.eq(st.fill_hits.any()),
            wp.fb_fill_misses_non_zero.eq(st.fill_misses.any()),
            wp.fb_fill_burst_count_non_zero.eq(st.fill_burst_count.any()),
            wp.fb_fill_burst_beats_non_zero.eq(st.fill_burst_beats.any()),
            wp.fb_fill_stall_cycles_non_zero.eq(st.fill_stall_cycles.any()),
            wp.pixels_in_non_zero.eq(pixels_in_counter.any()),
            wp.pixels_out_non_zero.eq(pixels_out_counter.any()),
            wp.z_func_fail_non_zero.eq(z_func_fail_counter.any()),
            wp.a_func_fail_non_zero.eq(a_func_fail_counter.any()),
        ]

        # ------------------------------------------------------------------
        # pipelineBusy / pipelineBusySources
        # ------------------------------------------------------------------
        sources = [
            triangle_setup.o.valid,                   # 0
            rasterizer.running,                       # 1
            tmu_input_valid,                          # 2
            tmu_busy,                                 # 3
            fb_access.busy,                           # 4
            self.fb_status.color_busy,                # 5  (not in pipelineBusySignal)
            self.fb_status.aux_busy,                  # 6  (not in pipelineBusySignal)
            color_combine.input.valid,                # 7
            fog_busy,                                 # 8
            fb_access.input.valid,                    # 9
            write_color.i.from_pipeline.valid,        # 10
            write_aux.i.from_pipeline.valid,          # 11
            write_color.o.fb_write.valid,             # 12
            write_aux.o.fb_write.valid,               # 13
            fastfill_writer.running,                  # 14
            fastfill_writer.word_valid,               # 15
            fastfill_writer.color_write.valid,        # 16
            fastfill_writer.aux_write.valid,          # 17
            pre_dither_merged.valid,                  # 18
            pre_dither_piped.valid,                   # 19
            dither_output.valid,                      # 20
            dither_joined.valid,                      # 21
            for_color_write.valid,                    # 22
            for_aux_write.valid,                      # 23
            color_write_input.valid,                  # 24
            aux_write_input.valid,                    # 25
            self.external_busy.nop_cmd,               # 26
            self.external_busy.fastfill_cmd,          # 27
            self.external_busy.swapbuffer_cmd,        # 28
            self.external_busy.swap_waiting,          # 29
            lfb.busy,                                 # 30
        ]
        pipeline_busy_signal = Signal(name="pipelineBusySignal")
        busy_terms = [s for k, s in enumerate(sources) if k not in (5, 6)]
        acc = busy_terms[0]
        for term in busy_terms[1:]:
            acc = acc | term
        m.d.comb += pipeline_busy_signal.eq(acc)

        # Framebuffer writeback can drain to external memory after the ordered
        # draw pipeline is idle. Status/sync must not wait on writeback-only
        # busy, but LFB reads still must for coherency.
        lfb_read_safe_busy_signal = Signal(name="lfbReadSafeBusySignal")
        m.d.comb += lfb_read_safe_busy_signal.eq(
            pipeline_busy_signal | self.fb_status.color_busy | self.fb_status.aux_busy)

        pipeline_busy_sources = Signal(32, name="pipelineBusySources")
        m.d.comb += pipeline_busy_sources.eq(0)
        for k, s in enumerate(sources):
            m.d.comb += pipeline_busy_sources[k].eq(s)

        m.d.comb += [
            self.debug.pipeline_busy.eq(pipeline_busy_signal),
            self.debug.pipeline_busy_sources.eq(pipeline_busy_sources),
            lfb.pipeline_busy.eq(lfb_read_safe_busy_signal),
        ]
        return m
