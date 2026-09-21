"""``ColorCombine``: the Voodoo colour/alpha combine unit (CCU / ACU).

1:1 port of ``ext/SpinalVoodoo/src/voodoo/pixel/ColorCombine.scala`` (571
lines, SpinalHDL 1.15.0) on top of :mod:`avoodoo.lib.pipeline`, which is the
``spinal.lib.misc.pipeline`` clone.  Same 34 payloads (``TRACE`` only when
``c.trace.enabled``), same nine nodes ``n0..n8``, same eight default
``StageLink``\\ s, same ``AFix`` format at every step, same rounding.

The eight stages, one per link::

    n0  driveFrom(io.input): pass-through loads, u8 -> s9 widening, the four
        selects (rgbSel / localSelect(+override) / alphaSel / alphaLocalSelect)
    n1  zero other          C_AFTER_ZERO = zeroOther ? 0 : C_OTHER
    n2  subtract local      C_AFTER_SUB  = SQ(10,0)(subClocal ? other-local : other)
    n3  factor select       C_FACTOR_PRE = UQ(0,8) per mselect
    n4  reverse blend       C_FACTOR     = UQ(1,8)((reverseBlend ? f : ~f) + 1)
    n5  multiply            C_AFTER_MUL  = (C_AFTER_SUB * C_FACTOR).fixTo(SQ(10,8))
    n6  add                 C_AFTER_ADD  = (mul + local<<8).fixTo(SQ(10,8)) per add
    n7  clamp               C_CLAMPED    = sat(0..255.996).floor
    n8  driveTo(io.output): pass-through stores, invertOutput

Liveness of the payload registers follows the library: a payload is
registered on a link only between the first and the last node that touches
it, which is how the naive 34 x 8 = 272 collapses to **150** stage registers
plus the 8 ``valid`` registers (``tests/test_pipeline.py::
test_color_combine_skeleton_registers`` pins the per-payload ranges;
``tests/test_color_combine.py`` checks this module emits exactly them).

Three things that are easy to get wrong, all reproduced on purpose:

1. **``clampAFixToFactor`` is a constant zero** (``docs/amaranth/
   afix-semantics.md`` §8.1).  ``UQ(0, 8)`` there is ``spinal.core.UQ`` --
   ``QFormat(width = 0, fraction = 8)``, a *zero-bit* format -- not
   ``AFix.UQ(0 bits, 8 bits)``.  So the ``MSelect.AOTHER`` factor path, RGB
   and alpha, is hard ``8'h0`` in the shipped Verilog and it is hard zero
   here (:func:`clamp_afix_to_factor` builds the same zero-bit ``fix_to``).
   Fixing it is a screenshot-visible change that belongs to a separate task.

2. **The last ``is`` of every Spinal ``switch`` is the Verilog ``default``.**
   Every enum select is a plain binary ``SpinalEnum`` narrower than its
   code space is wide (``MSelect`` is six values in three bits, ``AddMode``
   and ``AlphaLocalSel`` three in two), and Spinal emits the last arm as
   ``default``, so an out-of-range code behaves as the last enumerant.  The
   ``m.Default()`` arms below are those last enumerants, in the same order.

3. **Spinal's ``-`` on ``SInt`` does not grow.**  ``zero - local`` in stage 2
   is ``SInt(9) - SInt(9) = SInt(9)``; Amaranth's grows to 10 bits, so the
   port slices it back before ``AFix(...)`` wraps it (the operands are both
   0..255, so nothing actually wraps -- but the width feeds the AFix bound).

No plain ``Reg`` in the Scala; every register is a ``StageLink`` register,
which :class:`~avoodoo.lib.pipeline.Node` already declares ``reset_less``
for the payloads and reset-to-0 for ``valid`` (plan §6).
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config, QFormat, UQ
from avoodoo.core.core_types import (
    ADD_MODE,
    ALPHA_LOCAL_SEL,
    ALPHA_MODE_LAYOUT,
    ALPHA_SEL,
    FOG_MODE_LAYOUT,
    LOCAL_SEL,
    MSELECT,
    RGB_SEL,
    TRACE_PIXEL_KEY_LAYOUT,
    AddMode,
    AlphaLocalSel,
    AlphaSel,
    Color,
    ColorCombineOutput,
    FbRouting,
    FbzMode,
    LocalSel,
    MSelect,
    PixelCoords,
    RgbSel,
    channels,
)
from avoodoo.lib.fixed import AFixShape, AFixValue
from avoodoo.lib.pipeline import Builder, Node, Payload, StageLink

__all__ = [
    "ColorCombineConfig",
    "ColorCombineInput",
    "ColorCombineOutput",
    "ColorCombine",
    "COLOR_MUL_FORMAT",
    "FACTOR_FORMAT",
    "clamp_sint_to_factor",
    "clamp_afix_to_factor",
    "clamp_afix_to_u8",
    "invert_factor",
    "u8_to_s9",
]


# ---------------------------------------------------------------------------
# object ColorCombine { ... } -- the bundles
# ---------------------------------------------------------------------------


class ColorCombineConfig(data.StructLayout):
    """``ColorCombine.Config()`` -- the ``FbzColorPath`` subset the CCU reads.

    Field names are the ``FBZ_COLOR_PATH_LAYOUT`` names (the Scala fills it
    with ``assignSomeByName``), but the *order* is ``Config``'s own
    declaration order, which differs from the register's -- this is the bit
    packing of the ``config`` leaf group in ``io_input_payload_config_*``.
    """

    def __init__(self) -> None:
        super().__init__({
            # RGB channel controls
            "rgb_sel": RGB_SEL,
            "local_select": LOCAL_SEL,
            "local_select_override": 1,
            "zero_other": 1,
            "sub_clocal": 1,
            "mselect": MSELECT,
            "reverse_blend": 1,
            "add": ADD_MODE,
            "invert_output": 1,
            # Alpha channel controls
            "alpha_sel": ALPHA_SEL,
            "alpha_local_select": ALPHA_LOCAL_SEL,
            "alpha_zero_other": 1,
            "alpha_sub_clocal": 1,
            "alpha_mselect": MSELECT,
            "alpha_reverse_blend": 1,
            "alpha_add": ADD_MODE,
            "alpha_invert_output": 1,
            # Texture enable
            "texture_enable": 1,
        })

    #: ``FbzColorPath`` fields this bundle carries, for ``fromFbzColorPath``.
    FIELDS = (
        "rgb_sel", "local_select", "local_select_override", "zero_other",
        "sub_clocal", "mselect", "reverse_blend", "add", "invert_output",
        "alpha_sel", "alpha_local_select", "alpha_zero_other",
        "alpha_sub_clocal", "alpha_mselect", "alpha_reverse_blend",
        "alpha_add", "alpha_invert_output", "texture_enable",
    )

    @classmethod
    def from_fbz_color_path(cls, dst, fcp):
        """``Config.fromFbzColorPath(fcp)`` -- ``assignSomeByName`` as statements."""
        return [getattr(dst, name).eq(getattr(fcp, name)) for name in cls.FIELDS]


class ColorCombineInput(data.StructLayout):
    """``ColorCombine.Input(c)``.

    ``drawColorBufferBase`` / ``drawAuxBufferBase`` / ``fbPixelStride`` are
    ``routing.color_base_addr`` / ``routing.aux_base_addr`` /
    ``routing.pixel_stride``.  ``trace`` exists only when ``c.trace.enabled``.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        members = {
            "coords": PixelCoords(c),
            "iterated": Color.u8(),          # iterated (Gouraud) colours, 0-255
            "iterated_alpha": unsigned(8),   # iterated alpha, 0-255
            "iterated_z": unsigned(8),       # upper bits of Z for alpha local select
            "depth": AFixShape.from_q(c.v_depth_format),
            "raw_w": signed(32),             # raw W for fog wDepth calculation
            # Texture colours from the TMU chain
            "texture": Color.u8(),
            "texture_alpha": unsigned(8),
            # Constant colours from registers
            "color0": Color.u8(),
            "color1": Color.u8(),
            "color0_alpha": unsigned(8),     # color0 register bits 31:24
            "color1_alpha": unsigned(8),     # color1 register bits 31:24
            # Per-triangle colours passed through to Fog
            "fog_color": unsigned(32),
            "chroma_key": unsigned(32),
            "za_color": unsigned(32),
            # Configuration (decoded enums)
            "config": ColorCombineConfig(),
            # Per-triangle FIFO registers (travel with the pixels)
            "alpha_mode": ALPHA_MODE_LAYOUT,
            "fog_mode": FOG_MODE_LAYOUT,
            "fbz_mode": FbzMode(),
            "routing": FbRouting(c),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(members)


# ---------------------------------------------------------------------------
# the fixed-point formats
# ---------------------------------------------------------------------------

#: ``factorFormat = AFix.UQ(0 bits, 8 bits)`` -- 0.0 .. 255/256, 8 raw bits.
FACTOR_FORMAT = AFixShape.uq(0, 8)

#: ``AFix.UQ(1 bits, 8 bits)`` -- the post-reverse-blend factor, 1/256 .. 1.0.
FACTOR_WIDE_FORMAT = AFixShape.uq(1, 8)

#: ``AFix.SQ(10 bits, 0 bits)`` -- the subtraction result.
AFTER_SUB_FORMAT = AFixShape.sq(10, 0)

#: ``colorMulFormat = QFormat(19, 8, true)`` == ``AFix.SQ(10 bits, 8 bits)``:
#: 10 integer bits + 8 fractional + sign = 19 bits, raw bounds +-2^18.
COLOR_MUL_FORMAT = AFixShape.from_q(QFormat(19, 8, True))
assert COLOR_MUL_FORMAT == AFixShape.sq(10, 8)

#: ``UQ(0, 8)`` in ``clampAFixToFactor`` -- ``spinal.core.UQ``, i.e. a
#: **zero-bit** ``QFormat(0, 8)``.  See §8.1 and :func:`clamp_afix_to_factor`.
_DEAD_FACTOR_FORMAT = AFixShape.from_q(UQ(0, 8))
assert _DEAD_FACTOR_FORMAT.bit_width == 0

#: ``UQ(8, 0)`` in ``clampAFixToU8`` -- an honest 8-bit unsigned integer.
_U8_FORMAT = AFixShape.from_q(UQ(8, 0))
assert _U8_FORMAT == AFixShape.uq(8, 0)


def _color(fmt):
    """``Color(fmt, fmt, fmt)`` -- three identical channels."""
    return Color(fmt, fmt, fmt)


# ---------------------------------------------------------------------------
# the helpers (ColorCombine.scala:240-245, 349-372, 487-501)
# ---------------------------------------------------------------------------


def u8_to_s9(v):
    """``def u8ToS9(v: UInt): SInt = (False ## v).asSInt``."""
    return Cat(v[0:8], Const(0, 1)).as_signed()


def clamp_sint_to_factor(v) -> AFixValue:
    """``def clampSIntToFactor(v: SInt): AFix``.

    ``v.max(S(0)).min(255).asUInt.resize(8 bits)`` bit-copied into a fresh
    ``AFix.UQ(0 bits, 8 bits)`` (``result := clamped`` on an AFix is a raw
    copy, §5.5), so raw 0..255 is read as 0.0 .. 255/256.
    """
    clamped = Mux(v < 0, Const(0, signed(9)), Mux(v > 255, Const(255, signed(9)), v))
    return AFixValue.from_raw(FACTOR_FORMAT, clamped.as_unsigned()[0:8])


def clamp_afix_to_factor(v: AFixValue) -> AFixValue:
    """``def clampAFixToFactor(v: AFix): AFix`` -- **constant zero** (§8.1).

    ``v.sat(satMax = 255, satMin = 0).fixTo(UQ(0, 8))``: the ``UQ`` is
    ``spinal.core.UQ``, so the target is ``QFormat(0, 8, false)`` -- zero
    bits, ``maxRaw = minRaw = 0`` -- and the ``fixTo`` saturates everything
    to it.  The returned value has no bits; assigning it to an
    ``AFix.UQ(0 bits, 8 bits)`` payload stores 0, which is the ``8'h0`` the
    shipped Verilog has in the ``MSelect_AOTHER`` arms.
    """
    return v.sat(sat_max=255, sat_min=0).fix_to(_DEAD_FACTOR_FORMAT)


def clamp_afix_to_u8(v: AFixValue):
    """``def clampAFixToU8(v: AFix): UInt``.

    ``v.sat(255, 0, exp = 0 exp)`` clamps at integer resolution (the raw
    bound is ``255 * 256 + 255``, i.e. anything below 256.0 survives), then
    ``fixTo(UQ(8, 0))`` floors the fraction away and ``.asUInt`` reads the
    8-bit unsigned raw.
    """
    saturated = v.sat(sat_max=255, sat_min=0, exp=0)
    return saturated.fix_to(_U8_FORMAT).as_unsigned()


def invert_factor(m: Module, f: AFixValue, reverse_blend, *, name="invertFactor") -> AFixValue:
    """``def invertFactor(f: AFix, reverseBlend: Bool): AFix``.

    Voodoo1 convention (86Box): ``factor ^= 0xFF`` when ``!reverseBlend``,
    then ``factor += 1`` always, so the factor is 1..256 and 256/256 is an
    exact 1.0.  Widened to ``AFix.UQ(1 bits, 8 bits)`` to hold the 256.

    ``xored`` is a combinational ``UInt(8 bits)`` driven from a
    ``when/otherwise``, so the helper needs the caller's :class:`Module`.
    """
    f_raw = f.raw.as_unsigned()[0:8]                  # `f.raw.asUInt.resize(8 bits)`
    xored = Signal(8, name=f"{name}_xored")
    with m.If(reverse_blend):
        m.d.comb += xored.eq(f_raw)                    # reverse_blend=1: pass through
    with m.Else():
        m.d.comb += xored.eq(~f_raw)                   # reverse_blend=0: invert (XOR 0xFF)
    # `result.raw := (xored.resize(9 bits) + 1).asBits` -- +1 unconditionally
    return AFixValue.from_raw(FACTOR_WIDE_FORMAT, (xored + 1)[0:9])


# ---------------------------------------------------------------------------
# case class ColorCombine(c: Config) extends Component
# ---------------------------------------------------------------------------


class ColorCombine(wiring.Component):
    """``case class ColorCombine(c: voodoo.Config) extends Component``.

    ``self.report`` (a :class:`~avoodoo.lib.pipeline.PipelineReport`) is set
    by :meth:`elaborate`, so a test can check the register census after
    building the module.
    """

    def __init__(self, c: Config | None = None) -> None:
        c = c if c is not None else Config.voodoo1()
        self.c = c
        self.report = None
        super().__init__({
            "input": In(stream.Signature(ColorCombineInput(c))),
            "output": Out(stream.Signature(ColorCombineOutput(c))),
        })

    # -- Payload declarations (ColorCombine.scala:174-238) ------------------

    def _declare_payloads(self):
        c = self.c
        P = Payload
        p = {}
        p["COORDS"] = P(PixelCoords(c), name="COORDS")
        p["DEPTH"] = P(AFixShape.from_q(c.v_depth_format), name="DEPTH")
        p["ITERATED_ALPHA_PT"] = P(unsigned(8), name="ITERATED_ALPHA_PT")
        p["RAW_W"] = P(signed(32), name="RAW_W")
        p["CONFIG"] = P(ColorCombineConfig(), name="CONFIG")

        # Fog colour pass-through (per-triangle, travels with the pixels)
        p["FOG_COLOR"] = P(unsigned(32), name="FOG_COLOR")
        p["CHROMA_KEY"] = P(unsigned(32), name="CHROMA_KEY")
        p["ZA_COLOR"] = P(unsigned(32), name="ZA_COLOR")

        # Per-triangle FIFO registers pass-through
        p["ALPHA_MODE"] = P(ALPHA_MODE_LAYOUT, name="ALPHA_MODE")
        p["FOG_MODE"] = P(FOG_MODE_LAYOUT, name="FOG_MODE")
        p["FBZ_MODE"] = P(FbzMode(), name="FBZ_MODE")
        p["DRAW_COLOR_BUFFER_BASE"] = P(unsigned(c.address_width), name="DRAW_COLOR_BUFFER_BASE")
        p["DRAW_AUX_BUFFER_BASE"] = P(unsigned(c.address_width), name="DRAW_AUX_BUFFER_BASE")
        p["FB_PIXEL_STRIDE"] = P(unsigned(11), name="FB_PIXEL_STRIDE")
        if c.trace.enabled:
            p["TRACE"] = P(TRACE_PIXEL_KEY_LAYOUT, name="TRACE")

        # Colour payloads at various precisions
        p["C_OTHER"] = P(Color.s9(), name="C_OTHER")
        p["C_LOCAL"] = P(Color.s9(), name="C_LOCAL")
        p["A_OTHER"] = P(signed(9), name="A_OTHER")
        p["A_LOCAL"] = P(signed(9), name="A_LOCAL")

        p["TEXTURE_ALPHA"] = P(signed(9), name="TEXTURE_ALPHA")
        p["TEXTURE_RGB"] = P(Color.s9(), name="TEXTURE_RGB")

        # After zero/sub operations
        p["C_AFTER_ZERO"] = P(Color.s9(), name="C_AFTER_ZERO")
        p["A_AFTER_ZERO"] = P(signed(9), name="A_AFTER_ZERO")
        p["C_AFTER_SUB"] = P(_color(AFTER_SUB_FORMAT), name="C_AFTER_SUB")
        p["A_AFTER_SUB"] = P(AFTER_SUB_FORMAT, name="A_AFTER_SUB")

        # Multiply factors (before and after reverse blend)
        p["C_FACTOR_PRE"] = P(_color(FACTOR_FORMAT), name="C_FACTOR_PRE")   # Color.ufactor()
        p["A_FACTOR_PRE"] = P(FACTOR_FORMAT, name="A_FACTOR_PRE")
        p["C_FACTOR"] = P(_color(FACTOR_WIDE_FORMAT), name="C_FACTOR")
        p["A_FACTOR"] = P(FACTOR_WIDE_FORMAT, name="A_FACTOR")

        # After multiply/add: SQ(10,0) * UQ(0,8) -> SQ(10,8)
        p["C_AFTER_MUL"] = P(_color(COLOR_MUL_FORMAT), name="C_AFTER_MUL")
        p["A_AFTER_MUL"] = P(COLOR_MUL_FORMAT, name="A_AFTER_MUL")
        p["C_AFTER_ADD"] = P(_color(COLOR_MUL_FORMAT), name="C_AFTER_ADD")
        p["A_AFTER_ADD"] = P(COLOR_MUL_FORMAT, name="A_AFTER_ADD")

        # Final clamped values
        p["C_CLAMPED"] = P(Color.u8(), name="C_CLAMPED")
        p["A_CLAMPED"] = P(unsigned(8), name="A_CLAMPED")
        return p

    # -- the module -----------------------------------------------------------

    def elaborate(self, platform):
        m = Module()
        c = self.c
        P = self._declare_payloads()
        self.payloads = P

        def u8_color_to_s9(dst, src):
            """``def u8ColorToS9(dst: Color[SInt], src: Color[UInt])``."""
            return [d.eq(u8_to_s9(s)) for d, s in zip(channels(dst), channels(src))]

        # ---- loadPassThrough / storePassThrough (:247-265, :330-347) -------

        def load_pass_through(node, payload):
            stmts = [
                node(P["COORDS"]).eq(payload.coords),
                node(P["DEPTH"]).eq(payload.depth),
                node(P["ITERATED_ALPHA_PT"]).eq(payload.iterated_alpha),
                node(P["RAW_W"]).eq(payload.raw_w),
                node(P["CONFIG"]).eq(payload.config),
                node(P["FOG_COLOR"]).eq(payload.fog_color),
                node(P["CHROMA_KEY"]).eq(payload.chroma_key),
                node(P["ZA_COLOR"]).eq(payload.za_color),
                node(P["ALPHA_MODE"]).eq(payload.alpha_mode),
                node(P["FOG_MODE"]).eq(payload.fog_mode),
                node(P["FBZ_MODE"]).eq(payload.fbz_mode),
                node(P["DRAW_COLOR_BUFFER_BASE"]).eq(payload.routing.color_base_addr),
                node(P["DRAW_AUX_BUFFER_BASE"]).eq(payload.routing.aux_base_addr),
                node(P["FB_PIXEL_STRIDE"]).eq(payload.routing.pixel_stride),
            ]
            if c.trace.enabled:
                stmts.append(node(P["TRACE"]).eq(payload.trace))
            return stmts

        def store_pass_through(payload, node):
            stmts = [
                payload.coords.eq(node(P["COORDS"])),
                payload.depth.eq(node(P["DEPTH"])),
                payload.iterated_alpha.eq(node(P["ITERATED_ALPHA_PT"])),
                payload.raw_w.eq(node(P["RAW_W"])),
                payload.fog_color.eq(node(P["FOG_COLOR"])),
                payload.chroma_key.eq(node(P["CHROMA_KEY"])),
                payload.za_color.eq(node(P["ZA_COLOR"])),
                payload.alpha_mode.eq(node(P["ALPHA_MODE"])),
                payload.fog_mode.eq(node(P["FOG_MODE"])),
                payload.fbz_mode.eq(node(P["FBZ_MODE"])),
                payload.routing.color_base_addr.eq(node(P["DRAW_COLOR_BUFFER_BASE"])),
                payload.routing.aux_base_addr.eq(node(P["DRAW_AUX_BUFFER_BASE"])),
                payload.routing.pixel_stride.eq(node(P["FB_PIXEL_STRIDE"])),
            ]
            if c.trace.enabled:
                stmts.append(payload.trace.eq(node(P["TRACE"])))
            return stmts

        # ---- the four selects (:267-328) -----------------------------------
        # Each `switch` lists every enumerant; Spinal emits the last `is` as
        # the Verilog `default`, and so do these (`m.Default()` is the last
        # enumerant, never a separate "else 0").

        def select_rgb_other(node, payload):
            c_other = node(P["C_OTHER"])
            with m.Switch(payload.config.rgb_sel):
                with m.Case(RgbSel.ITERATED):
                    m.d.comb += u8_color_to_s9(c_other, payload.iterated)
                with m.Case(RgbSel.TEXTURE):
                    m.d.comb += u8_color_to_s9(c_other, payload.texture)
                with m.Case(RgbSel.COLOR1):
                    m.d.comb += u8_color_to_s9(c_other, payload.color1)
                with m.Default():                      # RgbSel.LFB
                    m.d.comb += [ch.eq(0) for ch in channels(c_other)]

        def select_rgb_local(node, payload):
            c_local = node(P["C_LOCAL"])
            with m.If(payload.config.local_select_override & payload.texture_alpha[7]):
                m.d.comb += u8_color_to_s9(c_local, payload.color0)
            with m.Else():
                with m.Switch(payload.config.local_select):
                    with m.Case(LocalSel.ITERATED):
                        m.d.comb += u8_color_to_s9(c_local, payload.iterated)
                    with m.Default():                  # LocalSel.COLOR0
                        m.d.comb += u8_color_to_s9(c_local, payload.color0)

        def select_alpha_other(node, payload):
            a_other = node(P["A_OTHER"])
            with m.Switch(payload.config.alpha_sel):
                with m.Case(AlphaSel.ITERATED):
                    m.d.comb += a_other.eq(u8_to_s9(payload.iterated_alpha))
                with m.Case(AlphaSel.TEXTURE):
                    m.d.comb += a_other.eq(u8_to_s9(payload.texture_alpha))
                with m.Case(AlphaSel.COLOR1):
                    m.d.comb += a_other.eq(u8_to_s9(payload.color1_alpha))
                with m.Default():                      # AlphaSel.LFB
                    m.d.comb += a_other.eq(0)

        def select_alpha_local(node, payload):
            a_local = node(P["A_LOCAL"])
            with m.Switch(payload.config.alpha_local_select):
                with m.Case(AlphaLocalSel.ITERATED):
                    m.d.comb += a_local.eq(u8_to_s9(payload.iterated_alpha))
                with m.Case(AlphaLocalSel.COLOR0):
                    m.d.comb += a_local.eq(u8_to_s9(payload.color0_alpha))
                with m.Default():                      # AlphaLocalSel.ITERATED_Z
                    m.d.comb += a_local.eq(u8_to_s9(payload.iterated_z))

        # ---- nodes and links (:374-387) ------------------------------------
        n = [Node(m, f"n{k}") for k in range(9)]
        n0, n1, n2, n3, n4, n5, n6, n7, n8 = n
        links = [StageLink(n[k], n[k + 1]) for k in range(8)]

        # ---- n0.driveFrom(io.input) (:390-401) -----------------------------
        def drive_in(node, payload):
            m.d.comb += load_pass_through(node, payload)
            # Convert texture inputs to the internal 9-bit signed format
            m.d.comb += node(P["TEXTURE_ALPHA"]).eq(u8_to_s9(payload.texture_alpha))
            m.d.comb += u8_color_to_s9(node(P["TEXTURE_RGB"]), payload.texture)
            select_rgb_other(node, payload)
            select_rgb_local(node, payload)
            select_alpha_other(node, payload)
            select_alpha_local(node, payload)

        n0.drive_from(self.input, drive_in)

        # ---- Stage 1: Zero Other (:403-407) --------------------------------
        cfg1 = n1(P["CONFIG"])
        for dst, src in zip(channels(n1(P["C_AFTER_ZERO"])), channels(n1(P["C_OTHER"]))):
            m.d.comb += dst.eq(Mux(cfg1.zero_other, Const(0, signed(9)), src))
        m.d.comb += n1(P["A_AFTER_ZERO"]).eq(
            Mux(cfg1.alpha_zero_other, Const(0, signed(9)), n1(P["A_OTHER"])))

        # ---- Stage 2: Subtract Local (:409-423) ----------------------------
        # `zero - local` is SInt(9) - SInt(9) = SInt(9) in Spinal (no growth);
        # `AFix(sint)` then takes its bounds from that 9-bit width.
        def sint9_sub(a, b):
            return (a - b)[0:9].as_signed()

        cfg2 = n2(P["CONFIG"])
        for dst, zero, local in zip(channels(n2(P["C_AFTER_SUB"])),
                                    channels(n2(P["C_AFTER_ZERO"])),
                                    channels(n2(P["C_LOCAL"]))):
            with m.If(cfg2.sub_clocal):
                m.d.comb += dst.eq(AFixValue.from_sint(sint9_sub(zero, local)))
            with m.Else():
                m.d.comb += dst.eq(AFixValue.from_sint(zero))
        with m.If(cfg2.alpha_sub_clocal):
            m.d.comb += n2(P["A_AFTER_SUB"]).eq(
                AFixValue.from_sint(sint9_sub(n2(P["A_AFTER_ZERO"]), n2(P["A_LOCAL"]))))
        with m.Else():
            m.d.comb += n2(P["A_AFTER_SUB"]).eq(AFixValue.from_sint(n2(P["A_AFTER_ZERO"])))

        # ---- Stage 3: Select Multiply Factor (:425-477) --------------------
        zero_factor = FACTOR_FORMAT.const(0)       # `zeroFactor := 0.0`
        cfg3 = n3(P["CONFIG"])
        c_factor_pre = channels(n3(P["C_FACTOR_PRE"]))

        def all_channels(value):
            return [dst.eq(value) for dst in c_factor_pre]

        with m.Switch(cfg3.mselect):
            with m.Case(MSelect.ZERO):
                m.d.comb += all_channels(zero_factor)
            with m.Case(MSelect.CLOCAL):
                m.d.comb += [dst.eq(clamp_sint_to_factor(src))
                             for dst, src in zip(c_factor_pre, channels(n3(P["C_LOCAL"])))]
            with m.Case(MSelect.AOTHER):
                # §8.1: constant zero, on purpose.
                m.d.comb += all_channels(clamp_afix_to_factor(n3(P["A_AFTER_SUB"])))
            with m.Case(MSelect.ALOCAL):
                m.d.comb += all_channels(clamp_sint_to_factor(n3(P["A_LOCAL"])))
            with m.Case(MSelect.TEXTURE_ALPHA):
                m.d.comb += all_channels(clamp_sint_to_factor(n3(P["TEXTURE_ALPHA"])))
            with m.Default():                          # MSelect.TEXTURE_RGB
                m.d.comb += [dst.eq(clamp_sint_to_factor(src))
                             for dst, src in zip(c_factor_pre, channels(n3(P["TEXTURE_RGB"])))]

        a_factor_pre = n3(P["A_FACTOR_PRE"])
        with m.Switch(cfg3.alpha_mselect):
            with m.Case(MSelect.ZERO):
                m.d.comb += a_factor_pre.eq(zero_factor)
            with m.Case(MSelect.CLOCAL):
                # The Scala really does read A_LOCAL here, not C_LOCAL.
                m.d.comb += a_factor_pre.eq(clamp_sint_to_factor(n3(P["A_LOCAL"])))
            with m.Case(MSelect.AOTHER):
                m.d.comb += a_factor_pre.eq(clamp_afix_to_factor(n3(P["A_AFTER_SUB"])))
            with m.Case(MSelect.ALOCAL):
                m.d.comb += a_factor_pre.eq(clamp_sint_to_factor(n3(P["A_LOCAL"])))
            with m.Case(MSelect.TEXTURE_ALPHA):
                m.d.comb += a_factor_pre.eq(clamp_sint_to_factor(n3(P["TEXTURE_ALPHA"])))
            with m.Default():                          # MSelect.TEXTURE_RGB
                m.d.comb += a_factor_pre.eq(clamp_sint_to_factor(n3(P["TEXTURE_ALPHA"])))

        # ---- Stage 4: Reverse Blend (:479-505) -----------------------------
        cfg4 = n4(P["CONFIG"])

        for name, dst, src in zip(("r", "g", "b"), channels(n4(P["C_FACTOR"])),
                                  channels(n4(P["C_FACTOR_PRE"]))):
            m.d.comb += dst.eq(invert_factor(m, src, cfg4.reverse_blend, name=f"n4_C_FACTOR_{name}"))
        m.d.comb += n4(P["A_FACTOR"]).eq(
            invert_factor(m, n4(P["A_FACTOR_PRE"]), cfg4.alpha_reverse_blend, name="n4_A_FACTOR"))

        # ---- Stage 5: Multiply (:507-513) ----------------------------------
        # AFix(SQ(10,0)) * AFix(UQ(1,8)) -> AFix(SQ(10,8)), saturated by fixTo.
        for dst, sub, factor in zip(channels(n5(P["C_AFTER_MUL"])),
                                    channels(n5(P["C_AFTER_SUB"])),
                                    channels(n5(P["C_FACTOR"]))):
            m.d.comb += dst.eq((sub * factor).fix_to(COLOR_MUL_FORMAT))
        m.d.comb += n5(P["A_AFTER_MUL"]).eq(
            (n5(P["A_AFTER_SUB"]) * n5(P["A_FACTOR"])).fix_to(COLOR_MUL_FORMAT))

        # ---- Stage 6: Add (:515-550) ---------------------------------------
        cfg6 = n6(P["CONFIG"])

        def local_afix(local):
            """``AFix(local).fixTo(colorMulFormat)`` -- the integer shifted up 8."""
            return AFixValue.from_sint(local).fix_to(COLOR_MUL_FORMAT)

        c_after_add = channels(n6(P["C_AFTER_ADD"]))
        c_after_mul = channels(n6(P["C_AFTER_MUL"]))
        with m.Switch(cfg6.add):
            with m.Case(AddMode.NONE):
                m.d.comb += [dst.eq(mul) for dst, mul in zip(c_after_add, c_after_mul)]
            with m.Case(AddMode.CLOCAL):
                for dst, mul, local in zip(c_after_add, c_after_mul, channels(n6(P["C_LOCAL"]))):
                    m.d.comb += dst.eq((mul + local_afix(local)).fix_to(COLOR_MUL_FORMAT))
            with m.Default():                          # AddMode.ALOCAL
                a_local_afix = local_afix(n6(P["A_LOCAL"]))
                for dst, mul in zip(c_after_add, c_after_mul):
                    m.d.comb += dst.eq((mul + a_local_afix).fix_to(COLOR_MUL_FORMAT))

        a_after_add = n6(P["A_AFTER_ADD"])
        a_after_mul = n6(P["A_AFTER_MUL"])
        with m.Switch(cfg6.alpha_add):
            with m.Case(AddMode.NONE):
                m.d.comb += a_after_add.eq(a_after_mul)
            with m.Case(AddMode.CLOCAL):
                m.d.comb += a_after_add.eq(
                    (a_after_mul + local_afix(n6(P["A_LOCAL"]))).fix_to(COLOR_MUL_FORMAT))
            with m.Default():                          # AddMode.ALOCAL
                m.d.comb += a_after_add.eq(
                    (a_after_mul + local_afix(n6(P["A_LOCAL"]))).fix_to(COLOR_MUL_FORMAT))

        # ---- Stage 7: Clamp (:552-556) -------------------------------------
        for dst, src in zip(channels(n7(P["C_CLAMPED"])), channels(n7(P["C_AFTER_ADD"]))):
            m.d.comb += dst.eq(clamp_afix_to_u8(src))
        m.d.comb += n7(P["A_CLAMPED"]).eq(clamp_afix_to_u8(n7(P["A_AFTER_ADD"])))

        # ---- n8.driveTo(io.output) + Stage 8: Invert Output (:558-567) -----
        def drive_out(payload, node):
            m.d.comb += store_pass_through(payload, node)
            cfg8 = node(P["CONFIG"])
            for dst, src in zip(channels(payload.color), channels(node(P["C_CLAMPED"]))):
                m.d.comb += dst.eq(Mux(cfg8.invert_output, ~src, src))
            a = node(P["A_CLAMPED"])
            m.d.comb += payload.alpha.eq(Mux(cfg8.alpha_invert_output, ~a, a))

        n8.drive_to(self.output, drive_out)

        # ---- Builder(links) (:570) -----------------------------------------
        self.report = Builder(links)
        self.nodes = n
        self.links = links
        return m
