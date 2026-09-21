"""Shared record/bundle layouts.

Home for the ``Bundle`` types that more than one ported module needs, as
``amaranth.lib.data`` layouts.  Scala sources, in the order they appear below:

* ``ext/SpinalVoodoo/src/voodoo/RenderConfig.scala`` -- the six
  ``SpinalEnum``s and ``AlphaMode`` / ``FbzColorPath`` / ``FogMode``
* ``ext/SpinalVoodoo/src/voodoo/Trace.scala`` -- ``Trace.PrimitiveKey`` /
  ``Trace.PixelKey``
* ``ext/SpinalVoodoo/src/voodoo/Bundles.scala`` -- ``Coefficients``
* ``ext/SpinalVoodoo/src/voodoo/texture/TexLayoutTables.scala`` --
  ``TexLayoutTables.Tables``
* ``ext/SpinalVoodoo/src/voodoo/raster/TriangleSetup.scala`` --
  ``PixelPipelineConfig``, ``TmuConfig``, ``HiAlpha``, ``HiTexCoords``,
  ``PerTriangleConfig``, ``Input``, ``Output``
* ``ext/SpinalVoodoo/src/voodoo/raster/Rasterizer.scala`` --
  ``InputGradient``, ``GradientBundle`` (here rather than in
  ``raster/rasterizer.py`` only because ``TriangleSetup.Output`` embeds them
  and the import has to run one way)

Two conventions, both load-bearing:

1. **Field names are ``tools.diff.naming.camel_to_snake`` of the Spinal
   names.**  That function is what the lockstep diff harness (plan §4.2 step
   4) uses to pair a Spinal Verilog port with an Amaranth signature leaf, so
   any other spelling needs a hand-written override.  It produces some ugly
   ones and they are deliberate: ``dSdX`` -> ``d_sd_x``, ``tmudTdY`` ->
   ``tmud_td_y``, ``tLOD`` -> ``t_lod``, ``tmuTLOD`` -> ``tmu_tlod``,
   ``clipLowY`` -> ``clip_low_y``, ``aSrcFact`` -> ``a_src_fact``,
   ``hiS`` -> ``hi_s``.  ``tests/test_rasterizer.py`` checks the whole
   ``Rasterizer`` surface against the committed port map, which is the real
   guard.

2. **Declaration order is Spinal's declaration order.**  Spinal packs the
   first-declared Bundle field at the LSB and ``Vec`` index 0 at the LSB, and
   so does ``data.StructLayout`` / ``data.ArrayLayout``, so a layout written
   top to bottom against the Scala has the same bit packing.  Nothing in the
   diff harness depends on that (it pairs by name), but the Verilator Glide
   harness and any ``.raw`` bit-punning will.

``trace`` fields exist only when ``c.trace.enabled``; the Scala writes
``if (c.trace.enabled) Trace.PrimitiveKey() else null`` and Spinal drops a
``null`` Bundle field, so the layouts do the same.
"""

from __future__ import annotations

from amaranth.hdl import unsigned
from amaranth.lib import data

from avoodoo.config import Config, QFormat
from avoodoo.lib.fixed import AFixShape
from avoodoo.vertex import triangle_with_sign_layout, vertex2d

__all__ = [
    "RGB_SEL",
    "ALPHA_SEL",
    "LOCAL_SEL",
    "ALPHA_LOCAL_SEL",
    "MSELECT",
    "ADD_MODE",
    "RgbSel",
    "AlphaSel",
    "LocalSel",
    "AlphaLocalSel",
    "MSelect",
    "AddMode",
    "TraceOrigin",
    "ALPHA_MODE_LAYOUT",
    "FBZ_COLOR_PATH_LAYOUT",
    "FOG_MODE_LAYOUT",
    "TRACE_PRIMITIVE_KEY_LAYOUT",
    "TRACE_PIXEL_KEY_LAYOUT",
    "coefficients_layout",
    "tex_layout_tables_layout",
    "pixel_pipeline_config_layout",
    "tmu_config_layout",
    "hi_alpha_layout",
    "hi_tex_coords_layout",
    "per_triangle_config_layout",
    "input_gradient_layout",
    "gradient_bundle_layout",
    "triangle_setup_output_layout",
    "triangle_setup_input_layout",
    "vertex2d",
]


def _afix(fmt: QFormat) -> AFixShape:
    return AFixShape.from_q(fmt)


# ---------------------------------------------------------------------------
# RenderConfig.scala -- SpinalEnum widths
#
# Every one of these is a plain `SpinalEnum` with the default (native/binary)
# encoding, so the width is `log2Up(elementCount)` and the values are the
# declaration indices.  The widths below were cross-checked against the
# generated `fpga/rtl/diff/TriangleSetup_spinal.v` port list.
# ---------------------------------------------------------------------------

RGB_SEL = unsigned(2)          # RgbSel:         ITERATED TEXTURE COLOR1 LFB
ALPHA_SEL = unsigned(2)        # AlphaSel:       ITERATED TEXTURE COLOR1 LFB
LOCAL_SEL = unsigned(1)        # LocalSel:       ITERATED COLOR0
ALPHA_LOCAL_SEL = unsigned(2)  # AlphaLocalSel:  ITERATED COLOR0 ITERATED_Z
MSELECT = unsigned(3)          # MSelect:        ZERO CLOCAL AOTHER ALOCAL TEXTURE_ALPHA TEXTURE_RGB
ADD_MODE = unsigned(2)         # AddMode:        NONE CLOCAL ALOCAL


class RgbSel:
    ITERATED, TEXTURE, COLOR1, LFB = range(4)


class AlphaSel:
    ITERATED, TEXTURE, COLOR1, LFB = range(4)


class LocalSel:
    ITERATED, COLOR0 = range(2)


class AlphaLocalSel:
    ITERATED, COLOR0, ITERATED_Z = range(3)


class MSelect:
    ZERO, CLOCAL, AOTHER, ALOCAL, TEXTURE_ALPHA, TEXTURE_RGB = range(6)


class AddMode:
    NONE, CLOCAL, ALOCAL = range(3)


# ---------------------------------------------------------------------------
# RenderConfig.scala -- register-shaped bundles
# ---------------------------------------------------------------------------

#: ``AlphaMode()`` -- SST-1 register 0x10C.
ALPHA_MODE_LAYOUT = data.StructLayout({
    "alpha_test_enable": 1,    # [0]
    "alpha_func": unsigned(3),  # [3:1]
    "alpha_blend_enable": 1,   # [4]
    "enable_anti_alias": 1,    # [5]
    "reserved1": unsigned(2),  # [7:6]
    "rgb_src_fact": unsigned(4),   # [11:8]
    "rgb_dst_fact": unsigned(4),   # [15:12]
    "a_src_fact": unsigned(4),     # [19:16]
    "a_dst_fact": unsigned(4),     # [23:20]
    "alpha_ref": unsigned(8),      # [31:24]
})

#: ``FbzColorPath()`` -- SST-1 register 0x104.
FBZ_COLOR_PATH_LAYOUT = data.StructLayout({
    "rgb_sel": RGB_SEL,                  # [1:0]
    "alpha_sel": ALPHA_SEL,              # [3:2]
    "local_select": LOCAL_SEL,           # [4]
    "alpha_local_select": ALPHA_LOCAL_SEL,  # [6:5]
    "local_select_override": 1,          # [7]
    "zero_other": 1,                     # [8]
    "sub_clocal": 1,                     # [9]
    "mselect": MSELECT,                  # [12:10]
    "reverse_blend": 1,                  # [13]
    "add": ADD_MODE,                     # [15:14]
    "invert_output": 1,                  # [16]
    "alpha_zero_other": 1,               # [17]
    "alpha_sub_clocal": 1,               # [18]
    "alpha_mselect": MSELECT,            # [21:19]
    "alpha_reverse_blend": 1,            # [22]
    "alpha_add": ADD_MODE,               # [24:23]
    "alpha_invert_output": 1,            # [25]
    "param_adjust": 1,                   # [26]
    "texture_enable": 1,                 # [27]
})

#: ``FogMode()`` -- SST-1 register 0x108.
FOG_MODE_LAYOUT = data.StructLayout({
    "fog_enable": 1,                  # [0]
    "fog_add": 1,                     # [1]
    "fog_mult": 1,                    # [2]
    "fog_mode_select": unsigned(2),   # [4:3]
    "fog_constant": 1,                # [5]
})


# ---------------------------------------------------------------------------
# Trace.scala
# ---------------------------------------------------------------------------


class TraceOrigin:
    """``Trace.Origin``."""

    triangle = 0
    fastfill = 1
    lfb = 2


#: ``Trace.PrimitiveKey()``.
TRACE_PRIMITIVE_KEY_LAYOUT = data.StructLayout({
    "valid": 1,
    "origin": unsigned(2),
    "draw_id": unsigned(32),
    "primitive_id": unsigned(32),
})

#: ``Trace.PixelKey()``.
TRACE_PIXEL_KEY_LAYOUT = data.StructLayout({
    "primitive": TRACE_PRIMITIVE_KEY_LAYOUT,
    "pixel_seq": unsigned(24),
})


# ---------------------------------------------------------------------------
# Bundles.scala
# ---------------------------------------------------------------------------


def coefficients_layout(c: Config) -> data.StructLayout:
    """``Coefficients(config)`` -- the three edge coefficients ``a``/``b``/``c``."""
    fmt = _afix(c.coefficient_format)
    return data.StructLayout({"a": fmt, "b": fmt, "c": fmt})


# ---------------------------------------------------------------------------
# TexLayoutTables.scala
# ---------------------------------------------------------------------------


def tex_layout_tables_layout() -> data.StructLayout:
    """``TexLayoutTables.Tables()`` -- nine LOD entries each, LOD 0 first.

    The pure-Python model of the same table (``TexLayoutTables.compute``) is
    ``avoodoo/texture/tex_layout_tables.py``; this is only the wire shape.
    """
    return data.StructLayout({
        "tex_base": data.ArrayLayout(unsigned(22), 9),
        "tex_end": data.ArrayLayout(unsigned(22), 9),
        "tex_shift": data.ArrayLayout(unsigned(4), 9),
    })


# ---------------------------------------------------------------------------
# TriangleSetup.scala -- the config bundles the raster path carries per pixel
# ---------------------------------------------------------------------------


def pixel_pipeline_config_layout(c: Config) -> data.StructLayout:
    """``TriangleSetup.PixelPipelineConfig(c)``."""
    return data.StructLayout({
        "fbz_color_path": FBZ_COLOR_PATH_LAYOUT,
        "fog_mode": FOG_MODE_LAYOUT,
        "alpha_mode": ALPHA_MODE_LAYOUT,
    })


def tmu_config_layout(c: Config) -> data.StructLayout:
    """``TriangleSetup.TmuConfig(c)``.

    ``texTables`` is present only when ``c.packedTexLayout`` (true for
    ``Config.voodoo1()``), matching the Scala's ``else null``.
    """
    tex = _afix(c.tex_coords_format)
    fields = {
        "texture_enable": 1,
        "texture_mode": unsigned(12),
        "tex_base_addr": unsigned(24),
        "tex_base_addr1": unsigned(24),
        "tex_base_addr2": unsigned(24),
        "tex_base_addr38": unsigned(24),
        "t_lod": unsigned(27),
        "d_sd_x": tex,
        "d_td_x": tex,
        "d_sd_y": tex,
        "d_td_y": tex,
    }
    if c.packed_tex_layout:
        fields["tex_tables"] = tex_layout_tables_layout()
    return data.StructLayout(fields)


def hi_alpha_layout(c: Config) -> data.StructLayout:
    """``TriangleSetup.HiAlpha(c)``."""
    hi = _afix(c.tex_coords_hi_format)
    return data.StructLayout({"start": hi, "d_ad_x": hi, "d_ad_y": hi})


def hi_tex_coords_layout(c: Config) -> data.StructLayout:
    """``TriangleSetup.HiTexCoords(c)``."""
    hi = _afix(c.tex_coords_hi_format)
    return data.StructLayout({
        "s_start": hi,
        "t_start": hi,
        "d_sd_x": hi,
        "d_td_x": hi,
        "d_sd_y": hi,
        "d_td_y": hi,
    })


def per_triangle_config_layout(c: Config) -> data.StructLayout:
    """``TriangleSetup.PerTriangleConfig(c)`` -- captured at triangle-command time."""
    tex = _afix(c.tex_coords_format)
    fields = {
        "fbz_color_path": FBZ_COLOR_PATH_LAYOUT,
        "fog_mode": FOG_MODE_LAYOUT,
        "alpha_mode": ALPHA_MODE_LAYOUT,
        "enable_clipping": 1,
        "clip_left": unsigned(10),
        "clip_right": unsigned(10),
        "clip_low_y": unsigned(10),
        "clip_high_y": unsigned(10),
        "tmu_texture_mode": unsigned(12),
        "tmu_tex_base_addr": unsigned(24),
        "tmu_tex_base_addr1": unsigned(24),
        "tmu_tex_base_addr2": unsigned(24),
        "tmu_tex_base_addr38": unsigned(24),
        "tmu_tlod": unsigned(27),
        "tmud_sd_x": tex,
        "tmud_td_x": tex,
        "tmud_sd_y": tex,
        "tmud_td_y": tex,
    }
    if c.packed_tex_layout:
        fields["tex_tables"] = tex_layout_tables_layout()
    return data.StructLayout(fields)


# ---------------------------------------------------------------------------
# Rasterizer.scala -- the gradient bundles, here because TriangleSetup.Output
# embeds them (Scala has no import cycle: both live in package voodoo.raster)
# ---------------------------------------------------------------------------


def input_gradient_layout(fmt: QFormat) -> data.StructLayout:
    """``Rasterizer.InputGradient(fmt)`` -- a start value and a 2D gradient."""
    afix = _afix(fmt)
    return data.StructLayout({"start": afix, "d": vertex2d(fmt)})


def gradient_bundle_layout(c: Config, mk=None) -> data.StructLayout:
    """``Rasterizer.GradientBundle(mk, c)``.

    ``mk`` maps a :class:`QFormat` to the per-gradient shape, exactly like the
    Scala's ``mk: QFormat => T``.  It defaults to ``AFix(_)``, which is what
    ``Rasterizer.Output`` uses; ``TriangleSetup.Input``/``Output`` pass
    ``Rasterizer.InputGradient(_)``.

    The eight gradients, in declaration order, and the *order* ``GradientBundle.all``
    yields them in -- the core indexes that sequence positionally (``take(6)``
    in ``SpanWalker.initTriangleConst``, ``lowGradFormats(gradIdx)`` in
    ``Rasterizer.SpanRasterizer``), so it is part of the contract.
    """
    if mk is None:
        mk = _afix
    return data.StructLayout({
        "red_grad": mk(c.v_color_format),
        "green_grad": mk(c.v_color_format),
        "blue_grad": mk(c.v_color_format),
        "depth_grad": mk(c.v_depth_format),
        "alpha_grad": mk(c.v_color_format),
        "w_grad": mk(c.w_accum_format),
        "s_grad": mk(c.tex_coords_accum_format),
        "t_grad": mk(c.tex_coords_accum_format),
    })


#: ``GradientBundle.all`` -- the field names in the order the Scala's ``all``
#: returns them.  Several modules zip this against a positional format list.
GRADIENT_BUNDLE_ALL = (
    "red_grad", "green_grad", "blue_grad", "depth_grad",
    "alpha_grad", "w_grad", "s_grad", "t_grad",
)


def triangle_setup_output_layout(c: Config) -> data.StructLayout:
    """``TriangleSetup.Output(c)`` -- also ``Rasterizer.Input``."""
    fields = {
        "coeffs": data.ArrayLayout(coefficients_layout(c), 3),
        "xrange": vertex2d(c.vertex_format),
        "yrange": vertex2d(c.vertex_format),
        "edge_start": data.ArrayLayout(_afix(c.coefficient_format), 3),
        "grads": gradient_bundle_layout(c, lambda fmt: input_gradient_layout(fmt)),
        "hi_alpha": hi_alpha_layout(c),
        "tex_hi": hi_tex_coords_layout(c),
        "config": per_triangle_config_layout(c),
    }
    if c.trace.enabled:
        fields["trace"] = TRACE_PRIMITIVE_KEY_LAYOUT
    return data.StructLayout(fields)


# ---------------------------------------------------------------------------
# TriangleSetup.scala -- the input side (appended by the triangle_setup port;
# the Output half above is the rasterizer's).  Keep the two adjacent: they
# differ only in `triWithSign` vs the computed `coeffs`/`xrange`/`yrange`/
# `edgeStart`, and the module copies the shared fields across one for one.
# ---------------------------------------------------------------------------


def triangle_setup_input_layout(c: Config) -> data.StructLayout:
    """``TriangleSetup.Input(c)`` (``raster/TriangleSetup.scala:477-484``).

    Triangle with gradients and the render config captured at command time.
    ``TriangleSetup.buildInput`` (``TriangleSetup.scala:340-375``) is what the
    register bank uses to fill it; the port keeps the field order so the
    packed word matches.
    """
    fields = {
        "tri_with_sign": triangle_with_sign_layout(c.vertex_format),
        "grads": gradient_bundle_layout(c, input_gradient_layout),
        "hi_alpha": hi_alpha_layout(c),
        "tex_hi": hi_tex_coords_layout(c),
        "config": per_triangle_config_layout(c),
    }
    if c.trace.enabled:
        fields["trace"] = TRACE_PRIMITIVE_KEY_LAYOUT
    return data.StructLayout(fields)


# ---------------------------------------------------------------------------
# Bundles.scala + RenderConfig.scala + the pixel-path companion bundles
# (appended by the Fog / Write port; `pixel/fog.py`, `pixel/write.py`).
#
# Imported here rather than at the top of the file so this block stays a
# self-contained append -- several agents are growing this module at once.
# ---------------------------------------------------------------------------

from amaranth import Cat, Const, Mux, signed  # noqa: E402

__all__ += [
    "PixelCoords",
    "FbRouting",
    "Color",
    "channels",
    "expand_to_8",
    "rgb888_color",
    "clamp_to_u8",
    "aligned_word_address",
    "select_word_lane",
    "pack_word_lane",
    "word_lane_mask",
    "FbzMode",
    "pixel_byte_offset",
    "plane_address",
    "FramebufferWriteReq",
    "ColorCombineOutput",
]


def _raw(value):
    """The plain ``Value`` behind a ``data.View`` / ``AFixValue`` / ``Value``."""
    return value.as_value() if hasattr(value, "as_value") else value


class PixelCoords(data.StructLayout):
    """``case class PixelCoords(config: Config)`` (``Bundles.scala``).

    ``x``/``y`` are ``SInt(config.vertexFormat.nonFraction bits)`` -- 12 bits
    for ``Config.voodoo1()``.  Scala's ``coords(0)`` / ``coords(1)`` are
    ``view.x`` / ``view.y``.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        w = c.vertex_format.non_fraction
        super().__init__({"x": signed(w), "y": signed(w)})


class FbRouting(data.StructLayout):
    """``case class FbRouting(config: Config)`` (``Bundles.scala``)."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "color_base_addr": unsigned(c.address_width),
            "aux_base_addr": unsigned(c.address_width),
            "pixel_stride": unsigned(11),
        })


class Color(data.StructLayout):
    """``case class Color[T <: Data](rt, gt, bt)`` (``Bundles.scala``).

    Declared ``b``, ``g``, ``r`` -- the Scala order, which puts ``r`` at the
    high bits, i.e. the standard ``[R][G][B]`` packing.
    """

    def __init__(self, rt, gt, bt) -> None:
        super().__init__({"b": bt, "g": gt, "r": rt})

    @staticmethod
    def u8() -> "Color":
        """``Color.u8()`` -- 8-bit unsigned RGB (0-255 per channel)."""
        return Color(unsigned(8), unsigned(8), unsigned(8))

    @staticmethod
    def s9() -> "Color":
        return Color(signed(9), signed(9), signed(9))

    @staticmethod
    def s10() -> "Color":
        return Color(signed(10), signed(10), signed(10))

    @staticmethod
    def s18() -> "Color":
        return Color(signed(18), signed(18), signed(18))

    @staticmethod
    def rgb565() -> "Color":
        """``def rgb565()`` in the package object."""
        return Color(unsigned(5), unsigned(6), unsigned(5))

    def from_channels(self, r, g, b):
        """``assignFromSeq(Seq(r, g, b))`` on a fresh bundle, as an expression."""
        return self(Cat(_raw(b), _raw(g), _raw(r)))


def channels(color):
    """``Color.channels`` -- ``(r, g, b)``, in that order."""
    return (color.r, color.g, color.b)


def expand_to_8(v, src_width: int):
    """``def expandTo8(v: UInt, srcWidth: Int): UInt``."""
    if not 1 <= src_width <= 8:
        raise ValueError(f"expand_to_8: srcWidth={src_width} must be in 1..8")
    if src_width >= 8:
        return v[0:8]
    if src_width == 1:
        return Mux(v[0], Const(255, 8), Const(0, 8))
    reps = -(-8 // src_width)
    wide = Cat(*([v[0:src_width]] * reps))  # Scala reduces with `@@`
    return wide[len(wide) - 8: len(wide)]


def rgb888_color(bits):
    """``def rgb888Color(bits: Bits): Color[UInt]`` (``assignRgb888``)."""
    return Color.u8().from_channels(bits[16:24], bits[8:16], bits[0:8])


def clamp_to_u8(v):
    """``def clampToU8(v: SInt): UInt`` -- clamp to ``[0, 255]``.

    The Scala is a ``when`` chain driving a ``UInt(8 bits)``; an expression
    here so it composes the way ``map(clampToU8)`` does.
    """
    return Mux(v < 0, Const(0, 8), Mux(v > 255, Const(255, 8), v[0:8]))


def aligned_word_address(address):
    """``def alignedWordAddress(address: UInt)`` -- clear the low two bits."""
    return Cat(Const(0, 2), address[2:])


def select_word_lane(word, lane_hi):
    """``def selectWordLane(word: Bits, laneHi: Bool): UInt``."""
    return Mux(lane_hi, word[16:32], word[0:16])


def pack_word_lane(data_, lane_hi):
    """``def packWordLane(data: Bits, laneHi: Bool): Bits``."""
    return Mux(lane_hi, Cat(Const(0, 16), data_[0:16]), Cat(data_[0:16], Const(0, 16)))


def word_lane_mask(lane_hi):
    """``def wordLaneMask(laneHi: Bool): Bits``."""
    return Mux(lane_hi, Const(0b1100, 4), Const(0b0011, 4))


class FbzMode(data.StructLayout):
    """``case class FbzMode()`` -- SST-1 register 0x110 (``RenderConfig.scala``)."""

    def __init__(self) -> None:
        super().__init__({
            "enable_clipping": unsigned(1),          # [0]
            "enable_chroma_key": unsigned(1),        # [1]
            "enable_stipple": unsigned(1),           # [2]
            "w_buffer_select": unsigned(1),          # [3]
            "enable_depth_buffer": unsigned(1),      # [4]
            "depth_function": unsigned(3),           # [7:5]
            "enable_dithering": unsigned(1),         # [8]
            "rgb_buffer_mask": unsigned(1),          # [9]
            "aux_buffer_mask": unsigned(1),          # [10]
            "dither_algorithm": unsigned(1),         # [11]
            "enable_stipple_pattern": unsigned(1),   # [12]
            "enable_alpha_mask": unsigned(1),        # [13]
            "draw_buffer": unsigned(2),              # [15:14]
            "enable_depth_bias": unsigned(1),        # [16]
            "y_origin": unsigned(1),                 # [17]
            "enable_alpha_planes": unsigned(1),      # [18]
            "enable_dither_subtract": unsigned(1),   # [19]
            "depth_source_select": unsigned(1),      # [20]
        })


# -- framebuffer/FramebufferAddressMath.scala --------------------------------


def _mul_by_small_unsigned(value, coeff):
    """``private def mulBySmallUnsigned(value: UInt, coeff: UInt): UInt``.

    A shift-and-add product written out term by term so both sides build the
    same adder tree.  Spinal's ``+^`` grows by one bit per add exactly like
    Amaranth's ``+``; the trailing slice is the ``resize(width bits)``.
    """
    width = len(value) + len(coeff)
    terms = [
        Mux(coeff[bit], (value.as_unsigned() << bit)[0:width], Const(0, width))
        for bit in range(len(coeff))
    ]
    total = terms[0]
    for term in terms[1:]:
        total = total + term
    return total[0:width]


def pixel_byte_offset(x, y, fb_pixel_stride, address_width: int):
    """``def pixelByteOffset(x, y, fbPixelStride, addressWidth): UInt``."""
    tiles_x = (fb_pixel_stride >> 6)[0:4]
    row_tiles = _mul_by_small_unsigned(y, tiles_x)
    row_bytes = (row_tiles[0:address_width].as_unsigned() << 7)[0:address_width]
    x_bytes = (x[0:address_width].as_unsigned() << 1)[0:address_width]
    return (row_bytes + x_bytes)[0:address_width]


def plane_address(base, x, y, fb_pixel_stride):
    """``def planeAddress(base, x, y, fbPixelStride): UInt``."""
    offset = pixel_byte_offset(x, y, fb_pixel_stride, len(base))
    return (base + offset)[0: len(base)]


# -- framebuffer/FramebufferPlaneBuffer.scala --------------------------------


class FramebufferWriteReq(data.StructLayout):
    """``FramebufferPlaneBuffer.WriteReq(c)`` -- one masked 32-bit plane write."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "address": unsigned(c.address_width),
            "data": unsigned(32),
            "mask": unsigned(4),
        })


# -- pixel/ColorCombine.scala ------------------------------------------------


class ColorCombineOutput(data.StructLayout):
    """``ColorCombine.Output(c)`` -- the payload ``Fog`` consumes.

    Lives here rather than in a (not yet ported) ``pixel/color_combine.py``
    because ``Fog`` is the only consumer so far and the two modules are
    being ported by different agents.

    ``depth`` is ``AFix(c.vDepthFormat)``: Spinal emits an ``AFix`` bundle
    field as one flat 32-bit signal and :class:`~avoodoo.lib.fixed.AFixShape`
    is a ``ShapeCastable`` of the same width, so the packings agree.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        members = {
            "coords": PixelCoords(c),
            "color": Color.u8(),
            "alpha": unsigned(8),
            "depth": _afix(c.v_depth_format),
            "iterated_alpha": unsigned(8),   # pass-through for fog alpha mode
            "raw_w": signed(32),             # pass-through for fog wDepth
            "fog_color": unsigned(32),
            "chroma_key": unsigned(32),
            "za_color": unsigned(32),
            "alpha_mode": ALPHA_MODE_LAYOUT,
            "fog_mode": FOG_MODE_LAYOUT,
            "fbz_mode": FbzMode(),
            "routing": FbRouting(c),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PIXEL_KEY_LAYOUT
        return super().__init__(members)


# ---------------------------------------------------------------------------
# RenderConfig.scala `LfbMode`, Lfb.scala `Lfb.Regs`, PciFifo.scala
# `TexWritePayload`, and the three `package object voodoo` data-shuffling
# helpers the LFB path uses (appended by the Lfb / CoreCpuFrontdoor port:
# `frontend/lfb.py`, `core/cpu_frontdoor.py`).  Same append-only rule as the
# block above; nothing here is imported at the top of the file.
# ---------------------------------------------------------------------------

__all__ += [
    "LfbMode",
    "LfbRegs",
    "TexWritePayload",
    "expand_rgb565",
    "expand_rgb555",
    "word_swap_and_swizzle",
]


class LfbMode(data.StructLayout):
    """``case class LfbMode()`` -- SST-1 register 0x114 (``RenderConfig.scala:97``)."""

    def __init__(self) -> None:
        super().__init__({
            "write_format": unsigned(4),          # [3:0]
            "write_buffer_select": unsigned(2),   # [5:4]
            "read_buffer_select": unsigned(2),    # [7:6]
            "pixel_pipeline_enable": unsigned(1), # [8]
            "rgba_lanes": unsigned(2),            # [10:9]
            "word_swap_writes": unsigned(1),      # [11]
            "byte_swizzle_writes": unsigned(1),   # [12]
            "y_origin": unsigned(1),              # [13]
            "w_select": unsigned(1),              # [14]
            "word_swap_reads": unsigned(1),       # [15]
            "byte_swizzle_reads": unsigned(1),    # [16]
        })


class LfbRegs(data.StructLayout):
    """``Lfb.Regs(c)`` (``frontend/Lfb.scala:453``) -- the register-bank view
    the LFB reads.  ``Lfb.Regs.fromRegisterBank`` fills it from
    ``RegisterBank.renderConfig`` plus the two ``FramebufferLayout`` routings.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "lfb_mode": LfbMode(),
            "fbz_mode": FbzMode(),
            "alpha_mode": ALPHA_MODE_LAYOUT,
            "fog_mode": FOG_MODE_LAYOUT,
            "fog_color": unsigned(32),
            "za_color": unsigned(32),
            "write_routing": FbRouting(c),
            "read_routing": FbRouting(c),
        })


class TexWritePayload(data.StructLayout):
    """``case class TexWritePayload()`` (``bus/PciFifo.scala:12``).

    ``CoreCpuFrontdoor.io.texWrite`` -> ``PciFifo.io.texWrite`` ->
    ``PciFifo.io.texDrain``; a raw 23-bit PCI offset inside the texture
    window plus the write data and byte mask.
    """

    def __init__(self) -> None:
        super().__init__({
            "pci_addr": unsigned(23),
            "data": unsigned(32),
            "mask": unsigned(4),
        })


def expand_rgb565(v):
    """``def expandRgb565(v: UInt): Color[UInt]`` -- as an ``(r, g, b)`` tuple.

    ``Bundles.scala:109``.  The tuple rather than a :class:`Color` view because
    every caller assigns the channels to three separate destinations.
    """
    return (expand_to_8(v[11:16], 5), expand_to_8(v[5:11], 6), expand_to_8(v[0:5], 5))


def expand_rgb555(v):
    """``def expandRgb555(v: UInt): Color[UInt]`` -- ignores bit 15 (alpha)."""
    return (expand_to_8(v[10:15], 5), expand_to_8(v[5:10], 5), expand_to_8(v[0:5], 5))


def word_swap_and_swizzle(data_, do_word_swap, do_byte_swizzle):
    """``def wordSwapAndSwizzle(data: Bits, doWordSwap: Bool, doByteSwizzle: Bool): Bits``.

    Word swap first (``lo16 ## hi16``), then the byte reversal of the whole
    32-bit word -- ``Bundles.scala:162``.
    """
    after_word_swap = Mux(do_word_swap, Cat(data_[16:32], data_[0:16]), data_)
    swizzled = Cat(after_word_swap[24:32], after_word_swap[16:24],
                   after_word_swap[8:16], after_word_swap[0:8])
    return Mux(do_byte_swizzle, swizzled, after_word_swap)


# ---------------------------------------------------------------------------
# framebuffer/FramebufferPlaneBuffer.scala + FramebufferPlaneReader.scala --
# the plane read-side bundles (agent p6-planereader, append-only block).
#
# `FramebufferPlaneBuffer.ReadReq(c)` / `ReadRsp()` are shared by the plane
# buffer, the cached plane reader and the direct plane reader;
# `FramebufferPlaneReader.PrefetchReq(c)` by the reader and the mem
# subsystem.  Field order is the Scala declaration order (convention 2).
# ---------------------------------------------------------------------------

__all__ += [
    "FramebufferReadReq",
    "FramebufferReadRsp",
    "FramebufferPrefetchReq",
]


class FramebufferReadReq(data.StructLayout):
    """``FramebufferPlaneBuffer.ReadReq(c)`` -- one 16-bit lane read by byte address."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({"address": unsigned(c.address_width)})


class FramebufferReadRsp(data.StructLayout):
    """``FramebufferPlaneBuffer.ReadRsp()`` -- the 16-bit lane that came back."""

    def __init__(self) -> None:
        super().__init__({"data": unsigned(16)})


class FramebufferPrefetchReq(data.StructLayout):
    """``FramebufferPlaneReader.PrefetchReq(c)`` -- inclusive byte-address span."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "start_address": unsigned(c.address_width),
            "end_address": unsigned(c.address_width),
        })


# ---------------------------------------------------------------------------
# core/CoreTypes.scala -- CoreStats / CoreBusyMap / CoreWritePathMap / CoreDebug,
# plus the two small bundles RegisterBank's ports carry (appended by the
# register_bank port; `frontend/register_bank.py`).
#
# `CoreBusyMap.bitsValue` / `CoreWritePathMap.bitsValue` assign bit i from the
# i-th declared field, so the packed layout *is* `bitsValue` and the two
# simulation-only debug registers read `debug.busy.as_value()` directly.
# ---------------------------------------------------------------------------

__all__ += [
    "CoreStats",
    "CoreBusyMap",
    "CoreWritePathMap",
    "CoreDebug",
    "PaletteWrite",
    "StatusInputs",
    "FloatShadowWriteLayout",
]


class CoreStats(data.StructLayout):
    """``case class CoreStats()`` (``core/CoreTypes.scala:61``) -- five 24-bit counters."""

    def __init__(self) -> None:
        super().__init__({
            "pixels_in": unsigned(24),
            "chroma_fail": unsigned(24),
            "z_func_fail": unsigned(24),
            "a_func_fail": unsigned(24),
            "pixels_out": unsigned(24),
        })


class CoreBusyMap(data.StructLayout):
    """``case class CoreBusyMap()`` (``core/CoreTypes.scala``) -- 32 busy sources, bit i = field i."""

    FIELDS = (
        "triangle_setup_valid", "rasterizer_running", "tmu_input_valid", "tmu_busy",
        "fb_access_busy", "color_combine_input_valid", "fog_busy", "fb_access_input_valid",
        "write_color_input_valid", "write_aux_input_valid", "fastfill_running", "swap_waiting",
        "lfb_busy", "fastfill_output_valid", "fastfill_output_ready", "fastfill_write_valid",
        "fastfill_write_ready", "pre_dither_merged_valid", "pre_dither_merged_ready",
        "pre_dither_piped_valid", "pre_dither_piped_ready", "dither_output_valid",
        "dither_output_ready", "dither_joined_valid", "dither_joined_ready", "color_fork_valid",
        "color_fork_ready", "aux_fork_valid", "aux_fork_ready", "write_color_ready",
        "write_aux_ready", "fastfill_write_aux_write",
    )

    def __init__(self) -> None:
        super().__init__({name: unsigned(1) for name in self.FIELDS})


class CoreWritePathMap(data.StructLayout):
    """``case class CoreWritePathMap()`` (``core/CoreTypes.scala``) -- 32 write-path flags."""

    FIELDS = (
        "fastfill_running", "fastfill_output_valid", "fastfill_output_ready",
        "fastfill_write_valid", "fastfill_write_ready", "pre_dither_merged_valid",
        "pre_dither_merged_ready", "dither_joined_valid", "dither_joined_ready",
        "color_fork_valid", "color_fork_ready", "color_write_input_valid",
        "color_write_input_ready", "color_write_fb_valid", "color_write_fb_ready",
        "fb_color_busy", "fb_aux_busy", "aux_fork_valid", "aux_fork_ready",
        "aux_write_input_valid", "aux_write_input_ready", "aux_write_fb_valid",
        "aux_write_fb_ready", "fb_fill_hits_non_zero", "fb_fill_misses_non_zero",
        "fb_fill_burst_count_non_zero", "fb_fill_burst_beats_non_zero",
        "fb_fill_stall_cycles_non_zero", "pixels_in_non_zero", "pixels_out_non_zero",
        "z_func_fail_non_zero", "a_func_fail_non_zero",
    )

    def __init__(self) -> None:
        super().__init__({name: unsigned(1) for name in self.FIELDS})


class CoreDebug(data.StructLayout):
    """``case class CoreDebug()`` (``core/CoreTypes.scala:387``).

    The ten trailing 32-bit words are declared upstream and never assigned by
    anything (``docs/amaranth/spinal-naming-and-harness.md`` §1.7); they stay
    in the layout because ``RegisterBank.io.debug`` carries them as ports.
    """

    def __init__(self) -> None:
        super().__init__({
            "pipeline_busy": unsigned(1),
            "busy": CoreBusyMap(),
            "write_path": CoreWritePathMap(),
            "pipeline_busy_sources": unsigned(32),
            "fb_access": unsigned(32),
            "fb_color_cache": unsigned(32),
            "fb_color_cache_req": unsigned(32),
            "fb_color_cache_expected": unsigned(32),
            "fb_color_cache_occupancy": unsigned(32),
            "fb_aux_cache": unsigned(32),
            "fb_aux_cache_req": unsigned(32),
            "fb_aux_cache_expected": unsigned(32),
            "fb_aux_cache_occupancy": unsigned(32),
        })


class PaletteWrite(data.StructLayout):
    """``RegisterBank.PaletteWrite()`` (``frontend/RegisterBank.scala:1253``)."""

    def __init__(self) -> None:
        super().__init__({"address": unsigned(8), "data": unsigned(24)})


class StatusInputs(data.StructLayout):
    """``RegisterBank.io.statusInputs`` (``frontend/RegisterBank.scala:34``)."""

    def __init__(self) -> None:
        super().__init__({
            "v_retrace": unsigned(1),
            "mem_fifo_free": unsigned(16),
            "pci_interrupt": unsigned(1),
        })


class FloatShadowWriteLayout(data.StructLayout):
    """``case class FloatShadowWrite(busAddrWidth)`` (``bus/PciFifo.scala:18``).

    The same two fields as ``avoodoo.bus.pci_fifo.FloatShadowWrite``; declared
    here too so the register bank does not import the FIFO.  Two
    ``StructLayout``\\ s with equal fields are equal, so ``wiring.connect``
    accepts either end.
    """

    def __init__(self, bus_addr_width: int) -> None:
        self.bus_addr_width = bus_addr_width
        super().__init__({"address": unsigned(bus_addr_width), "raw": signed(50)})


# ---------------------------------------------------------------------------
# core/CoreTypes.scala -- FramebufferLayout, FramebufferMemStatus,
# FramebufferMemStats (appended by the framebuffer_mem_subsystem port;
# `core/framebuffer_mem_subsystem.py`).
# ---------------------------------------------------------------------------

from amaranth import Signal, Value  # noqa: E402

__all__ += [
    "FramebufferLayout",
    "framebuffer_layout_from_register_bank",
    "FramebufferMemStatus",
    "FramebufferMemStats",
    "FRAMEBUFFER_MEM_STAT_NAMES",
]


class FramebufferLayout(data.StructLayout):
    """``case class FramebufferLayout(c: Config)`` (``core/CoreTypes.scala:8``).

    The resolved buffer bases and the three ``FbRouting``\\ s (draw, LFB write,
    LFB read) that ``Core`` derives from the register bank each cycle.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        aw = c.address_width
        super().__init__({
            "front": unsigned(aw),
            "back": unsigned(aw),
            "aux": unsigned(aw),
            "draw": FbRouting(c),
            "lfb_write": FbRouting(c),
            "lfb_read": FbRouting(c),
            "displayed_buffer": unsigned(2),
            "swaps_pending": unsigned(3),
        })


def framebuffer_layout_from_register_bank(m, c: Config, fb_base_addr, reg_bank,
                                          swap_buffer, *, name: str = "layout"):
    """``FramebufferLayout.fromRegisterBank(c, fbBaseAddr, regBank, swapBuffer)``
    (``core/CoreTypes.scala:20``): combinational, returns a ``Signal`` of
    :class:`FramebufferLayout` driven in ``m.d.comb``.

    ``reg_bank`` is the :class:`~avoodoo.frontend.register_bank.RegisterBank`
    (``init.fbi_init1.video_tiles_x``, ``init.fbi_init2.buffer_offset``,
    ``render_config.fbz_mode.draw_buffer``, ``render_config.lfb_mode.*``) and
    ``swap_buffer`` the :class:`~avoodoo.frontend.swap_buffer.SwapBuffer`
    (``swap_count``, ``swaps_pending``); ``fb_base_addr`` is the
    ``c.address_width``-bit base ``Core.io.fbBaseAddr``.  Any object with the
    same attribute paths (a test stub) does as well.

    Width bookkeeping, as Spinal does it: ``videoTilesX << 6`` is 10 bits
    then ``resize(11)``; ``bufferOffset.resize(aw) |<< 12`` keeps ``aw`` bits
    (``|<<`` is the width-preserving shift); ``fbBaseAddr + x`` keeps ``aw``
    bits (``UInt + UInt`` = ``max`` width, wrapping); ``(bufferOffsetBytes <<
    1).resized`` grows by one bit and is cut back to ``aw``.
    """
    aw = c.address_width
    layout = Signal(FramebufferLayout(c), name=name)
    video_tiles_x = Value.cast(reg_bank.init.fbi_init1.video_tiles_x)
    buffer_offset = Value.cast(reg_bank.init.fbi_init2.buffer_offset)
    fb_base_addr = Value.cast(fb_base_addr)

    def resize(v, width):
        v = Value.cast(v)
        return v[:width] if len(v) >= width else Cat(v, Const(0, width - len(v)))

    # val fbPixelStride = (regBank.init.fbiInit1_videoTilesX << 6).resize(11 bits)
    fb_pixel_stride = resize(Cat(Const(0, 6), video_tiles_x), 11)
    # val bufferOffsetBytes = fbiInit2_bufferOffset.resize(aw) |<< 12
    buffer_offset_bytes = resize(Cat(Const(0, 12), resize(buffer_offset, aw)), aw)
    buffer0_base = fb_base_addr
    buffer1_base = (fb_base_addr + buffer_offset_bytes)[:aw]
    # val auxBufferBase = fbBaseAddr + (bufferOffsetBytes << 1).resized
    aux_buffer_base = (fb_base_addr + resize(Cat(Const(0, 1), buffer_offset_bytes), aw))[:aw]
    # val displayed = swapBuffer.io.swapCount(0)
    displayed = Value.cast(swap_buffer.swap_count)[0]
    front_buffer_base = Mux(displayed, buffer1_base, buffer0_base)
    back_buffer_base = Mux(displayed, buffer0_base, buffer1_base)
    fbz_mode = reg_bank.render_config.fbz_mode
    lfb_mode = reg_bank.render_config.lfb_mode
    m.d.comb += [
        layout.front.eq(front_buffer_base),
        layout.back.eq(back_buffer_base),
        layout.aux.eq(aux_buffer_base),
        # drawBuffer(0) ? backBufferBase | frontBufferBase
        layout.draw.color_base_addr.eq(
            Mux(Value.cast(fbz_mode.draw_buffer)[0], back_buffer_base, front_buffer_base)),
        layout.draw.aux_base_addr.eq(aux_buffer_base),
        layout.draw.pixel_stride.eq(fb_pixel_stride),
        layout.lfb_write.color_base_addr.eq(
            Mux(Value.cast(lfb_mode.write_buffer_select)[0], back_buffer_base,
                front_buffer_base)),
        layout.lfb_write.aux_base_addr.eq(aux_buffer_base),
        layout.lfb_write.pixel_stride.eq(fb_pixel_stride),
        layout.lfb_read.color_base_addr.eq(
            Mux(Value.cast(lfb_mode.read_buffer_select)[0], back_buffer_base,
                front_buffer_base)),
        layout.lfb_read.aux_base_addr.eq(aux_buffer_base),
        layout.lfb_read.pixel_stride.eq(fb_pixel_stride),
        # displayed.asUInt.resize(2 bits)
        layout.displayed_buffer.eq(displayed),
        layout.swaps_pending.eq(swap_buffer.swaps_pending),
    ]
    return layout


class FramebufferMemStatus(data.StructLayout):
    """``case class FramebufferMemStatus()`` (``core/CoreTypes.scala:77``)."""

    def __init__(self) -> None:
        super().__init__({"color_busy": unsigned(1), "aux_busy": unsigned(1)})


#: ``FramebufferMemStats``'s fields in declaration order; all ``UInt(32 bits)``
#: except the last, ``readMaxQueueOccupancy``, which is 8.
FRAMEBUFFER_MEM_STAT_NAMES = (
    "fill_hits",
    "fill_misses",
    "fill_burst_count",
    "fill_burst_beats",
    "fill_stall_cycles",
    "write_stall_cycles",
    "write_drain_count",
    "write_full_drain_count",
    "write_partial_drain_count",
    "write_drain_reason_full_count",
    "write_drain_reason_rotate_count",
    "write_drain_reason_flush_count",
    "write_drain_dirty_word_total",
    "write_rotate_blocked_cycles",
    "write_single_word_drain_count",
    "write_single_word_drain_start_at_zero_count",
    "write_single_word_drain_start_at_last_count",
    "write_rotate_adjacent_line_count",
    "write_rotate_same_line_gap_count",
    "write_rotate_other_line_count",
    "mem_color_write_cmd_count",
    "mem_aux_write_cmd_count",
    "mem_color_read_cmd_count",
    "mem_aux_read_cmd_count",
    "mem_lfb_read_cmd_count",
    "mem_color_write_blocked_cycles",
    "mem_aux_write_blocked_cycles",
    "mem_color_read_blocked_cycles",
    "mem_aux_read_blocked_cycles",
    "mem_lfb_read_blocked_cycles",
    "read_req_count",
    "read_req_forward_step_count",
    "read_req_backward_step_count",
    "read_req_same_word_count",
    "read_req_same_line_count",
    "read_req_other_count",
    "read_single_beat_burst_count",
    "read_multi_beat_burst_count",
    "read_max_queue_occupancy",
)


class FramebufferMemStats(data.StructLayout):
    """``case class FramebufferMemStats()`` (``core/CoreTypes.scala:87``).

    Thirty-eight 32-bit counters and the 8-bit ``readMaxQueueOccupancy``,
    produced by ``FramebufferMemSubsystem`` and consumed by ``PixelPipeline``
    (``io.fbStats``).  ``exposeToSim()`` has no RTL.
    """

    def __init__(self) -> None:
        super().__init__({
            name: unsigned(8 if name == "read_max_queue_occupancy" else 32)
            for name in FRAMEBUFFER_MEM_STAT_NAMES
        })
