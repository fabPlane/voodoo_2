"""Phase 3 unit tests for the three fastfill modules.

:mod:`avoodoo.raster.fastfill`, :mod:`avoodoo.raster.fastfill_write` and
:mod:`avoodoo.raster.fastfill_word_writer` -- the ports of
``ext/SpinalVoodoo/src/voodoo/raster/Fastfill{,Write,WordWriter}.scala``.

Upstream has no ``FastfillTest.scala``; the modules are covered there only
through the Glide-level tests (``test04`` renders the fastfill stage).  What
is checked here, against small transaction-level Python models:

* **Fastfill** -- the pixel order over the clip rectangle (left to right,
  top to bottom, ``[clipLeft, clipRight) x [clipLowY, clipHighY)``), the
  one-beat ``running`` pulse and *no* output for an empty rectangle, the
  ``i.ready == !running`` / ``o.valid -> running`` invariants under random
  ``o.ready`` back-pressure, and that the clip inputs may change freely
  once the command has fired (they are latched into the loop state).
* **FastfillWrite** -- ``color1`` -> RGB, ``zaColor`` -> ``depthAlpha``,
  the ``yOrigin`` flip ``yOriginSwapValue - y`` at 12 bits, and the
  capture-on-``cmdFire`` behaviour: while ``running`` the *captured*
  registers drive the output even though ``io.regs`` has moved on, and the
  live ones come back the cycle after ``running`` drops.
* **FastfillWordWriter** -- the word sequence (even-aligned start, two
  pixels a beat, lane enables at the clip edges), the dithered 5-6-5
  colour word and the ``depthAlpha`` aux word, the plane addresses through
  ``FramebufferAddressMath.planeAddress``, the ``rgbBufferMask`` /
  ``auxBufferMask`` gating, ``generatedPixels`` / ``colorWrittenPixels``
  totals, and the drain: writes keep coming after ``running`` has dropped,
  in order, under random back-pressure on both write streams.  Plus the
  ``cmd.ready == !running`` / ``wordValid == running`` invariants and the
  Stream payload-hold contract on both outputs.
* a ``Reg``-vs-``RegInit`` audit of every register the three modules
  declare (plan §6: plain Spinal ``Reg`` must be ``reset_less``).

The cycle-exact proof is the lockstep diff (``tests/diff/test_diff_fastfill.py``
and ``make diff MOD=Fastfill{,Write,WordWriter}``); these tests pin the
*intent* so a diff failure can be read.
"""

from __future__ import annotations

import random

import pytest
from amaranth.hdl import Fragment
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.pixel.dither_tables import lookup_g, lookup_g2x2, lookup_rb, lookup_rb2x2
from avoodoo.raster.fastfill import Fastfill
from avoodoo.raster.fastfill_word_writer import FastfillWordWriter
from avoodoo.raster.fastfill_write import FastfillWrite

PERIOD = 1e-6
CFG = Config.voodoo1()
ADDRESS_MASK = (1 << CFG.address_width) - 1
COORD_MASK = (1 << CFG.vertex_format.non_fraction) - 1


# ---------------------------------------------------------------------------
# Python models
# ---------------------------------------------------------------------------


def fill_order(left, right, low_y, high_y):
    """``Fastfill``: the pixel sequence for one command (empty -> ``[]``)."""
    if right <= left or high_y <= low_y:
        return []
    return [(x, y) for y in range(low_y, high_y) for x in range(left, right)]


def to_s12(v):
    """A 12-bit two's-complement pattern as a Python int."""
    v &= COORD_MASK
    return v - (1 << 12) if v & (1 << 11) else v


def transformed_y(regs, y):
    """``FastfillWordWriter.transformedY`` / ``FastfillWrite.outputY`` (12-bit)."""
    if regs["y_origin"]:
        return (regs["y_origin_swap_value"] - y) & COORD_MASK
    return y


def plane_address(base, x, y, stride):
    """``FramebufferAddressMath.planeAddress`` (as in ``tests/test_write.py``)."""
    tiles_x = (stride >> 6) & 0xF
    row_tiles = (y * tiles_x) & ((1 << (CFG.address_width + 4)) - 1)
    row_bytes = (row_tiles << 7) & ADDRESS_MASK
    x_bytes = (x << 1) & ADDRESS_MASK
    return (base + row_bytes + x_bytes) & ADDRESS_MASK


def dither565(color1, x, y, enable, use2x2):
    """``Dither`` at ``(x, y)`` of ``color1`` -> the packed ``ditR ## ditG ## ditB``."""
    r, g, b = (color1 >> 16) & 0xFF, (color1 >> 8) & 0xFF, color1 & 0xFF
    if not enable:
        dr, dg, db = r >> 3, g >> 2, b >> 3
    elif use2x2:
        dr = lookup_rb2x2(r, y & 1, x & 1)
        dg = lookup_g2x2(g, y & 1, x & 1)
        db = lookup_rb2x2(b, y & 1, x & 1)
    else:
        dr = lookup_rb(r, y & 3, x & 3)
        dg = lookup_g(g, y & 3, x & 3)
        db = lookup_rb(b, y & 3, x & 3)
    return (dr << 11) | (dg << 5) | db


def word_writes(regs, left, right, low_y, high_y):
    """``FastfillWordWriter``: ``(color_writes, aux_writes, pixels)`` for one command.

    Each write is ``(address, data, mask)`` in issue order.  ``pixels`` is the
    number of enabled lanes over the walk, i.e. the rectangle's pixel count.
    """
    color, aux, pixels = [], [], 0
    if right <= left or high_y <= low_y:
        return color, aux, pixels
    aligned_left = left & ~1
    for y in range(low_y, high_y):
        ty = transformed_y(regs, y)
        x = aligned_left
        while x < right:
            lane0 = left <= x < right
            lane1 = left <= x + 1 < right
            pixels += int(lane0) + int(lane1)
            mask = (0b11 if lane0 else 0) | (0b1100 if lane1 else 0)
            if lane0 or lane1:
                if regs["rgb_write"]:
                    lo = dither565(regs["color1"], x, ty, regs["enable_dithering"],
                                   regs["use2x2"]) if lane0 else 0
                    hi = dither565(regs["color1"], x + 1, ty, regs["enable_dithering"],
                                   regs["use2x2"]) if lane1 else 0
                    addr = plane_address(regs["color_base"], x, ty, regs["stride"])
                    color.append((addr & ~3, (hi << 16) | lo, mask))
                if regs["aux_write"]:
                    da = regs["za_color"] & 0xFFFF
                    data = (da if lane0 else 0) | ((da << 16) if lane1 else 0)
                    addr = plane_address(regs["aux_base"], x, ty, regs["stride"])
                    aux.append((addr & ~3, data, mask))
            x += 2
    return color, aux, pixels


def random_regs(rng, **force):
    regs = {
        "color1": rng.getrandbits(32),
        "za_color": rng.getrandbits(32),
        "enable_dithering": rng.randrange(2),
        "use2x2": rng.randrange(2),
        "rgb_write": rng.randrange(2),
        "aux_write": rng.randrange(2),
        "y_origin": rng.randrange(2),
        "y_origin_swap_value": rng.randrange(1024),
        "color_base": rng.getrandbits(CFG.address_width),
        "aux_base": rng.getrandbits(CFG.address_width),
        "stride": rng.choice([640, 800, 1024, rng.randrange(2048)]),
        "fbz_junk": rng.getrandbits(21),
    }
    regs.update(force)
    return regs


def set_regs(ctx, port, regs):
    """Drive a ``FastfillWriteRegs`` port from a model dict.

    The FbzMode bits the modules do not read are randomised too
    (``fbz_junk``) so a wrong field slice would show.
    """
    fbz = port.fbz_mode
    ctx.set(port.color1, regs["color1"])
    ctx.set(port.za_color, regs["za_color"])
    ctx.set(fbz.as_value(), regs["fbz_junk"])
    ctx.set(fbz.enable_dithering, regs["enable_dithering"])
    ctx.set(fbz.dither_algorithm, regs["use2x2"])
    ctx.set(fbz.rgb_buffer_mask, regs["rgb_write"])
    ctx.set(fbz.aux_buffer_mask, regs["aux_write"])
    ctx.set(fbz.y_origin, regs["y_origin"])
    ctx.set(port.routing.color_base_addr, regs["color_base"])
    ctx.set(port.routing.aux_base_addr, regs["aux_base"])
    ctx.set(port.routing.pixel_stride, regs["stride"])
    ctx.set(port.y_origin_swap_value, regs["y_origin_swap_value"])


def run(dut, tb):
    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()


# ---------------------------------------------------------------------------
# Fastfill
# ---------------------------------------------------------------------------


RECTS = [
    (0, 1, 0, 1),          # one pixel
    (3, 9, 2, 5),          # small, odd left
    (10, 12, 7, 8),        # one row
    (5, 6, 0, 4),          # one column
    (7, 7, 0, 4),          # empty: zero width
    (0, 8, 4, 4),          # empty: zero height
    (9, 3, 0, 4),          # empty: inverted
    (1016, 1023, 1020, 1023),  # near the 10-bit top: the +1 compares must not wrap
]


@pytest.mark.parametrize("rect", RECTS)
@pytest.mark.parametrize("ready_density", [1.0, 0.4])
def test_fastfill_pixel_order(rect, ready_density):
    dut = Fastfill(CFG)
    rng = random.Random(hash((rect, ready_density)) & 0xFFFF)
    left, right, low_y, high_y = rect
    want = fill_order(*rect)
    got = []
    running_cycles = 0

    async def tb(ctx):
        ctx.set(dut.i.valid, 0)
        ctx.set(dut.o.ready, 0)
        ctx.set(dut.clip_left, left)
        ctx.set(dut.clip_right, right)
        ctx.set(dut.clip_low_y, low_y)
        ctx.set(dut.clip_high_y, high_y)
        await ctx.tick().repeat(2)
        assert ctx.get(dut.running) == 0
        assert ctx.get(dut.i.ready) == 1

        ctx.set(dut.i.valid, 1)
        await ctx.tick()
        ctx.set(dut.i.valid, 0)
        # The loop state is latched: the live clip inputs are free from here.
        ctx.set(dut.clip_left, 0)
        ctx.set(dut.clip_right, 1023)
        ctx.set(dut.clip_low_y, 0)
        ctx.set(dut.clip_high_y, 1023)

        nonlocal running_cycles
        budget = len(want) * 4 + 20
        for _ in range(budget):
            ready = int(rng.random() < ready_density)
            ctx.set(dut.o.ready, ready)
            running = ctx.get(dut.running)
            valid = ctx.get(dut.o.valid)
            # invariants (StreamWhile: trigger.ready == !running, output.valid == running)
            assert ctx.get(dut.i.ready) == (not running)
            assert not (valid and not running)
            if running:
                running_cycles += 1
            if valid and ready:
                got.append((to_s12(ctx.get(dut.o.payload.coords[0])),
                            to_s12(ctx.get(dut.o.payload.coords[1]))))
            await ctx.tick()
            if not ctx.get(dut.running) and len(got) == len(want):
                break
        await ctx.tick().repeat(2)
        assert ctx.get(dut.running) == 0
        assert ctx.get(dut.o.valid) == 0

    run(dut, tb)
    assert got == want
    if not want:
        # empty rectangle: exactly one loop beat, dropped by the takeWhen
        assert running_cycles == 1


def test_fastfill_back_to_back_commands():
    """``i.ready`` returns the cycle after the last pixel; a second command
    then starts a fresh walk with the *new* clip window."""
    dut = Fastfill(CFG)
    rects = [(2, 4, 0, 2), (0, 3, 5, 6)]
    want = fill_order(*rects[0]) + fill_order(*rects[1])
    got = []

    async def tb(ctx):
        ctx.set(dut.o.ready, 1)
        for rect in rects:
            ctx.set(dut.clip_left, rect[0])
            ctx.set(dut.clip_right, rect[1])
            ctx.set(dut.clip_low_y, rect[2])
            ctx.set(dut.clip_high_y, rect[3])
            ctx.set(dut.i.valid, 1)
            while True:
                _, _, ready = await ctx.tick().sample(dut.i.ready)
                if ready:
                    break
            ctx.set(dut.i.valid, 0)
            while ctx.get(dut.running):
                if ctx.get(dut.o.valid):
                    got.append((ctx.get(dut.o.payload.coords[0]),
                                ctx.get(dut.o.payload.coords[1])))
                await ctx.tick()

    run(dut, tb)
    assert got == want


# ---------------------------------------------------------------------------
# FastfillWrite
# ---------------------------------------------------------------------------


def pre_dither_expected(regs, x, y):
    c1 = regs["color1"]
    return {
        "r": (c1 >> 16) & 0xFF, "g": (c1 >> 8) & 0xFF, "b": c1 & 0xFF,
        "x": x & COORD_MASK, "y": transformed_y(regs, y),
        "enable_dithering": regs["enable_dithering"],
        "dither_algorithm": regs["use2x2"],
        "depth_alpha": regs["za_color"] & 0xFFFF,
        "rgb_write": regs["rgb_write"], "aux_write": regs["aux_write"],
        "color_base": regs["color_base"], "aux_base": regs["aux_base"],
        "stride": regs["stride"],
    }


def sample_pre_dither(ctx, out):
    p = out.payload
    return {
        "r": ctx.get(p.r), "g": ctx.get(p.g), "b": ctx.get(p.b),
        "x": ctx.get(p.coords.x) & COORD_MASK, "y": ctx.get(p.coords.y) & COORD_MASK,
        "enable_dithering": ctx.get(p.enable_dithering),
        "dither_algorithm": ctx.get(p.dither_algorithm),
        "depth_alpha": ctx.get(p.depth_alpha),
        "rgb_write": ctx.get(p.rgb_write), "aux_write": ctx.get(p.aux_write),
        "color_base": ctx.get(p.routing.color_base_addr),
        "aux_base": ctx.get(p.routing.aux_base_addr),
        "stride": ctx.get(p.routing.pixel_stride),
    }


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_fastfill_write_payload_and_handshake(seed):
    """Live registers, no command: a pure ``translateFrom``."""
    dut = FastfillWrite(CFG)
    rng = random.Random(seed)

    async def tb(ctx):
        ctx.set(dut.cmd_fire, 0)
        ctx.set(dut.running, 0)
        await ctx.tick()
        for _ in range(200):
            regs = random_regs(rng)
            x, y = rng.randrange(1024), rng.randrange(1024)
            valid, ready = rng.randrange(2), rng.randrange(2)
            set_regs(ctx, dut.regs, regs)
            ctx.set(dut.pixels.payload.coords[0], x)
            ctx.set(dut.pixels.payload.coords[1], y)
            ctx.set(dut.pixels.valid, valid)
            ctx.set(dut.output.ready, ready)
            assert ctx.get(dut.output.valid) == valid
            assert ctx.get(dut.pixels.ready) == ready
            assert sample_pre_dither(ctx, dut.output) == pre_dither_expected(regs, x, y)
            await ctx.tick()

    run(dut, tb)


def test_fastfill_write_captures_on_cmd_fire():
    """The Scala's reason to exist: registers latched on ``cmdFire`` hold
    while ``running``, and the live ones return once it drops."""
    dut = FastfillWrite(CFG)
    rng = random.Random(7)
    a = random_regs(rng, y_origin=1, y_origin_swap_value=479)
    b = random_regs(rng, y_origin=0)
    x, y = 100, 20

    async def tb(ctx):
        ctx.set(dut.pixels.valid, 1)
        ctx.set(dut.output.ready, 1)
        ctx.set(dut.pixels.payload.coords[0], x)
        ctx.set(dut.pixels.payload.coords[1], y)
        ctx.set(dut.cmd_fire, 0)
        ctx.set(dut.running, 0)
        set_regs(ctx, dut.regs, a)
        await ctx.tick()

        # the fire cycle: live regs (A) are what the output shows
        ctx.set(dut.cmd_fire, 1)
        ctx.set(dut.running, 1)
        assert sample_pre_dither(ctx, dut.output) == pre_dither_expected(a, x, y)
        await ctx.tick()
        ctx.set(dut.cmd_fire, 0)

        # register bank moves on to B while the fill is running: output stays A
        set_regs(ctx, dut.regs, b)
        for _ in range(5):
            assert sample_pre_dither(ctx, dut.output) == pre_dither_expected(a, x, y)
            await ctx.tick()

        # running drops: ffActive clears on the next edge, then B is live
        ctx.set(dut.running, 0)
        assert sample_pre_dither(ctx, dut.output) == pre_dither_expected(a, x, y)
        await ctx.tick()
        assert sample_pre_dither(ctx, dut.output) == pre_dither_expected(b, x, y)

    run(dut, tb)


def test_fastfill_write_y_origin_flip_is_12_bit():
    """``yOriginSwapValue - y`` at ``nonFraction`` bits, no growth: a
    negative result wraps to the 12-bit pattern, and a swap value above the
    signed range of 12 bits is zero-extended first."""
    dut = FastfillWrite(CFG)
    cases = [(479, 0, 479), (479, 479, 0), (10, 20, -10 & COORD_MASK),
             (1023, 0, 1023), (0, 1023, -1023 & COORD_MASK)]

    async def tb(ctx):
        ctx.set(dut.cmd_fire, 0)
        ctx.set(dut.running, 0)
        set_regs(ctx, dut.regs, random_regs(random.Random(0), y_origin=1))
        for swap, y, want in cases:
            ctx.set(dut.regs.y_origin_swap_value, swap)
            ctx.set(dut.pixels.payload.coords[1], y)
            assert ctx.get(dut.output.payload.coords.y) & COORD_MASK == want
            await ctx.tick()

    run(dut, tb)


# ---------------------------------------------------------------------------
# FastfillWordWriter
# ---------------------------------------------------------------------------


WW_RECTS = [
    (0, 2, 0, 1),        # one word
    (3, 8, 2, 4),        # odd left: first word has lane 0 off
    (4, 9, 0, 2),        # odd right: last word has lane 1 off
    (1, 2, 5, 6),        # single pixel at odd x: one word, lane 1 only
    (6, 6, 0, 3),        # empty
    (7, 12, 1, 4),
]


def _sample_req(ctx, s):
    return (ctx.get(s.payload.address), ctx.get(s.payload.data), ctx.get(s.payload.mask))


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
@pytest.mark.parametrize("rect", WW_RECTS)
def test_word_writer_matches_model(seed, rect):
    dut = FastfillWordWriter(CFG)
    rng = random.Random(seed * 100 + hash(rect) % 100)
    regs = random_regs(rng)
    # a large seed-dependent share of runs with both planes on, so the empty
    # cases do not dominate
    if seed % 2:
        regs["rgb_write"] = regs["aux_write"] = 1
    left, right, low_y, high_y = rect
    want_color, want_aux, want_pixels = word_writes(regs, *rect)
    ready_density = [1.0, 0.5, 0.2, 0.7][seed - 1]

    got_color, got_aux = [], []
    stats = {"generated": 0, "written": 0, "drain_writes": 0, "running_cycles": 0}

    async def tb(ctx):
        ctx.set(dut.cmd.valid, 0)
        ctx.set(dut.color_write.ready, 0)
        ctx.set(dut.aux_write.ready, 0)
        set_regs(ctx, dut.regs, regs)
        ctx.set(dut.clip_left, left)
        ctx.set(dut.clip_right, right)
        ctx.set(dut.clip_low_y, low_y)
        ctx.set(dut.clip_high_y, high_y)
        await ctx.tick().repeat(2)
        assert ctx.get(dut.cmd.ready) == 1 and ctx.get(dut.running) == 0

        ctx.set(dut.cmd.valid, 1)
        await ctx.tick()
        ctx.set(dut.cmd.valid, 0)
        # everything is captured: scramble the live inputs
        set_regs(ctx, dut.regs, random_regs(rng))
        ctx.set(dut.clip_left, 0)
        ctx.set(dut.clip_right, 1023)
        ctx.set(dut.clip_low_y, 0)
        ctx.set(dut.clip_high_y, 1023)

        held = {"color": None, "aux": None}
        idle = 0
        # The synchronous fork drops *both* valids until both sinks are ready
        # (P = density**2 per cycle), so a word can sit for many cycles with
        # nothing visible at the ports: the idle window has to be wide.
        for _ in range(300 * (len(want_color) + len(want_aux)) + 600):
            c_ready = int(rng.random() < ready_density)
            a_ready = int(rng.random() < ready_density)
            ctx.set(dut.color_write.ready, c_ready)
            ctx.set(dut.aux_write.ready, a_ready)

            running = ctx.get(dut.running)
            stats["running_cycles"] += running
            assert ctx.get(dut.cmd.ready) == (not running)
            assert ctx.get(dut.word_valid) == running
            stats["generated"] += ctx.get(dut.generated_pixels)
            stats["written"] += ctx.get(dut.color_written_pixels)

            fired = False
            for name, s, sink, ready in (("color", dut.color_write, got_color, c_ready),
                                         ("aux", dut.aux_write, got_aux, a_ready)):
                valid = ctx.get(s.valid)
                req = _sample_req(ctx, s)
                # Stream contract on the payload: what was offered and not
                # taken is still there next cycle (valid itself may drop --
                # the synchronous fork lets it, and the Scala relies on it).
                if held[name] is not None:
                    assert req == held[name], f"{name} payload changed while stalled"
                if valid and ready:
                    sink.append(req)
                    held[name] = None
                    fired = True
                    if not running:
                        stats["drain_writes"] += 1
                elif valid:
                    held[name] = req
            idle = 0 if (running or fired or ctx.get(dut.color_write.valid)
                         or ctx.get(dut.aux_write.valid)) else idle + 1
            await ctx.tick()
            if idle > 400:
                break

        assert ctx.get(dut.running) == 0
        assert ctx.get(dut.color_write.valid) == 0 and ctx.get(dut.aux_write.valid) == 0

    run(dut, tb)
    assert got_color == want_color
    assert got_aux == want_aux
    assert stats["generated"] == want_pixels
    assert stats["written"] == (want_pixels if regs["rgb_write"] else 0)
    if want_color or want_aux:
        # the last word is still in the dither / meta / halfPipe stages when
        # `active` drops, so at least one write lands after `running`
        assert stats["drain_writes"] >= 1
    if not want_pixels:
        # empty rectangle: `active := !emptyRect` never sets
        assert stats["running_cycles"] == 0


def test_word_writer_back_to_back_commands():
    """A second command while the first is draining: ``cmd.ready`` comes back
    with ``active``, and the two write sequences arrive in order."""
    dut = FastfillWordWriter(CFG)
    rng = random.Random(11)
    regs = random_regs(rng, rgb_write=1, aux_write=1, y_origin=0)
    rects = [(0, 4, 0, 2), (3, 7, 1, 3)]
    want_color, want_aux = [], []
    for r in rects:
        c, a, _ = word_writes(regs, *r)
        want_color += c
        want_aux += a
    got_color, got_aux = [], []

    async def tb(ctx):
        set_regs(ctx, dut.regs, regs)
        ctx.set(dut.color_write.ready, 1)
        ctx.set(dut.aux_write.ready, 1)
        for rect in rects:
            ctx.set(dut.clip_left, rect[0])
            ctx.set(dut.clip_right, rect[1])
            ctx.set(dut.clip_low_y, rect[2])
            ctx.set(dut.clip_high_y, rect[3])
            ctx.set(dut.cmd.valid, 1)
            while True:
                _, _, ready, cv, cr, av, ar = await ctx.tick().sample(
                    dut.cmd.ready, dut.color_write.valid, dut.color_write.payload,
                    dut.aux_write.valid, dut.aux_write.payload)
                if cv:
                    got_color.append((cr.address, cr.data, cr.mask))
                if av:
                    got_aux.append((ar.address, ar.data, ar.mask))
                if ready:
                    break
            ctx.set(dut.cmd.valid, 0)
        # halfPipe: one word every other cycle, so the drain of the second
        # rectangle's six words takes a dozen cycles on its own
        for _ in range(40):
            _, _, cv, cr, av, ar = await ctx.tick().sample(
                dut.color_write.valid, dut.color_write.payload,
                dut.aux_write.valid, dut.aux_write.payload)
            if cv:
                got_color.append((cr.address, cr.data, cr.mask))
            if av:
                got_aux.append((ar.address, ar.data, ar.mask))

    run(dut, tb)
    assert got_color == want_color
    assert got_aux == want_aux


# ---------------------------------------------------------------------------
# Reg vs RegInit audit (plan §6)
# ---------------------------------------------------------------------------


def _registers(frag, prefix=""):
    out = {}
    for domain, stmts in frag.statements.items():
        if domain == "comb":
            continue
        for sig in stmts._lhs_signals():
            out.setdefault(prefix + sig.name, sig)
    for sub, name, _src in frag.subfragments:
        out.update(_registers(sub, f"{prefix}{name}."))
    return out


@pytest.mark.parametrize("cls, reset_less, reset", [
    (Fastfill, ["i_streamWhile.state"], ["i_streamWhile.running", "i_streamWhile.counter_value"]),
    (FastfillWrite, ["captured"], ["ffActive"]),
    (FastfillWordWriter,
     ["regsCapture", "clipLeftCapture", "clipRightCapture", "clipLowYCapture",
      "clipHighYCapture", "metaPipe.rData", "ditherJoined.rData"],
     ["active", "curWordX", "curY", "metaPipe.rValid", "ditherJoined.rValid"]),
])
def test_reset_less_registers_match_spinal(cls, reset_less, reset):
    regs = _registers(Fragment.get(cls(CFG), None))
    for name in reset_less:
        assert name in regs, f"{name} not found; have {sorted(regs)}"
        assert regs[name].reset_less, f"{name} is a plain Spinal Reg: must be reset_less"
    for name in reset:
        assert name in regs, f"{name} not found; have {sorted(regs)}"
        assert not regs[name].reset_less, f"{name} is a Spinal RegInit: must reset"
