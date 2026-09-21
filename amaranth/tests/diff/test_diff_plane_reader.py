"""Short lockstep runs for ``FramebufferPlaneReader`` and
``FramebufferPlaneDirectReader`` (plan Phase 6).

Six registry rows, two modules, all ``Config.voodoo1()`` (26-bit addresses,
801-lane ``laneFifo``, ``needBurstSplitter = false`` so ``io.mem`` *is*
``internalMem``):

``FramebufferPlaneReader``
    the main row: ``tests/diff/plane_reader_stimulus.py`` attaches an
    in-order memory slave on ``io_mem_rsp`` and span-shaped
    ``io_prefetchReq``/``io_readReq`` traffic (reads follow their span's
    prefetch, with dropped lanes, repeats and a rare random address), so
    cache hits, forward skips, direct misses and long bursts all happen.
``FramebufferPlaneReaderNoDirectMiss``
    ``FramebufferPlaneReader(c, allowDirectMiss = false)``, the scanout reader
    of ``FramebufferMemSubsystem.scala:98``, under the same slave and spans
    but strictly in-order reads (``reader_traffic_800_in_order``): a read
    behind the consume pointer would wait forever here, so repeats and random
    addresses are off.  Checks that no ``source = 1`` command is ever issued.
``FramebufferPlaneReaderMemOnly``
    the same memory slave with prefetch/read left to the random generator:
    inverted spans, random read addresses.  It parks within a few thousand
    cycles -- an inverted span's wrapped ``wordCount`` asks for more beats
    than the 11-bit ``length`` field the slave answers, and the fill never
    retires -- identically on both sides, which is what the row shows.
``FramebufferPlaneReaderRandom``
    the bare random generator, kept so the ``io_mem_rsp`` parking the
    responder exists for stays reproducible (~30 live cycles per seed).
``FramebufferPlaneDirectReader`` / ``FramebufferPlaneDirectReaderMem``
    the uncached reader under the bare generator (it ignores ``rsp.source``,
    so random responses are accepted whenever ``laneQueue`` is non-empty)
    and behind the memory slave.

``cmd.ready`` and ``readRsp.ready`` are random on every row.  The full gate
is ``make diff MOD=<row> DIFF_SEEDS=1,2,3 DIFF_CYCLES=1000000``; results are
recorded in ``docs/amaranth-rewrite-plan.md`` Phase 6.

The main row's responder ``stats()`` are checked after the run so a clean
result is known to have exercised the hit path: a lockstep run that never
served a cached lane would pass just as cleanly.
"""

import json

from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


def test_diff_plane_reader(tmp_path):
    """Memory slave + span traffic: hits, misses, skips, long bursts."""
    run_short_diff("FramebufferPlaneReader", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        reads = s["SpanTraffic._Reads.beats_fired"]
        misses = s["MemSlave.miss_cmds"]
        # The responder counts a fire on the falling edge after it; the DUT
        # counters are read at the end of the seed, so they may lead by one.
        assert s["DutCounters.reqCount"] == s["DutCounters.fillHits"]
        assert 0 <= s["DutCounters.reqCount"] - reads <= 1
        # A parked reader (laneFifo full behind an outstanding miss, see the
        # stimulus module) still diffs clean; this is what catches it.
        assert reads > 20_000 // 10, f"seed {seed}: reader parked ({s})"
        assert misses > 0, f"seed {seed}: no direct miss"
        assert reads - misses > reads // 2, f"seed {seed}: hit path starved ({s})"
        assert s["SpanTraffic._Prefetches.long_spans"] > 0
        assert s["DutCounters.multiBeatBurstCount"] > 0
        assert s["DutCounters.singleBeatBurstCount"] > 0


def test_diff_plane_reader_no_direct_miss(tmp_path):
    """``allowDirectMiss = false`` (the scanout reader) under in-order traffic.

    No ``source = 1`` command may ever be issued, and the run must stay live
    (a read behind the pointer would stall this reader for good).
    """
    run_short_diff("FramebufferPlaneReaderNoDirectMiss", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        assert s["MemSlave.miss_cmds"] == 0, f"seed {seed}: direct miss issued ({s})"
        assert s["DutCounters.reqCount"] > 20_000 // 10, f"seed {seed}: reader parked ({s})"
        assert s["SpanTraffic._Reads.kind_repeat"] == s["SpanTraffic._Reads.kind_random"] == 0
        assert s["SpanTraffic._Prefetches.long_spans"] > 0


def test_diff_plane_reader_backpressured(tmp_path):
    """Starved ``cmd.ready``/``readRsp.ready``: the readRspFifo and laneFifo fill."""
    run_short_diff("FramebufferPlaneReader", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_plane_reader_mem_only(tmp_path):
    """Random (often inverted) spans and random reads behind a real memory."""
    run_short_diff("FramebufferPlaneReaderMemOnly", tmp_path)


def test_diff_plane_reader_random(tmp_path):
    """The bare generator: parks the rsp stream early, must still be clean."""
    run_short_diff("FramebufferPlaneReaderRandom", tmp_path)


def test_diff_plane_direct_reader(tmp_path):
    run_short_diff("FramebufferPlaneDirectReader", tmp_path)


def test_diff_plane_direct_reader_backpressured(tmp_path):
    """``laneQueue`` (8) and ``rspQueue`` (2) fill and stall the request side."""
    run_short_diff("FramebufferPlaneDirectReader", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_plane_direct_reader_mem(tmp_path):
    """Every response answers a real command; the lane select is checked."""
    run_short_diff("FramebufferPlaneDirectReaderMem", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        assert s["MemSlave.beats_fired"] > 1000, f"seed {seed}: {s}"
