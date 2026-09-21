"""Short lockstep runs for ``FramebufferMemSubsystem`` (plan Phase 6).

Two registry rows, one module, ``Config.voodoo1()`` (no write buffer, cached
colour/aux readers, 26-bit addresses):

``FramebufferMemSubsystem``
    the main row: ``tests/diff/framebuffer_mem_subsystem_stimulus.py``
    attaches an in-order memory slave to each of the four live ``fbMem``
    ports (``source`` echoed, so the arbiters' response demux works),
    span-shaped prefetch/read traffic on the colour, aux and scanout readers
    (the scanout one strictly in order: ``allowDirectMiss = false``) and
    well-formed ``lfbReadBus`` commands.  Writes, ``flush`` and every
    ``ready`` stay random.
``FramebufferMemSubsystemRandom``
    the bare generator, kept so the parking the responder exists for stays
    reproducible: both read ports stop within a few hundred cycles of every
    seed while the two write ports keep going.

The full gate is ``make diff MOD=FramebufferMemSubsystem`` (1e6 cycles x
seeds 1,2,3) plus a backpressure run; results are in
``docs/amaranth-rewrite-plan.md`` Phase 6.

The main row's responder ``stats()`` are checked after the run so a clean
result is known to have exercised every port: a lockstep pass with a parked
read port would look the same.
"""

import json

from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


def test_diff_framebuffer_mem_subsystem(tmp_path):
    """Memory slaves + three readers' traffic + LFB reads: every port live."""
    run_short_diff("FramebufferMemSubsystem", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        # both write ports: one single-beat write per accepted io_*Write beat
        for port, cnt in (("fbColorWriteMem", "memColorWriteCmdCount"),
                          ("fbAuxWriteMem", "memAuxWriteCmdCount")):
            assert s[f"{port}.writes"] > 1000, f"seed {seed}: {port} parked ({s})"
            assert s[f"{port}.reads"] == 0
            assert 0 <= s[f"dut.stats_{cnt}"] - s[f"{port}.writes"] <= 1
        # the colour read port carries all three routes
        assert s["fbColorReadMem.route0"] > 50, f"seed {seed}: scanout fills ({s})"
        assert s["fbColorReadMem.route1"] > 50, f"seed {seed}: colour fills ({s})"
        assert s["fbColorReadMem.route2"] > 10, f"seed {seed}: lfb reads ({s})"
        assert s["fbColorReadMem.route3"] == 0
        assert s["fbAuxReadMem.route0"] + s["fbAuxReadMem.route1"] > 50
        # the readers served hits and misses; scanout only hits
        assert s["color.reads.beats_fired"] > 500 and s["aux.reads.beats_fired"] > 500
        assert s["scanout.reads.beats_fired"] > 500, f"seed {seed}: scanout parked ({s})"
        assert s["dut.stats_fillMisses"] > 0 and s["dut.stats_fillHits"] > 0
        assert s["dut.scanoutFillHits"] > 500
        assert s["scanout.reads.kind_repeat"] == s["scanout.reads.kind_random"] == 0
        assert s["dut.stats_memLfbReadCmdCount"] == s["lfb.beats_fired"] or \
            s["dut.stats_memLfbReadCmdCount"] == s["lfb.beats_fired"] + 1
        assert s["dut.stats_readMultiBeatBurstCount"] > 0
        assert s["dut.stats_writeDrainCount"] == 0          # no write buffer here


def test_diff_framebuffer_mem_subsystem_backpressured(tmp_path):
    """Starved ``cmd.ready`` / ``rsp.ready`` everywhere: the s2mPipes, the
    plane buffers' queues and the readers' FIFOs all fill."""
    run_short_diff("FramebufferMemSubsystem", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_framebuffer_mem_subsystem_random(tmp_path):
    """The bare generator: the read ports park early, must still be clean."""
    run_short_diff("FramebufferMemSubsystemRandom", tmp_path)
