"""Phase 2.3 unit tests for :mod:`avoodoo.frontend.lfb`.

There is no ``LfbTest.scala`` upstream.  These tests drive the real ``MemBus``
command port and compare every emitted pixel against :func:`decode_write`, a
Python reference converter for the LFB write path, over

* every ``writeFormat`` (0-15, the undecoded ones included),
* every ``rgbaLanes`` swizzle for the 8888 formats,
* word swap x byte swizzle on writes,
* full-word / low-half / high-half masks (single vs dual pixel, ``x`` vs
  ``x + 1``),
* both ``pixelPipelineEnable`` routings (``writeOutput`` vs
  ``pipelineOutput``), each under a randomly stalling consumer,

plus the read path (two framebuffer-plane fetches through ``fbReadBus``
against a Python memory, word swap x byte swizzle on the response, the
``pciFifoEmpty && !pipelineBusy`` gate) and the bus handshake rules
(``cmd.ready`` only in idle with no response outstanding, one response per
command, ``busy`` on the write states only).
"""

import random
from dataclasses import dataclass

import pytest
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.frontend.lfb import (DUAL_PIXEL_FORMATS, STRIDE32_FORMATS, Lfb)
from avoodoo.lib.membus import CMD_READ, CMD_WRITE

PERIOD = 1e-6
C = Config.voodoo1()
ADDR_MASK = (1 << C.address_width) - 1


# ---------------------------------------------------------------------------
# Python reference model
# ---------------------------------------------------------------------------


def word_swap_and_swizzle(d: int, word_swap: bool, byte_swizzle: bool) -> int:
    """``wordSwapAndSwizzle`` (Bundles.scala:162)."""
    if word_swap:
        d = ((d & 0xFFFF) << 16) | (d >> 16)
    if byte_swizzle:
        d = int.from_bytes(d.to_bytes(4, "little"), "big")
    return d


def expand_to_8(v: int, w: int) -> int:
    """``expandTo8`` -- replicate the field until 8 bits are filled."""
    if w >= 8:
        return v & 0xFF
    if w == 1:
        return 0xFF if v & 1 else 0
    reps = (8 + w - 1) // w
    wide = 0
    for _ in range(reps):
        wide = (wide << w) | (v & ((1 << w) - 1))
    return (wide >> (reps * w - 8)) & 0xFF


def expand_rgb565(v):
    return expand_to_8(v >> 11, 5), expand_to_8((v >> 5) & 0x3F, 6), expand_to_8(v & 0x1F, 5)


def expand_rgb555(v):
    return (expand_to_8((v >> 10) & 0x1F, 5), expand_to_8((v >> 5) & 0x1F, 5),
            expand_to_8(v & 0x1F, 5))


def argb8888(d: int, lanes: int):
    """``decodeArgb8888`` + ``decodeAlpha8888`` -> ``(r, g, b, a)``."""
    b0, b1, b2, b3 = d & 0xFF, (d >> 8) & 0xFF, (d >> 16) & 0xFF, (d >> 24) & 0xFF
    if lanes == 0:    # ARGB
        return b2, b1, b0, b3
    if lanes == 1:    # ABGR
        return b0, b1, b2, b3
    if lanes == 2:    # RGBA
        return b3, b2, b1, b0
    return b1, b2, b3, b0  # BGRA


@dataclass
class Regs:
    write_format: int = 0
    rgba_lanes: int = 0
    word_swap_writes: int = 0
    byte_swizzle_writes: int = 0
    word_swap_reads: int = 0
    byte_swizzle_reads: int = 0
    pixel_pipeline_enable: int = 0
    read_buffer_select: int = 0
    enable_alpha_planes: int = 0
    enable_dithering: int = 0
    dither_algorithm: int = 0
    za_color: int = 0
    fog_color: int = 0
    alpha_mode: int = 0
    fog_mode: int = 0
    fbz_mode: int = 0
    write_color_base: int = 0
    write_aux_base: int = 0
    write_stride: int = 640
    read_color_base: int = 0
    read_aux_base: int = 0
    read_stride: int = 640


@dataclass
class Pixel:
    x: int
    y: int
    r: int
    g: int
    b: int
    depth: int
    rgb_write: int
    aux_write: int
    alpha: int


def decode_pixel(regs: Regs, data32: int, pix16: int) -> Pixel:
    """One pixel's colour/depth/alpha for the *captured* (post-swizzle) word."""
    fmt = regs.write_format
    lo16, hi16 = data32 & 0xFFFF, data32 >> 16
    r = g = b = 0
    depth = regs.za_color & 0xFFFF
    rgb_write, aux_write, alpha = 1, 0, 0xFF
    if fmt == 0:
        r, g, b = expand_rgb565(pix16)
        aux_write = regs.enable_alpha_planes
    elif fmt in (1, 2):
        r, g, b = expand_rgb555(pix16)
        aux_write = regs.enable_alpha_planes
        if fmt == 2:
            alpha = 0xFF if pix16 & 0x8000 else 0
    elif fmt in (4, 5):
        r, g, b, a = argb8888(data32, regs.rgba_lanes)
        aux_write = regs.enable_alpha_planes
        if fmt == 5:
            alpha = a
    elif fmt == 12:
        r, g, b = expand_rgb565(lo16)
        depth, aux_write = hi16, 1
    elif fmt in (13, 14):
        r, g, b = expand_rgb555(lo16)
        depth, aux_write = hi16, 1
        if fmt == 14:
            alpha = 0xFF if lo16 & 0x8000 else 0
    elif fmt == 15:
        depth, rgb_write, aux_write = pix16, 0, 1
    return Pixel(0, 0, r, g, b, depth, rgb_write, aux_write, alpha)


def decode_write(regs: Regs, address: int, data: int, mask: int) -> list[Pixel]:
    """Every pixel one LFB write command produces, in order."""
    fmt = regs.write_format
    data32 = word_swap_and_swizzle(data, regs.word_swap_writes, regs.byte_swizzle_writes)
    lo16, hi16 = data32 & 0xFFFF, data32 >> 16
    if fmt in STRIDE32_FORMATS:
        x, y = (address >> 2) & 0x3FF, (address >> 12) & 0x3FF
    else:
        x, y = (address >> 1) & 0x3FF, (address >> 11) & 0x3FF
    x2 = (x + 1) & 0x3FF
    half = mask in (0b0011, 0b1100)
    use_hi = mask == 0b1100
    p1 = decode_pixel(regs, data32, hi16 if use_hi else lo16)
    p1.x, p1.y = (x2 if (half and use_hi) else x), y
    out = [p1]
    if fmt in DUAL_PIXEL_FORMATS and not half:
        p2 = decode_pixel(regs, data32, hi16)
        p2.x, p2.y = x2, y
        out.append(p2)
    return out


def plane_address(base, x, y, stride):
    """``FramebufferAddressMath.planeAddress``, width for width (26 bits)."""
    tiles_x = (stride >> 6) & 0xF
    row_tiles = (y * tiles_x) & ((1 << 14) - 1)
    row_bytes = (row_tiles << 7) & ADDR_MASK
    x_bytes = (x << 1) & ADDR_MASK
    return (base + ((row_bytes + x_bytes) & ADDR_MASK)) & ADDR_MASK


def decode_read(regs: Regs, address: int, mem) -> tuple[int, list[int]]:
    """``(response word, [word addresses fetched])`` for one LFB read."""
    rx, ry = (address >> 1) & 0x3FF, (address >> 11) & 0x3FF
    base = regs.read_aux_base if regs.read_buffer_select == 2 else regs.read_color_base
    a1 = plane_address(base, rx, ry, regs.read_stride)
    a2 = (a1 + 2) & ADDR_MASK
    w1, w2 = mem(a1 & ~3), mem(a2 & ~3)
    d1 = (w1 >> 16) if a1 & 2 else (w1 & 0xFFFF)
    d2 = (w2 >> 16) if a2 & 2 else (w2 & 0xFFFF)
    raw = (d2 << 16) | d1
    return (word_swap_and_swizzle(raw, regs.word_swap_reads, regs.byte_swizzle_reads),
            [a1 & ~3, a2 & ~3])


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _set_regs(ctx, dut, regs: Regs):
    r = dut.regs
    ctx.set(r.lfb_mode.write_format, regs.write_format)
    ctx.set(r.lfb_mode.rgba_lanes, regs.rgba_lanes)
    ctx.set(r.lfb_mode.word_swap_writes, regs.word_swap_writes)
    ctx.set(r.lfb_mode.byte_swizzle_writes, regs.byte_swizzle_writes)
    ctx.set(r.lfb_mode.word_swap_reads, regs.word_swap_reads)
    ctx.set(r.lfb_mode.byte_swizzle_reads, regs.byte_swizzle_reads)
    ctx.set(r.lfb_mode.pixel_pipeline_enable, regs.pixel_pipeline_enable)
    ctx.set(r.lfb_mode.read_buffer_select, regs.read_buffer_select)
    ctx.set(r.fbz_mode.as_value(), regs.fbz_mode)
    ctx.set(r.fbz_mode.enable_alpha_planes, regs.enable_alpha_planes)
    ctx.set(r.fbz_mode.enable_dithering, regs.enable_dithering)
    ctx.set(r.fbz_mode.dither_algorithm, regs.dither_algorithm)
    ctx.set(r.alpha_mode.as_value(), regs.alpha_mode)
    ctx.set(r.fog_mode.as_value(), regs.fog_mode)
    ctx.set(r.za_color, regs.za_color)
    ctx.set(r.fog_color, regs.fog_color)
    ctx.set(r.write_routing.color_base_addr, regs.write_color_base)
    ctx.set(r.write_routing.aux_base_addr, regs.write_aux_base)
    ctx.set(r.write_routing.pixel_stride, regs.write_stride)
    ctx.set(r.read_routing.color_base_addr, regs.read_color_base)
    ctx.set(r.read_routing.aux_base_addr, regs.read_aux_base)
    ctx.set(r.read_routing.pixel_stride, regs.read_stride)


def _cmd(ctx, dut, *, valid=1, opcode=CMD_WRITE, address=0, data=0, mask=0xF, source=0):
    cmd = dut.bus.cmd
    f = cmd.payload.fragment
    ctx.set(cmd.valid, valid)
    ctx.set(cmd.payload.last, 1)
    ctx.set(f.opcode, opcode)
    ctx.set(f.address, address)
    ctx.set(f.data, data)
    ctx.set(f.mask, mask)
    ctx.set(f.source, source)
    ctx.set(f.length, 3)


def _sample_write_output(ctx, dut) -> Pixel:
    p = dut.write_output.payload
    return Pixel(ctx.get(p.coords.x), ctx.get(p.coords.y), ctx.get(p.r), ctx.get(p.g),
                 ctx.get(p.b), ctx.get(p.depth_alpha), ctx.get(p.rgb_write),
                 ctx.get(p.aux_write), 0xFF)


def _sample_pipeline_output(ctx, dut) -> Pixel:
    p = dut.pipeline_output.payload
    depth_raw = ctx.get(p.depth.raw)
    assert depth_raw & 0xFFF == 0
    return Pixel(ctx.get(p.coords.x), ctx.get(p.coords.y), ctx.get(p.color.r),
                 ctx.get(p.color.g), ctx.get(p.color.b), (depth_raw >> 12) & 0xFFFF,
                 1, 0, ctx.get(p.alpha))


class Bench:
    """Per-cycle stimulus/observation bookkeeping shared by the tests."""

    def __init__(self, ctx, dut, rng, regs: Regs, *, out_ready_p=0.7, rsp_ready_p=0.7,
                 mem=None, fb_cmd_ready_p=0.7, fb_rsp_delay=(0, 3)):
        self.ctx, self.dut, self.rng, self.regs = ctx, dut, rng, regs
        self.out_ready_p, self.rsp_ready_p = out_ready_p, rsp_ready_p
        self.mem = mem if mem is not None else (lambda a: 0)
        self.fb_cmd_ready_p, self.fb_rsp_delay = fb_cmd_ready_p, fb_rsp_delay
        self.pixels: list[Pixel] = []
        self.rsps: list[tuple[int, int]] = []
        self.fb_fetches: list[int] = []
        self._fb_queue: list[list] = []   # [address, remaining delay]
        self.busy_cycles = 0
        _set_regs(ctx, dut, regs)
        ctx.set(dut.pci_fifo_empty, 1)
        ctx.set(dut.pipeline_busy, 0)
        _cmd(ctx, dut, valid=0)

    def _drive(self):
        ctx, dut, rng = self.ctx, self.dut, self.rng
        ctx.set(dut.write_output.ready, int(rng.random() < self.out_ready_p))
        ctx.set(dut.pipeline_output.ready, int(rng.random() < self.out_ready_p))
        ctx.set(dut.bus.rsp.ready, int(rng.random() < self.rsp_ready_p))
        ctx.set(dut.fb_read_bus.cmd.ready, int(rng.random() < self.fb_cmd_ready_p))
        # framebuffer read responder
        rsp = dut.fb_read_bus.rsp
        if self._fb_queue and self._fb_queue[0][1] <= 0:
            ctx.set(rsp.valid, 1)
            ctx.set(rsp.payload.fragment.data, self.mem(self._fb_queue[0][0]))
        else:
            ctx.set(rsp.valid, 0)
            ctx.set(rsp.payload.fragment.data, rng.getrandbits(32))
        ctx.set(rsp.payload.last, 1)
        ctx.set(rsp.payload.fragment.opcode, 0)
        ctx.set(rsp.payload.fragment.source, 0)

    def _observe(self):
        """Record what fires at the coming clock edge."""
        ctx, dut = self.ctx, self.dut
        wo, po = dut.write_output, dut.pipeline_output
        # exactly one of the two outputs may be valid, and only per the mode
        assert not (ctx.get(wo.valid) and ctx.get(po.valid))
        if ctx.get(wo.valid):
            assert self.regs.pixel_pipeline_enable == 0
            assert ctx.get(dut.busy) == 1
            if ctx.get(wo.ready):
                self.pixels.append(_sample_write_output(ctx, dut))
        if ctx.get(po.valid):
            assert self.regs.pixel_pipeline_enable == 1
            assert ctx.get(dut.busy) == 1
            if ctx.get(po.ready):
                self.pixels.append(_sample_pipeline_output(ctx, dut))
        if ctx.get(dut.busy):
            self.busy_cycles += 1
        rsp = dut.bus.rsp
        if ctx.get(rsp.valid):
            assert ctx.get(rsp.payload.last) == 1
            assert ctx.get(rsp.payload.fragment.opcode) == 0
            assert ctx.get(dut.bus.cmd.ready) == 0     # no new command while pending
            if ctx.get(rsp.ready):
                self.rsps.append((ctx.get(rsp.payload.fragment.source),
                                  ctx.get(rsp.payload.fragment.data)))
        fb = dut.fb_read_bus
        if ctx.get(fb.cmd.valid):
            f = fb.cmd.payload.fragment
            assert ctx.get(f.opcode) == CMD_READ
            assert ctx.get(f.length) == 3
            assert ctx.get(fb.cmd.payload.last) == 1
            assert ctx.get(f.address) & 3 == 0
            if ctx.get(fb.cmd.ready):
                a = ctx.get(f.address)
                self.fb_fetches.append(a)
                self._fb_queue.append([a, self.rng.randint(*self.fb_rsp_delay)])
        if self._fb_queue:
            if ctx.get(fb.rsp.valid) and ctx.get(fb.rsp.ready):
                self._fb_queue.pop(0)
            else:
                self._fb_queue[0][1] -= 1

    async def cycle(self):
        self._drive()
        self._observe()
        await self.ctx.tick()

    async def issue(self, *, opcode, address, data=0, mask=0xF, source=0, max_wait=200):
        """Present one command until it is accepted."""
        _cmd(self.ctx, self.dut, opcode=opcode, address=address, data=data, mask=mask,
             source=source)
        for _ in range(max_wait):
            self._drive()
            fired = self.ctx.get(self.dut.bus.cmd.ready)
            self._observe()
            await self.ctx.tick()
            if fired:
                _cmd(self.ctx, self.dut, valid=0)
                return
        pytest.fail("command never accepted")

    async def drain(self, n=40):
        for _ in range(n):
            await self.cycle()


def run(tb):
    dut = Lfb(C)
    sim = Simulator(dut)
    sim.add_clock(PERIOD)

    async def bench(ctx):
        await tb(ctx, dut)

    sim.add_testbench(bench)
    sim.run()


# ---------------------------------------------------------------------------
# write path: every format x swizzle x mask x mode
# ---------------------------------------------------------------------------


MASKS = (0xF, 0b0011, 0b1100)


def _as_seen(pixels, pipeline: int) -> list[Pixel]:
    """Project the model onto what each output stream actually carries.

    ``Write.PreDither`` has no alpha (the samplers fill in 0xFF);
    ``ColorCombine.Output`` has no ``rgbWrite``/``auxWrite`` (filled in 1/0).
    """
    if pipeline:
        return [Pixel(p.x, p.y, p.r, p.g, p.b, p.depth, 1, 0, p.alpha) for p in pixels]
    return [Pixel(p.x, p.y, p.r, p.g, p.b, p.depth, p.rgb_write, p.aux_write, 0xFF)
            for p in pixels]


def _write_cases(rng, fmt, n):
    for _ in range(n):
        yield (rng.getrandbits(22) & ~0b11, rng.getrandbits(32), rng.choice(MASKS),
               rng.getrandbits(4))


@pytest.mark.parametrize("fmt", range(16))
@pytest.mark.parametrize("pipeline", [0, 1])
def test_write_formats(fmt, pipeline):
    rng = random.Random(0x1F0 + fmt * 2 + pipeline)

    async def tb(ctx, dut):
        for lanes in (range(4) if fmt in (4, 5) else (0,)):
            for ws, bs in ((0, 0), (1, 0), (0, 1), (1, 1)):
                for eap in (0, 1):
                    regs = Regs(write_format=fmt, rgba_lanes=lanes, word_swap_writes=ws,
                                byte_swizzle_writes=bs, pixel_pipeline_enable=pipeline,
                                enable_alpha_planes=eap, za_color=rng.getrandbits(32),
                                enable_dithering=rng.getrandbits(1),
                                dither_algorithm=rng.getrandbits(1))
                    bench = Bench(ctx, dut, rng, regs)
                    want, want_rsp = [], []
                    for address, data, mask, source in _write_cases(rng, fmt, 6):
                        await bench.issue(opcode=CMD_WRITE, address=address, data=data,
                                          mask=mask, source=source)
                        want += decode_write(regs, address, data, mask)
                        want_rsp.append((source, 0))
                    await bench.drain()
                    assert bench.pixels == _as_seen(want, pipeline), (fmt, lanes, ws, bs, eap)
                    assert bench.rsps == want_rsp

    run(tb)


def test_bypass_payload_carries_the_register_state():
    rng = random.Random(3)

    async def tb(ctx, dut):
        regs = Regs(write_format=0, enable_dithering=1, dither_algorithm=1,
                    write_color_base=0x123450, write_aux_base=0x2ABCD0, write_stride=800)
        bench = Bench(ctx, dut, rng, regs, out_ready_p=1.0)
        _cmd(ctx, dut, opcode=CMD_WRITE, address=0x1000, data=0x1234_5678)
        await ctx.tick()
        _cmd(ctx, dut, valid=0)
        p = dut.write_output.payload
        assert ctx.get(dut.write_output.valid) == 1
        assert ctx.get(p.enable_dithering) == 1
        assert ctx.get(p.dither_algorithm) == 1
        assert ctx.get(p.routing.color_base_addr) == 0x123450
        assert ctx.get(p.routing.aux_base_addr) == 0x2ABCD0
        assert ctx.get(p.routing.pixel_stride) == 800
        assert ctx.get(dut.pipeline_output.valid) == 0
        await bench.drain(5)

    run(tb)


def test_pipeline_payload_carries_the_register_state():
    rng = random.Random(4)

    async def tb(ctx, dut):
        regs = Regs(write_format=5, pixel_pipeline_enable=1, za_color=0xCAFE_F00D,
                    fog_color=0x00AABBCC, alpha_mode=0x0F0F0F0F, fog_mode=0x2A,
                    fbz_mode=0x1FFFFF, enable_alpha_planes=1, enable_dithering=1,
                    dither_algorithm=1, write_color_base=0x3210, write_aux_base=0x4321,
                    write_stride=1024)
        bench = Bench(ctx, dut, rng, regs, out_ready_p=1.0)
        _cmd(ctx, dut, opcode=CMD_WRITE, address=0x2000, data=0x8000_0000)
        await ctx.tick()
        _cmd(ctx, dut, valid=0)
        p = dut.pipeline_output.payload
        assert ctx.get(dut.pipeline_output.valid) == 1
        assert ctx.get(dut.write_output.valid) == 0
        assert ctx.get(p.za_color) == 0xCAFE_F00D
        assert ctx.get(p.fog_color) == 0x00AABBCC
        assert ctx.get(p.alpha_mode.as_value()) == 0x0F0F0F0F & ((1 << 32) - 1)
        assert ctx.get(p.fog_mode.as_value()) == 0x2A
        assert ctx.get(p.fbz_mode.as_value()) == 0x1FFFFF
        assert ctx.get(p.routing.color_base_addr) == 0x3210
        assert ctx.get(p.routing.aux_base_addr) == 0x4321
        assert ctx.get(p.routing.pixel_stride) == 1024
        assert ctx.get(p.raw_w) == 0
        assert ctx.get(p.chroma_key) == 0
        assert ctx.get(p.iterated_alpha) == ctx.get(p.alpha) == 0x80
        # depth defaults to zaColor[15:0] << 12 when the format has no depth
        assert ctx.get(p.depth.raw) == (0xF00D << 12)
        await bench.drain(5)

    run(tb)


def test_pipeline_fire_ignores_bypass_ready_and_vice_versa():
    rng = random.Random(5)

    async def tb(ctx, dut):
        for pipeline in (0, 1):
            regs = Regs(write_format=0, pixel_pipeline_enable=pipeline)
            Bench(ctx, dut, rng, regs)
            _cmd(ctx, dut, opcode=CMD_WRITE, address=0, data=0xFFFF_FFFF)
            ctx.set(dut.bus.rsp.ready, 1)
            await ctx.tick()
            _cmd(ctx, dut, valid=0)
            active, other = ((dut.pipeline_output, dut.write_output) if pipeline
                             else (dut.write_output, dut.pipeline_output))
            ctx.set(active.ready, 0)
            ctx.set(other.ready, 1)
            for _ in range(5):
                assert ctx.get(active.valid) == 1
                assert ctx.get(other.valid) == 0
                assert ctx.get(dut.busy) == 1
                assert ctx.get(dut.bus.cmd.ready) == 0
                await ctx.tick()
            ctx.set(active.ready, 1)
            await ctx.tick()   # pixel 1
            assert ctx.get(active.valid) == 1
            assert ctx.get(active.payload.coords.x) == 1
            await ctx.tick()   # pixel 2
            assert ctx.get(active.valid) == 0
            assert ctx.get(dut.busy) == 0
            # the write response was already delivered; idle again
            assert ctx.get(dut.bus.cmd.ready) == 1

    run(tb)


def test_cmd_ready_waits_for_the_response_handshake():
    rng = random.Random(6)

    async def tb(ctx, dut):
        regs = Regs(write_format=4)
        Bench(ctx, dut, rng, regs)
        ctx.set(dut.write_output.ready, 1)
        ctx.set(dut.bus.rsp.ready, 0)
        _cmd(ctx, dut, opcode=CMD_WRITE, address=0x40, data=1, source=9)
        assert ctx.get(dut.bus.cmd.ready) == 1
        await ctx.tick()
        _cmd(ctx, dut, valid=0)
        await ctx.tick()   # the single pixel goes out
        for _ in range(4):
            assert ctx.get(dut.bus.rsp.valid) == 1
            assert ctx.get(dut.bus.rsp.payload.fragment.source) == 9
            assert ctx.get(dut.bus.rsp.payload.fragment.data) == 0
            assert ctx.get(dut.bus.cmd.ready) == 0
            assert ctx.get(dut.busy) == 0
            await ctx.tick()
        ctx.set(dut.bus.rsp.ready, 1)
        await ctx.tick()
        assert ctx.get(dut.bus.rsp.valid) == 0
        assert ctx.get(dut.bus.cmd.ready) == 1

    run(tb)


# ---------------------------------------------------------------------------
# read path
# ---------------------------------------------------------------------------


def _mem_fn(seed):
    rng = random.Random(seed)
    cache = {}

    def mem(addr):
        if addr not in cache:
            cache[addr] = rng.getrandbits(32)
        return cache[addr]

    return mem


@pytest.mark.parametrize("select", [0, 1, 2, 3])
def test_reads(select):
    rng = random.Random(0x5EED + select)
    mem = _mem_fn(select)

    async def tb(ctx, dut):
        for ws, bs in ((0, 0), (1, 0), (0, 1), (1, 1)):
            regs = Regs(word_swap_reads=ws, byte_swizzle_reads=bs, read_buffer_select=select,
                        read_color_base=rng.getrandbits(26), read_aux_base=rng.getrandbits(26),
                        read_stride=rng.choice((640, 800, 1024, 0x7FF)),
                        # the write-side registers must not leak into reads
                        write_format=rng.getrandbits(4), word_swap_writes=rng.getrandbits(1),
                        byte_swizzle_writes=rng.getrandbits(1))
            bench = Bench(ctx, dut, rng, regs, mem=mem)
            want_rsp, want_fetch = [], []
            for _ in range(8):
                address, source = rng.getrandbits(22) & ~1, rng.getrandbits(4)
                await bench.issue(opcode=CMD_READ, address=address, source=source)
                data, fetches = decode_read(regs, address, mem)
                want_rsp.append((source, data))
                want_fetch += fetches
            await bench.drain(60)
            assert bench.fb_fetches == want_fetch
            assert bench.rsps == want_rsp
            assert bench.pixels == []
            assert bench.busy_cycles == 0    # read states never raise busy

    run(tb)


def test_read_waits_for_fifo_empty_and_pipeline_idle():
    rng = random.Random(8)
    mem = _mem_fn(99)

    async def tb(ctx, dut):
        regs = Regs(read_color_base=0x1000, read_stride=640)
        bench = Bench(ctx, dut, rng, regs, mem=mem, fb_cmd_ready_p=1.0)
        _cmd(ctx, dut, opcode=CMD_READ, address=0x2004, source=3)
        ctx.set(dut.pipeline_busy, 1)
        await ctx.tick()
        _cmd(ctx, dut, valid=0)
        for gate in ((1, 1), (0, 0), (0, 1), (1, 1), (1, 1)):
            ctx.set(dut.pci_fifo_empty, gate[0])
            ctx.set(dut.pipeline_busy, gate[1])
            assert ctx.get(dut.fb_read_bus.cmd.valid) == 0
            assert ctx.get(dut.bus.rsp.valid) == 0
            assert ctx.get(dut.bus.cmd.ready) == 0
            assert ctx.get(dut.busy) == 0
            await ctx.tick()
        ctx.set(dut.pci_fifo_empty, 1)
        ctx.set(dut.pipeline_busy, 0)
        await ctx.tick()
        assert ctx.get(dut.fb_read_bus.cmd.valid) == 1
        want, fetches = decode_read(regs, 0x2004, mem)
        assert ctx.get(dut.fb_read_bus.cmd.payload.fragment.address) == fetches[0]
        await bench.drain(40)
        assert bench.fb_fetches == fetches
        assert bench.rsps == [(3, want)]

    run(tb)


def test_read_then_write_then_read_mixed():
    """Random interleaving of the two command kinds under random stalls."""
    rng = random.Random(9)
    mem = _mem_fn(7)

    async def tb(ctx, dut):
        for pipeline in (0, 1):
            regs = Regs(write_format=rng.choice((0, 2, 5, 12, 15)),
                        pixel_pipeline_enable=pipeline, rgba_lanes=rng.getrandbits(2),
                        word_swap_writes=rng.getrandbits(1),
                        byte_swizzle_writes=rng.getrandbits(1),
                        word_swap_reads=rng.getrandbits(1),
                        byte_swizzle_reads=rng.getrandbits(1),
                        read_buffer_select=rng.getrandbits(2),
                        read_color_base=rng.getrandbits(26), read_aux_base=rng.getrandbits(26),
                        read_stride=rng.choice((640, 800, 1024)),
                        enable_alpha_planes=rng.getrandbits(1), za_color=rng.getrandbits(32))
            bench = Bench(ctx, dut, rng, regs, mem=mem, out_ready_p=0.4, rsp_ready_p=0.5)
            want_pix, want_rsp = [], []
            for _ in range(24):
                source = rng.getrandbits(4)
                if rng.random() < 0.5:
                    address, data, mask = (rng.getrandbits(22) & ~3, rng.getrandbits(32),
                                           rng.choice(MASKS))
                    await bench.issue(opcode=CMD_WRITE, address=address, data=data,
                                      mask=mask, source=source)
                    want_pix += _as_seen(decode_write(regs, address, data, mask), pipeline)
                    want_rsp.append((source, 0))
                else:
                    address = rng.getrandbits(22) & ~1
                    await bench.issue(opcode=CMD_READ, address=address, source=source)
                    want_rsp.append((source, decode_read(regs, address, mem)[0]))
            await bench.drain(80)
            assert bench.pixels == want_pix
            assert bench.rsps == want_rsp

    run(tb)
