"""Short lockstep runs for the three Phase 3 fastfill modules.

The full gate is ``make diff MOD=Fastfill`` / ``FastfillWrite`` /
``FastfillWordWriter`` (1e6 cycles x 3 seeds, plan §4.2); these are the
``make test`` versions at 20k.

What random stimulus does here:

* ``Fastfill`` -- ``i`` is ``Stream(NoData)``, so ``io_i_valid`` at the
  valid density retriggers a walk as soon as ``running`` drops, over a
  fresh 10-bit random clip window each time (mostly large, ~half empty or
  inverted -- both the empty-rectangle beat and the ``+1`` compares at
  the top of the 10-bit range get hit).  ``o.ready`` at the ready density
  stalls the loop mid-rectangle.
* ``FastfillWrite`` -- purely combinational apart from ``ffActive`` and
  the ``captured`` register, and every input is free: ``cmdFire`` /
  ``running`` toggle at random, so the live-vs-captured mux and the
  reset-less ``captured`` are compared every cycle.
* ``FastfillWordWriter`` -- the word walk, the two ``Dither`` ROM stages,
  the ``m2sPipe``'d meta path, the nested ``StreamJoin`` + ``halfPipe``
  and the synchronous forks on both ends; ``colorWrite.ready`` /
  ``auxWrite.ready`` independently random.  The back-pressure variant
  keeps both sinks mostly stalled, which is where the halfPipe's
  ``rValid``/``rData`` enables and the fork's ``valid = i.valid && i.ready``
  coupling show.
"""

from conftest import run_short_diff


def test_diff_fastfill(tmp_path):
    run_short_diff("Fastfill", tmp_path)


def test_diff_fastfill_backpressured(tmp_path):
    """Sparse ``o.ready``: the loop must hold the pixel, not skip it."""
    run_short_diff("Fastfill", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_fastfill_write(tmp_path):
    run_short_diff("FastfillWrite", tmp_path)


def test_diff_fastfill_word_writer(tmp_path):
    run_short_diff("FastfillWordWriter", tmp_path)


def test_diff_fastfill_word_writer_backpressured(tmp_path):
    """Both write sinks mostly stalled: the halfPipe / synchronous-fork drain."""
    run_short_diff("FastfillWordWriter", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)
