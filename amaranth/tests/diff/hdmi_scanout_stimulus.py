"""Responders for the ``HdmiScanout.scala`` lockstep rows (plan Phase 7.1).

The differential harness drives one clock (``clk``/``reset``), but
``HdmiCdcFramebufferScanout`` takes its pixel clock and reset as two plain
``in Bool`` ports, ``io_hdmiClock`` and ``io_hdmiReset``.  Those are ordinary
inputs to the harness, so a responder can *be* the HDMI clock: it toggles
``io_hdmiClock`` on the core clock's falling edges at a per-seed ratio and
pulses ``io_hdmiReset``.  Both Verilator models sit in the same ``diff_top``
and see the identical edge sequence, so the whole two-domain design -- the
``StreamFifoCC``, its buffered pop reset, the ``PulseCCByToggle`` and every
``BufferCC`` -- is compared in lockstep.  What this does *not* model is
metastability: an edge lands on both sides at the same instant, which is the
same idealisation SpinalSim/Verilator make in ``HdmiScanoutTest.scala``.

Three more helpers make the run mean something:

:class:`RegScene`
    ``io_regs_*`` are "registers" in the design (written by the CPU, stable
    for frames at a time), not per-cycle random inputs.  Re-randomised every
    cycle, ``displayEnabled`` thrashes and the prefetcher never leaves its
    reset arm.  A scene holds them for tens of thousands of cycles, biased to
    the values that make the design run (enable on, real widths, identity
    LUT), with a minority of degenerate ones (zero width, test pattern,
    random LUT).
:class:`ReadRspSlave`
    answers every ``io_readReq`` fire with one ``io_readRsp`` beat, in order,
    with random idle cycles, so ``outstanding`` tracks real traffic instead
    of random-walking through 2**13.
:class:`Activity`
    claims nothing; counts what the DUT did (frames, read requests, pushes,
    pixels displayed, underflow) so a clean 1e6-cycle run is known to have
    exercised the paths and not just idled.
"""

from __future__ import annotations

import random

from tests.diff.plane_reader_stimulus import Composite
from tools.diff.responders import Responder, StreamResponder

__all__ = [
    "HdmiClock",
    "RegScene",
    "ReadRspSlave",
    "Activity",
    "cdc_scene",
    "cdc_scene_small",
    "cdc_clock_only",
    "framebuffer_scene",
]

#: Identity gamma LUT, ``HdmiScanoutTest.identityGammaEntry``.
IDENTITY_LUT = [
    ((255 if i == 32 else i * 8) << 16) | ((255 if i == 32 else i * 8) << 8)
    | (255 if i == 32 else i * 8)
    for i in range(33)
]


class HdmiClock(Responder):
    """``io_hdmiClock`` / ``io_hdmiReset``.

    ``toggle_rate`` is toggles per core cycle as a phase accumulator, so
    ``1.0`` is a pixel clock of two core cycles (the fastest a falling-edge
    responder can make) and ``0.35`` roughly the Console's 27 MHz against
    a core clock in the 70-80 MHz range.  ``io_hdmiReset`` is held for
    ``reset_cycles`` core cycles after the harness reset, and again for
    ``reset_cycles`` at every ``mid_run_reset_every`` cycles when set, so the
    HDMI-domain reset is also seen while the core side is live.
    """

    def __init__(self, *, rng: random.Random, toggle_rate: float, reset_cycles: int = 12,
                 mid_run_reset_every: int | None = None):
        self._rng = rng
        self._rate = toggle_rate
        self._reset_cycles = reset_cycles
        self._mid = mid_run_reset_every
        self.reset()

    def claims(self):
        return {"io_hdmiClock", "io_hdmiReset"}

    def reset(self):
        self._clock = 0
        self._acc = 0.0
        self._cycle = 0
        self._rising = 0
        self._resets = 0
        self._in_reset = False

    def cycle(self, obs, drive):
        self._acc += self._rate
        if self._acc >= 1.0:
            self._acc -= 1.0
            self._clock ^= 1
            self._rising += self._clock
        in_reset = self._cycle < self._reset_cycles
        if self._mid and self._cycle >= self._mid:
            in_reset = in_reset or (self._cycle % self._mid) < self._reset_cycles
        if in_reset and not self._in_reset:
            self._resets += 1
        self._in_reset = in_reset
        drive["io_hdmiClock"] = self._clock
        drive["io_hdmiReset"] = 1 if in_reset else 0
        self._cycle += 1

    def stats(self):
        return {"hdmi_rising_edges": self._rising, "hdmi_resets": self._resets}


class RegScene(Responder):
    """Hold ``io_regs_*`` for a scene, then pick a new one."""

    PORTS = ("io_regs_frontBase", "io_regs_backBase", "io_regs_pixelStride",
             "io_regs_displayWidth", "io_regs_displayHeight", "io_regs_framebufferEnable",
             "io_regs_testPatternEnable")

    def __init__(self, *, rng: random.Random, spec: dict, active_width: int, active_height: int,
                 scene_cycles: tuple[int, int], first_scene_cycles: int | None = None):
        """``first_scene_cycles`` makes the first scene the canonical one (what
        ``Core`` drives) for that many cycles, so every seed runs the main
        path for at least one frame before the random scenes start."""
        self._rng = rng
        self._widths = {p["spinal"]: p["width"] for p in spec["inputs"]}
        self._lut_ports = [f"io_regs_gammaLut_{i}" for i in range(33)]
        for name in (*self.PORTS, *self._lut_ports):
            if name not in self._widths:
                raise ValueError(f"RegScene: {name} is not a DUT input")
        self._aw = self._widths["io_regs_frontBase"]
        self._active = (active_width, active_height)
        self._scene_cycles = scene_cycles
        self._first = first_scene_cycles
        self.reset()

    def claims(self):
        return {*self.PORTS, *self._lut_ports}

    def reset(self):
        self._remaining = 0
        self._values: dict = {}
        self._scenes = 0
        self._enabled_scenes = 0

    def _dimension(self, active: int, width: int) -> int:
        r = self._rng.random()
        if r < 0.55:
            return active
        if r < 0.80:
            return self._rng.randint(1, active)
        if r < 0.90:
            return self._rng.randint(1, (1 << width) - 1)
        return 0

    def _canonical_scene(self) -> dict:
        """What ``Core.scala:171-178`` drives: full active area, enable on, identity LUT."""
        aw_x, aw_y = self._active
        v = {
            "io_regs_frontBase": 0x1000,
            "io_regs_backBase": 0x2000,
            "io_regs_pixelStride": 1024,
            "io_regs_displayWidth": aw_x,
            "io_regs_displayHeight": aw_y,
            "io_regs_framebufferEnable": 1,
            "io_regs_testPatternEnable": 0,
        }
        for name, entry in zip(self._lut_ports, IDENTITY_LUT):
            v[name] = entry
        return v

    def _new_scene(self) -> dict:
        rng = self._rng
        aw, (aw_x, aw_y) = self._aw, self._active
        if self._scenes == 0 and self._first is not None:
            return self._canonical_scene()
        v = {
            "io_regs_frontBase": rng.getrandbits(aw) & ~1 if rng.random() < 0.7
            else rng.getrandbits(aw),
            "io_regs_backBase": rng.getrandbits(aw),
            "io_regs_pixelStride": rng.choice([64, 128, 256, 512, 1024, rng.getrandbits(11)]),
            "io_regs_displayWidth": self._dimension(aw_x, 10),
            "io_regs_displayHeight": self._dimension(aw_y, 10),
            "io_regs_framebufferEnable": 1 if rng.random() < 0.85 else 0,
            "io_regs_testPatternEnable": 1 if rng.random() < 0.15 else 0,
        }
        lut = IDENTITY_LUT if rng.random() < 0.5 else [rng.getrandbits(24) for _ in range(33)]
        for name, entry in zip(self._lut_ports, lut):
            v[name] = entry
        return v

    def cycle(self, obs, drive):
        if self._remaining == 0:
            self._values = self._new_scene()
            lo, hi = self._scene_cycles
            self._remaining = self._rng.randint(lo, hi)
            if self._scenes == 0 and self._first is not None:
                self._remaining = self._first
            self._scenes += 1
            if (self._values["io_regs_framebufferEnable"]
                    and self._values["io_regs_displayWidth"]
                    and self._values["io_regs_displayHeight"]):
                self._enabled_scenes += 1
        self._remaining -= 1
        drive.update(self._values)

    def stats(self):
        return {"scenes": self._scenes, "enabled_scenes": self._enabled_scenes}


class ReadRspSlave(StreamResponder):
    """One ``io_readRsp`` beat (random RGB565) per ``io_readReq`` fire, in order.

    ``stall_probability`` starts, per cycle, a stall of ``stall_cycles``
    (a random length in that range) during which no response is offered --
    a memory that falls behind, which is what makes the pixel FIFO run dry
    and ``underflowReg`` set.
    """

    def __init__(self, *, rng: random.Random, valid_density: float = 0.7,
                 stall_probability: float = 0.0, stall_cycles: tuple[int, int] = (200, 3000)):
        super().__init__(stream="io_readRsp", payload=["io_readRsp_payload_data"], rng=rng,
                         valid_density=valid_density)
        self._stall_p = stall_probability
        self._stall_range = stall_cycles
        self._reqs = 0
        self._stalled = 0
        self._stalls = 0

    def reset(self):
        super().reset()
        self._reqs = 0
        self._stalled = 0
        self._stalls = 0

    def observe(self, obs):
        if obs.fired("io_readReq"):
            self._reqs += 1
            self.enqueue({"io_readRsp_payload_data": self._rng.getrandbits(16)})

    def cycle(self, obs, drive):
        if self._stalled:
            self._stalled -= 1
        elif self._stall_p and self._rng.random() < self._stall_p:
            self._stalled = self._rng.randint(*self._stall_range)
            self._stalls += 1
        if self._stalled and self._presented is None:
            # keep the queue growing but offer nothing (hold rule: a beat
            # already presented stays until it fires)
            self.observe(obs)
            drive[self._valid] = 0
            for name in self._payload:
                drive[name] = 0
            return
        super().cycle(obs, drive)

    def stats(self):
        return {**super().stats(), "read_reqs": self._reqs, "stalls": self._stalls}


class Activity(Responder):
    """Claims nothing; counts what the DUT did, from the Spinal side."""

    def __init__(self, *, ports: dict[str, str]):
        self._ports = ports
        self.reset()

    def claims(self):
        return set()

    def reset(self):
        self._counts = {k: 0 for k in self._ports}
        self._fires = {"io_readReq": 0, "io_prefetchReq": 0, "io_readRsp": 0}
        self._underflow = 0
        self._max_push_occ = 0
        self._fb_pixels = 0

    def cycle(self, obs, drive):
        for key, port in self._ports.items():
            if port in obs and obs[port]:
                self._counts[key] += 1
        for base in self._fires:
            if f"{base}_valid" in obs and obs.fired(base):
                self._fires[base] += 1
        if "io_underflow" in obs and obs["io_underflow"]:
            self._underflow += 1
        if "io_fifoPushOccupancy" in obs:
            self._max_push_occ = max(self._max_push_occ, obs["io_fifoPushOccupancy"])
        if obs["io_video_de"] and (obs["io_video_rgb_r"] or obs["io_video_rgb_g"]
                                   or obs["io_video_rgb_b"]):
            self._fb_pixels += 1

    def stats(self):
        return {
            **self._counts,
            **{f"{k}_fires": v for k, v in self._fires.items()},
            "underflow_cycles": self._underflow,
            "max_push_occupancy": self._max_push_occ,
            "lit_pixels": self._fb_pixels,
        }


_CDC_ACTIVITY = {
    "new_frames": "io_status_newFrame",
    "active_cycles": "io_status_active",
    "de_cycles": "io_video_de",
}


def cdc_scene(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``HdmiCdcFramebufferScanout``: cea720x480p, fifo 4096/2048/1024/3072.

    A 858 x 525 frame is 450k pixel clocks, so the pixel clock runs at the
    fastest ratio (two core cycles) and the canonical scene (what ``Core``
    drives) is held for the first 1e6 core cycles: the 2048-deep prefill
    completes in ~4k cycles, the FIFO then sits at ``refillHighLevel`` until
    the first ``newFrame`` at pixel clock ~450k (core cycle ~901k) arms
    ``frameAligned``, and the last ~50k pixels of the seed pop from the FIFO.
    No mid-run HDMI reset here: it would restart the scan before that frame
    end.  A run longer than 1e6 cycles gets the random scenes after it.
    """
    return Composite(
        HdmiClock(rng=rng, toggle_rate=1.0),
        RegScene(rng=rng, spec=spec, active_width=720, active_height=480,
                 scene_cycles=(150_000, 400_000), first_scene_cycles=1_000_000),
        ReadRspSlave(rng=rng, valid_density=0.8),
        Activity(ports=_CDC_ACTIVITY),
    )


def cdc_scene_small(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``HdmiCdcFramebufferScanoutSmall``: 8x4 in 12x7, fifo 64/8/8/48.

    84 pixel clocks per frame, so the ratio varies per seed and the scenes
    are short; underflow, refill hysteresis and the end-of-frame reload all
    happen many times per run.
    """
    rate = rng.choice([1.0, 0.7, 0.5, 0.35])
    return Composite(
        HdmiClock(rng=rng, toggle_rate=rate, mid_run_reset_every=137_000),
        RegScene(rng=rng, spec=spec, active_width=8, active_height=4,
                 scene_cycles=(2_000, 20_000), first_scene_cycles=5_000),
        ReadRspSlave(rng=rng, valid_density=rng.choice([0.3, 0.6, 0.9]),
                     stall_probability=1 / 15_000),
        Activity(ports=_CDC_ACTIVITY),
    )


def cdc_clock_only(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``HdmiCdcFramebufferScanoutRandom``: the pixel clock only; everything
    else is the generator's per-cycle randomness.

    Known shape of that run: the generator's unsolicited ``io_readRsp`` beats
    are pushed whenever ``displayEnabled`` happens to be high, each one
    stepping ``outstanding`` below zero (it wraps to 8191), so ``projected``
    exceeds ``fifoDepth - 4`` almost at once and ``io_readReq`` never fires.
    The row still compares the two implementations cycle for cycle -- FIFO
    full, prefetch requests, the HDMI side -- but the read path is dead here;
    ``HdmiCdcFramebufferScanout`` and ``...Small`` are the rows that drive it.
    """
    rate = rng.choice([1.0, 0.5, 0.35])
    return Composite(
        HdmiClock(rng=rng, toggle_rate=rate, mid_run_reset_every=333_000),
        Activity(ports=_CDC_ACTIVITY),
    )


def framebuffer_scene(*, seed: int, rng: random.Random, spec: dict) -> Responder:
    """``HdmiFramebufferScanoutScene``: the single-clock scanout under register scenes;
    ``io_swap`` and ``io_readRsp`` stay with the generator."""
    return Composite(
        RegScene(rng=rng, spec=spec, active_width=720, active_height=480,
                 scene_cycles=(50_000, 300_000), first_scene_cycles=100_000),
        Activity(ports={"new_frames": "io_status_newFrame", "active_cycles": "io_status_active",
                        "swap_pending_cycles": "io_status_swapPending"}),
    )
