"""Phase 6 unit tests for :mod:`avoodoo.framebuffer.address_math`.

``FramebufferAddressMath.scala`` has no Scala test of its own (``WriteTest``
covers it at one stride); this sweeps the three functions at the widths the
core uses them with (12-bit ``x``/``y`` from ``PixelCoords``, 11-bit
``pixelStride`` from ``FbRouting``, 26-bit addresses) plus a narrow
configuration where the ``resize`` zero-extension paths matter, against the
exact-integer models in the same module.
"""

import random

from amaranth import Module, Signal
from amaranth.sim import Simulator

from avoodoo.framebuffer.address_math import (mul_by_small_unsigned,
                                              mul_by_small_unsigned_model,
                                              pixel_byte_offset,
                                              pixel_byte_offset_model,
                                              plane_address, plane_address_model)


def _harness(addr_width, xy_width, stride_width, coeff_width=4):
    m = Module()
    base = Signal(addr_width)
    x = Signal(xy_width)
    y = Signal(xy_width)
    stride = Signal(stride_width)
    coeff = Signal(coeff_width)
    mul = Signal(xy_width + coeff_width)
    offset = Signal(addr_width)
    addr = Signal(addr_width)
    m.d.comb += [
        mul.eq(mul_by_small_unsigned(y, coeff)),
        offset.eq(pixel_byte_offset(x, y, stride, addr_width)),
        addr.eq(plane_address(base, x, y, stride)),
    ]
    return m, dict(base=base, x=x, y=y, stride=stride, coeff=coeff,
                   mul=mul, offset=offset, addr=addr)


def _sweep(addr_width, xy_width, stride_width, cases, seed):
    m, s = _harness(addr_width, xy_width, stride_width)
    rng = random.Random(seed)

    async def tb(ctx):
        for _ in range(cases):
            base = rng.getrandbits(addr_width)
            x = rng.getrandbits(xy_width)
            y = rng.getrandbits(xy_width)
            stride = rng.choice([640, 800, 1024, 320, rng.getrandbits(stride_width)])
            stride &= (1 << stride_width) - 1
            coeff = rng.getrandbits(4)
            ctx.set(s["base"], base)
            ctx.set(s["x"], x)
            ctx.set(s["y"], y)
            ctx.set(s["stride"], stride)
            ctx.set(s["coeff"], coeff)
            assert ctx.get(s["mul"]) == mul_by_small_unsigned_model(y, xy_width, coeff, 4)
            assert ctx.get(s["offset"]) == pixel_byte_offset_model(
                x, y, xy_width, stride, addr_width)
            assert ctx.get(s["addr"]) == plane_address_model(
                base, addr_width, x, y, xy_width, stride)
            await ctx.delay(1e-9)

    sim = Simulator(m)
    sim.add_testbench(tb)
    sim.run()


def test_core_widths():
    """26-bit address, 12-bit coordinates, 11-bit stride: the ``Write`` call site."""
    _sweep(26, 12, 11, 2000, seed=1)


def test_narrow_widths_exercise_zero_extension():
    """8-bit address, 4-bit coordinates, 7-bit stride: ``resize`` widens, not narrows."""
    _sweep(8, 4, 7, 2000, seed=2)


def test_known_values():
    """640-pixel stride: row 1 starts at 1280 bytes, pixel x at 2x."""
    assert plane_address_model(0, 26, 0, 1, 12, 640) == 1280
    assert plane_address_model(0x1000, 26, 3, 2, 12, 640) == 0x1000 + 2 * 1280 + 6
    # 15 tiles is the most `tilesX` can express (4 bits); 1024 px -> 16 wraps to 0.
    assert plane_address_model(0, 26, 0, 1, 12, 1024) == 0
    assert plane_address_model(0, 26, 0, 1, 12, 960) == 15 * 128
