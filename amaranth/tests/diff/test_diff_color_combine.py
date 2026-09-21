"""Short lockstep run for ``ColorCombine`` (plan Phase 4).

The full gate is ``make diff MOD=ColorCombine`` (>= 1e6 cycles x 3 seeds,
plus one run at ``--valid-density 0.9 --ready-density 0.12``).

This is the first ``spinal.lib.misc.pipeline`` user through the harness, so
the run doubles as the check plan §4.2 asked for on ``avoodoo/lib/pipeline``
itself: eight ``StageLink``\\ s with ``collapseBubble``, 8 ``valid``
registers reset to 0 and 150 payload registers with no reset at all.  The
harness resets before every seed, so a payload register that reset on one
side and not the other would show up on the second seed, and the stalled
variant keeps several beats resident so the ``when(up.isReady)`` register
enables are exercised against real back-pressure.

Every input leaf is re-randomised each cycle (held while ``valid && !ready``),
so all six ``MSelect`` codes *and* the two out-of-range ones that Spinal's
``switch`` folds into ``TEXTURE_RGB``, the three ``AddMode`` codes plus the
fourth, and the dead ``MSelect.AOTHER`` factor are all hit thousands of
times per seed.
"""

from conftest import run_short_diff


def test_diff_color_combine(tmp_path):
    run_short_diff("ColorCombine", tmp_path)


def test_diff_color_combine_stalled(tmp_path):
    run_short_diff("ColorCombine", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.12)
