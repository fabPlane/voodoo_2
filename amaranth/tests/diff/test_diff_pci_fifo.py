"""Short lockstep run for ``PciFifo`` (plan Phase 1.6).

The full gate is ``make diff MOD=PciFifo`` (>= 1e6 cycles x 3 seeds) plus a
backpressure run (``--valid-density 0.9 --ready-density 0.12``); both passed
on the Mac on 2026-09-20.

What the generic driver reaches, and how well.  ``cpuSide.cmd`` is an input
stream whose 12-bit address is uniformly random, so per accepted beat:

* ~3.9% hit one of the 158 decoded registers and take the FIFO path (the
  other 96% are ``fifoBypass`` passthroughs, half of them reads);
* ~1.7% are ``syncRequired`` -- with ``pipelineBusy`` random every cycle the
  halted-pop path is exercised on roughly every second such entry;
* ~0.7% are one of the 30 float aliases, so the ``Fpxx2AFix`` conversion,
  ``floatCorrected`` and the ``floatShadow`` flow all fire hundreds of times
  per 1e6 cycles, with random 32-bit data (NaN/Inf/denormal patterns
  included);
* ~0.12% land on one of the 5 command addresses, so ``commandStreamBlocked``
  holds the pop a few hundred times per 1e6 cycles;
* ``wasEnqueuedAddr`` is random too, so ``wasEnqueued`` pulses only when the
  two 12-bit values coincide -- a handful of times per 1e6 cycles.

``texWrite`` is an input stream at the same density, so texture entries
interleave with register entries in the 64-entry FIFO throughout.  The
stalled run (low ``ready`` on ``regSide.cmd`` / ``texDrain`` / ``cpuSide.rsp``)
is what fills the FIFO and the ingress stage and exercises ``cmd.ready``
backpressure; the default densities mostly keep it shallow.

Covered thinly but not zero: the sync and command-blocked drain paths (see
above).  What the random driver cannot do is present a *protocol-correct*
``regSide.rsp`` (it randomises ``rsp.valid`` regardless of any outstanding
read), but both implementations see the same stimulus, so the ``pendingRead``
forwarding path is compared under harsher conditions than the core produces.
"""

from conftest import run_short_diff


def test_diff_pci_fifo(tmp_path):
    run_short_diff("PciFifo", tmp_path)


def test_diff_pci_fifo_backpressure(tmp_path):
    """Slow consumers: the 64-entry FIFO fills, ``cmd.ready`` stalls the host."""
    run_short_diff("PciFifo", tmp_path, cycles=20_000,
                   valid_density=0.9, ready_density=0.12)
