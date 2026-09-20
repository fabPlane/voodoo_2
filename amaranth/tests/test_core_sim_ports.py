"""The CoreSim port contract, checked against both sides that depend on it.

Two independent consumers must agree with :mod:`avoodoo.core_sim_ports`:

* the **Spinal** ``CoreSim``, whose port list is committed as
  ``fpga/src/voodoo/diff/portmaps/CoreSim.json`` — if that drifts, the Glide
  harness stops being able to drive both cores with the same C++;
* the **C harness copy** in ``amaranth/emu/sim/harness/sim_harness.cpp``,
  which spells the memory-array paths and the ``dbg_*`` ports by hand.

Neither check needs Verilator or a build, so both run in plain ``make test``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from amaranth.lib import wiring

from avoodoo import core_sim_ports as P

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTMAP = REPO_ROOT / "fpga" / "src" / "voodoo" / "diff" / "portmaps" / "CoreSim.json"
HARNESS = REPO_ROOT / "amaranth" / "emu" / "sim" / "harness" / "sim_harness.cpp"
HARNESS_H = REPO_ROOT / "amaranth" / "emu" / "sim" / "harness" / "sim_harness.h"
UPSTREAM_H = (
    REPO_ROOT / "ext" / "SpinalVoodoo" / "emu" / "sim" / "harness" / "sim_harness.h"
)


def test_signature_has_every_declared_port():
    sig = P.core_sim_signature()
    expected = {
        name for name, _, _ in (*P.SPINAL_IO_PORTS, *P.CLOCK_PORTS, *P.DBG_PORTS)
    }
    assert set(sig.members) == expected
    # Flat members only: a nested signature would emit `a__b`, not `a_b`.
    for name, member in sig.members.items():
        assert not isinstance(member.shape, wiring.Signature), name


@pytest.mark.skipif(not PORTMAP.exists(), reason="Spinal port map not generated")
def test_matches_spinal_portmap():
    """Name, width and direction of all 19 Spinal ports, exactly."""
    data = json.loads(PORTMAP.read_text())
    spinal = {
        p["name"]: (p["width"], p["direction"])
        for p in data["ports"]
    }
    ours = {
        name: (width, "input" if flow is wiring.In else "output")
        for name, width, flow in (*P.SPINAL_IO_PORTS, *P.CLOCK_PORTS)
    }
    assert ours == spinal

    # The dbg bundle is ours alone; it must not shadow a Spinal port.
    assert not {n for n, _, _ in P.DBG_PORTS} & set(spinal)


def test_harness_header_is_byte_identical_to_upstream():
    """The Glide tests link against this copy unchanged (plan §4.3)."""
    assert HARNESS_H.read_bytes() == UPSTREAM_H.read_bytes()


def test_harness_uses_the_declared_memory_paths():
    text = HARNESS.read_text()
    assert f"top->rootp->{P.FB_MEM_PATH}" in text
    assert f"top->rootp->{P.TEX_MEM_PATH}" in text
    # Byte-lane spellings and hierarchical taps into the core proper are
    # Spinal-only; neither may survive as *code* (prose about them is fine,
    # so strip comment lines before looking).
    code = "\n".join(
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(("*", "/*", "//"))
    )
    assert "ram_symbol" not in code
    assert "CoreSim__DOT__core_1" not in code


def test_harness_word_counts_match_the_rtl():
    text = HARNESS.read_text()

    def define(name: str) -> int:
        m = re.search(rf"#define\s+{name}\s+\(([^)]*)\)", text)
        assert m, name
        return eval(m.group(1), {"__builtins__": {}})  # noqa: S307 - literal arithmetic

    assert define("FB_WORD_COUNT") == P.FB_WORDS
    assert define("TEX_WORD_COUNT") == P.TEX_WORDS


def test_harness_uses_every_dbg_port():
    text = HARNESS.read_text()
    for name, _, _ in P.DBG_PORTS:
        assert f"top->{name}" in text, name


def test_fifty_six_stubbed_counters_are_preserved():
    """Plan 0.5: keep the zero-returning sim_get_* exactly as upstream."""
    text = HARNESS.read_text()
    upstream = (
        REPO_ROOT / "ext" / "SpinalVoodoo" / "emu" / "sim" / "harness" / "sim_harness.cpp"
    ).read_text()
    pattern = re.compile(r"^\w[\w ]*\s(\w+)\(void\) \{ return 0; \}$", re.M)
    assert set(pattern.findall(text)) == set(pattern.findall(upstream))
