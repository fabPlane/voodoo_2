"""Locality-biased ``io_writeReq`` stimulus for the ``FramebufferPlaneBuffer`` diff rows.

The generic driver re-randomises the write address per accepted beat, which
makes the combiner rotate on almost every write: the sequential-append path
(``address == pending + 4``) fires once per ``2**addressWidth`` writes and a
full-line drain needs ``lineWords - 1`` of them in a row -- never, on the
26-bit ``FramebufferPlaneBufferWb`` row.  This responder claims
``io_writeReq`` and emits the same mix ``tests/test_plane_buffer.py`` uses
(same word / next word / gap / next physical line / random), so the
``*Local`` rows in ``tools/diff/registry.py`` reach appends, merges, full
drains and the long 256-beat bursts in lockstep.  Everything else (``flush``,
``cmd.ready``, ``rsp``) stays with the generator, so the rows differ from
their random twins in the write stream only.

Lives under ``tests/diff`` rather than ``tools/diff`` because it is
module-specific stimulus, not harness machinery; the driver finds it through
``PYTHONPATH`` like any ``pkg.module:factory`` spec.
"""

from __future__ import annotations

import random

from tools.diff.responders import StreamResponder

__all__ = ["LocalWrites", "local_writes"]

_STREAM = "io_writeReq"
_ADDRESS = "io_writeReq_payload_address"
_DATA = "io_writeReq_payload_data"
_MASK = "io_writeReq_payload_mask"


class LocalWrites(StreamResponder):
    """Sequential runs of a chosen length, joined by jumps, with same-word merges mixed in.

    A run is ``length`` words at ``+4`` each; between runs the next address is
    a gap in the same line, the next physical line, or anywhere (unaligned
    too -- the module takes byte addresses).  Run lengths are drawn so that
    both the short single-word case and full lines (``line_words`` and a
    little over, which rolls into the next line) are common, whatever
    ``line_words`` is; the generic driver would never chain 256 appends.
    """

    #: (jump kind, weight) between runs.
    JUMPS = (("gap", 15), ("line", 35), ("random", 50))
    #: probability that a write repeats the previous address (byte merge).
    MERGE = 0.15

    def __init__(self, *, rng: random.Random, address_width: int, line_words: int,
                 valid_density: float = 0.7):
        super().__init__(stream=_STREAM, payload=[_ADDRESS, _DATA, _MASK],
                         rng=rng, valid_density=valid_density)
        self._mask = (1 << address_width) - 1
        self._width = address_width
        self._line_words = line_words
        self._line_bytes = 4 * line_words
        self._last = 0
        self._run_left = 0
        self._kinds = {"same": 0, "next": 0, "gap": 0, "line": 0, "random": 0}
        self._runs = 0

    def reset(self):
        super().reset()
        self._last = 0
        self._run_left = 0

    def _run_length(self) -> int:
        lw = self._line_words
        lengths = [1, 2, 3, 4, 8, lw - 1, lw, lw + 1, 2 * lw + 3]
        weights = [20, 15, 10, 10, 10, 8, 12, 10, 5]
        return max(1, self._rng.choices(lengths, weights=weights)[0])

    def observe(self, obs):
        if self._queue:
            return
        rng = self._rng
        if self._run_left > 0 and rng.random() < self.MERGE:
            kind, addr = "same", self._last
        elif self._run_left > 0:
            kind, addr = "next", (self._last + 4) & self._mask
            self._run_left -= 1
        else:
            kind = rng.choices([k for k, _ in self.JUMPS], weights=[w for _, w in self.JUMPS])[0]
            if kind == "gap":
                addr = (self._last + 8) & self._mask
            elif kind == "line":
                addr = ((self._last | (self._line_bytes - 1)) + 1) & self._mask
            else:
                addr = rng.getrandbits(self._width)
            self._run_left = self._run_length() - 1
            self._runs += 1
        self._kinds[kind] += 1
        self._last = addr
        self.enqueue({
            _ADDRESS: addr,
            _DATA: rng.getrandbits(32),
            _MASK: rng.choice([0xF, 0xF, 0xF, 0x3, 0xC, 0x1, 0x8, 0x6, 0x0]),
        })

    def stats(self):
        return {**super().stats(), "runs": self._runs,
                **{f"kind_{k}": v for k, v in self._kinds.items()}}


def _address_width(spec: dict) -> int:
    for p in spec["inputs"]:
        if p["spinal"] == _ADDRESS:
            return p["width"]
    raise ValueError(f"{_ADDRESS} is not an input of {spec['module']}")


def local_writes(line_words: int):
    """``DiffEntry.responder`` factory maker: ``local_writes(256)``."""

    def factory(*, seed: int, rng: random.Random, spec: dict):
        return LocalWrites(rng=rng, address_width=_address_width(spec),
                           line_words=line_words)

    return factory


#: The two rows' factories, named so the registry can spell them.
local_writes_256 = local_writes(256)
local_writes_2 = local_writes(2)
