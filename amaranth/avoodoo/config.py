"""Core configuration.

Port of ``ext/SpinalVoodoo/src/voodoo/Config.scala`` (plus the ``QFormat`` /
``SQ`` / ``UQ`` types that Scala file imports from ``spinal.core``, and
``TraceConfig`` from ``ext/SpinalVoodoo/src/voodoo/Trace.scala``).

The :class:`QFormat` semantics below were read off the SpinalHDL 1.15.0
``spinal-core`` jar by calling the real class from the JVM, not guessed:

    SQ(16,4)  -> nonFraction=12 amplify=11 numeric=15 resolution=0.0625
                 min=-2048.0 max=2047.9375 alignHex=4 alignOct=6
    SQ(16,4) * SQ(24,12) = SQ(40,16)
    SQ(16,4) + SQ(24,12) = SQ(24,12)
    UQ(8,0)  - UQ(16,4)  = SQ(17,4)
    -UQ(8,0) = SQ(9,0)
    SQ(16,4) >> 2 = SQ(14,2)      SQ(16,4) << 2 = SQ(18,6)

Note that ``QFormat`` is only the *format descriptor*. The AFix arithmetic
itself (``fixTo``, ``floor``, ``sat``, ``roundHalfDown`` ...) is Phase 0.2's
job and lives in ``avoodoo/lib/fixed.py``; nothing here assumes how that is
implemented.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

__all__ = ["QFormat", "SQ", "UQ", "TraceConfig", "Revision", "Voodoo1", "Config"]


@dataclass(frozen=True)
class QFormat:
    """SpinalHDL ``spinal.core.QFormat``.

    ``width`` is the *total* bit width (sign bit included for signed formats)
    and ``fraction`` the number of fractional bits.
    """

    width: int
    fraction: int
    signed: bool

    def __post_init__(self) -> None:
        if self.width < 0 or self.fraction < 0:
            raise ValueError(f"QFormat({self.width}, {self.fraction}) must be non-negative")
        # No `fraction <= width` check: Spinal's QFormat is an unvalidated case
        # class, and `ColorCombine.clampAFixToFactor` really does build
        # `UQ(0, 8)` -- a zero-bit format, which AFix turns into a constant
        # zero. See docs/amaranth/afix-semantics.md §8.1; the port has to be
        # able to express that to stay bit-identical.

    # -- derived properties (names kept identical to Spinal) ---------------

    @property
    def non_fraction(self) -> int:
        return self.width - self.fraction

    @property
    def amplify(self) -> int:
        return self.non_fraction - (1 if self.signed else 0)

    @property
    def numeric(self) -> int:
        """Magnitude bits, i.e. width minus the sign bit."""
        return self.width - (1 if self.signed else 0)

    @property
    def capcity(self) -> float:  # noqa: N802 - Spinal spells it this way
        return float(2 ** self.width)

    @property
    def half_capcity(self) -> float:
        return self.capcity / 2

    @property
    def resolution(self) -> float:
        return 2.0 ** -self.fraction

    @property
    def max_value(self) -> float:
        if self.signed:
            return (self.half_capcity - 1) * self.resolution
        return (self.capcity - 1) * self.resolution

    @property
    def min_value(self) -> float:
        if self.signed:
            return -self.half_capcity * self.resolution
        return 0.0

    @property
    def eps_in_double(self) -> float:
        return math.ulp(self.max_value)

    @property
    def align_hex(self) -> int:
        return -(-self.width // 4)

    @property
    def align_oct(self) -> int:
        return -(-self.width // 3)

    # -- format algebra ----------------------------------------------------

    def __mul__(self, other: "QFormat") -> "QFormat":
        return QFormat(
            self.width + other.width,
            self.fraction + other.fraction,
            self.signed or other.signed,
        )

    def __add__(self, other: "QFormat") -> "QFormat":
        fraction = max(self.fraction, other.fraction)
        non_fraction = max(self.non_fraction, other.non_fraction)
        return QFormat(non_fraction + fraction, fraction, self.signed or other.signed)

    def __neg__(self) -> "QFormat":
        if self.signed:
            return self
        return QFormat(self.width + 1, self.fraction, True)

    def __sub__(self, other: "QFormat") -> "QFormat":
        return self + (-other)

    def __rshift__(self, n: int) -> "QFormat":
        return replace(self, width=self.width - n, fraction=self.fraction - n)

    def __lshift__(self, n: int) -> "QFormat":
        return replace(self, width=self.width + n, fraction=self.fraction + n)

    def __str__(self) -> str:
        kind = "SQ" if self.signed else "UQ"
        return f"{kind}({self.width}, {self.fraction})"


def SQ(width: int, fraction: int) -> QFormat:  # noqa: N802 - Spinal name
    return QFormat(width, fraction, True)


def UQ(width: int, fraction: int) -> QFormat:  # noqa: N802 - Spinal name
    return QFormat(width, fraction, False)


@dataclass(frozen=True)
class TraceConfig:
    """``ext/SpinalVoodoo/src/voodoo/Trace.scala``."""

    enabled: bool = False


class Revision:
    """Sealed trait ``Config.Revision``."""


@dataclass(frozen=True)
class Voodoo1(Revision):
    pass


@dataclass(frozen=True)
class Config:
    revision: Revision
    vertex_format: QFormat
    coefficient_format: QFormat
    v_color_format: QFormat
    v_depth_format: QFormat
    tex_coords_format: QFormat
    tex_coords_hi_format: QFormat
    w_format: QFormat
    tex_coords_accum_format: QFormat
    w_accum_format: QFormat
    max_fb_dims: tuple[int, int]
    address_width: int
    mem_burst_length_width: int
    fb_write_buffer_line_words: int
    fb_write_buffer_count: int
    tex_fill_line_words: int
    use_fb_write_buffer: bool
    use_fb_read_cache: bool
    use_tex_fill_cache: bool
    enable_tmu: bool = True
    enable_fog: bool = True
    enable_dither: bool = True
    enable_chroma_key: bool = True
    enable_alpha_test: bool = True
    tex_fill_cache_slots: int = 16
    tex_fill_way_count: int = 2
    tex_fill_xor_index: bool = False
    tex_fill_request_window: int = 16
    packed_tex_layout: bool = True
    enable_hdmi_scanout: bool = True
    hdmi_scanout_pixel_repeat_x: int = 1
    trace: TraceConfig = field(default_factory=TraceConfig)

    @staticmethod
    def voodoo1(trace: TraceConfig | None = None) -> "Config":
        return Config(
            revision=Voodoo1(),
            # Datasheet: 12.4 format = 12 integer + 4 frac = SQ(16, 4)
            vertex_format=SQ(16, 4),
            # Edge coefficients: c = v0.x*v1.y - v1.x*v0.y needs 2x vertex product range
            coefficient_format=SQ(34, 8),
            # Datasheet: 12.12 format = 12 integer + 12 frac = SQ(24, 12)
            v_color_format=SQ(24, 12),
            # Datasheet: 20.12 format = 20 integer + 12 frac = SQ(32, 12)
            v_depth_format=SQ(32, 12),
            # Datasheet: 14.18 format = 14 integer + 18 frac = SQ(32, 18)
            tex_coords_format=SQ(32, 18),
            tex_coords_hi_format=SQ(46, 30),
            # Datasheet: 2.30 format = 2 integer + 30 frac = SQ(32, 30)
            w_format=SQ(32, 30),
            # Internal interpolation accumulators use a wider range than register width
            # to avoid wrap in long spans. Fractional precision remains unchanged.
            tex_coords_accum_format=SQ(48, 18),
            w_accum_format=SQ(48, 30),
            max_fb_dims=(800, 600),
            address_width=26,
            mem_burst_length_width=11,
            fb_write_buffer_line_words=16,
            fb_write_buffer_count=2,
            tex_fill_line_words=8,
            use_fb_write_buffer=False,
            use_fb_read_cache=True,
            use_tex_fill_cache=True,
            enable_tmu=True,
            enable_fog=True,
            enable_dither=True,
            enable_chroma_key=True,
            enable_alpha_test=True,
            tex_fill_cache_slots=16,
            tex_fill_request_window=16,
            trace=trace if trace is not None else TraceConfig(),
        )
