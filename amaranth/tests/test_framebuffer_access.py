"""Phase 4 unit tests for :mod:`avoodoo.pixel.framebuffer_access`.

There is no ``FramebufferAccessTest.scala`` upstream, so this is a fresh
pysim test against a Python model of
``ext/SpinalVoodoo/src/voodoo/pixel/FramebufferAccess.scala`` written
straight off the Scala:

* :func:`new_depth` -- the Z clamp, W select, ``zaColor`` source select and
  the **17-bit wrapping** depth bias;
* :func:`depth_passes` -- the eight ``depthFunction`` compares;
* :func:`blend_scale` / :func:`blend` -- every ``rgbSrcFact`` /
  ``rgbDstFact`` pair (0..7, 0xF and the undefined codes), ``divBy255``,
  the RGB565 expansion, the dither-subtract ROM lookup via
  :mod:`avoodoo.pixel.dither_tables` and the 9-bit clamped sum;
* :func:`model` -- the whole module for one pixel and its two plane words.

The AFix value in the module is ``depth``, which is only ever read as its
raw SQ(32,12) pattern, so :mod:`avoodoo.lib.fixed_model` is used to build
meaningful depth stimulus (a real number to its raw encoding) and the
arithmetic here is plain integers.

The pysim harness (:func:`run_pixels`) answers the two read-request streams
from a lazily random plane memory with a per-request random latency, holds
random ``output.ready``, and checks per-cycle invariants: the Spinal hold
rule on the three output streams, ``zFuncFail`` only with a fetched beat,
``busy`` while anything is inside.  Ordering is checked by pairing every
output beat with the next surviving model pixel.

**Read-needs are held constant within a run** (four combinations of
``needColorRead`` / ``needAuxRead``): the module's bypass-vs-fetched
priority mux lets a pixel that needs no read overtake an older one whose
response is still outstanding (see the module docstring), and
:func:`test_bypass_overtakes_an_outstanding_read` pins that as-ported
behaviour directly rather than letting it blur the model comparison.

The cycle-exact gate is ``make diff MOD=FramebufferAccess`` (and the
``FramebufferAccessMem`` row); this test is the readable statement of what
the module computes.
"""

from __future__ import annotations

import random

import pytest
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.lib.fixed_model import ModelShape, const as afix_const
from avoodoo.pixel import dither_tables as dt
from avoodoo.pixel.framebuffer_access import FramebufferAccess

PERIOD = 1e-6


# ---------------------------------------------------------------------------
# Python model
# ---------------------------------------------------------------------------


def u(v, bits):
    """Spinal's ``resize`` to a narrower ``UInt``: keep the low bits."""
    return v & ((1 << bits) - 1)


def sx(v, bits):
    """Reinterpret the low ``bits`` of ``v`` as a two's-complement ``SInt``."""
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def new_depth(tx):
    """Step 1 -- ``compDepth``."""
    za_depth = tx["za_color"] & 0xFFFF
    # `(depthRaw >> 12).resize(20 bits)`: arithmetic shift of the SInt(32)
    z_shifted = sx(tx["depth"], 32) >> 12
    if z_shifted < 0:
        z_depth = 0
    elif z_shifted > 0xFFFF:
        z_depth = 0xFFFF
    else:
        z_depth = z_shifted
    base = tx["w_depth"] if tx["w_buffer_select"] else z_depth
    pre_bias = za_depth if tx["depth_source_select"] else base
    # SInt(17) + SInt(17) does not grow: the sum wraps at 17 bits
    biased_raw = sx(pre_bias + sx(za_depth, 16), 17)
    if biased_raw < 0:
        biased = 0
    elif biased_raw > 0xFFFF:
        biased = 0xFFFF
    else:
        biased = biased_raw
    return biased if tx["enable_depth_bias"] else pre_bias


def depth_passes(func, new, old):
    """Step 3 -- ``depthPassed``."""
    return {
        0: False, 1: new < old, 2: new == old, 3: new <= old,
        4: new > old, 5: new != old, 6: new >= old, 7: True,
    }[func]


def expand_to_8(v, width):
    reps = -(-8 // width)
    wide = 0
    for _ in range(reps):
        wide = (wide << width) | (v & ((1 << width) - 1))
    return wide >> (reps * width - 8)


def expand_rgb565(word):
    return (expand_to_8(word >> 11, 5), expand_to_8((word >> 5) & 0x3F, 6),
            expand_to_8(word & 0x1F, 5))


def dither_sub(color, x, y, use2x2):
    """``destDsub`` -- the two ``dsub*Packed`` ROMs via the packed address."""
    r, g, b = color
    return (dt.dsub_rb_packed[dt.packed_index(r, y & 3, x & 3, use2x2)],
            dt.dsub_g_packed[dt.packed_index(g, y & 3, x & 3, use2x2)],
            dt.dsub_rb_packed[dt.packed_index(b, y & 3, x & 3, use2x2)])


def div_by_255(v):
    wide = u(v, 17)
    return u((u(u(wide + 1, 17) + (wide >> 8), 17)) >> 8, 8)


def blend_mul(factor, color):
    return div_by_255(factor * color)


def one_minus(x):
    return u(255 - x, 8)


def blend_scale(sel, src_alpha, dest_alpha, color_factor, *, is_src, col_before_fog):
    """``computeBlendScale`` -- an ``(r, g, b)`` scale triple."""
    if sel == 0x0:
        return (0, 0, 0)
    if sel == 0x1:
        return (src_alpha,) * 3
    if sel == 0x2:
        return tuple(color_factor)
    if sel == 0x3:
        return (dest_alpha,) * 3
    if sel == 0x4:
        return (255,) * 3
    if sel == 0x5:
        return (one_minus(src_alpha),) * 3
    if sel == 0x6:
        return tuple(one_minus(c) for c in color_factor)
    if sel == 0x7:
        return (one_minus(dest_alpha),) * 3
    if sel == 0xF:
        if is_src:
            return (min(src_alpha, one_minus(dest_alpha)),) * 3
        return tuple(col_before_fog)
    return (255,) * 3 if is_src else (0,) * 3


def blend(tx, color_word):
    """Step 4 + 5 -- the output ``color`` for one pixel and its old colour word."""
    src = (tx["r"], tx["g"], tx["b"])
    if not tx["alpha_blend_enable"]:
        return src
    dest = expand_rgb565(color_word)
    if tx["enable_dither_subtract"]:
        dest = dither_sub(dest, tx["x"], tx["y"], tx["dither_algorithm"])
    cbf = (tx["cbf_r"], tx["cbf_g"], tx["cbf_b"])
    src_scale = blend_scale(tx["rgb_src_fact"], tx["alpha"], 0xFF, dest,
                            is_src=True, col_before_fog=cbf)
    dst_scale = blend_scale(tx["rgb_dst_fact"], tx["alpha"], 0xFF, src,
                            is_src=False, col_before_fog=cbf)
    out = []
    for i in range(3):
        s = blend_mul(src_scale[i], src[i])
        d = blend_mul(dst_scale[i], dest[i])
        out.append(min(u(s + d, 9), 255))
    return tuple(out)


def model(tx, color_word, aux_word):
    """The whole module for one pixel; ``None`` if the depth test kills it."""
    nd = new_depth(tx)
    if tx["enable_depth_buffer"] and not depth_passes(tx["depth_function"], nd, aux_word):
        return None
    return {
        "coords": (tx["x"], tx["y"]),
        "color": blend(tx, color_word),
        "alpha": tx["alpha"],
        "color_before_fog": (tx["cbf_r"], tx["cbf_g"], tx["cbf_b"]),
        "new_depth": nd,
        "chroma_key": tx["chroma_key"],
        "rgb_write": tx["rgb_buffer_mask"],
        "aux_write": tx["aux_buffer_mask"],
        "enable_alpha_planes": tx["enable_alpha_planes"],
        "enable_dithering": tx["enable_dithering"],
        "dither_algorithm": tx["dither_algorithm"],
        "rgb_src_fact": tx["rgb_src_fact"],
        "rgb_dst_fact": tx["rgb_dst_fact"],
        "routing": (tx["color_base"], tx["aux_base"], tx["stride"]),
    }


def need_color_read(tx):
    return bool(tx["alpha_blend_enable"] or tx["enable_dither_subtract"])


def need_aux_read(tx):
    return bool(tx["enable_depth_buffer"])


def plane_address(base, x, y, stride, aw=26):
    """``FramebufferAddressMath.planeAddress`` with the module's ``## 0`` lane clear."""
    tiles_x = (stride >> 6) & 0xF
    row_bytes = u((u(y * tiles_x, 12 + 4)) << 7, aw)
    addr = u(base + u(row_bytes + u(x << 1, aw), aw), aw)
    return addr & ~1


# ---------------------------------------------------------------------------
# stimulus
# ---------------------------------------------------------------------------


DEFAULT_TX = {
    "x": 0, "y": 0, "r": 0, "g": 0, "b": 0, "alpha": 0, "depth": 0, "w_depth": 0,
    "cbf_r": 0, "cbf_g": 0, "cbf_b": 0, "chroma_key": 0, "za_color": 0,
    "alpha_blend_enable": 0, "rgb_src_fact": 0, "rgb_dst_fact": 0,
    "w_buffer_select": 0, "enable_depth_buffer": 0, "depth_function": 7,
    "enable_dithering": 0, "rgb_buffer_mask": 1, "aux_buffer_mask": 1,
    "dither_algorithm": 0, "enable_depth_bias": 0, "enable_alpha_planes": 0,
    "enable_dither_subtract": 0, "depth_source_select": 0,
    "color_base": 0, "aux_base": 0x100000, "stride": 640,
}


def tx(**kwargs):
    out = dict(DEFAULT_TX)
    out.update(kwargs)
    return out


def afix_depth_raw(c, value):
    """The raw SQ(32,12) encoding of a real ``value``, via the AFix model."""
    shape = ModelShape.from_q(c.v_depth_format)
    return u(afix_const(shape, value).raw, 32)


def random_tx(rng, *, color_read=None, aux_read=None):
    """A random pixel; ``color_read``/``aux_read`` pin the read-needs when given."""
    t = {
        "x": rng.randrange(1 << 12), "y": rng.randrange(1 << 12),
        "r": rng.randrange(256), "g": rng.randrange(256), "b": rng.randrange(256),
        "alpha": rng.randrange(256),
        "depth": rng.randrange(1 << 32), "w_depth": rng.randrange(1 << 16),
        "cbf_r": rng.randrange(256), "cbf_g": rng.randrange(256),
        "cbf_b": rng.randrange(256),
        "chroma_key": rng.randrange(1 << 32), "za_color": rng.randrange(1 << 32),
        "alpha_blend_enable": rng.randrange(2),
        "rgb_src_fact": rng.randrange(16), "rgb_dst_fact": rng.randrange(16),
        "w_buffer_select": rng.randrange(2), "enable_depth_buffer": rng.randrange(2),
        "depth_function": rng.randrange(8), "enable_dithering": rng.randrange(2),
        "rgb_buffer_mask": rng.randrange(2), "aux_buffer_mask": rng.randrange(2),
        "dither_algorithm": rng.randrange(2), "enable_depth_bias": rng.randrange(2),
        "enable_alpha_planes": rng.randrange(2),
        "enable_dither_subtract": rng.randrange(2),
        "depth_source_select": rng.randrange(2),
        "color_base": rng.randrange(1 << 26), "aux_base": rng.randrange(1 << 26),
        "stride": rng.randrange(1 << 11),
    }
    if color_read is not None:
        if color_read:
            if not (t["alpha_blend_enable"] or t["enable_dither_subtract"]):
                t["alpha_blend_enable"] = 1
        else:
            t["alpha_blend_enable"] = t["enable_dither_subtract"] = 0
    if aux_read is not None:
        t["enable_depth_buffer"] = int(aux_read)
    return t


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _set_input(ctx, dut, t):
    p = dut.input.payload
    ctx.set(p.coords.x, sx(t["x"], 12))
    ctx.set(p.coords.y, sx(t["y"], 12))
    ctx.set(p.color.r, t["r"])
    ctx.set(p.color.g, t["g"])
    ctx.set(p.color.b, t["b"])
    ctx.set(p.alpha, t["alpha"])
    ctx.set(p.depth.as_value(), sx(t["depth"], 32))
    ctx.set(p.w_depth, t["w_depth"])
    ctx.set(p.color_before_fog.r, t["cbf_r"])
    ctx.set(p.color_before_fog.g, t["cbf_g"])
    ctx.set(p.color_before_fog.b, t["cbf_b"])
    ctx.set(p.chroma_key, t["chroma_key"])
    ctx.set(p.za_color, t["za_color"])
    ctx.set(p.alpha_mode.alpha_blend_enable, t["alpha_blend_enable"])
    ctx.set(p.alpha_mode.rgb_src_fact, t["rgb_src_fact"])
    ctx.set(p.alpha_mode.rgb_dst_fact, t["rgb_dst_fact"])
    fm = p.fbz_mode
    ctx.set(fm.w_buffer_select, t["w_buffer_select"])
    ctx.set(fm.enable_depth_buffer, t["enable_depth_buffer"])
    ctx.set(fm.depth_function, t["depth_function"])
    ctx.set(fm.enable_dithering, t["enable_dithering"])
    ctx.set(fm.rgb_buffer_mask, t["rgb_buffer_mask"])
    ctx.set(fm.aux_buffer_mask, t["aux_buffer_mask"])
    ctx.set(fm.dither_algorithm, t["dither_algorithm"])
    ctx.set(fm.enable_depth_bias, t["enable_depth_bias"])
    ctx.set(fm.enable_alpha_planes, t["enable_alpha_planes"])
    ctx.set(fm.enable_dither_subtract, t["enable_dither_subtract"])
    ctx.set(fm.depth_source_select, t["depth_source_select"])
    ctx.set(p.routing.color_base_addr, t["color_base"])
    ctx.set(p.routing.aux_base_addr, t["aux_base"])
    ctx.set(p.routing.pixel_stride, t["stride"])


def _out_signals(dut):
    p = dut.output.payload
    return (p.coords.x, p.coords.y, p.color.r, p.color.g, p.color.b, p.alpha,
            p.color_before_fog.r, p.color_before_fog.g, p.color_before_fog.b,
            p.new_depth, p.chroma_key, p.rgb_write, p.aux_write,
            p.enable_alpha_planes, p.enable_dithering, p.dither_algorithm,
            p.alpha_mode.rgb_src_fact, p.alpha_mode.rgb_dst_fact,
            p.routing.color_base_addr, p.routing.aux_base_addr, p.routing.pixel_stride)


def _as_dict(sample):
    (x, y, cr, cg, cb, alpha, br, bg, bb, nd, ck, rw, aw, eap, ed, da, sf, df,
     cba, aba, st) = sample
    return {
        "coords": (u(x, 12), u(y, 12)),
        "color": (cr, cg, cb),
        "alpha": alpha,
        "color_before_fog": (br, bg, bb),
        "new_depth": nd,
        "chroma_key": ck,
        "rgb_write": rw,
        "aux_write": aw,
        "enable_alpha_planes": eap,
        "enable_dithering": ed,
        "dither_algorithm": da,
        "rgb_src_fact": sf,
        "rgb_dst_fact": df,
        "routing": (cba, aba, st),
    }


class PlaneMem:
    """A lazily random 16-bit lane memory behind one request/response pair."""

    def __init__(self, rng, *, max_latency, fixed=None):
        self.rng = rng
        self.words = {} if fixed is None else dict(fixed)
        self.max_latency = max_latency
        self.pending = []      # [remaining_wait, data]
        self.reqs = []         # addresses seen, in order

    def word(self, addr):
        if addr not in self.words:
            self.words[addr] = self.rng.getrandbits(16)
        return self.words[addr]

    def request(self, addr):
        self.reqs.append(addr)
        self.pending.append([self.rng.randrange(self.max_latency + 1), self.word(addr)])


def run_pixels(transactions, *, seed=0, max_latency=3, ready_density=0.7,
               req_ready_density=0.8, rsp_density=0.8, color_mem=None, aux_mem=None,
               c=None, budget_per_pixel=40):
    """Push ``transactions`` through the DUT against the two plane memories.

    Returns ``(outputs, color_mem, aux_mem, stats)``; every output beat is a
    dict from :func:`_as_dict`.  Invariants are asserted every cycle.
    """
    c = c if c is not None else Config.voodoo1()
    dut = FramebufferAccess(c)
    rng = random.Random(seed)
    color = PlaneMem(random.Random(seed + 1), max_latency=max_latency, fixed=color_mem)
    aux = PlaneMem(random.Random(seed + 2), max_latency=max_latency, fixed=aux_mem)
    got = []
    stats = {"z_func_fail": 0, "busy_cycles": 0, "cycles": 0, "max_in_flight": 0}

    async def bench(ctx):
        pending = list(transactions)
        outs = _out_signals(dut)
        budget = budget_per_pixel * (len(transactions) + 8) + 64
        cycle = 0
        in_flight = 0
        # hold-rule state: (valid, payload) as last seen on each output stream
        held = {"out": None, "creq": None, "areq": None}
        presenting = {"color": None, "aux": None}   # response beats on the wire
        done_out = 0

        while cycle < budget:
            # ---- drive this cycle's inputs ---------------------------------
            ready = int(rng.random() < ready_density)
            ctx.set(dut.output.ready, ready)
            creq_ready = int(rng.random() < req_ready_density)
            areq_ready = int(rng.random() < req_ready_density)
            ctx.set(dut.fb_read_color_req.ready, creq_ready)
            ctx.set(dut.fb_read_aux_req.ready, areq_ready)
            for name, mem, port in (("color", color, dut.fb_read_color_rsp),
                                    ("aux", aux, dut.fb_read_aux_rsp)):
                if presenting[name] is None:
                    for entry in mem.pending:
                        if entry[0] > 0:
                            entry[0] -= 1
                    if mem.pending and mem.pending[0][0] == 0 and rng.random() < rsp_density:
                        presenting[name] = mem.pending.pop(0)[1]
                if presenting[name] is None:
                    ctx.set(port.valid, 0)
                else:
                    ctx.set(port.valid, 1)
                    ctx.set(port.payload.data, presenting[name])
            if pending:
                _set_input(ctx, dut, pending[0])
                ctx.set(dut.input.valid, 1)
            else:
                ctx.set(dut.input.valid, 0)

            # ---- settle and observe the pre-edge handshakes ---------------
            in_fire = bool(pending) and ctx.get(dut.input.ready)
            out_valid = ctx.get(dut.output.valid)
            out_fire = out_valid and ready
            creq_valid = ctx.get(dut.fb_read_color_req.valid)
            areq_valid = ctx.get(dut.fb_read_aux_req.valid)
            creq_addr = ctx.get(dut.fb_read_color_req.payload.address)
            areq_addr = ctx.get(dut.fb_read_aux_req.payload.address)
            crsp_fire = presenting["color"] is not None and ctx.get(dut.fb_read_color_rsp.ready)
            arsp_fire = presenting["aux"] is not None and ctx.get(dut.fb_read_aux_rsp.ready)
            z_fail = ctx.get(dut.z_func_fail)
            busy = ctx.get(dut.busy)
            debug = ctx.get(dut.debug)
            fetched_valid, fetched_ready = (debug >> 25) & 1, (debug >> 26) & 1
            sample = [ctx.get(s) for s in outs]

            # ---- invariants -------------------------------------------------
            # Spinal hold rule: while valid && !ready the payload is frozen.
            for key, valid, ok, payload in (
                ("out", out_valid, ready, tuple(sample)),
                ("creq", creq_valid, creq_ready, creq_addr),
                ("areq", areq_valid, areq_ready, areq_addr),
            ):
                if held[key] is not None:
                    assert valid, f"cycle {cycle}: {key} dropped valid while stalled"
                    assert payload == held[key], f"cycle {cycle}: {key} payload moved while stalled"
                held[key] = payload if (valid and not ok) else None
            # zFuncFail is `fetched.fire && depthKill`
            if z_fail:
                assert fetched_valid and fetched_ready, f"cycle {cycle}: zFuncFail without fetched.fire"
            # busy reflects the in-flight count (mod 32 -- see the module docstring)
            assert busy == int(in_flight % 32 != 0), f"cycle {cycle}: busy={busy}, in_flight={in_flight}"

            # ---- bookkeeping for the edge -----------------------------------
            if creq_valid and creq_ready:
                color.request(creq_addr)
            if areq_valid and areq_ready:
                aux.request(areq_addr)
            if crsp_fire:
                presenting["color"] = None
            if arsp_fire:
                presenting["aux"] = None
            if out_fire:
                got.append(_as_dict(sample))
                done_out += 1
            stats["z_func_fail"] += int(z_fail)
            stats["busy_cycles"] += int(busy)
            if in_fire:
                pending.pop(0)
                in_flight += 1
            if fetched_valid and fetched_ready:
                in_flight -= 1
            stats["max_in_flight"] = max(stats["max_in_flight"], in_flight)

            await ctx.tick()
            cycle += 1
            stats["cycles"] = cycle
            if not pending and in_flight == 0 and not any(presenting.values()) \
                    and not color.pending and not aux.pending:
                # drain the two pipeline registers after the fetched queue
                idle = 0
                while idle < 8:
                    ready = int(rng.random() < ready_density)
                    ctx.set(dut.output.ready, ready)
                    out_valid = ctx.get(dut.output.valid)
                    sample = [ctx.get(s) for s in outs]
                    if out_valid and ready:
                        got.append(_as_dict(sample))
                        idle = 0
                    elif not out_valid:
                        idle += 1
                    await ctx.tick()
                    cycle += 1
                stats["cycles"] = cycle
                return
        raise AssertionError(f"did not drain in {budget} cycles ({len(got)} beats out, "
                             f"{len(pending)} pending, {in_flight} in flight)")

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(bench)
    sim.run()
    return got, color, aux, stats


def expected(transactions, color, aux):
    """What the model says should come out, given the plane memories' contents."""
    want = []
    for t in transactions:
        cw = color.word(plane_address(t["color_base"], t["x"], t["y"], t["stride"])) \
            if need_color_read(t) else 0
        aw = aux.word(plane_address(t["aux_base"], t["x"], t["y"], t["stride"])) \
            if need_aux_read(t) else 0
        m = model(t, cw, aw)
        if m is not None:
            want.append(m)
    return want


def check(transactions, **kwargs):
    got, color, aux, stats = run_pixels(transactions, **kwargs)
    want = expected(transactions, color, aux)
    assert len(got) == len(want), f"{len(got)} beats out, model expects {len(want)}"
    for i, (w, g) in enumerate(zip(want, got)):
        for key, value in w.items():
            assert g[key] == value, (
                f"beat {i} field {key}: expected {value!r}, got {g[key]!r}")
    # the request streams carried exactly the reads the model expected, in order
    assert color.reqs == [plane_address(t["color_base"], t["x"], t["y"], t["stride"])
                          for t in transactions if need_color_read(t)]
    assert aux.reqs == [plane_address(t["aux_base"], t["x"], t["y"], t["stride"])
                        for t in transactions if need_aux_read(t)]
    killed = sum(1 for t in transactions
                 if model(t, 0, aux.word(plane_address(t["aux_base"], t["x"], t["y"],
                                                       t["stride"]))) is None)
    assert stats["z_func_fail"] == killed
    return got, stats


# ---------------------------------------------------------------------------
# model-level tests (pure Python, no simulation)
# ---------------------------------------------------------------------------


def test_depth_bias_wraps_at_17_bits():
    """``compDepthPreBias.resize(17).asSInt + zaColorSigned.resize(17)`` does not grow."""
    # 0xFFFF + 0x7FFF = 98302 > 65535: wraps negative, clamps to 0 (not 0xFFFF)
    t = tx(w_buffer_select=1, w_depth=0xFFFF, enable_depth_bias=1, za_color=0x7FFF)
    assert new_depth(t) == 0
    # one less and it still saturates high the honest way
    t = tx(w_buffer_select=1, w_depth=0x8000, enable_depth_bias=1, za_color=0x7FFF)
    assert new_depth(t) == 0xFFFF
    # a negative bias clamps at 0
    t = tx(w_buffer_select=1, w_depth=0x0010, enable_depth_bias=1, za_color=0xFFF0)
    assert new_depth(t) == 0
    # zaColor as the source, then biased by itself
    t = tx(depth_source_select=1, enable_depth_bias=1, za_color=0x1234)
    assert new_depth(t) == 0x2468


def test_z_depth_clamp():
    c = Config.voodoo1()
    assert new_depth(tx(depth=afix_depth_raw(c, 1000.0))) == 1000
    assert new_depth(tx(depth=afix_depth_raw(c, 65535.75))) == 0xFFFF
    assert new_depth(tx(depth=afix_depth_raw(c, 70000.0))) == 0xFFFF
    assert new_depth(tx(depth=afix_depth_raw(c, -1.0))) == 0
    assert new_depth(tx(depth=afix_depth_raw(c, 0.5))) == 0


def test_div_by_255_is_exact():
    for a in range(256):
        for b in range(0, 256, 5):
            assert div_by_255(a * b) == (a * b) // 255, (a, b)
    assert div_by_255(255 * 255) == 255


# ---------------------------------------------------------------------------
# simulation tests
# ---------------------------------------------------------------------------


def test_pass_through_no_reads():
    """Neither plane is read: no requests, latency 6, everything but color from the input."""
    txs = [tx(x=i, y=i * 3, r=i, g=2 * i, b=3 * i, alpha=i, chroma_key=0xABCD00 + i,
              depth=afix_depth_raw(Config.voodoo1(), 100.0 * i), rgb_buffer_mask=i & 1,
              aux_buffer_mask=(i >> 1) & 1, enable_dithering=1, dither_algorithm=i & 1)
           for i in range(8)]
    got, stats = check(txs, ready_density=1.0)
    for t, g in zip(txs, got):
        assert g["color"] == (t["r"], t["g"], t["b"])
        assert g["new_depth"] == 100 * txs.index(t)
    assert stats["max_in_flight"] <= 8


def test_latency_is_six_without_reads():
    dut = FramebufferAccess(Config.voodoo1())
    seen = []

    async def bench(ctx):
        ctx.set(dut.output.ready, 1)
        _set_input(ctx, dut, tx(r=1, g=2, b=3))
        ctx.set(dut.input.valid, 1)
        await ctx.tick()
        ctx.set(dut.input.valid, 0)
        for _ in range(10):
            _, _, valid, busy = await ctx.tick().sample(dut.output.valid, dut.busy)
            seen.append((valid, busy))

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(bench)
    sim.run()
    valids = [v for v, _ in seen]
    assert valids.count(1) == 1
    assert valids.index(1) == 5, f"latency {valids.index(1) + 1}, expected 6"


@pytest.mark.parametrize("func", range(8))
def test_each_depth_function(func):
    """Aux plane read, every compare, ``zFuncFail`` counted."""
    rng = random.Random(0x100 + func)
    txs = []
    for _ in range(24):
        t = random_tx(rng, color_read=False, aux_read=True)
        t["depth_function"] = func
        t["enable_depth_bias"] = 0
        t["depth_source_select"] = 1          # newDepth = zaColor[15:0]
        txs.append(t)
    # fix the aux memory so equal / unequal both happen for functions 2 and 5
    aux_mem = {}
    for i, t in enumerate(txs):
        addr = plane_address(t["aux_base"], t["x"], t["y"], t["stride"])
        aux_mem[addr] = (t["za_color"] & 0xFFFF) if i % 3 == 0 else rng.getrandbits(16)
    got, stats = check(txs, aux_mem=aux_mem, seed=func)
    survivors = [t for t in txs if depth_passes(
        func, t["za_color"] & 0xFFFF,
        aux_mem[plane_address(t["aux_base"], t["x"], t["y"], t["stride"])])]
    assert len(got) == len(survivors)
    if func == 0:
        assert got == []
    if func == 7:
        assert len(got) == len(txs)


@pytest.mark.parametrize("src_fact", list(range(8)) + [0xF, 0x8, 0xB])
def test_each_source_factor(src_fact):
    rng = random.Random(0x200 + src_fact)
    txs = []
    for _ in range(12):
        t = random_tx(rng, color_read=True, aux_read=False)
        t["rgb_src_fact"] = src_fact
        t["alpha_blend_enable"] = 1
        txs.append(t)
    check(txs, seed=src_fact)


@pytest.mark.parametrize("dst_fact", list(range(8)) + [0xF, 0x9, 0xC])
def test_each_dest_factor(dst_fact):
    rng = random.Random(0x300 + dst_fact)
    txs = []
    for _ in range(12):
        t = random_tx(rng, color_read=True, aux_read=False)
        t["rgb_dst_fact"] = dst_fact
        t["alpha_blend_enable"] = 1
        txs.append(t)
    check(txs, seed=dst_fact)


def test_dither_subtract_reads_the_colour_plane_without_blending():
    """``needColorRead`` = blend OR ditherSubtract; without blend the colour is the source."""
    rng = random.Random(7)
    txs = []
    for _ in range(12):
        t = random_tx(rng, color_read=True, aux_read=False)
        t["alpha_blend_enable"] = 0
        t["enable_dither_subtract"] = 1
        txs.append(t)
    got, _ = check(txs)
    for t, g in zip(txs, got):
        assert g["color"] == (t["r"], t["g"], t["b"])


def test_dither_subtract_changes_the_destination():
    """With blend enabled and ONE/ZERO factors the output is the (dsub'd) old colour."""
    rng = random.Random(8)
    base = dict(alpha_blend_enable=1, rgb_src_fact=0, rgb_dst_fact=4,
                enable_depth_buffer=0)
    txs = [tx(x=rng.randrange(1 << 12), y=rng.randrange(1 << 12), enable_dither_subtract=ds,
              dither_algorithm=rng.randrange(2), **base)
           for ds in (0, 1) * 6]
    white = {plane_address(0, t["x"], t["y"], 640): 0xFFFF for t in txs}
    got, _ = check(txs, ready_density=1.0, color_mem=white)
    assert len(got) == len(txs)
    # ZERO*src + ONE*dest: the output *is* the destination -- white when
    # taken as is, and the ROM's per-cell subtraction of it otherwise
    for t, g in zip(txs, got):
        if t["enable_dither_subtract"]:
            assert g["color"] == dither_sub((255, 255, 255), t["x"], t["y"],
                                            t["dither_algorithm"])
        else:
            assert g["color"] == (255, 255, 255)


@pytest.mark.parametrize("color_read,aux_read", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_random_traffic_per_read_mix(color_read, aux_read):
    """Random pixels, random modes, both planes, random latencies and back-pressure."""
    rng = random.Random(0x400 + 2 * color_read + aux_read)
    txs = [random_tx(rng, color_read=bool(color_read), aux_read=bool(aux_read))
           for _ in range(48)]
    got, stats = check(txs, seed=rng.randrange(1 << 16), ready_density=0.5,
                       req_ready_density=0.6, rsp_density=0.6, max_latency=5)
    if aux_read:
        assert stats["z_func_fail"] > 0
    assert stats["max_in_flight"] > 2


def test_backpressure_fills_the_queues():
    """``output.ready`` held low: the 5-bit ``inFlightCount`` wraps (as-ported).

    With the output blocked, ``fetched`` still fires 34 times (32 into its
    own queue, two into the pipeline registers) and then stalls, while the
    fork keeps accepting pixels into the three 32-deep control queues, so
    more than 31 pixels sit between ``io.input`` and ``fetched`` and the
    counter passes 31 -> 0.  ``busy`` is then wrong on both sides alike.
    """
    rng = random.Random(9)
    txs = [random_tx(rng, color_read=False, aux_read=False) for _ in range(90)]
    dut = FramebufferAccess(Config.voodoo1())
    trace = []          # (counter, busy, accepted so far, fetched fires so far)

    async def bench(ctx):
        ctx.set(dut.output.ready, 0)
        pending = list(txs)
        accepted = fetched_fires = 0
        for cycle in range(200):
            if pending:
                _set_input(ctx, dut, pending[0])
                ctx.set(dut.input.valid, 1)
            else:
                ctx.set(dut.input.valid, 0)
            in_fire = bool(pending) and ctx.get(dut.input.ready)
            debug = ctx.get(dut.debug)
            trace.append((debug >> 27, ctx.get(dut.busy), accepted, fetched_fires))
            if (debug >> 25) & 1 and (debug >> 26) & 1:
                fetched_fires += 1
            await ctx.tick()
            if in_fire:
                pending.pop(0)
                accepted += 1
        assert pending, "the fork eventually stalls with the output blocked"

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(bench)
    sim.run()
    counters = [t[0] for t in trace]
    assert 31 in counters, "the 5-bit counter reaches its top"
    wrapped = [i for i in range(1, len(trace)) if trace[i - 1][0] == 31 and trace[i][0] == 0]
    assert wrapped, "the counter wraps 31 -> 0"
    i = wrapped[0]
    _, busy, accepted, fires = trace[i]
    assert accepted - fires == 32, "exactly 32 pixels between input and fetched at the wrap"
    assert not busy, "busy reads low with 32 pixels inside"
    assert max(t[2] - t[3] for t in trace) > 32
    # every sample agrees with `inFlightCount == (accepted - fetched) mod 32`
    assert all(n == (a - f) % 32 for n, _, a, f in trace)


def test_bypass_overtakes_an_outstanding_read():
    """As-ported ordering hazard: a no-read pixel passes a read pixel whose response is late.

    Pixel A needs the colour plane, pixel B does not.  With the response
    held back, ``colorBypass`` (B's record, the head of ``colorCtlBase``)
    fires before ``colorFetched`` (A's record + the late response), so the
    ``passMeta`` join pairs B's bypass data (0) with A's passthrough and A's
    fetched word with B's.  The output order is still A, B (``passMeta`` is
    in order) -- but A blends against 0 and B against A's word.
    """
    a = tx(x=1, y=1, r=200, g=200, b=200, alpha_blend_enable=1, rgb_src_fact=0,
           rgb_dst_fact=4, color_base=0)               # out = old colour
    b = tx(x=2, y=1, r=10, g=20, b=30)                # no reads at all
    old_word = 0xFFFF                                 # expands to white
    got, color, aux, _ = run_pixels([a, b], max_latency=12, ready_density=1.0,
                                    req_ready_density=1.0, rsp_density=1.0,
                                    color_mem={plane_address(0, 1, 1, 640): old_word},
                                    seed=3)
    assert [g["coords"] for g in got] == [(1, 1), (2, 1)]
    assert got[1]["color"] == (10, 20, 30), "B never blends, whatever data it was paired with"
    # A got the bypass's zero word, not its own read
    assert got[0]["color"] == (0, 0, 0), got[0]["color"]
    # Even a response on the very next cycle is too late: the bypass record
    # reaches the mux (queue latency 2) before the fetched join's two queues
    # (ctl 2 + route 2, response 1 + 2) can line up.
    got2, *_ = run_pixels([a, b], max_latency=0, ready_density=1.0,
                          req_ready_density=1.0, rsp_density=1.0,
                          color_mem={plane_address(0, 1, 1, 640): old_word}, seed=3)
    assert got2[0]["color"] == (0, 0, 0)
    # A on its own, or after B, blends against its own word
    for order in ([a], [b, a]):
        got3, *_ = run_pixels(order, max_latency=12, ready_density=1.0,
                              req_ready_density=1.0, rsp_density=1.0,
                              color_mem={plane_address(0, 1, 1, 640): old_word}, seed=3)
        assert got3[-1]["color"] == (255, 255, 255)
