"""``FastfillWordWriter``: the fastfill clear as 32-bit word writes, two pixels a beat.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/raster/FastfillWordWriter.scala``
(288 lines, SpinalHDL 1.15.0), plan Phase 3.  Same ports, same registers,
same fork/join/pipe topology, same two ``Dither`` instances.

What it does, in the Scala's own terms: one ``NoData`` beat on ``cmd``
latches the registers and the clip window (``regsCapture``,
``clip*Capture``) and starts a walk over the clip rectangle in *word*
steps -- ``curWordX`` starts at the even-aligned ``clipLeft`` and advances
by two pixels, ``curY`` by one row.  Each word beat (``wordStream``) is
forked three ways: lane 0 and lane 1 go through their own ``Dither``
(``color1`` dithered at ``(x, y)`` and ``(x + 1, y)``), the third copy is
translated into ``WordMeta`` (the two plane addresses, lane enables, write
enables, ``depthAlpha``) and ``m2sPipe``'d to line up with the dither
latency.  The three are ``StreamJoin``'d, ``halfPipe``'d, and forked once
more into ``colorWrite`` and ``auxWrite``, each of which only asserts
``valid`` when its plane is enabled and at least one lane is inside the
clip window, and otherwise lets the beat through unconsumed.

Fidelity notes, each a thing that would silently diverge if ported by eye:

* **Reset.**  ``active``, ``curWordX``, ``curY`` are ``RegInit``/``init(0)``
  and reset; ``regsCapture`` and the four ``clip*Capture`` registers are
  plain ``RegNextWhen`` and are ``reset_less=True`` (plan §6).  Inside the
  library pieces: the ``m2sPipe`` ``rValid`` and the ``halfPipe`` ``rValid``
  reset, their ``rData`` do not (``lib/stream.py`` already does that).
* **Widths.**  ``nextWordXWide`` / ``nextYWide`` are the 11-bit
  ``resize(11 bits) + 2`` / ``+ 1`` so the row-end and last-word compares
  see the carry; ``lane1X = (curWordX + 1).resize(10)`` and
  ``laneX = (baseX + 1).resize(10)`` wrap at 10 bits
  (:func:`avoodoo.lib.intops.u_add`).  ``transformedY`` is a 12-bit
  ``SInt`` subtraction with no growth, reinterpreted unsigned and
  zero-extended to ``addressWidth`` (:func:`avoodoo.lib.intops.s_sub`).
* **``StreamFork2(synchronous = true)``** drives each output's ``valid``
  from ``i.valid && i.ready`` -- output valid depends on output ready.
  That is what ``lib.stream.Fork`` reproduces and what the drain timing
  of this module depends on; do not "fix" it.
* **``StreamJoin`` payload** is Spinal's ``TupleBundle2`` (``_1``/``_2``),
  nested once: ``ditherJoined.payload._1._1`` is dither 0, ``_1._2``
  dither 1, ``_2`` the meta.  ``data.View`` refuses attribute access to
  names starting with ``_``, so they are indexed: ``payload["_1"]["_2"]``.
* The Scala names the internal live/captured register mux ``regs``, the
  same as the ``io.regs`` port that Amaranth flattens to ``regs``; the
  internal one is spelled ``regsSel`` here (as in ``fastfill_write.py``).

The companion ``object FastfillWordWriter`` bundles (``WordReq``,
``WordMeta``) and the six private ``def``s are here, module level, each
naming its Scala line.  ``FramebufferAddressMath.planeAddress`` and
``alignedWordAddress`` are the shared helpers in
:mod:`avoodoo.core.core_types`.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, Value, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (
    FbRouting,
    FramebufferWriteReq,
    aligned_word_address,
    plane_address,
)
from avoodoo.lib.intops import s_sub, u_add, zext
from avoodoo.lib.stream import (
    Stream,
    fire,
    fork2,
    half_pipe,
    join,
    m2s_pipe,
    translate_with,
)
from avoodoo.pixel.dither import Dither
from avoodoo.raster.fastfill_write import FastfillWriteRegs

__all__ = [
    "WordReq",
    "WordMeta",
    "FastfillWordWriter",
    "transformed_y",
    "any_lane_enabled",
    "lane_count",
    "lane_mask",
    "pack_color_word",
    "pack_aux_word",
]


# ---------------------------------------------------------------------------
# object FastfillWordWriter -- the companion bundles
# ---------------------------------------------------------------------------


class WordReq(data.StructLayout):
    """``FastfillWordWriter.WordReq(c)`` (``FastfillWordWriter.scala:205``)."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "base_x": unsigned(10),
            "y": unsigned(10),
            "lane0_enable": unsigned(1),
            "lane1_enable": unsigned(1),
            "rgb_write": unsigned(1),
            "aux_write": unsigned(1),
            "enable_dithering": unsigned(1),
            "use2x2_dither": unsigned(1),
            "routing": FbRouting(c),
            "color1": unsigned(32),
            "depth_alpha": unsigned(16),
            "y_origin": unsigned(1),
            "y_origin_swap_value": unsigned(10),
        })


class WordMeta(data.StructLayout):
    """``FastfillWordWriter.WordMeta(c)`` (``FastfillWordWriter.scala:221``)."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "address": unsigned(c.address_width),
            "aux_address": unsigned(c.address_width),
            "lane0_enable": unsigned(1),
            "lane1_enable": unsigned(1),
            "rgb_write": unsigned(1),
            "aux_write": unsigned(1),
            "depth_alpha": unsigned(16),
        })


# ---------------------------------------------------------------------------
# the companion's private defs
# ---------------------------------------------------------------------------


def transformed_y(c: Config, y_origin, y_origin_swap_value, y) -> Value:
    """``private def transformedY(c, yOrigin, yOriginSwapValue, y): UInt`` (scala:231-241).

    ``y.resize(addressWidth)``, overridden when ``yOrigin`` by
    ``(yOriginSwapValue.resize(nonFraction).asSInt - y.resize(nonFraction).asSInt)
    .asUInt.resize(addressWidth)`` -- the subtraction is at ``nonFraction``
    bits and does not grow.
    """
    w = c.vertex_format.non_fraction
    aw = c.address_width
    flipped = s_sub(zext(y_origin_swap_value, w).as_signed(), zext(y, w).as_signed())
    return Mux(y_origin, zext(flipped.as_unsigned(), aw), zext(y, aw))


def any_lane_enabled(meta) -> Value:
    """``private def anyLaneEnabled(meta: WordMeta): Bool`` (scala:243)."""
    return meta.lane0_enable | meta.lane1_enable


def lane_count(lane0_enable, lane1_enable) -> Value:
    """``private def laneCount(lane0Enable, lane1Enable): UInt`` (scala:245-247).

    ``lane0.asUInt.resize(2) + lane1.asUInt.resize(2)`` -- a 2-bit sum.
    """
    return u_add(zext(lane0_enable, 2), zext(lane1_enable, 2))


def lane_mask(meta) -> Value:
    """``private def laneMask(meta: WordMeta): Bits`` (scala:249-259).

    ``mask := 0``, then ``mask(1 downto 0) := 2'b11`` when lane 0 and
    ``mask(3 downto 2) := 2'b11`` when lane 1 -- as an expression.
    """
    return Cat(meta.lane0_enable.replicate(2), meta.lane1_enable.replicate(2))


def pack_color_word(dit0, dit1, meta) -> Value:
    """``private def packColorWord(dit0, dit1, meta): Bits`` (scala:261-275).

    ``lo = ditR ## ditG ## ditB`` (R at the top, 5-6-5) goes in bits 15..0
    when lane 0 is enabled, ``hi`` (from ``dit1``) in 31..16 when lane 1 is;
    disabled lanes are zero.
    """
    lo = Cat(dit0.dit_b, dit0.dit_g, dit0.dit_r)
    hi = Cat(dit1.dit_b, dit1.dit_g, dit1.dit_r)
    return Cat(Mux(meta.lane0_enable, lo, Const(0, 16)),
               Mux(meta.lane1_enable, hi, Const(0, 16)))


def pack_aux_word(meta) -> Value:
    """``private def packAuxWord(meta: WordMeta): Bits`` (scala:277-287)."""
    return Cat(Mux(meta.lane0_enable, meta.depth_alpha, Const(0, 16)),
               Mux(meta.lane1_enable, meta.depth_alpha, Const(0, 16)))


# ---------------------------------------------------------------------------
# the component
# ---------------------------------------------------------------------------


class FastfillWordWriter(wiring.Component):
    """``case class FastfillWordWriter(c: Config) extends Component``.

    Attributes
    ----------
    cmd : In(stream.Signature(unsigned(0)))
        ``slave Stream(NoData)``; ``ready`` is ``!active``.
    regs : In(FastfillWriteRegs(c))
        ``in(FastfillWrite.Regs(c))``, live; captured on ``cmd.fire``.
    clip_left, clip_right, clip_low_y, clip_high_y : In(10)
        Live clip window; captured on ``cmd.fire``.
    color_write, aux_write : Out(stream.Signature(FramebufferWriteReq(c)))
        ``master Stream(FramebufferPlaneBuffer.WriteReq(c))``, one per plane.
    running, word_valid, word_ready : Out(1)
    generated_pixels, color_written_pixels : Out(2)
        Per-cycle pixel counts for the core's statistics counters.
    """

    def __init__(self, c: Config | None = None) -> None:
        self.c = c = Config.voodoo1() if c is None else c
        super().__init__({
            "cmd": In(stream.Signature(unsigned(0))),
            "regs": In(FastfillWriteRegs(c)),
            "clip_left": In(10),
            "clip_right": In(10),
            "clip_low_y": In(10),
            "clip_high_y": In(10),
            "color_write": Out(stream.Signature(FramebufferWriteReq(c))),
            "aux_write": Out(stream.Signature(FramebufferWriteReq(c))),
            "running": Out(1),
            "word_valid": Out(1),
            "word_ready": Out(1),
            "generated_pixels": Out(2),
            "color_written_pixels": Out(2),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        aw = c.address_width

        cmd_fire = Signal(name="io_cmd_fire")
        m.d.comb += cmd_fire.eq(fire(self.cmd))

        # --- register / clip capture (scala:30-54) --------------------------
        active = Signal(name="active")                             # RegInit(False)
        regs_capture = Signal(FastfillWriteRegs(c), reset_less=True, name="regsCapture")
        with m.If(cmd_fire):
            m.d.sync += regs_capture.eq(self.regs)
        regs = Signal(FastfillWriteRegs(c), name="regsSel")
        m.d.comb += regs.eq(Mux(active, regs_capture, self.regs))

        clip_left_capture = Signal(10, reset_less=True, name="clipLeftCapture")
        clip_right_capture = Signal(10, reset_less=True, name="clipRightCapture")
        clip_low_y_capture = Signal(10, reset_less=True, name="clipLowYCapture")
        clip_high_y_capture = Signal(10, reset_less=True, name="clipHighYCapture")
        with m.If(cmd_fire):
            m.d.sync += [
                clip_left_capture.eq(self.clip_left),
                clip_right_capture.eq(self.clip_right),
                clip_low_y_capture.eq(self.clip_low_y),
                clip_high_y_capture.eq(self.clip_high_y),
            ]

        clip_left = Signal(10, name="clipLeft")
        clip_right = Signal(10, name="clipRight")
        clip_low_y = Signal(10, name="clipLowY")
        clip_high_y = Signal(10, name="clipHighY")
        with m.If(active):
            m.d.comb += [
                clip_left.eq(clip_left_capture),
                clip_right.eq(clip_right_capture),
                clip_low_y.eq(clip_low_y_capture),
                clip_high_y.eq(clip_high_y_capture),
            ]
        with m.Else():
            m.d.comb += [
                clip_left.eq(self.clip_left),
                clip_right.eq(self.clip_right),
                clip_low_y.eq(self.clip_low_y),
                clip_high_y.eq(self.clip_high_y),
            ]

        # `((clipLeft >> 1) ## False).asUInt.resize(10 bits)` (scala:56)
        aligned_clip_left = Signal(10, name="alignedClipLeft")
        m.d.comb += aligned_clip_left.eq(Cat(Const(0, 1), clip_left[1:]))
        empty_rect = Signal(name="emptyRect")
        m.d.comb += empty_rect.eq((clip_right <= clip_left) | (clip_high_y <= clip_low_y))

        cur_word_x = Signal(10, name="curWordX")                   # Reg init(0)
        cur_y = Signal(10, name="curY")                            # Reg init(0)

        m.d.comb += self.cmd.ready.eq(~active)
        with m.If(cmd_fire):
            m.d.sync += [
                active.eq(~empty_rect),
                cur_word_x.eq(aligned_clip_left),
                cur_y.eq(clip_low_y),
            ]

        # --- the word stream (scala:69-84) ----------------------------------
        word_stream = Stream(WordReq(c), path=("wordStream",))
        lane1_x = u_add(cur_word_x, Const(1, 10))
        wp = word_stream.payload
        m.d.comb += [
            word_stream.valid.eq(active),
            wp.base_x.eq(cur_word_x),
            wp.y.eq(cur_y),
            wp.lane0_enable.eq((cur_word_x >= clip_left) & (cur_word_x < clip_right)),
            wp.lane1_enable.eq((lane1_x >= clip_left) & (lane1_x < clip_right)),
            wp.rgb_write.eq(regs.fbz_mode.rgb_buffer_mask),
            wp.aux_write.eq(regs.fbz_mode.aux_buffer_mask),
            wp.enable_dithering.eq(regs.fbz_mode.enable_dithering),
            wp.use2x2_dither.eq(regs.fbz_mode.dither_algorithm),
            wp.routing.eq(regs.routing),
            wp.color1.eq(regs.color1),
            wp.depth_alpha.eq(regs.za_color[0:16]),
            wp.y_origin.eq(regs.fbz_mode.y_origin),
            wp.y_origin_swap_value.eq(regs.y_origin_swap_value),
        ]

        # --- advance (scala:86-102) -----------------------------------------
        next_word_x_wide = Signal(11, name="nextWordXWide")
        next_y_wide = Signal(11, name="nextYWide")
        m.d.comb += [
            next_word_x_wide.eq(u_add(zext(cur_word_x, 11), Const(2, 11))),
            next_y_wide.eq(u_add(zext(cur_y, 11), Const(1, 11))),
        ]
        next_word_x = next_word_x_wide[0:10]
        next_y = next_y_wide[0:10]
        at_row_end = Signal(name="atRowEnd")
        is_last_word = Signal(name="isLastWord")
        m.d.comb += [
            at_row_end.eq(next_word_x_wide >= zext(clip_right, 11)),
            is_last_word.eq(at_row_end & (next_y_wide >= zext(clip_high_y, 11))),
        ]

        with m.If(fire(word_stream)):
            with m.If(is_last_word):
                m.d.sync += active.eq(0)
            with m.Elif(at_row_end):
                m.d.sync += [cur_word_x.eq(aligned_clip_left), cur_y.eq(next_y)]
            with m.Else():
                m.d.sync += cur_word_x.eq(next_word_x)

        # --- fork into the two dither lanes and the meta path (scala:104-105)
        for_lane0, for_rest = fork2(m, word_stream, synchronous=True,
                                    name="wordStream_fork2")
        for_lane1, for_meta = fork2(m, for_rest, synchronous=True,
                                    name="forRest_fork2")

        m.submodules.dither0 = dither0 = Dither()
        m.submodules.dither1 = dither1 = Dither()

        # `dither0.io.input.translateFrom(forLane0) { ... }` (scala:110-119)
        in0, d0 = for_lane0.payload, dither0.input
        lane_y0 = transformed_y(c, in0.y_origin, in0.y_origin_swap_value, in0.y)
        m.d.comb += [
            d0.valid.eq(for_lane0.valid),
            for_lane0.ready.eq(d0.ready),
            d0.payload.r.eq(in0.color1[16:24]),
            d0.payload.g.eq(in0.color1[8:16]),
            d0.payload.b.eq(in0.color1[0:8]),
            d0.payload.x.eq(in0.base_x[0:2]),
            d0.payload.y.eq(lane_y0[0:2]),
            d0.payload.enable.eq(in0.enable_dithering),
            d0.payload.use2x2.eq(in0.use2x2_dither),
        ]

        # `dither1.io.input.translateFrom(forLane1) { ... }` (scala:121-131)
        in1, d1 = for_lane1.payload, dither1.input
        lane_x1 = u_add(in1.base_x, Const(1, 10))
        lane_y1 = transformed_y(c, in1.y_origin, in1.y_origin_swap_value, in1.y)
        m.d.comb += [
            d1.valid.eq(for_lane1.valid),
            for_lane1.ready.eq(d1.ready),
            d1.payload.r.eq(in1.color1[16:24]),
            d1.payload.g.eq(in1.color1[8:16]),
            d1.payload.b.eq(in1.color1[0:8]),
            d1.payload.x.eq(lane_x1[0:2]),
            d1.payload.y.eq(lane_y1[0:2]),
            d1.payload.enable.eq(in1.enable_dithering),
            d1.payload.use2x2.eq(in1.use2x2_dither),
        ]

        # `val metaPipe = forMeta.translateWith { ... }.m2sPipe()` (scala:133-161)
        inm = for_meta.payload
        meta = Signal(WordMeta(c), name="meta")
        output_y = transformed_y(c, inm.y_origin, inm.y_origin_swap_value, inm.y)
        m.d.comb += [
            meta.address.eq(plane_address(
                inm.routing.color_base_addr, zext(inm.base_x, aw),
                zext(output_y, aw), inm.routing.pixel_stride)),
            meta.aux_address.eq(plane_address(
                inm.routing.aux_base_addr, zext(inm.base_x, aw),
                zext(output_y, aw), inm.routing.pixel_stride)),
            meta.lane0_enable.eq(inm.lane0_enable),
            meta.lane1_enable.eq(inm.lane1_enable),
            meta.rgb_write.eq(inm.rgb_write),
            meta.aux_write.eq(inm.aux_write),
            meta.depth_alpha.eq(inm.depth_alpha),
        ]
        meta_pipe = m2s_pipe(m, translate_with(m, for_meta, meta), name="metaPipe")

        # `StreamJoin(StreamJoin(dither0.io.output, dither1.io.output), metaPipe).halfPipe()`
        # (scala:163-164)
        dither_joined = half_pipe(
            m,
            join(m, join(m, dither0.output, dither1.output, name="ditherPair"),
                 meta_pipe, name="ditherMeta"),
            name="ditherJoined")
        for_color, for_aux = fork2(m, dither_joined, synchronous=True,
                                   name="ditherJoined_fork2")

        # --- colour plane (scala:167-179) -----------------------------------
        cp = for_color.payload
        c_meta = cp["_2"]
        c_dit0, c_dit1 = cp["_1"]["_1"], cp["_1"]["_2"]
        color_gate = c_meta.rgb_write & any_lane_enabled(c_meta)
        m.d.comb += [
            self.color_write.valid.eq(for_color.valid & color_gate),
            self.color_write.payload.address.eq(aligned_word_address(c_meta.address)),
            self.color_write.payload.data.eq(pack_color_word(c_dit0, c_dit1, c_meta)),
            self.color_write.payload.mask.eq(lane_mask(c_meta)),
            for_color.ready.eq(~color_gate | self.color_write.ready),
        ]

        # --- aux plane (scala:181-189) --------------------------------------
        ap = for_aux.payload
        a_meta = ap["_2"]
        aux_gate = a_meta.aux_write & any_lane_enabled(a_meta)
        m.d.comb += [
            self.aux_write.valid.eq(for_aux.valid & aux_gate),
            self.aux_write.payload.address.eq(aligned_word_address(a_meta.aux_address)),
            self.aux_write.payload.data.eq(pack_aux_word(a_meta)),
            self.aux_write.payload.mask.eq(lane_mask(a_meta)),
            for_aux.ready.eq(~aux_gate | self.aux_write.ready),
        ]

        # --- status outputs (scala:191-201) ---------------------------------
        m.d.comb += [
            self.running.eq(active),
            self.word_valid.eq(word_stream.valid),
            self.word_ready.eq(word_stream.ready),
            self.generated_pixels.eq(Mux(
                fire(word_stream), lane_count(wp.lane0_enable, wp.lane1_enable),
                Const(0, 2))),
            self.color_written_pixels.eq(Mux(
                fire(self.color_write),
                lane_count(c_meta.lane0_enable, c_meta.lane1_enable),
                Const(0, 2))),
        ]
        return m
