"""Short lockstep run for ``TextureMemSubsystem`` (plan Phase 5).

The full gate is ``make diff MOD=TextureMemSubsystem`` (1e6 cycles x seeds
1,2,3); the result is recorded in ``docs/amaranth-rewrite-plan.md`` Phase 5.

What the generic driver covers here, and one thing it does not:

* ``cpu_tex_drain`` is an input stream, so ``downloadConfig`` (a free input,
  re-randomised every cycle) and the 23-bit ``pciAddr`` sweep the whole
  packed-address decode -- every LOD including the thrown-away 9..15, both
  texel widths, ``seq8``, multibase -- through the two ``m2sPipe`` stages;
* ``cpu_tex_read.cmd`` and ``tmu_tex_read.cmd`` are input streams, so the
  three-way priority is exercised under every combination of pending
  masters, with ``tex_mem.cmd.ready`` random;
* ``tex_mem.rsp`` is an input stream whose ``source`` is random, so the
  response demux sees every route tag (including the unmapped ``3``) against
  random slave ``ready``\\ s.

What it does not model is the *system* contract that a response's ``source``
echoes the command's -- the driver's memory-side responses are unrelated to
the commands it accepted.  That does not weaken the diff: both
implementations route purely on ``rsp.source[1:0]``, so the comparison is
exact either way; it only means an end-to-end "TMU read comes back to the
TMU" check belongs to the pysim tests (``tests/test_texture_mem_subsystem.py``)
and, later, to the ``CoreSim`` Glide runs.

The back-pressured run matters for the ``S2MPipe`` on the TMU command (its
``rValidN`` only moves on ``ready``), the 2-deep CPU read queue (full/empty
under a starved output) and the two ``M2SPipe`` payload registers, which
load on ``ready`` rather than on ``fire``.
"""

from conftest import run_short_diff


def test_diff_texture_mem_subsystem(tmp_path):
    run_short_diff("TextureMemSubsystem", tmp_path)


def test_diff_texture_mem_subsystem_backpressured(tmp_path):
    run_short_diff("TextureMemSubsystem", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)
