"""Phase 7.3 -- the system parity gate (plan §4.1 L2/L3, §4.3).

``make sim-trace-check TEST=<trace>`` replays a captured Glide/game trace through
``ext/SpinalVoodoo/emu/test/trace_test`` built against the **Amaranth** ``CoreSim``
and writes ``amaranth/test-output/<trace>/`` -- ``<trace>_{ref,sim,diff}.png``,
``<trace>_profile.json`` and ``run.log``.  This test compares that output with
what the **Spinal** ``CoreSim`` produces on the same trace, recorded once in
:data:`EXPECTED` below:

* ``sim_md5`` -- md5 of ``<trace>_sim.png``.  Byte-identical is the bar.
* ``cycles`` -- the ``[sim_harness] Shutdown after N cycles`` line of the run
  log (``sim_get_cycle()``; ``trace_test`` only puts cycles into the profile
  JSON for the DE10 interface).  Plan tolerance 0.5 %; the port so far is
  cycle-*exact*, so a drift is reported precisely.
* ``ref_md5`` / ``mismatches`` -- md5 of ``<trace>_ref.png`` (the 86Box
  reference model, which has nothing to do with either RTL) and the
  ``N/307200 pixels mismatched`` count of RTL vs 86Box.  The Spinal core does
  *not* match 86Box exactly (test04: 143 px on rows 0-3; test16: PAL8 palette
  missing from the trace state), so ``trace_test``'s own exit status is not
  the gate; these two only prove the reference side and the harness copy are
  unchanged.

How the Spinal numbers were produced (2026-09-21, Mac, Verilator 5.052,
clang 17): ``fpga/`` ``GenOne CoreSim --async-reset`` of the pinned submodule
commit ``2749df5`` (same config as ``CoreSimGen`` with no flags; ROMs inlined),
top renamed back to ``CoreSim``, verilated with upstream's unmodified
``emu/sim/harness/sim_harness.cpp`` and ``emu/test/Makefile`` flags, run as
``trace_test traces/<trace> --output-dir ...``.  The Amaranth side is the same
binary recipe with ``SIM_DIR=amaranth/emu/sim`` and ``VERILOG=rtl/CoreSim.v``.

Skips, never fails, when the trace is not present (the traces are Git LFS
objects in the submodule) or when ``make sim-trace-check`` has not been run
for it.  Set ``AVOODOO_PARITY=run`` to have the test invoke
``make sim-trace-check`` itself (Verilator build + replay: ~12 min for
test04 on the Mac, ~20 min for test16).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

AMARANTH = Path(__file__).resolve().parents[1]
REPO_ROOT = AMARANTH.parent
TRACES = REPO_ROOT / "ext" / "SpinalVoodoo" / "traces"
OUTPUT = AMARANTH / "test-output"

CYCLE_TOLERANCE = 0.005  # plan §4.1: "cycle counts within 0.5 %"

#: Spinal ``CoreSim`` results per trace.  ``mismatches`` is RTL vs 86Box.
EXPECTED: dict[str, dict[str, object]] = {
    "test04": {
        "ref_md5": "c49bd424ba57cf4038115f8fd33aff2d",
        "sim_md5": "d467a4c24242f92d6f690bc6f6888f15",
        "cycles": 1_375_051,
        "mismatches": 143,
    },
    "test_alphabet": {
        "ref_md5": "af29c0ccd71da928adf99e39d3dbd56e",
        "sim_md5": "e413f7c4604783d2b810fcd58c811ecb",
        "cycles": 1_290_051,
        "mismatches": 400,
    },
    "test16": {
        "ref_md5": "8bddaad4eaed0d80d101ecec93995be9",
        "sim_md5": "b712ccf9041d93ae7fba7a0a820c1195",
        "cycles": 2_245_052,
        "mismatches": 103_033,
    },
    # L3: a real game frame (Tomb Raider, traces/tomb/{trace,state}.bin, 52328
    # entries, 1967 triangles).  The other four game traces deadlock the Spinal
    # CoreSim itself (see the plan's Phase 7.3 entry), so they have no reference.
    # The Amaranth run comes out at 3_170_024 cycles: the `dbg_swap_count` load
    # port costs the state-snapshot restore one clock, which shifts the final
    # status-poll phase by one and saves one 2-cycle poll (-1 net).
    "tomb": {
        "ref_md5": "6c2f6c817f388f94a3d851257540e698",
        "sim_md5": "28b26a8885d56216678770d513fe51c3",
        "cycles": 3_170_025,
        "mismatches": 8_538,
    },
}

_CYCLES_RE = re.compile(r"\[sim_harness\] Shutdown after (\d+) cycles")
_MISMATCH_RE = re.compile(r"\[trace_test\] (\d+)/(\d+) pixels mismatched")


def _trace_present(name: str) -> bool:
    """A real LFS object, not a 130-byte pointer file."""
    for p in (TRACES / f"{name}.bin", TRACES / name / "trace.bin"):
        if p.is_file() and p.stat().st_size > 1024:
            return True
    return False


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_check(name: str) -> None:
    subprocess.run(
        ["make", "-C", str(AMARANTH), "sim-trace-check", f"TEST={name}"],
        check=True,
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_trace_replay_matches_spinal(name: str):
    if not _trace_present(name):
        pytest.skip(f"trace {name} not present under {TRACES} (git lfs)")
    out = OUTPUT / name
    log = out / "run.log"
    sim_png = out / f"{name}_sim.png"
    ref_png = out / f"{name}_ref.png"
    if os.environ.get("AVOODOO_PARITY") == "run":
        _run_check(name)
    if not (log.is_file() and sim_png.is_file() and ref_png.is_file()):
        pytest.skip(
            f"no replay output in {out}; run `make sim-trace-check TEST={name}` "
            f"(or AVOODOO_PARITY=run)"
        )
    exp = EXPECTED[name]
    text = log.read_text(errors="replace")

    # The reference side first: if 86Box itself changed, nothing below means anything.
    assert _md5(ref_png) == exp["ref_md5"], f"{name}: 86Box reference image changed"

    m = _MISMATCH_RE.search(text)
    assert m, f"{name}: no 'pixels mismatched' line in {log}"
    mismatches = int(m.group(1))

    m = _CYCLES_RE.search(text)
    assert m, f"{name}: no 'Shutdown after N cycles' line in {log}"
    cycles = int(m.group(1))

    sim_md5 = _md5(sim_png)
    problems = []
    if sim_md5 != exp["sim_md5"]:
        problems.append(
            f"{name}_sim.png md5 {sim_md5} != Spinal {exp['sim_md5']} "
            f"(RTL vs 86Box mismatches: Amaranth {mismatches}, Spinal {exp['mismatches']})"
        )
    drift = abs(cycles - exp["cycles"]) / exp["cycles"]
    if drift > CYCLE_TOLERANCE:
        problems.append(
            f"cycles {cycles} vs Spinal {exp['cycles']} ({drift * 100:.3f} % > "
            f"{CYCLE_TOLERANCE * 100} %)"
        )
    assert not problems, f"{name}: " + "; ".join(problems)
