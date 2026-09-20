"""Short lockstep run for ``AddressRemapper`` (plan task 2.3).

The full gate is ``make diff MOD=AddressRemapper``; it passed 3 seeds x 1e6
cycles on the Mac on 2026-09-20.

The module is purely combinational, so neither density affects what it
computes -- but ``cmd.address`` is 22 bits of uniform random, and only
0x200088..0x2000fc (30 of 4 194 304 addresses) exercises the remap table, so a
random run alone would essentially only prove the pass-through path.  The
table itself is covered exhaustively, address by address, by
``tests/test_address_remapper.py``; this is the check that the *rest* of the
Bmb splice (opcode, length, data, mask, source, last, and both handshakes)
matches the Spinal Verilog bit for bit.
"""

from conftest import run_short_diff


def test_diff_address_remapper(tmp_path):
    run_short_diff("AddressRemapper", tmp_path)


def test_diff_address_remapper_backpressured(tmp_path):
    """Sparse ``ready``: the splice must pass stalls straight through."""
    run_short_diff("AddressRemapper", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.1)
