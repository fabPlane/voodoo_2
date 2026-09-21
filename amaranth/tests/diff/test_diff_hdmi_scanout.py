"""Short lockstep runs for the ``HdmiScanout.scala`` components (plan Phase 7.1).

Eleven registry rows over seven Spinal components, all ``Config.voodoo1()``
where a ``Config`` is taken:

``VideoScanout`` / ``VideoScanoutRun``
    the 800 x 525 timing generator with ``enable`` random per cycle (the
    counters restart constantly) and pinned high (free-running).
``HdmiTestPattern``, ``TmdsEncoder``, ``TmdsSerializer``, ``HdmiTestPatternScanout``
    the generator alone.  ``HdmiTestPatternScanout`` has no inputs at all.
``HdmiFramebufferScanout`` / ``HdmiFramebufferScanoutScene``
    the single-clock scanout under the generator, and with ``io_regs_*``
    held as register scenes (``tests/diff/hdmi_scanout_stimulus.py``).
``HdmiCdcFramebufferScanout`` / ``...Random`` / ``...Small``
    the two-clock scanout.  **The harness drives one clock**, but the pixel
    clock is a plain input port here, so the ``HdmiClock`` responder toggles
    it on the core clock's falling edges -- both Verilator models see the
    same edges -- and the whole design is compared, ``StreamFifoCC`` and all.
    The main row holds the canonical registers (what ``Core`` drives) and
    answers reads in order; ``Random`` claims only the clock; ``Small`` is the
    8x4-in-12x7 build of ``HdmiScanoutTest.scala`` with a 64-deep FIFO, so
    frames come every 84 pixel clocks and ``frameAligned``, ``underflow``,
    the refill hysteresis and the end-of-frame reload all recur.

The full gate is ``make diff MOD=<row>`` (1e6 cycles x seeds 1,2,3); results
are in ``docs/amaranth-rewrite-plan.md`` Phase 7.  The responder stats are
checked after the short runs so a clean result is known to have done
something: pixel clock edges, HDMI resets, read traffic, frames.
"""

import json

import pytest
from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


@pytest.mark.parametrize("module", [
    "VideoScanout", "VideoScanoutRun", "HdmiTestPattern", "TmdsEncoder", "TmdsSerializer",
    "HdmiTestPatternScanout", "HdmiFramebufferScanout",
])
def test_diff_single_clock_rows(module, tmp_path):
    run_short_diff(module, tmp_path)


def test_diff_framebuffer_scanout_scene(tmp_path):
    run_short_diff("HdmiFramebufferScanoutScene", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        assert s["RegScene.scenes"] >= 1
        assert s["Activity.active_cycles"] > 0


def test_diff_cdc_scanout_small(tmp_path):
    """The many-frames build: the FIFO path must actually have shown pixels."""
    run_short_diff("HdmiCdcFramebufferScanoutSmall", tmp_path, cycles=40_000)
    frames = lit = 0
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        assert s["HdmiClock.hdmi_rising_edges"] > 40_000 // 6
        assert s["HdmiClock.hdmi_resets"] >= 1
        assert s["Activity.new_frames"] > 10
        frames += s["Activity.new_frames"]
        lit += s["Activity.lit_pixels"]
        assert s["ReadRspSlave.beats_fired"] == s["ReadRspSlave.read_reqs"] \
            or s["ReadRspSlave.queued"] > 0
    assert lit > 0, "no framebuffer pixel ever reached io_video"
    assert frames > 100


def test_diff_cdc_scanout_main(tmp_path):
    """cea720x480p: prefill to 3072 and wait for the (450k-pixel) frame."""
    run_short_diff("HdmiCdcFramebufferScanout", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        assert s["HdmiClock.hdmi_rising_edges"] == 20_000 // 2
        assert s["ReadRspSlave.read_reqs"] >= 3072
        assert s["Activity.max_push_occupancy"] >= 3072
        assert s["Activity.io_prefetchReq_fires"] >= 4


def test_diff_cdc_scanout_random(tmp_path):
    run_short_diff("HdmiCdcFramebufferScanoutRandom", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        assert s["HdmiClock.hdmi_rising_edges"] > 0
        assert s["Activity.io_readRsp_fires"] > 0
