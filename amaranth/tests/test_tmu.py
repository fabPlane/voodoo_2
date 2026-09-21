"""Unit tests for ``avoodoo.texture.tmu.Tmu`` -- the intent of ``TmuTest.scala``.

``ext/SpinalVoodoo/test/TmuTest.scala`` drives the whole TMU with a tiny
memory model that answers every ``texRead`` command with one constant word
and checks (a) RGB565 white/black/red/green/blue decode to the right RGBA8888
and (b) the texel address for ``(s, t) = (10, 20)``.  Those are ported as-is
(with a memory model that returns the full 8-word burst the cache asks for --
the Scala test answers ``last`` on the first beat, which the current cache
does not accept).

The rest is a **Python model of the front end** -- everything from ``io.input``
to ``sampleRequest`` -- written from the Scala with plain integers and the
same width rules: the reciprocal + log ROMs (:mod:`avoodoo.texture.tmu_tables`),
``clz32``/``clz64``, the 66-bit perspective divide with its round bias, the
LOD from the gradient magnitude, ``lodbias``/``lodmin``/``lodmax``, aspect,
point and bilinear coordinates, ``wrapOrClamp`` and the packed ``texelAddr``.
The DUT's ``sampleRequest`` stream (kept as ``dut.sample_request``) is watched
and every fired beat is compared with the model, over directed cases
(perspective on/off, LOD 0/1/2 from the gradients, bias, clamp, aspect, wrap
vs clamp, ``clampW`` with a negative ``1/W``, 8- and 16-bit formats) and a
random sweep.  ``avoodoo.lib.fixed_model`` supplies the AFix raw encodings
of the real-valued stimulus.

The model is *not* derived from the port: where the Scala does something odd
(``bilinearEnable`` decoded one stage earlier than the rest of the request,
the 4-bit ``U(4) + lodLevel`` add) the model spells the same thing out from
the Scala, so a transliteration slip in the port shows up here as well as in
the lockstep diff.
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.lib.fixed_model import ModelShape, const as afix_const
from avoodoo.texture.tex_layout_tables import TexConfig, compute as compute_tables
from avoodoo.texture.tmu import MAX_OUTSTANDING, Tmu
from avoodoo.texture.tmu_tables import log_table, recip_table
from avoodoo.texture.tmu_types import TextureFormat as TF

PERIOD = 1e-6
C = Config.voodoo1()
LINE_WORDS = C.tex_fill_line_words


# ---------------------------------------------------------------------------
# integer helpers (Spinal widths)
# ---------------------------------------------------------------------------


def u(v: int, w: int) -> int:
    return v & ((1 << w) - 1)


def sx(v: int, w: int) -> int:
    v = u(v, w)
    return v - (1 << w) if v >> (w - 1) else v


def clz32(v: int) -> int:
    """``Tmu.clz32``: binary search, 6 bits; note ``clz32(0) == 31``."""
    v = u(v, 32)
    n = 0
    if v >> 16 == 0:
        n += 16
        v = u(v << 16, 32)
    if v >> 24 == 0:
        n += 8
        v = u(v << 8, 32)
    if v >> 28 == 0:
        n += 4
        v = u(v << 4, 32)
    if v >> 30 == 0:
        n += 2
        v = u(v << 2, 32)
    if not (v >> 31):
        n += 1
    return u(n, 6)


def clz64(v: int) -> int:
    v = u(v, 64)
    hi, lo = v >> 32, u(v, 32)
    return 32 + clz32(lo) if hi == 0 else clz32(hi)


def wrap_or_clamp(coord: int, size_bits: int, clamp: bool) -> int:
    """``Tmu.wrapOrClamp`` on a 14-bit SInt coordinate."""
    size = u(1 << size_bits, 10)
    max_val = u(size - 1, 10)
    if clamp:
        if coord < 0:
            return 0
        if u(coord, 14) >= size:
            return max_val
        return u(coord, 10)
    return u(coord, 10) & max_val


# ---------------------------------------------------------------------------
# the front-end model
# ---------------------------------------------------------------------------


def front_end(s_raw: int, t_raw: int, w_raw: int, *, perspective: bool, clamp_w: bool):
    """``stageRecipPrep`` .. ``stageA``: ``(texS, texT, perspLodAdjust, clampToZero)``."""
    oow = sx(w_raw, 48)
    neg = oow < 0
    abs_oow = u((~u(oow, 48) if neg else u(oow, 48)) + int(neg), 48)
    clz = clz64(abs_oow)
    msb_pos = u(63 - clz, 7)
    norm = u(abs_oow << clz, 64)
    index = (norm >> 55) & 0xFF
    frac = (norm >> 47) & 0xFF
    base = recip_table[index]
    diff = u(base - recip_table[index + 1], 17)
    log_frac = log_table[index]
    valid_oow = (not neg) and abs_oow != 0
    clamp_to_zero = clamp_w and neg

    interp = u(base - ((diff * frac) >> 8), 17) if valid_oow else 0
    if valid_oow:
        adj = sx(27 - msb_pos, 8)
        persp_lod_adjust = sx(sx(adj << 8, 16) - log_frac - 1, 16)
    else:
        persp_lod_adjust = 0

    s = sx(s_raw, 48)
    t = sx(t_raw, 48)
    if not perspective:
        tex_s = sx(s >> 14, 18)
        tex_t = sx(t >> 14, 18)
    else:
        round_bias = sx(1 << u(msb_pos - 1, 7), 66)
        tex_s = sx(sx(s * interp + round_bias, 66) >> msb_pos, 18)
        tex_t = sx(sx(t * interp + round_bias, 66) >> msb_pos, 18)
    return tex_s, tex_t, persp_lod_adjust, clamp_to_zero


def base_lod(grads: tuple[int, int, int, int]) -> int:
    """``lodPipeline.baseLod_8_8`` from the four 32-bit raw gradients."""
    dsdx, dtdx, dsdy, dtdy = (sx(g, 32) for g in grads)
    tempdx = u(u(dsdx * dsdx, 64) + u(dtdx * dtdx, 64), 64)
    tempdy = u(u(dsdy * dsdy, 64) + u(dtdy * dtdy, 64), 64)
    temp = tempdx if tempdx > tempdy else tempdy
    if temp == 0:
        return 0
    hi, lo = temp >> 32, u(temp, 32)
    use_hi = hi != 0
    c = clz32(hi if use_hi else lo)
    msb64 = u(63 - c, 7) if use_hi else u(31 - c, 7)
    shift = u(msb64 - 8, 6) if msb64 >= 8 else 0
    shifted = u(temp >> shift, 16)
    raw = sx(sx(sx(msb64, 8) << 8, 16) + log_table[shifted & 0xFF] - 36 * 256, 16)
    return sx(raw >> 2, 16)


def lod_stage(base_lod_8_8: int, persp_lod_adjust: int, t_lod: int):
    """``stageLodPrep`` -> ``(lodLevel, texWidthBits, texHeightBits)``."""
    lodmin = t_lod & 0x3F
    lodmax = (t_lod >> 6) & 0x3F
    lodbias = sx(t_lod >> 12, 6)
    s_is_wider = (t_lod >> 20) & 1
    aspect = (t_lod >> 21) & 3
    lod = sx(sx(base_lod_8_8 + persp_lod_adjust, 16) + sx(lodbias << 6, 16), 16)
    lo = lodmin << 6
    hi = min(lodmax << 6, 0x800)
    lod = lo if lod < lo else hi if lod > hi else lod
    lod_int = sx(lod >> 8, 16)
    level = 0 if lod_int < 0 else 8 if lod_int > 8 else lod_int
    dim = u(8 - level, 4)
    narrow = u(dim - aspect, 4) if dim >= aspect else 0
    width = height = dim
    if s_is_wider:
        height = narrow
    elif aspect != 0:
        width = narrow
    return level, width, height


def coord_gen(tex_s: int, tex_t: int, level: int, clamp_to_zero: bool):
    """``coordGen``: ``(sPoint, tPoint, si, ti, ds, dt)``."""
    if clamp_to_zero:
        return (0, 0, 0, 0, 0, 0)
    shift = u(4 + level, 4)
    s_point = sx(tex_s >> shift, 14)
    t_point = sx(tex_t >> shift, 14)
    half = sx(1 << u(3 + level, 4), 18)
    s_scaled = sx(tex_s - half, 18) >> level
    t_scaled = sx(tex_t - half, 18) >> level
    return (s_point, t_point, sx(s_scaled >> 4, 14), sx(t_scaled >> 4, 14),
            u(s_scaled, 4), u(t_scaled, 4))


def texel_addr(tables, level: int, x: int, y: int, is16: bool) -> int:
    base = tables.tex_base[level]
    shift = tables.tex_shift[level]
    if is16:
        return u(u(u(base + u(x << 1, 22), 22) + u(y << u(shift + 1, 5), 22), 22), C.address_width)
    return u(u(u(base + x, 22) + u(y << shift, 22), 22), C.address_width)


def sample_request_model(px: dict, next_px: dict | None = None) -> dict:
    """Everything the DUT's ``sampleRequest`` beat should carry for pixel ``px``.

    ``next_px`` is the pixel that entered the pipeline the cycle after ``px``
    (``None`` if there was none).  ``bilinearEnable`` (``Tmu.scala:332``) is
    decoded from ``stageLod``'s ``textureMode`` but folded into ``stageB``'s
    ``translateWith`` one stage later, so a request carries the *following*
    pixel's min/mag-filter bits when the two flow back to back -- and its own
    when it is the last one, since the ``m2sPipe`` register keeps holding it.
    """
    tm = px["texture_mode"]
    perspective = bool(tm & 1)
    bilinear = bool(((next_px or px)["texture_mode"] >> 1) & 3)
    clamp_w = bool((tm >> 3) & 1)
    clamp_s = bool((tm >> 6) & 1)
    clamp_t = bool((tm >> 7) & 1)
    fmt = (tm >> 8) & 0xF
    is16 = fmt >= TF.ARGB8332
    tex_s, tex_t, pla, ctz = front_end(px["s"], px["t"], px["w"], perspective=perspective,
                                       clamp_w=clamp_w)
    level, wbits, hbits = lod_stage(base_lod(px["grads"]), pla, px["t_lod"])
    s_point, t_point, si, ti, ds, dt = coord_gen(tex_s, tex_t, level, ctz)
    tables = px["tables"]
    px_x = wrap_or_clamp(s_point, wbits, clamp_s)
    px_y = wrap_or_clamp(t_point, hbits, clamp_t)
    x0 = wrap_or_clamp(si, wbits, clamp_s)
    x1 = wrap_or_clamp(sx(si + 1, 14), wbits, clamp_s)
    y0 = wrap_or_clamp(ti, hbits, clamp_t)
    y1 = wrap_or_clamp(sx(ti + 1, 14), hbits, clamp_t)
    return {
        "tex_s": tex_s, "tex_t": tex_t, "lod_level": level,
        "point_addr": texel_addr(tables, level, px_x, px_y, is16),
        "bi_addr0": texel_addr(tables, level, x0, y0, is16),
        "bi_addr1": texel_addr(tables, level, x1, y0, is16),
        "bi_addr2": texel_addr(tables, level, x0, y1, is16),
        "bi_addr3": texel_addr(tables, level, x1, y1, is16),
        "lod_base": u(tables.tex_base[level], C.address_width),
        "lod_shift": tables.tex_shift[level],
        "is16_bit": int(is16),
        "bilinear": int(bilinear),
        "format": fmt, "ds": ds, "dt": dt,
        "ncc_table_select": (tm >> 5) & 1,
    }


# ---------------------------------------------------------------------------
# stimulus helpers
# ---------------------------------------------------------------------------

S_SHAPE = ModelShape.from_q(C.tex_coords_accum_format)   # SQ(48,18)
W_SHAPE = ModelShape.from_q(C.w_accum_format)            # SQ(48,30)
G_SHAPE = ModelShape.from_q(C.tex_coords_format)         # SQ(32,18)


def _quantise(shape, value) -> Fraction:
    """Round ``value`` to the nearest representable step of ``shape``."""
    step = shape.step
    return Fraction(round(Fraction(value) / step)) * step


def raw48(shape, value) -> int:
    return u(afix_const(shape, _quantise(shape, value)).raw, 48)


def raw32(value) -> int:
    return u(afix_const(G_SHAPE, _quantise(G_SHAPE, value)).raw, 32)


def texture_mode(*, fmt=TF.RGB565, perspective=False, bilinear=False, clamp_w=False,
                 clamp_s=False, clamp_t=False, ncc=0) -> int:
    return (int(perspective) | (int(bilinear) << 1) | (int(clamp_w) << 3) | (ncc << 5)
            | (int(clamp_s) << 6) | (int(clamp_t) << 7) | (fmt << 8))


def t_lod(*, lodmin=0, lodmax=32, lodbias=0, s_is_wider=False, aspect=0) -> int:
    """``tLOD``; ``lodmin``/``lodmax``/``lodbias`` are 4.2 (quarter levels), so the
    default ``lodmax = 32`` is LOD 8.0 -- the Scala test's ``0x200`` (``lodmax = 8``)
    is a 2.0 cap, which its LOD-0 pixels never reach."""
    return lodmin | (lodmax << 6) | ((lodbias & 0x3F) << 12) | (int(s_is_wider) << 20) | (aspect << 21)


def tables_for(*, base_byte_addr=0, fmt=TF.RGB565, aspect=0, s_is_wider=False):
    """``setTexTables`` of the Scala test, via the ported ``TexLayoutTables``."""
    return compute_tables(TexConfig(tex_base_addr=base_byte_addr >> 3, tformat=fmt,
                                    tlod_aspect=aspect, tlod_s_is_wider=s_is_wider))


def pixel(*, s=0.0, t=0.0, w=1.0, grads=(1.0, 0.0, 0.0, 1.0), tm=None, tl=None,
          tables=None, texture_enable=True, base_byte_addr=0, aspect=0, s_is_wider=False):
    """One ``Tmu.Input`` beat as plain ints (``s``/``t`` are S/W and T/W)."""
    tm = texture_mode() if tm is None else tm
    tl = t_lod(aspect=aspect, s_is_wider=s_is_wider) if tl is None else tl
    fmt = (tm >> 8) & 0xF
    if tables is None:
        tables = tables_for(base_byte_addr=base_byte_addr, fmt=fmt, aspect=aspect,
                            s_is_wider=s_is_wider)
    return {
        "s": raw48(S_SHAPE, s), "t": raw48(S_SHAPE, t), "w": raw48(W_SHAPE, w),
        "grads": tuple(raw32(g) for g in grads),
        "texture_mode": tm, "t_lod": tl, "tables": tables,
        "tex_base_addr": base_byte_addr >> 3,
        "texture_enable": int(texture_enable),
    }


# ---------------------------------------------------------------------------
# testbench
# ---------------------------------------------------------------------------


class Driver:
    """Drives ``input``, models texture memory, records ``sampleRequest`` and ``output``."""

    def __init__(self, ctx, dut, *, word=0xFFFF_FFFF, words=None, ready=True):
        self.ctx = ctx
        self.dut = dut
        self.word = word
        self.words = words or {}
        self.ready = ready
        self.requests = []
        self.outputs = []
        self.cmds = []
        self._rsp = []          # pending (data, last) beats

    def _mem_word(self, addr):
        return self.words.get(addr & ~3, self.word)

    async def tick(self):
        ctx, dut = self.ctx, self.dut
        sr = dut.sample_request
        rsp = dut.tex_read.rsp
        # Present the head of the response queue (hold rule: only advance on fire).
        if self._rsp:
            data, last = self._rsp[0]
            ctx.set(rsp.valid, 1)
            ctx.set(rsp.payload.fragment.data, data)
            ctx.set(rsp.payload.last, last)
        else:
            ctx.set(rsp.valid, 0)
        ctx.set(dut.tex_read.cmd.ready, 1)
        p = sr.payload
        vals = await ctx.tick().sample(
            sr.valid, sr.ready, p.point_addr, p.bi_addr0, p.bi_addr1, p.bi_addr2, p.bi_addr3,
            p.lod_base, p.lod_shift, p.is16_bit, p.bilinear, p.passthrough.format,
            p.passthrough.ds, p.passthrough.dt, p.passthrough.ncc_table_select,
            dut.output.valid, dut.output.ready, dut.output.payload.texture.r,
            dut.output.payload.texture.g, dut.output.payload.texture.b,
            dut.output.payload.texture_alpha,
            dut.tex_read.cmd.valid, dut.tex_read.cmd.ready,
            dut.tex_read.cmd.payload.fragment.address, dut.tex_read.cmd.payload.fragment.length,
            rsp.valid, rsp.ready, dut.input.ready, dut.tex_s, dut.tex_t,
        )
        (sr_v, sr_r, pa, b0, b1, b2, b3, lb, ls, i16, bil, fmt, ds, dt, ncc,
         o_v, o_r, r, g, b, a, c_v, c_r, c_addr, c_len, r_v, r_r, in_ready,
         tex_s, tex_t) = vals[2:]
        if sr_v and sr_r:
            self.requests.append({
                "point_addr": pa, "bi_addr0": b0, "bi_addr1": b1, "bi_addr2": b2,
                "bi_addr3": b3, "lod_base": lb, "lod_shift": ls, "is16_bit": i16,
                "bilinear": bil, "format": fmt, "ds": ds, "dt": dt,
                "ncc_table_select": ncc, "tex_s": tex_s, "tex_t": tex_t,
            })
        if o_v and o_r:
            self.outputs.append((r, g, b, a))
        if c_v and c_r:
            self.cmds.append((c_addr, c_len))
            beats = ((c_addr & 3) + c_len) // 4 + 1
            base = c_addr & ~3
            for i in range(beats):
                self._rsp.append((self._mem_word(base + 4 * i), int(i == beats - 1)))
        if r_v and r_r:
            self._rsp.pop(0)
        return in_ready

    async def init(self):
        ctx, dut = self.ctx, self.dut
        ctx.set(dut.input.valid, 0)
        ctx.set(dut.output.ready, int(self.ready))
        ctx.set(dut.invalidate, 0)
        ctx.set(dut.send_config, 0)
        ctx.set(dut.palette_write.valid, 0)
        ctx.set(dut.tex_read.rsp.payload.fragment.opcode, 0)
        ctx.set(dut.tex_read.rsp.payload.fragment.source, 0)
        await self.tick()

    def drive(self, px):
        ctx, dut = self.ctx, self.dut
        p = dut.input.payload
        ctx.set(p.s.as_value(), px["s"])
        ctx.set(p.t.as_value(), px["t"])
        ctx.set(p.w.as_value(), px["w"])
        for name, g in zip(("d_sd_x", "d_td_x", "d_sd_y", "d_td_y"), px["grads"]):
            ctx.set(getattr(p, name).as_value(), g)
        ctx.set(p.c_other.as_value(), 0)
        ctx.set(p.a_other, 0)
        cfg = p.config
        ctx.set(cfg.texture_mode, px["texture_mode"])
        ctx.set(cfg.tex_base_addr, px["tex_base_addr"])
        ctx.set(cfg.tex_base_addr1, 0)
        ctx.set(cfg.tex_base_addr2, 0)
        ctx.set(cfg.tex_base_addr38, 0)
        ctx.set(cfg.t_lod, px["t_lod"])
        ctx.set(cfg.texture_enable, px["texture_enable"])
        tb = px["tables"]
        for lod in range(9):
            ctx.set(cfg.tex_tables.tex_base[lod], tb.tex_base[lod])
            ctx.set(cfg.tex_tables.tex_end[lod], tb.tex_end[lod])
            ctx.set(cfg.tex_tables.tex_shift[lod], tb.tex_shift[lod])
        ctx.set(dut.input.valid, 1)

    async def push(self, px, *, limit=400):
        self.drive(px)
        for _ in range(limit):
            if await self.tick():
                self.ctx.set(self.dut.input.valid, 0)
                return
        raise AssertionError("input never became ready")

    async def wait_requests(self, count, *, limit=600):
        for _ in range(limit):
            if len(self.requests) >= count:
                return
            await self.tick()
        raise AssertionError(f"only {len(self.requests)} of {count} sampleRequests fired")

    async def wait_outputs(self, count, *, limit=3000):
        for _ in range(limit):
            if len(self.outputs) >= count:
                return
            await self.tick()
        raise AssertionError(f"only {len(self.outputs)} of {count} outputs came out")

    async def drain(self, cycles=60):
        for _ in range(cycles):
            await self.tick()


def run(tb, **kw):
    dut = Tmu(C)

    async def wrapper(ctx):
        d = Driver(ctx, dut, **kw)
        await d.init()
        await tb(d)

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(wrapper)
    sim.run()


REQ_KEYS = ("point_addr", "bi_addr0", "bi_addr1", "bi_addr2", "bi_addr3", "lod_base",
            "lod_shift", "is16_bit", "bilinear", "format", "ds", "dt", "ncc_table_select")


def check_requests(driver, pixels):
    """``pixels`` were pushed back to back (see :func:`sample_request_model`)."""
    assert len(driver.requests) == len(pixels)
    for i, (got, px) in enumerate(zip(driver.requests, pixels)):
        want = sample_request_model(px, pixels[i + 1] if i + 1 < len(pixels) else None)
        got_sub = {k: got[k] for k in REQ_KEYS}
        want_sub = {k: want[k] for k in REQ_KEYS}
        assert got_sub == want_sub, f"pixel {i}: {got_sub} != {want_sub}"


# ---------------------------------------------------------------------------
# the model's own sanity (no simulator)
# ---------------------------------------------------------------------------


def test_clz_matches_bit_length():
    for v in [1, 2, 3, 0x80000000, 0x12345678, 0xFFFFFFFF, 0x100]:
        assert clz32(v) == 32 - v.bit_length()
    assert clz32(0) == 31                       # the Scala's quirk
    for v in [1, 1 << 40, (1 << 63) | 5, 0xFFFF_FFFF_FFFF_FFFF]:
        assert clz64(v) == 64 - v.bit_length()
    assert clz64(0) == 63


def test_reciprocal_interpolation_is_close_to_1_over_w():
    """``interp`` is 2^(24 - msbPos) / w scaled -- check the perspective path lands
    within one texel of the exact S/W / (1/W)."""
    for w in (1.0, 0.5, 0.25, 0.75, 1.5, 3.0, 0.1, 100.0):
        for coord in (0.0, 10.0, 137.25, 255.0, -3.5):
            s_raw = raw48(S_SHAPE, coord * w)
            w_raw = raw48(W_SHAPE, w)
            tex_s, _, _, _ = front_end(s_raw, 0, w_raw, perspective=True, clamp_w=False)
            assert abs(tex_s / 16 - coord) <= 1.0, (w, coord, tex_s / 16)


def test_no_perspective_is_a_plain_14_4_truncation():
    tex_s, tex_t, pla, ctz = front_end(raw48(S_SHAPE, 10.0), raw48(S_SHAPE, 20.5),
                                       raw48(W_SHAPE, 1.0), perspective=False, clamp_w=False)
    assert (tex_s, tex_t) == (10 << 4, int(20.5 * 16))
    assert ctz is False


@pytest.mark.parametrize("dsdx,lod_8_8", [(1.0, 0x000), (2.0, 0x080), (4.0, 0x100),
                                          (8.0, 0x180), (16.0, 0x200), (0.5, 0x000),
                                          (256.0, 0x400), (1024.0, 0x500)])
def test_base_lod_from_gradient(dsdx, lod_8_8):
    """``baseLod_8_8`` is ``(log2(max(|dX|^2, |dY|^2)) - 36) / 4`` in 8.8 -- the same
    ``LOD >>= 2`` 86Box does (``vid_voodoo_render.c:1521``), so a gradient of 2
    texels/pixel is LOD 0.5, of 4 is LOD 1.0."""
    assert base_lod((raw32(dsdx), 0, 0, raw32(1.0))) == lod_8_8
    # The Y gradient counts the same way, and the larger of the two wins.
    assert base_lod((raw32(1.0), 0, 0, raw32(dsdx))) == lod_8_8
    assert base_lod((0, raw32(1.0), raw32(dsdx), 0)) == lod_8_8


def test_lod_level_clamps_below_zero():
    """A gradient below 1 texel/pixel is a negative LOD, clamped to level 0."""
    assert base_lod((raw32(0.5), 0, 0, 0)) < 0
    assert lod_stage(base_lod((raw32(0.5), 0, 0, 0)), 0, t_lod())[0] == 0
    # 1/W = 1.0 gives perspLodAdjust = -3.004 (27 - msbPos 30, minus 1/256); with a
    # LOD 2 gradient that is still negative.
    _, _, pla, _ = front_end(0, 0, raw48(W_SHAPE, 1.0), perspective=True, clamp_w=False)
    assert pla == -(3 << 8) - 1
    assert lod_stage(base_lod((raw32(16.0), 0, 0, 0)), pla, t_lod())[0] == 0
    assert lod_stage(base_lod((raw32(256.0), 0, 0, 0)), pla, t_lod())[0] == 0      # 3.996
    assert lod_stage(base_lod((raw32(1024.0), 0, 0, 0)), pla, t_lod())[0] == 1     # 4.996


def test_lod_bias_and_clamps():
    base = base_lod((raw32(1.0), 0, 0, raw32(1.0)))
    assert lod_stage(base, 0, t_lod(lodbias=2 << 2))[0] == 2           # +2.0 (6-bit 4.2)
    assert lod_stage(base, 0, t_lod(lodbias=(-4) & 0x3F))[0] == 0        # -1.0, clamped at min
    assert lod_stage(base, 0, t_lod(lodmin=3))[0] == 0                   # 0.75
    assert lod_stage(base, 0, t_lod(lodmin=12))[0] == 3
    base4 = base_lod((raw32(256.0), 0, 0, raw32(256.0)))                 # 4.0
    assert lod_stage(base4, 0, t_lod())[0] == 4
    assert lod_stage(base4, 0, t_lod(lodmax=8))[0] == 2                  # 2.0 cap
    assert lod_stage(base4, 0, t_lod(lodmax=2))[0] == 0                  # 0.5 cap
    base6 = base_lod((raw32(4096.0), 0, 0, 0))                           # 6.0
    assert lod_stage(base6, 0, t_lod(lodbias=31, lodmax=63))[0] == 8     # 13.75 -> 0x800 cap
    assert lod_stage(base6, 0, t_lod(lodbias=31, lodmax=32))[0] == 8


def test_aspect_ratio_dims():
    base = base_lod((raw32(1.0), 0, 0, raw32(1.0)))
    assert lod_stage(base, 0, t_lod(aspect=1, s_is_wider=True))[1:] == (8, 7)
    assert lod_stage(base, 0, t_lod(aspect=2, s_is_wider=False))[1:] == (6, 8)
    base7 = base_lod((raw32(128.0), 0, 0, 0))                            # 3.5
    lvl, w, h = lod_stage(base7, 0, t_lod(lodbias=14, aspect=3, s_is_wider=True))  # +3.5
    assert (lvl, w, h) == (7, 1, 0)


def test_wrap_or_clamp():
    assert wrap_or_clamp(-1, 8, False) == 255
    assert wrap_or_clamp(-1, 8, True) == 0
    assert wrap_or_clamp(300, 8, False) == 44
    assert wrap_or_clamp(300, 8, True) == 255
    assert wrap_or_clamp(5, 3, False) == 5
    assert wrap_or_clamp(9, 3, True) == 7


# ---------------------------------------------------------------------------
# the Scala test cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word,rgba", [
    (0xFFFF, (255, 255, 255, 255)),     # RGB565 white
    (0x0000, (0, 0, 0, 255)),           # black
    (0xF800, (255, 0, 0, 255)),         # red
    (0x07E0, (0, 255, 0, 255)),         # green
    (0x001F, (0, 0, 255, 255)),         # blue
])
def test_rgb565_solid_texture_decodes(word, rgba):
    """``TMU converts RGB565 <colour> to RGB888`` -- memory returns one constant texel.

    Sampled **twice**.  The first sample misses the cold cache and comes back
    as texel 0 -- ``TmuTextureCache.scala:765`` pushes ``ownerRspCommitted``
    (the *registered* ``owner.texels``) into ``fillFetchRaw`` in the very beat
    the last tap is captured, so the fetch carries the value from before the
    capture (0 after ``compareConsumeMiss``; ``ownerRsp`` with ``nextTexel``
    is computed and never used).  The second sample hits and decodes the
    colour the Scala test expects.  The lockstep diff shows the Spinal RTL
    doing the same; the Console builds with ``useTexFillCache = false`` so
    this path is not what the hardware runs.  Pinned here so a "fix" is a
    deliberate decision, not an accident.
    """

    async def tb(d):
        await d.push(pixel())
        await d.wait_outputs(1)
        assert d.outputs == [(0, 0, 0, 255)], "cold miss: pre-capture texel 0"
        assert len(d.cmds) == 1, "the sample went to texture memory"
        await d.push(pixel())
        await d.wait_outputs(2)
        assert d.outputs[1] == rgba
        assert len(d.cmds) == 1, "the second sample hit the line just filled"

    run(tb, word=(word << 16) | word)


def test_cold_bilinear_sample_has_one_stale_tap():
    """Same upstream quirk, bilinear: the three taps captured on earlier beats are
    committed, the one captured on the emitting beat is stale (0), so a solid
    white texture blends to ``255 - w3 * 255 / 256`` on a cold line; the retry hits."""

    async def tb(d):
        px = pixel(s=10.25, t=20.75, tm=texture_mode(bilinear=True))
        await d.push(px)
        await d.wait_outputs(1)
        # ds = 12, dt = 4: w3 = 12 * 4 = 48 -> 255 * (256 - 48) / 256 = 207
        assert d.outputs == [(207, 207, 207, 255)]
        await d.push(px)
        await d.wait_outputs(2)
        assert d.outputs[1] == (255, 255, 255, 255)

    run(tb, word=0xFFFF_FFFF)


def test_texel_address_for_10_20():
    """``TMU computes correct texel address``: (10, 20) on a 256x256 RGB565 texture
    at base 0x1000 is ``base + (20 << 9) + (10 << 1)``, and the line the cache fetches
    is that address rounded down to the 32-byte line."""
    base_byte_addr = 0x1000

    async def tb(d):
        await d.push(pixel(s=10.0, t=20.0, base_byte_addr=base_byte_addr))
        await d.wait_requests(1)
        want = base_byte_addr + (20 << 9) + (10 << 1)
        assert d.requests[0]["point_addr"] == want
        assert d.requests[0]["tex_s"] == 10 << 4 and d.requests[0]["tex_t"] == 20 << 4
        await d.wait_outputs(1)
        assert d.cmds[0][0] == want & ~(LINE_WORDS * 4 - 1)
        assert d.cmds[0][1] == LINE_WORDS * 4 - 1

    run(tb)


# ---------------------------------------------------------------------------
# front-end model vs DUT
# ---------------------------------------------------------------------------


def _directed_pixels():
    px = []
    # point sampling, no perspective, 8- and 16-bit formats
    px.append(pixel(s=10.0, t=20.0))
    px.append(pixel(s=10.0, t=20.0, tm=texture_mode(fmt=TF.P8)))
    px.append(pixel(s=255.75, t=0.25, tm=texture_mode(fmt=TF.ARGB4444, bilinear=True)))
    # perspective: S/W and T/W scaled by several W
    for w in (0.5, 2.0, 0.3, 7.0):
        px.append(pixel(s=33.5 * w, t=100.25 * w, w=w, tm=texture_mode(perspective=True)))
        px.append(pixel(s=33.5 * w, t=100.25 * w, w=w,
                        tm=texture_mode(perspective=True, bilinear=True)))
    # LOD from the gradients, with and without bias, and clamps
    for dsdx in (1.0, 2.0, 4.0, 3.0, 0.7, 40.0):
        px.append(pixel(s=64.0, t=64.0, grads=(dsdx, 0.0, 0.0, dsdx),
                        tm=texture_mode(bilinear=True)))
    px.append(pixel(s=64.0, t=64.0, grads=(1.0, 0.0, 0.0, 1.0), tl=t_lod(lodbias=6)))
    px.append(pixel(s=64.0, t=64.0, grads=(8.0, 0.0, 0.0, 8.0), tl=t_lod(lodmax=4)))
    px.append(pixel(s=64.0, t=64.0, grads=(1.0, 0.0, 0.0, 1.0), tl=t_lod(lodmin=16)))
    px.append(pixel(s=64.0, t=64.0, grads=(64.0, 0.0, 0.0, 64.0), tl=t_lod(lodbias=20)))
    # aspect ratios
    px.append(pixel(s=200.0, t=50.0, aspect=1, s_is_wider=True, tm=texture_mode(bilinear=True)))
    px.append(pixel(s=50.0, t=200.0, aspect=2, s_is_wider=False))
    # wrap vs clamp, outside the texture, both signs
    for s, t in ((-3.0, 300.0), (600.0, -1.0), (255.9, 255.9)):
        px.append(pixel(s=s, t=t, tm=texture_mode(bilinear=True)))
        px.append(pixel(s=s, t=t, tm=texture_mode(bilinear=True, clamp_s=True, clamp_t=True)))
    # clampW with 1/W < 0 -> everything zero; and 1/W == 0 (invalid)
    px.append(pixel(s=40.0, t=40.0, w=-1.0, tm=texture_mode(perspective=True, clamp_w=True)))
    px.append(pixel(s=40.0, t=40.0, w=-1.0, tm=texture_mode(perspective=True)))
    px.append(pixel(s=40.0, t=40.0, w=0.0, tm=texture_mode(perspective=True)))
    # a different base, multi-word offsets
    px.append(pixel(s=17.0, t=3.0, base_byte_addr=0x20000, tm=texture_mode(fmt=TF.RGB332)))
    return px


def test_directed_pixels_match_model():
    pixels = _directed_pixels()

    async def tb(d):
        for px in pixels:
            await d.push(px)
        await d.wait_requests(len(pixels))
        check_requests(d, pixels)
        await d.wait_outputs(len(pixels))

    run(tb)


def test_random_pixels_match_model():
    rng = random.Random(7)
    pixels = []
    for _ in range(40):
        fmt = rng.choice(TF.ALL)
        tm = texture_mode(fmt=fmt, perspective=rng.random() < 0.6, bilinear=rng.random() < 0.5,
                          clamp_w=rng.random() < 0.3, clamp_s=rng.random() < 0.5,
                          clamp_t=rng.random() < 0.5, ncc=rng.getrandbits(1))
        aspect = rng.randrange(4)
        s_is_wider = rng.random() < 0.5
        tl = t_lod(lodmin=rng.choice([0, 0, rng.randrange(36)]),
                   lodmax=rng.choice([32, 32, rng.randrange(64)]),
                   lodbias=rng.choice([0, rng.randrange(64)]), aspect=aspect,
                   s_is_wider=s_is_wider)
        w = rng.choice([1.0, rng.uniform(0.05, 4.0), -rng.uniform(0.1, 2.0)])
        sc, tc = rng.uniform(-50, 320), rng.uniform(-50, 320)
        persp = tm & 1
        pixels.append(pixel(s=sc * w if persp else sc, t=tc * w if persp else tc, w=w,
                            grads=tuple(rng.uniform(-6, 6) for _ in range(4)),
                            tm=tm, tl=tl, base_byte_addr=rng.randrange(0, 1 << 16) & ~7,
                            aspect=aspect, s_is_wider=s_is_wider))

    async def tb(d):
        for px in pixels:
            await d.push(px)
        await d.wait_requests(len(pixels), limit=4000)
        check_requests(d, pixels)

    run(tb)


# ---------------------------------------------------------------------------
# bypass, in-flight accounting, back-pressure
# ---------------------------------------------------------------------------


def test_texture_disabled_bypasses_memory():
    """``textureEnable = 0``: (0, 0, 0, 255) comes out and nothing is fetched."""

    async def tb(d):
        for _ in range(3):
            await d.push(pixel(texture_enable=False))
        await d.wait_outputs(3)
        assert d.outputs == [(0, 0, 0, 255)] * 3
        assert d.cmds == []
        assert d.requests == []

    run(tb)


def test_busy_falls_when_idle():
    async def tb(d):
        assert not d.ctx.get(d.dut.busy)
        await d.push(pixel())
        assert d.ctx.get(d.dut.busy)
        await d.wait_outputs(1)
        await d.drain(40)
        assert not d.ctx.get(d.dut.busy)
        assert d.ctx.get(d.dut.in_flight_count) == 0

    run(tb)


def test_in_flight_count_caps_and_recovers_under_backpressure():
    """With ``output.ready`` low the count climbs to ``maxOutstanding`` and no more
    requests are allocated; releasing the output drains everything in order."""

    async def tb(d):
        d.ctx.set(d.dut.output.ready, 0)
        n = MAX_OUTSTANDING + 4
        for i in range(n):
            await d.push(pixel(s=float(i), t=0.0))
        await d.drain(400)
        assert d.ctx.get(d.dut.in_flight_count) == MAX_OUTSTANDING
        assert len(d.requests) == MAX_OUTSTANDING
        assert d.outputs == []
        d.ctx.set(d.dut.output.ready, 1)
        await d.wait_outputs(n)
        assert len(d.outputs) == n
        # Solid white memory: every pixel that hit a line already filled is white;
        # the ones that opened a line carry the cold-miss texel 0 (see above).
        assert all(o in ((255, 255, 255, 255), (0, 0, 0, 255)) for o in d.outputs)
        assert (255, 255, 255, 255) in d.outputs
        await d.drain(40)
        assert d.ctx.get(d.dut.in_flight_count) == 0

    run(tb)


def test_bilinear_taps_are_adjacent_texels():
    """The four bilinear addresses are (x, y), (x+1, y), (x, y+1), (x+1, y+1) of the
    texel *below and left* of the sample point (the half-texel offset)."""

    async def tb(d):
        await d.push(pixel(s=10.25, t=20.75, tm=texture_mode(bilinear=True)))
        await d.wait_requests(1)
        r = d.requests[0]
        # 10.25 - 0.5 = 9.75 -> si = 9, ds = 12/16; 20.75 - 0.5 = 20.25 -> ti = 20, dt = 4/16
        row = 1 << 9
        assert r["bi_addr0"] == (20 * row) + (9 << 1)
        assert r["bi_addr1"] == (20 * row) + (10 << 1)
        assert r["bi_addr2"] == (21 * row) + (9 << 1)
        assert r["bi_addr3"] == (21 * row) + (10 << 1)
        assert (r["ds"], r["dt"]) == (12, 4)
        assert r["bilinear"] == 1

    run(tb)
