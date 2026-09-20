"""Short lockstep runs for the Phase 1.3 float modules.

The full gate is ``make diff MOD=FpxxDiffWrap`` / ``MOD=Fpxx2AFix``
(1e6 cycles x 3 seeds, plan §4.2).  These are the `make test` versions.
"""

from conftest import run_short_diff


def test_diff_fpxx(tmp_path):
    """Every ``Fpxx`` predicate and helper, ``FpxxConfig.float32()``.

    Purely combinational, so the stream densities are irrelevant.
    """
    run_short_diff("FpxxDiffWrap", tmp_path)


def test_diff_fpxx2afix(tmp_path):
    """``Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1)``.

    Exercises the harness's packed-payload pairing as well: Spinal's three
    ``io_op_payload_{mant,exp,sign}`` ports map onto slices of the one
    ``op__payload`` port Amaranth emits for a ``data.StructLayout`` payload.
    """
    run_short_diff("Fpxx2AFix", tmp_path)


def test_diff_fpxx2afix_backpressure(tmp_path):
    """Same, with the consumer stalling most of the time.

    Sparse ``ready`` is what actually exercises ``StageLink``'s
    ``n0_ready = n1_ready | !n1_valid`` and the payload hold.
    """
    run_short_diff("Fpxx2AFix", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.15)
