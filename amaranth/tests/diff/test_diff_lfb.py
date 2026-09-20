"""Short lockstep run for ``Lfb`` (plan task 2.3).

The full gate is ``make diff MOD=Lfb`` (>= 1e6 cycles x 3 seeds); it passed
seeds 1,2,3 x 1e6 on the Mac on 2026-09-20, plus 1e6 x 3 at
``--valid-density 0.9 --ready-density 0.12``.

What the generic driver exercises here: ``io_bus_cmd`` is an input stream
(payload held while stalled), so reads and writes arrive in random order with
random masks; the 266-bit ``io_regs`` port is re-randomised *every cycle*, so
``writeFormat``, ``rgbaLanes``, the swap/swizzle bits and
``pixelPipelineEnable`` all change under a captured command -- the decode mux
and the two output valids are measured on every combination the register
bank could ever present, and some it never would.  ``io_fbReadBus_rsp`` is
an input stream too, so the two-fetch read path completes at random times,
and ``pciFifoEmpty``/``pipelineBusy`` gate ``stateRwait`` at random.

The stalled case leans on the output-side back-pressure: ``writeOutput`` and
``pipelineOutput`` ready at 12 %, so the PIXEL1/PIXEL2 states sit for many
cycles while ``regs`` keeps changing underneath them.
"""

from conftest import run_short_diff


def test_diff_lfb(tmp_path):
    run_short_diff("Lfb", tmp_path)


def test_diff_lfb_stalled(tmp_path):
    run_short_diff("Lfb", tmp_path, cycles=10_000,
                   valid_density=0.95, ready_density=0.12)
