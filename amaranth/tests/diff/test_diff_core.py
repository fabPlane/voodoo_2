"""Short lockstep runs for ``Core`` (plan Phase 7.2).

Two registry rows, one module, ``Config.voodoo1()`` (HDMI scanout enabled, as
``GenOne``'s ``Core`` row builds it):

``Core``
    the main row: ``tests/diff/core_stimulus.py`` attaches an in-order
    memory slave to each of the six memory buses, the pixel clock, and a CPU
    that programs a sane scene and then issues register writes, real
    triangles (fixed and float), fastfills, swaps, nops, LFB and texture
    writes and reads through ``io_cpuBus``.
``CoreRandom``
    the same slaves and clock with the generator on ``io_cpuBus`` (mostly
    texture writes and undecoded register writes), kept for
    reproducibility.

``CoreSim`` is *not* diffed: its two simulation RAMs (4 MiB + 8 MiB) put it
out of reach of both Verilog emitters (see the registry row).  Its wiring
is covered by ``tests/test_core.py`` and the Glide/trace parity gate.

The full gate is ``make diff MOD=Core`` (1e6 cycles x seeds 1,2,3) plus a
backpressure run; results are in ``docs/amaranth-rewrite-plan.md`` Phase 7.
The main row's responder ``stats()`` are checked afterwards so a clean run
is known to have pushed traffic through every path.
"""

import json

from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


def test_diff_core(tmp_path):
    run_short_diff("Core", tmp_path, cycles=30_000)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        # The CPU got its commands in and answered.
        assert s["cpu.cmd_beats"] > 500, f"seed {seed}: cpu parked ({s})"
        assert s["cpu.rsp_beats"] > 400
        assert s["cpu.kind_triangle"] + s["cpu.kind_ftriangle"] > 5
        # Pixels reached the framebuffer: colour writes through the plane buffer.
        assert s["fbColorWriteMem.writes"] > 20, f"seed {seed}: no colour writes ({s})"
        # Texture downloads reached texMem; the TMU read from it.
        assert s["texMem.writes"] > 5, f"seed {seed}: no texture writes ({s})"
        # The scanout prefetched through the colour read port (route 0).
        assert s["fbColorReadMem.route0"] >= 3, f"seed {seed}: scanout idle ({s})"
        assert s["dut.scanoutReadRspCount"] > 1000, f"seed {seed}: scanout parked ({s})"
        assert s["hdmi.hdmi_rising_edges"] > 1000


def test_diff_core_backpressured(tmp_path):
    """Starved ``rsp.ready`` / memory ``cmd.ready`` everywhere."""
    run_short_diff("Core", tmp_path, cycles=10_000, valid_density=0.9,
                   ready_density=0.1)


def test_diff_core_random(tmp_path):
    run_short_diff("CoreRandom", tmp_path, cycles=20_000)
