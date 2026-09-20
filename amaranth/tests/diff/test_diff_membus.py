"""Short lockstep runs for the MemBus (Bmb) adapters (plan task 1.2 / 1.4).

The full gate is ``make diff MOD=<Name> DIFF_SEEDS=1,2,3 DIFF_CYCLES=1000000``.
Results recorded in ``docs/amaranth-rewrite-plan.md`` Phase 1.

Two of the eight rows are not here yet:

``BmbArbiter`` / ``BmbArbiterSingle``
    blocked on ``tools/diff/amports.py``: an ``In(sig).array(n)`` signature
    member loses its index in ``signature.members.flatten()``, so the Spinal
    ``io_inputs_0_cmd_valid`` has nothing to pair with even though the emitted
    Verilog port really is ``inputs__0__cmd__valid``.  Reported to the harness
    owner; the arbiter is covered meanwhile by the pysim tests in
    ``tests/test_membus.py`` (priority, fragment lock, source routing).

Back-pressure matters more here than for a combinational splice: every one of
these adapters holds state that only advances on ``fire``, and three of them
(``Aligner``, ``LengthFixer``, ``AlignedSpliter``) deliberately decouple the
input and output handshakes -- a read command can produce several output
bursts before the input beat is even accepted.  So each module gets a
balanced run plus a starved-``ready`` run.
"""

from conftest import run_short_diff


def test_diff_membus_source_remover(tmp_path):
    run_short_diff("BmbSourceRemover", tmp_path)


def test_diff_membus_source_remover_backpressured(tmp_path):
    run_short_diff("BmbSourceRemover", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_membus_context_remover(tmp_path):
    """Exercises the 64-deep context FIFO and its ``first``-gated push."""
    run_short_diff("BmbContextRemover", tmp_path)


def test_diff_membus_context_remover_backpressured(tmp_path):
    """A starved rsp ``ready`` is what fills the FIFO and stalls the fork."""
    run_short_diff("BmbContextRemover", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_membus_aligner(tmp_path):
    run_short_diff("BmbAligner", tmp_path)


def test_diff_membus_aligner_backpressured(tmp_path):
    """``prePadding``/``postPadding`` and the rsp ``drop`` counters only move
    on ``fire``, so a sparse ``ready`` is the interesting case."""
    run_short_diff("BmbAligner", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_membus_length_fixer(tmp_path):
    run_short_diff("BmbLengthFixer", tmp_path)


def test_diff_membus_length_fixer_backpressured(tmp_path):
    run_short_diff("BmbLengthFixer", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_membus_aligned_spliter(tmp_path):
    run_short_diff("BmbAlignedSpliter", tmp_path)


def test_diff_membus_aligned_spliter_backpressured(tmp_path):
    run_short_diff("BmbAlignedSpliter", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_sim_membus_memory(tmp_path):
    """``SimBmbMemory`` at 64 KiB; see the registry note for why not 4 MiB."""
    run_short_diff("SimBmbMemorySmall", tmp_path)


def test_diff_sim_membus_memory_backpressured(tmp_path):
    """A stalled ``rsp.ready`` is what holds the read state machine mid-burst."""
    run_short_diff("SimBmbMemorySmall", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)
