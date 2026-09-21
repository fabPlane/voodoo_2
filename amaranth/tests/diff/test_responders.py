"""Pure-Python checks of the ``StreamResponder`` base (no simulator).

The hold rule is the part that matters: a presented beat must stay bit-for-bit
identical, with ``valid`` high, until the DUT's ``ready`` accepts it.  The
end-to-end responder plumbing is covered by ``test_diff_nodata.py``.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

AM_ROOT = Path(__file__).resolve().parents[2]
if str(AM_ROOT) not in sys.path:
    sys.path.insert(0, str(AM_ROOT))

from tools.diff.responders import StreamResponder  # noqa: E402

PAYLOAD = ["io_rsp_payload_data", "io_rsp_payload_source"]


class Obs(dict):
    """Stand-in for the driver's ``_Observe``: ``fired`` is the pre-edge fire."""

    def __init__(self, values, fired):
        super().__init__(values)
        self._fired = fired

    def fired(self, stream):
        return self._fired.get(stream, False)

    def beat(self, stream):
        return {}


class Scripted(StreamResponder):
    """Enqueue whatever the test hands it via ``pending``."""

    def __init__(self, **kw):
        super().__init__(stream="io_rsp", payload=PAYLOAD, **kw)
        self.pending: list[dict] = []

    def observe(self, obs):
        while self.pending:
            self.enqueue(self.pending.pop(0))


def step(r, ready):
    """One falling edge: the beat presented last time fired iff ``ready``."""
    presented = getattr(r, "_presented", None) is not None
    obs = Obs({"io_rsp_ready": ready, "io_rsp_valid": int(presented)},
              {"io_rsp": presented and bool(ready)})
    drive = {}
    r.cycle(obs, drive)
    return drive


def test_claims_valid_and_payload():
    r = Scripted(rng=random.Random(0))
    assert r.claims() == {"io_rsp_valid", *PAYLOAD}


def test_idle_when_empty():
    r = Scripted(rng=random.Random(0))
    assert step(r, ready=1) == {"io_rsp_valid": 0, PAYLOAD[0]: 0, PAYLOAD[1]: 0}


def test_beat_is_held_until_accepted():
    r = Scripted(rng=random.Random(0))
    beat = {PAYLOAD[0]: 0xABCD, PAYLOAD[1]: 1}
    r.pending.append(beat)

    presented = step(r, ready=0)          # nothing was offered yet; now offer B
    assert presented == {"io_rsp_valid": 1, **beat}
    # stalled twice: identical each time
    assert step(r, ready=0) == presented
    assert step(r, ready=0) == presented
    # B fired on the edge that just passed -> idle from here
    assert step(r, ready=1)["io_rsp_valid"] == 0
    assert r.stats()["beats_fired"] == 1


def test_latency_inserts_idle_cycles():
    r = Scripted(rng=random.Random(0), latency=2)
    r.pending += [{PAYLOAD[0]: 1, PAYLOAD[1]: 0}, {PAYLOAD[0]: 2, PAYLOAD[1]: 0}]
    assert step(r, ready=0)[PAYLOAD[0]] == 1      # offer beat 1
    assert step(r, ready=1)["io_rsp_valid"] == 0  # it fired; latency 1
    assert step(r, ready=1)["io_rsp_valid"] == 0  # latency 2
    assert step(r, ready=1)[PAYLOAD[0]] == 2      # offer beat 2


def test_reset_drops_everything():
    r = Scripted(rng=random.Random(0))
    r.pending.append({PAYLOAD[0]: 7, PAYLOAD[1]: 0})
    step(r, ready=0)
    r.reset()
    assert step(r, ready=1)["io_rsp_valid"] == 0
    assert r.stats() == {"beats_fired": 0, "queued": 0}


def test_run_sim_missing_report_is_a_failure(monkeypatch, tmp_path):
    """A driver that crashes before writing report.json must not read as PASS.

    cocotb 2.1's runner.test() does not raise on a failed test, so run_sim
    cannot rely on an exception; the missing report is the signal.
    """
    from tools.diff import harness

    class _Runner:
        def build(self, **kw):
            pass

        def test(self, **kw):
            return None  # cocotb 2.1: failed test, no exception

    import cocotb_tools.runner
    monkeypatch.setattr(cocotb_tools.runner, "get_runner", lambda name: _Runner())
    build = tmp_path / "b"
    build.mkdir()
    (build / "diff_spec.json").write_text("{}")
    result = harness.run_sim("Fake", build, [], seeds=[1], cycles=10,
                             valid_density=0.5, ready_density=0.5,
                             reset_cycles=2, waves=False, verbose=False)
    assert result["ok"] is False
    assert result["reason"] == "driver wrote no report"
