"""HDMI test-pattern demo for the Tang Retro Console 138K (hardware checkpoint).

Second Gowin checkpoint after the Phase 0.3 spike. Where the spike proved that
Amaranth's Verilog goes through ``gw_sh`` at all, this one proves the pieces
the real Console build (plan Phase 8) is made of:

* :class:`avoodoo.hdmi.scanout.HdmiTestPatternScanout` -- the ported video
  timing generator + pattern, diffed clean against Spinal -- running in a
  **second clock domain** (``hdmi``, the 25.2 MHz pixel clock) under a
  ``DomainRenamer``, exactly how ``HdmiCdcFramebufferScanout`` runs it;
* the vendor PLL + ``CLKDIV`` + DVI TX around it, driven from the Amaranth
  module's parallel RGB / hs / vs / de outputs (``hw/hdmi_demo/``);
* a **cross-clock path**: the USER key (an asynchronous pad) goes through an
  ``FFSynchronizer`` into the pixel domain and blanks the picture while held;
* a core-clock (``sync``, 50 MHz) heartbeat on ``led_n[0]`` and a pixel-domain
  frame counter on ``led_n[1]``, so both clocks are visibly alive.

Unlike ``SpikeBlinky`` this top does **not** own its clocks: ``sync`` and
``hdmi`` are ordinary top-level domains, so the emitted Verilog has ``clk`` /
``rst`` / ``hdmi_clk`` / ``hdmi_rst`` ports that the board top
(``hw/hdmi_demo/hdmi_demo_board_top.v``) wires to the oscillator, the CLKDIV
output and ``~pll_lock``. That is also what lets the pysim smoke test drive
both domains with ``Simulator.add_clock``.

Picture (``HdmiTestPattern``, unchanged from the Scala): four vertical bands
over 640x480 -- red, green, blue, then ``(x, y, x ^ y)`` on the low 8 bits of
the counters. Black while the USER key is held; syncs keep running so the
monitor stays locked.
"""

from amaranth import Module, Signal
from amaranth.hdl import Cat, DomainRenamer
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, Out

from avoodoo.hdmi.scanout import DMT_640X480, HdmiTestPatternScanout, VideoTiming

__all__ = ["HdmiDemoTop", "HEARTBEAT_BIT", "FRAME_LED_BIT"]

#: ``led_n[0]`` = inverted bit of a free-running ``sync`` counter:
#: 50 MHz / 2^25 = ~1.5 Hz.
HEARTBEAT_BIT = 24
#: ``led_n[1]`` = inverted bit of the frame counter: toggles every 32 frames
#: (~0.5 s at 60 Hz).
FRAME_LED_BIT = 5


class HdmiDemoTop(wiring.Component):
    key0_n: In(1)
    hdmi_r: Out(8)
    hdmi_g: Out(8)
    hdmi_b: Out(8)
    hdmi_hs: Out(1)
    hdmi_vs: Out(1)
    hdmi_de: Out(1)
    led_n: Out(2)

    def __init__(self, timing: VideoTiming = DMT_640X480, *,
                 heartbeat_bit: int = HEARTBEAT_BIT, frame_led_bit: int = FRAME_LED_BIT):
        self.timing = timing
        self._heartbeat_bit = heartbeat_bit
        self._frame_led_bit = frame_led_bit
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        # Pixel clock domain `hdmi`. Deliberately *not* defined here (no
        # `m.domains.hdmi = ClockDomain(...)`): a domain that is used but never
        # defined is a top-level domain of the emitted module, so Amaranth
        # gives it `hdmi_clk` / `hdmi_rst` ports (synchronous, active-high
        # reset, like `sync`'s `clk` / `rst`). Defining it would create an
        # undriven internal clock that yosys ties to 0.

        # --- the ported scanout, in the pixel domain -----------------------
        scanout = DomainRenamer({"sync": "hdmi"})(HdmiTestPatternScanout(self.timing))
        m.submodules.scanout = scanout

        # --- USER key -> pixel domain (the cross-clock path) ---------------
        key0_hdmi = Signal(init=1)
        m.submodules.key0_ff = DomainRenamer({"sync": "hdmi"})(
            FFSynchronizer(self.key0_n, key0_hdmi, init=1))
        blank = Signal()
        m.d.comb += blank.eq(~key0_hdmi)

        # --- registered video outputs (one pixel of latency on all six) ----
        rgb = scanout.rgb
        m.d.hdmi += [
            self.hdmi_hs.eq(scanout.h_sync),
            self.hdmi_vs.eq(scanout.v_sync),
            self.hdmi_de.eq(scanout.active),
        ]
        with m.If(blank):
            m.d.hdmi += [self.hdmi_r.eq(0), self.hdmi_g.eq(0), self.hdmi_b.eq(0)]
        with m.Else():
            m.d.hdmi += [self.hdmi_r.eq(rgb.r), self.hdmi_g.eq(rgb.g), self.hdmi_b.eq(rgb.b)]

        # --- frame counter (pixel domain): one count per v_sync assertion --
        # DMT v_sync is active low: count the falling edge of the raw signal.
        vs_prev = Signal(init=1)
        frames = Signal(self._frame_led_bit + 1)
        m.d.hdmi += vs_prev.eq(scanout.v_sync)
        with m.If(vs_prev & ~scanout.v_sync):
            m.d.hdmi += frames.eq(frames + 1)

        # --- heartbeat (core domain) --------------------------------------
        heartbeat = Signal(self._heartbeat_bit + 1)
        m.d.sync += heartbeat.eq(heartbeat + 1)

        # Both LEDs are active low: 0 = lit.
        m.d.comb += self.led_n.eq(Cat(~heartbeat[self._heartbeat_bit],
                                      ~frames[self._frame_led_bit]))
        return m
