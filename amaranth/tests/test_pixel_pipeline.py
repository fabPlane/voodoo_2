"""Unit tests for ``avoodoo.core.pixel_pipeline`` -- the Phase 4 assembly.

Three layers:

1. **The port surface** (no simulator): every Spinal leaf of
   ``PixelPipeline_spinal.v`` -- 662 ports, of which 660 are data -- pairs
   mechanically against the Amaranth signature, and the nine never-assigned
   ``CoreDebug`` words are *not* ports (``spinal-naming-and-harness.md``
   §1.7; the module docstring).  The Verilator lockstep run does this too,
   but this one runs without scala-cli or Verilator.

2. **The register-bank glue**: ``controls_from_register_bank`` /
   ``external_busy_from_core`` against a stub with the ``RegisterBank``
   attribute paths, checked field by field in pysim -- the NCC table
   re-packing (four bytes of ``table<N>Y<r>`` into ``y[r*4+b]``, the low 27
   bits of I/Q), the ``dfog:fog`` fog-table pairs, ``trexInit1(18)``, the
   ``fbzModeBundle`` copies that drop the register's reserved bits.

3. **End to end, in pysim**: small flat-shaded triangles through the whole
   pipeline with ``SimMemBusMemory`` on ``texRead`` and a Python plane
   memory on the colour/aux read and write ports.  The written words are
   compared with a *composition of the stage models the other tests already
   have*: ``test_triangle_setup.SetupModel`` + the bounding-box edge scan of
   ``test_rasterizer`` for coverage,
   ``test_color_combine.model`` for the colour, the RGB565 truncation that
   ``Dither`` does when dithering is off, and ``Write``'s lane packing
   (``core_types.plane_address`` / ``pack_word_lane`` / ``word_lane_mask``).
   A depth-buffered pair of triangles then checks the aux plane, the
   ``zFuncFail`` kill and the ``pixelsIn`` / ``pixelsOut`` / ``zFuncFail``
   counters; a textured triangle checks the TMU path (a zero texture memory
   decodes to black) and that ``texRead`` really went to the memory model.
   Per-cycle invariants (the merge priorities, ``pipelineBusy`` while
   anything is in flight, the fork/join handshake) are asserted on every
   tick of every run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from amaranth import Elaboratable, Module, Signal
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.core import core_types
from avoodoo.core.core_types import FramebufferLayout, FbzMode
from avoodoo.core.membus_params import tmu_bmb_params
from avoodoo.core.pixel_pipeline import (
    PixelPipeline,
    PixelPipelineDebug,
    SpanPrefetcher,
    build_palette_write_flow,
    controls_from_register_bank,
    external_busy_from_core,
)
from avoodoo.lib import membus
from avoodoo.sim_membus_memory import SimMemBusMemory
from avoodoo.texture.tmu_types import TextureFormat as TF

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_color_combine import default_tx, model as cc_model, passthrough_config  # noqa: E402
from test_tmu import t_lod, tables_for, texture_mode  # noqa: E402
from test_triangle_setup import SetupInput, SetupModel, sext  # noqa: E402

from avoodoo.core.core_types import AddMode, LocalSel, RgbSel  # noqa: E402

C = Config.voodoo1()
PERIOD = 1e-6

# ---------------------------------------------------------------------------
# 1. the port surface
# ---------------------------------------------------------------------------

#: The nine ``CoreDebug`` words the design never drives (naming doc §1.7).
UNDRIVEN_DEBUG_WORDS = (
    "fb_access", "fb_color_cache", "fb_color_cache_req", "fb_color_cache_expected",
    "fb_color_cache_occupancy", "fb_aux_cache", "fb_aux_cache_req",
    "fb_aux_cache_expected", "fb_aux_cache_occupancy",
)


def test_port_surface_is_the_spinal_one_minus_the_undriven_debug_words():
    """660 data leaves; the Spinal names below all derive mechanically."""
    from tools.diff.amports import amaranth_fields

    fields = amaranth_fields(PixelPipeline(C))
    assert len(fields) == 660
    for spinal, am in {
        "io_triangleCmd_payload_config_texTables_texBase_0":
            "triangle_cmd__payload__config__tex_tables__tex_base__0",
        "io_ftriangleCmd_payload_triWithSign_tri_2_1":
            "ftriangle_cmd__payload__tri_with_sign__tri__2__1",
        "io_controls_paletteRegs_table0I0": "controls__palette_regs__table0_i0",
        "io_controls_paletteRegs_table0Q3": "controls__palette_regs__table0_q3",
        "io_controls_fogTable_63": "controls__fog_table__63",
        "io_controls_nccTables_table1_y_15": "controls__ncc_tables__table1__y__15",
        "io_controls_nccTables_table0_q_3": "controls__ncc_tables__table0__q__3",
        "io_controls_lfb_lfbMode_wSelect": "controls__lfb__lfb_mode__w_select",
        "io_controls_clip_highY": "controls__clip__high_y",
        "io_controls_syncRender_tmuSendConfig": "controls__sync_render__tmu_send_config",
        "io_externalBusy_nopCmdReset": "external_busy__nop_cmd_reset",
        "io_fbStats_readMaxQueueOccupancy": "fb_stats__read_max_queue_occupancy",
        "io_fbStats_writeSingleWordDrainStartAtZeroCount":
            "fb_stats__write_single_word_drain_start_at_zero_count",
        "io_debug_busy_triangleSetupValid": "debug__busy__triangle_setup_valid",
        "io_debug_writePath_fbFillHitsNonZero": "debug__write_path__fb_fill_hits_non_zero",
        "io_debug_pipelineBusySources": "debug__pipeline_busy_sources",
        "io_stats_aFuncFail": "stats__a_func_fail",
        "io_texRead_cmd_payload_fragment_length": "tex_read__cmd__payload__fragment__length",
        "io_lfbBus_cmd_payload_fragment_mask": "lfb_bus__cmd__payload__fragment__mask",
        "io_prefetchAux_payload_endAddress": "prefetch_aux__payload__end_address",
    }.items():
        assert am in fields, (spinal, am, sorted(f for f in fields if f.startswith(am[:12]))[:5])
    for word in UNDRIVEN_DEBUG_WORDS:
        assert f"debug__{word}" not in fields
    assert set(PixelPipelineDebug().members) == {
        "pipeline_busy", "busy", "write_path", "pipeline_busy_sources"}
    assert set(core_types.CoreDebug().members) == set(PixelPipelineDebug().members) | set(
        UNDRIVEN_DEBUG_WORDS)


def test_elaborates_and_span_prefetcher_alone():
    from amaranth.hdl import Fragment

    Fragment.get(PixelPipeline(C), None)
    Fragment.get(SpanPrefetcher(C, color_plane=False), None)


def test_span_prefetcher_clamps_negative_coordinates_like_afix_asuint():
    """``floor(0).asUInt`` on a signed AFix clamps to 0 (``AFix.scala:1203``):
    a span starting left of x = 0 prefetches from column 0, a row above
    y = 0 is row 0 -- and with the y-origin swap that row 0 becomes ``swap``.
    This is what the 1e6-cycle lockstep run caught first (seed 1, cycle
    149826: both addresses off by one row's worth of sign bit).
    """
    dut = SpanPrefetcher(C, color_plane=True)
    got = {}

    async def tb(ctx):
        vf = C.vertex_format
        ctx.set(dut.routing.color_base_addr, 0x1000)
        ctx.set(dut.routing.aux_base_addr, 0x2000)
        ctx.set(dut.routing.pixel_stride, STRIDE)
        ctx.set(dut.span.payload.pixel_config.alpha_mode.alpha_blend_enable, 1)
        ctx.set(dut.span.valid, 1)
        ctx.set(dut.read_req.ready, 1)
        ctx.set(dut.span.payload.x_start.as_value(), q(-3.0, vf.fraction, vf.width))
        ctx.set(dut.span.payload.x_end.as_value(), q(5.25, vf.fraction, vf.width))
        ctx.set(dut.span.payload.y.as_value(), q(-2.0, vf.fraction, vf.width))
        ctx.set(dut.y_origin_enable, 0)
        await ctx.delay(1e-9)
        got["plain"] = (ctx.get(dut.read_req.payload.start_address),
                        ctx.get(dut.read_req.payload.end_address), ctx.get(dut.read_req.valid))
        ctx.set(dut.y_origin_enable, 1)
        ctx.set(dut.y_origin_swap_value, 479)
        await ctx.delay(1e-9)
        got["swapped"] = (ctx.get(dut.read_req.payload.start_address),
                          ctx.get(dut.read_req.payload.end_address))
        ctx.set(dut.span.payload.pixel_config.alpha_mode.alpha_blend_enable, 0)
        ctx.set(dut.fbz_mode.enable_dither_subtract, 0)
        await ctx.delay(1e-9)
        got["not_needed"] = (ctx.get(dut.read_req.valid), ctx.get(dut.span.ready))

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    assert got["plain"] == (plane_address(0x1000, 0, 0, STRIDE), plane_address(0x1000, 5, 0, STRIDE), 1)
    assert got["swapped"] == (plane_address(0x1000, 0, 479, STRIDE), plane_address(0x1000, 5, 479, STRIDE))
    assert got["not_needed"] == (0, 1)


# ---------------------------------------------------------------------------
# 2. Controls.fromRegisterBank / ExternalBusy.fromCore
# ---------------------------------------------------------------------------


class _Stream:
    def __init__(self, width):
        self.valid = Signal(name="valid")
        self.payload = Signal(width, name="payload")


class _RegBankStub:
    """The ``RegisterBank`` attribute paths ``controls_from_register_bank`` reads."""

    def __init__(self):
        from avoodoo.frontend.register_bank import RegisterBank

        rb = RegisterBank(C)
        rb._MustUse__silence = True      # only its signature is wanted
        # Fresh signals with the real port shapes; not the component itself,
        # so nothing has to elaborate the bus interface.
        self.render_config = type("RC", (), {})()
        for name, member in rb.signature.members["render_config"].signature.members.items():
            setattr(self.render_config, name, Signal(member.shape, name=f"rc_{name}"))
        self.init = type("Init", (), {})()
        for name, member in rb.signature.members["init"].signature.members.items():
            setattr(self.init, name, Signal(member.shape, name=f"init_{name}"))
        self.tmu_config = type("TC", (), {})()
        for name, member in rb.signature.members["tmu_config"].signature.members.items():
            setattr(self.tmu_config, name, Signal(member.shape, name=f"tmu_{name}"))
        self.ncc_table = type("Ncc", (), {})()
        for name, member in rb.signature.members["ncc_table"].signature.members.items():
            setattr(self.ncc_table, name, Signal(member.shape, name=f"ncc_{name}"))
        self.fog_table = Signal(rb.signature.members["fog_table"].shape, name="fog_table")
        self.commands = type("Cmds", (), {})()
        self.commands.nop_cmd_stream = _Stream(32)
        self.commands.fastfill_cmd_stream = _Stream(0)
        self.commands.swapbuffer_cmd_stream = _Stream(0)


class _SwapStub:
    def __init__(self):
        self.waiting = Signal(name="waiting")


class _GlueBench(Elaboratable):
    def __init__(self):
        self.rb = _RegBankStub()
        self.swap = _SwapStub()
        self.layout = Signal(FramebufferLayout(C), name="layout")
        self.ctrl = None
        self.busy = None

    def elaborate(self, platform):
        m = Module()
        self.ctrl = controls_from_register_bank(m, C, self.rb, self.layout)
        self.busy = external_busy_from_core(m, self.rb, self.swap)
        return m


def test_controls_from_register_bank_field_by_field():
    bench = _GlueBench()
    rb = bench.rb
    rng_words = [(0x9E3779B9 * (k + 1)) & 0xFFFF_FFFF for k in range(64)]

    async def tb(ctx):
        rc = rb.render_config
        ctx.set(rc.clip_left_x, 0x123)
        ctx.set(rc.clip_right_x, 0x2BC)
        ctx.set(rc.clip_low_y, 0x055)
        ctx.set(rc.clip_high_y, 0x1E0)
        # fbz_mode with every reserved bit set: the bundle copy must drop them
        ctx.set(rc.fbz_mode.as_value(), 0xFFE0_0000 | 0x0004_5A31)
        ctx.set(rc.lfb_mode.as_value(), 0xFFFE_0000 | 0x0001_2345)
        ctx.set(rc.alpha_mode, 0xDEAD_BEEF)
        ctx.set(rc.fog_mode, 0xFFFF_FFDA)
        ctx.set(rc.fog_color, 0x11223344)
        ctx.set(rc.za_color, 0x55667788)
        ctx.set(rc.chroma_key, 0x99AABBCC)
        ctx.set(rc.color0, 0x01020304)
        ctx.set(rc.color1, 0x05060708)
        ctx.set(rb.init.fbi_init3.y_origin_swap, 0x1DF)
        ctx.set(rb.tmu_config.trex_init1, 1 << 18)
        for t, key in ((0, "table0"), (1, "table1")):
            for k, kind in enumerate("yiq"):
                sig = getattr(rb.ncc_table, f"{key}_{kind}")
                for r in range(4):
                    ctx.set(sig[r], rng_words[t * 12 + k * 4 + r])
        for k in range(64):
            ctx.set(rb.fog_table[k].dfog, (k * 3) & 0xFF)
            ctx.set(rb.fog_table[k].fog, (255 - k) & 0xFF)
        ctx.set(bench.layout.draw.color_base_addr, 0x0123456)
        ctx.set(bench.layout.draw.aux_base_addr, 0x0ABCDEF)
        ctx.set(bench.layout.draw.pixel_stride, 0x280)
        ctx.set(bench.layout.lfb_write.color_base_addr, 0x1111111)
        ctx.set(bench.layout.lfb_read.color_base_addr, 0x2222222)
        ctx.set(rb.commands.nop_cmd_stream.valid, 1)
        ctx.set(rb.commands.nop_cmd_stream.payload, 0xFFFF_FFFE)
        ctx.set(rb.commands.fastfill_cmd_stream.valid, 1)
        ctx.set(rb.commands.swapbuffer_cmd_stream.valid, 0)
        ctx.set(bench.swap.waiting, 1)
        await ctx.delay(1e-9)

        ctrl = bench.ctrl
        assert ctx.get(ctrl.clip.left) == 0x123
        assert ctx.get(ctrl.clip.right) == 0x2BC
        assert ctx.get(ctrl.clip.low_y) == 0x055
        assert ctx.get(ctrl.clip.high_y) == 0x1E0
        # fbzModeBundle: the 21 named bits, three times over
        for view in (ctrl.lfb.fbz_mode, ctrl.fastfill.fbz_mode, ctrl.sync_render.fbz_mode):
            assert ctx.get(view.as_value()) == 0x0004_5A31
        assert ctx.get(ctrl.lfb.lfb_mode.as_value()) == 0x0001_2345
        assert ctx.get(ctrl.lfb.alpha_mode.as_value()) == \
            0xDEAD_BEEF & ((1 << core_types.ALPHA_MODE_LAYOUT.size) - 1)
        assert ctx.get(ctrl.lfb.fog_mode.as_value()) == \
            0xFFFF_FFDA & ((1 << core_types.FOG_MODE_LAYOUT.size) - 1)
        assert ctx.get(ctrl.lfb.fog_color) == 0x11223344
        assert ctx.get(ctrl.lfb.za_color) == 0x55667788
        assert ctx.get(ctrl.lfb.write_routing.color_base_addr) == 0x1111111
        assert ctx.get(ctrl.lfb.read_routing.color_base_addr) == 0x2222222
        assert ctx.get(ctrl.fastfill.color1) == 0x05060708
        assert ctx.get(ctrl.fastfill.za_color) == 0x55667788
        assert ctx.get(ctrl.fastfill.routing.color_base_addr) == 0x0123456
        assert ctx.get(ctrl.fastfill.routing.aux_base_addr) == 0x0ABCDEF
        assert ctx.get(ctrl.fastfill.routing.pixel_stride) == 0x280
        assert ctx.get(ctrl.fastfill.y_origin_swap_value) == 0x1DF
        assert ctx.get(ctrl.sync_render.color0) == 0x01020304
        assert ctx.get(ctrl.sync_render.color1) == 0x05060708
        assert ctx.get(ctrl.sync_render.fog_color) == 0x11223344
        assert ctx.get(ctrl.sync_render.chroma_key) == 0x99AABBCC
        assert ctx.get(ctrl.sync_render.za_color) == 0x55667788
        assert ctx.get(ctrl.sync_render.routing.color_base_addr) == 0x0123456
        assert ctx.get(ctrl.sync_render.tmu_send_config) == 1
        for t, tbl in ((0, ctrl.ncc_tables.table0), (1, ctrl.ncc_tables.table1)):
            for r in range(4):
                y_word = rng_words[t * 12 + r]
                for b in range(4):
                    assert ctx.get(tbl.y[r * 4 + b]) == (y_word >> (8 * b)) & 0xFF
            for idx in range(4):
                assert ctx.get(tbl.i[idx]) == rng_words[t * 12 + 4 + idx] & 0x7FF_FFFF
                assert ctx.get(tbl.q[idx]) == rng_words[t * 12 + 8 + idx] & 0x7FF_FFFF
        for k in range(64):
            assert ctx.get(ctrl.fog_table[k]) == (((k * 3) & 0xFF) << 8) | ((255 - k) & 0xFF)
        for k in range(4):
            assert ctx.get(getattr(ctrl.palette_regs, f"table0_i{k}")) == rng_words[4 + k]
            assert ctx.get(getattr(ctrl.palette_regs, f"table0_q{k}")) == rng_words[8 + k]
        busy = bench.busy
        assert ctx.get(busy.nop_cmd) == 1
        assert ctx.get(busy.nop_cmd_reset) == 0        # payload(0) is clear
        assert ctx.get(busy.fastfill_cmd) == 1
        assert ctx.get(busy.swapbuffer_cmd) == 0
        assert ctx.get(busy.swap_waiting) == 1
        ctx.set(rb.commands.nop_cmd_stream.payload, 1)
        ctx.set(rb.tmu_config.trex_init1, ~(1 << 18) & 0xFFFF_FFFF)
        await ctx.delay(1e-9)
        assert ctx.get(busy.nop_cmd_reset) == 1
        assert ctx.get(ctrl.sync_render.tmu_send_config) == 0

    sim = Simulator(bench)
    sim.add_testbench(tb)
    sim.run()


def test_palette_write_flow_is_the_documented_dead_code():
    """One beat per changed register with bit 31 set; odd registers set address bit 0."""
    from avoodoo.core.pixel_pipeline import PaletteRegs

    class Bench(Elaboratable):
        def __init__(self):
            self.regs = Signal(PaletteRegs(), name="regs")
            self.flow = None

        def elaborate(self, platform):
            m = Module()
            self.flow = build_palette_write_flow(m, C, self.regs)
            return m

    bench = Bench()
    seen = []

    async def tb(ctx):
        await ctx.tick()
        for k, name in enumerate(PaletteRegs.FIELDS):
            ctx.set(getattr(bench.regs, name), (1 << 31) | (0x5A << 23) | (0x123456 + k))
            await ctx.delay(1e-9)                      # reg != RegNext(reg): one comb beat
            seen.append((ctx.get(bench.flow.valid), ctx.get(bench.flow.payload.address),
                         ctx.get(bench.flow.payload.data)))
            await ctx.tick()
            await ctx.delay(1e-9)                      # prev caught up: beat gone
            assert ctx.get(bench.flow.valid) == 0
        # bit 31 clear: no beat
        ctx.set(bench.regs.table0_i0, 0x7FFF_FFFF)
        await ctx.delay(1e-9)
        assert ctx.get(bench.flow.valid) == 0
        await ctx.tick()

    sim = Simulator(bench)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()
    assert seen == [(1, (0x5A & 0xFE) | (k & 1), 0x123456 + k) for k in range(8)]


# ---------------------------------------------------------------------------
# 3. end to end
# ---------------------------------------------------------------------------

COLOR_BASE = 0x000000
AUX_BASE = 0x100000
STRIDE = 640            # tilesX = 10 -> 1280 bytes per row
TEX_MEM_BYTES = 64 * 1024


class Bench(Elaboratable):
    """``PixelPipeline`` + ``SimMemBusMemory`` on ``texRead``."""

    def __init__(self):
        self.pp = PixelPipeline(C)
        self.tex = SimMemBusMemory(tmu_bmb_params(C), TEX_MEM_BYTES)

    def elaborate(self, platform):
        m = Module()
        m.submodules.pp = self.pp
        m.submodules.tex = self.tex
        membus.connect(m, self.tex.bus, self.pp.tex_read)
        return m


def flat_fbz(**kw):
    """An ``FbzMode`` dict: colour writes on, everything else off, overrides."""
    d = {name: 0 for name in FbzMode().members}
    d["rgb_buffer_mask"] = 1
    d.update(kw)
    return d


def q(value: float, frac: int, width: int) -> int:
    return int(round(value * (1 << frac))) & ((1 << width) - 1)


def triangle(verts, *, color=(200, 100, 50), alpha=255, depth=0.0, sign_bit=0,
             config=None, textured=False):
    """One ``TriangleSetup.Input`` as a nested dict of raw words."""
    cfg = passthrough_config(zero_other=1, add=AddMode.CLOCAL,
                             local_select=LocalSel.ITERATED,
                             alpha_zero_other=1, alpha_add=AddMode.CLOCAL)
    if textured:
        cfg = passthrough_config(rgb_sel=RgbSel.TEXTURE, reverse_blend=0,
                                 alpha_zero_other=1, alpha_add=AddMode.CLOCAL,
                                 texture_enable=1)
    if config:
        cfg.update(config)
    r, g, b = color
    t = {
        "verts": verts, "sign_bit": sign_bit, "cfg": cfg,
        "grads": {
            "red_grad": q(r, 12, 24), "green_grad": q(g, 12, 24), "blue_grad": q(b, 12, 24),
            "alpha_grad": q(alpha, 12, 24), "depth_grad": q(depth, 12, 32),
            "w_grad": q(1.0, 30, 48), "s_grad": q(8.0, 18, 48), "t_grad": q(8.0, 18, 48),
        },
        "hi_alpha": q(alpha, 30, 46),
        "tex_hi": (q(8.0, 30, 46), q(8.0, 30, 46)),
        "texture_mode": texture_mode(fmt=TF.RGB565) if textured else 0,
        "t_lod": t_lod(),
        "tables": tables_for(fmt=TF.RGB565),
        "color": color, "alpha": alpha, "depth": depth, "textured": textured,
    }
    return t


def expected_color(t):
    """``ColorCombine`` model on the iterated / texture inputs this triangle sets up."""
    tx = default_tx(iterated=t["color"], iterated_alpha=t["alpha"],
                    texture=(0, 0, 0), texture_alpha=255, config=t["cfg"])
    out = cc_model(tx)
    return out["color"], out["alpha"]


def reference_pixels(t):
    """Coverage from the ``TriangleSetup`` model + the bounding-box scan.

    ``test_triangle_setup.SetupModel`` gives the three edge equations and the
    (floor, ceil) pixel ranges exactly as the hardware computes them from the
    vertices and ``signBit``; ``test_rasterizer.reference_pixels`` proved the
    rasterizer covers ``a*x + b*y + c >= 0`` over that box, both ends
    exclusive, rows top to bottom and left to right.  The composition of the
    two is what a triangle command turns into.
    """
    vf = C.vertex_format
    tri = tuple((q(x, vf.fraction, vf.width), q(y, vf.fraction, vf.width)) for x, y in t["verts"])
    out = SetupModel(C).run(SetupInput(tri=tri, sign_bit=t["sign_bit"]))
    cw = C.coefficient_format.width
    edges = [tuple(sext(v, cw) for v in coeff) for coeff in out.coeffs]
    # edgeStart is the edge function at the pixel *centre* of the box's
    # corner (a*(xmin+.5) + b*(ymin+.5) + c); the walker steps it by a / b.
    starts = [sext(v, cw) for v in out.edge_start]

    def pix(raw):
        return sext(raw, vf.width) >> vf.fraction   # floor/ceil already applied

    xmin, xmax = pix(out.xrange[0]), pix(out.xrange[1])
    ymin, ymax = pix(out.yrange[0]), pix(out.yrange[1])
    return [(x, y) for y in range(ymin, ymax) for x in range(xmin, xmax)
            if all(s0 + a * (x - xmin) + b * (y - ymin) >= 0
                   for (a, b, _c), s0 in zip(edges, starts))]


def rgb565(rgb):
    r, g, b = rgb
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def plane_address(base, x, y, stride):
    tiles_x = (stride >> 6) & 0xF
    return (base + ((y * tiles_x) << 7) + (x << 1)) & ((1 << C.address_width) - 1)


class Planes:
    """The colour / aux planes as 16-bit lanes; word writes with byte masks."""

    def __init__(self):
        self.words: dict[int, int] = {}

    def write(self, address, data, mask):
        old = self.words.get(address & ~3, 0)
        new = old
        for lane in range(4):
            if (mask >> lane) & 1:
                new = (new & ~(0xFF << (8 * lane))) | (data & (0xFF << (8 * lane)))
        self.words[address & ~3] = new

    def lane(self, address) -> int:
        w = self.words.get(address & ~3, 0)
        return (w >> 16) & 0xFFFF if address & 2 else w & 0xFFFF

    def pixel(self, base, x, y) -> int | None:
        a = plane_address(base, x, y, STRIDE)
        if (a & ~3) not in self.words:
            return None
        return self.lane(a)


class Driver:
    def __init__(self, ctx, bench, *, fbz):
        self.ctx = ctx
        self.pp = bench.pp
        self.fbz = fbz
        self.planes = Planes()
        self.color_rsp: list[int] = []
        self.aux_rsp: list[int] = []
        self.color_writes = 0
        self.aux_writes = 0
        self.tex_cmds = 0
        self.max_busy_gap = 0
        self.invariant_failures: list[str] = []

    # -- static inputs ----------------------------------------------------

    def set_controls(self):
        ctx, pp = self.ctx, self.pp
        ctx.set(pp.controls.as_value(), 0)
        for view in (pp.controls.lfb.fbz_mode, pp.controls.fastfill.fbz_mode,
                     pp.controls.sync_render.fbz_mode):
            for name, v in self.fbz.items():
                ctx.set(getattr(view, name), v)
        for routing in (pp.controls.fastfill.routing, pp.controls.sync_render.routing,
                        pp.controls.lfb.write_routing, pp.controls.lfb.read_routing):
            ctx.set(routing.color_base_addr, COLOR_BASE)
            ctx.set(routing.aux_base_addr, AUX_BASE)
            ctx.set(routing.pixel_stride, STRIDE)
        ctx.set(pp.controls.clip.right, 640)
        ctx.set(pp.controls.clip.high_y, 480)
        ctx.set(pp.external_busy.as_value(), 0)
        ctx.set(pp.fb_status.as_value(), 0)
        ctx.set(pp.fb_stats.as_value(), 0)
        ctx.set(pp.tmu_invalidate, 0)
        ctx.set(pp.pci_fifo_empty, 1)
        ctx.set(pp.triangle_cmd.valid, 0)
        ctx.set(pp.ftriangle_cmd.valid, 0)
        ctx.set(pp.fastfill_cmd.valid, 0)
        ctx.set(pp.palette_write.valid, 0)
        ctx.set(pp.lfb_bus.cmd.valid, 0)
        ctx.set(pp.lfb_bus.rsp.ready, 1)
        ctx.set(pp.lfb_read_bus.cmd.ready, 1)
        ctx.set(pp.lfb_read_bus.rsp.valid, 0)
        ctx.set(pp.prefetch_color.ready, 1)
        ctx.set(pp.prefetch_aux.ready, 1)
        ctx.set(pp.color_write.ready, 1)
        ctx.set(pp.aux_write.ready, 1)
        ctx.set(pp.color_read_req.ready, 1)
        ctx.set(pp.aux_read_req.ready, 1)

    def set_triangle(self, port, t):
        ctx = self.ctx
        p = port.payload
        ctx.set(p.as_value(), 0)
        vf = C.vertex_format
        for v in range(3):
            for a in range(2):
                ctx.set(p.tri_with_sign.tri[v][a].as_value(), q(t["verts"][v][a], vf.fraction, vf.width))
        ctx.set(p.tri_with_sign.sign_bit, t["sign_bit"])
        for name, raw in t["grads"].items():
            ctx.set(getattr(p.grads, name).start.as_value(), raw)
        ctx.set(p.hi_alpha.start.as_value(), t["hi_alpha"])
        ctx.set(p.tex_hi.s_start.as_value(), t["tex_hi"][0])
        ctx.set(p.tex_hi.t_start.as_value(), t["tex_hi"][1])
        for name, v in t["cfg"].items():
            ctx.set(getattr(p.config.fbz_color_path, name), v)
        ctx.set(p.config.tmu_texture_mode, t["texture_mode"])
        ctx.set(p.config.tmu_tlod, t["t_lod"])
        tb = t["tables"]
        for lod in range(9):
            ctx.set(p.config.tex_tables.tex_base[lod], tb.tex_base[lod])
            ctx.set(p.config.tex_tables.tex_end[lod], tb.tex_end[lod])
            ctx.set(p.config.tex_tables.tex_shift[lod], tb.tex_shift[lod])

    # -- one clock ----------------------------------------------------------

    async def tick(self):
        ctx, pp = self.ctx, self.pp
        for rsp, queue_ in ((pp.color_read_rsp, self.color_rsp), (pp.aux_read_rsp, self.aux_rsp)):
            ctx.set(rsp.valid, int(bool(queue_)))
            ctx.set(rsp.payload.data, queue_[0] if queue_ else 0)
        s = await ctx.tick().sample(
            pp.color_write.valid, pp.color_write.payload.address, pp.color_write.payload.data,
            pp.color_write.payload.mask,
            pp.aux_write.valid, pp.aux_write.payload.address, pp.aux_write.payload.data,
            pp.aux_write.payload.mask,
            pp.color_read_req.valid, pp.color_read_req.payload.address,
            pp.aux_read_req.valid, pp.aux_read_req.payload.address,
            pp.color_read_rsp.valid, pp.color_read_rsp.ready,
            pp.aux_read_rsp.valid, pp.aux_read_rsp.ready,
            pp.tex_read.cmd.valid, pp.tex_read.cmd.ready,
            pp.debug.pipeline_busy, pp.debug.pipeline_busy_sources,
            pp.triangle_cmd.ready, pp.ftriangle_cmd.ready,
            pp.triangle_cmd.valid, pp.ftriangle_cmd.valid,
        )
        (cw_v, cw_a, cw_d, cw_m, aw_v, aw_a, aw_d, aw_m, cr_v, cr_a, ar_v, ar_a,
         crs_v, crs_r, ars_v, ars_r, tc_v, tc_r, busy, busy_src, tri_r, ftri_r,
         tri_v, ftri_v) = s[2:]
        if cw_v:
            self.planes.write(cw_a, cw_d, cw_m)
            self.color_writes += 1
        if aw_v:
            self.planes.write(aw_a, aw_d, aw_m)
            self.aux_writes += 1
        if cr_v:
            self.color_rsp.append(self.planes.lane(cr_a))
        if ar_v:
            self.aux_rsp.append(self.planes.lane(ar_a))
        if crs_v and crs_r:
            self.color_rsp.pop(0)
        if ars_v and ars_r:
            self.aux_rsp.pop(0)
        if tc_v and tc_r:
            self.tex_cmds += 1
        # -- per-cycle invariants ------------------------------------------
        # pipelineBusy is the OR of its 31 sources minus the two fbStatus bits
        want_busy = int(bool(busy_src & ~((1 << 5) | (1 << 6))))
        if busy != want_busy:
            self.invariant_failures.append(f"pipelineBusy {busy} vs sources {busy_src:#x}")
        # assumeOhInput arbiter: ready goes only to the input that is valid
        if (tri_r and not tri_v) or (ftri_r and not ftri_v):
            self.invariant_failures.append(
                f"arbiter ready without valid: tri {tri_v}/{tri_r} ftri {ftri_v}/{ftri_r}")
        return busy

    async def push(self, port, t, *, limit=2000):
        self.set_triangle(port, t)
        self.ctx.set(port.valid, 1)
        for _ in range(limit):
            took = self.ctx.get(port.ready)
            await self.tick()
            if took:
                self.ctx.set(port.valid, 0)
                return
        raise AssertionError("triangle command never accepted")

    async def drain(self, *, limit=20000):
        """Until ``pipelineBusy`` has been low for a while.  The gaps between
        stages (triangle setup done, rasterizer not yet running; the 64-deep
        post-TMU queue draining) can be a few tens of cycles."""
        idle = 0
        for _ in range(limit):
            busy = await self.tick()
            idle = 0 if busy else idle + 1
            if idle > 64:
                return
        raise AssertionError("pipeline did not go idle")

    def stats(self):
        ctx, pp = self.ctx, self.pp
        return {name: ctx.get(getattr(pp.stats, name)) for name in pp.stats.shape().members}


def run(tb, *, fbz):
    bench = Bench()

    async def wrapper(ctx):
        d = Driver(ctx, bench, fbz=fbz)
        d.set_controls()
        await d.tick()
        await tb(d)
        assert not d.invariant_failures, d.invariant_failures[:5]

    sim = Simulator(bench)
    sim.add_clock(PERIOD)
    sim.add_testbench(wrapper)
    sim.run()


def expected_color_plane(triangles):
    """Compose the stage models: coverage x colour x RGB565 x lane packing."""
    planes = Planes()
    for t in triangles:
        color, _alpha = expected_color(t)
        px = rgb565(color)
        for x, y in reference_pixels(t):
            a = plane_address(COLOR_BASE, x, y, STRIDE)
            lane_hi = (a >> 1) & 1
            planes.write(a, px << 16 if lane_hi else px, 0b1100 if lane_hi else 0b0011)
    return planes


TRIANGLES = [
    [(0, 0), (8, 0), (0, 8)],
    [(20, 4), (23, 5), (21, 10)],
    [(100.5, 40.25), (108, 41), (101, 47.75)],
]


@pytest.mark.parametrize("verts", TRIANGLES, ids=["right8", "sliver", "fractional"])
@pytest.mark.parametrize("port", ["triangle_cmd", "ftriangle_cmd"])
def test_flat_triangle_writes_the_colour_plane(verts, port):
    """Coverage from the rasterizer reference, colour from the CC model,
    RGB565 truncation (dithering off), lane packing from ``Write``."""
    t = triangle(verts, color=(200, 100, 50))
    got = {}

    async def tb(d):
        await d.push(getattr(d.pp, port), t)
        await d.drain()
        got["planes"] = d.planes
        got["stats"] = d.stats()
        got["aux_writes"] = d.aux_writes

    run(tb, fbz=flat_fbz())
    want = expected_color_plane([t])
    assert got["planes"].words == want.words
    n = len(reference_pixels(t))
    assert n > 0
    assert got["stats"]["pixels_in"] == n
    assert got["stats"]["pixels_out"] == n
    assert got["stats"]["z_func_fail"] == 0
    assert got["aux_writes"] == 0            # auxBufferMask off


def test_depth_buffered_triangles_and_the_counters():
    """Depth on: the aux plane holds newDepth, a nearer second triangle overwrites,
    a farther third one is killed pixel for pixel and counted in zFuncFail."""
    verts = [(4, 4), (12, 4), (4, 12)]
    fbz = flat_fbz(aux_buffer_mask=1, enable_depth_buffer=1, depth_function=4)  # GREATER
    a = triangle(verts, color=(10, 20, 30), depth=1000.0)
    b = triangle(verts, color=(240, 160, 80), depth=2000.0)
    c = triangle(verts, color=(1, 2, 3), depth=500.0)
    got = {}

    async def tb(d):
        await d.push(d.pp.triangle_cmd, a)
        await d.drain()
        got["after_a"] = dict(d.planes.words)
        await d.push(d.pp.triangle_cmd, b)
        await d.drain()
        got["after_b"] = dict(d.planes.words)
        got["fail_after_b"] = d.stats()["z_func_fail"]
        await d.push(d.pp.triangle_cmd, c)
        await d.drain()
        got["after_c"] = dict(d.planes.words)
        got["stats"] = d.stats()
        got["planes"] = d.planes

    run(tb, fbz=fbz)
    px = reference_pixels(a)
    n = len(px)
    assert n > 0
    planes = got["planes"]
    color_b, _ = expected_color(b)
    for x, y in px:
        assert planes.pixel(COLOR_BASE, x, y) == rgb565(color_b), (x, y)
        assert planes.pixel(AUX_BASE, x, y) == 2000, (x, y)
    assert got["after_a"] != got["after_b"]
    assert got["after_b"] == got["after_c"]              # c was killed entirely
    assert got["fail_after_b"] == 0
    assert got["stats"]["z_func_fail"] == n
    assert got["stats"]["pixels_in"] == 3 * n
    assert got["stats"]["pixels_out"] == 2 * n


def test_textured_triangle_reads_texture_memory():
    """``rgbSel = TEXTURE`` over a zero texture memory: every pixel decodes to
    RGB565 black, and the TMU really fetched through ``texRead``."""
    verts = [(0, 0), (6, 0), (0, 6)]
    t = triangle(verts, color=(200, 100, 50), textured=True)
    got = {}

    async def tb(d):
        await d.push(d.pp.triangle_cmd, t)
        await d.drain()
        got["planes"] = d.planes
        got["tex_cmds"] = d.tex_cmds
        got["stats"] = d.stats()

    run(tb, fbz=flat_fbz())
    px = reference_pixels(t)
    assert px
    for x, y in px:
        assert got["planes"].pixel(COLOR_BASE, x, y) == 0, (x, y)
    assert got["tex_cmds"] >= 1
    assert got["stats"]["pixels_out"] == len(px)


def test_nop_reset_clears_the_counters():
    verts = [(0, 0), (4, 0), (0, 4)]
    t = triangle(verts)
    got = {}

    async def tb(d):
        await d.push(d.pp.triangle_cmd, t)
        await d.drain()
        got["before"] = d.stats()
        d.ctx.set(d.pp.external_busy.nop_cmd_reset, 1)
        await d.tick()
        d.ctx.set(d.pp.external_busy.nop_cmd_reset, 0)
        await d.tick()
        got["after"] = d.stats()

    run(tb, fbz=flat_fbz())
    assert got["before"]["pixels_out"] == len(reference_pixels(t)) > 0
    assert all(v == 0 for v in got["after"].values())
