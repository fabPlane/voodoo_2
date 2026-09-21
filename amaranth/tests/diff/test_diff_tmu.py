"""Short lockstep runs for the Phase 5 texture unit.

The full gate is ``make diff MOD=Tmu`` and ``make diff MOD=TmuMemOnly`` (1e6
cycles x 3 seeds each, plan §4.2); these are the `make test` versions.

Both rows need ``tests/diff/tmu_stimulus.py``: the texture cache inside
``Tmu`` only takes an ``io_texRead_rsp`` beat while a fill is outstanding, so
a random held response parks the stream and nothing else would be compared.
``TexMemSlave`` answers every ``io_texRead_cmd`` with the burst it asked for.
The ``Tmu`` row adds ``TmuTraffic`` on ``io_input`` -- texture-shaped pixels
so the cache hits as well as misses; ``TmuMemOnly`` drives every arithmetic
input (S, T, 1/W, gradients, LOD register, mode bits) as a raw random
pattern on every pixel, which is the harsher stimulus for the front end
(every ``clz``/shift/wrap path, ``1/W`` of both signs and zero, the 22-bit
address wrap, ``lodLevel`` at both clamps), while keeping the packed tables
consistent with the format -- a *fully* random ``io_input`` (the generic
driver's) produces a texel address outside every LOD within a few beats and
the cache then livelocks on continue-fills, freezing the rest of the run.

Both rows check the responder ``stats()`` after the run: a parked TMU (the
cache chasing continue-fills, or ``inFlightCount`` stuck) still diffs clean
on both sides, so a pass has to show pixels going *out* in proportion to
the cycles run, texture memory being read, and -- on the traffic row -- the
cache serving most pixels without a fill.
"""

import json

from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


def _check_flowing(tmp_path, seed: int, cycles: int) -> dict:
    s = _stats(tmp_path, seed)
    pixels_in, pixels_out = s["DutCounters.pixels_in"], s["DutCounters.pixels_out"]
    assert pixels_in == s["TmuTraffic.beats_fired"]
    # What the DUT can hold: nine front-end stages, the m2sPipe and the
    # 16-deep FIFO before sampleRequest, then maxOutstanding (7) beyond it.
    assert 0 <= pixels_in - pixels_out <= 9 + 1 + 16 + 7, f"seed {seed}: {s}"
    # A parked run (see the stimulus module) stops here.
    assert pixels_out > cycles // 10, f"seed {seed}: TMU parked ({s})"
    assert s["TexMemSlave.cmds"] > 0, f"seed {seed}: nothing read from texture memory"
    # Every burst asked for was (all but at most the last beat) delivered.
    assert s["TexMemSlave.beats_queued_total"] - s["TexMemSlave.beats_fired"] <= 8
    return s


def test_diff_tmu_traffic(tmp_path):
    """``Tmu(Config.voodoo1())`` with texture-shaped input traffic and a memory slave."""
    run_short_diff("Tmu", tmp_path)
    for seed in (1, 2, 3):
        s = _check_flowing(tmp_path, seed, 20_000)
        # Every fill is one cmd; the walk must make the cache *hit* most of
        # the time, or only the miss path has been compared.
        assert s["TexMemSlave.cmds"] < s["DutCounters.pixels_in"] // 2, \
            f"seed {seed}: hit path starved ({s})"
        assert s["TmuTraffic.kind_wild"] > 0
        assert s["TmuTraffic.kind_random"] == 0


def test_diff_tmu_wild_input(tmp_path):
    """Same DUT, every arithmetic input a random pattern (tables consistent)."""
    run_short_diff("TmuMemOnly", tmp_path)
    for seed in (1, 2, 3):
        s = _check_flowing(tmp_path, seed, 20_000)
        assert s["TmuTraffic.kind_walk"] == 0 and s["TmuTraffic.kind_wild"] > 0


def test_diff_tmu_backpressure(tmp_path):
    """Traffic row with the consumer stalling most of the time.

    Drives ``inFlightCount`` to ``maxOutstanding`` and holds it there, so the
    ``canAllocate`` / ``bypassSafe`` gating, the 16-deep request FIFO filling
    up and the ``>/->`` pipes around the cache all see back-pressure.
    """
    run_short_diff("Tmu", tmp_path, cycles=10_000, valid_density=0.9, ready_density=0.12)
