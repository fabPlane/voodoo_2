"""Generic cocotb stimulus driver for the Spinal-vs-Amaranth diff wrapper.

Loaded by :mod:`tools.diff.harness`, never run by hand.  It reads
``$DIFF_SPEC`` (written by the harness: the paired port list, with each port's
role and handshake group) and applies seeded constrained-random stimulus:

* Every non-handshake input gets a fresh random word each cycle.
* An input stream's ``valid`` is asserted with probability
  ``--valid-density``, and **the payload is held stable while
  ``valid && !ready``** -- the SpinalHDL Stream contract
  (``spinal/lib/Stream.scala``: "payload must not change while valid is high
  and ready is low").  Violating it would let the two implementations diverge
  legitimately and is the single easiest way to write a bogus diff failure.
* An output stream's ``ready`` is asserted with probability
  ``--ready-density``, freely (a slave may change ready at will).
* Reset matches Spinal's SYNC / active-HIGH default and Amaranth's ``rst``:
  held high for ``--reset-cycles`` clock edges with inputs at zero, then
  released.  Comparison starts the cycle after release, because before the
  first edge the Spinal registers have no initial value in Verilog while the
  Amaranth ones do.

``mismatch`` is read once per cycle; only when it is set does the driver pay
for reading the individual outputs, which is what keeps a 1e6-cycle sweep
tolerable in Python.  The first mismatch stops the run and is written to
``$DIFF_REPORT`` as JSON, with the seed, the cycle and every differing port.

Stimulus is a pure function of (seed, cycle), so the harness can replay a
failing seed with tracing on and get the identical waveform.
"""

from __future__ import annotations

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

with open(os.environ["DIFF_SPEC"]) as _fh:
    SPEC = json.load(_fh)
REPORT = os.environ.get("DIFF_REPORT")
SEEDS = [int(s) for s in os.environ.get("DIFF_SEEDS", "1").split(",") if s.strip()]
CYCLES = int(os.environ.get("DIFF_CYCLES", "20000"))
VALID_DENSITY = float(os.environ.get("DIFF_VALID_DENSITY", "0.6"))
READY_DENSITY = float(os.environ.get("DIFF_READY_DENSITY", "0.6"))
RESET_CYCLES = int(os.environ.get("DIFF_RESET_CYCLES", "8"))
VERBOSE = os.environ.get("DIFF_VERBOSE", "0") == "1"

CLK_PERIOD_NS = 10


class Plan:
    """Static classification of the wrapper's ports, done once."""

    def __init__(self, dut, spec):
        self.dut = dut
        outputs = spec["outputs"]
        inputs = spec["inputs"]

        out_by_stream: dict[str, dict] = {}
        for p in outputs:
            if p["stream"]:
                out_by_stream.setdefault(p["stream"], {})[p["role"]] = p

        #: streams flowing INTO the dut: valid+payload are inputs, ready an output
        self.in_streams = []
        #: streams flowing OUT of the dut: ready is an input
        self.out_ready = []
        #: inputs belonging to no handshake, randomised every cycle
        self.free = []

        grouped: dict[str, dict] = {}
        for p in inputs:
            if not p["stream"]:
                self.free.append(p)
                continue
            grouped.setdefault(p["stream"], {"valid": None, "ready": None, "payload": []})
            g = grouped[p["stream"]]
            if p["role"] == "valid":
                g["valid"] = p
            elif p["role"] == "ready":
                g["ready"] = p
            else:
                g["payload"].append(p)

        for name, g in grouped.items():
            if g["valid"] is not None:
                ready_port = out_by_stream.get(name, {}).get("ready")
                self.in_streams.append(
                    {
                        "name": name,
                        "valid": self._sig(g["valid"], "in_"),
                        "ready": self._sig(ready_port, "s_") if ready_port else None,
                        "payload": [(self._sig(p, "in_"), p["width"]) for p in g["payload"]],
                        # payload is held across a stall, so it lives here
                        "held": None,
                        "armed": False,
                    }
                )
                # A stream whose payload ports are inputs but whose valid is an
                # output cannot happen; a Flow (no ready) is handled by ready=None.
            if g["ready"] is not None:
                self.out_ready.append(self._sig(g["ready"], "in_"))
            for p in g["payload"] if g["valid"] is None else []:
                # payload inputs of a stream whose valid is NOT an input: the
                # dut drives valid, so these are just free inputs.
                self.free.append(p)

        self.free_sigs = [(self._sig(p, "in_"), p["width"]) for p in self.free]
        self.out_sigs = [(p["spinal"], getattr(dut, "s_" + p["spinal"]),
                          getattr(dut, "a_" + p["spinal"])) for p in outputs]
        self.all_input_sigs = (
            [s for st in self.in_streams for s, _ in st["payload"]]
            + [st["valid"] for st in self.in_streams]
            + self.out_ready
            + [s for s, _ in self.free_sigs]
        )

    def _sig(self, port, prefix):
        return getattr(self.dut, prefix + port["spinal"])


def _rand_words(rng, widths):
    return [rng.getrandbits(w) if w > 0 else 0 for w in widths]


async def _reset(dut, plan):
    for sig in plan.all_input_sigs:
        sig.value = 0
    # The wrapper always has clk/rst (it carries a registered sticky flag), even
    # when the DUT pair is purely combinational and has no reset of its own.
    dut.rst.value = 1
    for _ in range(RESET_CYCLES):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await FallingEdge(dut.clk)


def _apply(plan, rng):
    """Drive one cycle's stimulus. Assumes we are mid-cycle (falling edge)."""
    for sig, width in plan.free_sigs:
        sig.value = rng.getrandbits(width)
    for sig in plan.out_ready:
        sig.value = 1 if rng.random() < READY_DENSITY else 0
    for st in plan.in_streams:
        stalled = st["armed"] and (st["ready"] is not None and int(st["ready"].value) == 0)
        if not stalled:
            # Either the previous beat fired or we were idle: free to choose.
            st["armed"] = rng.random() < VALID_DENSITY
            if st["armed"]:
                st["held"] = _rand_words(rng, [w for _, w in st["payload"]])
        # while stalled: keep both `valid` and the payload exactly as they were
        st["valid"].value = 1 if st["armed"] else 0
        if st["armed"] and st["held"] is not None:
            for (sig, _), value in zip(st["payload"], st["held"]):
                sig.value = value


def _differences(plan):
    out = []
    for name, s, a in plan.out_sigs:
        sv, av = s.value, a.value
        if sv != av:
            out.append({"port": name, "spinal": int(sv), "amaranth": int(av)})
    return out


def _write_report(payload):
    if REPORT:
        with open(REPORT, "w") as fh:
            json.dump(payload, fh, indent=2)


@cocotb.test()
async def diff(dut):
    """Sweep every seed; stop at the first mismatch."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    plan = Plan(dut, SPEC)

    for seed in SEEDS:
        rng = random.Random(seed)
        for st in plan.in_streams:
            st["armed"], st["held"] = False, None
        await _reset(dut, plan)

        for cycle in range(CYCLES):
            _apply(plan, rng)
            await FallingEdge(dut.clk)
            if int(dut.mismatch.value) != 0:
                diffs = _differences(plan)
                _write_report({"ok": False, "seed": seed, "cycle": cycle,
                               "differences": diffs})
                lines = ", ".join(
                    f"{d['port']} (spinal={d['spinal']:#x} amaranth={d['amaranth']:#x})"
                    for d in diffs
                )
                raise AssertionError(
                    f"mismatch at seed {seed}, cycle {cycle}: {lines}"
                )
        if VERBOSE:
            dut._log.info(f"seed {seed}: {CYCLES} cycles clean")

    _write_report({"ok": True, "seeds": SEEDS, "cycles": CYCLES})
    dut._log.info(f"diff clean: {len(SEEDS)} seed(s) x {CYCLES} cycles")
