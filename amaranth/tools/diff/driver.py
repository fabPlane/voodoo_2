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
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from tools.diff.responders import Responder

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
    """Static classification of the wrapper's ports, done once.

    ``claimed`` is the set of Spinal input port names that something other
    than the random generator drives -- ``pinned`` constants and whatever the
    row's responder claims.  A claimed ``valid`` takes its whole input stream
    (valid + every payload port) out of the generator; a claimed payload port
    on an otherwise generator-driven stream is an error, because the hold
    rule could not then be honoured for the stream as a whole.
    """

    def __init__(self, dut, spec, claimed=frozenset()):
        self.dut = dut
        outputs = spec["outputs"]
        inputs = spec["inputs"]
        self.claimed = set(claimed)
        self.by_name = {p["spinal"]: p for p in inputs + outputs}
        unknown = self.claimed - {p["spinal"] for p in inputs}
        if unknown:
            raise ValueError(f"claimed ports are not DUT inputs: {sorted(unknown)}")

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
            if p["spinal"] in self.claimed:
                continue
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
            valid_claimed = any(
                p["stream"] == name and p["role"] == "valid" and p["spinal"] in self.claimed
                for p in inputs
            )
            if valid_claimed:
                # The responder owns this stream; its payload ports must not
                # be left to the generator, which would break the hold rule.
                self.claimed.update(p["spinal"] for p in g["payload"])
                continue
            if g["valid"] is not None and any(
                p["spinal"] in self.claimed for p in inputs
                if p["stream"] == name and p["role"] == "payload"
            ):
                raise ValueError(
                    f"stream {name!r}: payload port(s) claimed but `valid` is not; "
                    f"claim the whole stream (its valid) or pin the payload instead"
                )
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

        #: every handshake group in either direction, for the pre-edge snapshot:
        #: base -> {"valid": sig, "ready": sig | None, "payload": [(name, sig)]}
        self.streams: dict[str, dict] = {}
        for p in inputs + outputs:
            if not p["stream"]:
                continue
            g = self.streams.setdefault(p["stream"], {"valid": None, "ready": None, "payload": []})
            sig = self.signal_for(p["spinal"])
            if p["role"] == "valid":
                g["valid"] = sig
            elif p["role"] == "ready":
                g["ready"] = sig
            else:
                g["payload"].append((p["spinal"], sig))
        self.streams = {k: v for k, v in self.streams.items() if v["valid"] is not None}
        self.claimed_sigs = {
            name: getattr(dut, "in_" + name) for name in sorted(self.claimed)
        }
        self.all_input_sigs = (
            [s for st in self.in_streams for s, _ in st["payload"]]
            + [st["valid"] for st in self.in_streams]
            + self.out_ready
            + [s for s, _ in self.free_sigs]
            + list(self.claimed_sigs.values())
        )

    def _sig(self, port, prefix):
        return getattr(self.dut, prefix + port["spinal"])

    def signal_for(self, spinal_name: str):
        """The wrapper signal carrying a Spinal port, by direction.

        Inputs read back as the value the driver last set; outputs are the
        *Spinal* side (``s_``), which is the reference the responder should
        react to.  The Amaranth side is compared, not observed.
        """
        p = self.by_name[spinal_name]
        return getattr(self.dut, ("in_" if p["direction"] == "input" else "s_") + spinal_name)


def _rand_words(rng, widths):
    return [rng.getrandbits(w) if w > 0 else 0 for w in widths]


class _Observe:
    """``obs`` for :meth:`Responder.cycle`.

    ``obs[name]`` reads a Spinal port *now* (post-edge; inputs as last
    driven, outputs from the Spinal side).  ``obs.fired(base)`` and
    ``obs.beat(base)`` come from the pre-edge :class:`Snapshot`, so they
    describe the beat that actually crossed on the rising edge that just
    passed -- which is what a slave model needs, since a DUT's output payload
    may already show the *next* beat by the time the responder runs.
    """

    __slots__ = ("_plan", "snap")

    def __init__(self, plan):
        self._plan = plan
        self.snap: Snapshot | None = None

    def __getitem__(self, name: str) -> int:
        return int(self._plan.signal_for(name).value)

    def __contains__(self, name: str) -> bool:
        return name in self._plan.by_name

    def fired(self, stream: str) -> bool:
        """Did ``stream`` (Spinal base name, e.g. ``io_mem_cmd``) fire on the last edge?"""
        return bool(self.snap is not None and self.snap.fired.get(stream, False))

    def beat(self, stream: str) -> dict:
        """Payload of the beat that fired on the last edge (``{}`` if none did)."""
        return {} if self.snap is None else self.snap.beats.get(stream, {})


class _Pinned(Responder):
    """The trivial responder behind ``DiffEntry.pinned``."""

    def __init__(self, values: dict):
        self._values = dict(values)

    def claims(self):
        return set(self._values)

    def cycle(self, obs, drive):
        drive.update(self._values)


def _load_responder(spec_str: str, seed: int):
    import importlib

    mod_name, _, attr = spec_str.partition(":")
    factory = getattr(importlib.import_module(mod_name), attr)
    return factory(seed=seed, rng=random.Random(f"{seed}-responder"), spec=SPEC)


def _responders(seed: int) -> list:
    out = []
    if SPEC.get("pinned"):
        out.append(_Pinned(SPEC["pinned"]))
    if SPEC.get("responder"):
        out.append(_load_responder(SPEC["responder"], seed))
    return out


async def _reset(dut, plan, responders=()):
    for sig in plan.all_input_sigs:
        sig.value = 0
    # The wrapper always has clk/rst (it carries a registered sticky flag), even
    # when the DUT pair is purely combinational and has no reset of its own.
    dut.rst.value = 1
    for r in responders:
        r.reset()
    for _ in range(RESET_CYCLES):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await FallingEdge(dut.clk)


def _drive_responders(plan, responders, obs):
    """Let each responder observe the cycle that just completed and drive the next."""
    for r in responders:
        drive: dict = {}
        r.cycle(obs, drive)
        missing = set(r.claims()) - set(drive)
        extra = set(drive) - plan.claimed
        if missing or extra:
            raise ValueError(
                f"{type(r).__name__}: claimed but not driven {sorted(missing)}, "
                f"driven but not claimed {sorted(extra)}"
            )
        for name, value in drive.items():
            plan.claimed_sigs[name].value = int(value)


class Snapshot:
    """What the handshakes looked like just before the last rising edge.

    Read in cocotb's ``ReadOnly`` phase after the cycle's inputs were driven,
    i.e. with every combinational path settled, so ``fired[base]`` is exactly
    Spinal's ``valid && ready`` for that edge.  Reading ``ready`` *after* the
    edge instead is wrong in both directions: a beat that was accepted can
    look stalled (``StreamWhile``: accepting the trigger drops ``ready``), and
    a beat that was refused can look accepted (the DUT freed up on the edge)
    -- and the latter makes the generator swap the payload of a beat that is
    still being offered, breaking the hold rule it is supposed to enforce.

    ``beats[base]`` holds the payload of a fired beat, pre-edge; it is only
    collected when a responder is present, since a DUT output payload may
    already have moved on by the time the responder runs.
    """

    __slots__ = ("fired", "beats")

    def __init__(self, plan, *, with_beats: bool):
        self.fired = {}
        self.beats = {}
        for base, g in plan.streams.items():
            v = int(g["valid"].value)
            r = 1 if g["ready"] is None else int(g["ready"].value)
            f = bool(v and r)
            self.fired[base] = f
            if f and with_beats:
                self.beats[base] = {name: int(sig.value) for name, sig in g["payload"]}


def _apply(plan, rng, snap):
    """Drive one cycle's stimulus. Assumes we are mid-cycle (falling edge)."""
    for sig, width in plan.free_sigs:
        sig.value = rng.getrandbits(width)
    for sig in plan.out_ready:
        sig.value = 1 if rng.random() < READY_DENSITY else 0
    for st in plan.in_streams:
        stalled = st["armed"] and not (snap is not None and snap.fired[st["name"]])
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

    # The claim set is a property of the row, not the seed: build it once so
    # the Plan (and its port classification) is stable across seeds.
    probe = _responders(SEEDS[0]) if SEEDS else []
    claimed = set()
    for r in probe:
        overlap = claimed & set(r.claims())
        if overlap:
            raise ValueError(f"ports claimed twice (pinned and responder): {sorted(overlap)}")
        claimed |= set(r.claims())
    plan = Plan(dut, SPEC, claimed)
    obs = _Observe(plan)
    responder_stats: dict = {}

    for seed in SEEDS:
        rng = random.Random(seed)
        responders = _responders(seed)
        for st in plan.in_streams:
            st["armed"], st["held"] = False, None
        await _reset(dut, plan, responders)

        snap = None
        for cycle in range(CYCLES):
            _apply(plan, rng, snap)
            if responders:
                obs.snap = snap
                _drive_responders(plan, responders, obs)
            # Let the new inputs settle through the combinational paths, then
            # record valid/ready exactly as the rising edge will sample them.
            await ReadOnly()
            snap = Snapshot(plan, with_beats=bool(responders))
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
        stats = {type(r).__name__: r.stats() for r in responders if r.stats()}
        if stats:
            responder_stats[str(seed)] = stats
        if VERBOSE:
            dut._log.info(f"seed {seed}: {CYCLES} cycles clean {stats or ''}")

    _write_report({"ok": True, "seeds": SEEDS, "cycles": CYCLES,
                   "responder_stats": responder_stats})
    dut._log.info(f"diff clean: {len(SEEDS)} seed(s) x {CYCLES} cycles")
