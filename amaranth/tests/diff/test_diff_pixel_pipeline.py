"""Short lockstep runs for the Phase 4 assembly (``core/pixel_pipeline.py``).

The full gate is ``make diff MOD=PixelPipeline`` (1e6 cycles x 3 seeds, plan
§4.2) plus a back-pressure run; these are the ``make test`` versions.

The row needs ``tests/diff/pixel_pipeline_stimulus.py``: real triangles on
``io_triangleCmd`` / ``io_ftriangleCmd`` (a random ``texTables`` word parks
the TMU cache, see ``tmu_stimulus.py``), an in-order texture-memory slave on
``io_texRead_rsp`` and a plane memory on each of ``io_colorReadRsp`` /
``io_auxReadRsp``.  Every stage inside is individually diff-clean, so a
mismatch here is a composition error: a wrong fork/join/queue order, a
merge whose priority is reversed, a ``>/->`` written as ``>->``, a counter
enable off by a cycle.
"""

import json

from conftest import run_short_diff


def _stats(tmp_path, seed: int) -> dict:
    report = json.loads((tmp_path / "report.json").read_text())
    return report["responder_stats"][str(seed)]["Composite"]


def test_diff_pixel_pipeline(tmp_path):
    """``PixelPipeline(Config.voodoo1())`` under triangle traffic, random
    controls / LFB / fastfill / palette stimulus, in-order memory slaves.
    The counters prove pixels really went through: triangles accepted,
    texture bursts fetched, plane lanes read, colour and aux words written."""
    run_short_diff("PixelPipeline", tmp_path)
    for seed in (1, 2, 3):
        s = _stats(tmp_path, seed)
        tris = s["TriangleTraffic.triangles"] + s["TriangleTraffic.ftriangles"]
        assert tris >= 5, f"seed {seed}: {s}"
        assert s["TexMemSlave.cmds"] > 50, f"seed {seed}: {s}"
        assert s["PipelineCounters.color_writes"] > 20, f"seed {seed}: {s}"
        assert s["PipelineCounters.aux_writes"] > 20, f"seed {seed}: {s}"
        assert s["PipelineCounters.color_reads"] > 20, f"seed {seed}: {s}"
        assert s["PipelineCounters.color_reads"] == s["Color.reqs"]
        assert s["PipelineCounters.aux_reads"] == s["Aux.reqs"]


def test_diff_pixel_pipeline_backpressure(tmp_path):
    """Every output stream mostly stalled: the 64-deep post-TMU queue, the
    TMU request FIFO, the fork/join pairs and every ``s2mPipe`` fill up and
    the ``pipelineBusy`` sources stay high for long stretches."""
    run_short_diff("PixelPipeline", tmp_path, cycles=10_000,
                   valid_density=0.9, ready_density=0.15)
