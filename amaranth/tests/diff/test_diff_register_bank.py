"""Short lockstep run for ``RegisterBank`` (plan Phase 2.2, with the
``BmbBusInterface`` port of 1.6 inside it).

The full gate is ``make diff MOD=RegisterBank`` (>= 1e6 cycles x 3 seeds)
plus a backpressure run (``--valid-density 0.9 --ready-density 0.12``).

What the generic driver reaches.  ``io_bus_cmd`` is an input stream whose
12-bit address is uniformly random and *not* word aligned, so per accepted
beat about 3.3% (136/4096) hit a declared register -- half of them writes
with a random byte mask, half reads that go through the registered read mux
(``reg_rdata``) and come back on ``io_bus_rsp`` a cycle later; every other
beat is a miss, which still produces a response (``rsp.valid :=
RegNext(cmd.fire)``) with ``rsp.data = 0``.  So over 1e6 cycles each of the
136 registers is written and read back a few dozen times, with byte-lane
masks that exercise the regif ``wmask``/``wmaskn`` merge on every multi-field
word.

* ~0.12% of beats are one of the 5 command addresses: a few hundred command
  pulses per 1e6 cycles, and the five ``s2mPipe().m2sPipe()`` buffers are
  drained at ``--ready-density``, so the two-deep back-pressure and the
  ``commandReady`` export are exercised; the ``triangleCMD``/``ftriangleCMD``
  payload (116 leaves each: vertices, eight gradients, the hi tex-coord and
  alpha shadows, ``triangleConfigReg``) is compared on every pulse.
* ``io_floatShadow`` is an input Flow with a random address and 50-bit raw,
  so the twelve float shadow registers take the PciFifo-side write path
  (12/4096 of valid beats: ~1800 per 1e6 cycles) and the fixed-write path (a
  bus write to 0x030-0x07C, shifted left) about half as often;
  ``ftriangleCMD`` then reads them through its ``Mux(useFloatShadow, ...)``.
* ``clutData`` (0x228) and the eight NCC I/Q palette registers are ordinary
  register addresses, so ``gammaLut`` and ``io_paletteWrite`` fire at the
  same ~0.024% rate each.
* Every other input -- ``statusInputs``, ``statistics``, ``debug``,
  ``pciFifoFree``, ``swapDisplayedBuffer``, ``swapsPending``,
  ``syncDrained`` -- is re-randomised every cycle, so the RO read-back words
  (status, the five counters) are compared under continuously changing
  inputs and ``syncPulse`` is checked as a plain ``RegNext``.

The stalled run keeps the five command streams' ``ready`` low most of the
time so the pulse buffers sit full and ``commandReady`` stays low across
further writes to the same command register (those writes are then dropped
by both sides, which the diff confirms).
"""

from conftest import run_short_diff


def test_diff_register_bank(tmp_path):
    run_short_diff("RegisterBank", tmp_path)


def test_diff_register_bank_backpressure(tmp_path):
    run_short_diff("RegisterBank", tmp_path, cycles=20_000,
                   valid_density=0.9, ready_density=0.12)
