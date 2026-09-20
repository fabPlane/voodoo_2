"""Phase 4 unit tests for :mod:`avoodoo.pixel.dither`.

Ports the intent of ``ext/SpinalVoodoo/test/voodoo/DitherTest.scala``: the
three SpinalSim tests there (4x4 exhaustive, 2x2 exhaustive, disabled bypass)
drive one transaction at a time through the handshake and compare against the
``DitherTables`` lookups, so :func:`dither_transaction` below is a
transliteration of the Scala's helper of the same name, down to dropping
``input.valid`` after acceptance (``streamReadSync``'s ``isFree`` would
otherwise re-fire the beat).

Two checks the Scala does not have are added, because the transaction-at-a-
time protocol never exercises the pipeline:

* :func:`test_streaming_full_rate` pushes every 4x4 *and* 2x2 combination
  back to back with ``valid``/``ready`` both stuck high, which is the only
  way the "one result per cycle, read latency 1" claim gets tested;
* :func:`test_random_handshake_matches_model` compares every output every
  cycle against :class:`DitherModel`, a cycle-accurate transliteration of the
  expanded ``streamReadSync``.

The reference values come from :mod:`avoodoo.pixel.dither_tables`, whose own
tests already pin all 10 240 entries against the Scala literals and the 86Box
header, so a failure here is a failure of the *module*, not of the tables.
"""

from __future__ import annotations

import random

import pytest
from amaranth.sim import Simulator

from avoodoo.pixel.dither import Dither
from avoodoo.pixel.dither_tables import (
    g_packed,
    lookup_g,
    lookup_g2x2,
    lookup_rb,
    lookup_rb2x2,
    packed_index,
    rb_packed,
)

PERIOD = 1e-6


# ---------------------------------------------------------------------------
# reference model
# ---------------------------------------------------------------------------


def expected(r, g, b, x, y, enable, use2x2):
    """Transaction-level reference: the Scala test's ``ref*`` expressions."""
    if not enable:
        return (r >> 3, g >> 2, b >> 3)
    if use2x2:
        return (lookup_rb2x2(r, y & 1, x & 1),
                lookup_g2x2(g, y & 1, x & 1),
                lookup_rb2x2(b, y & 1, x & 1))
    return (lookup_rb(r, y, x), lookup_g(g, y, x), lookup_rb(b, y, x))


class DitherModel:
    """Cycle-accurate model of ``Dither.scala``'s expanded ``streamReadSync``.

    State is exactly the five register groups in ``Dither_spinal.v``: the
    three ROM read-data registers (clock-enabled by ``addrStream.fire``),
    ``retValid`` (reset, enabled by ``addrStream.ready``) and the four
    ``linked`` registers (no reset, same enable).
    """

    def __init__(self):
        self.ret_valid = 0
        self.linked = (0, 0, 0, 0)      # enable, trunc_r, trunc_g, trunc_b
        self.rom_r = 0
        self.rom_g = 0
        self.rom_b = 0

    # --- combinational --------------------------------------------------
    def addr_ready(self, out_ready):
        """``addrStream.ready = romRResult.isFree = !valid || ready``."""
        return int((not self.ret_valid) or out_ready)

    def outputs(self, out_ready):
        enable, trunc_r, trunc_g, trunc_b = self.linked
        return {
            "in_ready": self.addr_ready(out_ready),
            "out_valid": self.ret_valid,
            "dit_r": self.rom_r if enable else trunc_r,
            "dit_g": self.rom_g if enable else trunc_g,
            "dit_b": self.rom_b if enable else trunc_b,
        }

    # --- sequential -------------------------------------------------------
    def step(self, in_valid, payload, out_ready):
        r, g, b, x, y, enable, use2x2 = payload
        ready = self.addr_ready(out_ready)
        if in_valid and ready:                       # addrStream.fire
            self.rom_r = rb_packed[packed_index(r, y, x, use2x2)]
            self.rom_g = g_packed[packed_index(g, y, x, use2x2)]
            self.rom_b = rb_packed[packed_index(b, y, x, use2x2)]
        if ready:
            self.linked = (enable, r >> 3, g >> 2, b >> 3)
            self.ret_valid = int(in_valid)


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------


async def set_input(ctx, dut, r, g, b, x, y, enable, use2x2):
    ctx.set(dut.input.payload.r, r)
    ctx.set(dut.input.payload.g, g)
    ctx.set(dut.input.payload.b, b)
    ctx.set(dut.input.payload.x, x)
    ctx.set(dut.input.payload.y, y)
    ctx.set(dut.input.payload.enable, int(enable))
    ctx.set(dut.input.payload.use2x2, int(use2x2))


async def dither_transaction(ctx, dut, r, g, b, x, y, enable, use2x2):
    """``DitherTest.ditherTransaction``: present, wait accepted, wait output."""
    ctx.set(dut.input.valid, 1)
    await set_input(ctx, dut, r, g, b, x, y, enable, use2x2)
    ctx.set(dut.output.ready, 0)

    # waitSamplingWhere(input.ready).  SpinalSim resumes the thread *at* the
    # edge, before the RTL reacts, which is what `ctx.tick().sample()` does
    # too -- the sampled values are the ones the registers see.
    while True:
        _, _, ready = await ctx.tick().sample(dut.input.ready)
        if ready:
            break
    # Input accepted at this edge; deassert to prevent re-fire.
    ctx.set(dut.input.valid, 0)

    ctx.set(dut.output.ready, 1)
    while True:
        _, _, valid, dit_r, dit_g, dit_b = await ctx.tick().sample(
            dut.output.valid, dut.output.payload.dit_r,
            dut.output.payload.dit_g, dut.output.payload.dit_b)
        if valid:
            break
    ctx.set(dut.output.ready, 0)
    return (dit_r, dit_g, dit_b)


def run(dut, testbench):
    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(testbench)
    sim.run()


# ---------------------------------------------------------------------------
# the three DitherTest cases
# ---------------------------------------------------------------------------


def _exhaustive(tile, use2x2):
    dut = Dither()
    mismatches = []

    async def tb(ctx):
        ctx.set(dut.input.valid, 0)
        ctx.set(dut.output.ready, 0)
        await ctx.tick().repeat(3)
        for y in range(tile):
            for x in range(tile):
                for v in range(256):
                    got = await dither_transaction(
                        ctx, dut, v, v, v, x, y, True, use2x2)
                    ref = expected(v, v, v, x, y, True, use2x2)
                    if got != ref:
                        mismatches.append((v, x, y, got, ref))

    run(dut, tb)
    assert not mismatches, (
        f"{tile}x{tile} dither: {len(mismatches)} mismatches, "
        f"first 5 {mismatches[:5]}")


def test_4x4_exhaustive():
    """``test("4x4 dither mode - exhaustive")``: all 4*4*256 cells."""
    _exhaustive(4, use2x2=False)


def test_2x2_exhaustive():
    """``test("2x2 dither mode - exhaustive")``: all 2*2*256 cells."""
    _exhaustive(2, use2x2=True)


def test_disabled_bypass():
    """``test("disabled bypass")``: ``enable`` low truncates, 8:5 / 8:6 / 8:5."""
    dut = Dither()
    values = [0, 1, 7, 8, 127, 128, 248, 255]
    got = []

    async def tb(ctx):
        ctx.set(dut.input.valid, 0)
        ctx.set(dut.output.ready, 0)
        await ctx.tick().repeat(3)
        for v in values:
            got.append(await dither_transaction(
                ctx, dut, v, v, v, 0, 0, False, False))

    run(dut, tb)
    assert got == [(v >> 3, v >> 2, v >> 3) for v in values]


# ---------------------------------------------------------------------------
# pipeline behaviour the Scala test does not reach
# ---------------------------------------------------------------------------


def test_streaming_full_rate():
    """Back-to-back beats: one result per cycle, latency 1, nothing dropped.

    Covers both tables and both channel widths, and mixes ``enable`` so the
    bypass mux is exercised while the ROM pipeline is full -- the case where
    a ``linked`` register on the wrong clock enable would show up.
    """
    dut = Dither()
    stim = []
    for use2x2 in (False, True):
        tile = 2 if use2x2 else 4
        for y in range(tile):
            for x in range(tile):
                for v in range(256):
                    enable = (v + x + y) % 7 != 0
                    stim.append((v, (v * 3) & 0xFF, (v * 5) & 0xFF,
                                 x, y, enable, use2x2))
    got = []
    first_out_cycle = []

    async def tb(ctx):
        ctx.set(dut.input.valid, 0)
        ctx.set(dut.output.ready, 1)
        await ctx.tick().repeat(3)

        ctx.set(dut.input.valid, 1)
        cycle = 0
        for beat in stim:
            await set_input(ctx, dut, *beat)
            _, _, valid, r, g, b, ready = await ctx.tick().sample(
                dut.output.valid, dut.output.payload.dit_r,
                dut.output.payload.dit_g, dut.output.payload.dit_b,
                dut.input.ready)
            assert ready, "full-rate stream stalled"
            if valid:
                got.append((r, g, b))
                if not first_out_cycle:
                    first_out_cycle.append(cycle)
            cycle += 1
        ctx.set(dut.input.valid, 0)
        # Drain the last beat.
        _, _, valid, r, g, b = await ctx.tick().sample(
            dut.output.valid, dut.output.payload.dit_r,
            dut.output.payload.dit_g, dut.output.payload.dit_b)
        if valid:
            got.append((r, g, b))

    run(dut, tb)
    assert first_out_cycle == [1], "read latency is not 1"
    assert got == [expected(*beat) for beat in stim]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_handshake_matches_model(seed):
    """Every port, every cycle, against :class:`DitherModel`.

    ``valid`` is allowed to drop and ``ready`` to stall, which is what pins
    the two different clock enables (``fire`` for the ROMs, ``ready`` for
    ``linked`` / ``retValid``).
    """
    dut = Dither()
    rng = random.Random(seed)
    model = DitherModel()
    errors = []

    async def tb(ctx):
        ctx.set(dut.input.valid, 0)
        ctx.set(dut.output.ready, 0)
        await ctx.tick().repeat(3)

        for cycle in range(4000):
            in_valid = int(rng.random() < 0.7)
            out_ready = int(rng.random() < 0.55)
            payload = (rng.randrange(256), rng.randrange(256),
                       rng.randrange(256), rng.randrange(4), rng.randrange(4),
                       rng.randrange(2), rng.randrange(2))
            ctx.set(dut.input.valid, in_valid)
            ctx.set(dut.output.ready, out_ready)
            await set_input(ctx, dut, *payload)
            # A testbench process resumes only once the design has settled,
            # so these are this cycle's combinational values.
            want = model.outputs(out_ready)
            have = {
                "in_ready": ctx.get(dut.input.ready),
                "out_valid": ctx.get(dut.output.valid),
                "dit_r": ctx.get(dut.output.payload.dit_r),
                "dit_g": ctx.get(dut.output.payload.dit_g),
                "dit_b": ctx.get(dut.output.payload.dit_b),
            }
            if not want["out_valid"]:
                # The ROM read registers have no reset, so their value before
                # the first read is not part of the contract.
                for key in ("dit_r", "dit_g", "dit_b"):
                    want.pop(key)
                    have.pop(key)
            if want != have:
                errors.append((cycle, want, have))
                break
            model.step(in_valid, payload, out_ready)
            await ctx.tick()

    run(dut, tb)
    assert not errors, f"seed {seed}: {errors[:3]}"
