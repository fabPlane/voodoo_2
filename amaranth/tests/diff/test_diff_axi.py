"""Short lockstep runs for the Phase 1.5 AXI boundary bridges.

The full gate is ``make diff MOD=<Name>`` (>= 1e6 cycles x 3 seeds); results
are recorded in ``docs/amaranth-rewrite-plan.md`` Phase 1.

Rows (all from ``fpga/src``, so their Spinal side is a GenOne entry rather
than an upstream module):

``Axi4ToAxiLite4``                 one-outstanding AXI4 -> AXI4-Lite FSM
``AxiLite4ToBmbBridge``            the BAR bridge, shipping ``timeoutLog2 = 20``
``AxiLite4ToBmbBridgeFastTimeout`` same with ``timeoutLog2 = 4`` so the wedged
                                   / timeout / droppedWrites paths fire under
                                   random stimulus (the starved run below)
``BmbToAxi4SharedBridge``          the SpinalHDL library bridge on the
                                   post-aligner/fixer parameter
``BmbToAxi4Mem`` / ``...Tex``      aligner + fixer + bridge + ``toAxi4()``
``AxiLite4ClockCrossing``          five ``StreamFifoCC``, **single clock**:
                                   the harness pairs one ``clk``/``reset``,
                                   so the Spinal side is elaborated with both
                                   ``ClockDomain.current``; the two-clock
                                   behaviour is pysim-only (``tests/test_axi.py``)
``Axi4AddressRelocate``            combinational, no clock on either side
"""

from conftest import run_short_diff


def test_diff_axi4_to_axilite4(tmp_path):
    run_short_diff("Axi4ToAxiLite4", tmp_path)


def test_diff_axi4_to_axilite4_backpressured(tmp_path):
    run_short_diff("Axi4ToAxiLite4", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_axilite4_to_bmb_bridge(tmp_path):
    run_short_diff("AxiLite4ToBmbBridge", tmp_path)


def test_diff_axilite4_to_bmb_bridge_fast_timeout(tmp_path):
    """``ready`` at 0.1 and ``valid`` at 0.1: a 16-cycle stall on ``cmd.ready``
    or ``rsp.valid`` happens every few hundred cycles, so every timeout and
    wedged branch is exercised."""
    run_short_diff("AxiLite4ToBmbBridgeFastTimeout", tmp_path, cycles=20_000,
                   valid_density=0.1, ready_density=0.1)


def test_diff_bmb_to_axi4_shared_bridge(tmp_path):
    run_short_diff("BmbToAxi4SharedBridge", tmp_path)


def test_diff_bmb_to_axi4_shared_bridge_backpressured(tmp_path):
    """A starved ``ready`` fills the two Info FIFOs and holds ``rspSelLock``."""
    run_short_diff("BmbToAxi4SharedBridge", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_bmb_to_axi4_mem(tmp_path):
    run_short_diff("BmbToAxi4Mem", tmp_path)


def test_diff_bmb_to_axi4_mem_backpressured(tmp_path):
    run_short_diff("BmbToAxi4Mem", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_bmb_to_axi4_mem_tex(tmp_path):
    run_short_diff("BmbToAxi4MemTex", tmp_path)


def test_diff_axilite4_clock_crossing(tmp_path):
    run_short_diff("AxiLite4ClockCrossing", tmp_path)


def test_diff_axilite4_clock_crossing_backpressured(tmp_path):
    """Full FIFOs on the push side, so ``push.ready`` (the gray ``full``
    compare) is what is being checked."""
    run_short_diff("AxiLite4ClockCrossing", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)


def test_diff_axi4_address_relocate(tmp_path):
    run_short_diff("Axi4AddressRelocate", tmp_path)
