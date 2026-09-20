"""Short lockstep run for ``StreamWhile`` (plan task 1.1, proof module for 0.4).

The full gate is ``make diff MOD=StreamWhileDiffWrap``.

Note the low ``ready_density`` in the second case: ``StreamWhile`` holds
``trigger.ready = !running``, so a fast consumer drains each countdown in a
handful of cycles and the interesting states (a stalled output mid-run, the
``willOverflowIfInc`` early stop) barely get visited.
"""

from conftest import run_short_diff


def test_diff_stream_while(tmp_path):
    run_short_diff("StreamWhileDiffWrap", tmp_path)


def test_diff_stream_while_stalled(tmp_path):
    run_short_diff("StreamWhileDiffWrap", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)
