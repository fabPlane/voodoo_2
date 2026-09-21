"""Real triangles and in-order memory responses for the ``PixelPipeline`` diff row.

The generic random driver would leave three holes in a ``PixelPipeline``
run, so this row claims:

``io_triangleCmd`` / ``io_ftriangleCmd`` (:class:`TriangleTraffic`)
    One triangle at a time, on one of the two streams (they feed a
    ``StreamArbiterFactory.assumeOhInput``, so the pair is kept one-hot).
    Each is a small screen-space triangle (a few to ~50 pixels a side, the
    odd larger or off-screen one) with colour/alpha/depth/W start values and
    gradients in range, and a texture set-up borrowed from
    ``tmu_stimulus._Setup`` -- consistent packed layout tables, S/T in texel
    units, sane 1/W.  That last part is the reason this exists at all: with
    a *random* ``texTables`` word the TMU's texel address falls outside
    every LOD and the cache chases continue-fills for thousands of cycles,
    which is how ``tests/diff/tmu_stimulus.py`` found that a fully random
    ``Tmu.Input`` parks a run.  Every other field of the command (the
    ``fbzColorPath`` / ``fogMode`` / ``alphaMode`` config, clip values,
    ``signBit``, the trace ids) is random per triangle.

``io_texRead_rsp`` (``tmu_stimulus.TexMemSlave``)
    Answers every ``io_texRead_cmd`` burst in order; the port names inside
    ``PixelPipeline`` are the TMU's own, so the class is reused as is.

``io_colorReadRsp`` / ``io_auxReadRsp`` (``framebuffer_access_stimulus.PlaneMemory``)
    One 16-bit lane per ``io_colorReadReq`` / ``io_auxReadReq`` beat, in
    order, with random latency -- so each response is the one its pixel
    asked for, as the plane buffers would deliver it.

Everything else stays with the random generator: ``io_controls`` (re-randomised
every cycle, which is harsher than any register bank), ``io_lfbBus``,
``io_fastfillCmd``, ``io_paletteWrite``, ``io_lfbReadBus_rsp``,
``io_externalBusy``, ``io_fbStatus`` / ``io_fbStats``, ``io_tmuInvalidate``,
``io_pciFifoEmpty`` and the readies of every output stream.

:class:`PipelineCounters` claims nothing and counts what crossed the ports
(triangles accepted, texture bursts, plane reads, colour/aux writes,
prefetches) plus the two statistics counters at the end of the run, so a
clean run can be checked for having actually drawn something
(``tests/diff/test_diff_pixel_pipeline.py``).
"""

from __future__ import annotations

import random

from avoodoo.config import Config
from tools.diff.responders import Responder

from tests.diff.framebuffer_access_stimulus import Composite, PlaneMemory
from tests.diff.tmu_stimulus import TexMemSlave, _Setup, _raw

__all__ = ["TriangleTraffic", "PipelineCounters", "pipeline_traffic"]

_TRI = "io_triangleCmd"
_FTRI = "io_ftriangleCmd"


def _payload_ports(spec: dict, stream: str) -> list[str]:
    ports = [p["spinal"] for p in spec["inputs"]
             if p["stream"] == stream and p["role"] == "payload"]
    if not ports:
        raise ValueError(f"{stream} has no payload inputs in {spec['module']}")
    return ports


def _widths(spec: dict) -> dict[str, int]:
    return {p["spinal"]: p["width"] for p in spec["inputs"]}


class TriangleTraffic(Responder):
    """Triangles on ``io_triangleCmd`` / ``io_ftriangleCmd``, one at a time.

    Owns the Spinal hold rule for both streams: the presented beat (stream +
    payload) is kept until the pre-edge snapshot says it fired; the other
    stream idles with ``valid`` low and a zero payload.
    """

    def __init__(self, *, rng: random.Random, spec: dict, valid_density: float = 0.6,
                 setups: int = 4, ftriangle_fraction: float = 0.3):
        self._rng = rng
        self._density = valid_density
        self._setup_count = setups
        self._ftri = ftriangle_fraction
        self._widths = _widths(spec)
        # payload suffix -> width, common to both streams
        tri_ports = _payload_ports(spec, _TRI)
        self._suffixes = [p[len(_TRI):] for p in tri_ports]
        for s in self._suffixes:
            if _FTRI + s not in self._widths:
                raise ValueError(f"{_FTRI}{s} missing: the two command streams differ")
        self._c = Config.voodoo1()
        self._setups: list[_Setup] = []
        self._presented: tuple[str, dict] | None = None
        self._fired = {_TRI: 0, _FTRI: 0}
        self._kinds = {"small": 0, "large": 0, "offscreen": 0}

    def claims(self):
        out = set()
        for s in (_TRI, _FTRI):
            out.add(f"{s}_valid")
            out.update(f"{s}{suffix}" for suffix in self._suffixes)
        return out

    def reset(self):
        self._setups = [_Setup(self._rng) for _ in range(self._setup_count)]
        self._presented = None
        for k in self._fired:
            self._fired[k] = 0
        for k in self._kinds:
            self._kinds[k] = 0

    # -- one triangle ----------------------------------------------------

    def _vertices(self):
        rng = self._rng
        r = rng.random()
        if r < 0.03:
            self._kinds["offscreen"] += 1
            cx, cy = rng.uniform(-100, 800), rng.uniform(-100, 600)
            size = rng.uniform(1, 40)
        elif r < 0.10:
            self._kinds["large"] += 1
            cx, cy = rng.uniform(40, 600), rng.uniform(40, 440)
            size = rng.uniform(40, 120)
        else:
            self._kinds["small"] += 1
            cx, cy = rng.uniform(0, 640), rng.uniform(0, 480)
            size = rng.uniform(1, 48)
        verts = []
        for _ in range(3):
            verts.append((cx + rng.uniform(-size, size), cy + rng.uniform(-size, size)))
        return verts

    def _beat(self) -> dict:
        """One ``TriangleSetup.Input`` as ``{suffix: value}``."""
        rng = self._rng
        c = self._c
        st = self._setups[rng.randrange(len(self._setups))]
        verts = self._vertices()
        b: dict = {}
        vf = c.vertex_format
        for v in range(3):
            for a in range(2):
                b[f"_payload_triWithSign_tri_{v}_{a}"] = _raw(verts[v][a], vf.fraction, vf.width)
        b["_payload_triWithSign_signBit"] = rng.getrandbits(1)

        # Colour / alpha: 0..255 with gradients of a few units per pixel;
        # depth 0..65535; W around the set-up's 1/W.  SQ(24,12) colours,
        # SQ(32,12) depth, SQ(48,30) W, SQ(48,18) S/T, SQ(46,30) the hi ones.
        def grad(name, start, dx, dy, frac, width):
            b[f"_payload_grads_{name}_start"] = _raw(start, frac, width)
            b[f"_payload_grads_{name}_d_0"] = _raw(dx, frac, width)
            b[f"_payload_grads_{name}_d_1"] = _raw(dy, frac, width)

        for name in ("redGrad", "greenGrad", "blueGrad", "alphaGrad"):
            grad(name, rng.uniform(-20, 275), rng.uniform(-6, 6), rng.uniform(-6, 6), 12, 24)
        grad("depthGrad", rng.uniform(-500, 66000), rng.uniform(-300, 300),
             rng.uniform(-300, 300), 12, 32)
        w = st.w if rng.random() < 0.9 else rng.uniform(-3.0, 3.0)
        grad("wGrad", w, rng.uniform(-0.01, 0.01), rng.uniform(-0.01, 0.01), 30, 48)
        s0 = st.s * w if st.perspective else st.s
        t0 = st.t * w if st.perspective else st.t
        dsdx, dtdx, dsdy, dtdy = st.dsdx, st.dtdx, st.dsdy, st.dtdy
        grad("sGrad", s0, dsdx, dsdy, 18, 48)
        grad("tGrad", t0, dtdx, dtdy, 18, 48)
        b["_payload_texHi_sStart"] = _raw(s0, 30, 46)
        b["_payload_texHi_tStart"] = _raw(t0, 30, 46)
        b["_payload_texHi_dSdX"] = _raw(dsdx, 30, 46)
        b["_payload_texHi_dTdX"] = _raw(dtdx, 30, 46)
        b["_payload_texHi_dSdY"] = _raw(dsdy, 30, 46)
        b["_payload_texHi_dTdY"] = _raw(dtdy, 30, 46)
        alpha = rng.uniform(-20, 275)
        b["_payload_hiAlpha_start"] = _raw(alpha, 30, 46)
        b["_payload_hiAlpha_dAdX"] = _raw(rng.uniform(-6, 6), 30, 46)
        b["_payload_hiAlpha_dAdY"] = _raw(rng.uniform(-6, 6), 30, 46)

        # TMU per-triangle registers: the set-up's, so the cache resolves.
        b["_payload_config_tmuTextureMode"] = st.texture_mode
        b["_payload_config_tmuTexBaseAddr"] = st.tex_base_addr
        b["_payload_config_tmuTexBaseAddr1"] = st.tex_base_addr1
        b["_payload_config_tmuTexBaseAddr2"] = st.tex_base_addr2
        b["_payload_config_tmuTexBaseAddr38"] = st.tex_base_addr38
        b["_payload_config_tmuTLOD"] = st.t_lod
        b["_payload_config_tmudSdX"] = _raw(dsdx, 18, 32)
        b["_payload_config_tmudTdX"] = _raw(dtdx, 18, 32)
        b["_payload_config_tmudSdY"] = _raw(dsdy, 18, 32)
        b["_payload_config_tmudTdY"] = _raw(dtdy, 18, 32)
        for lod in range(9):
            b[f"_payload_config_texTables_texBase_{lod}"] = st.tables.tex_base[lod]
            b[f"_payload_config_texTables_texEnd_{lod}"] = st.tables.tex_end[lod]
            b[f"_payload_config_texTables_texShift_{lod}"] = st.tables.tex_shift[lod]
        b["_payload_config_fbzColorPath_textureEnable"] = int(st.texture_enable)

        # Everything else (fbzColorPath, fogMode, alphaMode, clip, trace ids):
        # random per triangle.
        for suffix in self._suffixes:
            b.setdefault(suffix, rng.getrandbits(self._widths[_TRI + suffix]))
        return b

    # -- Responder -------------------------------------------------------

    def cycle(self, obs, drive):
        if self._presented is not None and obs.fired(self._presented[0]):
            self._fired[self._presented[0]] += 1
            self._presented = None
        if self._presented is None and self._rng.random() < self._density:
            stream = _FTRI if self._rng.random() < self._ftri else _TRI
            self._presented = (stream, self._beat())
        for s in (_TRI, _FTRI):
            on = self._presented is not None and self._presented[0] == s
            drive[f"{s}_valid"] = int(on)
            beat = self._presented[1] if on else None
            for suffix in self._suffixes:
                drive[f"{s}{suffix}"] = beat[suffix] if on else 0

    def stats(self):
        return {"triangles": self._fired[_TRI], "ftriangles": self._fired[_FTRI],
                **{f"kind_{k}": v for k, v in self._kinds.items()}}


class PipelineCounters(Responder):
    """Claims nothing; counts beats on the ports and reads the stats at the end."""

    _FIRES = {
        "tex_cmds": "io_texRead_cmd",
        "color_reads": "io_colorReadReq",
        "aux_reads": "io_auxReadReq",
        "color_writes": "io_colorWrite",
        "aux_writes": "io_auxWrite",
        "prefetch_color": "io_prefetchColor",
        "prefetch_aux": "io_prefetchAux",
        "lfb_cmds": "io_lfbBus_cmd",
        "fastfills": "io_fastfillCmd",
    }

    def __init__(self):
        self._obs = None
        self._n = {k: 0 for k in self._FIRES}
        self._busy_cycles = 0
        self._cycles = 0

    def claims(self):
        return set()

    def reset(self):
        self._n = {k: 0 for k in self._FIRES}
        self._busy_cycles = 0
        self._cycles = 0

    def cycle(self, obs, drive):
        self._obs = obs
        for k, stream in self._FIRES.items():
            self._n[k] += int(obs.fired(stream))
        self._cycles += 1
        self._busy_cycles += int(obs["io_debug_pipelineBusy"])

    def stats(self):
        if self._obs is None:
            return {}
        return {**self._n, "cycles": self._cycles, "busy_cycles": self._busy_cycles,
                "stats_pixels_in": self._obs["io_stats_pixelsIn"],
                "stats_pixels_out": self._obs["io_stats_pixelsOut"],
                "stats_z_func_fail": self._obs["io_stats_zFuncFail"],
                "stats_a_func_fail": self._obs["io_stats_aFuncFail"],
                "stats_chroma_fail": self._obs["io_stats_chromaFail"]}


def pipeline_traffic(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder`` for the ``PixelPipeline`` row."""
    return Composite(
        TriangleTraffic(rng=random.Random(rng.random()), spec=spec),
        TexMemSlave(rng=random.Random(rng.random()), spec=spec),
        PlaneMemory(req="io_colorReadReq", rsp="io_colorReadRsp", label="Color",
                    rng=random.Random(rng.random()), spec=spec),
        PlaneMemory(req="io_auxReadReq", rsp="io_auxReadRsp", label="Aux",
                    rng=random.Random(rng.random()), spec=spec),
        PipelineCounters(),
    )
