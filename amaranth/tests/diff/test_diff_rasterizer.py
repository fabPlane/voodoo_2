"""Short lockstep runs for the Phase 3 rasterizer (``raster/rasterizer.py``).

The full gate is ``make diff MOD=SpanRasterizer`` and ``make diff
MOD=Rasterizer`` (1e6 cycles x 3 seeds, plan §4.2); these are the
``make test`` versions.  Both modules pair every Spinal port mechanically --
no ``overrides`` in ``tools/diff/registry.py`` -- which
``tests/test_rasterizer.py::test_port_surface_matches_the_committed_spinal_portmap``
checks without Verilator.
"""

from conftest import run_short_diff


def test_diff_span_rasterizer(tmp_path):
    """``Rasterizer.SpanRasterizer(c)``: the per-pixel ``StreamWhile`` loop.

    Random spans mean random ``xEnd``, so most loops run to the
    ``maxIterations`` cap (800 pixels) rather than to ``isLast``; the
    ``stalled`` variant below and the pysim tests cover the short spans.
    This is the run that found the AFix-in-a-layout signedness bug
    (``lib/fixed.py`` b47c9b2).
    """
    run_short_diff("SpanRasterizer", tmp_path)


def test_diff_span_rasterizer_stalled(tmp_path):
    """Consumer mostly stalled: ``StreamWhile`` holds state across ``!ready``."""
    run_short_diff("SpanRasterizer", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_rasterizer(tmp_path):
    """The whole ``Rasterizer(c)``: queue(2) -> SpanWalker -> queue(4) ->
    synchronous StreamFork2 -> {SpanRasterizer, prefetchSpan}.

    Covers the two ``m2sPipe`` stages, both FIFOs, and the synchronous fork,
    whose ``valid`` depends on ``ready`` (plan finding 1.1(c)); ``prefetchSpan``
    and ``o`` are consumed at independent random rates, so the fork's
    coupling is exercised.
    """
    run_short_diff("Rasterizer", tmp_path)


def test_diff_rasterizer_backpressure(tmp_path):
    """Both consumers slow, producer eager: the input FIFO fills, the span
    FIFO fills, and ``running`` is high for long stretches."""
    run_short_diff("Rasterizer", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.15)
