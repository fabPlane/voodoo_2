"""Short lockstep run for ``SpanWalker`` (plan Phase 3).

The full gate is ``make diff MOD=SpanWalker`` at 1e6 cycles x 3 seeds; this
is the same harness at 20k so a regression fails in the normal test loop.

What random stimulus is and is not good for here: the payload is 125 leaves
of uniform random, so ``insideTriangle``, the pixel-domain comparisons and
every ``fixTo`` saturation get hammered (the wide accumulators saturate on
most beats, which is exactly where an AFix width bug hides -- the very first
run caught one, in ``lib/fixed.py``, on cycle 2 of seed 1).  What it does not
produce is a *geometrically sensible* triangle, so the span-level behaviour
-- how many rows, where each starts and ends, whether the clip clamp fires --
is covered by the directed cases and the Python reference model in
``tests/test_span_walker.py`` instead.

``i`` is a stream and ``o`` is a stream, so both densities matter: a stalled
``o.ready`` parks the walker in ``EmitSpan`` and re-exercises the
``emitVisible`` gate every cycle.
"""

from conftest import run_short_diff


def test_diff_span_walker(tmp_path):
    run_short_diff("SpanWalker", tmp_path)


def test_diff_span_walker_backpressured(tmp_path):
    """Sparse ``o.ready``: the walker must hold the span and not advance the row."""
    run_short_diff("SpanWalker", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)
