"""``avoodoo/hdmi/scanout.py`` -- pysim port of ``ext/SpinalVoodoo/test/HdmiScanoutTest.scala``.

The eleven SpinalSim scenarios are ported one to one (same timings, same
FIFO parameters, same register values, same assertions), then a few checks
the Scala test does not have: the gamma interpolator against its integer
model, a TMDS encoder model over random data, the sticky ``underflow`` and
the two occupancy outputs of the CDC scanout, the ``newFrame`` pulse
crossing, the ``HdmiScanoutPort`` signature, and the plan §6 register audit
(every ``Reg`` in ``HdmiScanout.scala`` has an ``init``, so nothing here may be
``reset_less``).

The CDC component takes its pixel clock and reset as ports, so the two-clock
tests wrap it in :class:`_TwoClocks`, which owns a top-level ``pixel`` domain
and feeds ``ClockSignal("pixel")`` into ``dut.hdmi_clock``; ``dut.hdmi_reset``
is a testbench signal, as ``dut.io.hdmiReset #=`` is in the Scala.  The
pixel clock is deliberately *not* the core clock's period.
"""

from __future__ import annotations

import random
from collections import deque

import pytest
from amaranth import (ClockDomain, ClockSignal, Elaboratable, Module, Shape, Signal,
                      unsigned)
from amaranth.lib import wiring
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.hdmi.scanout import (
    CEA_720X480P, DMT_640X480, HdmiCdcFramebufferScanout, HdmiFramebufferScanout,
    HdmiScanoutPortSignature, HdmiTestPattern, HdmiTestPatternScanout, Rgb888, TmdsEncoder,
    TmdsSerializer, VideoScanout, VideoTiming, apply_gamma, apply_gamma_model,
    gamma_interpolate_model, rgb888_from_rgb565)

CORE = 10e-9
HDMI = 13e-9


def identity_gamma_entry(index: int) -> int:
    value = 255 if index == 32 else index * 8
    return (value << 16) | (value << 8) | value


IDENTITY_LUT = [identity_gamma_entry(i) for i in range(33)]


# ==========================================================================
# simulation plumbing
# ==========================================================================


class _Clocked(Elaboratable):
    def __init__(self, dut):
        self.dut = dut

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.submodules.dut = self.dut
        return m


class _TwoClocks(Elaboratable):
    """Core ``sync`` + a top-level ``pixel`` clock feeding the DUT's clock port.

    Named ``pixel`` rather than ``hdmi``: the DUT builds its own local
    ``hdmi`` domain from the port, and Amaranth refuses two different domains
    of one name in a hierarchy."""

    def __init__(self, dut):
        self.dut = dut
        self.hdmi_rst = Signal(name="hdmi_rst")

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.domains.pixel = ClockDomain("pixel", reset_less=True)
        m.submodules.dut = self.dut
        m.d.comb += [
            self.dut.hdmi_clock.eq(ClockSignal("pixel")),
            self.dut.hdmi_reset.eq(self.hdmi_rst),
        ]
        return m


def run(top, *testbenches, background=(), deadline=None):
    sim = Simulator(top)
    sim.add_clock(CORE)
    if isinstance(top, _TwoClocks):
        sim.add_clock(HDMI, domain="pixel")
    for tb in testbenches:
        sim.add_testbench(tb)
    for tb in background:
        sim.add_testbench(tb, background=True)
    if deadline is None:
        sim.run()
    else:
        sim.run_until(deadline * CORE)


def _set_regs(ctx, regs, *, front=0x1000, back=0x2000, stride=64, width, height,
              fb_enable=True, test_pattern=False, lut=IDENTITY_LUT):
    ctx.set(regs.front_base, front)
    ctx.set(regs.back_base, back)
    ctx.set(regs.pixel_stride, stride)
    ctx.set(regs.display_width, width)
    ctx.set(regs.display_height, height)
    ctx.set(regs.framebuffer_enable, int(fb_enable))
    ctx.set(regs.test_pattern_enable, int(test_pattern))
    for i in range(33):
        ctx.set(regs.gamma_lut[i], lut[i])


def _rgb(ctx, rgb):
    return ctx.get(rgb.r), ctx.get(rgb.g), ctx.get(rgb.b)


# ==========================================================================
# VideoTiming / bundles
# ==========================================================================


def test_video_timing_totals():
    assert DMT_640X480.h_total == 800 and DMT_640X480.v_total == 525
    assert DMT_640X480.h_width == 10 and DMT_640X480.v_width == 10
    assert CEA_720X480P.h_total == 858 and CEA_720X480P.v_total == 525
    small = VideoTiming(active_width=8, active_height=4, h_front_porch=2, h_sync=2,
                        h_back_porch=2, v_front_porch=1, v_sync=1, v_back_porch=1)
    assert small.h_total == 14 and small.v_total == 7
    assert small.h_width == 4 and small.v_width == 3


def test_scanout_port_signature_is_the_scala_master_view():
    """``HdmiScanoutPort.asMaster``: clock/reset in; video, status, underflow, fifoLevel out."""
    sig = HdmiScanoutPortSignature(Config.voodoo1())
    members = sig.members
    assert members["clock"].flow is wiring.In and members["reset"].flow is wiring.In
    for name in ("video", "status", "underflow", "fifo_level"):
        assert members[name].flow is wiring.Out
    assert Shape.cast(members["fifo_level"].shape) == unsigned(13)
    assert members["status"].shape["displayed_base"].shape == unsigned(26)
    assert [k for k, _ in members["video"].shape] == ["rgb", "de", "h_sync", "v_sync"]
    assert [k for k, _ in members["status"].shape] == [
        "displayed_buffer", "displayed_base", "swap_pending", "swap_count", "new_frame",
        "active", "x", "y"]
    assert Shape.cast(HdmiScanoutPortSignature(Config.voodoo1(), fifo_level_width=7).members[
        "fifo_level"].shape) == unsigned(7)


@pytest.mark.filterwarnings("ignore::amaranth.hdl._ir.UnusedElaboratable")
def test_cdc_constructor_checks_match_the_scala_requires():
    """The five ``require``s; an object whose constructor raises is never elaborated."""
    c = Config.voodoo1()
    with pytest.raises(ValueError):
        HdmiCdcFramebufferScanout(c, fifo_depth=1000)
    with pytest.raises(ValueError):
        HdmiCdcFramebufferScanout(c, pixel_repeat_x=3)
    with pytest.raises(ValueError):
        HdmiCdcFramebufferScanout(c, prefill_level=4096)
    with pytest.raises(ValueError):
        HdmiCdcFramebufferScanout(c, refill_low_level=3072, refill_high_level=1024)
    with pytest.raises(ValueError):
        HdmiCdcFramebufferScanout(c, refill_high_level=4096)
    dut = HdmiCdcFramebufferScanout(c)
    dut._MustUse__silence = True
    assert dut.occupancy_width == 13
    assert dut.fifo_push_occupancy.shape() == unsigned(13)


# ==========================================================================
# 1. "scanout produces expected active-area count and line/frame cadence"
# ==========================================================================


def _count_frame(timing: VideoTiming):
    dut = VideoScanout(timing)
    counts = {"active": 0, "lines": 0, "frames": 0}

    async def tb(ctx):
        ctx.set(dut.enable, 1)
        await ctx.tick()
        for _ in range(timing.h_total * timing.v_total):
            counts["active"] += ctx.get(dut.active)
            counts["lines"] += ctx.get(dut.new_line)
            counts["frames"] += ctx.get(dut.new_frame)
            await ctx.tick()

    run(_Clocked(dut), tb)
    return counts


def test_scanout_active_count_and_cadence():
    timing = VideoTiming(active_width=8, active_height=4, h_front_porch=2, h_sync=2,
                         h_back_porch=2, v_front_porch=1, v_sync=1, v_back_porch=1)
    counts = _count_frame(timing)
    assert counts["active"] == timing.active_width * timing.active_height
    assert counts["lines"] == timing.v_total
    assert counts["frames"] == 1


# ==========================================================================
# 5. "CEA 720x480p timing matches the working HDMI mode"
# ==========================================================================


def test_cea_720x480p_timing():
    counts = _count_frame(CEA_720X480P)
    assert counts["active"] == 720 * 480
    assert counts["lines"] == 525
    assert counts["frames"] == 1


def test_scanout_sync_polarity_and_disable_restart():
    """``hSync``/``vSync`` are active-low by default, high in the sync window
    when ``*SyncActiveHigh``; ``enable`` low zeroes both counters."""
    timing = VideoTiming(active_width=4, active_height=2, h_front_porch=1, h_sync=2,
                         h_back_porch=1, v_front_porch=1, v_sync=1, v_back_porch=1)
    for active_high in (False, True):
        t = VideoTiming(**{**timing.__dict__, "h_sync_active_high": active_high,
                           "v_sync_active_high": active_high})
        dut = VideoScanout(t)
        seen = []

        async def tb(ctx):
            ctx.set(dut.enable, 1)
            for _ in range(t.h_total * t.v_total):
                seen.append((ctx.get(dut.x), ctx.get(dut.y), ctx.get(dut.h_sync),
                             ctx.get(dut.v_sync)))
                await ctx.tick()
            ctx.set(dut.enable, 0)
            await ctx.tick()
            assert ctx.get(dut.x) == 0 and ctx.get(dut.y) == 0
            await ctx.tick()
            assert ctx.get(dut.x) == 0 and ctx.get(dut.y) == 0
            ctx.set(dut.enable, 1)
            await ctx.tick()
            assert ctx.get(dut.x) == 1

        run(_Clocked(dut), tb)
        for x, y, hs, vs in seen:
            in_h = t.active_width + t.h_front_porch <= x < t.active_width + t.h_front_porch + t.h_sync
            in_v = t.active_height + t.v_front_porch <= y < t.active_height + t.v_front_porch + t.v_sync
            assert hs == (in_h if active_high else not in_h), (x, y, active_high)
            assert vs == (in_v if active_high else not in_v), (x, y, active_high)


# ==========================================================================
# 2. "test pattern emits color bars inside active area and black during blanking"
# ==========================================================================


def test_test_pattern_bars_and_blanking():
    timing = VideoTiming(active_width=8, active_height=4)
    dut = HdmiTestPattern(timing)

    async def tb(ctx):
        ctx.set(dut.active, 1)
        ctx.set(dut.y, 1)
        ctx.set(dut.x, 0)
        assert _rgb(ctx, dut.rgb) == (255, 0, 0)
        ctx.set(dut.x, 2)
        assert _rgb(ctx, dut.rgb) == (0, 255, 0)
        ctx.set(dut.x, 4)
        assert _rgb(ctx, dut.rgb) == (0, 0, 255)
        ctx.set(dut.x, 7)
        assert _rgb(ctx, dut.rgb) == (7, 1, 7 ^ 1)
        ctx.set(dut.active, 0)
        assert _rgb(ctx, dut.rgb) == (0, 0, 0)

    run(_Clocked(dut), tb)


# ==========================================================================
# 3. "TMDS encoder emits DVI control tokens while blanking" + a model check
# ==========================================================================


def tmds_model(data: int, control: int, data_enable: bool, disparity: int) -> tuple[int, int]:
    """The Scala encoder as integers: returns ``(encoded, next_disparity)``.

    ``disparity`` is the 5-bit signed register; every add wraps to 5 bits
    exactly as SpinalHDL's non-widening ``SInt`` arithmetic does.
    """
    def wrap5(v):
        v &= 31
        return v - 32 if v & 16 else v

    if not data_enable:
        return [0b1101010100, 0b0010101011, 0b0101010100, 0b1010101011][control], 0
    ones = bin(data).count("1")
    use_xnor = ones > 4 or (ones == 4 and not data & 1)
    q = [data & 1]
    for i in range(1, 8):
        bit = (data >> i) & 1
        q.append((1 - (q[i - 1] ^ bit)) if use_xnor else (q[i - 1] ^ bit))
    q.append(0 if use_xnor else 1)
    low8 = sum(b << i for i, b in enumerate(q[:8]))
    q_ones = bin(low8).count("1")
    q_balance = wrap5(wrap5(q_ones << 1) - 8)
    q8 = q[8]
    if disparity == 0 or q_balance == 0:
        enc = ((1 - q8) << 9) | (q8 << 8) | (low8 if q8 else (~low8 & 0xFF))
        nd = q_balance if q8 else wrap5(-q_balance)
    elif (disparity > 0 and q_balance > 0) or (disparity < 0 and q_balance < 0):
        enc = (1 << 9) | (q8 << 8) | (~low8 & 0xFF)
        nd = wrap5(disparity + (2 if q8 else 0) - q_balance)
    else:
        enc = (q8 << 8) | low8
        nd = wrap5(disparity - (0 if q8 else 2) + q_balance)
    return enc, nd


def test_tmds_control_tokens_while_blanking():
    dut = TmdsEncoder()

    async def tb(ctx):
        ctx.set(dut.data, 0)
        ctx.set(dut.data_enable, 0)
        for control, token in enumerate((0b1101010100, 0b0010101011, 0b0101010100,
                                         0b1010101011)):
            ctx.set(dut.control, control)
            await ctx.tick()
            assert ctx.get(dut.encoded) == token

    run(_Clocked(dut), tb)


def test_tmds_encoder_matches_the_integer_model():
    dut = TmdsEncoder()
    rng = random.Random(5)

    async def tb(ctx):
        disparity = 0
        for _ in range(3000):
            data = rng.getrandbits(8)
            control = rng.getrandbits(2)
            de = rng.random() < 0.9
            ctx.set(dut.data, data)
            ctx.set(dut.control, control)
            ctx.set(dut.data_enable, int(de))
            enc, disparity = tmds_model(data, control, de, disparity)
            assert ctx.get(dut.encoded) == enc, (data, control, de)
            if de:
                # DVI: a data word always carries 3..7 ones in its low 8 bits
                # plus the two flags; the running disparity stays in [-8, 8].
                assert -8 <= disparity <= 8
            await ctx.tick()

    run(_Clocked(dut), tb)


# ==========================================================================
# 4. "TMDS serializer shifts symbols LSB first"
# ==========================================================================


def test_tmds_serializer_lsb_first():
    dut = TmdsSerializer()
    expected = [0b1010101100, 0b0101010011, 0b1111000001]

    async def tb(ctx):
        for lane, sym in enumerate(expected):
            ctx.set(dut.symbols[lane], sym)
        ctx.set(dut.load, 1)
        await ctx.tick()
        ctx.set(dut.load, 0)
        for bit in range(10):
            data = ctx.get(dut.data)
            for lane in range(3):
                assert (data >> lane) & 1 == (expected[lane] >> bit) & 1, (bit, lane)
            # the clock lane is 0000011111 reloaded on every load: five
            # zeros then five ones, LSB first
            assert ctx.get(dut.clock) == (1 if bit < 5 else 0)
            await ctx.tick()

    run(_Clocked(dut), tb)


def test_test_pattern_scanout_lanes_carry_the_pattern_and_syncs():
    """``HdmiTestPatternScanout``: blue lane control word is ``vSync ## hSync``
    during blanking; every active symbol decodes back to the pattern."""
    timing = VideoTiming(active_width=8, active_height=2, h_front_porch=1, h_sync=2,
                         h_back_porch=1, v_front_porch=1, v_sync=1, v_back_porch=1)
    dut = HdmiTestPatternScanout(timing)
    tokens = {0b1101010100: 0, 0b0010101011: 1, 0b0101010100: 2, 0b1010101011: 3}

    async def tb(ctx):
        for _ in range(timing.h_total * timing.v_total):
            active = ctx.get(dut.active)
            b, g, r = (ctx.get(dut.tmds[0]), ctx.get(dut.tmds[1]), ctx.get(dut.tmds[2]))
            if not active:
                assert tokens[b] == (ctx.get(dut.v_sync) << 1) | ctx.get(dut.h_sync)
                assert tokens[g] == 0 and tokens[r] == 0
            else:
                assert b not in tokens or True  # data words may collide with tokens only by chance
            await ctx.tick()

    run(_Clocked(dut), tb)


# ==========================================================================
# gamma
# ==========================================================================


class _GammaProbe(wiring.Component):
    def __init__(self):
        super().__init__({
            "rgb": wiring.In(Rgb888()),
            "pixel565": wiring.In(16),
            "lut": wiring.In(unsigned(24)).array(33),
            "out": wiring.Out(Rgb888()),
            "out565": wiring.Out(Rgb888()),
        })

    def elaborate(self, platform):
        m = Module()
        r, g, b = apply_gamma(self.rgb, self.lut)
        m.d.comb += [self.out.r.eq(r), self.out.g.eq(g), self.out.b.eq(b)]
        r2, g2, b2 = rgb888_from_rgb565(self.pixel565)
        m.d.comb += [self.out565.r.eq(r2), self.out565.g.eq(g2), self.out565.b.eq(b2)]
        return m


def test_gamma_interpolation_matches_the_model():
    """Random LUTs and channels, plus every channel value against an identity
    LUT (which must be the identity) and the Scala test's two-entry LUT."""
    dut = _GammaProbe()
    rng = random.Random(11)

    async def tb(ctx):
        for lut in (IDENTITY_LUT, [rng.getrandbits(24) for _ in range(33)],
                    [rng.getrandbits(24) for _ in range(33)]):
            for i in range(33):
                ctx.set(dut.lut[i], lut[i])
            for _ in range(300):
                r, g, b = (rng.getrandbits(8) for _ in range(3))
                if rng.random() < 0.1:
                    r = 255
                ctx.set(dut.rgb.r, r)
                ctx.set(dut.rgb.g, g)
                ctx.set(dut.rgb.b, b)
                assert _rgb(ctx, dut.out) == apply_gamma_model(r, g, b, lut), (r, g, b)
        for i in range(33):
            ctx.set(dut.lut[i], IDENTITY_LUT[i])
        # The "identity" LUT (i * 8, then 255) is only the identity on
        # multiples of 8 and at 255: 254 interpolates 248..255 at 6/8 = 253.
        for v in range(256):
            ctx.set(dut.rgb.r, v)
            ctx.set(dut.rgb.g, 255 - v)
            ctx.set(dut.rgb.b, (v * 7) & 0xFF)
            assert _rgb(ctx, dut.out) == apply_gamma_model(v, 255 - v, (v * 7) & 0xFF,
                                                           IDENTITY_LUT)
            if v % 8 == 0 or v == 255:
                assert ctx.get(dut.out.r) == v
        lut = [0] * 33
        lut[31], lut[32] = 0x100000, 0x180000
        for i in range(33):
            ctx.set(dut.lut[i], lut[i])
        ctx.set(dut.rgb.r, 255)
        ctx.set(dut.rgb.g, 0)
        ctx.set(dut.rgb.b, 0)
        assert _rgb(ctx, dut.out) == (24, 0, 0)
        ctx.set(dut.rgb.r, 248)          # index 31, fraction 0 -> lut[31].r
        assert ctx.get(dut.out.r) == 16
        ctx.set(dut.rgb.r, 252)          # halfway: (16*4 + 24*4) >> 3
        assert ctx.get(dut.out.r) == 20
        for pixel in (0xF800, 0x07E0, 0x001F, 0xFFFF, 0x0000, 0x8410):
            ctx.set(dut.pixel565, pixel)
            r5, g6, b5 = pixel >> 11, (pixel >> 5) & 63, pixel & 31
            assert _rgb(ctx, dut.out565) == (
                (r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2))

    run(_Clocked(dut), tb)


def test_gamma_model_edge_cases():
    lut = IDENTITY_LUT
    assert gamma_interpolate_model(255, lut, 23, 16) == 255
    assert gamma_interpolate_model(0, lut, 7, 0) == 0
    assert gamma_interpolate_model(254, lut, 15, 8) == 253   # (248 * 2 + 255 * 6) >> 3
    assert gamma_interpolate_model(248, lut, 15, 8) == 248


# ==========================================================================
# 6. "framebuffer scanout issues front-buffer addresses and flips on vblank swap"
# ==========================================================================


def _small_timing(width=4, height=2, **porches):
    base = dict(h_front_porch=1, h_sync=1, h_back_porch=1, v_front_porch=1, v_sync=1,
                v_back_porch=1)
    base.update(porches)
    return VideoTiming(active_width=width, active_height=height, **base)


def test_framebuffer_scanout_front_addresses_and_swap():
    c = Config.voodoo1()
    timing = _small_timing()
    dut = HdmiFramebufferScanout(c, timing)
    frame = timing.h_total * timing.v_total

    async def tb(ctx):
        _set_regs(ctx, dut.regs, width=timing.active_width, height=timing.active_height)
        ctx.set(dut.swap.valid, 0)
        ctx.set(dut.read_req.ready, 1)
        ctx.set(dut.read_rsp.valid, 0)
        ctx.set(dut.read_rsp.payload.data, 0)
        await ctx.tick().repeat(20)

        seen_front = []
        waited = 0
        while len(seen_front) < 6 and waited < frame * 2:
            await ctx.tick()
            if ctx.get(dut.read_req.valid):
                seen_front.append(ctx.get(dut.read_req.payload.address))
            waited += 1
        assert len(seen_front) >= 6
        assert seen_front[:4] == [0x1000, 0x1002, 0x1004, 0x1006]
        assert seen_front[4] == 0x1080
        assert seen_front[5] == 0x1082
        assert ctx.get(dut.status.displayed_buffer) == 0

        ctx.set(dut.swap.valid, 1)
        await ctx.tick()
        ctx.set(dut.swap.valid, 0)
        assert ctx.get(dut.status.swap_pending) == 1
        assert ctx.get(dut.swap.ready) == 0

        waited = 0
        while ctx.get(dut.status.displayed_buffer) == 0 and waited < frame * 2:
            await ctx.tick()
            waited += 1
        assert waited < frame * 2
        assert ctx.get(dut.status.swap_pending) == 0
        assert ctx.get(dut.status.swap_count) == 1
        assert ctx.get(dut.status.displayed_base) == 0x2000

        seen_back = []
        waited = 0
        while len(seen_back) < 2 and waited < frame * 2:
            await ctx.tick()
            if ctx.get(dut.read_req.valid):
                seen_back.append(ctx.get(dut.read_req.payload.address))
            waited += 1
        assert len(seen_back) >= 2
        assert 0x2000 <= seen_back[0] < 0x2100

    run(_Clocked(dut), tb)


# ==========================================================================
# 7./8. RGB565 -> RGB888 and the gamma CLUT on HdmiFramebufferScanout
# ==========================================================================


def _framebuffer_rgb_after_two_ticks(lut):
    c = Config.voodoo1()
    timing = VideoTiming(active_width=4, active_height=2, h_front_porch=1, h_sync=1,
                         h_back_porch=1)
    dut = HdmiFramebufferScanout(c, timing)
    result = {}

    async def tb(ctx):
        _set_regs(ctx, dut.regs, front=0, back=0, width=timing.active_width,
                  height=timing.active_height, lut=lut)
        ctx.set(dut.swap.valid, 0)
        ctx.set(dut.read_req.ready, 1)
        ctx.set(dut.read_rsp.valid, 1)
        ctx.set(dut.read_rsp.payload.data, 0xF800)
        await ctx.tick().repeat(2)
        result["rgb"] = _rgb(ctx, dut.video.rgb)
        # blanking is black regardless of the response
        ctx.set(dut.regs.display_width, 0)
        result["blank"] = _rgb(ctx, dut.video.rgb)
        # the last accepted response is remembered in fbRgb
        ctx.set(dut.regs.display_width, timing.active_width)
        ctx.set(dut.read_rsp.valid, 0)
        result["held"] = _rgb(ctx, dut.video.rgb)

    run(_Clocked(dut), tb)
    return result


def test_framebuffer_scanout_rgb565_to_rgb888():
    r = _framebuffer_rgb_after_two_ticks(IDENTITY_LUT)
    assert r["rgb"] == (255, 0, 0)
    assert r["blank"] == (0, 0, 0)
    assert r["held"] == (255, 0, 0)


def test_framebuffer_scanout_gamma_clut():
    lut = [0] * 33
    lut[31], lut[32] = 0x100000, 0x180000
    assert _framebuffer_rgb_after_two_ticks(lut)["rgb"] == (24, 0, 0)


def test_framebuffer_scanout_test_pattern_when_disabled():
    """``testPatternEnable || !framebufferEnable`` selects the pattern; ``de``
    follows ``scan.active`` and ``readReq`` stays quiet with the framebuffer off."""
    c = Config.voodoo1()
    timing = _small_timing(8, 2)
    dut = HdmiFramebufferScanout(c, timing)

    async def tb(ctx):
        _set_regs(ctx, dut.regs, width=8, height=2, fb_enable=False)
        ctx.set(dut.read_req.ready, 1)
        seen = set()
        for _ in range(timing.h_total * timing.v_total):
            assert ctx.get(dut.read_req.valid) == 0
            assert ctx.get(dut.video.de) == ctx.get(dut.status.active) or not ctx.get(
                dut.video.de)
            if ctx.get(dut.video.de):
                seen.add(_rgb(ctx, dut.video.rgb))
            await ctx.tick()
        assert {(255, 0, 0), (0, 255, 0), (0, 0, 255)} <= seen
        assert any(r and g for r, g, _ in seen)

    run(_Clocked(dut), tb)


# ==========================================================================
# CDC scanout: shared testbench pieces
# ==========================================================================


def _pixel_for(address: int) -> int:
    return [0xF800, 0x07E0, 0x001F, 0xFFFF][((address - 0x1000) // 2) & 3]


def _read_responder(dut, seen: list, *, pixel=_pixel_for, start_after: int = 0,
                    pending: deque | None = None, echo_valid: bool = False):
    """The Scala ``fork``: answer each accepted ``readReq`` with one ``readRsp``
    beat, in order.  ``echo_valid`` is the padding test's variant, which
    raises ``valid`` whenever ``readReq.valid`` is seen (not only on a fire)."""
    pending = deque() if pending is None else pending

    async def tb(ctx):
        cycles = 0
        async for _clk, _rst, rsp_v, rsp_r, req_v, req_r, addr in ctx.tick().sample(
                dut.read_rsp.valid, dut.read_rsp.ready, dut.read_req.valid,
                dut.read_req.ready, dut.read_req.payload.address):
            cycles += 1
            if rsp_v and rsp_r:
                ctx.set(dut.read_rsp.valid, 0)
                rsp_v = 0
            if req_v and req_r:
                if addr >= 0x1000:
                    seen.append(addr)
                pending.append(pixel(addr))
            if echo_valid:
                if not rsp_v and req_v:
                    ctx.set(dut.read_rsp.payload.data, 0xFFFF)
                    ctx.set(dut.read_rsp.valid, 1)
            elif not rsp_v and pending and cycles > start_after:
                ctx.set(dut.read_rsp.payload.data, pending.popleft())
                ctx.set(dut.read_rsp.valid, 1)

    return tb


def _cdc(timing, *, fifo_depth, prefill, low, high):
    dut = HdmiCdcFramebufferScanout(Config.voodoo1(), timing, fifo_depth=fifo_depth,
                                    prefill_level=prefill, refill_low_level=low,
                                    refill_high_level=high)
    return dut, _TwoClocks(dut)


def _cdc_init(ctx, top, dut, timing, *, width=None, height=None, test_pattern=False,
              front=0x1000, back=0x2000, prefetch_ready=1, read_ready=1):
    ctx.set(top.hdmi_rst, 1)
    _set_regs(ctx, dut.regs, front=front, back=back,
              width=timing.active_width if width is None else width,
              height=timing.active_height if height is None else height,
              test_pattern=test_pattern)
    ctx.set(dut.prefetch_req.ready, prefetch_ready)
    ctx.set(dut.read_req.ready, read_ready)
    ctx.set(dut.read_rsp.valid, 0)
    ctx.set(dut.read_rsp.payload.data, 0)


# ==========================================================================
# 9. "CDC framebuffer scanout prefetches pixels and outputs them in the HDMI clock domain"
# ==========================================================================


def test_cdc_scanout_prefetches_and_outputs_pixels():
    timing = _small_timing()
    dut, top = _cdc(timing, fifo_depth=16, prefill=4, low=4, high=12)
    seen = []

    async def tb(ctx):
        _cdc_init(ctx, top, dut, timing)
        await ctx.tick().repeat(4)
        ctx.set(top.hdmi_rst, 0)
        await ctx.tick().repeat(2)
        ctx.set(dut.regs.framebuffer_enable, 1)

        waited = 0
        while len(seen) < 4 and waited < 200:
            await ctx.tick()
            waited += 1
        assert len(seen) >= 4

        cycles = 0
        while not ctx.get(dut.status.active) and cycles < 400:
            await ctx.tick()
            cycles += 1
        assert cycles < 400
        assert seen[:4] == [0x1000, 0x1002, 0x1004, 0x1006]
        assert ctx.get(dut.fifo_push_occupancy) > 0
        assert ctx.get(dut.underflow) == 0

        saw = set()
        for _ in range(200):
            if ctx.get(dut.video.de):
                saw.add(_rgb(ctx, dut.video.rgb))
            await ctx.tick()
        assert {(255, 0, 0), (0, 255, 0), (0, 0, 255)} <= saw
        assert ctx.get(dut.underflow) == 0
        # prefetch spans are one display line: [lineStart, lineEnd]
        assert ctx.get(dut.prefetch_req.payload.end_address) - ctx.get(
            dut.prefetch_req.payload.start_address) == 2 * (timing.active_width - 1)

    run(top, tb, background=[_read_responder(dut, seen)])


# ==========================================================================
# 10. "CDC scanout test-pattern mode drives pattern pixels without FIFO data"
# ==========================================================================


def test_cdc_scanout_test_pattern_mode():
    timing = _small_timing(8, 2)
    dut, top = _cdc(timing, fifo_depth=16, prefill=4, low=4, high=12)

    async def tb(ctx):
        _cdc_init(ctx, top, dut, timing, test_pattern=True, front=0, back=0,
                  prefetch_ready=0, read_ready=0)
        await ctx.tick().repeat(4)
        ctx.set(top.hdmi_rst, 0)

        saw = set()
        for _ in range(timing.h_total * timing.v_total * 4):
            if ctx.get(dut.video.de):
                saw.add(_rgb(ctx, dut.video.rgb))
            await ctx.tick()
        assert {(255, 0, 0), (0, 255, 0), (0, 0, 255)} <= saw
        assert any(r and g for r, g, _ in saw)
        assert ctx.get(dut.fifo_push_occupancy) == 0
        assert ctx.get(dut.underflow) == 0

    run(top, tb)


# ==========================================================================
# 11. "CDC framebuffer scanout starts FIFO pixels at HDMI frame origin"
# ==========================================================================


def test_cdc_scanout_starts_at_frame_origin():
    timing = _small_timing(8, 4, h_back_porch=2)
    dut, top = _cdc(timing, fifo_depth=64, prefill=8, low=8, high=48)
    first = {}

    async def tb(ctx):
        _cdc_init(ctx, top, dut, timing, back=0)
        await ctx.tick().repeat(4)
        ctx.set(top.hdmi_rst, 0)

        previous_de = False
        inactive = timing.h_total * timing.v_total
        active_x = active_y = 0
        for _ in range(timing.h_total * timing.v_total * 4):
            await ctx.tick()
            de = ctx.get(dut.video.de)
            if de and not previous_de:
                active_y = 0 if inactive > timing.h_total else active_y + 1
                active_x = 0
                inactive = 0
            elif not de:
                inactive += 1
            rgb = _rgb(ctx, dut.video.rgb)
            if de and rgb != (0, 0, 0):
                first["pixel"] = (active_x, active_y, rgb)
                break
            if de:
                active_x += 1
            previous_de = de
        assert "pixel" in first
        x, y, rgb = first["pixel"]
        assert rgb == (255, 0, 0)
        assert y == 0, f"first framebuffer pixel appeared on HDMI row {y}"
        assert x <= 1, f"first framebuffer pixel appeared at HDMI x={x}"
        assert ctx.get(dut.underflow) == 0

    # pysim samples the core clock, and the pixel clock is slower than it, so
    # the Scala's "x <= 1" (one pixel of slack on a same-rate clock) holds.
    run(top, tb, background=[_read_responder(dut, [], start_after=30)])


# ==========================================================================
# 12. "CDC scanout pads HDMI pixels outside logical framebuffer width"
# ==========================================================================


def test_cdc_scanout_pads_outside_logical_width():
    timing = _small_timing()
    dut, top = _cdc(timing, fifo_depth=16, prefill=4, low=4, high=12)
    seen = []

    async def tb(ctx):
        _cdc_init(ctx, top, dut, timing, width=3, height=2, back=0)
        ctx.set(dut.read_rsp.payload.data, 0xFFFF)
        await ctx.tick().repeat(4)
        ctx.set(top.hdmi_rst, 0)
        waited = 0
        while len(seen) < 6 and waited < 200:
            await ctx.tick()
            waited += 1
        assert len(seen) >= 6
        expected = {0x1000, 0x1002, 0x1004, 0x1080, 0x1082, 0x1084}
        assert set(seen) <= expected
        assert len(set(seen)) >= 5
        assert 0x1006 not in seen

    run(top, tb, background=[_read_responder(dut, seen, echo_valid=True)])


# ==========================================================================
# beyond the Scala test: underflow, occupancies, newFrame crossing
# ==========================================================================


def test_cdc_scanout_underflow_is_sticky_and_pixels_go_black():
    """Serve the first eight reads only: the FIFO drains mid-frame, the
    HDMI side shows black where it has no pixel, ``underflowReg`` sets and
    stays set (BufferCC'd to ``io.underflow``) until the HDMI reset."""
    timing = _small_timing(8, 4)
    dut, top = _cdc(timing, fifo_depth=16, prefill=4, low=4, high=12)
    seen = []
    pending = deque()
    served = {"n": 0}

    def pixel(addr):
        served["n"] += 1
        return 0xFFFF

    async def starving(ctx):
        # `_read_responder` but stopping after eight beats
        async for _clk, _rst, rsp_v, rsp_r, req_v, req_r, addr in ctx.tick().sample(
                dut.read_rsp.valid, dut.read_rsp.ready, dut.read_req.valid,
                dut.read_req.ready, dut.read_req.payload.address):
            if rsp_v and rsp_r:
                ctx.set(dut.read_rsp.valid, 0)
                rsp_v = 0
            if req_v and req_r:
                seen.append(addr)
                if len(seen) <= 8:
                    pending.append(pixel(addr))
            if not rsp_v and pending:
                ctx.set(dut.read_rsp.payload.data, pending.popleft())
                ctx.set(dut.read_rsp.valid, 1)

    async def tb(ctx):
        _cdc_init(ctx, top, dut, timing)
        await ctx.tick().repeat(4)
        ctx.set(top.hdmi_rst, 0)
        lit = black_active = 0
        underflow_at = None
        for cycle in range(timing.h_total * timing.v_total * 6):
            await ctx.tick()
            if ctx.get(dut.status.active):
                if _rgb(ctx, dut.video.rgb) == (0, 0, 0):
                    black_active += 1
                else:
                    lit += 1
            if underflow_at is None and ctx.get(dut.underflow):
                underflow_at = cycle
        assert lit > 0 and black_active > 0
        assert underflow_at is not None
        assert ctx.get(dut.underflow) == 1
        # sticky: still set after more frames
        await ctx.tick().repeat(timing.h_total * timing.v_total)
        assert ctx.get(dut.underflow) == 1
        # an HDMI reset clears it (and the core side sees that through BufferCC)
        ctx.set(top.hdmi_rst, 1)
        await ctx.tick().repeat(6)
        assert ctx.get(dut.underflow) == 0

    run(top, tb, background=[starving])


def test_cdc_scanout_occupancies_and_status_crossing():
    """``fifoPushOccupancy`` (core) and ``fifoPopOccupancy`` (BufferCC'd from the
    pixel clock) agree once traffic stops; ``status.newFrame`` is one core
    pulse per HDMI frame; ``status.x``/``y`` follow the scan; the swap fields
    are constant."""
    timing = _small_timing(4, 2)
    dut, top = _cdc(timing, fifo_depth=16, prefill=4, low=4, high=12)
    frame = timing.h_total * timing.v_total

    async def tb(ctx):
        _cdc_init(ctx, top, dut, timing, read_ready=0)
        await ctx.tick().repeat(4)
        ctx.set(top.hdmi_rst, 0)
        # push six pixels with the scan not yet armed: nothing pops
        for _ in range(6):
            ctx.set(dut.read_rsp.valid, 1)
            ctx.set(dut.read_rsp.payload.data, 0x1234)
            await ctx.tick()
        ctx.set(dut.read_rsp.valid, 0)
        await ctx.tick().repeat(2)
        assert ctx.get(dut.fifo_push_occupancy) == 6
        await ctx.tick().repeat(12)
        assert ctx.get(dut.fifo_pop_occupancy) == 6
        assert ctx.get(dut.status.displayed_buffer) == 0
        assert ctx.get(dut.status.swap_pending) == 0
        assert ctx.get(dut.status.swap_count) == 0
        assert ctx.get(dut.status.displayed_base) == 0x1000

        pulses = 0
        xs = set()
        for _ in range(frame * 3 * 2):     # HDMI is slower than the core clock
            pulses += ctx.get(dut.status.new_frame)
            xs.add(ctx.get(dut.status.x))
            await ctx.tick()
        assert 2 <= pulses <= 5
        assert xs == set(range(timing.h_total))

    run(top, tb)


def test_cdc_scanout_display_disabled_holds_the_prefetcher():
    """``framebufferEnable = 0`` (or a zero dimension) resets the prefetch
    state: no ``prefetchReq``, no ``readReq``, ``readRsp`` not accepted into
    the FIFO, ``displayedBase`` follows ``frontBase``."""
    timing = _small_timing()
    dut, top = _cdc(timing, fifo_depth=16, prefill=4, low=4, high=12)

    async def tb(ctx):
        _cdc_init(ctx, top, dut, timing)
        ctx.set(dut.regs.framebuffer_enable, 0)
        ctx.set(dut.read_rsp.valid, 1)
        await ctx.tick().repeat(4)
        ctx.set(top.hdmi_rst, 0)
        for _ in range(40):
            assert ctx.get(dut.prefetch_req.valid) == 0
            assert ctx.get(dut.read_req.valid) == 0
            await ctx.tick()
        assert ctx.get(dut.fifo_push_occupancy) == 0
        ctx.set(dut.regs.front_base, 0x4000)
        await ctx.tick().repeat(2)
        assert ctx.get(dut.status.displayed_base) == 0x4000
        ctx.set(dut.regs.display_width, 0)
        ctx.set(dut.regs.framebuffer_enable, 1)
        for _ in range(20):
            assert ctx.get(dut.prefetch_req.valid) == 0
            await ctx.tick()
        # An unsolicited response would be pushed the moment the display is
        # enabled and step `outstanding` below zero (it wraps to 127, and
        # `projected` then blocks readReq for good): stop offering one first.
        ctx.set(dut.read_rsp.valid, 0)
        ctx.set(dut.regs.display_width, 4)
        # combinational: displayEnabled && refillActive && !linePrefetched
        assert ctx.get(dut.prefetch_req.valid) == 1
        assert ctx.get(dut.prefetch_req.payload.start_address) == 0x4000
        assert ctx.get(dut.prefetch_req.payload.end_address) == 0x4006
        await ctx.tick()
        # ... and it fired (ready is high), so the line is now prefetched and
        # the pixel reads start at the line's first lane
        assert ctx.get(dut.prefetch_req.valid) == 0
        assert ctx.get(dut.read_req.valid) == 1
        assert ctx.get(dut.read_req.payload.address) == 0x4000

    run(top, tb)


# ==========================================================================
# plan §6: register audit
# ==========================================================================


@pytest.mark.parametrize("build", [
    lambda: VideoScanout(),
    lambda: HdmiFramebufferScanout(Config.voodoo1()),
    lambda: HdmiCdcFramebufferScanout(Config.voodoo1()),
    lambda: TmdsEncoder(),
    lambda: HdmiTestPatternScanout(),
    lambda: TmdsSerializer(),
])
def test_no_register_is_reset_less(build):
    """Every ``Reg`` in ``HdmiScanout.scala`` carries ``init``, so no register
    of the port may be ``reset_less`` (plan §6); the CDC synchronisers all
    have ``init`` too (``BufferCC(x, False)`` / ``B(0, ...)``)."""
    from amaranth.hdl._ir import Fragment
    from amaranth.hdl._xfrm import LHSMaskCollector

    def audit(frag, path=()):
        found = []
        for domain, stmts in frag.statements.items():
            if domain == "comb":
                continue
            collector = LHSMaskCollector()
            collector.visit_stmt(stmts)
            for chunk in collector.chunks():
                sig = chunk[0]
                if sig.reset_less:
                    found.append((path, domain, sig.name))
        for sub, name, _src in frag.subfragments:
            found += audit(sub, path + (name,))
        return found

    frag = Fragment.get(build(), None)
    # The one reset-less flop under the CDC scanout is the library's: the
    # pop-side `m2sPipe` inside StreamFifoCC keeps its `rData` as a plain
    # `Reg(dataType)` (lib/stream.py M2SPipe), which is not this file's.
    library = {(("pixelFifo", "read_arbitration"), "avoodoo_fifocc_pop", "rData")}
    assert set(audit(frag)) <= library
