"""Phase 5 unit tests for :mod:`avoodoo.texture.tmu_texel_decoder`.

Ports the intent of ``ext/SpinalVoodoo/test/TmuTexelDecoderTest.scala`` -- the
point-sampled RGB565 case and the bilinear RGB565 blend -- and goes well past
it, because the Scala test covers two of the thirteen texture formats and
asserts the blended channels only with ``>= 120``.  (It is also stale: it
drives ``passthrough.ncc``, ``readIdx``, ``requestId`` and ``sendConfig``
fields that ``TmuPassthrough`` no longer has, so it cannot compile against
today's Scala.  The RGB565 numbers are still the right expectations and are
pinned exactly here.)

What is added:

* an independent Python model (:func:`decode_texel`, :func:`blend`,
  :func:`weights`) written from ``TmuTexelDecoder.scala`` /
  ``Bundles.expandTo8`` / ``Tmu.blendChannel``, not from the Amaranth port;
* every format, including the three unassigned codes that fall through to
  RGB565, and the NCC path with saturating I/Q offsets on both tables;
* the palette RAM's write-then-read ordering and its read-before-write
  behaviour on a same-cycle collision;
* the three-cycle latency, back-pressure, and ``sendConfig``;
* a randomised sweep of 400 requests against the model.

``pysim`` is fine here: the module is three registered stages and the diff
harness (``tests/diff/test_diff_tmu_texel_decoder.py``) does the cycle-exact
comparison against the Spinal Verilog.
"""

import random

import pytest
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.texture.tmu_texel_decoder import TmuTexelDecoder
from avoodoo.texture.tmu_types import TextureFormat as TF

PERIOD = 1e-6

#: Three ``m2sPipe`` stages between ``sampleFetch`` and ``output``.
LATENCY = 3


# ---------------------------------------------------------------------------
# Python model -- written from the Scala, independently of the port
# ---------------------------------------------------------------------------


def expand_to8(v: int, src_width: int) -> int:
    """``Bundles.expandTo8``: replicate the MSBs into the padding."""
    assert 1 <= src_width <= 8
    v &= (1 << src_width) - 1
    if src_width == 8:
        return v
    if src_width == 1:
        return 0xFF if v else 0x00
    reps = -(-8 // src_width)
    wide = 0
    for _ in range(reps):
        wide = (wide << src_width) | v
    return (wide >> (reps * src_width - 8)) & 0xFF


def clamp_to_u8(v: int) -> int:
    """``Bundles.clampToU8`` on an already-signed Python int."""
    if v < 0:
        return 0
    if v > 255:
        return 255
    return v & 0xFF


def _s(value: int, width: int) -> int:
    """Interpret ``value``'s low ``width`` bits as two's complement."""
    value &= (1 << width) - 1
    return value - (1 << width) if value >> (width - 1) else value


def ncc_decode(tex_byte: int, ncc: dict) -> tuple[int, int, int]:
    """``nccDecode``: Y from 4 bits, I from 2, Q from 2, three 11-bit adds."""
    y = ncc["y"][(tex_byte >> 4) & 0xF]
    i_entry = ncc["i"][(tex_byte >> 2) & 0x3]
    q_entry = ncc["q"][tex_byte & 0x3]
    out = []
    for lo in (18, 9, 0):
        i_c = _s(i_entry >> lo, 9)
        q_c = _s(q_entry >> lo, 9)
        out.append(clamp_to_u8(_s(y + i_c + q_c, 11)))
    return tuple(out)


def decode_texel(word: int, fmt: int, ncc: dict, palette: int) -> tuple[int, int, int, int]:
    """``decodeTexelWord`` -> ``(r, g, b, a)``."""
    word &= 0xFFFF
    byte = word & 0xFF
    hi = (word >> 8) & 0xFF
    pr, pg, pb = (palette >> 16) & 0xFF, (palette >> 8) & 0xFF, palette & 0xFF

    if fmt == TF.RGB332:
        return (expand_to8(byte >> 5, 3), expand_to8((byte >> 2) & 0x7, 3),
                expand_to8(byte & 0x3, 2), 255)
    if fmt == TF.YIQ422:
        return ncc_decode(byte, ncc) + (255,)
    if fmt == TF.A8:
        return (byte, byte, byte, byte)
    if fmt == TF.I8:
        return (byte, byte, byte, 255)
    if fmt == TF.AI44:
        i = expand_to8(byte & 0xF, 4)
        return (i, i, i, expand_to8(byte >> 4, 4))
    if fmt == TF.P8:
        return (pr, pg, pb, 255)
    if fmt == TF.ARGB8332:
        return (expand_to8(byte >> 5, 3), expand_to8((byte >> 2) & 0x7, 3),
                expand_to8(byte & 0x3, 2), hi)
    if fmt == TF.AYIQ8422:
        return ncc_decode(byte, ncc) + (hi,)
    if fmt == TF.ARGB1555:
        return (expand_to8((word >> 10) & 0x1F, 5), expand_to8((word >> 5) & 0x1F, 5),
                expand_to8(word & 0x1F, 5), expand_to8(word >> 15, 1))
    if fmt == TF.ARGB4444:
        return (expand_to8((word >> 8) & 0xF, 4), expand_to8((word >> 4) & 0xF, 4),
                expand_to8(word & 0xF, 4), expand_to8(word >> 12, 4))
    if fmt == TF.AI88:
        return (byte, byte, byte, hi)
    if fmt == TF.AP88:
        return (pr, pg, pb, hi)
    # RGB565 and the unassigned codes 6, 7, 15 (the Scala's `default`).
    return (expand_to8(word >> 11, 5), expand_to8((word >> 5) & 0x3F, 6),
            expand_to8(word & 0x1F, 5), 255)


def weights(ds: int, dt: int) -> tuple[int, int, int, int]:
    """``Tmu.bilinearWeights``; the 5-bit subtraction cannot wrap for 4-bit ds."""
    ds5, dt5 = ds & 0x1F, dt & 0x1F
    inv_ds, inv_dt = (16 - ds5) & 0x1F, (16 - dt5) & 0x1F
    return ((inv_ds * inv_dt) & 0x3FF, (ds5 * inv_dt) & 0x3FF,
            (inv_ds * dt5) & 0x3FF, (ds5 * dt5) & 0x3FF)


def blend(w: tuple[int, int, int, int], taps: tuple[int, int, int, int]) -> int:
    """``Tmu.blendChannel``: 28-bit sum, then ``sum(15 downto 8)``."""
    total = sum(t * wi for t, wi in zip(taps, w)) & ((1 << 28) - 1)
    return (total >> 8) & 0xFF


def model(sample, ncc_tables, palettes, send_config=False):
    """Whole-module model for one accepted ``sampleFetch``.

    ``palettes`` is the four taps' palette words as they were *before* the
    fetch fired (read-before-write), already looked up by ``texels(i)[7:0]``.
    """
    pt = sample["passthrough"]
    ncc = ncc_tables[pt["ncc_table_select"]]
    decoded = [decode_texel(sample["texels"][i], pt["format"], ncc, palettes[i])
               for i in range(4)]
    w = weights(pt["ds"], pt["dt"])
    out = []
    for ch in range(4):
        taps = tuple(decoded[i][ch] for i in range(4))
        out.append(blend(w, taps) if sample["bilinear"] else taps[0])
    if send_config:
        return (0, 0, 1, 255)
    return tuple(out)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

ZERO_NCC = {"y": [0] * 16, "i": [0] * 4, "q": [0] * 4}


def ncc_bits(table: dict) -> int:
    """Pack an NCC table dict into the ``NccTableData`` word (y, then i, then q)."""
    word = 0
    for k, v in enumerate(table["y"]):
        word |= (v & 0xFF) << (8 * k)
    base = 128
    for k, v in enumerate(table["i"]):
        word |= (v & 0x7FFFFFF) << (base + 27 * k)
    base += 108
    for k, v in enumerate(table["q"]):
        word |= (v & 0x7FFFFFF) << (base + 27 * k)
    return word


def ncc_entry(r: int, g: int, b: int) -> int:
    """Pack a 27-bit I/Q entry: ``[26:18]=R [17:9]=G [8:0]=B``, 9-bit signed."""
    return ((r & 0x1FF) << 18) | ((g & 0x1FF) << 9) | (b & 0x1FF)


def sample(fmt, texels, *, bilinear=False, ds=0, dt=0, ncc_select=0):
    return {
        "bilinear": bilinear,
        "texels": list(texels),
        "passthrough": {
            "format": fmt,
            "bilinear": bilinear,
            "ncc_table_select": ncc_select,
            "ds": ds,
            "dt": dt,
        },
    }


class Driver:
    """Testbench helper: drives ``sampleFetch``, records every ``output`` beat.

    Every wait goes through :meth:`tick`, which samples the output handshake on
    the same edge, so nothing is lost while a long push loop runs with
    ``output.ready`` high.
    """

    def __init__(self, ctx, dut):
        self.ctx = ctx
        self.dut = dut
        self.seen = []

    async def tick(self):
        """One clock edge; record an accepted output beat. Returns sampleFetch.ready."""
        dut = self.dut
        vals = await self.ctx.tick().sample(
            dut.output.valid, dut.output.ready,
            dut.output.payload.texture.r, dut.output.payload.texture.g,
            dut.output.payload.texture.b, dut.output.payload.texture_alpha,
            dut.sample_fetch.ready,
        )
        valid, ready, r, g, b, a, sf_ready = vals[2:]
        if valid and ready:
            self.seen.append((r, g, b, a))
        return sf_ready

    async def init(self, *, ready=True, send_config=False):
        ctx, dut = self.ctx, self.dut
        ctx.set(dut.sample_fetch.valid, 0)
        ctx.set(dut.output.ready, ready)
        ctx.set(dut.palette_write.valid, 0)
        ctx.set(dut.send_config, send_config)
        self.set_ncc(0, ZERO_NCC)
        self.set_ncc(1, ZERO_NCC)
        await self.tick()

    def set_ncc(self, index, table):
        port = self.dut.ncc_tables.table1 if index else self.dut.ncc_tables.table0
        self.ctx.set(port.as_value(), ncc_bits(table))

    async def write_palette(self, address, data):
        ctx, dut = self.ctx, self.dut
        ctx.set(dut.palette_write.valid, 1)
        ctx.set(dut.palette_write.payload.address, address)
        ctx.set(dut.palette_write.payload.data, data)
        await self.tick()
        ctx.set(dut.palette_write.valid, 0)

    def drive(self, s):
        ctx, dut = self.ctx, self.dut
        p = dut.sample_fetch.payload
        ctx.set(p.bilinear, s["bilinear"])
        for i, t in enumerate(s["texels"]):
            ctx.set(p.texels[i], t)
        pt = s["passthrough"]
        ctx.set(p.passthrough.format, pt["format"])
        ctx.set(p.passthrough.bilinear, pt["bilinear"])
        ctx.set(p.passthrough.ncc_table_select, pt["ncc_table_select"])
        ctx.set(p.passthrough.ds, pt["ds"])
        ctx.set(p.passthrough.dt, pt["dt"])
        ctx.set(dut.sample_fetch.valid, 1)

    async def push(self, s, *, limit=100):
        """Drive one sample and wait for ``sampleFetch`` to accept it."""
        self.drive(s)
        for _ in range(limit):
            if await self.tick():
                self.ctx.set(self.dut.sample_fetch.valid, 0)
                return
        raise AssertionError("sampleFetch never became ready")

    async def collect(self, count, *, limit=400):
        """Wait for ``count`` more output beats and pop them."""
        for _ in range(limit):
            if len(self.seen) >= count:
                break
            await self.tick()
        assert len(self.seen) >= count, \
            f"only {len(self.seen)} of {count} beats came out"
        out, self.seen = self.seen[:count], self.seen[count:]
        return out


def run(tb, *, c=None):
    dut = TmuTexelDecoder(c if c is not None else Config.voodoo1())

    async def wrapper(ctx):
        await tb(ctx, dut)

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(wrapper)
    sim.run()


# ---------------------------------------------------------------------------
# the model itself
# ---------------------------------------------------------------------------


def test_expand_to8_matches_the_scala_rules():
    assert expand_to8(0b111, 3) == 0xFF
    assert expand_to8(0b101, 3) == 0b10110110  # 101_101_101 >> 1
    assert expand_to8(0b11, 2) == 0xFF
    assert expand_to8(0b10, 2) == 0b10101010
    assert expand_to8(0x1F, 5) == 0xFF
    assert expand_to8(0x10, 5) == 0b10000100
    assert expand_to8(0x3F, 6) == 0xFF
    assert expand_to8(0x20, 6) == 0b10000010
    assert expand_to8(0xF, 4) == 0xFF
    assert expand_to8(0x9, 4) == 0x99
    assert expand_to8(1, 1) == 0xFF
    assert expand_to8(0, 1) == 0x00
    assert expand_to8(0xAB, 8) == 0xAB
    # Every width maps 0 -> 0 and all-ones -> 255, which is the property that
    # makes the expansion a correct range rescale.
    for w in range(1, 9):
        assert expand_to8(0, w) == 0
        assert expand_to8((1 << w) - 1, w) == 255


def test_weights_sum_to_256():
    for ds in range(16):
        for dt in range(16):
            assert sum(weights(ds, dt)) == 256


def test_blend_of_equal_taps_is_the_tap():
    for ds in range(16):
        for dt in range(16):
            w = weights(ds, dt)
            for v in (0, 1, 127, 255):
                assert blend(w, (v, v, v, v)) == v


# ---------------------------------------------------------------------------
# the two Scala tests
# ---------------------------------------------------------------------------


def test_point_rgb565_sample():
    """``TmuTexelDecoder decodes a point RGB565 sample``: 0xF81F -> pure magenta."""

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        await d.push(sample(TF.RGB565, [0xF81F, 0, 0, 0]))
        assert await d.collect(1) == [(255, 0, 255, 255)]

    run(tb)


def test_bilinear_rgb565_blend():
    """``blends a bilinear RGB565 sample``: ds=dt=8 is the exact centre.

    The Scala only asserts ``>= 120`` per channel; the model says the four
    weights are all 64, so red is ``(255 + 0 + 0 + 255) * 64 >> 8 = 127``.
    """
    s = sample(TF.RGB565, [0xF800, 0x07E0, 0x001F, 0xFFFF],
               bilinear=True, ds=8, dt=8)
    expected = model(s, [ZERO_NCC, ZERO_NCC], [0, 0, 0, 0])
    assert expected == (127, 127, 127, 255)

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        await d.push(s)
        assert await d.collect(1) == [expected]

    run(tb)


# ---------------------------------------------------------------------------
# every format
# ---------------------------------------------------------------------------

#: One representative word per format, chosen so no two channels coincide.
FORMAT_VECTORS = [
    (TF.RGB332, 0x00B6),
    (TF.A8, 0x1234),
    (TF.I8, 0x12A5),
    (TF.AI44, 0x0039),
    (TF.ARGB8332, 0x7FB6),
    (TF.RGB565, 0xF81F),
    (TF.RGB565, 0x1234),
    (TF.ARGB1555, 0x8C63),
    (TF.ARGB1555, 0x0C63),
    (TF.ARGB4444, 0x1234),
    (TF.AI88, 0x5AC3),
    (6, 0x1234),
    (7, 0xF81F),
    (15, 0x07E0),
]


@pytest.mark.parametrize("fmt,word", FORMAT_VECTORS)
def test_point_decode_per_format(fmt, word):
    """Point-sampled decode of one word, against the Python model."""
    s = sample(fmt, [word, 0, 0, 0])
    expected = model(s, [ZERO_NCC, ZERO_NCC], [0, 0, 0, 0])

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        await d.push(s)
        assert await d.collect(1) == [expected]

    run(tb)


def test_rgb332_channels():
    """Spot-check RGB332 by hand: 0xB6 = r 101, g 101, b 10."""
    assert decode_texel(0xB6, TF.RGB332, ZERO_NCC, 0) == (
        0b10110110, 0b10110110, 0b10101010, 255)


def test_a8_is_grey_plus_alpha_and_i8_is_opaque():
    assert decode_texel(0x0034, TF.A8, ZERO_NCC, 0) == (0x34, 0x34, 0x34, 0x34)
    assert decode_texel(0x0034, TF.I8, ZERO_NCC, 0) == (0x34, 0x34, 0x34, 255)


def test_argb1555_alpha_is_all_or_nothing():
    assert decode_texel(0x8000, TF.ARGB1555, ZERO_NCC, 0)[3] == 255
    assert decode_texel(0x0000, TF.ARGB1555, ZERO_NCC, 0)[3] == 0


# ---------------------------------------------------------------------------
# NCC / YIQ
# ---------------------------------------------------------------------------


def yiq_table():
    """A table whose I/Q offsets force both clamp directions."""
    return {
        "y": [i * 17 for i in range(16)],           # 0, 17, ... 255
        "i": [ncc_entry(0, 0, 0), ncc_entry(100, -50, 25),
              ncc_entry(-256, 255, -1), ncc_entry(255, 255, 255)],
        "q": [ncc_entry(0, 0, 0), ncc_entry(-40, 60, -80),
              ncc_entry(255, -256, 0), ncc_entry(-256, -256, -256)],
    }


@pytest.mark.parametrize("fmt", [TF.YIQ422, TF.AYIQ8422])
@pytest.mark.parametrize("select", [0, 1])
def test_ncc_decode_every_index(fmt, select):
    """All 256 texel bytes through the NCC path, on both tables."""
    table = yiq_table()
    tables = [table, ZERO_NCC] if select == 0 else [ZERO_NCC, table]
    words = [(0xA5 << 8) | b for b in range(256)]
    expected = [model(sample(fmt, [w, 0, 0, 0], ncc_select=select), tables,
                      [0, 0, 0, 0]) for w in words]

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        d.set_ncc(0, tables[0])
        d.set_ncc(1, tables[1])
        for w in words:
            await d.push(sample(fmt, [w, 0, 0, 0], ncc_select=select))
        assert await d.collect(len(words), limit=4 * len(words)) == expected

    run(tb)


def test_ncc_clamps_both_ways():
    """The model's clamp is reachable: index 2/3 saturate low and high."""
    table = yiq_table()
    # byte 0xF8 -> y[15] = 255, i[2] = (-256, +255, -1), q[0] = 0:
    # R saturates low (-1), G saturates high (510), B lands at 254.
    assert ncc_decode(0xF8, table) == (0, 255, 254)
    # byte 0x0F -> y[0] = 0, i[3] = (255, 255, 255), q[3] = (-256, -256, -256)
    assert ncc_decode(0x0F, table) == (0, 0, 0)
    # byte 0xF3 -> y[15] = 255, i[0] = 0, q[3] = (-256, -256, -256)
    assert ncc_decode(0xF3, table) == (0, 0, 0)
    # byte 0xFC -> y[15] = 255, i[3] = (255, 255, 255), q[0] = 0 -> clamps high
    assert ncc_decode(0xFC, table) == (255, 255, 255)


def test_yiq422_ignores_the_high_byte_but_ayiq8422_uses_it_as_alpha():
    table = yiq_table()
    assert decode_texel(0x5A33, TF.YIQ422, table, 0)[3] == 255
    assert decode_texel(0x5A33, TF.AYIQ8422, table, 0)[3] == 0x5A
    assert (decode_texel(0x5A33, TF.YIQ422, table, 0)[:3]
            == decode_texel(0x9933, TF.AYIQ8422, table, 0)[:3])


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [TF.P8, TF.AP88])
def test_palette_write_then_lookup(fmt):
    """Write eight entries, then read them back through P8 / AP88."""
    entries = {i * 31: 0x112233 + i * 0x010101 for i in range(8)}

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        for addr, value in entries.items():
            await d.write_palette(addr, value)
        for addr, value in entries.items():
            # texels(0) low byte is the palette index; the high byte is AP88's
            # alpha, and must not disturb P8.
            await d.push(sample(fmt, [(0xC7 << 8) | addr, 0, 0, 0]))
        expected = [
            model(sample(fmt, [(0xC7 << 8) | addr, 0, 0, 0]),
                  [ZERO_NCC, ZERO_NCC], [value, 0, 0, 0])
            for addr, value in entries.items()
        ]
        assert await d.collect(len(entries)) == expected

    run(tb)


def test_palette_is_replicated_across_all_four_taps():
    """One write feeds all four RAMs, so a bilinear P8 blend sees real data."""

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        for addr, value in ((1, 0xFF0000), (2, 0x00FF00), (3, 0x0000FF),
                            (4, 0xFFFFFF)):
            await d.write_palette(addr, value)
        s = sample(TF.P8, [1, 2, 3, 4], bilinear=True, ds=8, dt=8)
        expected = model(s, [ZERO_NCC, ZERO_NCC],
                         [0xFF0000, 0x00FF00, 0x0000FF, 0xFFFFFF])
        await d.push(s)
        assert await d.collect(1) == [expected]
        # Each tap contributed a different colour, so the blend is not a copy.
        assert expected[0] == blend((64, 64, 64, 64), (255, 0, 0, 255))

    run(tb)


def test_palette_read_is_not_transparent_to_a_same_cycle_write():
    """Spinal puts the write and the read in separate ``always`` blocks.

    A fetch firing on the same edge as a write to the same address therefore
    sees the *old* contents.  Amaranth's default ``read_port(domain="sync")``
    has ``transparent_for=()``, which is the same thing.
    """

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        await d.write_palette(7, 0x101010)
        # Drive the fetch and the colliding write in the same cycle.
        ctx.set(dut.palette_write.valid, 1)
        ctx.set(dut.palette_write.payload.address, 7)
        ctx.set(dut.palette_write.payload.data, 0xFFFFFF)
        await d.push(sample(TF.P8, [7, 0, 0, 0]))
        ctx.set(dut.palette_write.valid, 0)
        assert await d.collect(1) == [(0x10, 0x10, 0x10, 255)]
        # The write did land: the next lookup sees it.
        await d.push(sample(TF.P8, [7, 0, 0, 0]))
        assert await d.collect(1) == [(0xFF, 0xFF, 0xFF, 255)]

    run(tb)


# ---------------------------------------------------------------------------
# pipeline behaviour
# ---------------------------------------------------------------------------


def test_latency_is_three_cycles():
    """``output`` goes valid exactly three clocks after ``sampleFetch`` fires."""

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        d.drive(sample(TF.RGB565, [0xF81F, 0, 0, 0]))
        # `sampleFetch.ready` is high while the pipe is empty, so this fires
        # on the very next edge.
        assert ctx.get(dut.sample_fetch.ready)
        await ctx.tick()
        ctx.set(dut.sample_fetch.valid, 0)
        for cycle in range(LATENCY - 1):
            assert not ctx.get(dut.output.valid), f"output valid after {cycle + 1}"
            await ctx.tick()
        assert ctx.get(dut.output.valid)
        assert ctx.get(dut.output.payload.texture.r) == 255

    run(tb)


def test_backpressure_holds_the_result():
    """With ``output.ready`` low the payload must stay put, then drain in order."""
    words = [0xF800, 0x07E0, 0x001F]
    expected = [model(sample(TF.RGB565, [w, 0, 0, 0]), [ZERO_NCC, ZERO_NCC],
                      [0, 0, 0, 0]) for w in words]

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init(ready=False)
        for w in words:
            await d.push(sample(TF.RGB565, [w, 0, 0, 0]))
        # The three stages are now full; nothing may have been dropped.
        for _ in range(10):
            await d.tick()
        assert ctx.get(dut.output.valid)
        assert d.seen == []
        ctx.set(dut.output.ready, 1)
        assert await d.collect(len(words)) == expected

    run(tb)


def test_send_config_overrides_the_colour():
    """``io.sendConfig`` is a level input read at the *last* stage."""

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init(send_config=True)
        await d.push(sample(TF.RGB565, [0xF81F, 0, 0, 0]))
        assert await d.collect(1) == [(0, 0, 1, 255)]

    run(tb)


def test_send_config_applies_to_whatever_is_in_flight():
    """Raising it after the request was accepted still recolours that beat."""

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        await d.push(sample(TF.RGB565, [0xF81F, 0, 0, 0]))
        ctx.set(dut.send_config, 1)
        assert await d.collect(1) == [(0, 0, 1, 255)]

    run(tb)


# ---------------------------------------------------------------------------
# randomised sweep
# ---------------------------------------------------------------------------


def test_random_sweep_against_the_model():
    """400 random requests, every format, both NCC tables, a live palette."""
    rng = random.Random(0xF81F)
    tables = [yiq_table(), {
        "y": [rng.randrange(256) for _ in range(16)],
        "i": [ncc_entry(rng.randrange(-256, 256), rng.randrange(-256, 256),
                        rng.randrange(-256, 256)) for _ in range(4)],
        "q": [ncc_entry(rng.randrange(-256, 256), rng.randrange(-256, 256),
                        rng.randrange(-256, 256)) for _ in range(4)],
    }]
    palette = {i: rng.randrange(1 << 24) for i in range(256)}
    formats = list(TF.ALL) + list(TF.UNASSIGNED)

    requests = []
    for _ in range(400):
        requests.append(sample(
            rng.choice(formats),
            [rng.randrange(1 << 16) for _ in range(4)],
            bilinear=rng.random() < 0.5,
            ds=rng.randrange(16),
            dt=rng.randrange(16),
            ncc_select=rng.randrange(2),
        ))
    expected = [
        model(s, tables, [palette[s["texels"][i] & 0xFF] for i in range(4)])
        for s in requests
    ]

    async def tb(ctx, dut):
        d = Driver(ctx, dut)
        await d.init()
        d.set_ncc(0, tables[0])
        d.set_ncc(1, tables[1])
        for addr, value in palette.items():
            await d.write_palette(addr, value)
        for s in requests:
            await d.push(s)
        assert await d.collect(len(requests),
                               limit=4 * len(requests)) == expected

    run(tb)
