"""Cache-shaped stimulus for the ``TmuTextureCache`` diff row.

The generic driver is the wrong stimulus for this module on three counts:

* ``io_texRead_rsp`` is a slave response.  The module halts it while no fill
  is active (``haltWhen(!fill.activeReg)``), so a random held beat parks the
  stream until the next miss and then becomes the first beat of that fill;
  and a random ``last`` ends a burst after any number of words, so the line
  is marked ready with most of its words never written.  Both sides stay in
  lockstep through all of that, but the interesting paths (a full 8-word
  fill, the owner picking up its texel from the word that carries it, the
  continue fill for the next unresolved tap) are hardly ever reached.
* Random 26-bit tap addresses never revisit a line: the cache holds 16 lines
  of 32 bytes out of a 64 MiB space, so there are no hits, no bilinear
  requests that are partly cached, and no victim reuse.
* ``io_invalidate`` is a free input, re-randomised every cycle; at 50 % per
  cycle the epoch moves faster than a fill completes, so nothing would hit
  even with a small working set.

So this responder owns all three.  ``io_texRead_rsp`` answers every accepted
``io_texRead_cmd`` with exactly ``(length + 1) / 4`` beats of random data,
``last`` on the final one, ``source`` echoed, after a random latency and
with random idle cycles between beats.  ``io_sampleRequest`` draws taps from
a small working set of texture rows of a fixed 16-bit or 8-bit layout
(``texTables`` / ``lodBase`` / ``lodShift`` as ``Tmu`` would compute them for
one 256 x 256 mip chain), with the per-tap ``tapPackedBank`` /
``tapPackedPair`` / ``tapStartupDecode`` computed by the same
``packedLocation22`` arithmetic the hardware uses, so the bank a tap is
looked up in is the bank the fill wrote it to.  A share of requests are
fully random instead, so the arbitrary-input paths the generic driver would
have covered still are.  ``io_invalidate`` pulses about once per 3000
cycles.  Everything else -- ``io_sampleFetch_ready``, ``io_texRead_cmd_ready``
-- stays with the generator at ``--ready-density``.

Lives under ``tests/diff`` because it is module-specific stimulus, not
harness machinery; the driver finds it through ``PYTHONPATH`` like any
``pkg.module:factory`` spec.
"""

from __future__ import annotations

import random

from tools.diff.responders import Responder, StreamResponder

__all__ = ["CacheTraffic", "cache_traffic", "packed_location", "PackedLayout"]

_REQ = "io_sampleRequest"
_RSP = "io_texRead_rsp"
_CMD = "io_texRead_cmd"
_INVALIDATE = "io_invalidate"

PACKED_STARTUP_OFFSET_LOW_WIDTH = 10


# ---------------------------------------------------------------------------
# a Python model of the packed layout arithmetic (Tmu.packedLocation22)
# ---------------------------------------------------------------------------


def packed_location(addr: int, is16_bit: bool, lod_base: int, lod_shift: int,
                    tex_base: list[int], tex_end: list[int], tex_shift: list[int]):
    """``Tmu.packedLocation22`` on ints: ``(in, bank, pair)`` for one byte address.

    Mirrors :func:`avoodoo.texture.tmu_types.packed_location22`: the LOD scan
    is last-match-wins, the offset is taken in the low 10 bits, and the
    16-bit path ORs in the neighbouring bit for both bank components.
    """
    addr22 = addr & 0x3FFFFF
    in_ = 0
    base = lod_base & 0x3FFFFF
    shift = lod_shift & 0xF
    for lod in range(len(tex_base)):
        if tex_base[lod] <= addr22 < tex_end[lod]:
            in_, base, shift = 1, tex_base[lod], tex_shift[lod]
    low = PACKED_STARTUP_OFFSET_LOW_WIDTH
    offs = (addr22 - base) & ((1 << low) - 1)
    bank_msb = (offs >> shift) & 1
    bank_lsb = offs & 1
    if is16_bit:
        bank_msb |= (offs >> ((shift + 1) & 0xF)) & 1
        bank_lsb |= (offs >> 1) & 1
    bank = (bank_msb << 1) | bank_lsb
    pair = 0 if is16_bit else (offs >> 1) & 1
    return in_, bank, pair


class PackedLayout:
    """One texture's packed layout: a 256 x 256 mip chain at ``base``.

    ``tex_shift[lod]`` is the log2 row pitch in texels, so ``+2 ** shift``
    texels is "one row down"; a 16-bit texel is 2 bytes, an 8-bit one 1.
    """

    def __init__(self, *, base: int, is16_bit: bool, top_log2: int = 8) -> None:
        self.is16_bit = is16_bit
        self.bpp = 2 if is16_bit else 1
        self.tex_base: list[int] = []
        self.tex_end: list[int] = []
        self.tex_shift: list[int] = []
        addr = base
        for lod in range(9):
            log2 = max(0, top_log2 - lod)
            size = (1 << (2 * log2)) * self.bpp
            self.tex_base.append(addr & 0x3FFFFF)
            self.tex_end.append((addr + size) & 0x3FFFFF)
            self.tex_shift.append(log2)
            addr += size
        self.lod_base = self.tex_base[0]
        self.lod_shift = self.tex_shift[0]

    def location(self, addr: int):
        return packed_location(addr, self.is16_bit, self.lod_base, self.lod_shift,
                               self.tex_base, self.tex_end, self.tex_shift)

    def row_bytes(self, lod: int) -> int:
        return (1 << self.tex_shift[lod]) * self.bpp


# ---------------------------------------------------------------------------
# the responders
# ---------------------------------------------------------------------------


class _LineFills(StreamResponder):
    """``io_texRead_rsp``: one ``(length + 1) / 4``-beat burst per accepted cmd."""

    def __init__(self, *, rng: random.Random, payload: list[str], latency_max: int = 6,
                 valid_density: float = 0.8) -> None:
        super().__init__(stream=_RSP, payload=payload, rng=rng, valid_density=valid_density)
        self._latency_max = latency_max
        self._pending: list[tuple[int, list[dict]]] = []
        self._cmds = 0
        self._bursts = 0
        self._data = f"{_RSP}_payload_fragment_data"
        self._last = f"{_RSP}_payload_last"
        self._source = f"{_RSP}_payload_fragment_source"
        self._opcode = f"{_RSP}_payload_fragment_opcode"

    def reset(self):
        super().reset()
        self._pending.clear()
        self._cmds = 0
        self._bursts = 0

    def observe(self, obs):
        if obs.fired(_CMD):
            beat = obs.beat(_CMD)
            length = beat.get(f"{_CMD}_payload_fragment_length", 0)
            words = max(1, (length + 1) // 4)
            source = beat.get(f"{_CMD}_payload_fragment_source", 0)
            burst = []
            for i in range(words):
                b = {name: 0 for name in self._payload}
                b[self._data] = self._rng.getrandbits(32)
                b[self._last] = int(i == words - 1)
                if self._source in b:
                    b[self._source] = source
                if self._opcode in b:
                    b[self._opcode] = 0
                burst.append(b)
            self._pending.append((self._rng.randint(0, self._latency_max), burst))
            self._cmds += 1
        # Release the head burst once its latency has elapsed and the queue
        # has drained: bursts never interleave, as the memory subsystem's
        # in-order response path guarantees.
        if self._pending and not self._queue and self._presented is None:
            wait, burst = self._pending[0]
            if wait == 0:
                self._pending.pop(0)
                for b in burst:
                    self.enqueue(b)
                self._bursts += 1
            else:
                self._pending[0] = (wait - 1, burst)

    def stats(self):
        return {**super().stats(), "cmds": self._cmds, "bursts": self._bursts}


class _Requests(StreamResponder):
    """``io_sampleRequest``: bilinear-consistent taps from a small working set.

    Every tap must decode as *inside* one LOD of the request's ``texTables``,
    or the fill never writes the (bank, pair) the owner waits for and the
    cache re-fills that line forever (``continuePendingValid`` is re-armed
    after every fill while a tap is unresolved, and ``owner.active`` blocks
    all lookups meanwhile).  ``Tmu`` guarantees that by wrapping / clamping
    the texel coordinates inside the LOD before it forms addresses, so this
    generator does the same: it picks ``(s, t)`` in a ``2**log2`` square and
    wraps the three bilinear neighbours.  Banks, pairs and the startup decode
    of each tap's line come from :func:`packed_location`, i.e. the same
    arithmetic the fill uses to place texels.
    """

    #: how many texels around the hot point a request lands (per axis);
    #: drawn per request so both tight reuse and set conflicts happen
    HOT_SPANS = (0, 1, 2, 3, 6)
    #: probability of a bilinear request
    BILINEAR = 0.5
    #: probability the hot point jumps to a new LOD / place
    JUMP = 0.03
    #: probability that a raw ``texEnd`` entry is sent as 0 (fallback path)
    ZERO_END = 0.15
    #: probability the address carries a non-zero bit 22+ alias
    HIGH_ALIAS = 0.1

    def __init__(self, *, rng: random.Random, payload: list[str], widths: dict[str, int],
                 valid_density: float = 0.7) -> None:
        super().__init__(stream=_REQ, payload=payload, rng=rng, valid_density=valid_density)
        self._widths = widths
        self._aw = widths[f"{_REQ}_payload_pointAddr"]
        self._amask = (1 << self._aw) - 1
        # Two textures: a 16-bit one and an 8-bit one, apart in memory.
        self._layouts = [
            PackedLayout(base=0x000000, is16_bit=True),
            PackedLayout(base=0x100000, is16_bit=False),
        ]
        self._hot: list[tuple[int, int, int] | None] = [None, None]
        self._kinds = {"point": 0, "bilinear": 0, "zero_end": 0, "alias": 0}

    def reset(self):
        super().reset()
        self._hot = [None, None]
        self._kinds = {k: 0 for k in self._kinds}

    def _pick(self, layout_idx: int) -> tuple[int, int, int]:
        """``(lod, s, t)`` near the layout's hot point, moving it now and then."""
        rng = self._rng
        lay = self._layouts[layout_idx]
        if self._hot[layout_idx] is None or rng.random() < self.JUMP:
            # LOD 0's 512-byte rows are 16 lines apart, i.e. every row of a
            # column maps to the same set (texFillXorIndex = false), so the
            # small LODs are where hits live and the large ones where the
            # 2-way victim rotation does; weight them evenly.
            lod = rng.choices(range(9), weights=[12, 12, 12, 12, 12, 10, 10, 10, 10])[0]
            n = 1 << lay.tex_shift[lod]
            self._hot[layout_idx] = (lod, rng.randrange(n), rng.randrange(n))
        lod, hs, ht = self._hot[layout_idx]
        n = 1 << lay.tex_shift[lod]
        span = rng.choice(self.HOT_SPANS)
        s = (hs + rng.randint(-span, span)) % n
        t = (ht + rng.randint(-span, span)) % n
        return lod, s, t

    def _build(self, lay: PackedLayout, lod: int, s: int, t: int, bilinear: bool) -> dict:
        rng = self._rng
        b = {name: 0 for name in self._payload}
        p = f"{_REQ}_payload_"
        n = 1 << lay.tex_shift[lod]
        bpp = lay.bpp

        def texel(ss, tt):
            return lay.tex_base[lod] + ((tt % n) * n + (ss % n)) * bpp

        taps = [texel(s, t), texel(s + 1, t), texel(s, t + 1), texel(s + 1, t + 1)]
        alias = 0
        if self._aw > 22 and rng.random() < self.HIGH_ALIAS:
            alias = rng.getrandbits(self._aw - 22) << 22
            self._kinds["alias"] += 1
        b[p + "pointAddr"] = (taps[0] | alias) & self._amask
        for i in range(4):
            b[p + f"biAddr{i}"] = (taps[i] | alias) & self._amask
        b[p + "lodBase"] = lay.lod_base
        b[p + "lodShift"] = lay.lod_shift
        b[p + "is16Bit"] = int(lay.is16_bit)
        zeroed = False
        for i in range(9):
            b[p + f"texTables_texBase_{i}"] = lay.tex_base[i]
            raw_end = lay.tex_end[i]
            if rng.random() < self.ZERO_END:
                # `startTexEnd` falls back to the next LOD's base (or base +
                # texel size for LOD 8), which is exactly the true end of a
                # contiguous chain, so the bank arithmetic below still holds.
                raw_end = 0
                zeroed = True
            b[p + f"texTables_texEnd_{i}"] = raw_end
            b[p + f"texTables_texShift_{i}"] = lay.tex_shift[i]
        if zeroed:
            self._kinds["zero_end"] += 1
        for i, tap in enumerate(taps):
            tap_addr = tap if (bilinear or i > 0) else taps[0]
            _in, bank, pair = lay.location(tap_addr)
            assert _in, (lay.is16_bit, lod, s, t, i)
            b[p + f"tapPackedBank_{i}"] = bank
            b[p + f"tapPackedPair_{i}"] = pair
            line = (tap_addr >> 5) << 5
            valid = 0
            pairs = 0
            for byte in range(4):
                v, bk, pr = lay.location(line + byte)
                valid |= v << byte
                pairs |= pr << byte
                b[p + f"tapStartupDecode_{i}_bank_{byte}"] = bk
            b[p + f"tapStartupDecode_{i}_valid"] = valid
            b[p + f"tapStartupDecode_{i}_pair"] = pairs
        b[p + "bilinear"] = int(bilinear)
        b[p + "passthrough_format"] = rng.getrandbits(4)
        b[p + "passthrough_bilinear"] = rng.getrandbits(1)
        b[p + "passthrough_nccTableSelect"] = rng.getrandbits(1)
        b[p + "passthrough_ds"] = rng.getrandbits(4)
        b[p + "passthrough_dt"] = rng.getrandbits(4)
        return b

    def observe(self, obs):
        if self._queue:
            return
        rng = self._rng
        layout_idx = rng.randrange(len(self._layouts))
        lod, s, t = self._pick(layout_idx)
        bilinear = rng.random() < self.BILINEAR
        self._kinds["bilinear" if bilinear else "point"] += 1
        self.enqueue(self._build(self._layouts[layout_idx], lod, s, t, bilinear))

    def stats(self):
        return {**super().stats(), **{f"req_{k}": v for k, v in self._kinds.items()}}


class CacheTraffic(Responder):
    """The row's responder: fills + requests + a rare invalidate pulse."""

    INVALIDATE_MEAN = 3000

    def __init__(self, *, rng: random.Random, spec: dict) -> None:
        inputs = {p["spinal"]: p for p in spec["inputs"]}
        rsp_payload = [n for n, p in inputs.items()
                       if p["stream"] == _RSP and p["role"] == "payload"]
        # Every io_sampleRequest_payload_* leaf.  Not `role == "payload"`:
        # ports.py takes the handshake keyword closest to the end, so the four
        # `tapStartupDecode_N_valid` fields classify as the `valid` of a
        # one-port flow each, and they have to be claimed by name.
        req_payload = [n for n in inputs if n.startswith(f"{_REQ}_payload_")]
        widths = {n: p["width"] for n, p in inputs.items()}
        self._rng = rng
        self._fills = _LineFills(rng=random.Random(rng.getrandbits(64)), payload=rsp_payload)
        self._reqs = _Requests(rng=random.Random(rng.getrandbits(64)), payload=req_payload,
                               widths=widths)
        self._has_invalidate = _INVALIDATE in inputs
        self._pulses = 0
        self._fetches = 0

    def claims(self):
        out = self._fills.claims() | self._reqs.claims()
        if self._has_invalidate:
            out.add(_INVALIDATE)
        return out

    def reset(self):
        self._fills.reset()
        self._reqs.reset()
        self._pulses = 0
        self._fetches = 0

    def cycle(self, obs, drive):
        # `io.sampleFetch.valid` is the renamed `dbgDataValid` output, so the
        # snapshot does not group it as a stream; count fetches post-edge
        # instead (an estimate, for the report only).
        if "dbgDataValid" in obs and obs["dbgDataValid"] and obs["io_sampleFetch_ready"]:
            self._fetches += 1
        self._fills.cycle(obs, drive)
        self._reqs.cycle(obs, drive)
        if self._has_invalidate:
            pulse = self._rng.random() < 1.0 / self.INVALIDATE_MEAN
            drive[_INVALIDATE] = int(pulse)
            self._pulses += int(pulse)

    def stats(self):
        return {
            **{f"rsp_{k}": v for k, v in self._fills.stats().items()},
            **{f"req_{k}": v for k, v in self._reqs.stats().items()},
            "invalidate_pulses": self._pulses,
            "fetch_cycles": self._fetches,
        }


def cache_traffic(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder`` factory for the ``TmuTextureCache`` row."""
    return CacheTraffic(rng=rng, spec=spec)
