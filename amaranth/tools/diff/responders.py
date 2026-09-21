"""The ``Responder`` protocol for per-row stimulus models, plus worked examples.

Kept separate from ``driver.py`` on purpose: that module reads ``$DIFF_SPEC``
and imports cocotb at import time, and a responder (or a test of one) must
be importable without a simulator behind it.

A responder replaces the random generator for the inputs it *claims*.  It
exists for the case the generator cannot handle: an input stream that is
really a slave's response, whose beats must correspond to commands the DUT
issued.  ``FramebufferPlaneReader`` was the first to need one -- its
``io_mem_rsp`` is only accepted while a matching miss is outstanding, so a
random held ``source=1`` beat with nothing outstanding parks the stream and
the remaining 999 970 cycles of the sweep prove nothing.

The protocol (full text on :class:`Responder` below): ``claims()`` names the Spinal
input ports it drives; ``reset()`` is called during reset; ``cycle(obs,
drive)`` runs on every falling edge, *after* the mismatch check and after
the generator has driven its own inputs for the next cycle.  ``obs[name]``
reads any Spinal port *now* -- outputs from the Spinal side, inputs as
last driven; ``obs.fired("io_mem_cmd")`` says whether a cmd beat crossed on
the rising edge that just passed and ``obs.beat("io_mem_cmd")`` is that
beat's payload as it was *before* the edge (the live port may already show
the next one).  ``drive[name] = value`` sets a claimed input for
the next rising edge.  Claiming a stream's ``valid`` claims its payload, and
the responder then owns the Spinal hold rule for that stream.

Register one on a row with ``DiffEntry(responder="pkg.module:factory")``;
the factory is called as ``factory(seed=, rng=, spec=)`` once per seed.

Two shapes are given here.  :class:`AckWhen` is the smallest possible one and
what ``tests/diff/test_diff_nodata.py`` runs; :class:`StreamResponder` is
the base a memory model wants -- it does the hold rule, so a subclass only
has to say what the next beat is.
"""

from __future__ import annotations

import random
from collections import deque

__all__ = ["Responder", "AckWhen", "StreamResponder", "ack_when_busy"]


class Responder:
    """Protocol for a per-row stimulus model (``DiffEntry.responder``).

    The random generator is right for a module whose inputs are independent
    of its outputs.  It is wrong for an input stream that is really a *slave
    response* -- a memory ``rsp`` whose ``source``/ordering must correspond to
    the ``cmd`` beats the DUT issued -- because a held beat the DUT will never
    accept blocks the stream forever and the rest of the run proves nothing.
    A responder replaces the generator for the inputs it claims.

    Lifecycle, per seed::

        r = factory(seed=seed, rng=random.Random(...), spec=SPEC)
        r.claims()      -> set of Spinal input port names it drives
        r.reset()       -> called while reset is asserted, inputs at zero
        r.cycle(obs, drive)   each cycle, see below

    ``cycle`` runs on the **falling edge**, after the mismatch check and after
    the generator has driven its own inputs for the next cycle.  ``obs`` is a
    lazy mapping from *any* Spinal port name to its current value: outputs
    read the Spinal side, inputs read what was driven for the cycle that just
    completed; so ``obs["io_mem_cmd_valid"] and obs["io_mem_cmd_ready"]`` is
    "a cmd beat fired on the rising edge that just passed".  ``drive`` is a
    ``dict`` the responder fills with ``{spinal_input_name: value}``; every
    claimed port must be present every cycle.  Values take effect before the
    next rising edge.

    Claiming a stream's ``valid`` claims its whole payload; the responder
    then owns the Spinal hold rule for that stream (payload stable while
    ``valid && !ready``) -- the generator will not enforce it for you.  A
    responder may own several streams (a memory model answering two ports).

    The rng handed in is derived from the seed but separate from the
    generator's, so the free-input stimulus of a row is identical with and
    without a responder attached.
    """

    def claims(self) -> set:
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def cycle(self, obs, drive: dict) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        """Optional counters, copied into the report so a run can prove the
        responder actually did something (beats issued, acks given ...)."""
        return {}


class AckWhen(Responder):
    """Assert ``drive`` (a scalar input) while ``observe`` (an output) is high.

    With ``density < 1`` the acknowledge is withheld at random, so the DUT is
    seen both waiting and proceeding.
    """

    def __init__(self, *, observe: str, drive: str, rng: random.Random,
                 density: float = 1.0):
        self._observe = observe
        self._drive = drive
        self._rng = rng
        self._density = density
        self._acks = 0

    def claims(self):
        return {self._drive}

    def reset(self):
        self._acks = 0

    def cycle(self, obs, drive):
        want = obs[self._observe] and self._rng.random() < self._density
        drive[self._drive] = 1 if want else 0
        self._acks += int(want)

    def stats(self):
        return {"acks": self._acks}


class StreamResponder(Responder):
    """Drive one input stream from a queue of beats, honouring the hold rule.

    Subclass and implement :meth:`observe`, which looks at the DUT (typically
    for command fires) and calls :meth:`enqueue` with the payload of each
    response beat as a ``{spinal_payload_port: value}`` dict.  The base class
    presents the head of the queue with ``valid`` high, holds it until the
    DUT's ``ready`` accepts it, and idles with ``valid`` low when the queue is
    empty.  ``latency`` beats of idling are inserted after each acceptance
    when set, and ``valid_density < 1`` adds random idle cycles on top, so the
    stream is not always immediately ready.

    ``stream`` is the Spinal base name (``io_mem_rsp``); ``payload`` lists its
    payload ports (``io_mem_rsp_payload_fragment_data`` ...) -- copy them from
    the port map's ``streams`` section.
    """

    def __init__(self, *, stream: str, payload: list[str], rng: random.Random,
                 latency: int = 0, valid_density: float = 1.0):
        self._stream = stream
        self._valid = f"{stream}_valid"
        self._ready = f"{stream}_ready"
        self._payload = list(payload)
        self._rng = rng
        self._latency = latency
        self._density = valid_density
        self._queue: deque[dict] = deque()
        self._presented: dict | None = None
        self._wait = 0
        self._fired = 0

    # -- subclass API --------------------------------------------------------

    def observe(self, obs) -> None:
        """Look at the DUT and :meth:`enqueue` the beats it has earned."""
        raise NotImplementedError

    def enqueue(self, beat: dict) -> None:
        missing = set(self._payload) - set(beat)
        if missing:
            raise ValueError(f"beat is missing payload ports {sorted(missing)}")
        self._queue.append(dict(beat))

    # -- Responder -----------------------------------------------------------

    def claims(self):
        return {self._valid, *self._payload}

    def reset(self):
        self._queue.clear()
        self._presented = None
        self._wait = 0
        self._fired = 0

    def stats(self):
        return {"beats_fired": self._fired, "queued": len(self._queue)}

    def cycle(self, obs, drive):
        # Did the beat presented for the cycle that just completed fire? The
        # snapshot is pre-edge, so this is exactly Spinal's `fire`.
        if self._presented is not None and obs.fired(self._stream):
            self._presented = None
            self._wait = self._latency
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


def ack_when_busy(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """Factory for ``tests/diff/test_diff_nodata.py``: ``io_done`` follows ``io_busy``."""
    return AckWhen(observe="io_busy", drive="io_done", rng=rng, density=0.5)
