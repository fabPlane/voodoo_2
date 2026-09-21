"""Word-read responses and neighbourly tap addresses for the ``TmuNoTextureCache`` diff rows.

Two things the generic driver would get wrong for the direct path
(``TmuTextureCache.scala:865``):

* ``io_texRead_rsp`` is a slave response.  The module joins it against the
  ``queued`` word request (``StreamJoin(rsp, queued)``), keeps only the beat
  with ``last`` set (``takeWhen``) and asks for one word per command
  (``length = 3``).  A random ``last`` drops random beats and a random held
  beat with nothing queued parks the response stream; both sides stay in
  lockstep through that, but the assembly path is hardly exercised.  So the
  row reuses :class:`tests.diff.tmu_stimulus.TexMemSlave`, which answers
  every accepted command with exactly the burst it asked for (one beat here),
  ``last`` on it, after random idle cycles.
* Random 26-bit tap addresses almost never share a word, so ``currentMask``
  would only ever have one bit set and the "several taps, one read" fold
  (``nextPending``) would go untested.  :class:`DirectRequests` drives
  ``io_sampleRequest`` with bilinear 2x2 footprints whose taps mostly land
  in one or two words (a 16-bit or 8-bit texel pitch, row pitch 4..512
  bytes), point samples, and a share of fully random requests (every other
  field -- ``lodBase``, the tables, the packed bank/pair/startup fields --
  is random every beat: the direct path never reads them, and the lockstep
  diff proves it).

``io_invalidate``, ``io_sampleFetch_ready`` and ``io_texRead_cmd_ready`` stay
with the generator: the first is a no-op here (kept random so the diff pins
that), the other two at ``--ready-density`` give the back-pressured run its
stalls on the fork, the two FIFOs and the single ``outValid`` slot.

Both rows (``TmuNoTextureCache`` = ``Config.voodoo1().copy(useTexFillCache =
false)``, ``TmuNoTextureCacheConsole`` = the Console's 2-deep request window)
use :func:`direct_traffic`.
"""

from __future__ import annotations

import random

from tests.diff.tmu_stimulus import Composite, TexMemSlave
from tools.diff.responders import Responder, StreamResponder

__all__ = ["DirectRequests", "FetchCounter", "direct_traffic"]

_REQ = "io_sampleRequest"
_FETCH = "io_sampleFetch"


class DirectRequests(StreamResponder):
    """``io_sampleRequest`` beats whose taps share words often enough to matter."""

    #: probability a request is bilinear
    BILINEAR = 0.5
    #: probability a request is fully random (taps included)
    RANDOM = 0.15

    def __init__(self, *, rng: random.Random, spec: dict, valid_density: float = 0.7) -> None:
        inputs = {p["spinal"]: p for p in spec["inputs"]}
        # Every io_sampleRequest_payload_* leaf, by name: ports.py takes the
        # handshake keyword closest to the end of a port name, so the four
        # `tapStartupDecode_N_valid` fields classify as the `valid` of a
        # one-port flow each rather than as this stream's payload.
        payload = [n for n in inputs if n.startswith(f"{_REQ}_payload_")]
        super().__init__(stream=_REQ, payload=payload, rng=rng, valid_density=valid_density)
        self._widths = {n: p["width"] for n, p in inputs.items()}
        self._aw = self._widths[f"{_REQ}_payload_pointAddr"]
        self._amask = (1 << self._aw) - 1
        self._kinds = {"point": 0, "bilinear": 0, "random": 0, "one_word": 0, "two_words": 0}

    def reset(self):
        super().reset()
        self._kinds = {k: 0 for k in self._kinds}

    def _beat(self) -> dict:
        rng = self._rng
        p = f"{_REQ}_payload_"
        b = {name: rng.getrandbits(self._widths[name]) for name in self._payload}
        if rng.random() < self.RANDOM:
            self._kinds["random"] += 1
            return b
        is16 = rng.random() < 0.5
        bpp = 2 if is16 else 1
        bilinear = rng.random() < self.BILINEAR
        base = rng.randrange(0, 1 << self._aw)
        pitch = rng.choice([4, 8, 16, 64, 512, 4 * bpp, bpp])
        taps = [(base + dx * bpp + dy * pitch) & self._amask for dy in (0, 1) for dx in (0, 1)]
        if rng.random() < 0.2:
            # Scatter one tap so a 3+1 word split happens too.
            taps[rng.randrange(4)] = rng.randrange(0, 1 << self._aw)
        b[p + "pointAddr"] = taps[0] if rng.random() < 0.7 else rng.randrange(0, 1 << self._aw)
        for i in range(4):
            b[p + f"biAddr{i}"] = taps[i]
        b[p + "is16Bit"] = int(is16)
        b[p + "bilinear"] = int(bilinear)
        b[p + "passthrough_bilinear"] = int(bilinear) if rng.random() < 0.8 else rng.getrandbits(1)
        self._kinds["bilinear" if bilinear else "point"] += 1
        if bilinear:
            words = {t & ~3 for t in taps}
            if len(words) == 1:
                self._kinds["one_word"] += 1
            elif len(words) == 2:
                self._kinds["two_words"] += 1
        return b

    def observe(self, obs):
        if not self._queue:
            self.enqueue(self._beat())

    def stats(self):
        return {**super().stats(), **{f"kind_{k}": v for k, v in self._kinds.items()}}


class FetchCounter(Responder):
    """Claims nothing; counts ``io_sampleFetch`` fires so a run can prove it flowed."""

    def __init__(self):
        self._obs = None
        self._fetches = 0

    def claims(self):
        return set()

    def reset(self):
        self._fetches = 0

    def cycle(self, obs, drive):
        self._obs = obs
        self._fetches += int(obs.fired(_FETCH))

    def stats(self):
        if self._obs is None:
            return {}
        return {"fetches": self._fetches, "busy_at_end": self._obs["io_busy"]}


def direct_traffic(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder``: one-word memory slave + word-sharing requests."""
    return Composite(TexMemSlave(rng=random.Random(rng.random()), spec=spec),
                     DirectRequests(rng=random.Random(rng.random()), spec=spec),
                     FetchCounter())
