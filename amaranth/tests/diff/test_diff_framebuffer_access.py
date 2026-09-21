"""Short lockstep runs for the Phase 4 ``FramebufferAccess`` module.

Two registry rows, one module (``Config.voodoo1()``):

``FramebufferAccess``
    the bare generator.  The two response streams carry no ordering tag and
    are accepted whenever the 4-deep response queues have room, so random
    responses never park the module -- an unmatched one waits in the queue
    for the next pixel that needs a read, on both sides alike.  The mode bits
    flip per pixel, which also exercises the bypass-vs-fetched ordering
    hazard the module docstring describes, identically on both sides.
``FramebufferAccessMem``
    ``tests/diff/framebuffer_access_stimulus.py`` answers every
    ``fbReadColorReq`` / ``fbReadAuxReq`` beat in order from a plane memory
    with a random per-beat latency, and counts the traffic so the run is
    known to be live.

The full gate is ``make diff MOD=<row>`` (1e6 cycles x 3 seeds, plan §4.2)
plus the back-pressure run
(``DIFF_ARGS="--valid-density 0.9 --ready-density 0.12"``), which is what
fills the 32-deep queues and wraps the 5-bit ``inFlightCount``; results are
in ``docs/amaranth-rewrite-plan.md`` Phase 4.
"""

import json

from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


def test_diff_framebuffer_access(tmp_path):
    """Random responses, random modes per pixel."""
    run_short_diff("FramebufferAccess", tmp_path)


def test_diff_framebuffer_access_backpressure(tmp_path):
    """Starved ``output.ready`` and request ``ready``: queues fill, the counter wraps."""
    run_short_diff("FramebufferAccess", tmp_path, cycles=20_000,
                   valid_density=0.9, ready_density=0.12)


def test_diff_framebuffer_access_mem(tmp_path):
    """Plane memory answering real requests; the counters prove the run was live."""
    run_short_diff("FramebufferAccessMem", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        cycles = s["PixelCounters.cycles"]
        assert s["PixelCounters.pixels_in"] > cycles // 10, f"seed {seed}: parked ({s})"
        assert s["PixelCounters.pixels_out"] > cycles // 10, f"seed {seed}: parked ({s})"
        assert s["Color.reqs"] > cycles // 20 and s["Aux.reqs"] > cycles // 20
        # the responder counts a fire on the falling edge after it, so it may
        # trail the DUT-side count by the beat still on the wire
        assert 0 <= s["Color.reqs"] - s["Color.beats_fired"] <= s["Color.max_outstanding"]
        assert s["PixelCounters.color_reqs"] == s["Color.reqs"]
        assert s["PixelCounters.aux_reqs"] == s["Aux.reqs"]
        assert s["PixelCounters.z_func_fail"] > 0, f"seed {seed}: no depth kill"


def test_diff_framebuffer_access_mem_backpressure(tmp_path):
    run_short_diff("FramebufferAccessMem", tmp_path, cycles=20_000,
                   valid_density=0.9, ready_density=0.12)
