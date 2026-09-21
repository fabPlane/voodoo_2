"""Well-formed memory responses and span-shaped reads for the plane-reader diff rows.

The generic driver randomises ``io_mem_rsp`` independently of the commands
the DUT issued and holds a beat while it is refused.  ``FramebufferPlaneReader``
only accepts a ``source = 1`` beat while a direct miss is outstanding, so the
first random ``source = 1`` beat with nothing outstanding parks the response
stream for the rest of the run: measured with a probe, ``fillHits = 1``,
four bursts, and 19 970 dead cycles out of 20 000.  It also never serves a
cache hit, because a random 26-bit ``io_readReq`` address equals the next
prefetched lane once in 2**26 requests.

Two responders fix that, composed per row in :mod:`tools.diff.registry`:

:class:`MemSlave`
    claims ``io_mem_rsp`` and answers every ``io_mem_cmd`` beat in order --
    ``((address & 3) + length) // 4 + 1`` data beats from a lazily randomised
    word image, ``source`` echoed, ``last`` on the final beat -- with random
    idle cycles between beats.  This is what ``SimBmbMemory`` does at the
    port; it is enough for both readers and never deadlocks.

:class:`SpanTraffic`
    claims ``io_prefetchReq`` *and* ``io_readReq``.  Spans are mostly short
    (1-16 lanes) with some full-width ones (up to ``maxFbDims._1`` lanes, the
    400-beat bursts the real rasteriser issues) and a few starting on an odd
    byte or an odd lane.  The reads for a span are generated when its
    prefetch beat fires: in order with lanes dropped (forward skips),
    occasional repeats (direct misses, "behind"), and a rare random address
    (a long forward skip, or a miss).  Reads are only generated after the
    prefetch fired, so the consumer lags the prefetcher, which is where cache
    hits come from -- but the prefetcher is throttled to stay within
    ``lookahead`` queued reads of the consumer.  Without that throttle the
    reader accepts 64 spans ahead, the 801-lane ``laneFifo`` fills, and a
    direct miss then waits behind fill beats the reader cannot take while
    its answer is what would let the consumer drain them: an in-order memory
    (this slave, ``SimBmbMemory``, the DDR bridge) deadlocks the reader in
    that state.  The real pipeline never gets there because its prefetch
    runs a span or two ahead of its reads, and so does this traffic.

    Inverted spans (``end < start``, which the Scala formal test assumes
    away) are left to the ``*MemOnly`` row: its random prefetches are
    inverted half the time, so the wrapping ``wordCount``/``laneCount``
    arithmetic and the resulting span/beat misaccounting are compared there,
    where they cannot starve the hit path of this traffic.

Both stimulus streams are pure functions of the seed, so a failure replays.
``stats()`` reports what was served so a clean run can be checked for
having actually exercised the paths (see ``tests/diff/test_diff_plane_reader.py``).

Lives under ``tests/diff`` like ``plane_buffer_stimulus.py``: module-specific
stimulus, not harness machinery.
"""

from __future__ import annotations

import random
from collections import deque

from tools.diff.responders import Responder, StreamResponder

__all__ = [
    "MemSlave",
    "SpanTraffic",
    "Composite",
    "DutCounters",
    "mem_slave",
    "reader_traffic",
]

_CMD = "io_mem_cmd"
_RSP = "io_mem_rsp"
_PREFETCH = "io_prefetchReq"
_READ = "io_readReq"


def _payload_ports(spec: dict, stream: str) -> list[str]:
    ports = [p["spinal"] for p in spec["inputs"]
             if p["stream"] == stream and p["role"] == "payload"]
    if not ports:
        raise ValueError(f"{stream} has no payload inputs in {spec['module']}")
    return ports


def _width(spec: dict, port: str) -> int:
    for p in spec["inputs"]:
        if p["spinal"] == port:
            return p["width"]
    raise ValueError(f"{port} is not an input of {spec['module']}")


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
            for k, v in p.stats().items():
                out[f"{type(p).__name__}.{k}"] = v
        return out


class DutCounters(Responder):
    """Claims nothing; reports the DUT's diagnostic counters at the end of a seed.

    The values come from the Spinal side (``obs`` reads outputs there), read
    once when :meth:`stats` is called rather than every cycle.
    """

    PORTS = ("io_fillHits", "io_fillMisses", "io_fillBurstCount", "io_fillBurstBeats",
             "io_fillStallCycles", "io_reqCount", "io_reqForwardStepCount",
             "io_reqBackwardStepCount", "io_reqSameWordCount", "io_reqOtherCount",
             "io_singleBeatBurstCount", "io_multiBeatBurstCount", "io_maxOccupancy",
             "io_busy", "io_cacheDebug", "io_cacheDebugOccupancy",
             "io_cacheDebugReadAddr", "io_cacheDebugExpectedAddr", "io_cacheDebugRemaining")

    def __init__(self):
        self._obs = None

    def claims(self):
        return set()

    def cycle(self, obs, drive):
        self._obs = obs

    def stats(self):
        if self._obs is None:
            return {}
        return {name[3:]: self._obs[name] for name in self.PORTS if name in self._obs}


class MemSlave(StreamResponder):
    """In-order read slave on ``io_mem_rsp`` answering ``io_mem_cmd`` beats."""

    def __init__(self, *, rng: random.Random, spec: dict, valid_density: float = 0.8):
        payload = _payload_ports(spec, _RSP)
        super().__init__(stream=_RSP, payload=payload, rng=rng, valid_density=valid_density)
        self._has = set(payload)
        self._words: dict[int, int] = {}
        self._cmds = 0
        self._miss_cmds = 0
        self._beats = 0

    def reset(self):
        super().reset()
        self._words.clear()
        self._cmds = self._miss_cmds = self._beats = 0

    def _word(self, addr: int) -> int:
        w = self._words.get(addr)
        if w is None:
            w = self._words[addr] = self._rng.getrandbits(32)
        return w

    def observe(self, obs):
        if not obs.fired(_CMD):
            return
        beat = obs.beat(_CMD)
        address = beat[f"{_CMD}_payload_fragment_address"]
        length = beat.get(f"{_CMD}_payload_fragment_length", 0)
        source = beat.get(f"{_CMD}_payload_fragment_source", 0)
        assert beat.get(f"{_CMD}_payload_fragment_opcode", 0) == 0, "readers only read"
        self._cmds += 1
        self._miss_cmds += int(source == 1)
        base = address & ~3
        beats = ((address & 3) + length) // 4 + 1
        for i in range(beats):
            b = {f"{_RSP}_payload_last": int(i == beats - 1),
                 f"{_RSP}_payload_fragment_opcode": 0,
                 f"{_RSP}_payload_fragment_data": self._word(base + 4 * i),
                 f"{_RSP}_payload_fragment_source": source}
            self.enqueue({k: v for k, v in b.items() if k in self._has})
            self._beats += 1

    def stats(self):
        return {**super().stats(), "cmds": self._cmds, "miss_cmds": self._miss_cmds,
                "beats_queued_total": self._beats}


class _Prefetches(StreamResponder):
    def __init__(self, *, rng, spec, addr_width, max_lanes, valid_density,
                 reads: "_Reads", lookahead: int):
        super().__init__(stream=_PREFETCH, payload=_payload_ports(spec, _PREFETCH),
                         rng=rng, valid_density=valid_density)
        self._mask = (1 << addr_width) - 1
        self._max_lanes = max_lanes
        self._reads = reads
        self._lookahead = lookahead
        self._long = 0

    def reset(self):
        super().reset()
        self._long = 0

    def observe(self, obs):
        if self._queue or self._reads.pending() > self._lookahead:
            return
        rng = self._rng
        r = rng.random()
        if r < 0.9:
            lanes = rng.randint(1, 16)
        else:
            lanes = rng.randint(17, self._max_lanes)
            self._long += 1
        start = rng.getrandbits(self._mask.bit_length()) & ~1
        if rng.random() < 0.2:
            start |= 1                       # exactLaneAddress clears it
        end = (start & ~1) + 2 * (lanes - 1)
        if rng.random() < 0.2:
            end |= 1
        self.enqueue({f"{_PREFETCH}_payload_startAddress": start & self._mask,
                      f"{_PREFETCH}_payload_endAddress": end & self._mask})

    def stats(self):
        return {**super().stats(), "long_spans": self._long}


class _Reads(StreamResponder):
    def __init__(self, *, rng, spec, addr_width, valid_density,
                 repeat_p: float = 0.1, random_p: float = 0.002):
        super().__init__(stream=_READ, payload=_payload_ports(spec, _READ),
                         rng=rng, valid_density=valid_density)
        self._mask = (1 << addr_width) - 1
        self._width = addr_width
        self._repeat_p = repeat_p
        self._random_p = random_p
        self._kinds = {"inorder": 0, "repeat": 0, "random": 0}
        self._staged: deque = deque()

    def reset(self):
        super().reset()
        self._staged.clear()
        for k in self._kinds:
            self._kinds[k] = 0

    def pending(self) -> int:
        return (len(self._queue) + (self._presented is not None)
                + sum(len(r) for _, r in self._staged))

    def observe(self, obs):
        # Release a span's reads three cycles after its prefetch fired: the
        # consumeQueue is a latency-2 StreamFifo, so a read presented the very
        # next cycle finds consumeExpectedValid low and is a direct miss.
        for entry in self._staged:
            entry[0] -= 1
        while self._staged and self._staged[0][0] <= 0:
            for a in self._staged.popleft()[1]:
                self.enqueue({f"{_READ}_payload_address": a & self._mask})
        if not obs.fired(_PREFETCH):
            return
        beat = obs.beat(_PREFETCH)
        start = beat[f"{_PREFETCH}_payload_startAddress"] & ~1
        end = beat[f"{_PREFETCH}_payload_endAddress"] & ~1
        rng = self._rng
        seen = []
        addr = start
        while addr <= end:
            # Lanes are dropped at random (pipeline-side kills), but never the
            # span's last lane: the reader advances its consume pointer only
            # on a request at or beyond it, so a dropped last lane leaves the
            # pointer parked there and every lower address that follows --
            # half of all spans, placed at random -- becomes a direct miss.
            # The Scala comment calls a non-monotonic request an invariant
            # violation; the real pipeline ends each span where it started it.
            if addr == end or rng.random() < 0.8:
                seen.append(addr)
                self._kinds["inorder"] += 1
            if seen and addr != end and rng.random() < self._repeat_p:
                # A repeat of an earlier lane of the *same* span is "behind"
                # the consume pointer: a cheap direct miss. Never after the
                # last lane: the pointer is then on the next span, whose
                # start is a random address, and a higher repeat address is
                # "ahead" of it -- it skips that span (and any other lower
                # one queued) and every read of it becomes a miss.
                seen.append(rng.choice(seen))
                self._kinds["repeat"] += 1
            if rng.random() < self._random_p:
                # A global random address: half the time it is ahead of every
                # queued span, which skips them all and turns the rest of the
                # lookahead window into direct misses -- so keep it rare.
                seen.append(rng.getrandbits(self._width) & ~1)
                self._kinds["random"] += 1
            addr += 2
        self._staged.append([3, seen])

    def stats(self):
        return {**super().stats(), **{f"kind_{k}": v for k, v in self._kinds.items()}}


class SpanTraffic(Composite):
    """``io_prefetchReq`` spans and the reads that follow them."""

    def __init__(self, *, rng: random.Random, spec: dict, max_lanes: int,
                 prefetch_density: float = 0.5, read_density: float = 0.7,
                 lookahead: int = 48, in_order: bool = False):
        aw = _width(spec, f"{_READ}_payload_address")
        # ``in_order``: no repeats and no random addresses -- a read *behind*
        # the consume pointer is a direct miss, and the ``allowDirectMiss =
        # false`` scanout reader waits forever for one.  Dropped lanes stay:
        # they are ahead of the pointer and are skipped.
        reads = _Reads(rng=random.Random(rng.random()), spec=spec, addr_width=aw,
                       valid_density=read_density,
                       **(dict(repeat_p=0.0, random_p=0.0) if in_order else {}))
        prefetches = _Prefetches(rng=random.Random(rng.random()), spec=spec,
                                 addr_width=aw, max_lanes=max_lanes,
                                 valid_density=prefetch_density, reads=reads,
                                 lookahead=lookahead)
        # Reads first: in the cycle a prefetch fires, the reads model must
        # stage that span's reads *before* the prefetcher re-checks
        # ``pending()``, or two long spans go out back to back (1200 lanes
        # into an 801-lane FIFO) and the reader deadlocks as described above.
        super().__init__(reads, prefetches)


def mem_slave(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder``: memory side only; prefetch/read stay random."""
    return Composite(MemSlave(rng=rng, spec=spec), DutCounters())


def reader_traffic(max_lanes: int, *, in_order: bool = False):
    """``DiffEntry.responder`` factory maker: memory side + span-shaped traffic."""

    def factory(*, seed: int, rng: random.Random, spec: dict) -> Responder:
        return Composite(MemSlave(rng=random.Random(rng.random()), spec=spec),
                         SpanTraffic(rng=random.Random(rng.random()), spec=spec,
                                     max_lanes=max_lanes, in_order=in_order),
                         DutCounters())

    return factory


#: ``Config.voodoo1()`` allows ``maxFbDims._1 = 800`` lanes per span; 600 keeps
#: one long span plus the lookahead inside the 801-lane ``laneFifo``.
reader_traffic_800 = reader_traffic(600)

#: The scanout reader (``allowDirectMiss = false``): same spans, reads strictly
#: in order (with drops), because a read behind the pointer would never return.
reader_traffic_800_in_order = reader_traffic(600, in_order=True)
