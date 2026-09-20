"""Short lockstep runs for the Phase 5 texel decoder.

The full gate is ``make diff MOD=TmuTexelDecoder`` (1e6 cycles x 3 seeds, plan
§4.2); these are the `make test` versions.

The generic driver covers the module's two unusual inputs without any
directed hooks:

* ``paletteWrite`` is a ``Flow`` -- valid with no ready -- so the driver
  treats it as a stream whose back-pressure never stalls and gives it a fresh
  address/data every cycle it is asserted.  At the default 0.6 valid density
  all 256 entries of the four replicated RAMs are written within a few
  thousand cycles, interleaved with the P8/AP88 lookups that read them, which
  is the write-then-use ordering this module needs.  Before that the RAMs
  read as zero on both sides (Amaranth's mandatory ``init``, Verilator's
  zero-fill of the Spinal ``reg`` array), so the early cycles are compared
  too rather than being a don't-care.
* ``nccTables`` is 688 bits of free input, re-randomised every cycle.  That is
  strictly harder than the real core, which loads the tables from registers
  and holds them across a triangle.
"""

from conftest import run_short_diff


def test_diff_tmu_texel_decoder(tmp_path):
    """``TmuTexelDecoder(Config.voodoo1())`` -- all 13 formats plus the 3 unassigned.

    Random 4-bit ``format`` hits every branch of the decode ``switch``, both
    NCC tables, and the palette path, on all four bilinear taps.
    """
    run_short_diff("TmuTexelDecoder", tmp_path)


def test_diff_tmu_texel_decoder_backpressure(tmp_path):
    """Same, with the consumer stalling most of the time.

    Three chained ``m2sPipe``s only show their collapsing-bubble behaviour
    when ``output.ready`` is sparse; it also decorrelates the palette RAM's
    read enable (``sampleFetch.fire``) from its write enable.
    """
    run_short_diff("TmuTexelDecoder", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.12)
