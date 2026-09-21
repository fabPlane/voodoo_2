"""Memory slaves and reader-shaped traffic for the ``FramebufferMemSubsystem`` diff row.

The subsystem has five ``Bmb`` masters and three cached ``FramebufferPlaneReader``
instances behind them, so the generic driver parks it for the same reason it
parks a lone reader (``tests/diff/plane_reader_stimulus.py``): a random
``io_fb*ReadMem_rsp`` beat that no outstanding command matches is refused for
ever, and a random 26-bit read address is never the next prefetched lane.
Measured on ``FramebufferMemSubsystemRandom``: the two read buses are dead
within a few hundred cycles of every seed while the write path keeps going.

What this module attaches instead (``subsystem_traffic``):

:class:`MemSlave` x 4
    one per live ``fbMem`` port (``fbColorWriteMem``, ``fbAuxWriteMem``,
    ``fbColorReadMem``, ``fbAuxReadMem``; ``fbMemWrite`` is tied off in
    ``Config.voodoo1()`` and stays with the generator).  In-order, like
    ``SimBmbMemory``: a read command yields ``((address & 3) + length) // 4 + 1``
    data beats from a lazily randomised word image; a write burst is consumed
    beat by beat and answered with one ``last`` beat after its ``last``
    command beat.  ``source`` is echoed, which is the contract the arbiters'
    response demux relies on (``lib/membus.py`` note 3): the colour read port
    carries scanout (route 0), the colour reader (1) and ``lfbReadBus`` (2)
    in its low two source bits.
:class:`SpanTraffic` x 3
    span-shaped prefetch + read traffic for the colour, aux and scanout
    readers -- the ``plane_reader_stimulus`` mix, re-targetable to a stream
    pair.  The scanout reader is built ``allowDirectMiss = false``, so its
    traffic is strictly in order (``in_order=True``: lanes may still be
    dropped, which is a forward skip, but never repeated or random -- a read
    behind the consume pointer would wait for ever there).
:class:`LfbReads`
    ``io_lfbReadBus_cmd``: the single-word reads ``Lfb.scala:341`` issues
    (``length = 3``, ``last``, ``source = 0``), plus a few with ``source = 1``,
    a few multi-word reads and a few write bursts so the arbiter's
    ``fragmentLock`` and the memory's write path are exercised from this
    port too.
:class:`DutCounters`
    claims nothing; copies ``io_stats_*`` / ``io_status_*`` and the scanout
    diagnostics into the report so a clean run can be checked for activity.

Writes (``io_colorWrite`` / ``io_auxWrite``), ``io_flush``, every output
stream's ``ready`` and every ``cmd.ready`` stay with the generator.

Everything is a pure function of the seed, so a failing seed replays exactly.
"""

from __future__ import annotations

import random
from collections import deque

from tools.diff.responders import Responder, StreamResponder

__all__ = [
    "MemSlave",
    "SpanTraffic",
    "LfbReads",
    "DutCounters",
    "Composite",
    "subsystem_traffic",
]


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
                out[f"{p.label}.{k}"] = v
        return out


class MemSlave(StreamResponder):
    """In-order ``Bmb`` slave on ``<bus>_rsp`` answering ``<bus>_cmd`` beats, reads and writes."""

    def __init__(self, *, bus: str, rng: random.Random, spec: dict,
                 valid_density: float = 0.8):
        self.label = bus.removeprefix("io_")
        self._cmd = f"{bus}_cmd"
        self._rsp = f"{bus}_rsp"
        payload = _payload_ports(spec, self._rsp)
        super().__init__(stream=self._rsp, payload=payload, rng=rng,
                         valid_density=valid_density)
        self._has = set(payload)
        self._words: dict[int, int] = {}
        self._reads = 0
        self._writes = 0
        self._write_beats = 0
        self._read_beats = 0
        self._by_route = [0, 0, 0, 0]
        self._in_write = False
        self._write_source = 0

    def reset(self):
        super().reset()
        self._words.clear()
        self._reads = self._writes = self._write_beats = self._read_beats = 0
        self._by_route = [0, 0, 0, 0]
        self._in_write = False

    def _word(self, addr: int) -> int:
        w = self._words.get(addr)
        if w is None:
            w = self._words[addr] = self._rng.getrandbits(32)
        return w

    def _rsp_beat(self, *, last: int, data: int, source: int) -> None:
        b = {f"{self._rsp}_payload_last": last,
             f"{self._rsp}_payload_fragment_opcode": 0,
             f"{self._rsp}_payload_fragment_data": data,
             f"{self._rsp}_payload_fragment_source": source}
        self.enqueue({k: v for k, v in b.items() if k in self._has})

    def observe(self, obs):
        if not obs.fired(self._cmd):
            return
        beat = obs.beat(self._cmd)
        address = beat[f"{self._cmd}_payload_fragment_address"]
        length = beat.get(f"{self._cmd}_payload_fragment_length", 0)
        source = beat.get(f"{self._cmd}_payload_fragment_source", 0)
        opcode = beat.get(f"{self._cmd}_payload_fragment_opcode", 0)
        last = beat.get(f"{self._cmd}_payload_last", 1)
        if opcode == 0:
            # A read is one command beat; `last` is not looked at (SimBmbMemory
            # starts a burst on any read beat).
            self._reads += 1
            self._by_route[source & 3] += 1
            base = address & ~3
            beats = ((address & 3) + length) // 4 + 1
            for i in range(beats):
                self._rsp_beat(last=int(i == beats - 1), data=self._word(base + 4 * i),
                               source=source)
                self._read_beats += 1
            return
        # A write: data beats until `last`, then a single response beat with
        # the source of the burst's first beat (SimBmbMemory latches it in Idle).
        if not self._in_write:
            self._in_write = True
            self._write_source = source
        self._write_beats += 1
        data = beat.get(f"{self._cmd}_payload_fragment_data", 0)
        mask = beat.get(f"{self._cmd}_payload_fragment_mask", 0xF)
        word = self._word(address & ~3)
        for lane in range(4):
            if mask >> lane & 1:
                word = (word & ~(0xFF << (8 * lane))) | (data & (0xFF << (8 * lane)))
        self._words[address & ~3] = word
        if last:
            self._in_write = False
            self._writes += 1
            self._by_route[self._write_source & 3] += 1
            self._rsp_beat(last=1, data=0, source=self._write_source)

    def stats(self):
        return {**super().stats(), "reads": self._reads, "read_beats": self._read_beats,
                "writes": self._writes, "write_beats": self._write_beats,
                **{f"route{i}": n for i, n in enumerate(self._by_route)}}


class _Prefetches(StreamResponder):
    def __init__(self, *, stream, rng, spec, addr_width, max_lanes, valid_density,
                 reads: "_Reads", lookahead: int):
        super().__init__(stream=stream, payload=_payload_ports(spec, stream),
                         rng=rng, valid_density=valid_density)
        self._start = f"{stream}_payload_startAddress"
        self._end = f"{stream}_payload_endAddress"
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
        if rng.random() < 0.9:
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
        self.enqueue({self._start: start & self._mask, self._end: end & self._mask})

    def stats(self):
        return {**super().stats(), "long_spans": self._long}


class _Reads(StreamResponder):
    def __init__(self, *, stream, prefetch, rng, spec, addr_width, valid_density,
                 in_order: bool):
        super().__init__(stream=stream, payload=_payload_ports(spec, stream),
                         rng=rng, valid_density=valid_density)
        self._addr = f"{stream}_payload_address"
        self._prefetch = prefetch
        self._pf_start = f"{prefetch}_payload_startAddress"
        self._pf_end = f"{prefetch}_payload_endAddress"
        self._mask = (1 << addr_width) - 1
        self._width = addr_width
        self._in_order = in_order
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
        # next cycle finds consumeExpectedValid low (a direct miss, or on the
        # scanout reader a wait).
        for entry in self._staged:
            entry[0] -= 1
        while self._staged and self._staged[0][0] <= 0:
            for a in self._staged.popleft()[1]:
                self.enqueue({self._addr: a & self._mask})
        if not obs.fired(self._prefetch):
            return
        beat = obs.beat(self._prefetch)
        start = beat[self._pf_start] & ~1
        end = beat[self._pf_end] & ~1
        rng = self._rng
        seen = []
        addr = start
        while addr <= end:
            # Lanes are dropped at random (pipeline-side kills) but never the
            # span's last lane -- see plane_reader_stimulus for why.
            if addr == end or rng.random() < 0.8:
                seen.append(addr)
                self._kinds["inorder"] += 1
            if not self._in_order:
                if seen and addr != end and rng.random() < 0.1:
                    seen.append(rng.choice(seen))       # "behind": a direct miss
                    self._kinds["repeat"] += 1
                if rng.random() < 0.002:
                    seen.append(rng.getrandbits(self._width) & ~1)
                    self._kinds["random"] += 1
            addr += 2
        self._staged.append([3, seen])

    def stats(self):
        return {**super().stats(), **{f"kind_{k}": v for k, v in self._kinds.items()}}


class SpanTraffic(Composite):
    """``<prefetch>`` spans and the ``<read>`` requests that follow them."""

    def __init__(self, *, label: str, prefetch: str, read: str, rng: random.Random,
                 spec: dict, max_lanes: int, in_order: bool = False,
                 prefetch_density: float = 0.5, read_density: float = 0.7,
                 lookahead: int = 48):
        self.label = label
        aw = _width(spec, f"{read}_payload_address")
        reads = _Reads(stream=read, prefetch=prefetch, rng=random.Random(rng.random()),
                       spec=spec, addr_width=aw, valid_density=read_density,
                       in_order=in_order)
        prefetches = _Prefetches(stream=prefetch, rng=random.Random(rng.random()),
                                 spec=spec, addr_width=aw, max_lanes=max_lanes,
                                 valid_density=prefetch_density, reads=reads,
                                 lookahead=lookahead)
        reads.label, prefetches.label = "reads", "prefetches"
        # Reads first, so a fired prefetch's reads are staged before the
        # prefetcher re-checks pending() (plane_reader_stimulus's finding).
        super().__init__(reads, prefetches)


class LfbReads(StreamResponder):
    """``io_lfbReadBus_cmd``: mostly ``Lfb.scala:341``'s single-word reads."""

    label = "lfb"

    def __init__(self, *, rng: random.Random, spec: dict, valid_density: float = 0.3,
                 max_pending: int = 8):
        stream = "io_lfbReadBus_cmd"
        super().__init__(stream=stream, payload=_payload_ports(spec, stream), rng=rng,
                         valid_density=valid_density)
        self._p = f"{stream}_payload"
        self._aw = _width(spec, f"{self._p}_fragment_address")
        self._lw = _width(spec, f"{self._p}_fragment_length")
        self._max_pending = max_pending
        self._kinds = {"read1": 0, "readN": 0, "write": 0}
        self._pending = 0
        self._rsp_beats = self._rsp_last = 0

    def reset(self):
        super().reset()
        self._pending = 0
        self._rsp_beats = self._rsp_last = 0
        for k in self._kinds:
            self._kinds[k] = 0

    def _beat(self, *, last, source, opcode, address, length, data=0, mask=0):
        return {f"{self._p}_last": last, f"{self._p}_fragment_source": source,
                f"{self._p}_fragment_opcode": opcode, f"{self._p}_fragment_address": address,
                f"{self._p}_fragment_length": length, f"{self._p}_fragment_data": data,
                f"{self._p}_fragment_mask": mask}

    def observe(self, obs):
        if obs.fired("io_lfbReadBus_rsp"):
            self._rsp_beats += 1
            if obs.beat("io_lfbReadBus_rsp")["io_lfbReadBus_rsp_payload_last"]:
                self._pending -= 1
                self._rsp_last += 1
        if self._queue or self._pending >= self._max_pending:
            return
        rng = self._rng
        self._pending += 1
        addr = rng.getrandbits(self._aw) & ~3
        source = int(rng.random() < 0.1)
        r = rng.random()
        if r < 0.85:
            self._kinds["read1"] += 1
            self.enqueue(self._beat(last=1, source=source, opcode=0, address=addr, length=3))
        elif r < 0.95:
            self._kinds["readN"] += 1
            words = rng.randint(2, 16)
            self.enqueue(self._beat(last=1, source=source, opcode=0,
                                    address=addr | (rng.getrandbits(2) if rng.random() < 0.3 else 0),
                                    length=(4 * words - 1) & ((1 << self._lw) - 1)))
        else:
            self._kinds["write"] += 1
            words = rng.randint(1, 4)
            for i in range(words):
                self.enqueue(self._beat(last=int(i == words - 1), source=source, opcode=1,
                                        address=(addr + 4 * i) & ((1 << self._aw) - 1),
                                        length=4 * words - 1, data=rng.getrandbits(32),
                                        mask=rng.getrandbits(4)))

    def stats(self):
        return {**super().stats(), **{f"kind_{k}": v for k, v in self._kinds.items()},
                "rsp_beats": self._rsp_beats, "rsp_last": self._rsp_last,
                "pending": self._pending}


class DutCounters(Responder):
    """Claims nothing; reports the DUT's ``io_stats_*`` and diagnostics at the end of a seed."""

    label = "dut"
    PREFIXES = ("io_stats_", "io_status_", "io_scanoutFill", "io_scanoutCacheOccupancy")

    def __init__(self, spec: dict):
        self._ports = [p["spinal"] for p in spec["outputs"]
                       if p["spinal"].startswith(self.PREFIXES)]
        self._obs = None

    def claims(self):
        return set()

    def cycle(self, obs, drive):
        self._obs = obs

    def stats(self):
        if self._obs is None:
            return {}
        return {name[3:]: self._obs[name] for name in self._ports}


#: ``Config.voodoo1()`` allows ``maxFbDims._1 = 800`` lanes per span; 600 keeps
#: one long span plus the lookahead inside the 801-lane ``laneFifo``.
MAX_LANES = 600


def subsystem_traffic(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder`` for ``FramebufferMemSubsystem``."""
    sub = lambda: random.Random(rng.random())  # noqa: E731
    return Composite(
        MemSlave(bus="io_fbColorWriteMem", rng=sub(), spec=spec),
        MemSlave(bus="io_fbAuxWriteMem", rng=sub(), spec=spec),
        MemSlave(bus="io_fbColorReadMem", rng=sub(), spec=spec),
        MemSlave(bus="io_fbAuxReadMem", rng=sub(), spec=spec),
        SpanTraffic(label="color", prefetch="io_prefetchColor", read="io_colorReadReq",
                    rng=sub(), spec=spec, max_lanes=MAX_LANES),
        SpanTraffic(label="aux", prefetch="io_prefetchAux", read="io_auxReadReq",
                    rng=sub(), spec=spec, max_lanes=MAX_LANES),
        SpanTraffic(label="scanout", prefetch="io_scanoutPrefetchReq",
                    read="io_scanoutReadReq", rng=sub(), spec=spec, max_lanes=MAX_LANES,
                    in_order=True),
        LfbReads(rng=sub(), spec=spec),
        DutCounters(spec),
    )
