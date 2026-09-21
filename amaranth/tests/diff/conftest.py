"""Shared plumbing for the short differential runs that `make test` does.

`make diff MOD=X` is the real gate (>= 1e6 cycles x 3 seeds, plan §4.2); these
tests run the same harness at a few tens of thousands of cycles so a broken
port fails in the normal test loop instead of being noticed a phase later.

They are skipped, not failed, when the machine cannot run them:

* no Verilator on ``PATH``;
* the Spinal Verilog is missing *and* there is no ``scala-cli`` to make it
  (``fpga/rtl/`` is gitignored, so a fresh checkout has neither);
* the registry row is still ``pending``.

That keeps `make test` green on a box with only the Python toolchain while
still being a real check on the two build machines, which have both.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

AM_ROOT = Path(__file__).resolve().parents[2]
if str(AM_ROOT) not in sys.path:
    sys.path.insert(0, str(AM_ROOT))

from tools.diff import harness  # noqa: E402
from tools.diff.registry import resolve  # noqa: E402

#: Short by design; `make diff` is where the plan's 1e6 lives.
SHORT_SEEDS = [1, 2, 3]
SHORT_CYCLES = 20_000


def run_short_diff(module: str, tmp_path: Path, *, cycles: int = SHORT_CYCLES,
                   seeds: list[int] | None = None,
                   valid_density: float = 0.6,
                   ready_density: float = 0.6) -> None:
    """Build and run the lockstep diff for ``module``; assert it is clean."""
    entry = resolve(module)
    if entry.pending:
        pytest.skip(f"{module}: {entry.pending.splitlines()[0]}")
    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")
    spinal_v = harness.SPINAL_RTL / f"{entry.spinal}_spinal.v"
    if not spinal_v.is_file() and shutil.which("scala-cli") is None:
        pytest.skip(f"{spinal_v} absent and scala-cli not on PATH "
                    f"(run `make diff MOD={module}` on a machine that has one)")

    seeds = seeds if seeds is not None else SHORT_SEEDS
    spinal_v = harness.ensure_spinal(entry.spinal, quiet=True, copy_to=tmp_path)
    am_v, dut = harness.emit_amaranth(entry, module, tmp_path)
    pm = harness.load_portmap(entry, spinal_v, tmp_path)
    pairs, am_widths = harness.pair_ports(pm, entry, spinal_v, am_v, dut)
    spinal_ports = harness.vlog.parse_ports(spinal_v)
    wrapper_v, _spec = harness.emit_wrapper(
        module, pairs, am_widths, tmp_path,
        has_clk="clk" in spinal_ports, has_rst="reset" in spinal_ports,
        pinned=entry.pinned, responder=entry.responder,
    )
    result = harness.run_sim(
        module, tmp_path, [spinal_v, am_v, wrapper_v],
        seeds=seeds, cycles=cycles,
        valid_density=valid_density, ready_density=ready_density,
        reset_cycles=8, waves=False, verbose=False,
    )
    if not result.get("ok"):
        diffs = ", ".join(
            f"{d['port']} (spinal={d['spinal']:#x} amaranth={d['amaranth']:#x})"
            for d in result.get("differences", [])
        )
        pytest.fail(
            f"{module}: mismatch at seed {result.get('seed')}, "
            f"cycle {result.get('cycle')}: {diffs or result.get('reason')}"
        )
