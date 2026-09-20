"""``Fog``: blend the combined pixel colour toward the fog colour.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/pixel/Fog.scala`` (SpinalHDL
1.15.0).

Three ``m2sPipe`` stages, so latency 3 and back-pressure that collapses
bubbles (``collapsBubble`` defaults true):

=========  =========================================================
``wStage`` W-buffer depth: ``voodooFls`` on ``rawW`` and the 86Box
           exponent/mantissa reconstruction, plus the Z- and
           alpha-derived fog factors.  Payload: :class:`FogWStage`.
``stage1`` the fog-table lookup (``wDepth[15:10]`` selects one of 64
           entries, ``wDepth[9:2]`` interpolates between base and
           base+delta) and the ``fogModeSelect`` mux that picks the
           final ``fogA``.  Payload: :class:`FogStage1`.
``stage2`` the blend itself: pass-through, constant-add, additive or
           multiplicative fog.  Payload: :class:`FogOutput`.
=========  =========================================================

**The fog table is not a memory.** ``io.fogTable`` is
``in Vec(Bits(16 bits), 64)`` -- 64 flat input ports
(``io_fogTable_0`` ... ``io_fogTable_63``), read combinationally with a
dynamic index in ``stage1``.  The 64 registers live in ``RegisterBank`` and
are wired straight across by ``PixelPipeline``; ``Fog`` itself holds no
state beyond the three pipeline registers.  Ported as
``In(data.ArrayLayout(unsigned(16), 64))``, which the lockstep harness
expands back into ``fog_table__0`` ... ``fog_table__63``.

**Widths.** Spinal's ``+``/``-`` do *not* grow; Amaranth's do.  Every place
the Scala relies on a wrapping fixed-width add (the ``voodooFls``
accumulator, ``wFogFactorRaw``, the ``diff``/``finalColor`` channel
arithmetic) the Amaranth below truncates explicitly back to the Scala width,
and the comment says which Scala expression it is reproducing.  Likewise
Spinal's ``>>``/``<<`` by a constant change the width while ``|>>``/``|<<``
do not.

No plain ``Reg`` appears in this module -- the only registers are inside
``M2SPipe``, which already declares its unreset payload register
``reset_less=True`` (see ``avoodoo/lib/stream.py``).
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (
    ALPHA_MODE_LAYOUT,
    TRACE_PIXEL_KEY_LAYOUT,
    Color,
    ColorCombineOutput,
    FbRouting,
    FbzMode,
    PixelCoords,
    clamp_to_u8,
    rgb888_color,
)
from avoodoo.lib.fixed import AFixShape
from avoodoo.lib.stream import connect_stream, m2s_pipe, translate_with

__all__ = ["FogOutput", "FogWStage", "FogStage1", "Fog"]


# ---------------------------------------------------------------------------
# object Fog { ... }
# ---------------------------------------------------------------------------


class FogOutput(data.StructLayout):
    """``Fog.Output(c)``.

    ``ColorCombine.Output`` minus ``rawW``/``iteratedAlpha`` (both consumed
    here) plus ``wDepth`` and ``colorBeforeFog``.  The Scala ``def``s
    ``drawColorBufferBase`` / ``drawAuxBufferBase`` / ``fbPixelStride`` are
    ``view.routing.color_base_addr`` and friends.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        members = {
            "coords": PixelCoords(c),
            "color": Color.u8(),
            "alpha": unsigned(8),
            "depth": AFixShape.from_q(c.v_depth_format),
            # precomputed wDepth for W-buffer depth mode
            "w_depth": unsigned(16),
            # pre-fog color for ACOLORBEFOREFOG blend factor
            "color_before_fog": Color.u8(),
            "chroma_key": unsigned(32),
            "za_color": unsigned(32),
            # Per-triangle FIFO registers (pass-through for alpha test and
            # framebuffer access)
            "alpha_mode": ALPHA_MODE_LAYOUT,
            "fbz_mode": FbzMode(),
            "routing": FbRouting(c),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(members)


class FogWStage(data.StructLayout):
    """``Fog.WStage(c)`` -- the payload of the first pipeline stage."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__(
            {
                "payload": FogOutput(c),
                "fog_rgb": Color.u8(),
                "fog_enable": unsigned(1),
                "fog_add": unsigned(1),
                "fog_mult": unsigned(1),
                "fog_constant": unsigned(1),
                "fog_mode_select": unsigned(2),
                "z_fog_factor": unsigned(8),
                "alpha_fog_factor": unsigned(8),
            }
        )


class FogStage1(data.StructLayout):
    """``Fog.Stage1(c)`` -- the payload of the second pipeline stage."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__(
            {
                "payload": FogOutput(c),
                "fog_a": unsigned(8),
                "fog_rgb": Color.u8(),
                "fog_enable": unsigned(1),
                "fog_add": unsigned(1),
                "fog_mult": unsigned(1),
                "fog_constant": unsigned(1),
            }
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _min(a, b):
    """Spinal's ``UInt.min``."""
    return Mux(a < b, a, b)


def voodoo_fls(m: Module, v, *, name: str = "fls"):
    """``def voodooFls(v: UInt): UInt`` -- leading-zero count of a 16-bit value.

    A four-step binary search, no loops.  The accumulator is ``UInt(5 bits)``
    on both sides and Spinal's ``+`` wraps at 5 bits; it never can here
    (the result is at most 15), but the width is pinned anyway so the two
    netlists match.  ``|<<`` keeps the 16-bit width.
    """
    v0 = Signal(16, name=f"{name}_v0")
    m.d.comb += v0.eq(v)

    test0 = Signal(name=f"{name}_test0")
    n0 = Signal(5, name=f"{name}_n0")
    s0 = Signal(16, name=f"{name}_s0")
    m.d.comb += [
        test0.eq(v0[8:16] == 0),
        n0.eq(Mux(test0, 8, 0)),
        s0.eq(Mux(test0, (v0 << 8)[0:16], v0)),
    ]

    test1 = Signal(name=f"{name}_test1")
    n1 = Signal(5, name=f"{name}_n1")
    s1 = Signal(16, name=f"{name}_s1")
    m.d.comb += [
        test1.eq(s0[12:16] == 0),
        n1.eq(n0 + Mux(test1, 4, 0)),
        s1.eq(Mux(test1, (s0 << 4)[0:16], s0)),
    ]

    test2 = Signal(name=f"{name}_test2")
    n2 = Signal(5, name=f"{name}_n2")
    s2 = Signal(16, name=f"{name}_s2")
    m.d.comb += [
        test2.eq(s1[14:16] == 0),
        n2.eq(n1 + Mux(test2, 2, 0)),
        s2.eq(Mux(test2, (s1 << 2)[0:16], s1)),
    ]

    test3 = Signal(name=f"{name}_test3")
    out = Signal(5, name=name)
    m.d.comb += [
        test3.eq(~s2[15]),
        out.eq(n2 + Mux(test3, 1, 0)),
    ]
    return out


# ---------------------------------------------------------------------------
# case class Fog(c: Config) extends Component
# ---------------------------------------------------------------------------


class Fog(wiring.Component):
    """``case class Fog(c: Config) extends Component``."""

    def __init__(self, c: Config | None = None) -> None:
        c = c if c is not None else Config.voodoo1()
        self.c = c
        super().__init__(
            {
                "input": In(stream.Signature(ColorCombineOutput(c))),
                "output": Out(stream.Signature(FogOutput(c))),
                "fog_table": In(data.ArrayLayout(unsigned(16), 64)),
                "busy": Out(1),
            }
        )

    # `def assignFogOutputPayload(dst: Fog.Output, src: Fog.Output)`
    # -- everything except `color`, which each stage drives itself.
    def _assign_fog_output_payload(self, dst, src):
        stmts = [
            dst.coords.eq(src.coords),
            dst.alpha.eq(src.alpha),
            dst.depth.eq(src.depth),
            dst.w_depth.eq(src.w_depth),
            dst.color_before_fog.eq(src.color_before_fog),
            dst.chroma_key.eq(src.chroma_key),
            dst.za_color.eq(src.za_color),
            dst.alpha_mode.eq(src.alpha_mode),
            dst.fbz_mode.eq(src.fbz_mode),
            dst.routing.eq(src.routing),
        ]
        if self.c.trace.enabled:
            stmts.append(dst.trace.eq(src.trace))
        return stmts

    def elaborate(self, platform):
        m = Module()
        c = self.c
        payload = self.input.payload

        # --- Fog mode bits (per-triangle captured config) -------------------
        fog_enable = payload.fog_mode.fog_enable
        fog_add = payload.fog_mode.fog_add
        fog_mult = payload.fog_mode.fog_mult
        fog_constant = payload.fog_mode.fog_constant
        fog_mode_select = payload.fog_mode.fog_mode_select

        fog_rgb = rgb888_color(payload.fog_color)

        # --- Fog factor (fogA) selection ------------------------------------
        # `(depthRaw >> 20).asUInt.resize(8 bits)`: Spinal's `>>` by a constant
        # narrows to SInt(12), the resize then keeps the low 8 -- i.e. raw
        # depth bits [27:20] of the SQ(32,12) value.
        depth_raw = Signal(signed(32), name="depthRaw")
        m.d.comb += depth_raw.eq(payload.depth.raw)
        z_fog_factor = Signal(8, name="zFogFactor")
        m.d.comb += z_fog_factor.eq((depth_raw >> 20)[0:8])

        alpha_fog_factor = payload.iterated_alpha

        # hw_w is the raw 32-bit SInt from wGrad (SQ(32,30) = 2.30 format);
        # 86Box's w is `hw_w << 2`.
        hw_w = payload.raw_w

        # `wUpper = hwW(29 downto 14).asUInt` == (86box_w >> 16) & 0xffff
        w_upper = Signal(16, name="wUpper")
        m.d.comb += w_upper.eq(hw_w[14:30])
        w_exp = voodoo_fls(m, w_upper, name="wExp")

        # `notW = ~((hwW << 2).resize(32 bits))`
        hw_w_shifted = Signal(32, name="hwWShifted")
        not_w = Signal(32, name="notW")
        m.d.comb += [
            hw_w_shifted.eq((hw_w << 2)[0:32]),
            not_w.eq(~hw_w_shifted),
        ]

        # mant = ((~86box_w) >>> (19 - exp)) & 0xfff; exp is 0..15 so the
        # UInt(5) subtraction never wraps.
        shift_amount = Signal(5, name="shiftAmount")
        mantissa = Signal(12, name="mantissa")
        m.d.comb += [
            shift_amount.eq(Const(19, 5) - w_exp),
            mantissa.eq((not_w >> shift_amount)[0:12]),
        ]

        # wDepthRaw = (exp << 12) | mant + 1, all in 17 bits (Spinal's `+`
        # does not grow).
        exp_shifted = Signal(17, name="expShifted")
        w_depth_raw = Signal(17, name="wDepthRaw")
        w_depth_clamped = Signal(16, name="wDepthClamped")
        m.d.comb += [
            exp_shifted.eq((w_exp << 12)[0:17]),
            w_depth_raw.eq((exp_shifted | mantissa) + 1),
        ]
        with m.If(w_depth_raw > 0xFFFF):
            m.d.comb += w_depth_clamped.eq(0xFFFF)
        with m.Else():
            m.d.comb += w_depth_clamped.eq(w_depth_raw[0:16])

        # Edge cases: negative hw_w or bits [31:30] nonzero -> 0;
        # wUpper == 0 -> 0xF001.
        w_depth = Signal(16, name="wDepth")
        with m.If(hw_w[31] | hw_w[30]):
            m.d.comb += w_depth.eq(0)
        with m.Elif(w_upper == 0):
            m.d.comb += w_depth.eq(0xF001)
        with m.Else():
            m.d.comb += w_depth.eq(w_depth_clamped)

        # --- wStage = io.input.translateWith{...}.m2sPipe() ------------------
        w_stage_in = Signal(FogWStage(c), name="wStagePayload")
        m.d.comb += [
            w_stage_in.payload.coords.eq(payload.coords),
            w_stage_in.payload.color.eq(payload.color),
            w_stage_in.payload.alpha.eq(payload.alpha),
            w_stage_in.payload.depth.eq(payload.depth),
            w_stage_in.payload.w_depth.eq(w_depth),
            w_stage_in.payload.color_before_fog.eq(payload.color),
            w_stage_in.payload.chroma_key.eq(payload.chroma_key),
            w_stage_in.payload.za_color.eq(payload.za_color),
            w_stage_in.payload.alpha_mode.eq(payload.alpha_mode),
            w_stage_in.payload.fbz_mode.eq(payload.fbz_mode),
            w_stage_in.payload.routing.eq(payload.routing),
            w_stage_in.fog_rgb.eq(fog_rgb),
            w_stage_in.fog_enable.eq(fog_enable),
            w_stage_in.fog_add.eq(fog_add),
            w_stage_in.fog_mult.eq(fog_mult),
            w_stage_in.fog_constant.eq(fog_constant),
            w_stage_in.fog_mode_select.eq(fog_mode_select),
            w_stage_in.z_fog_factor.eq(z_fog_factor),
            w_stage_in.alpha_fog_factor.eq(alpha_fog_factor),
        ]
        if c.trace.enabled:
            m.d.comb += w_stage_in.payload.trace.eq(payload.trace)

        w_stage = m2s_pipe(m, translate_with(m, self.input, w_stage_in), name="wStage")

        # --- stage1 = wStage.translateWith{...}.m2sPipe() --------------------
        ws = w_stage.payload
        stage1_in = Signal(FogStage1(c), name="stage1Payload")
        m.d.comb += stage1_in.payload.eq(ws.payload)

        table_idx = Signal(6, name="tableIdx")
        m.d.comb += table_idx.eq(ws.payload.w_depth[10:16])
        table_entry = self.fog_table[table_idx]
        fog_base = table_entry[0:8]
        fog_delta = table_entry[8:16].as_signed()

        # `wBlend.asSInt.resize(16 bits)`: wBlend is a UInt(8) reinterpreted
        # as SInt(8) *before* the sign-extension, so blends >= 128 really do
        # go negative here. Faithful to the Scala.
        w_blend = Signal(8, name="wBlend")
        w_blend_s = Signal(signed(16), name="wBlendS")
        delta_product = Signal(signed(24), name="deltaProduct")
        m.d.comb += [
            w_blend.eq(ws.payload.w_depth[2:10]),
            w_blend_s.eq(w_blend.as_signed()),
            delta_product.eq(fog_delta * w_blend_s),
        ]

        # `fogBase.resize(10).asSInt + deltaProduct.resize(10)`, SInt(10) wrap.
        w_fog_factor_raw = Signal(signed(10), name="wFogFactorRaw")
        m.d.comb += w_fog_factor_raw.eq(
            (fog_base + (delta_product >> 8)[0:10].as_signed())[0:10].as_signed()
        )
        # the Scala spells `clampToU8` out inline here; same three cases.
        w_fog_factor = Signal(8, name="wFogFactor")
        m.d.comb += w_fog_factor.eq(clamp_to_u8(w_fog_factor_raw))

        with m.Switch(ws.fog_mode_select):
            with m.Case(0):
                m.d.comb += stage1_in.fog_a.eq(w_fog_factor)
            with m.Case(1):
                m.d.comb += stage1_in.fog_a.eq(ws.alpha_fog_factor)
            with m.Case(2):
                m.d.comb += stage1_in.fog_a.eq(ws.z_fog_factor)
            with m.Default():
                m.d.comb += stage1_in.fog_a.eq(0)

        m.d.comb += [
            stage1_in.fog_rgb.eq(ws.fog_rgb),
            stage1_in.fog_enable.eq(ws.fog_enable),
            stage1_in.fog_add.eq(ws.fog_add),
            stage1_in.fog_mult.eq(ws.fog_mult),
            stage1_in.fog_constant.eq(ws.fog_constant),
        ]

        stage1 = m2s_pipe(m, translate_with(m, w_stage, stage1_in), name="stage1")

        # --- stage2 = stage1.translateWith{...}.m2sPipe() --------------------
        s1 = stage1.payload
        stage2_in = Signal(FogOutput(c), name="stage2Payload")
        m.d.comb += self._assign_fog_output_payload(stage2_in, s1.payload)

        fog_a_plus_1 = Signal(9, name="fogAPlus1")
        m.d.comb += fog_a_plus_1.eq(s1.fog_a + 1)

        cbf = s1.payload.color_before_fog
        out_color = Signal(Color.u8(), name="outColor")

        with m.If(~s1.fog_enable):
            m.d.comb += out_color.eq(cbf)
        with m.Elif(s1.fog_constant):
            # `(s.resize(9) + f.resize(9)).min(255).resize(8)` per channel.
            for ch in ("r", "g", "b"):
                total = Signal(9, name=f"constSum_{ch}")
                m.d.comb += [
                    total.eq(getattr(cbf, ch) + getattr(s1.fog_rgb, ch)),
                    getattr(out_color, ch).eq(_min(total, Const(255, 9))[0:8]),
                ]
        with m.Else():
            base = Signal(Color.s10(), name="base")
            with m.If(s1.fog_add):
                m.d.comb += base.eq(0)  # base.foreach(_ := 0)
            with m.Else():
                # fogRgb.map(_.resize(10 bits).asSInt) -- zero-extend, positive
                m.d.comb += [
                    base.r.eq(s1.fog_rgb.r),
                    base.g.eq(s1.fog_rgb.g),
                    base.b.eq(s1.fog_rgb.b),
                ]

            diff = Signal(Color.s10(), name="diff")
            with m.If(~s1.fog_mult):
                # `b - s.resize(10 bits).asSInt`, SInt(10) wrap
                for ch in ("r", "g", "b"):
                    m.d.comb += getattr(diff, ch).eq(
                        (getattr(base, ch) - getattr(cbf, ch))[0:10].as_signed()
                    )
            with m.Else():
                m.d.comb += diff.eq(base)

            # `(False ## fogAPlus1).asSInt` -- SInt(10), always positive
            fog_a_plus_1_signed = Cat(fog_a_plus_1, Const(0, 1)).as_signed()

            mul_channels = {}
            for ch in ("r", "g", "b"):
                mul = Signal(signed(10), name=f"mulChannel_{ch}")
                m.d.comb += mul.eq(
                    ((getattr(diff, ch) * fog_a_plus_1_signed) >> 8)[0:10].as_signed()
                )
                mul_channels[ch] = mul

            final_color = Signal(Color.s10(), name="finalColor")
            with m.If(s1.fog_mult):
                for ch in ("r", "g", "b"):
                    m.d.comb += getattr(final_color, ch).eq(mul_channels[ch])
            with m.Else():
                # `s.resize(10 bits).asSInt + m`, SInt(10) wrap
                for ch in ("r", "g", "b"):
                    m.d.comb += getattr(final_color, ch).eq(
                        (getattr(cbf, ch) + mul_channels[ch])[0:10].as_signed()
                    )

            for ch in ("r", "g", "b"):
                m.d.comb += getattr(out_color, ch).eq(clamp_to_u8(getattr(final_color, ch)))

        m.d.comb += stage2_in.color.eq(out_color)

        stage2 = m2s_pipe(m, translate_with(m, stage1, stage2_in), name="stage2")

        # --- io.output << stage2 ; io.busy -----------------------------------
        connect_stream(m, self.output, stage2)
        m.d.comb += self.busy.eq(w_stage.valid | stage1.valid | stage2.valid)
        return m
