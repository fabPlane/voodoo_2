"""``hdmi/HdmiScanout.scala`` -- video timing, test pattern, framebuffer scanout, TMDS.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/hdmi/HdmiScanout.scala`` (671 lines,
SpinalHDL 1.15.0, the working-tree version with
``amaranth/spinal-ref/upstream-local-edits.patch`` applied; plan Phase 7.1).
``Core.scala:133`` builds ``HdmiCdcFramebufferScanout(c, timing =
if (c.hdmiScanoutDmt640x480) VideoTiming.dmt640x480 else
VideoTiming.cea720x480p, pixelRepeatX = c.hdmiScanoutPixelRepeatX)`` and
exposes it through ``master(HdmiScanoutPort(c))``; every other component in
the file is either a building block of that one or a board-level helper
(``HdmiTestPatternScanout``, ``TmdsEncoder``, ``TmdsSerializer``) kept because
the Scala file is the unit of porting.

Structure map (Scala -> here)
-----------------------------

=====================================  ==========================================
Scala                                  Python
=====================================  ==========================================
``case class VideoTiming``             :class:`VideoTiming` (frozen dataclass)
``VideoTiming.dmt640x480``             :data:`DMT_640X480`
``VideoTiming.cea720x480p``            :data:`CEA_720X480P`
``case class Rgb888() extends Bundle`` :class:`Rgb888` (``StructLayout``)
``Rgb888.black()``                     :func:`rgb888_black`
``Rgb888.fromRgb565``                  :func:`rgb888_from_rgb565`
``Rgb888.applyGamma``                  :func:`apply_gamma`
``Rgb888.asBits24``                    :func:`rgb888_as_bits24`
``ParallelRgbVideo``                   :class:`ParallelRgbVideo`
``HdmiScanoutRegs(c)``                 :class:`HdmiScanoutRegs`
``HdmiScanoutStatus(c)``               :class:`HdmiScanoutStatus`
``HdmiScanoutPort(c, fifoLevelWidth)`` :class:`HdmiScanoutPortSignature`
``VideoScanout(timing)``               :class:`VideoScanout`
``HdmiTestPattern(timing)``            :class:`HdmiTestPattern`
``HdmiFramebufferScanout(c, timing)``  :class:`HdmiFramebufferScanout`
``HdmiCdcFramebufferScanout(...)``     :class:`HdmiCdcFramebufferScanout`
``TmdsEncoder()``                      :class:`TmdsEncoder`
``HdmiTestPatternScanout(timing)``     :class:`HdmiTestPatternScanout`
``TmdsSerializer()``                   :class:`TmdsSerializer`
``*Gen`` objects                       (none: ``gen/gen_module.py``)
=====================================  ==========================================

Clock domains
-------------

``HdmiCdcFramebufferScanout`` is the one two-clock component.  The Scala
builds ``hdmiClockDomain = ClockDomain(clock = io.hdmiClock, reset =
io.hdmiReset, config = ClockDomain.current.config)`` from two plain ``in
Bool`` ports; the port does the same with :func:`avoodoo.lib.cdc.clock_domain`
(a local ``hdmi`` domain, SYNC/HIGH reset like every domain in this design,
plan §3 decision 6), so the port list is the Spinal one -- ``hdmi_clock`` and
``hdmi_reset`` are ordinary inputs and the lockstep diff can drive them.
Everything inside ``val hdmi = new ClockingArea(hdmiClockDomain)`` is written
to ``m.d.hdmi`` or runs under ``DomainRenamer({"sync": "hdmi"})``; everything
else is the core (``sync``) domain, exactly as ``ClockDomain.current`` is in
the Scala.

Crossings, all through :mod:`avoodoo.lib.cdc` / :mod:`avoodoo.lib.stream`:

* ``pixelFifo = StreamFifoCC(Bits(16 bits), fifoDepth, coreCd, hdmiCd)`` ->
  :class:`~avoodoo.lib.stream.FifoCC` with its default
  ``with_pop_buffered_reset=True``: the pop side runs in
  ``hdmiCd.withBufferedResetFrom(coreCd)``.  Spinal's ``globalCache`` keys
  that synchroniser on ``(hdmiCd, coreCd, depth)``; the only other buffered
  reset here is ``PulseCCByToggle``'s, keyed the other way round
  ``(coreCd, hdmiCd, depth)``, so nothing is shared and the per-pair domain
  trick of ``gowin/axilite4_clock_crossing.py`` is not needed.
* ``PulseCCByToggle(hdmi.scan.io.newFrame, hdmiCd, coreCd)`` ->
  :func:`~avoodoo.lib.cdc.pulse_cc_by_toggle`.
* ten ``BufferCC(x, init)`` call sites -- five into the hdmi domain
  (``testPatternEnable``, ``gammaLut(i)`` x33, ``displayWidth``,
  ``displayHeight``, ``scanoutRun``) and five back into the core domain
  (``contentActive``, ``x``, ``y``, ``popOccupancy``, ``underflowReg``) ->
  :func:`~avoodoo.lib.cdc.buffer_cc`.  Every one of them carries an ``init``,
  so every synchroniser flop here is a *reset* flop (plan §2's three
  un-init'd sites are in ``TangConsole138k``, not here).

Registers: every ``Reg`` in the file has an ``init``, so nothing is
``reset_less`` (``tests/test_hdmi_scanout.py`` audits that).  Width rules
follow the Scala operator by operator: ``+``/``-`` wrap at the operand width,
``<<`` widens and the following ``.resize`` truncates, ``.resized`` takes the
width of the other operand (``scan.io.x < io.regs.displayWidth.resized`` is
a compare at ``hWidth``, which truncates ``displayWidth`` for timings whose
``hTotal`` is below 1024 -- reproduced, not fixed).

Memories: the ``StreamFifoCC`` RAM (4096 x 16 in the core build) is below the
``width >= 24`` line of ``force_distributed_fifo_ram.py`` and stays BSRAM
(:func:`~avoodoo.lib.stream.spinal_fifo_ram_attrs` returns ``{}``); it is
uninitialised in Spinal, so ``gen/gen_core_sim.py``'s ``strip_meminit`` must
cover it when Phase 7.2 puts it under ``CoreSim``.

Behaviour worth knowing (both implementations, seen in the lockstep diff)
-------------------------------------------------------------------------

* ``outstanding`` only counts ``readReq`` fires minus FIFO pushes; a
  ``readRsp`` beat that answers no request steps it below zero, it wraps to
  ``2**13 - 1``, and ``projected`` then blocks ``readReq`` until enough
  pushes wrap it back.  The reader behind it (``FramebufferPlaneReader``)
  answers one for one, so ``Core`` never sees this; a stimulus must.
* After a core reset with the pixel clock stopped, the pop side's
  synchronised reset has not yet had an edge to act on, so
  ``fifoPushOccupancy`` reads ``0 - <old pop pointer>`` (a value above the
  depth) until two pixel clocks have passed.  Harmless at power-on (both
  pointers are zero) and identical on both sides.
* ``underflowReg`` is sticky until the next HDMI-domain reset; the core
  cannot clear it.
* ``Config.hdmiScanoutDmt640x480`` (``Core.scala:135`` picks the timing with
  it; ``CoreGowinGen.scala:79`` sets it) has no field in
  :class:`avoodoo.config.Config` yet -- Phase 7.2 (``core.py``) adds it.
"""

from __future__ import annotations

from dataclasses import dataclass

from amaranth import Array, Cat, Const, Module, Mux, Signal, Value, signed, unsigned
from amaranth.hdl import DomainRenamer
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out
from amaranth.utils import ceil_log2

from avoodoo.config import Config
from avoodoo.core.core_types import (FramebufferPrefetchReq, FramebufferReadReq,
                                     FramebufferReadRsp)
from avoodoo.framebuffer.address_math import plane_address
from avoodoo.lib.cdc import buffer_cc, clock_domain, pulse_cc_by_toggle
from avoodoo.lib.intops import zext
from avoodoo.lib.stream import FifoCC, fire, spinal_fifo_ram_attrs

__all__ = [
    "VideoTiming",
    "DMT_640X480",
    "CEA_720X480P",
    "Rgb888",
    "rgb888_black",
    "rgb888_from_rgb565",
    "rgb888_as_bits24",
    "apply_gamma",
    "gamma_interpolate_model",
    "apply_gamma_model",
    "ParallelRgbVideo",
    "HdmiScanoutRegs",
    "HdmiScanoutStatus",
    "HdmiScanoutPortSignature",
    "VideoScanout",
    "HdmiTestPattern",
    "HdmiFramebufferScanout",
    "HdmiCdcFramebufferScanout",
    "TmdsEncoder",
    "HdmiTestPatternScanout",
    "TmdsSerializer",
    "NO_DATA",
]


# ---------------------------------------------------------------------------
# VideoTiming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoTiming:
    """``case class VideoTiming(...)`` with its two derived totals."""

    active_width: int = 640
    active_height: int = 480
    h_front_porch: int = 16
    h_sync: int = 96
    h_back_porch: int = 48
    v_front_porch: int = 10
    v_sync: int = 2
    v_back_porch: int = 33
    h_sync_active_high: bool = False
    v_sync_active_high: bool = False

    @property
    def h_total(self) -> int:
        return self.active_width + self.h_front_porch + self.h_sync + self.h_back_porch

    @property
    def v_total(self) -> int:
        return self.active_height + self.v_front_porch + self.v_sync + self.v_back_porch

    @property
    def h_width(self) -> int:
        """``log2Up(timing.hTotal)``."""
        return ceil_log2(self.h_total)

    @property
    def v_width(self) -> int:
        """``log2Up(timing.vTotal)``."""
        return ceil_log2(self.v_total)


#: ``VideoTiming.dmt640x480 = VideoTiming()``.
DMT_640X480 = VideoTiming()

#: ``VideoTiming.cea720x480p``.
CEA_720X480P = VideoTiming(
    active_width=720, active_height=480,
    h_front_porch=16, h_sync=62, h_back_porch=60,
    v_front_porch=9, v_sync=6, v_back_porch=30,
    h_sync_active_high=False, v_sync_active_high=False,
)


# ---------------------------------------------------------------------------
# Rgb888 and the bundles
# ---------------------------------------------------------------------------


class Rgb888(data.StructLayout):
    """``case class Rgb888() extends Bundle { r, g, b: UInt(8 bits) }``."""

    def __init__(self) -> None:
        super().__init__({"r": unsigned(8), "g": unsigned(8), "b": unsigned(8)})


def rgb888_black() -> tuple[Value, Value, Value]:
    """``Rgb888.black()`` as an ``(r, g, b)`` triple."""
    return Const(0, 8), Const(0, 8), Const(0, 8)


def rgb888_as_bits24(rgb) -> Value:
    """``def asBits24: Bits = r.asBits ## g.asBits ## b.asBits`` (``r`` on top)."""
    return Cat(rgb.b, rgb.g, rgb.r)


def rgb888_from_rgb565(pixel) -> tuple[Value, Value, Value]:
    """``Rgb888.fromRgb565(pixel: Bits)``: 5/6/5 -> 8 by replicating the top bits.

    ::

        rgb.r := (pixel(15 downto 11) ## pixel(15 downto 13)).asUInt
        rgb.g := (pixel(10 downto 5) ## pixel(10 downto 9)).asUInt
        rgb.b := (pixel(4 downto 0) ## pixel(4 downto 2)).asUInt
    """
    pixel = Value.cast(pixel)
    r = Cat(pixel[13:16], pixel[11:16])
    g = Cat(pixel[9:11], pixel[5:11])
    b = Cat(pixel[2:5], pixel[0:5])
    return r, g, b


def _gamma_interpolate(channel, lut, high: int, low: int) -> Value:
    """``def interpolate(channel: UInt, high: Int, low: Int): UInt`` of ``applyGamma``.

    ::

        val index = channel(7 downto 3).resize(log2Up(33))          // 6 bits
        val fraction = channel(2 downto 0)
        val a = lut(index)(high downto low).asUInt
        val b = lut(index + 1)(high downto low).asUInt
        val invFraction = U(8, 4 bits) - fraction.resize(4)
        val weighted = (a.resize(12) * invFraction) + (b.resize(12) * fraction.resize(4))
        Mux(channel === 255, b, (weighted >> 3).resize(8))

    ``index + 1`` is a 6-bit wrapping add and the ``Vec`` is 33 entries, so
    ``index`` reaches 31 and ``index + 1`` reaches 32 at most; the LUT is read
    through an :class:`amaranth.Array` of the 33 entries, as the Spinal ``Vec``
    read mux is.
    """
    channel = Value.cast(channel)
    index = zext(channel[3:8], ceil_log2(33))
    fraction = channel[0:3]
    index_plus = (index + 1)[0:len(index)]
    entries = Array(Value.cast(lut[i])[low:high + 1] for i in range(33))
    a = entries[index]
    b = entries[index_plus]
    inv_fraction = (Const(8, 4) - zext(fraction, 4))[0:4]
    weighted = ((zext(a, 12) * inv_fraction) + (zext(b, 12) * zext(fraction, 4)))[0:16]
    return Mux(channel == 255, b, (weighted >> 3)[0:8])


def apply_gamma(rgb, lut) -> tuple[Value, Value, Value]:
    """``Rgb888.applyGamma(rgb: Rgb888, lut: Vec[Bits])`` -> ``(r, g, b)``.

    ``rgb`` is anything with ``.r``/``.g``/``.b`` (a :class:`Rgb888` view) or
    an ``(r, g, b)`` triple; ``lut`` is indexable ``[0..32]`` of 24-bit values
    packed ``r`` in bits 23..16, ``g`` in 15..8, ``b`` in 7..0.
    """
    r, g, b = _rgb_parts(rgb)
    return (_gamma_interpolate(r, lut, 23, 16),
            _gamma_interpolate(g, lut, 15, 8),
            _gamma_interpolate(b, lut, 7, 0))


def _rgb_parts(rgb) -> tuple[Value, Value, Value]:
    if isinstance(rgb, tuple):
        return rgb
    return rgb.r, rgb.g, rgb.b


def _assign_rgb(dst, rgb) -> list:
    r, g, b = _rgb_parts(rgb)
    return [dst.r.eq(r), dst.g.eq(g), dst.b.eq(b)]


def gamma_interpolate_model(channel: int, lut: list[int], high: int, low: int) -> int:
    """Exact-integer twin of :func:`_gamma_interpolate` for the tests."""
    index = channel >> 3
    fraction = channel & 7
    mask = (1 << (high - low + 1)) - 1
    a = (lut[index] >> low) & mask
    b = (lut[index + 1] >> low) & mask
    weighted = a * (8 - fraction) + b * fraction
    return b if channel == 255 else (weighted >> 3) & 0xFF


def apply_gamma_model(r: int, g: int, b: int, lut: list[int]) -> tuple[int, int, int]:
    return (gamma_interpolate_model(r, lut, 23, 16),
            gamma_interpolate_model(g, lut, 15, 8),
            gamma_interpolate_model(b, lut, 7, 0))


class ParallelRgbVideo(data.StructLayout):
    """``case class ParallelRgbVideo() extends Bundle { rgb, de, hSync, vSync }``."""

    def __init__(self) -> None:
        super().__init__({
            "rgb": Rgb888(),
            "de": unsigned(1),
            "h_sync": unsigned(1),
            "v_sync": unsigned(1),
        })


class HdmiScanoutRegs(data.StructLayout):
    """``case class HdmiScanoutRegs(c: Config) extends Bundle``."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "front_base": unsigned(c.address_width),
            "back_base": unsigned(c.address_width),
            "pixel_stride": unsigned(11),
            "display_width": unsigned(10),
            "display_height": unsigned(10),
            "framebuffer_enable": unsigned(1),
            "test_pattern_enable": unsigned(1),
            "gamma_lut": data.ArrayLayout(unsigned(24), 33),
        })


class HdmiScanoutStatus(data.StructLayout):
    """``case class HdmiScanoutStatus(c: Config) extends Bundle``."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "displayed_buffer": unsigned(1),
            "displayed_base": unsigned(c.address_width),
            "swap_pending": unsigned(1),
            "swap_count": unsigned(2),
            "new_frame": unsigned(1),
            "active": unsigned(1),
            "x": unsigned(10),
            "y": unsigned(10),
        })


class HdmiScanoutPortSignature(wiring.Signature):
    """``case class HdmiScanoutPort(c: Config, fifoLevelWidth: Int = 13)``, master view.

    ``asMaster`` takes ``clock``/``reset`` in and drives ``video``,
    ``status``, ``underflow``, ``fifoLevel``; ``Core.scala:96`` is
    ``master(HdmiScanoutPort(c))`` and ``Core.scala:250`` fills ``fifoLevel``
    with ``hdmiScanout.io.fifoPushOccupancy.resized``.
    """

    def __init__(self, c: Config, fifo_level_width: int = 13) -> None:
        self.c = c
        self.fifo_level_width = fifo_level_width
        super().__init__({
            "clock": In(1),
            "reset": In(1),
            "video": Out(ParallelRgbVideo()),
            "status": Out(HdmiScanoutStatus(c)),
            "underflow": Out(1),
            "fifo_level": Out(fifo_level_width),
        })


#: ``slave Stream(NoData)`` (``HdmiFramebufferScanout.io.swap``); see
#: ``frontend/swap_buffer.py`` for why a zero-width payload is the spelling.
NO_DATA = stream.Signature(unsigned(0))


# ---------------------------------------------------------------------------
# VideoScanout
# ---------------------------------------------------------------------------


class VideoScanout(wiring.Component):
    """``case class VideoScanout(timing: VideoTiming = VideoTiming()) extends Component``.

    ``h``/``v`` free-run while ``enable`` is high and are held at zero
    otherwise (the Scala has two ``when(!enable)`` blocks that both do that;
    both are kept).  Sync outputs follow ``hSyncActiveHigh``/``vSyncActiveHigh``.
    """

    def __init__(self, timing: VideoTiming = DMT_640X480) -> None:
        self.timing = timing
        super().__init__({
            "x": Out(timing.h_width),
            "y": Out(timing.v_width),
            "active": Out(1),
            "h_sync": Out(1),
            "v_sync": Out(1),
            "new_line": Out(1),
            "new_frame": Out(1),
            "enable": In(1),
        })

    def elaborate(self, platform):
        m = Module()
        t = self.timing
        h = Signal(t.h_width, name="h")
        v = Signal(t.v_width, name="v")

        at_end_of_line = Signal(name="atEndOfLine")
        at_end_of_frame = Signal(name="atEndOfFrame")
        m.d.comb += [
            at_end_of_line.eq(h == t.h_total - 1),
            at_end_of_frame.eq(at_end_of_line & (v == t.v_total - 1)),
        ]

        with m.If(self.enable):
            m.d.sync += h.eq(h + 1)
            with m.If(at_end_of_line):
                m.d.sync += [h.eq(0), v.eq(v + 1)]
                with m.If(v == t.v_total - 1):
                    m.d.sync += v.eq(0)
        with m.Else():
            m.d.sync += [h.eq(0), v.eq(0)]

        with m.If(~self.enable):
            with m.If((h != 0) | (v != 0)):
                m.d.sync += [v.eq(0), h.eq(0)]

        h_sync_raw = Signal(name="hSyncRaw")
        v_sync_raw = Signal(name="vSyncRaw")
        m.d.comb += [
            h_sync_raw.eq((h >= t.active_width + t.h_front_porch)
                          & (h < t.active_width + t.h_front_porch + t.h_sync)),
            v_sync_raw.eq((v >= t.active_height + t.v_front_porch)
                          & (v < t.active_height + t.v_front_porch + t.v_sync)),
            self.x.eq(h),
            self.y.eq(v),
            self.active.eq((h < t.active_width) & (v < t.active_height)),
            self.h_sync.eq(h_sync_raw if t.h_sync_active_high else ~h_sync_raw),
            self.v_sync.eq(v_sync_raw if t.v_sync_active_high else ~v_sync_raw),
            self.new_line.eq(at_end_of_line),
            self.new_frame.eq(at_end_of_frame),
        ]
        return m


# ---------------------------------------------------------------------------
# HdmiTestPattern
# ---------------------------------------------------------------------------


class HdmiTestPattern(wiring.Component):
    """``case class HdmiTestPattern(timing: VideoTiming = VideoTiming()) extends Component``.

    Four vertical bands: red, green, blue, then ``(x, y, x ^ y)`` on the low 8
    bits of the counters (``resize(8)`` -- truncating or zero-extending with
    the timing's counter width).  Black outside ``active``.
    """

    def __init__(self, timing: VideoTiming = DMT_640X480) -> None:
        self.timing = timing
        super().__init__({
            "x": In(timing.h_width),
            "y": In(timing.v_width),
            "active": In(1),
            "rgb": Out(Rgb888()),
        })

    def elaborate(self, platform):
        m = Module()
        t = self.timing
        rgb = self.rgb
        m.d.comb += _assign_rgb(rgb, rgb888_black())
        with m.If(self.active):
            with m.If(self.x < t.active_width // 4):
                m.d.comb += _assign_rgb(rgb, (Const(255, 8), Const(0, 8), Const(0, 8)))
            with m.Elif(self.x < t.active_width // 2):
                m.d.comb += _assign_rgb(rgb, (Const(0, 8), Const(255, 8), Const(0, 8)))
            with m.Elif(self.x < t.active_width * 3 // 4):
                m.d.comb += _assign_rgb(rgb, (Const(0, 8), Const(0, 8), Const(255, 8)))
            with m.Else():
                x8 = zext(self.x, 8)
                y8 = zext(self.y, 8)
                m.d.comb += _assign_rgb(rgb, (x8, y8, x8 ^ y8))
        return m


# ---------------------------------------------------------------------------
# HdmiFramebufferScanout (single clock; the pre-CDC design, kept and diffed)
# ---------------------------------------------------------------------------


class HdmiFramebufferScanout(wiring.Component):
    """``case class HdmiFramebufferScanout(c: Config, timing = VideoTiming.cea720x480p)``.

    The synchronous scanout: one ``readReq`` per active content pixel at the
    scan position, the response (or the last one, ``fbRgb``) gamma-corrected
    onto ``video``, a vblank-synchronised front/back swap on ``io.swap``.
    Not instantiated by ``Core``; ``GenOne`` keeps a row for it.
    """

    def __init__(self, c: Config | None = None, timing: VideoTiming = CEA_720X480P) -> None:
        c = Config.voodoo1() if c is None else c
        self.c = c
        self.timing = timing
        super().__init__({
            "regs": In(HdmiScanoutRegs(c)),
            "swap": In(NO_DATA),
            "read_req": Out(stream.Signature(FramebufferReadReq(c))),
            "read_rsp": In(stream.Signature(FramebufferReadRsp())),
            "video": Out(ParallelRgbVideo()),
            "status": Out(HdmiScanoutStatus(c)),
        })

    def elaborate(self, platform):
        m = Module()
        c, t = self.c, self.timing
        regs = self.regs
        h_width = t.h_width

        m.submodules.scan = scan = VideoScanout(t)
        m.d.comb += scan.enable.eq(1)
        m.submodules.pattern = pattern = HdmiTestPattern(t)
        m.d.comb += [
            pattern.x.eq(scan.x),
            pattern.y.eq(scan.y),
            pattern.active.eq(scan.active),
        ]

        # `scan.io.x < io.regs.displayWidth.resized`: `.resized` takes the
        # other operand's width, so the register is cut to hWidth/vWidth.
        content_active = Signal(name="contentActive")
        m.d.comb += content_active.eq(
            scan.active
            & (scan.x < zext(regs.display_width, h_width))
            & (scan.y < zext(regs.display_height, t.v_width)))

        displayed_buffer = Signal(1, name="displayedBuffer")
        swap_pending = Signal(name="swapPending")
        swap_count = Signal(2, name="swapCount")

        m.d.comb += self.swap.ready.eq(~swap_pending)
        with m.If(fire(self.swap)):
            m.d.sync += swap_pending.eq(1)

        with m.If(scan.new_frame & swap_pending):
            m.d.sync += [
                displayed_buffer.eq(~displayed_buffer),
                swap_pending.eq(0),
                swap_count.eq(swap_count + 1),
            ]

        displayed_base = Signal(c.address_width, name="displayedBase")
        m.d.comb += displayed_base.eq(Mux(displayed_buffer[0], regs.back_base, regs.front_base))

        m.d.comb += [
            self.read_req.valid.eq(regs.framebuffer_enable & content_active),
            self.read_req.payload.address.eq(plane_address(
                displayed_base, zext(scan.x, 10), zext(scan.y, 10), regs.pixel_stride)),
        ]

        fb_rgb = Signal(Rgb888(), name="fbRgb")
        read_rsp_rgb = rgb888_from_rgb565(self.read_rsp.payload.data)
        m.d.comb += self.read_rsp.ready.eq(1)
        with m.If(fire(self.read_rsp)):
            m.d.sync += _assign_rgb(fb_rgb, read_rsp_rgb)

        selected_rgb = Signal(Rgb888(), name="selectedRgb")
        m.d.comb += _assign_rgb(selected_rgb, fb_rgb)
        with m.If(self.read_rsp.valid):
            m.d.comb += _assign_rgb(selected_rgb, read_rsp_rgb)
        with m.If(regs.test_pattern_enable | ~regs.framebuffer_enable):
            m.d.comb += _assign_rgb(selected_rgb, pattern.rgb)

        video = self.video
        m.d.comb += _assign_rgb(video.rgb, apply_gamma(selected_rgb, regs.gamma_lut))
        with m.If(~content_active):
            m.d.comb += _assign_rgb(video.rgb, rgb888_black())
        m.d.comb += [
            video.de.eq(scan.active),
            video.h_sync.eq(scan.h_sync),
            video.v_sync.eq(scan.v_sync),
        ]

        status = self.status
        m.d.comb += [
            status.displayed_buffer.eq(displayed_buffer),
            status.displayed_base.eq(displayed_base),
            status.swap_pending.eq(swap_pending),
            status.swap_count.eq(swap_count),
            status.new_frame.eq(scan.new_frame),
            status.active.eq(content_active),
            status.x.eq(zext(scan.x, 10)),
            status.y.eq(zext(scan.y, 10)),
        ]
        return m


# ---------------------------------------------------------------------------
# HdmiCdcFramebufferScanout (core + hdmi clock domains)
# ---------------------------------------------------------------------------


class HdmiCdcFramebufferScanout(wiring.Component):
    """``case class HdmiCdcFramebufferScanout(c, timing, fifoDepth, prefillLevel,
    refillLowLevel, refillHighLevel, pixelRepeatX) extends Component``.

    Core side: a line prefetcher (``prefetchReq`` per display line, then one
    ``readReq`` per source pixel while ``projected = pushOccupancy +
    outstanding`` leaves room) feeding the 16-bit pixel ``StreamFifoCC``;
    ``scanoutRun`` arms once the FIFO reaches ``prefillLevel`` and
    ``refillActive`` is the low/high hysteresis after that.

    HDMI side (``hdmi`` domain, from ``hdmi_clock``/``hdmi_reset``): a
    free-running :class:`VideoScanout`, the test pattern, and the FIFO pop --
    gated by ``frameAligned`` so the first pixel out is at the frame origin
    -- with gamma from a synchronised copy of the LUT.  ``underflowReg`` is
    sticky until an HDMI reset.

    Status goes back through ``PulseCCByToggle`` (``newFrame``) and
    ``BufferCC`` (the rest); ``displayedBuffer``/``swapPending``/``swapCount``
    are constants here (the swap lives in ``SwapBuffer``).
    """

    def __init__(self, c: Config | None = None, timing: VideoTiming = CEA_720X480P, *,
                 fifo_depth: int = 4096, prefill_level: int = 2048,
                 refill_low_level: int = 1024, refill_high_level: int = 3072,
                 pixel_repeat_x: int = 1) -> None:
        c = Config.voodoo1() if c is None else c
        if fifo_depth & (fifo_depth - 1) or fifo_depth <= 0:
            raise ValueError("HDMI CDC FIFO depth must be a power of two")
        if pixel_repeat_x & (pixel_repeat_x - 1) or pixel_repeat_x <= 0:
            raise ValueError("horizontal pixel repeat must be a power of two")
        if not (0 < prefill_level < fifo_depth):
            raise ValueError("prefillLevel must be in (0, fifoDepth)")
        if not (0 < refill_low_level < refill_high_level):
            raise ValueError("refillLowLevel must be in (0, refillHighLevel)")
        if not refill_high_level < fifo_depth:
            raise ValueError("refillHighLevel must be below fifoDepth")
        self.c = c
        self.timing = timing
        self.fifo_depth = fifo_depth
        self.prefill_level = prefill_level
        self.refill_low_level = refill_low_level
        self.refill_high_level = refill_high_level
        self.pixel_repeat_x = pixel_repeat_x
        self.occupancy_width = ceil_log2(fifo_depth + 1)
        super().__init__({
            "hdmi_clock": In(1),
            "hdmi_reset": In(1),
            "regs": In(HdmiScanoutRegs(c)),
            "prefetch_req": Out(stream.Signature(FramebufferPrefetchReq(c))),
            "read_req": Out(stream.Signature(FramebufferReadReq(c))),
            "read_rsp": In(stream.Signature(FramebufferReadRsp())),
            "video": Out(ParallelRgbVideo()),
            "status": Out(HdmiScanoutStatus(c)),
            "fifo_push_occupancy": Out(self.occupancy_width),
            "fifo_pop_occupancy": Out(self.occupancy_width),
            "underflow": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        c, t = self.c, self.timing
        regs = self.regs
        h_width, v_width = t.h_width, t.v_width
        fifo_depth = self.fifo_depth
        occ_w = self.occupancy_width

        # `val hdmiClockDomain = ClockDomain(clock = io.hdmiClock, reset =
        # io.hdmiReset, config = ClockDomain.current.config)`: SYNC/HIGH.
        clock_domain(m, "hdmi", clk=self.hdmi_clock, rst=self.hdmi_reset, local=True)

        # `val pixelFifo = StreamFifoCC(Bits(16 bits), fifoDepth, coreClockDomain, hdmiClockDomain)`
        pixel_fifo = FifoCC(unsigned(16), fifo_depth, push_domain="sync", pop_domain="hdmi",
                            mem_attrs=spinal_fifo_ram_attrs(16, fifo_depth))
        m.submodules.pixelFifo = pixel_fifo

        # ---- core clock domain ------------------------------------------
        prefetch_x = Signal(h_width, name="prefetchX")
        prefetch_y = Signal(v_width, name="prefetchY")
        prefetch_base = Signal(c.address_width, name="prefetchBase")
        outstanding = Signal(occ_w, name="outstanding")
        scanout_run = Signal(name="scanoutRun")
        refill_active = Signal(name="refillActive", init=1)
        line_prefetched = Signal(name="linePrefetched")

        projected = Signal(occ_w + 1, name="projected")
        m.d.comb += projected.eq(zext(pixel_fifo.push_occupancy, occ_w + 1)
                                 + zext(outstanding, occ_w + 1))

        repeat_shift = ceil_log2(self.pixel_repeat_x)
        display_width = Signal(h_width, name="displayWidth")
        source_width = Signal(h_width, name="sourceWidth")
        display_height = Signal(v_width, name="displayHeight")
        display_enabled = Signal(name="displayEnabled")
        can_prefetch = Signal(name="canPrefetch")
        at_end_of_display_line = Signal(name="atEndOfDisplayLine")
        at_end_of_display_frame = Signal(name="atEndOfDisplayFrame")
        m.d.comb += [
            display_width.eq(zext(regs.display_width, h_width)),
            source_width.eq(zext(display_width >> repeat_shift, h_width)),
            display_height.eq(zext(regs.display_height, v_width)),
            display_enabled.eq(regs.framebuffer_enable
                               & (regs.display_width != 0) & (regs.display_height != 0)),
            can_prefetch.eq(display_enabled & refill_active
                            & (projected < Const(fifo_depth - 4, occ_w + 1))),
            # `prefetchX === (sourceWidth - 1).resized`: hWidth-bit wrapping subtract
            at_end_of_display_line.eq(prefetch_x == (source_width - 1)[0:h_width]),
            at_end_of_display_frame.eq(at_end_of_display_line
                                       & (prefetch_y == (display_height - 1)[0:v_width])),
        ]
        line_start_address = plane_address(
            prefetch_base, Const(0, 10), zext(prefetch_y, 10), regs.pixel_stride)
        line_end_address = plane_address(
            prefetch_base, zext((source_width - 1)[0:h_width] << repeat_shift, 10),
            zext(prefetch_y, 10), regs.pixel_stride)

        with m.If(~display_enabled):
            m.d.sync += [
                prefetch_x.eq(0),
                prefetch_y.eq(0),
                prefetch_base.eq(regs.front_base),
                scanout_run.eq(0),
                refill_active.eq(1),
                line_prefetched.eq(0),
            ]
        with m.Else():
            with m.If(~scanout_run):
                m.d.sync += [prefetch_base.eq(regs.front_base), refill_active.eq(1)]
                with m.If(pixel_fifo.push_occupancy >= self.prefill_level):
                    m.d.sync += scanout_run.eq(1)
                    with m.If(projected >= self.refill_high_level):
                        m.d.sync += refill_active.eq(0)
            with m.Else():
                with m.If(pixel_fifo.push_occupancy <= self.refill_low_level):
                    m.d.sync += refill_active.eq(1)
                with m.If(projected >= self.refill_high_level):
                    m.d.sync += refill_active.eq(0)

        m.d.comb += [
            self.prefetch_req.valid.eq(display_enabled & refill_active & ~line_prefetched),
            self.prefetch_req.payload.start_address.eq(line_start_address),
            self.prefetch_req.payload.end_address.eq(line_end_address),
            self.read_req.valid.eq(can_prefetch & line_prefetched),
            self.read_req.payload.address.eq(plane_address(
                prefetch_base, zext(prefetch_x << repeat_shift, 10), zext(prefetch_y, 10),
                regs.pixel_stride)),
        ]

        with m.If(fire(self.prefetch_req)):
            m.d.sync += line_prefetched.eq(1)

        with m.If(fire(self.read_req)):
            with m.If(at_end_of_display_line):
                m.d.sync += prefetch_x.eq(0)
                with m.If(at_end_of_display_frame):
                    m.d.sync += [
                        prefetch_y.eq(0),
                        prefetch_base.eq(regs.front_base),
                        line_prefetched.eq(0),
                    ]
                with m.Else():
                    m.d.sync += [prefetch_y.eq(prefetch_y + 1), line_prefetched.eq(0)]
            with m.Else():
                m.d.sync += prefetch_x.eq(prefetch_x + 1)

        m.d.comb += [
            pixel_fifo.push.valid.eq(self.read_rsp.valid & display_enabled),
            pixel_fifo.push.payload.eq(self.read_rsp.payload.data),
            self.read_rsp.ready.eq(pixel_fifo.push.ready),
        ]

        # `switch(readReq.fire ## push.fire) { is("10") +1; is("01") -1 }`
        read_req_fire = Signal(name="readReqFire")
        push_fire = Signal(name="pushFire")
        m.d.comb += [read_req_fire.eq(fire(self.read_req)), push_fire.eq(fire(pixel_fifo.push))]
        with m.If(read_req_fire & ~push_fire):
            m.d.sync += outstanding.eq(outstanding + 1)
        with m.Elif(~read_req_fire & push_fire):
            m.d.sync += outstanding.eq(outstanding - 1)

        # ---- `val hdmi = new ClockingArea(hdmiClockDomain)` --------------
        hdmi_test_pattern_enable = buffer_cc(
            m, regs.test_pattern_enable, init=0, o_domain="hdmi", name="hdmi_testPatternEnable")
        hdmi_gamma_lut = [
            buffer_cc(m, regs.gamma_lut[i], init=0, o_domain="hdmi", name=f"hdmi_gammaLut_{i}")
            for i in range(33)
        ]
        hdmi_content_width = zext(buffer_cc(
            m, regs.display_width, init=0, o_domain="hdmi", name="hdmi_contentWidth"), h_width)
        hdmi_content_height = zext(buffer_cc(
            m, regs.display_height, init=0, o_domain="hdmi", name="hdmi_contentHeight"), v_width)

        hdmi_scan = DomainRenamer({"sync": "hdmi"})(VideoScanout(t))
        m.submodules.hdmi_scan = hdmi_scan
        m.d.comb += hdmi_scan.enable.eq(1)
        m.submodules.hdmi_pattern = hdmi_pattern = HdmiTestPattern(t)
        m.d.comb += [
            hdmi_pattern.x.eq(hdmi_scan.x),
            hdmi_pattern.y.eq(hdmi_scan.y),
            hdmi_pattern.active.eq(hdmi_scan.active),
        ]
        hdmi_underflow_reg = Signal(name="hdmi_underflowReg")
        hdmi_content_active = Signal(name="hdmi_contentActive")
        m.d.comb += hdmi_content_active.eq(
            hdmi_scan.active & (hdmi_scan.x < hdmi_content_width)
            & (hdmi_scan.y < hdmi_content_height))

        hdmi_use_framebuffer = Signal(name="hdmi_useFramebuffer")
        m.d.comb += hdmi_use_framebuffer.eq(~hdmi_test_pattern_enable)
        hdmi_start_armed = buffer_cc(m, scanout_run, init=0, o_domain="hdmi",
                                     name="hdmi_startArmed")
        hdmi_frame_aligned = Signal(name="hdmi_frameAligned")
        with m.If(~hdmi_use_framebuffer | ~hdmi_start_armed):
            m.d.hdmi += hdmi_frame_aligned.eq(0)
        with m.Elif(~hdmi_frame_aligned & hdmi_scan.new_frame):
            m.d.hdmi += hdmi_frame_aligned.eq(1)

        # Consume one source pixel at the end of each repeated group.
        if self.pixel_repeat_x == 1:
            hdmi_repeat_group_last = Const(1)
        else:
            hdmi_repeat_group_last = (
                hdmi_scan.x[0:repeat_shift] == Const(self.pixel_repeat_x - 1, repeat_shift))
        pop = pixel_fifo.pop
        m.d.comb += pop.ready.eq(
            hdmi_use_framebuffer & hdmi_frame_aligned & hdmi_content_active
            & hdmi_repeat_group_last)
        with m.If(hdmi_use_framebuffer & hdmi_frame_aligned & hdmi_content_active & ~pop.valid):
            m.d.hdmi += hdmi_underflow_reg.eq(1)

        video = self.video
        hdmi_rgb = apply_gamma(rgb888_from_rgb565(pop.payload), hdmi_gamma_lut)
        m.d.comb += _assign_rgb(video.rgb, hdmi_rgb)
        with m.If(hdmi_test_pattern_enable):
            m.d.comb += _assign_rgb(video.rgb, hdmi_pattern.rgb)
        with m.Elif(~hdmi_frame_aligned | ~hdmi_content_active | ~pop.valid):
            m.d.comb += _assign_rgb(video.rgb, rgb888_black())
        m.d.comb += [
            video.de.eq(hdmi_scan.active),
            video.h_sync.eq(hdmi_scan.h_sync),
            video.v_sync.eq(hdmi_scan.v_sync),
        ]

        # ---- back to the core clock domain --------------------------------
        new_frame_core = pulse_cc_by_toggle(m, hdmi_scan.new_frame, i_domain="hdmi",
                                            o_domain="sync", name="newFrameCore")

        status = self.status
        m.d.comb += [
            status.displayed_buffer.eq(0),
            status.displayed_base.eq(prefetch_base),
            status.swap_pending.eq(0),
            status.swap_count.eq(0),
            status.new_frame.eq(new_frame_core),
            status.active.eq(buffer_cc(m, hdmi_content_active, init=0, name="status_active")),
            status.x.eq(buffer_cc(m, zext(hdmi_scan.x, 10), init=0, name="status_x")),
            status.y.eq(buffer_cc(m, zext(hdmi_scan.y, 10), init=0, name="status_y")),
            self.fifo_push_occupancy.eq(pixel_fifo.push_occupancy),
            self.fifo_pop_occupancy.eq(buffer_cc(m, pixel_fifo.pop_occupancy, init=0,
                                                 name="fifoPopOccupancy")),
            self.underflow.eq(buffer_cc(m, hdmi_underflow_reg, init=0, name="underflow_cc")),
        ]
        return m


# ---------------------------------------------------------------------------
# TmdsEncoder
# ---------------------------------------------------------------------------


def _count_one(value) -> Value:
    """``CountOne(bits)``: population count."""
    value = Value.cast(value)
    total = Const(0, 1)
    for i in range(len(value)):
        total = total + value[i]
    return total


class TmdsEncoder(wiring.Component):
    """``case class TmdsEncoder() extends Component`` -- DVI 8b/10b with disparity.

    ``disparity`` is ``Reg(SInt(5 bits)) init (0)``; every ``SInt`` add/sub
    on it wraps at 5 bits, including ``qBalance = (qOnes.resize(5).asSInt
    |<< 1) - 8``, which relies on that wrap for ``qOnes == 8``.
    """

    def __init__(self) -> None:
        super().__init__({
            "data": In(8),
            "control": In(2),
            "data_enable": In(1),
            "encoded": Out(10),
        })

    def elaborate(self, platform):
        m = Module()
        disparity = Signal(signed(5), name="disparity")

        ones = _count_one(self.data)
        use_xnor = Signal(name="useXnor")
        m.d.comb += use_xnor.eq((ones > 4) | ((ones == 4) & ~self.data[0]))

        q = [Signal(name=f"q_{i}") for i in range(9)]
        m.d.comb += q[0].eq(self.data[0])
        for i in range(1, 8):
            m.d.comb += q[i].eq(Mux(use_xnor, ~(q[i - 1] ^ self.data[i]), q[i - 1] ^ self.data[i]))
        m.d.comb += q[8].eq(~use_xnor)
        q_bits = Cat(*q)

        q_ones = _count_one(q_bits[0:8])
        q_balance = Signal(signed(5), name="qBalance")
        m.d.comb += q_balance.eq(((zext(q_ones, 5) << 1)[0:5].as_signed()
                                  - Const(8, signed(5)))[0:5].as_signed())

        next_encoded = Signal(10, name="nextEncoded")
        next_disparity = Signal(signed(5), name="nextDisparity")
        m.d.comb += [next_encoded.eq(0), next_disparity.eq(disparity)]

        low8 = q_bits[0:8]
        s2 = Const(2, signed(5))
        s0 = Const(0, signed(5))
        with m.If((disparity == 0) | (q_balance == 0)):
            m.d.comb += [
                next_encoded[9].eq(~q[8]),
                next_encoded[8].eq(q[8]),
                next_encoded[0:8].eq(Mux(q[8], low8, ~low8)),
                next_disparity.eq(Mux(q[8], q_balance, (-q_balance)[0:5].as_signed())),
            ]
        with m.Elif(((disparity > 0) & (q_balance > 0)) | ((disparity < 0) & (q_balance < 0))):
            m.d.comb += [
                next_encoded[9].eq(1),
                next_encoded[8].eq(q[8]),
                next_encoded[0:8].eq(~low8),
                next_disparity.eq((disparity + Mux(q[8], s2, s0) - q_balance)[0:5].as_signed()),
            ]
        with m.Else():
            m.d.comb += [
                next_encoded[9].eq(0),
                next_encoded[8].eq(q[8]),
                next_encoded[0:8].eq(low8),
                next_disparity.eq((disparity - Mux(q[8], s0, s2) + q_balance)[0:5].as_signed()),
            ]

        with m.If(self.data_enable):
            m.d.comb += self.encoded.eq(next_encoded)
            m.d.sync += disparity.eq(next_disparity)
        with m.Else():
            m.d.sync += disparity.eq(0)
            with m.Switch(self.control):
                with m.Case(0b00):
                    m.d.comb += self.encoded.eq(0b1101010100)
                with m.Case(0b01):
                    m.d.comb += self.encoded.eq(0b0010101011)
                with m.Case(0b10):
                    m.d.comb += self.encoded.eq(0b0101010100)
                with m.Default():
                    m.d.comb += self.encoded.eq(0b1010101011)
        return m


# ---------------------------------------------------------------------------
# HdmiTestPatternScanout
# ---------------------------------------------------------------------------


class HdmiTestPatternScanout(wiring.Component):
    """``case class HdmiTestPatternScanout(timing = VideoTiming()) extends Component``.

    Free-running scan + test pattern + three :class:`TmdsEncoder` lanes;
    the blue lane carries ``vSync ## hSync`` as its control word.
    """

    def __init__(self, timing: VideoTiming = DMT_640X480) -> None:
        self.timing = timing
        super().__init__({
            "active": Out(1),
            "h_sync": Out(1),
            "v_sync": Out(1),
            "rgb": Out(Rgb888()),
            "tmds": Out(data.ArrayLayout(unsigned(10), 3)),
        })

    def elaborate(self, platform):
        m = Module()
        t = self.timing
        m.submodules.scan = scan = VideoScanout(t)
        m.d.comb += scan.enable.eq(1)
        m.submodules.pattern = pattern = HdmiTestPattern(t)
        m.d.comb += [
            pattern.x.eq(scan.x),
            pattern.y.eq(scan.y),
            pattern.active.eq(scan.active),
        ]

        m.submodules.encB = enc_b = TmdsEncoder()
        m.submodules.encG = enc_g = TmdsEncoder()
        m.submodules.encR = enc_r = TmdsEncoder()
        m.d.comb += [
            enc_b.data.eq(pattern.rgb.b),
            enc_b.control.eq(Cat(scan.h_sync, scan.v_sync)),
            enc_b.data_enable.eq(scan.active),
            enc_g.data.eq(pattern.rgb.g),
            enc_g.control.eq(0),
            enc_g.data_enable.eq(scan.active),
            enc_r.data.eq(pattern.rgb.r),
            enc_r.control.eq(0),
            enc_r.data_enable.eq(scan.active),
            self.active.eq(scan.active),
            self.h_sync.eq(scan.h_sync),
            self.v_sync.eq(scan.v_sync),
            self.rgb.eq(pattern.rgb),
            self.tmds[0].eq(enc_b.encoded),
            self.tmds[1].eq(enc_g.encoded),
            self.tmds[2].eq(enc_r.encoded),
        ]
        return m


# ---------------------------------------------------------------------------
# TmdsSerializer
# ---------------------------------------------------------------------------


class TmdsSerializer(wiring.Component):
    """``case class TmdsSerializer() extends Component`` -- 10:1 LSB-first shifter.

    ``clockShift`` reloads ``0000011111`` on every ``load`` so the clock lane
    is five zeros then five ones per symbol; the data lanes shift a zero in
    at the top between loads.
    """

    CLOCK_PATTERN = 0b0000011111

    def __init__(self) -> None:
        super().__init__({
            "load": In(1),
            "symbols": In(data.ArrayLayout(unsigned(10), 3)),
            "data": Out(3),
            "clock": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        data_shift = [Signal(10, name=f"dataShift_{lane}") for lane in range(3)]
        clock_shift = Signal(10, name="clockShift", init=self.CLOCK_PATTERN)

        with m.If(self.load):
            for lane in range(3):
                m.d.sync += data_shift[lane].eq(self.symbols[lane])
            m.d.sync += clock_shift.eq(self.CLOCK_PATTERN)
        with m.Else():
            for lane in range(3):
                m.d.sync += data_shift[lane].eq(Cat(data_shift[lane][1:10], Const(0, 1)))
            m.d.sync += clock_shift.eq(Cat(clock_shift[1:10], Const(0, 1)))

        for lane in range(3):
            m.d.comb += self.data[lane].eq(data_shift[lane][0])
        m.d.comb += self.clock.eq(clock_shift[0])
        return m
