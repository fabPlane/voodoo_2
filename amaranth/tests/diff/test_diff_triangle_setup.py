"""Short lockstep run for the Phase 3 ``TriangleSetup`` port.

The full gate is ``make diff MOD=TriangleSetup`` (1e6 cycles x 3 seeds, plan
§4.2); this is the ``make test`` version.
"""

from conftest import run_short_diff


def test_diff_triangle_setup(tmp_path):
    """``TriangleSetup(Config.voodoo1())``.

    The widest payload pairing in the tree so far: Spinal emits 258 scalar
    leaf ports, Amaranth two packed ones (``i__payload`` 2129 bits,
    ``o__payload`` 2504), and ``tools/diff/amports.py`` slices them apart
    with no entry in the override table.
    """
    run_short_diff("TriangleSetup", tmp_path)


def test_diff_triangle_setup_backpressure(tmp_path):
    """Same, with the producer eager and the consumer stalling.

    ``i.ready`` is only high in ``Idle`` and ``o.valid`` only in ``Done``, so
    a sparse ``o.ready`` is what holds the FSM in ``Done`` and exercises the
    "``o.payload`` must not move while it waits" path, plus the
    ``Done -> Idle -> Prep`` turnaround on the very next cycle.
    """
    run_short_diff("TriangleSetup", tmp_path, cycles=20_000,
                   valid_density=0.95, ready_density=0.12)
