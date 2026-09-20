"""Short lockstep run for ``SwapBuffer`` (plan task 2.3).

The full gate is ``make diff MOD=SwapBuffer``; it passed 3 seeds x 1e6 cycles
on the Mac on 2026-09-20.

``io_cmd`` carries ``NoData``, so the stimulus generator has nothing but the
handshake to drive on it -- which is the point: the whole state machine hangs
off ``cmd.fire``, ``vRetrace`` edges and the two register-bank inputs.  The
second case keeps ``vsyncEnable`` biased on by lowering the *output* ready
density (there is no output stream here, so ``--ready-density`` is inert) and
instead leans on a longer run.
"""

from conftest import run_short_diff


def test_diff_swap_buffer(tmp_path):
    run_short_diff("SwapBuffer", tmp_path)


def test_diff_swap_buffer_dense_commands(tmp_path):
    """``cmd.valid`` almost always high: the Idle -> Sampling path every time."""
    run_short_diff("SwapBuffer", tmp_path, cycles=10_000, valid_density=0.95)
