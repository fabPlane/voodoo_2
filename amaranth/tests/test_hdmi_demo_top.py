"""Smoke test for the HDMI demo top before spending PnR time on it.

One full DMT 640x480 frame through pysim (420k pixel clocks): the sync pulse
and data-enable counts must be exactly the timing's, and on a few sampled
lines the pattern must be the four bands. USER-key blanking and the LEDs are
checked on a small timing (the logic is timing-independent, and a full frame
is ~2 min of pysim). A last check confirms the Verilog exposes both clock
domains as ports.
"""

import pytest
from amaranth.sim import Simulator

from avoodoo.gowin.hdmi_demo_top import HdmiDemoTop
from avoodoo.hdmi.scanout import DMT_640X480, VideoTiming
from gen import gen_module

CORE = 20e-9    # 50 MHz
PIXEL = 40e-9   # 25 MHz (the board's CLKDIV/5 of the 125 MHz serial clock)

TINY = VideoTiming(active_width=16, active_height=4, h_front_porch=1, h_sync=2,
                   h_back_porch=1, v_front_porch=1, v_sync=1, v_back_porch=1)


def _run(dut, tb):
    sim = Simulator(dut)
    sim.add_clock(CORE, domain="sync")
    sim.add_clock(PIXEL, domain="hdmi")
    sim.add_testbench(tb)
    sim.run()


def _frame(dut, timing: VideoTiming, *, hold_key: bool, rgb_lines):
    """Observe exactly one frame's worth of pixels, starting at a line start.

    Returns sync/de counts and, for the lines in ``rgb_lines`` (numbered from
    the first observed line), the ``{x: (r, g, b)}`` seen while ``de``.
    """
    stats = {"hs": 0, "vs": 0, "de": 0, "hs_edges": 0, "vs_edges": 0, "rgb": {}}

    async def tb(ctx):
        ctx.set(dut.key0_n, 0 if hold_key else 1)
        # Let the FFSynchronizer pass the key (two ticks) and the registered
        # outputs catch up, then align to a line start (de falls, then rises).
        for _ in range(4):
            await ctx.tick("hdmi")
        while ctx.get(dut.hdmi_de):
            await ctx.tick("hdmi")
        while not ctx.get(dut.hdmi_de):
            await ctx.tick("hdmi")
        hs_prev = ctx.get(dut.hdmi_hs)
        vs_prev = ctx.get(dut.hdmi_vs)
        x = y = 0
        for _ in range(timing.h_total * timing.v_total):
            hs, vs, de = ctx.get(dut.hdmi_hs), ctx.get(dut.hdmi_vs), ctx.get(dut.hdmi_de)
            stats["hs"] += not hs      # active low
            stats["vs"] += not vs
            stats["de"] += de
            stats["hs_edges"] += hs_prev and not hs
            stats["vs_edges"] += vs_prev and not vs
            if hs_prev and not hs:
                y += 1
            hs_prev, vs_prev = hs, vs
            if de:
                if y in rgb_lines:
                    stats["rgb"].setdefault(y, {})[x] = (
                        ctx.get(dut.hdmi_r), ctx.get(dut.hdmi_g), ctx.get(dut.hdmi_b))
                x += 1
            else:
                x = 0
            await ctx.tick("hdmi")

    _run(dut, tb)
    return stats


def _expected_rgb(t: VideoTiming, x: int, y: int):
    if x < t.active_width // 4:
        return (255, 0, 0)
    if x < t.active_width // 2:
        return (0, 255, 0)
    if x < t.active_width * 3 // 4:
        return (0, 0, 255)
    x8, y8 = x & 0xFF, y & 0xFF
    return (x8, y8, x8 ^ y8)


def _check_counts(s, t: VideoTiming):
    assert s["hs"] == t.h_sync * t.v_total, "h_sync low samples"      # 96 * 525
    assert s["hs_edges"] == t.v_total, "lines"                         # 525
    assert s["vs"] == t.v_sync * t.h_total, "v_sync low samples"      # 2 * 800
    assert s["vs_edges"] == 1, "one frame"
    assert s["de"] == t.active_width * t.active_height, "de samples"  # 640 * 480


def test_one_dmt_frame():
    t = DMT_640X480
    # Observation starts at line 1 (the first line start after reset), so
    # sampled line k is scan line k + 1.
    lines = {0, 1, 254, 255, 478}
    s = _frame(HdmiDemoTop(t), t, hold_key=False, rgb_lines=lines)
    _check_counts(s, t)
    assert set(s["rgb"]) == lines
    for k, row in s["rgb"].items():
        assert len(row) == t.active_width
        for x, rgb in row.items():
            assert rgb == _expected_rgb(t, x, k + 1), (x, k + 1)


@pytest.mark.parametrize("hold_key", [False, True])
def test_user_key_blanks_video_only(hold_key):
    t = TINY
    s = _frame(HdmiDemoTop(t), t, hold_key=hold_key, rgb_lines=set(range(t.v_total)))
    _check_counts(s, t)
    pixels = [rgb for row in s["rgb"].values() for rgb in row.values()]
    assert len(pixels) == t.active_width * t.active_height
    if hold_key:
        assert all(p == (0, 0, 0) for p in pixels), "USER key must black the picture"
    else:
        assert any(p != (0, 0, 0) for p in pixels)


def test_leds_toggle_in_their_own_domains():
    dut = HdmiDemoTop(TINY, heartbeat_bit=3, frame_led_bit=1)
    seen_heartbeat, seen_frames = set(), set()

    async def tb(ctx):
        ctx.set(dut.key0_n, 1)
        for _ in range(8 * TINY.h_total * TINY.v_total):
            led = ctx.get(dut.led_n)
            seen_heartbeat.add(led & 1)
            seen_frames.add(led >> 1)
            await ctx.tick("hdmi")

    _run(dut, tb)
    assert seen_heartbeat == {0, 1}, "core-clock heartbeat must toggle"
    assert seen_frames == {0, 1}, "pixel-domain frame counter must toggle"


def test_verilog_has_both_clock_domains_as_ports(tmp_path):
    path = gen_module.emit("avoodoo.gowin.hdmi_demo_top:HdmiDemoTop", tmp_path)
    text = path.read_text()
    assert "module HdmiDemoTop(" in text
    for port in ("input clk;", "input rst;", "input hdmi_clk;", "input hdmi_rst;",
                 "input key0_n;", "output [1:0] led_n;", "output hdmi_de;"):
        assert port in text, port
    assert "posedge hdmi_clk" in text and "posedge clk" in text
