"""Well-formed texture-memory responses and texture-shaped input traffic for the ``Tmu`` diff row.

Two things the generic driver cannot do for ``Tmu``:

:class:`TexMemSlave`
    claims ``io_texRead_rsp`` and answers every ``io_texRead_cmd`` beat in
    order -- ``((address & 3) + length) // 4 + 1`` data beats from a lazily
    randomised word image, ``source`` echoed, ``last`` on the final beat --
    with random idle cycles between beats.  ``TmuTextureCache`` only accepts
    a response while a fill is outstanding, so a random held ``rsp`` beat
    parks the stream for the rest of the run; with this slave every fill
    completes and the pipeline keeps flowing.  Same model as
    ``plane_reader_stimulus.MemSlave``, on the TMU's port names.

:class:`TmuTraffic`
    claims ``io_input`` and drives it with pixels that look like a texture
    being rasterised: a handful of per-seed texture set-ups (packed layout
    tables from :func:`avoodoo.texture.tex_layout_tables.compute`, format,
    clamp/wrap, filter, perspective, LOD register), each held for a run of
    pixels while the S/T coordinates take a random walk across the texture
    and ``1/W`` stays in a sensible range.  That is what makes the cache
    *hit*: fully random 48-bit S/T against random tables never revisit a
    32-byte line, so the random driver alone would only ever compare the
    miss/fill path.  One pixel in twenty is "wild" (S, T, 1/W, gradients,
    LOD register, mode bits and ``textureEnable`` random, tables still
    consistent -- see :meth:`TmuTraffic._wild_beat`), so the bypass path,
    clamp-to-zero, out-of-range LODs and the wrapping arithmetic get their
    coverage too.  No pixel is *fully* random (``random_fraction`` defaults
    to 0): random packed tables put the texel address outside every LOD and
    the cache then chases continue-fills for thousands of cycles on both
    sides, so even one such pixel in two hundred parks most of a 1e6-cycle
    run (see :func:`mem_only`).  ``io_invalidate`` is claimed as well and
    pulsed rarely instead of on half the cycles.

Both are pure functions of the seed, so a failure replays.  ``stats()``
reports what was served so a clean run can be checked for having actually
exercised the paths (``tests/diff/test_diff_tmu.py``).
"""

from __future__ import annotations

import random

from avoodoo.texture.tex_layout_tables import TexConfig, compute as compute_tables
from avoodoo.texture.tmu_types import TextureFormat
from tools.diff.responders import Responder, StreamResponder

__all__ = ["TexMemSlave", "TmuTraffic", "Composite", "mem_only", "tmu_traffic", "tmu_wild"]

_CMD = "io_texRead_cmd"
_RSP = "io_texRead_rsp"
_IN = "io_input"


def _payload_ports(spec: dict, stream: str) -> list[str]:
    ports = [p["spinal"] for p in spec["inputs"]
             if p["stream"] == stream and p["role"] == "payload"]
    if not ports:
        raise ValueError(f"{stream} has no payload inputs in {spec['module']}")
    return ports


def _widths(spec: dict) -> dict[str, int]:
    return {p["spinal"]: p["width"] for p in spec["inputs"]}


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


class TexMemSlave(StreamResponder):
    """In-order read slave on ``io_texRead_rsp`` answering ``io_texRead_cmd`` beats."""

    def __init__(self, *, rng: random.Random, spec: dict, valid_density: float = 0.8):
        payload = _payload_ports(spec, _RSP)
        super().__init__(stream=_RSP, payload=payload, rng=rng, valid_density=valid_density)
        self._has = set(payload)
        self._words: dict[int, int] = {}
        self._cmds = 0
        self._beats = 0

    def reset(self):
        super().reset()
        self._words.clear()
        self._cmds = self._beats = 0

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
        assert beat.get(f"{_CMD}_payload_fragment_opcode", 0) == 0, "the TMU only reads"
        self._cmds += 1
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
        return {**super().stats(), "cmds": self._cmds, "beats_queued_total": self._beats}


class _Setup:
    """One texture configuration: registers + the packed tables for it."""

    def __init__(self, rng: random.Random):
        self.tformat = rng.choice(TextureFormat.ALL + TextureFormat.UNASSIGNED)
        self.is16 = self.tformat >= TextureFormat.ARGB8332
        self.aspect = rng.choice([0, 0, 1, 2, 3])
        self.s_is_wider = rng.random() < 0.5
        # 8-byte units, kept small so the tables stay far from the 22-bit wrap
        # (a random pixel beat covers the wrapping case).
        self.tex_base_addr = rng.randrange(0, 1 << 14)
        self.multibase = rng.random() < 0.2
        self.tex_base_addr1 = rng.randrange(0, 1 << 14)
        self.tex_base_addr2 = rng.randrange(0, 1 << 14)
        self.tex_base_addr38 = rng.randrange(0, 1 << 14)
        cfg = TexConfig(tex_base_addr=self.tex_base_addr,
                        tex_base_addr1=self.tex_base_addr1,
                        tex_base_addr2=self.tex_base_addr2,
                        tex_base_addr38=self.tex_base_addr38,
                        tformat=self.tformat, tlod_aspect=self.aspect,
                        tlod_s_is_wider=self.s_is_wider, tlod_multibase=self.multibase)
        self.tables = compute_tables(cfg)
        # textureMode: [0] persp [1] minfilter [2] magfilter [3] clampW
        # [4] loddither [5] nccselect [6] clampS [7] clampT [11:8] format
        self.perspective = rng.random() < 0.6
        bilinear = rng.random() < 0.5
        self.texture_mode = (int(self.perspective)
                             | (int(bilinear) << 1) | (int(bilinear and rng.random() < 0.7) << 2)
                             | (int(rng.random() < 0.3) << 3)
                             | (rng.getrandbits(1) << 4) | (rng.getrandbits(1) << 5)
                             | (int(rng.random() < 0.5) << 6) | (int(rng.random() < 0.5) << 7)
                             | (self.tformat << 8))
        # lodmin / lodmax / lodbias are 4.2 (quarter levels): 32 is LOD 8.0.
        lodmin = rng.choice([0, 0, 0, rng.randrange(0, 36)])
        lodmax = rng.choice([32, 32, rng.randrange(0, 64)])
        lodbias = rng.choice([0, 0, rng.randrange(0, 64)])
        self.t_lod = (lodmin | (lodmax << 6) | (lodbias << 12)
                      | (rng.getrandbits(1) << 18) | (rng.getrandbits(1) << 19)
                      | (int(self.s_is_wider) << 20) | (self.aspect << 21)
                      | (rng.getrandbits(1) << 23) | (int(self.multibase) << 24)
                      | (rng.getrandbits(2) << 25))
        self.texture_enable = rng.random() < 0.9
        # Where the walk lives: texel units at LOD 0.
        self.s = rng.uniform(-8.0, 264.0)
        self.t = rng.uniform(-8.0, 264.0)
        self.w = rng.choice([1.0, rng.uniform(0.05, 3.0), rng.uniform(0.5, 1.5)])
        self.dsdx = rng.uniform(-3.0, 3.0) if rng.random() < 0.3 else rng.uniform(-1.0, 1.0)
        self.dtdx = rng.uniform(-1.0, 1.0) if rng.random() < 0.3 else 0.0
        self.dsdy = rng.uniform(-1.0, 1.0) if rng.random() < 0.3 else 0.0
        self.dtdy = rng.uniform(-3.0, 3.0) if rng.random() < 0.3 else rng.uniform(-1.0, 1.0)


def _raw(value: float, frac_bits: int, width: int) -> int:
    return int(round(value * (1 << frac_bits))) & ((1 << width) - 1)


class TmuTraffic(StreamResponder):
    """Texture-shaped ``io_input`` beats, plus rare ``io_invalidate`` pulses."""

    def __init__(self, *, rng: random.Random, spec: dict, valid_density: float = 0.7,
                 setups: int = 4, random_fraction: float = 0.0,
                 wild_fraction: float = 0.05, invalidate_rate: float = 0.0005):
        super().__init__(stream=_IN, payload=_payload_ports(spec, _IN), rng=rng,
                         valid_density=valid_density)
        self._widths = _widths(spec)
        self._setup_count = setups
        self._random_fraction = random_fraction
        self._wild_fraction = wild_fraction
        self._invalidate_rate = invalidate_rate
        self._setups: list[_Setup] = []
        self._current = 0
        self._run_left = 0
        self._kinds = {"walk": 0, "wild": 0, "random": 0, "invalidates": 0}

    def claims(self):
        return super().claims() | {"io_invalidate"}

    def reset(self):
        super().reset()
        self._setups = [_Setup(self._rng) for _ in range(self._setup_count)]
        self._current = 0
        self._run_left = 0
        for k in self._kinds:
            self._kinds[k] = 0

    # -- beat builders ---------------------------------------------------

    def _random_beat(self) -> dict:
        return {p: self._rng.getrandbits(self._widths[p]) for p in self._payload}

    def _wild_beat(self) -> dict:
        """Every arithmetic input random, the packed tables consistent.

        S, T, 1/W and the four gradients are raw random bit patterns (so the
        front end sees both signs, zero, the 66-bit product's full range and
        every ``clz`` outcome), and the LOD register's min/max/bias, the
        clamp/filter/perspective/clampW bits and ``textureEnable`` are random
        too.  What stays consistent is what the *cache* needs to resolve a
        tap: the format, aspect, ``sIsWider`` and ``multibase`` bits agree with
        the set-up's ``texTables``, so every texel address the front end can
        produce lies inside a LOD's ``[texBase, texEnd)``.  A request whose
        address is outside every LOD (which a fully random beat produces)
        makes the cache chase continue-fills for thousands of cycles.
        """
        rng = self._rng
        st = self._setups[self._current]
        beat = self._walk_beat()
        for name in ("s", "t", "w", "dSdX", "dTdX", "dSdY", "dTdY"):
            port = f"{_IN}_payload_{name}"
            beat[port] = rng.getrandbits(self._widths[port])
        if rng.random() < 0.3:
            # Small-magnitude 1/W and gradients: the interesting msbPos range.
            beat[f"{_IN}_payload_w"] = rng.getrandbits(rng.randint(1, 40))
            for name in ("dSdX", "dTdX", "dSdY", "dTdY"):
                beat[f"{_IN}_payload_{name}"] = _raw(rng.uniform(-64, 64), 18, 32)
        keep = st.texture_mode & 0xF00                       # format
        beat[f"{_IN}_payload_config_textureMode"] = keep | rng.getrandbits(8)
        keep = st.t_lod & ((1 << 20) | (3 << 21) | (1 << 24))   # sIsWider, aspect, multibase
        beat[f"{_IN}_payload_config_tLOD"] = keep | (rng.getrandbits(27) & ~keep
                                                    & ~((1 << 20) | (3 << 21) | (1 << 24)))
        beat[f"{_IN}_payload_config_textureEnable"] = int(rng.random() < 0.8)
        return beat

    def _walk_beat(self) -> dict:
        rng = self._rng
        st = self._setups[self._current]
        # A short random walk; every ~30 pixels jump somewhere else on the
        # texture so wrap/clamp and several mip lines get visited.
        if rng.random() < 0.03:
            st.s = rng.uniform(-40.0, 300.0)
            st.t = rng.uniform(-40.0, 300.0)
        else:
            st.s += rng.uniform(-1.5, 1.5)
            st.t += rng.uniform(-1.5, 1.5)
        if rng.random() < 0.05:
            st.w = rng.choice([1.0, rng.uniform(0.05, 3.0)])
        w = st.w
        if rng.random() < 0.01:
            w = -w                       # 1/W < 0: clampToZero / validOow low
        beat = {}
        beat[f"{_IN}_payload_s"] = _raw(st.s * w if st.perspective else st.s, 18, 48)
        beat[f"{_IN}_payload_t"] = _raw(st.t * w if st.perspective else st.t, 18, 48)
        beat[f"{_IN}_payload_w"] = _raw(w, 30, 48)
        beat[f"{_IN}_payload_cOther_r"] = rng.getrandbits(8)
        beat[f"{_IN}_payload_cOther_g"] = rng.getrandbits(8)
        beat[f"{_IN}_payload_cOther_b"] = rng.getrandbits(8)
        beat[f"{_IN}_payload_aOther"] = rng.getrandbits(8)
        beat[f"{_IN}_payload_config_textureMode"] = st.texture_mode
        beat[f"{_IN}_payload_config_texBaseAddr"] = st.tex_base_addr
        beat[f"{_IN}_payload_config_texBaseAddr1"] = st.tex_base_addr1
        beat[f"{_IN}_payload_config_texBaseAddr2"] = st.tex_base_addr2
        beat[f"{_IN}_payload_config_texBaseAddr38"] = st.tex_base_addr38
        beat[f"{_IN}_payload_config_tLOD"] = st.t_lod
        beat[f"{_IN}_payload_config_textureEnable"] = int(st.texture_enable)
        for lod in range(9):
            beat[f"{_IN}_payload_config_texTables_texBase_{lod}"] = st.tables.tex_base[lod]
            beat[f"{_IN}_payload_config_texTables_texEnd_{lod}"] = st.tables.tex_end[lod]
            beat[f"{_IN}_payload_config_texTables_texShift_{lod}"] = st.tables.tex_shift[lod]
        beat[f"{_IN}_payload_dSdX"] = _raw(st.dsdx, 18, 32)
        beat[f"{_IN}_payload_dTdX"] = _raw(st.dtdx, 18, 32)
        beat[f"{_IN}_payload_dSdY"] = _raw(st.dsdy, 18, 32)
        beat[f"{_IN}_payload_dTdY"] = _raw(st.dtdy, 18, 32)
        # Trace ports exist only with c.trace.enabled; fill anything left.
        for p in self._payload:
            beat.setdefault(p, rng.getrandbits(self._widths[p]))
        return beat

    # -- StreamResponder -------------------------------------------------

    def observe(self, obs):
        if self._queue:
            return
        rng = self._rng
        if self._run_left == 0:
            self._current = rng.randrange(len(self._setups))
            self._run_left = rng.randint(8, 200)
        self._run_left -= 1
        r = rng.random()
        if r < self._random_fraction:
            self._kinds["random"] += 1
            self.enqueue(self._random_beat())
        elif r < self._random_fraction + self._wild_fraction:
            self._kinds["wild"] += 1
            self.enqueue(self._wild_beat())
        else:
            self._kinds["walk"] += 1
            self.enqueue(self._walk_beat())

    def cycle(self, obs, drive):
        super().cycle(obs, drive)
        inv = self._rng.random() < self._invalidate_rate
        drive["io_invalidate"] = int(inv)
        self._kinds["invalidates"] += int(inv)

    def stats(self):
        return {**super().stats(), **{f"kind_{k}": v for k, v in self._kinds.items()}}


class DutCounters(Responder):
    """Claims nothing; counts the pixels that went in and came out.

    ``io_input`` / ``io_output`` fires are read from the Spinal side's
    handshake snapshot, so a clean run can be checked for having pushed a
    few thousand pixels through rather than sitting stalled.
    """

    def __init__(self):
        self._obs = None
        self._in = 0
        self._out = 0

    def claims(self):
        return set()

    def reset(self):
        self._in = self._out = 0

    def cycle(self, obs, drive):
        self._obs = obs
        self._in += int(obs.fired(_IN))
        self._out += int(obs.fired("io_output"))

    def stats(self):
        if self._obs is None:
            return {}
        return {"pixels_in": self._in, "pixels_out": self._out,
                "busy_at_end": self._obs["io_busy"]}


def mem_only(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder``: memory side only; ``io_input`` stays random.

    Kept for reference: a fully random ``io_input`` parks the cache on the
    first request whose address is outside every LOD (34 pixels per 1e6
    cycles measured), so :func:`tmu_wild` is what the ``TmuMemOnly`` row runs.
    """
    return Composite(TexMemSlave(rng=rng, spec=spec), DutCounters())


def tmu_wild(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder``: memory side + every arithmetic input random
    (``TmuTraffic`` with ``wild_fraction = 1``), tables kept consistent."""
    return Composite(TexMemSlave(rng=random.Random(rng.random()), spec=spec),
                     TmuTraffic(rng=random.Random(rng.random()), spec=spec,
                                random_fraction=0.0, wild_fraction=1.0),
                     DutCounters())


def tmu_traffic(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder``: memory side + texture-shaped input traffic."""
    return Composite(TexMemSlave(rng=random.Random(rng.random()), spec=spec),
                     TmuTraffic(rng=random.Random(rng.random()), spec=spec),
                     DutCounters())
