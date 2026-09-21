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
"""

from conftest import run_short_diff


def test_diff_tmu_traffic(tmp_path):
    """``Tmu(Config.voodoo1())`` with texture-shaped input traffic and a memory slave."""
    run_short_diff("Tmu", tmp_path)


def test_diff_tmu_wild_input(tmp_path):
    """Same DUT, every arithmetic input a random pattern (tables consistent)."""
    run_short_diff("TmuMemOnly", tmp_path)


def test_diff_tmu_backpressure(tmp_path):
    """Traffic row with the consumer stalling most of the time.

    Drives ``inFlightCount`` to ``maxOutstanding`` and holds it there, so the
    ``canAllocate`` / ``bypassSafe`` gating, the 16-deep request FIFO filling
    up and the ``>/->`` pipes around the cache all see back-pressure.
    """
    run_short_diff("Tmu", tmp_path, cycles=10_000, valid_density=0.9, ready_density=0.12)
