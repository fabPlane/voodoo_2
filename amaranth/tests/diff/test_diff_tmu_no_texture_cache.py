"""Short lockstep runs for ``TmuNoTextureCache`` and the Console's ``Tmu`` (plan Phase 8.0).

Three registry rows:

* ``TmuNoTextureCache`` -- ``Config.voodoo1().copy(useTexFillCache = false)``,
  the module on its own with the default 16-deep request window;
* ``TmuNoTextureCacheConsole`` -- the same module with the Tang Console's
  configuration (``CoreGowinGen.scala:73``: ``texFillRequestWindow = 2``);
* ``TmuConsole`` -- the whole ``Tmu`` elaborated with the Console
  configuration, i.e. ``tmu.py`` selecting ``TmuNoTextureCache`` from
  ``c.use_tex_fill_cache``; same stimulus as the ``Tmu`` row
  (``tests/diff/tmu_stimulus.py``).

The first two carry ``tests/diff/tmu_no_texture_cache_stimulus.py``: the
``TexMemSlave`` from the ``Tmu`` row answers every one-word command, and
``io_sampleRequest`` gets bilinear footprints whose taps share words, so the
"several taps, one read" fold is compared, plus fully random requests.

The full gate is ``make diff MOD=<row>`` at 1e6 cycles x seeds 1,2,3 for each,
plus ``DIFF_ARGS="--valid-density 0.9 --ready-density 0.12"``; results are in
``docs/amaranth-rewrite-plan.md`` Phase 8.0.  Every run checks the responder
stats afterwards: a run that parked (a held response beat, a stuck join)
still diffs clean on both sides, so a pass has to show fetches in proportion
to the cycles run.
"""

import json

from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


def _check_direct_flowing(tmp_path, seed: int, cycles: int, *, min_rate: float) -> dict:
    s = _stats(tmp_path, seed)
    reqs = s["DirectRequests.beats_fired"]
    fetches = s["FetchCounter.fetches"]
    assert fetches > cycles * min_rate, f"seed {seed}: direct path parked ({s})"
    # One fetch per request; at most the window + expander + queue + one in flight.
    assert 0 <= reqs - fetches <= 16 + 1 + 16 + 2, f"seed {seed}: {s}"
    assert s["TexMemSlave.cmds"] >= fetches, f"seed {seed}: fewer reads than fetches ({s})"
    assert s["TexMemSlave.beats_queued_total"] == s["TexMemSlave.cmds"], \
        "every command is a one-word burst"
    assert s["DirectRequests.kind_one_word"] > 0 and s["DirectRequests.kind_two_words"] > 0
    assert s["DirectRequests.kind_random"] > 0 and s["DirectRequests.kind_point"] > 0
    return s


def test_diff_tmu_no_texture_cache(tmp_path):
    """Point and bilinear requests, shared and scattered words, random memory latency."""
    run_short_diff("TmuNoTextureCache", tmp_path)
    for seed in (1, 2, 3):
        _check_direct_flowing(tmp_path, seed, 20_000, min_rate=0.05)


def test_diff_tmu_no_texture_cache_console_window(tmp_path):
    """The Console's 2-deep request window."""
    run_short_diff("TmuNoTextureCacheConsole", tmp_path)
    for seed in (1, 2, 3):
        _check_direct_flowing(tmp_path, seed, 20_000, min_rate=0.05)


def test_diff_tmu_no_texture_cache_backpressured(tmp_path):
    """Starved ``sampleFetch.ready`` / ``texRead.cmd.ready``: the single ``outValid``
    slot, the 16-deep word queue and the fork's link bits all stall."""
    run_short_diff("TmuNoTextureCache", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.12)
    for seed in (1, 2, 3):
        _check_direct_flowing(tmp_path, seed, 10_000, min_rate=0.01)


def _check_tmu_flowing(tmp_path, seed: int, cycles: int, *, min_rate: float) -> dict:
    s = _stats(tmp_path, seed)
    pixels_in, pixels_out = s["DutCounters.pixels_in"], s["DutCounters.pixels_out"]
    assert pixels_in == s["TmuTraffic.beats_fired"]
    # Nine front-end stages, the m2sPipe and the 16-deep FIFO before
    # sampleRequest, then maxOutstanding (7) beyond it.
    assert 0 <= pixels_in - pixels_out <= 9 + 1 + 16 + 7, f"seed {seed}: {s}"
    assert pixels_out > cycles * min_rate, f"seed {seed}: TMU parked ({s})"
    assert s["TexMemSlave.cmds"] > 0, f"seed {seed}: nothing read from texture memory"
    assert s["TexMemSlave.beats_queued_total"] == s["TexMemSlave.cmds"], \
        "the direct path reads one word per command"
    # No cache: every textured pixel reads memory at least once.
    assert s["TexMemSlave.cmds"] >= pixels_out // 2, f"seed {seed}: reads too few ({s})"
    return s


def test_diff_tmu_console(tmp_path):
    """``Tmu(consoleConfig)``: the direct path inside the whole texture unit."""
    run_short_diff("TmuConsole", tmp_path)
    for seed in (1, 2, 3):
        s = _check_tmu_flowing(tmp_path, seed, 20_000, min_rate=0.05)
        assert s["TmuTraffic.kind_wild"] > 0 and s["TmuTraffic.kind_random"] == 0


def test_diff_tmu_console_backpressure(tmp_path):
    """The Console Tmu with the consumer stalling most of the time."""
    run_short_diff("TmuConsole", tmp_path, cycles=10_000, valid_density=0.9,
                   ready_density=0.12)
    for seed in (1, 2, 3):
        _check_tmu_flowing(tmp_path, seed, 10_000, min_rate=0.01)
