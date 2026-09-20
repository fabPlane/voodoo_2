"""Short lockstep run for ``CoreCpuFrontdoor`` (plan task 2.3).

The full gate is ``make diff MOD=CoreCpuFrontdoor`` (>= 1e6 cycles x 3
seeds); it passed seeds 1,2,3 x 1e6 on the Mac on 2026-09-20, plus 1e6 x 3
at ``--valid-density 0.9 --ready-density 0.12``.

Almost everything here is combinational, so the per-cycle stimulus covers
the whole decode table on its own: a random 24-bit address lands in each of
the three windows a quarter/quarter/half of the time, and ``opcode`` splits
the texture window into the ``texWrite`` and relocated ``texReadBus`` paths.
The three downstream ``rsp`` channels are input streams with random
``valid``, which is what drives the fixed-priority response chain, and the
dense-``valid`` / sparse-``ready`` case is the one that produces back-to-back
texture writes -- the case where the one-cycle ``texWriteRspPending``
acknowledgement gets clobbered by the next accept.
"""

from conftest import run_short_diff


def test_diff_cpu_frontdoor(tmp_path):
    run_short_diff("CoreCpuFrontdoor", tmp_path)


def test_diff_cpu_frontdoor_dense(tmp_path):
    run_short_diff("CoreCpuFrontdoor", tmp_path, cycles=10_000,
                   valid_density=0.95, ready_density=0.12)
