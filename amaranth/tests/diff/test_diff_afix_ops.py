"""Short lockstep run for the Phase 0.2 fixed-point library.

The full gate is ``make diff MOD=AfixOps`` (1e6 cycles x 3 seeds, plan §4.2);
this is the `make test` version.

``AfixOps`` is 282 combinational ports covering, on the real
``Config.voodoo1()`` formats: ``+ - * unary-``, ``fixTo`` in every rounding
mode, standalone ``floor/ceil/roundHalfDown/roundHalfUp/round/floorToZero/
ceilToInf`` to exponent 0, ``.asSInt``, all three ``sat`` call shapes, the
``.truncated`` wrap, ColorCombine's multiply-add-saturate chain, and
RegisterBank's ``UInt.sat``.  The Spinal side is
``fpga/src/voodoo/diff/AfixOps.scala``; the Amaranth side is
``avoodoo/lib/afix_ops.py`` on top of ``avoodoo/lib/fixed.py``.
"""

from conftest import run_short_diff


def test_diff_afix_ops(tmp_path):
    """Every AFix operation the core uses, against SpinalHDL's own elaboration.

    Purely combinational, so the stream densities are irrelevant: every input
    is randomised on every cycle.  This is the check that the bound-derived
    widths, the rounding bound propagation and the saturation rescale of
    ``avoodoo.lib.fixed`` really match SpinalHDL 1.15.0 rather than merely
    matching our own reading of it.
    """
    run_short_diff("AfixOps", tmp_path)
