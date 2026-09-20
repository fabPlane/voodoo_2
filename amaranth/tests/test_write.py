"""Phase 3 unit tests for :mod:`avoodoo.pixel.write`.

Ports the intent of ``ext/SpinalVoodoo/test/WriteTest.scala``.  That test was
written when ``Write`` drove a *Bmb* bus (it asserts ``cmd.opcode == 1``,
``cmd.length == 3``, ``cmd.last``, and finishes with a ``BmbMemoryAgent``
round trip); the module now drives a plain
``Stream(FramebufferPlaneBuffer.WriteReq)``, so the Bmb-specific assertions
have no counterpart.  What carries over is every claim about the *payload*,
which is the whole of what ``Write`` computes:

* word-aligned address from ``(x, y, fbBaseAddr, fbPixelStride)``
* the 16-bit pixel placed in the low or high half-word by ``planeAddress[1]``
* the matching byte mask, ``0x3`` low / ``0xC`` high
* a purely combinational handshake -- ``valid`` and ``ready`` pass straight
  through, in both directions

plus a randomised sweep against the Python model of
``FramebufferAddressMath.planeAddress``, which the Scala test only exercised
at one stride.
"""

import random

from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.pixel.write import Write

PERIOD = 1e-6

#: fbiInit1[7:4] = 5 -> 5*128 bytes/row -> 640 pixels/row, the Scala default.
DEFAULT_STRIDE = 640

ADDRESS_MASK = (1 << 26) - 1


# ---------------------------------------------------------------------------
# Python model
# ---------------------------------------------------------------------------


def plane_address(base, x, y, stride=DEFAULT_STRIDE, address_width=26):
    """``FramebufferAddressMath.planeAddress``, width for width.

    Rows are addressed in 128-byte tiles: ``tilesX = (stride >> 6)[3:0]`` and
    ``rowBytes = (y * tilesX) << 7``.  For a stride that is a multiple of 64
    and below 1024 that is just ``y * stride * 2``, which is what the Scala
    test's ``calculateExpectedAddress`` assumes.
    """
    mask = (1 << address_width) - 1
    tiles_x = (stride >> 6) & 0xF
    row_tiles = (y * tiles_x) & ((1 << (12 + 4)) - 1)  # mulBySmallUnsigned width
    row_bytes = (row_tiles << 7) & mask
    x_bytes = (x << 1) & mask
    return (base + row_bytes + x_bytes) & mask


def expected(base, x, y, data16, stride=DEFAULT_STRIDE):
    """The whole ``WriteReq`` the module must produce."""
    addr = plane_address(base, x, y, stride)
    lane_hi = bool(addr & 0x2)
    return {
        "address": addr & ~0x3 & ADDRESS_MASK,
        "data": (data16 << 16) if lane_hi else data16,
        "mask": 0xC if lane_hi else 0x3,
    }


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _drive(ctx, dut, *, valid=1, x=0, y=0, data=0, base=0, stride=DEFAULT_STRIDE,
           ready=1):
    src = dut.i.from_pipeline
    ctx.set(src.valid, valid)
    ctx.set(src.payload.coords.x, x)
    ctx.set(src.payload.coords.y, y)
    ctx.set(src.payload.data, data)
    ctx.set(src.payload.fb_base_addr, base)
    ctx.set(src.payload.fb_pixel_stride, stride)
    ctx.set(dut.o.fb_write.ready, ready)


def _sample(ctx, dut):
    out = dut.o.fb_write
    return {
        "valid": ctx.get(out.valid),
        "address": ctx.get(out.payload.address),
        "data": ctx.get(out.payload.data),
        "mask": ctx.get(out.payload.mask),
        "src_ready": ctx.get(dut.i.from_pipeline.ready),
    }


def run(tb, c=None):
    dut = Write(c if c is not None else Config.voodoo1())
    sim = Simulator(dut)
    # `Write` is purely combinational -- no `sync` domain to clock.
    sim.add_clock(PERIOD, if_exists=True)

    async def bench(ctx):
        await tb(ctx, dut)

    sim.add_testbench(bench)
    sim.run()


# ---------------------------------------------------------------------------
# the WriteTest.scala cases
# ---------------------------------------------------------------------------


def test_converts_pixel_coordinates_to_aligned_address():
    """"Write converts pixel coordinates to aligned command address"."""
    base, x, y, data16 = 0x1000000, 11, 5, 0xABCD
    want = expected(base, x, y, data16)

    async def tb(ctx, dut):
        _drive(ctx, dut, x=x, y=y, data=data16, base=base)
        got = _sample(ctx, dut)
        assert got["valid"] == 1
        assert got["address"] == want["address"], (
            f"expected {want['address']:#08x}, got {got['address']:#08x}")
        assert got["address"] % 4 == 0
        assert got["mask"] == want["mask"]

    run(tb)


def test_handles_stream_handshaking():
    """"Write handles Stream handshaking correctly" -- valid is combinational."""

    async def tb(ctx, dut):
        _drive(ctx, dut, valid=0)
        assert _sample(ctx, dut)["valid"] == 0, "no request while the input is idle"
        _drive(ctx, dut, valid=1, data=0x1234)
        assert _sample(ctx, dut)["valid"] == 1

    run(tb)


def test_places_payload_in_the_correct_lane():
    """"Write places 16-bit payload in the correct BMB lane"."""
    pixels = [(100, 50, 0xBEEF), (101, 50, 0xCAFE)]  # low lane, then high lane

    async def tb(ctx, dut):
        for x, y, data16 in pixels:
            want = expected(0, x, y, data16)
            _drive(ctx, dut, x=x, y=y, data=data16)
            got = _sample(ctx, dut)
            assert got["data"] == want["data"], (
                f"({x},{y}): expected {want['data']:#08x}, got {got['data']:#08x}")
            assert got["mask"] == want["mask"]
        # the two pixels really did land in different lanes
        assert expected(0, 100, 50, 0)["mask"] == 0x3
        assert expected(0, 101, 50, 0)["mask"] == 0xC

    run(tb)


def test_handles_backpressure():
    """"Write handles backpressure from BMB" -- ready passes straight through."""

    async def tb(ctx, dut):
        _drive(ctx, dut, x=50, y=50, data=0x8001, base=0x300000, ready=0)
        got = _sample(ctx, dut)
        assert got["valid"] == 1, "the request stays presented while stalled"
        assert got["src_ready"] == 0, "the input is back-pressured"
        ctx.set(dut.o.fb_write.ready, 1)
        assert _sample(ctx, dut)["src_ready"] == 1, "and recovers at once"

    run(tb)


def test_payload_is_stable_across_a_stall():
    """A stalled beat must not change: the Stream contract the diff relies on."""

    async def tb(ctx, dut):
        want = expected(0x2000000, 7, 9, 0x4321)
        _drive(ctx, dut, x=7, y=9, data=0x4321, base=0x2000000, ready=0)
        first = _sample(ctx, dut)
        for _ in range(4):
            await ctx.delay(PERIOD)
            assert _sample(ctx, dut) == first

        ctx.set(dut.o.fb_write.ready, 1)
        got = _sample(ctx, dut)
        assert (got["address"], got["data"], got["mask"]) == (
            want["address"], want["data"], want["mask"])

    run(tb)


# ---------------------------------------------------------------------------
# randomised sweep against the model
# ---------------------------------------------------------------------------


def test_random_against_model():
    rng = random.Random(0xF06)
    # strides the register bank can actually produce: fbiInit1[7:4] * 128 bytes
    strides = [n * 64 for n in range(1, 16)]
    cases = [
        (
            rng.randrange(1 << 26) & ~0x1,
            rng.randrange(4096),
            rng.randrange(4096),
            rng.randrange(1 << 16),
            rng.choice(strides),
        )
        for _ in range(400)
    ]
    # the corners the random draw is unlikely to hit
    cases += [
        (0, 0, 0, 0x0000, 640),
        (0, 4095, 4095, 0xFFFF, 960),
        ((1 << 26) - 4, 1, 1, 0xFFFF, 64),
        (0x3FFFFE, 0, 0, 0xAAAA, 64),
    ]

    async def tb(ctx, dut):
        for base, x, y, data16, stride in cases:
            want = expected(base, x, y, data16, stride)
            _drive(ctx, dut, x=x, y=y, data=data16, base=base, stride=stride)
            got = _sample(ctx, dut)
            assert (got["address"], got["data"], got["mask"]) == (
                want["address"], want["data"], want["mask"]
            ), f"base={base:#x} x={x} y={y} stride={stride} data={data16:#06x}"

    run(tb)


def test_negative_coordinates_are_reinterpreted_not_sign_extended():
    """``in.coords(0).asUInt`` -- an off-screen negative x wraps, it does not
    sign-extend into the row address."""

    async def tb(ctx, dut):
        _drive(ctx, dut, x=-1, y=0, data=0x1111, base=0)
        got = _sample(ctx, dut)
        want = expected(0, 0xFFF, 0, 0x1111)  # SInt(12) -1 read as 4095
        assert got["address"] == want["address"]
        assert got["mask"] == want["mask"]

    run(tb)
