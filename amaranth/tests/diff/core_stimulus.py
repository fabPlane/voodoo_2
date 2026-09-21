"""Stimulus for the ``Core`` diff rows: memory slaves, the pixel clock, and a CPU.

``Core`` has one CPU bus in and six memory buses out, and everything
interesting happens *between* them: a register write through the frontdoor,
the remapper and the PCI FIFO; a triangle command through setup, raster,
TMU, colour combine, fog, framebuffer access, dither and write into the
plane buffers and out on ``fbColorWriteMem``; a texture download through the
drain into ``texMem``; the scanout prefetching lines through
``fbColorReadMem``.  The bare generator reaches almost none of that:

* a random ``io_*Mem_rsp`` beat that no outstanding command matches is
  refused for ever (the same parking as ``FramebufferMemSubsystem``), so the
  six buses get an in-order :class:`MemSlave` each (echoing ``source``, which
  both arbiter layers demux on);
* ``io_hdmi_clock`` must actually toggle for the HDMI half of the scanout to
  exist -- :class:`HdmiClock` from the scanout stimulus, renamed to
  ``Core``'s port spelling;
* a random 24-bit ``io_cpuBus`` address is a texture write half the time and
  an undecoded register a quarter of the time; the 158 decoded registers,
  the five commands and the float aliases are hit at a few percent, and a
  triangle from random vertex registers is mostly degenerate or the size of
  the screen.  :class:`CpuTraffic` replaces it with a host: a sane initial
  scene (colour writes on, clipping on with a small window, RGB565 LFB, the
  two colour buffers 600 KiB apart), then a random mix of register writes
  (mostly to decoded addresses, the clip windows and ``fbiInit2`` always
  kept small/sane), real small triangles through both ``triangleCMD`` (fixed
  vertex registers) and ``ftriangleCMD`` (float aliases), ``fastfillCMD``
  inside the clip window, ``swapbufferCMD`` / ``nopCMD``, LFB writes and
  reads, texture writes and reads, and register reads.  Single-beat
  commands, as ``sim_write`` / ``sim_read`` issue them.

``CoreRandom`` keeps the generator on ``io_cpuBus`` (memory slaves and pixel
clock still attached) so the shape of that run stays reproducible.

``statusInputs`` (so ``vRetrace`` flips at random and the swap machine
progresses), ``flushFbCaches``, ``io_cpuBus_rsp_ready`` and every memory bus
``cmd.ready`` stay with the generator.  ``fbBaseAddr`` is **pinned to 0**
(the registry row; ``CoreSim`` ties it to 0 too): the scanout tracks
``regs.frontBase`` live while it prefills and only latches it per frame once
``scanoutRun`` is set, and its reader is the in-order one (``allowDirectMiss
= false``), so a base that moves between a line's prefetch and its reads --
the generator's per-cycle randomness, or even a single jump from the
harness's all-zero reset value to a per-seed constant, since the first
prefetch fires the cycle after reset -- strands the reader for the rest of
the run, on both sides.  Swaps and buffer-offset changes move the front base
the same way, so the CPU keeps them out of the first
:attr:`CpuTraffic.PREFILL_CYCLES` cycles.  The ``fbBaseAddr + ...`` adders
are still exercised through ``fbiInit2``'s buffer offset.

Everything is a pure function of the seed.
"""

from __future__ import annotations

import random
import struct

from avoodoo.frontend import regdef
from tests.diff.framebuffer_mem_subsystem_stimulus import Composite, MemSlave
from tests.diff.hdmi_scanout_stimulus import HdmiClock
from tools.diff.responders import Responder, StreamResponder

__all__ = ["CpuTraffic", "HdmiClockPort", "CoreCounters", "core_traffic", "core_random"]

_CMD = "io_cpuBus_cmd"
_RSP = "io_cpuBus_rsp"

REG_BASE = 0x000000
LFB_BASE = 0x400000
TEX_BASE = 0x800000

# Register addresses (regdef spellings).
VERTEX_REGS = (0x008, 0x00C, 0x010, 0x014, 0x018, 0x01C)
START_RGBA = (0x020, 0x024, 0x028, 0x030)           # startR/G/B/A, 12.12
START_Z, START_S, START_T, START_W = 0x02C, 0x034, 0x038, 0x03C
DX_RGBA = (0x040, 0x044, 0x048, 0x050)
DX_Z, DX_S, DX_T, DX_W = 0x04C, 0x054, 0x058, 0x05C
DY_RGBA = (0x060, 0x064, 0x068, 0x070)
DY_Z, DY_S, DY_T, DY_W = 0x06C, 0x074, 0x078, 0x07C
TRIANGLE_CMD, FTRIANGLE_CMD = 0x080, 0x100
FBZ_COLOR_PATH, FOG_MODE, ALPHA_MODE, FBZ_MODE, LFB_MODE = 0x104, 0x108, 0x10C, 0x110, 0x114
CLIP_LR, CLIP_LH = 0x118, 0x11C
NOP_CMD, FASTFILL_CMD, SWAPBUFFER_CMD = 0x120, 0x124, 0x128
FBI_INIT1, FBI_INIT2 = 0x214, 0x218
TEXTURE_MODE, T_LOD, TEX_BASE_ADDR = 0x300, 0x304, 0x310

#: registers whose random contents would turn one command into a
#: hundred-thousand-cycle job (a fastfill of the whole 1024 x 1024 clip space,
#: colour buffers on top of each other) or park the scanout (a stride change
#: mid-line, see `_next`); they get "sane" values instead, fbiInit1 only once.
_SANE = {CLIP_LR, CLIP_LH, FBI_INIT2, FBI_INIT1}


def _payload_ports(spec: dict, stream: str) -> list[str]:
    ports = [p["spinal"] for p in spec["inputs"]
             if p["stream"] == stream and p["role"] == "payload"]
    if not ports:
        raise ValueError(f"{stream} has no payload inputs in {spec['module']}")
    return ports


def _fix(value: float, frac: int, width: int) -> int:
    """Two's-complement fixed point, ``width`` bits, ``frac`` fraction bits."""
    return int(round(value * (1 << frac))) & ((1 << width) - 1)


def _f32(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


class HdmiClockPort(HdmiClock):
    """:class:`HdmiClock` on ``Core``'s ``io_hdmi_clock`` / ``io_hdmi_reset``."""

    label = "hdmi"

    def claims(self):
        return {"io_hdmi_clock", "io_hdmi_reset"}

    def cycle(self, obs, drive):
        tmp: dict = {}
        super().cycle(obs, tmp)
        drive["io_hdmi_clock"] = tmp["io_hdmiClock"]
        drive["io_hdmi_reset"] = tmp["io_hdmiReset"]


class CpuTraffic(StreamResponder):
    """The host on ``io_cpuBus_cmd``.  See the module docstring for the mix."""

    label = "cpu"

    #: Cycles before the first swap / buffer-offset change: the scanout's
    #: prefill (2048 pixels through the shared colour read port) is done well
    #: inside this, after which it latches its base per frame and a swap
    #: cannot strand it (see the module docstring on ``fbBaseAddr``).
    PREFILL_CYCLES = 10_000
    #: smallest triangle area (px^2) the CPU submits; see `_vertices`.
    MIN_AREA = 8.0

    def __init__(self, *, rng: random.Random, spec: dict, valid_density: float = 0.7,
                 max_pending_reads: int = 2):
        super().__init__(stream=_CMD, payload=_payload_ports(spec, _CMD), rng=rng,
                         valid_density=valid_density)
        self._p = f"{_CMD}_payload"
        self._cycle = 0
        self._decoded = [a for a in regdef.decode_addresses() if a not in _SANE]
        self._commands = set(regdef.command_addresses())
        self._aliases = regdef.float_aliases()
        self._max_pending = max_pending_reads
        self._kinds = {k: 0 for k in (
            "reg_write", "reg_write_undecoded", "reg_read", "triangle", "ftriangle",
            "fastfill", "swapbuffer", "nop", "lfb_write", "lfb_read", "tex_write",
            "tex_read", "clip", "sane", "degenerate_skipped")}
        self._pending_reads = 0
        self._rsp_beats = 0
        self._cmd_beats = 0
        self._writes_fired = self._reads_fired = 0
        self._scripted = 0
        self._recent: list = []

    # -- beats ---------------------------------------------------------------

    def _beat(self, *, opcode: int, address: int, data: int = 0, mask: int = 0xF,
              source: int | None = None) -> dict:
        if source is None:
            source = self._rng.getrandbits(4) if self._rng.random() < 0.2 else 0
        return {f"{self._p}_last": 1, f"{self._p}_fragment_source": source,
                f"{self._p}_fragment_opcode": opcode,
                f"{self._p}_fragment_address": address & 0xFFFFFF,
                f"{self._p}_fragment_length": 3,
                f"{self._p}_fragment_data": data & 0xFFFFFFFF,
                f"{self._p}_fragment_mask": mask & 0xF}

    def _write(self, address: int, data: int, mask: int = 0xF) -> None:
        if address < LFB_BASE:
            self._recent.append((self._cycle, hex(address), hex(data)))
            del self._recent[:-120]
        self.enqueue(self._beat(opcode=1, address=address, data=data, mask=mask))

    def _read(self, address: int) -> None:
        self.enqueue(self._beat(opcode=0, address=address))

    # -- the scene -----------------------------------------------------------

    def _clip_window(self) -> None:
        rng = self._rng
        w = rng.randint(1, 64)
        h = rng.randint(1, 48)
        left = rng.randint(0, 640 - w)
        low = rng.randint(0, 480 - h)
        self._write(CLIP_LR, (left << 16) | (left + w))
        self._write(CLIP_LH, (low << 16) | (low + h))
        self._kinds["clip"] += 1

    def _sane_init(self) -> None:
        rng = self._rng
        # videoTilesX = 10 (the reset value: 640-pixel stride, the display
        # width); written once and never changed, see `_next`.
        self._write(FBI_INIT1, 10 << 4)
        self._write(FBI_INIT2, 150 << 11)                          # 600 KiB apart
        self._write(FBZ_MODE, (1 << 9) | (1 << 0) | (rng.getrandbits(1) << 8)
                    | (rng.getrandbits(1) << 10) | (rng.getrandbits(1) << 4))
        self._write(LFB_MODE, 0)
        self._write(FBZ_COLOR_PATH, rng.getrandbits(32))
        self._write(ALPHA_MODE, rng.getrandbits(32) & ~0x1)       # alpha test off
        self._write(FOG_MODE, rng.getrandbits(32) & ~0x1)          # fog off
        self._write(TEXTURE_MODE, rng.getrandbits(32))
        self._write(T_LOD, rng.getrandbits(32))
        self._write(TEX_BASE_ADDR, rng.getrandbits(19))
        self._clip_window()
        self._kinds["sane"] += 1

    def _sane_value(self, addr: int) -> int:
        rng = self._rng
        if addr == FBI_INIT2:
            return rng.choice([150, 160, 200]) << 11
        raise AssertionError(addr)

    def _vertices(self):
        rng = self._rng
        r = rng.random()
        # ~0.5 pixel/cycle with memory always ready (pysim: a 40x40 right
        # triangle, 780 pixels, is ~1650 cycles), less under random readies;
        # a "large" one here is ~5-10k cycles of pipelineBusy.
        if r < 0.05:
            cx, cy, size = rng.uniform(-100, 800), rng.uniform(-100, 600), rng.uniform(2, 32)
        elif r < 0.15:
            cx, cy, size = rng.uniform(40, 600), rng.uniform(40, 440), rng.uniform(32, 60)
        else:
            cx, cy, size = rng.uniform(0, 640), rng.uniform(0, 480), rng.uniform(2, 32)
        while True:
            verts = [(cx + rng.uniform(-size, size), cy + rng.uniform(-size, size))
                     for _ in range(3)]
            (x0, y0), (x1, y1), (x2, y2) = verts
            # A near-degenerate triangle (seed 2 of the 1e6 gate: area 1.4 px^2)
            # keeps `rasterizer_running` up for >140k cycles with no pixel
            # coming out, on both sides; Glide never submits one.  Regenerate.
            if abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) >= 2 * self.MIN_AREA:
                return verts
            self._kinds["degenerate_skipped"] += 1

    def _gradients(self) -> dict[int, float]:
        rng = self._rng
        g: dict[int, float] = {}
        for a in START_RGBA:
            g[a] = rng.uniform(-20, 275)
        for a in (*DX_RGBA, *DY_RGBA):
            g[a] = rng.uniform(-6, 6)
        g[START_Z] = rng.uniform(-500, 66000)
        g[DX_Z] = rng.uniform(-300, 300)
        g[DY_Z] = rng.uniform(-300, 300)
        w = rng.uniform(0.05, 1.0) if rng.random() < 0.9 else rng.uniform(-1.9, 1.9)
        g[START_W] = w
        g[DX_W] = rng.uniform(-0.01, 0.01)
        g[DY_W] = rng.uniform(-0.01, 0.01)
        for a in (START_S, START_T):
            g[a] = rng.uniform(-256, 256) * w
        for a in (DX_S, DX_T, DY_S, DY_T):
            g[a] = rng.uniform(-2, 2)
        return g

    def _triangle(self, *, floats: bool) -> None:
        verts = self._vertices()
        grads = self._gradients()
        # the last few, for a wedge post-mortem (`stats()` prints them)
        self._recent.append((self._cycle, "f" if floats else "i",
                             [(round(x, 2), round(y, 2)) for x, y in verts]))
        coords = {}
        for i, (x, y) in enumerate(verts):
            coords[VERTEX_REGS[2 * i]] = x
            coords[VERTEX_REGS[2 * i + 1]] = y
        # Only a random subset of the gradient registers is rewritten per
        # triangle; the rest keep whatever the last one (or the reset) left.
        regs = list(coords.items()) + [(a, v) for a, v in grads.items()
                                       if self._rng.random() < 0.6]
        if floats:
            by_target = {al.target_addr: (addr, al) for addr, al in self._aliases.items()}
            for target, value in regs:
                addr, _al = by_target[target]
                self._write(addr, _f32(value))
            self._write(FTRIANGLE_CMD, self._rng.getrandbits(32))
            self._kinds["ftriangle"] += 1
        else:
            for target, value in regs:
                reg = regdef.by_addr(target)
                fld = reg.fields[0]
                q = fld.qformat
                self._write(target, _fix(value, q.fraction, q.width))
            self._write(TRIANGLE_CMD, self._rng.getrandbits(32))
            self._kinds["triangle"] += 1

    # -- the mix ---------------------------------------------------------------

    def _next(self) -> None:
        rng = self._rng
        r = rng.random()
        if r < 0.30:
            addr = rng.choice(self._decoded)
            if addr in self._commands:
                addr = FBZ_COLOR_PATH
            self._write(addr, rng.getrandbits(32), 0xF if rng.random() < 0.9 else rng.getrandbits(4))
            self._kinds["reg_write"] += 1
        elif r < 0.34:
            self._write(rng.getrandbits(22) & ~3, rng.getrandbits(32))
            self._kinds["reg_write_undecoded"] += 1
        elif r < 0.36:
            # fbiInit2 (buffer offset) only, and only after the prefill: the
            # scanout latches its base per frame but reads `pixelStride` live,
            # so an fbiInit1 change between a line's prefetch and its reads
            # mismatches the in-order scanout reader (allowDirectMiss = false)
            # and parks it for the rest of the run -- on both sides.  Real
            # hosts write it once, at init.
            if self._cycle > self.PREFILL_CYCLES:
                self._write(FBI_INIT2, self._sane_value(FBI_INIT2))
                self._kinds["sane"] += 1
        elif r < 0.40:
            self._clip_window()
        elif r < 0.58:
            self._triangle(floats=False)
        elif r < 0.665:
            self._triangle(floats=True)
        elif r < 0.67:
            # A fastfill of the (<= 64x48) clip window is ~1.5k cycles of
            # pipelineBusy, during which every sync'd command waits.
            self._write(FASTFILL_CMD, rng.getrandbits(32))
            self._kinds["fastfill"] += 1
        elif r < 0.70:
            # swapVsyncEnable in bit 0, swapInterval in 8:1; not during the
            # scanout prefill (a swap moves frontBase under it; module docstring).
            if self._cycle > self.PREFILL_CYCLES:
                self._write(SWAPBUFFER_CMD, rng.getrandbits(1) | (rng.randint(0, 3) << 1))
                self._kinds["swapbuffer"] += 1
        elif r < 0.72:
            self._write(NOP_CMD, rng.getrandbits(32) if rng.random() < 0.3 else 0)
            self._kinds["nop"] += 1
        elif r < 0.82:
            n = rng.randint(1, 8)
            base = rng.getrandbits(22) & ~3 if rng.random() < 0.2 else \
                ((rng.randint(0, 479) << 11) | (rng.randint(0, 318) << 1)) & ~3
            for i in range(n):
                self._write(LFB_BASE + ((base + 4 * i) & 0x3FFFFF), rng.getrandbits(32),
                            0xF if rng.random() < 0.8 else rng.getrandbits(4))
            self._kinds["lfb_write"] += n
        elif r < 0.85:
            if self._pending_reads < self._max_pending:
                self._read(LFB_BASE + (rng.getrandbits(22) & ~3))
                self._kinds["lfb_read"] += 1
        elif r < 0.93:
            n = rng.randint(1, 16)
            base = rng.getrandbits(23) & ~3
            for i in range(n):
                self._write(TEX_BASE + ((base + 4 * i) & 0x7FFFFF), rng.getrandbits(32),
                            0xF if rng.random() < 0.9 else rng.getrandbits(4))
            self._kinds["tex_write"] += n
        elif r < 0.96:
            if self._pending_reads < self._max_pending:
                self._read(TEX_BASE + (rng.getrandbits(23) & ~3))
                self._kinds["tex_read"] += 1
        else:
            if self._pending_reads < self._max_pending:
                self._read(rng.choice([0x000, 0x000, 0x000, rng.choice(self._decoded)]))
                self._kinds["reg_read"] += 1

    # -- Responder -------------------------------------------------------------

    def reset(self):
        super().reset()
        for k in self._kinds:
            self._kinds[k] = 0
        self._pending_reads = 0
        self._rsp_beats = self._cmd_beats = 0
        self._writes_fired = self._reads_fired = 0
        self._scripted = 0
        self._cycle = 0
        self._recent = []
        self._sane_init()

    def observe(self, obs):
        self._cycle += 1
        if obs.fired(_CMD):
            beat = obs.beat(_CMD)
            self._cmd_beats += 1
            a = beat[f"{self._p}_fragment_address"]
            if a in (TRIANGLE_CMD, FTRIANGLE_CMD, FASTFILL_CMD, SWAPBUFFER_CMD, NOP_CMD):
                self._recent.append((self._cycle, "FIRE", hex(a), self._cmd_beats))
            if beat[f"{self._p}_fragment_opcode"]:
                self._writes_fired += 1
            else:
                self._reads_fired += 1
                self._pending_reads += 1
        if obs.fired(_RSP):
            self._rsp_beats += 1
            # Every write is acknowledged too, and a write ack carries the
            # same SUCCESS opcode as read data, so the response cannot say
            # which it answers.  `_pending_reads` is only a throttle on how
            # many reads are *issued* close together (each path -- PciFifo,
            # Lfb, the texture read queue -- serialises its own anyway):
            # decrementing on any response over-counts completions and can
            # never wedge the mix.
            if self._pending_reads:
                self._pending_reads -= 1
        while len(self._queue) < 4:
            self._next()

    def stats(self):
        return {**super().stats(), **{f"kind_{k}": v for k, v in self._kinds.items()},
                "cmd_beats": self._cmd_beats, "writes": self._writes_fired,
                "reads": self._reads_fired, "rsp_beats": self._rsp_beats,
                "pending_reads": self._pending_reads,
                # the last ~120 register writes / command fires / triangles,
                # for a wedge post-mortem (how the degenerate case was found)
                "recent": repr(self._recent)}


class CoreCounters(Responder):
    """Claims nothing; copies the Core's counters and status into the report."""

    label = "dut"
    PREFIXES = ("io_scanoutPrefetchCount", "io_scanoutReadReqCount", "io_scanoutReadRspCount",
                "io_scanoutFillHits", "io_scanoutFillMisses", "io_swapDisplayedBuffer",
                "io_swapsPending", "io_pipelineBusySources", "io_hdmi_status_",
                "io_hdmi_underflow")

    def __init__(self, spec: dict):
        self._ports = [p["spinal"] for p in spec["outputs"]
                       if p["spinal"].startswith(self.PREFIXES)]
        self._obs = None
        self.reset()

    def claims(self):
        return set()

    def reset(self):
        self._obs = None
        self._busy_cycles = self._cycles = self._swaps = 0
        self._last_displayed = 0
        # per-source busy cycles, and the longest unbroken pipelineBusy run
        self._source_cycles = [0] * 32
        self._streak = self._longest_streak = 0
        self._streak_start = self._longest_start = 0

    def cycle(self, obs, drive):
        self._obs = obs
        self._cycles += 1
        busy = obs["io_pipelineBusySources"]
        if busy:
            self._busy_cycles += 1
            if self._streak == 0:
                self._streak_start = self._cycles
            self._streak += 1
            if self._streak > self._longest_streak:
                self._longest_streak = self._streak
                self._longest_start = self._streak_start
            for i in range(32):
                if busy >> i & 1:
                    self._source_cycles[i] += 1
        else:
            self._streak = 0
        d = obs["io_swapDisplayedBuffer"]
        if d != self._last_displayed:
            self._swaps += 1
            self._last_displayed = d

    def stats(self):
        out = {"busy_cycles": self._busy_cycles, "cycles": self._cycles, "swaps": self._swaps,
               "longest_busy_streak": self._longest_streak,
               "longest_busy_streak_start": self._longest_start,
               **{f"busy_src{i}": n for i, n in enumerate(self._source_cycles) if n}}
        if self._obs is not None:
            out.update({name[3:]: self._obs[name] for name in self._ports})
        return out


def _memory_slaves(rng: random.Random, spec: dict) -> list[MemSlave]:
    sub = lambda: random.Random(rng.random())  # noqa: E731
    return [MemSlave(bus=bus, rng=sub(), spec=spec)
            for bus in ("io_fbMemWrite", "io_fbColorWriteMem", "io_fbAuxWriteMem",
                        "io_fbColorReadMem", "io_fbAuxReadMem", "io_texMem")]


def core_traffic(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder`` for ``Core``."""
    slaves = _memory_slaves(rng, spec)
    # The fastest pixel clock (2 core cycles) so the HDMI side reaches a
    # frame origin inside 1e6 cycles (858x525 = 450k pixel clocks) and pops
    # pixels; no mid-run HDMI reset, which would restart that count.  The
    # slower ratios and the resets are the scanout rows' business.
    return Composite(
        *slaves,
        HdmiClockPort(rng=random.Random(rng.random()), toggle_rate=1.0),
        CpuTraffic(rng=random.Random(rng.random()), spec=spec,
                   valid_density=rng.choice([0.5, 0.7, 0.9])),
        CoreCounters(spec),
    )


def core_random(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``DiffEntry.responder`` for ``CoreRandom``: slaves and pixel clock only."""
    slaves = _memory_slaves(rng, spec)
    rate = rng.choice([1.0, 0.5, 0.35])
    return Composite(
        *slaves,
        HdmiClockPort(rng=random.Random(rng.random()), toggle_rate=rate,
                      mid_run_reset_every=333_000),
        CoreCounters(spec),
    )
