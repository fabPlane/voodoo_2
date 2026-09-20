"""``avoodoo.texture.tmu_tables`` against the SpinalVoodoo definitions.

Ground truth for the log table is ``tests/data/tmu_tables_spinal.json``, the
``logTableValues`` literal of
``ext/SpinalVoodoo/src/voodoo/texture/Tmu.scala``, extracted by::

    cd amaranth && python -m tools.extract_spinal_tables

The reciprocal table is a formula on both sides, so it is checked against a
transliteration of the Scala expression plus the properties the ``Tmu``
pipeline relies on (17-bit fit, the 256..511 plateau that makes the
``index + 1`` interpolation read safe, monotonicity).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from avoodoo.texture import tmu_tables as tt

GROUND_TRUTH = Path(__file__).parent / "data" / "tmu_tables_spinal.json"
REPO = Path(__file__).resolve().parents[2]
RENDER_C = REPO / "ext/SpinalVoodoo/emu/86Box/src/video/vid_voodoo_render.c"


@pytest.fixture(scope="module")
def spinal():
    return json.loads(GROUND_TRUTH.read_text())["tables"]


def test_log_table_matches_spinal_literal(spinal):
    expected = spinal["logTableValues"]
    assert len(expected) == tt.LOG_TABLE_SIZE
    assert list(tt.log_table) == expected


def test_log_table_is_floor_log2(spinal):
    """The literal really is ``floor(log2(1 + i/256) * 256)``, entry by entry."""
    for i, value in enumerate(spinal["logTableValues"]):
        assert value == math.floor(math.log2(1.0 + i / 256.0) * 256.0)


def test_log_table_shape():
    assert tt.log_table[0] == 0
    assert tt.log_table[255] == 0xFF
    assert max(tt.log_table) < (1 << tt.LOG_TABLE_BITS)
    assert all(b >= a for a, b in zip(tt.log_table, tt.log_table[1:]))


@pytest.mark.skipif(not RENDER_C.exists(), reason="SpinalVoodoo submodule not checked out")
def test_log_table_matches_86box():
    from tools.extract_spinal_tables import c_arrays

    assert list(tt.log_table) == c_arrays(RENDER_C)["logtable"]


def test_recip_table_matches_scala_formula():
    """``round((1 << 24) / (256 + min(i, 256)))``, the Scala's own expression."""
    assert len(tt.recip_table) == tt.RECIP_TABLE_SIZE
    for i, value in enumerate(tt.recip_table):
        clamped = min(i, 256)
        assert value == round((1 << 24) / (256.0 + clamped))


def test_recip_table_endpoints_and_plateau():
    assert tt.recip_table[0] == 1 << 16  # 2**24 / 256
    assert tt.recip_table[256] == 1 << 15  # 2**24 / 512
    # entries past 256 repeat 256 so the `index + 1` read in Tmu.scala:102 is safe
    assert set(tt.recip_table[256:]) == {tt.recip_table[256]}


def test_recip_table_fits_17_bits_and_decreases():
    assert max(tt.recip_table) == (1 << 16)
    assert max(tt.recip_table) < (1 << tt.RECIP_TABLE_BITS)
    assert min(tt.recip_table) > 0
    assert all(b <= a for a, b in zip(tt.recip_table, tt.recip_table[1:]))


def test_recip_table_error_is_below_half_an_lsb():
    for i in range(257):
        exact = (1 << 24) / (256.0 + i)
        assert abs(tt.recip_table[i] - exact) <= 0.5
