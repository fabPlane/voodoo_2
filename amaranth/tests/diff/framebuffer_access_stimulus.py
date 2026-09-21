"""Plane-memory responses for the ``FramebufferAccessMem`` diff row.

The bare generator can drive ``FramebufferAccess`` (the ``FramebufferAccess``
row): the two response streams have no ordering tag, the module accepts a
response whenever its 4-deep ``queue(4)`` has room, and an unmatched one just
waits in that queue for the next pixel that needs a read -- nothing parks,
and both sides pair the same (wrong) data with the same pixel.  What that
run cannot show is the module doing its job: a response that *is* the word
the pixel asked for, at a latency that depends on the request.

This responder claims ``io_fbReadColorRsp`` and ``io_fbReadAuxRsp`` and
answers every ``io_fbReadColorReq`` / ``io_fbReadAuxReq`` beat in order from
a lazily random 16-bit image keyed by address, after a per-beat random
latency and with ``valid`` withheld at random on top, so the response FIFOs
are seen both empty and full.  Everything else (``io_input``,
``io_output_ready``, the two ``req.ready``) stays with the generator.  A
counter part reports how many pixels went in and out, how many reads each
plane saw and how many ``zFuncFail`` pulses there were, so the diff test can
assert that the run stayed live (plan Phase 6's "parked run" lesson).

Lives under ``tests/diff`` like ``plane_reader_stimulus.py``: module-specific
stimulus, not harness machinery.
"""

from __future__ import annotations

import random

from tools.diff.responders import Responder, StreamResponder

__all__ = ["PlaneMemory", "PixelCounters", "Composite", "plane_memory"]

_COLOR_REQ = "io_fbReadColorReq"
_COLOR_RSP = "io_fbReadColorRsp"
_AUX_REQ = "io_fbReadAuxReq"
_AUX_RSP = "io_fbReadAuxRsp"


def _payload_ports(spec: dict, stream: str) -> list[str]:
    ports = [p["spinal"] for p in spec["inputs"]
             if p["stream"] == stream and p["role"] == "payload"]
    if not ports:
        raise ValueError(f"{stream} has no payload inputs in {spec['module']}")
    return ports


class Composite(Responder):
    """Several responders on one row, each owning different ports."""

    def __init__(self, *parts: Responder):
        self._parts = parts
        claims = [p.claims() for p in parts]
        for i, a in enumerate(claims):
            for b in claims[i + 1:]:
                if a & b:
                    raise ValueError(f"ports claimed twice: {sorted(a & b)}")

    def claims(self):
        return set().union(*(p.claims() for p in self._parts))

    def reset(self):
        for p in self._parts:
            p.reset()

    def cycle(self, obs, drive):
        for p in self._parts:
            p.cycle(obs, drive)

    def stats(self):
        out = {}
        for p in self._parts:
            label = getattr(p, "label", type(p).__name__)
            for k, v in p.stats().items():
                out[f"{label}.{k}"] = v
        return out


class PlaneMemory(StreamResponder):
    """In-order 16-bit lane memory on one ``ReadRsp`` stream answering its ``ReadReq``.

    ``latency`` picks a fresh idle count in ``[0, max_latency]`` for every
    beat (the base class's fixed ``latency`` is left at 0), and
    ``valid_density`` adds random idle cycles on top.
    """

    def __init__(self, *, req: str, rsp: str, label: str, rng: random.Random, spec: dict,
                 max_latency: int = 4, valid_density: float = 0.8):
        payload = _payload_ports(spec, rsp)
        super().__init__(stream=rsp, payload=payload, rng=rng, valid_density=valid_density)
        self.label = label
        self._req = req
        self._data_port = f"{rsp}_payload_data"
        if self._data_port not in payload:
            raise ValueError(f"{rsp} has no data port ({payload})")
        self._max_latency = max_latency
        self._lanes: dict[int, int] = {}
        self._reqs = 0
        self._max_outstanding = 0

    def reset(self):
        super().reset()
        self._lanes.clear()
        self._reqs = 0
        self._max_outstanding = 0

    def _lane(self, addr: int) -> int:
        w = self._lanes.get(addr)
        if w is None:
            w = self._lanes[addr] = self._rng.getrandbits(16)
        return w

    def observe(self, obs):
        if obs.fired(self._req):
            addr = obs.beat(self._req)[f"{self._req}_payload_address"]
            self._reqs += 1
            self.enqueue({self._data_port: self._lane(addr)})
            self._max_outstanding = max(self._max_outstanding, len(self._queue))

    def cycle(self, obs, drive):
        # The base class's cycle with one change: a fresh random latency per
        # beat instead of the fixed `latency`.
        if self._presented is not None and obs.fired(self._stream):
            self._presented = None
            self._wait = self._rng.randrange(self._max_latency + 1)
            self._fired += 1
        self.observe(obs)
        if self._presented is None and self._wait == 0 and self._queue \
                and self._rng.random() < self._density:
            self._presented = self._queue.popleft()
        elif self._presented is None and self._wait:
            self._wait -= 1
        if self._presented is None:
            drive[self._valid] = 0
            for name in self._payload:
                drive[name] = 0
        else:
            drive[self._valid] = 1
            drive.update(self._presented)

    def stats(self):
        return {**super().stats(), "reqs": self._reqs,
                "max_outstanding": self._max_outstanding,
                "lanes_touched": len(self._lanes)}


class PixelCounters(Responder):
    """Claims nothing; counts the module's traffic so a run is known to be live."""

    def __init__(self):
        self._n = {}
        self.reset()

    def claims(self):
        return set()

    def reset(self):
        self._n = {"cycles": 0, "pixels_in": 0, "pixels_out": 0, "z_func_fail": 0,
                   "color_reqs": 0, "aux_reqs": 0, "busy_cycles": 0,
                   "input_stall_cycles": 0}

    def cycle(self, obs, drive):
        n = self._n
        n["cycles"] += 1
        n["pixels_in"] += int(obs.fired("io_input"))
        n["pixels_out"] += int(obs.fired("io_output"))
        n["color_reqs"] += int(obs.fired(_COLOR_REQ))
        n["aux_reqs"] += int(obs.fired(_AUX_REQ))
        # io_zFuncFail is `fetched.fire && depthKill`, combinational from the
        # pre-edge state; post-edge it describes the *next* beat, so this is
        # a count of cycles it was high rather than an exact pulse count.
        n["z_func_fail"] += int(obs["io_zFuncFail"])
        n["busy_cycles"] += int(obs["io_busy"])
        n["input_stall_cycles"] += int(obs["io_input_valid"] and not obs["io_input_ready"])

    def stats(self):
        return dict(self._n)


def plane_memory(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder`` for the ``FramebufferAccessMem`` row."""
    return Composite(
        PlaneMemory(req=_COLOR_REQ, rsp=_COLOR_RSP, label="Color",
                    rng=random.Random(rng.random()), spec=spec),
        PlaneMemory(req=_AUX_REQ, rsp=_AUX_RSP, label="Aux",
                    rng=random.Random(rng.random()), spec=spec),
        PixelCounters(),
    )
