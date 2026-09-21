"""Short lockstep runs for ``FramebufferPlaneBuffer`` (plan Phase 6).

Five registry rows, one module -- ``Config.voodoo1()`` has
``useFbWriteBuffer = false``, so the plain row is the direct 8-deep queue
that CoreSim and the Console build, and the two ``Wb`` rows are the write
combiner (Dock/Artix parameters, and the throughput test's 2-word line).
The two ``WbLocal`` rows are the same Verilog with
``tests/diff/plane_buffer_stimulus.py`` driving ``io_writeReq`` in
sequential runs, which is what fills lines.

The full gate is ``make diff MOD=<row> DIFF_SEEDS=1,2,3 DIFF_CYCLES=1000000``;
results are recorded in ``docs/amaranth-rewrite-plan.md`` Phase 6.

What the generic driver reaches on its own: ``writeReq`` addresses are
re-randomised per accepted write, so the combiner sees rotates on nearly
every write (single-word drains, both ``startAt*`` counters, the three
rotate classifications), ``io.flush`` is a free input at 50 % so flush
drains and the full+flush coincidence happen, and ``mem.cmd.ready`` at
``--ready-density`` stalls drains mid-burst.  Sequential appends need
``address == pending + 4`` and are rare (1 in 2**addressWidth per write):
tens of them per 1e6 cycles on the 12-bit ``WbSmall`` row, effectively none
on the 26-bit ``Wb`` row -- hence the ``*Local`` rows, whose responder emits
runs of 1 .. 2*lineWords+3 words so appends, merges, full-line drains and
256-beat bursts happen in lockstep.  ``tests/test_plane_buffer.py`` covers
the same paths deterministically against the scoreboard.
"""

from conftest import run_short_diff


def test_diff_plane_buffer_direct(tmp_path):
    """``useFbWriteBuffer = false``: m2sPipe + 8-deep queue, single-beat writes."""
    run_short_diff("FramebufferPlaneBuffer", tmp_path)


def test_diff_plane_buffer_direct_backpressured(tmp_path):
    """A starved ``cmd.ready`` fills the queue and stalls the pipe."""
    run_short_diff("FramebufferPlaneBuffer", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_plane_buffer_wb_small(tmp_path):
    """2-word lines: the only row where random addresses fill a line."""
    run_short_diff("FramebufferPlaneBufferWbSmall", tmp_path)


def test_diff_plane_buffer_wb_small_backpressured(tmp_path):
    """Drains stall between beats; rotates block while a drain is out."""
    run_short_diff("FramebufferPlaneBufferWbSmall", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_plane_buffer_wb(tmp_path):
    """The Dock/Artix combiner: 255-deep slot FIFOs, 11-bit burst lengths."""
    run_short_diff("FramebufferPlaneBufferWb", tmp_path)


def test_diff_plane_buffer_wb_backpressured(tmp_path):
    run_short_diff("FramebufferPlaneBufferWb", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_plane_buffer_wb_local(tmp_path):
    """Sequential runs into 256-word lines: full-line drains, 256-beat bursts."""
    run_short_diff("FramebufferPlaneBufferWbLocal", tmp_path)


def test_diff_plane_buffer_wb_small_local(tmp_path):
    run_short_diff("FramebufferPlaneBufferWbSmallLocal", tmp_path)


def test_diff_plane_buffer_wb_local_backpressured(tmp_path):
    """A long burst stalled beat by beat while the other slot fills and blocks."""
    run_short_diff("FramebufferPlaneBufferWbLocal", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)
