"""Short lockstep run for ``Write`` (plan Phase 3).

The full gate is ``make diff MOD=Write`` (>= 1e6 cycles x 3 seeds).

``Write`` is purely combinational -- neither ``Write_spinal`` nor ``Write_am``
has a ``clk``/``reset`` port -- so the only interesting dimension is the
stream handshake, which the second case pushes into a near-permanent stall.
"""

from conftest import run_short_diff


def test_diff_write(tmp_path):
    run_short_diff("Write", tmp_path)


def test_diff_write_stalled(tmp_path):
    run_short_diff("Write", tmp_path, cycles=10_000,
                   valid_density=0.95, ready_density=0.1)
