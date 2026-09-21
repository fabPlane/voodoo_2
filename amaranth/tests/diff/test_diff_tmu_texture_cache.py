"""Short lockstep runs for ``TmuTextureCache`` (plan Phase 5).

One registry row, ``Config.voodoo1()``: 16 slots x 2 ways x 8-word lines,
16-deep request window.  The row carries the responder in
``tests/diff/tmu_texture_cache_stimulus.py``, which owns ``io_texRead_rsp``
(exactly one ``lineWords``-beat burst per accepted command, ``last`` on the
final beat), ``io_sampleRequest`` (bilinear-consistent taps from a small
working set of two packed textures, with the ``tapPackedBank`` /
``tapPackedPair`` / ``tapStartupDecode`` fields computed by the same
arithmetic the fill uses) and ``io_invalidate`` (a pulse every ~3000
cycles).  Without it the module livelocks on the first tap that decodes as
outside every LOD -- the fill never writes that tap's (bank, pair), the
continue fill re-fills the same line forever and ``owner.active`` blocks all
lookups -- so the random driver would compare two stuck designs.

The full gate is ``make diff MOD=TmuTextureCache`` (1e6 cycles x seeds
1,2,3) plus the same with ``DIFF_ARGS="--valid-density 0.9 --ready-density
0.12"``; results are recorded in ``docs/amaranth-rewrite-plan.md`` Phase 5.

``io_sampleFetch_ready`` and ``io_texRead_cmd_ready`` stay with the generator
at ``--ready-density``: the back-pressured run is where the compare-hold
skid, the ``fillFetchRsp`` queue(1) and the halted fill response get their
stalls.
"""

from conftest import run_short_diff


def test_diff_tmu_texture_cache(tmp_path):
    """Fills, hits, bilinear multi-fills, continue fills, epoch invalidation."""
    run_short_diff("TmuTextureCache", tmp_path)


def test_diff_tmu_texture_cache_backpressured(tmp_path):
    """Starved ``sampleFetch.ready`` / ``texRead.cmd.ready``: holds and stalls."""
    run_short_diff("TmuTextureCache", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.12)
