"""Short lockstep run for ``Fog`` (plan Phase 4).

The full gate is ``make diff MOD=Fog`` (>= 1e6 cycles x 3 seeds).

Three ``m2sPipe`` stages with ``collapsBubble``, so the stalled case is the
one that exercises the interesting states: two or three beats resident in the
pipe at once, ``io.busy`` high across all of them, and the input re-presented
while ``valid && !ready`` (the driver holds the payload, which is the Spinal
``Stream`` contract).

The 64 ``io_fogTable_*`` ports are free inputs and the driver re-randomises
every one of them every cycle, so the table lookup in ``stage1`` sees a new
entry each beat rather than a fixed ramp -- harsher than anything the
hardware does, and exactly what a mis-sliced index or a sign error in the
base+delta interpolation would fail on.
"""

from conftest import run_short_diff


def test_diff_fog(tmp_path):
    run_short_diff("Fog", tmp_path)


def test_diff_fog_stalled(tmp_path):
    run_short_diff("Fog", tmp_path, cycles=10_000,
                   valid_density=0.95, ready_density=0.15)
