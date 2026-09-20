"""``avoodoo.pixel.dither_tables`` against the SpinalVoodoo literals.

The module generates the eight ordered-dither tables from a formula; this
file is what makes that safe.  Ground truth is
``tests/data/dither_tables_spinal.json``, the literal arrays of
``ext/SpinalVoodoo/src/voodoo/pixel/DitherTableData.scala``, extracted by::

    cd amaranth && python -m tools.extract_spinal_tables

Every one of the 10 240 entries is compared, not a sample.  When the
submodule is checked out the same numbers are also re-parsed straight from
86Box's ``vid_voodoo_dither.h``, so a submodule bump that changed either
source would fail here rather than silently drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avoodoo.pixel import dither_tables as dt

GROUND_TRUTH = Path(__file__).parent / "data" / "dither_tables_spinal.json"
REPO = Path(__file__).resolve().parents[2]
DITHER_H = REPO / "ext/SpinalVoodoo/emu/86Box/src/include/86box/vid_voodoo_dither.h"

#: Scala name -> generated table, with its tile size and code width.
TABLES = {
    "rb4x4": (dt.rb4x4, 4, 5),
    "g4x4": (dt.g4x4, 4, 6),
    "rb2x2": (dt.rb2x2, 2, 5),
    "g2x2": (dt.g2x2, 2, 6),
    "dsubRb4x4": (dt.dsub_rb4x4, 4, 8),
    "dsubG4x4": (dt.dsub_g4x4, 4, 8),
    "dsubRb2x2": (dt.dsub_rb2x2, 2, 8),
    "dsubG2x2": (dt.dsub_g2x2, 2, 8),
}


@pytest.fixture(scope="module")
def spinal():
    payload = json.loads(GROUND_TRUTH.read_text())
    return payload["tables"]


@pytest.mark.parametrize("name", sorted(TABLES))
def test_generated_matches_spinal(spinal, name):
    """Every entry of every table equals the Scala literal."""
    expected = spinal[name]
    got, n, _bits = TABLES[name]
    assert len(got) == 256 * n * n
    assert len(got) == len(expected)
    mismatches = [i for i in range(len(got)) if got[i] != expected[i]]
    assert not mismatches, (
        f"{name}: {len(mismatches)} mismatches, first at index {mismatches[0]} "
        f"(v={mismatches[0] // (n * n)}, y={(mismatches[0] % (n * n)) // n}, "
        f"x={mismatches[0] % n}): got {got[mismatches[0]]}, "
        f"want {expected[mismatches[0]]}"
    )


@pytest.mark.parametrize("name", sorted(TABLES))
def test_value_range(name):
    """5-bit tables stay in 0..31, 6-bit in 0..63, dither-subtract in 0..255."""
    got, _n, bits = TABLES[name]
    assert min(got) >= 0
    assert max(got) == (1 << bits) - 1
    assert max(got) <= (1 << bits) - 1


@pytest.mark.skipif(not DITHER_H.exists(), reason="SpinalVoodoo submodule not checked out")
def test_generated_matches_86box_header():
    """Same check against the upstream C, parsed here rather than trusted."""
    from tools.extract_spinal_tables import DITHER_NAMES, c_arrays

    c = c_arrays(DITHER_H)
    for scala_name, c_name in DITHER_NAMES.items():
        got, _n, _bits = TABLES[scala_name]
        assert list(got) == c[c_name], f"{scala_name} != 86Box {c_name}"


def test_json_records_86box_agreement(spinal):
    """The extractor saw Scala == 86Box when it wrote the ground truth."""
    payload = json.loads(GROUND_TRUTH.read_text())
    assert payload["_matches_86box"] is True


@pytest.mark.parametrize(
    "lookup, table, n",
    [
        (dt.lookup_rb, dt.rb4x4, 4),
        (dt.lookup_g, dt.g4x4, 4),
        (dt.lookup_rb2x2, dt.rb2x2, 2),
        (dt.lookup_g2x2, dt.g2x2, 2),
        (dt.lookup_dsub_rb, dt.dsub_rb4x4, 4),
        (dt.lookup_dsub_g, dt.dsub_g4x4, 4),
        (dt.lookup_dsub_rb2x2, dt.dsub_rb2x2, 2),
        (dt.lookup_dsub_g2x2, dt.dsub_g2x2, 2),
    ],
)
def test_lookup_indexing(lookup, table, n):
    """``lookup*`` indexes ``v * n*n + y * n + x`` like the Scala accessors."""
    for v in (0, 1, 17, 128, 254, 255):
        for y in range(n):
            for x in range(n):
                assert lookup(v, y, x) == table[v * n * n + y * n + x]


@pytest.mark.parametrize(
    "packed, four, two",
    [
        (dt.rb_packed, dt.rb4x4, dt.rb2x2),
        (dt.g_packed, dt.g4x4, dt.g2x2),
        (dt.dsub_rb_packed, dt.dsub_rb4x4, dt.dsub_rb2x2),
        (dt.dsub_g_packed, dt.dsub_g4x4, dt.dsub_g2x2),
    ],
)
def test_packed_rom_image(packed, four, two):
    """``*Packed`` is ``4x4 ++ 2x2`` with the 2x2 half at 4096 (bit 12)."""
    assert len(packed) == 5120
    assert packed == four + two
    assert dt.PACKED_2X2_BASE == len(four) == 1 << 12


def test_packed_index_matches_dither_scala():
    """``packed_index`` reproduces ``Dither.packedAddr`` for every input."""
    for v in range(256):
        for y in range(4):
            for x in range(4):
                assert dt.packed_index(v, y, x, False) == v * 16 + y * 4 + x
                assert dt.rb_packed[dt.packed_index(v, y, x, False)] == dt.lookup_rb(v, y, x)
                i2 = dt.packed_index(v, y, x, True)
                assert i2 == 4096 + v * 4 + (y & 1) * 2 + (x & 1)
                assert dt.rb_packed[i2] == dt.lookup_rb2x2(v, y & 1, x & 1)


def test_order_matrices_are_permutations():
    """Both Bayer rank matrices are a full permutation of 0..n*n-1."""
    assert sorted(v for row in dt.ORDER_4X4 for v in row) == list(range(16))
    assert sorted(v for row in dt.ORDER_2X2 for v in row) == list(range(4))


@pytest.mark.parametrize("name", sorted(TABLES))
def test_monotonic_in_value(name):
    """Every cell is non-decreasing in the 8-bit input, as 86Box's are."""
    got, n, _bits = TABLES[name]
    for y in range(n):
        for x in range(n):
            col = [got[v * n * n + y * n + x] for v in range(256)]
            assert all(col[v] >= col[v - 1] for v in range(1, 256))
