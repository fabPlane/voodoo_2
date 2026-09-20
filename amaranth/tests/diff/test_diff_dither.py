"""Short lockstep runs for the Phase 4 ``Dither`` module.

The full gate is ``make diff MOD=Dither`` (1e6 cycles x 3 seeds, plan §4.2);
these are the `make test` versions.
"""

from conftest import run_short_diff


def test_diff_dither(tmp_path):
    """``Dither()`` -- three 5120-entry ROMs, one stream in, one out.

    Default densities keep ``output.ready`` high most of the time, so the
    design runs at full rate and the ROM read-data registers are reloaded
    every cycle.
    """
    run_short_diff("Dither", tmp_path)


def test_diff_dither_backpressure(tmp_path):
    """Same, with the consumer stalling most of the time.

    This is the case that separates the two clock enables inside the expanded
    ``Mem.streamReadSync``: ``addrStream.fire`` for the three ROM read-data
    registers, ``addrStream.ready`` for ``retValid`` and the four ``linked``
    bypass registers.  Getting either wrong leaves the outputs correct at
    full rate and wrong here.
    """
    run_short_diff("Dither", tmp_path, cycles=20_000,
                   valid_density=0.9, ready_density=0.12)
