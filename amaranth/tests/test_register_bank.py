"""Phase 2.2 / 1.6 unit tests for :mod:`avoodoo.frontend.register_bank` and
:mod:`avoodoo.bus.bus_interface`.

Three layers:

1. **Table and naming checks** (no simulation).  The regif field indices the
   Scala bakes into its ``sim_reg_%03x_f%d`` names are recomputed from
   ``regdef`` and pinned against the committed ``--dump-regmeta`` ground truth;
   the local ``camel_to_snake`` copy is pinned against the diff harness's; the
   metadata the elaborated ``BusInterface`` collected is pinned against
   ``regdef``.

2. **The intent of ``ext/SpinalVoodoo/test/RegisterBankTest.scala``** as pysim
   testbenches: status read, vertex/gradient/clip/init/fog writes, RW
   read-back, RO statistics, the four command pulses, command back-pressure,
   sync pulse -- plus the side effects that test file does not reach (byte
   strobes, ``clutData`` -> ``gammaLut``, the YIQ palette flow, both float
   shadow write sources, the ``ftriangleCMD`` payload and the packed texture
   tables).  There is no ``BmbBusInterfaceTest.scala`` upstream (the name in
   the plan is aspirational); the bus-protocol claims are asserted here on the
   ``RegisterBank`` instance since the adapter has no other host.

3. **Trace replay** (plan Phase 2 exit): the register-write prefix of the
   packed Glide demo traces under ``fpga/hw/gowin/console/demo_*.bin`` is
   pushed through the PCI front door / address remap / float-alias conversion
   in Python, driven at the register bank's bus one write per cycle (with
   random idle gaps and random stream back-pressure), and every cycle's bus
   response, command-stream ``valid``/payload and ``commandReady`` are compared
   against :class:`RegisterBankModel`, a cycle-accurate Python model of the
   Scala.  A read of every written register is interleaved so the read mux is
   checked on the same addresses.

The cycle-exact gate against the Spinal Verilog is ``make diff
MOD=RegisterBank`` (``tests/diff/test_diff_register_bank.py`` is the short
version); the model here is what says the *behaviour* is the intended one.
"""

from __future__ import annotations

import json
import random
import struct
from fractions import Fraction
from pathlib import Path

import pytest
from amaranth.hdl import Shape
from amaranth.lib import data
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.core.core_types import (
    ALPHA_MODE_LAYOUT,
    FBZ_COLOR_PATH_LAYOUT,
    FOG_MODE_LAYOUT,
    triangle_setup_input_layout,
)
from avoodoo.frontend import regdef
from avoodoo.frontend.regdef import Access
from avoodoo.frontend.register_bank import (
    FLOAT_SHADOW_ADDRS,
    RegisterBank,
    output_path,
    spinal_field_index,
    spinal_port_overrides,
    spinal_sim_reg_name,
    _snake,
)
from avoodoo.lib.fixed import AFixShape
from avoodoo.lib.membus import CMD_READ, CMD_WRITE
from avoodoo.texture import tex_layout_tables as tlt

CFG = Config.voodoo1()
PERIOD = 1e-6
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEMO_DIR = REPO / "fpga" / "hw" / "gowin" / "console"
GROUND_TRUTH = HERE / "data" / "registerbank_regmeta.json"

HI_W = AFixShape.from_q(CFG.tex_coords_hi_format).bit_width   # 46


# ---------------------------------------------------------------------------
# integer helpers
# ---------------------------------------------------------------------------


def mask(bits: int) -> int:
    return (1 << bits) - 1


def sx(v: int, bits: int) -> int:
    """Reinterpret the low ``bits`` of ``v`` as two's complement."""
    v &= mask(bits)
    return v - (1 << bits) if v >> (bits - 1) else v


def resize_s(v: int, from_bits: int, to_bits: int) -> int:
    """``SInt.resize``: sign-extend or keep the low bits; returns the signed value."""
    return sx(sx(v, from_bits), to_bits)


def shl_keep(v: int, n: int, bits: int) -> int:
    """``|<<``: width-preserving left shift, as an unsigned word."""
    return (v << n) & mask(bits)


# ---------------------------------------------------------------------------
# 1. table / naming
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def truth() -> dict:
    with GROUND_TRUTH.open() as fp:
        return json.load(fp)


def test_snake_matches_diff_harness_rule():
    from tools.diff.naming import camel_to_snake

    names = set()
    for reg in regdef.REGISTERS:
        names.add(reg.name)
        for f in reg.fields:
            names.add(f.name)
    names |= {"triangleGeometry", "startValues", "gradX", "gradY", "renderConfig",
              "tmuConfig", "nccTable", "fogTable", "commandReady", "gammaLut",
              "swapDisplayedBuffer", "pciFifoFree", "floatShadow", "triangleCmdStream"}
    for n in names:
        assert _snake(n) == camel_to_snake(n), n


def test_spinal_field_indices_match_regmeta(truth):
    """``sim_reg_<addr>_f<N>``: ``N`` counts regif's NA padding the way the Scala does."""
    by_addr = {r["addr"]: r for r in truth["registers"]}
    checked = 0
    for reg in regdef.registers(include_sim_only=False):
        if not reg.fields:
            continue
        truth_fields = by_addr[reg.addr]["fields"]
        for f in reg.fields:
            idx = spinal_field_index(reg, f)
            tf = truth_fields[idx]
            assert (tf["lo"], tf["hi"], tf["access"]) == (f.lo, f.hi, f.access.value), \
                f"{reg.name}.{f.name}: index {idx} is {tf}"
            checked += 1
    assert checked == 279 - 2   # every field but the two sim-only debug words


def test_spinal_port_overrides_cover_every_asOutput_field():
    """231 ``sim_reg_*`` output ports on the Spinal side, one override each."""
    ov = spinal_port_overrides()
    want = 0
    for reg in regdef.registers(include_sim_only=False):
        for f in reg.fields:
            if f.qformat is None and output_path(reg, f) is not None:
                want += 1
                assert ov[spinal_sim_reg_name(reg, f)] == "__".join(output_path(reg, f))
    assert len(ov) == want == 231
    # spot checks against RegisterBank_spinal.v's port list
    assert ov["sim_reg_110_f0"] == "render_config__fbz_mode__enable_clipping"
    assert ov["sim_reg_118_f2"] == "render_config__clip_left_x"
    assert ov["sim_reg_214_f4"] == "init__fbi_init1__video_tiles_x"
    assert ov["sim_reg_244_f1"] == "init__fbi_init5__multi_cvg"
    assert ov["sim_reg_160_f3"] == "fog_table__1__fog"
    assert ov["sim_reg_128_f1"] == "commands__swap_interval"
    assert ov["sim_reg_380_f0"] == "ncc_table__table1_q__3"
    assert "sim_reg_120_f0" not in ov and "sim_reg_124_f0" not in ov
    assert "sim_reg_008_f0" not in ov   # AFix: exported as triangleGeometry_vertices_vertexAx


def test_busif_metadata_matches_regdef():
    """``getCategories`` / ``getFloatAliases`` / ``getCommandStreamReady`` == regdef."""
    from amaranth.hdl import Fragment

    dut = RegisterBank(CFG)
    Fragment.get(dut, None)     # elaborate
    busif = dut.busif
    assert busif.get_categories() == regdef.categories()
    got = busif.get_float_aliases()
    want = regdef.float_aliases()
    assert set(got) == set(want)
    for addr, alias in want.items():
        g = got[addr]
        assert (g.target_addr, g.int_bits, g.frac_bits) == \
            (alias.target_addr, alias.int_bits, alias.frac_bits), hex(addr)
    assert tuple(busif.get_command_stream_ready()) == regdef.command_addresses()
    assert len(busif.slices) == len(regdef.registers(include_sim_only=False))


def test_simulation_flag_adds_debug_registers():
    from amaranth.hdl import Fragment

    dut = RegisterBank(CFG, simulation=True)
    Fragment.get(dut, None)
    addrs = {s.addr for s in dut.busif.slices}
    assert {0x240, 0x250} <= addrs
    assert len(dut.busif.slices) == len(regdef.registers(include_sim_only=True))


# ---------------------------------------------------------------------------
# simulation plumbing
# ---------------------------------------------------------------------------


def run(dut, tb):
    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()


def set_defaults(ctx, dut):
    """``RegisterBankTest.setupDut``."""
    ctx.set(dut.status_inputs.v_retrace, 1)
    ctx.set(dut.status_inputs.mem_fifo_free, 0xFFFF)
    ctx.set(dut.status_inputs.pci_interrupt, 0)
    ctx.set(dut.swap_displayed_buffer, 0)
    ctx.set(dut.swaps_pending, 0)
    ctx.set(dut.statistics.as_value(), 0)
    ctx.set(dut.debug.as_value(), 0)
    ctx.set(dut.pci_fifo_empty, 1)
    ctx.set(dut.pci_fifo_free, 64)
    ctx.set(dut.swap_cmd_enqueued, 0)
    ctx.set(dut.sync_drained, 0)
    ctx.set(dut.float_shadow.valid, 0)
    ctx.set(dut.bus.cmd.valid, 0)
    ctx.set(dut.bus.rsp.ready, 1)
    for s in streams(dut):
        ctx.set(s.ready, 1)


def streams(dut):
    c = dut.commands
    return [c.triangle_cmd_stream, c.ftriangle_cmd_stream, c.nop_cmd_stream,
            c.fastfill_cmd_stream, c.swapbuffer_cmd_stream]


def put_cmd(ctx, dut, *, valid=1, write=True, addr=0, data=0, strb=0xF, source=0):
    frag = dut.bus.cmd.payload.fragment
    ctx.set(dut.bus.cmd.valid, valid)
    ctx.set(dut.bus.cmd.payload.last, 1)
    ctx.set(frag.opcode, CMD_WRITE if write else CMD_READ)
    ctx.set(frag.address, addr)
    ctx.set(frag.data, data & 0xFFFFFFFF)
    ctx.set(frag.mask, strb)
    ctx.set(frag.source, source)
    ctx.set(frag.length, 0)


async def bmb_write(ctx, dut, addr, data, strb=0xF, source=0):
    """``BmbDriver.write`` + the two settle cycles ``bmbWrite`` adds."""
    put_cmd(ctx, dut, write=True, addr=addr, data=data, strb=strb, source=source)
    await ctx.tick()
    put_cmd(ctx, dut, valid=0)
    await ctx.tick()
    await ctx.tick()


async def bmb_read(ctx, dut, addr, source=0):
    """``BmbDriver.read``: the response comes one cycle after the command."""
    put_cmd(ctx, dut, write=False, addr=addr, source=source)
    await ctx.tick()
    put_cmd(ctx, dut, valid=0)
    assert ctx.get(dut.bus.rsp.valid) == 1
    assert ctx.get(dut.bus.rsp.payload.fragment.source) == source
    assert ctx.get(dut.bus.rsp.payload.last) == 1
    assert ctx.get(dut.bus.rsp.payload.fragment.opcode) == 0
    value = ctx.get(dut.bus.rsp.payload.fragment.data)
    await ctx.tick()
    assert ctx.get(dut.bus.rsp.valid) == 0
    return value


def raw_of(ctx, afix) -> int:
    """Unsigned raw word of an AFix-typed port."""
    return ctx.get(afix.raw) & mask(len(afix.raw))


# ---------------------------------------------------------------------------
# 2. RegisterBankTest.scala intent
# ---------------------------------------------------------------------------


def test_status_register_reads_hardware_inputs():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        ctx.set(dut.debug.pipeline_busy, 1)
        ctx.set(dut.swap_displayed_buffer, 1)
        ctx.set(dut.swaps_pending, 2)
        await ctx.tick().repeat(2)
        status = await bmb_read(ctx, dut, 0x000)
        assert status & 0x3F == 0x3F            # 64 saturates to 63
        assert (status >> 6) & 1 == 1           # vRetrace
        assert (status >> 7) & 1 == 1 and (status >> 8) & 1 == 1 and (status >> 9) & 1 == 1
        assert (status >> 10) & 3 == 1
        assert (status >> 12) & 0xFFFF == 0xFFFF
        assert (status >> 28) & 7 == 2
        # below 64 the count passes through
        ctx.set(dut.pci_fifo_free, 17)
        assert (await bmb_read(ctx, dut, 0x000)) & 0x3F == 17
        # writes to the status word do nothing
        await bmb_write(ctx, dut, 0x000, 0xFFFFFFFF)
        assert (await bmb_read(ctx, dut, 0x000)) & 0x3F == 17

    run(dut, tb)


def test_vertex_registers_write_and_reset_values():
    dut = RegisterBank(CFG)
    tg = dut.triangle_geometry

    async def tb(ctx):
        set_defaults(ctx, dut)
        # reset values: startR/G/B = 255 << 12, everything else 0
        assert raw_of(ctx, tg.start_values.start_r) == 255 << 12
        assert raw_of(ctx, tg.start_values.start_g) == 255 << 12
        assert raw_of(ctx, tg.start_values.start_b) == 255 << 12
        assert raw_of(ctx, tg.start_values.start_a) == 0
        assert ctx.get(dut.render_config.clip_right_x) == 0x3FF
        assert ctx.get(dut.render_config.clip_high_y) == 0x3FF
        assert ctx.get(dut.init.fbi_init1.video_tiles_x) == 10
        await bmb_write(ctx, dut, 0x008, 1608)
        await bmb_write(ctx, dut, 0x00C, 2004)
        assert raw_of(ctx, tg.vertices.vertex_ax) == 1608
        assert raw_of(ctx, tg.vertices.vertex_ay) == 2004
        # "Registers write immediately (no FIFO)": 16-bit field keeps the low half
        await bmb_write(ctx, dut, 0x008, 0x12345678)
        assert raw_of(ctx, tg.vertices.vertex_ax) == 0x5678
        # WO: reads back as zero
        assert await bmb_read(ctx, dut, 0x008) == 0

    run(dut, tb)


def test_triangle_gradients_store():
    dut = RegisterBank(CFG)
    tg = dut.triangle_geometry

    async def tb(ctx):
        set_defaults(ctx, dut)
        for addr, v in [(0x040, 0x123456), (0x044, 0x789ABC), (0x048, 0xDEF012),
                        (0x060, 0x111111), (0x064, 0x222222), (0x068, 0x333333)]:
            await bmb_write(ctx, dut, addr, v)
        assert raw_of(ctx, tg.grad_x.d_rd_x) == 0x123456
        assert raw_of(ctx, tg.grad_x.d_gd_x) == 0x789ABC
        assert raw_of(ctx, tg.grad_x.d_bd_x) == 0xDEF012
        assert raw_of(ctx, tg.grad_y.d_rd_y) == 0x111111
        assert raw_of(ctx, tg.grad_y.d_gd_y) == 0x222222
        assert raw_of(ctx, tg.grad_y.d_bd_y) == 0x333333

    run(dut, tb)


def test_rendering_mode_registers_read_write():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        await bmb_write(ctx, dut, 0x104, 0xDEADBEEF)
        assert ctx.get(dut.render_config.fbz_color_path) == 0xDEADBEEF
        assert await bmb_read(ctx, dut, 0x104) == 0xDEADBEEF
        # byte strobes: only the enabled lanes change
        await bmb_write(ctx, dut, 0x104, 0x00000000, strb=0b0110)
        assert ctx.get(dut.render_config.fbz_color_path) == 0xDE0000EF
        await bmb_write(ctx, dut, 0x104, 0x11223344, strb=0b1001)
        assert await bmb_read(ctx, dut, 0x104) == 0x11000044
        # a sub-word field on a strobed write (fbzMode.drawBuffer is [15:14])
        await bmb_write(ctx, dut, 0x110, 0xFFFFFFFF, strb=0b0010)
        assert ctx.get(dut.render_config.fbz_mode.draw_buffer) == 3
        assert ctx.get(dut.render_config.fbz_mode.enable_clipping) == 0
        assert await bmb_read(ctx, dut, 0x110) == 0x0000FF00
        assert ctx.get(dut.render_config.fbz_mode.reserved) == 0

    run(dut, tb)


def test_clipping_registers():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        left_x, right_x = 10, 640
        await bmb_write(ctx, dut, 0x118, (left_x << 16) | right_x)
        assert ctx.get(dut.render_config.clip_left_x) == left_x
        assert ctx.get(dut.render_config.clip_right_x) == right_x
        back = await bmb_read(ctx, dut, 0x118)
        assert back & 0x3FF == right_x and (back >> 16) & 0x3FF == left_x
        assert back == (left_x << 16) | right_x   # the NA gaps read as zero
        await bmb_write(ctx, dut, 0x11C, (20 << 16) | 480)
        assert ctx.get(dut.render_config.clip_low_y) == 20
        assert ctx.get(dut.render_config.clip_high_y) == 480

    run(dut, tb)


def test_statistics_registers_are_read_only():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        ctx.set(dut.statistics.pixels_in, 12345)
        ctx.set(dut.statistics.chroma_fail, 100)
        ctx.set(dut.statistics.pixels_out, 0xABCDEF)
        await ctx.tick().repeat(2)
        assert (await bmb_read(ctx, dut, 0x14C)) & 0xFFFFFF == 12345
        assert (await bmb_read(ctx, dut, 0x150)) & 0xFFFFFF == 100
        assert await bmb_read(ctx, dut, 0x15C) == 0xABCDEF
        await bmb_write(ctx, dut, 0x14C, 99999)
        assert (await bmb_read(ctx, dut, 0x14C)) & 0xFFFFFF == 12345
        # unmapped and no-field addresses read as zero
        assert await bmb_read(ctx, dut, 0x004) == 0
        assert await bmb_read(ctx, dut, 0x080) == 0
        assert await bmb_read(ctx, dut, 0xFFC) == 0

    run(dut, tb)


def test_fog_table_registers():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        for i in range(32):
            e0d, e0f = (i * 2) & 0xFF, (i * 2 + 1) & 0xFF
            e1d, e1f = (i * 2 + 32) & 0xFF, (i * 2 + 33) & 0xFF
            await bmb_write(ctx, dut, 0x160 + i * 4, (e1f << 24) | (e1d << 16) | (e0f << 8) | e0d)
        await ctx.tick().repeat(2)
        for i in range(32):
            assert ctx.get(dut.fog_table[i * 2].dfog) == (i * 2) & 0xFF
            assert ctx.get(dut.fog_table[i * 2].fog) == (i * 2 + 1) & 0xFF
            assert ctx.get(dut.fog_table[i * 2 + 1].dfog) == (i * 2 + 32) & 0xFF
            assert ctx.get(dut.fog_table[i * 2 + 1].fog) == (i * 2 + 33) & 0xFF
        assert await bmb_read(ctx, dut, 0x160) == 0   # WO

    run(dut, tb)


def test_init_registers_read_write():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        await bmb_write(ctx, dut, 0x210, 0x3)
        assert ctx.get(dut.init.fbi_init0.vga_passthrough) == 1
        assert ctx.get(dut.init.fbi_init0.graphics_reset) == 1
        assert (await bmb_read(ctx, dut, 0x210)) & 0x3 == 0x3
        await bmb_write(ctx, dut, 0x200, 0x1)
        assert ctx.get(dut.init.fbi_init4.pci_read_wait_states) == 1
        await bmb_write(ctx, dut, 0x218, (75 << 11) | (2 << 9))
        assert ctx.get(dut.init.fbi_init2.swap_algorithm) == 2
        assert ctx.get(dut.init.fbi_init2.buffer_offset) == 75
        assert await bmb_read(ctx, dut, 0x218) == (75 << 11) | (2 << 9)
        # undeclared bits are not stored
        await bmb_write(ctx, dut, 0x214, 0xFFFFFFFF)
        assert await bmb_read(ctx, dut, 0x214) == 0x1F6
        assert ctx.get(dut.init.fbi_init1.video_tiles_x) == 0xF
        await bmb_write(ctx, dut, 0x20C, (480 << 16) | 639)
        assert ctx.get(dut.init.video_dimensions.h_disp) == 639
        assert ctx.get(dut.init.video_dimensions.v_disp) == 480
        assert await bmb_read(ctx, dut, 0x20C) == 0   # WO fields
        await bmb_write(ctx, dut, 0x30C, 0x123456)
        assert ctx.get(dut.tmu_config.tex_base_addr) == 0x123456
        await bmb_write(ctx, dut, 0x328, 0xCAFEBABE)
        assert ctx.get(dut.ncc_table.table0_y[1]) == 0xCAFEBABE

    run(dut, tb)


def test_sync_pulse_follows_sync_drained():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        assert ctx.get(dut.sync_pulse) == 0
        ctx.set(dut.sync_drained, 1)
        await ctx.tick()
        ctx.set(dut.sync_drained, 0)
        assert ctx.get(dut.sync_pulse) == 1
        await ctx.tick()
        assert ctx.get(dut.sync_pulse) == 0
        ctx.set(dut.pci_fifo_empty, 0)
        assert ctx.get(dut.fifo_empty) == 0

    run(dut, tb)


@pytest.mark.parametrize("addr,index", [(0x080, 0), (0x100, 1), (0x120, 2), (0x124, 3), (0x128, 4)])
def test_command_registers_pulse_once(addr, index):
    """``valid`` for exactly one cycle, one cycle after the write, when ready."""
    dut = RegisterBank(CFG)
    strm = streams(dut)[index]

    async def tb(ctx):
        set_defaults(ctx, dut)
        assert ctx.get(strm.valid) == 0
        seen = []
        put_cmd(ctx, dut, write=True, addr=addr, data=1)
        for _ in range(10):
            await ctx.tick()
            put_cmd(ctx, dut, valid=0)
            seen.append(ctx.get(strm.valid))
        assert seen == [1] + [0] * 9
        assert all(ctx.get(dut.command_ready[i]) == 1 for i in range(5))

    run(dut, tb)


def test_nop_payload_and_swapbuffer_fields():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        put_cmd(ctx, dut, write=True, addr=0x120, data=0xA5A5_0001)
        await ctx.tick()
        put_cmd(ctx, dut, valid=0)
        assert ctx.get(dut.commands.nop_cmd_stream.valid) == 1
        assert ctx.get(dut.commands.nop_cmd_stream.payload) == 0xA5A5_0001
        assert await bmb_read(ctx, dut, 0x120) == 0xA5A5_0001    # RW data word
        await bmb_write(ctx, dut, 0x128, (0x12 << 1) | 1)
        assert ctx.get(dut.commands.swap_vsync_enable) == 1
        assert ctx.get(dut.commands.swap_interval) == 0x12
        assert await bmb_read(ctx, dut, 0x128) == (0x12 << 1) | 1

    run(dut, tb)


def test_command_backpressure_two_deep():
    """s2mPipe + m2sPipe hold two commands; ``commandReady`` drops on the second."""
    dut = RegisterBank(CFG)
    tri = dut.commands.triangle_cmd_stream

    async def tb(ctx):
        set_defaults(ctx, dut)
        ctx.set(tri.ready, 0)
        # first command lands in the m2s register
        await bmb_write(ctx, dut, 0x080, 1)
        assert ctx.get(tri.valid) == 1
        assert ctx.get(dut.command_ready[0]) == 1
        # second is parked in the s2m register: ready drops
        await bmb_write(ctx, dut, 0x080, 1)
        assert ctx.get(tri.valid) == 1
        assert ctx.get(dut.command_ready[0]) == 0
        await ctx.tick().repeat(5)
        assert ctx.get(tri.valid) == 1 and ctx.get(dut.command_ready[0]) == 0
        # release: two beats fire back to back, then idle
        ctx.set(tri.ready, 1)
        fires = []
        for _ in range(4):
            fires.append(ctx.get(tri.valid) & ctx.get(tri.ready))
            await ctx.tick()
        assert fires == [1, 1, 0, 0]
        assert ctx.get(dut.command_ready[0]) == 1
        # part 2: with ready high from the start, a single-cycle pulse
        seen = []
        put_cmd(ctx, dut, write=True, addr=0x080, data=1)
        for _ in range(10):
            await ctx.tick()
            put_cmd(ctx, dut, valid=0)
            seen.append(ctx.get(tri.valid))
        assert seen == [1] + [0] * 9

    run(dut, tb)


def test_clut_data_updates_gamma_lut():
    dut = RegisterBank(CFG)

    async def tb(ctx):
        set_defaults(ctx, dut)
        for i in range(32):
            assert ctx.get(dut.gamma_lut[i]) == ((i * 8) << 16) | ((i * 8) << 8) | (i * 8)
        assert ctx.get(dut.gamma_lut[32]) == 0xFFFFFF
        await bmb_write(ctx, dut, 0x228, (5 << 24) | 0x123456)
        assert ctx.get(dut.gamma_lut[5]) == 0x123456
        await bmb_write(ctx, dut, 0x228, (32 << 24) | 0xABCDEF)
        assert ctx.get(dut.gamma_lut[32]) == 0xABCDEF
        await bmb_write(ctx, dut, 0x228, (33 << 24) | 0x000001)     # index > 32: ignored
        assert all(ctx.get(dut.gamma_lut[i]) != 1 for i in range(33))
        assert ctx.get(dut.init.clut_data) == (33 << 24) | 1        # the word itself is stored
        # a floatShadow beat in the same cycle takes priority over the CLUT write
        ctx.set(dut.float_shadow.valid, 1)
        ctx.set(dut.float_shadow.payload.address, 0x034)
        ctx.set(dut.float_shadow.payload.raw, 77)
        put_cmd(ctx, dut, write=True, addr=0x228, data=(7 << 24) | 0x777777)
        await ctx.tick()
        ctx.set(dut.float_shadow.valid, 0)
        put_cmd(ctx, dut, valid=0)
        await ctx.tick()
        assert ctx.get(dut.gamma_lut[7]) != 0x777777
        assert ctx.get(dut.init.clut_data) == (7 << 24) | 0x777777

    run(dut, tb)


def test_palette_write_flow():
    dut = RegisterBank(CFG)
    pw = dut.palette_write

    async def tb(ctx):
        set_defaults(ctx, dut)
        # NCC table 0 I/Q writes with bit 31 set are palette writes
        put_cmd(ctx, dut, write=True, addr=0x334, data=(1 << 31) | (0x37 << 23) | 0xABCDEF)
        assert ctx.get(pw.valid) == 1
        assert ctx.get(pw.payload.address) == 0x36     # even entry, even register
        assert ctx.get(pw.payload.data) == 0xABCDEF
        await ctx.tick()
        put_cmd(ctx, dut, write=True, addr=0x350, data=(1 << 31) | (0x37 << 23) | 0x000001)
        assert ctx.get(pw.valid) == 1
        assert ctx.get(pw.payload.address) == 0x37     # odd register
        await ctx.tick()
        # bit 31 clear: an ordinary NCC write
        put_cmd(ctx, dut, write=True, addr=0x334, data=0x7FFFFFFF)
        assert ctx.get(pw.valid) == 0
        await ctx.tick()
        put_cmd(ctx, dut, valid=0)
        assert ctx.get(dut.ncc_table.table0_i[0]) == 0x7FFFFFFF
        # table 1 has no palette path
        put_cmd(ctx, dut, write=True, addr=0x364, data=(1 << 31) | 0x1234)
        assert ctx.get(pw.valid) == 0
        await ctx.tick()
        put_cmd(ctx, dut, valid=0)
        assert ctx.get(pw.valid) == 0

    run(dut, tb)


def get_payload(ctx, strm):
    payload = strm.payload
    return ctx.get(payload.as_value() if hasattr(payload, "as_value") else payload)


def _width(shape) -> int:
    return shape.size if isinstance(shape, data.Layout) else Shape.cast(shape).width


def unpack(layout, word: int):
    """Unpack an integer into a nested dict/list by ``layout``."""
    if isinstance(layout, data.ArrayLayout):
        elem, w = layout.elem_shape, _width(layout.elem_shape)
        return [unpack(elem, (word >> (i * w)) & mask(w)) for i in range(layout.length)]
    if isinstance(layout, data.Layout):
        return {key: unpack(f.shape, (word >> f.offset) & mask(_width(f.shape)))
                for key, f in layout}
    return word


def test_ftriangle_payload_uses_float_shadow_and_fixed_paths():
    """Both write sources of the shadow registers, seen through the two commands."""
    dut = RegisterBank(CFG)
    layout = triangle_setup_input_layout(CFG)
    tri, ftri = dut.commands.triangle_cmd_stream, dut.commands.ftriangle_cmd_stream

    async def tb(ctx):
        set_defaults(ctx, dut)
        # fixed writes fill both the register and the shadow (shadow = data << shift)
        await bmb_write(ctx, dut, 0x034, 0xFFFF_F000)       # startS = -4096 (14.18)
        await bmb_write(ctx, dut, 0x030, 0x0080_0000)       # startA (24-bit field: 0x800000)
        await bmb_write(ctx, dut, 0x03C, 0x8000_0001)       # startW
        await bmb_write(ctx, dut, 0x008, 0x0123)            # vertexAx
        await bmb_write(ctx, dut, 0x104, 0x0ABCDEF1)        # fbzColorPath
        await ctx.tick().repeat(2)

        put_cmd(ctx, dut, write=True, addr=0x100, data=(1 << 31))
        await ctx.tick()
        put_cmd(ctx, dut, valid=0)
        assert ctx.get(ftri.valid) == 1
        p = unpack(layout, get_payload(ctx, ftri))
        assert p["tri_with_sign"]["sign_bit"] == 1
        assert p["tri_with_sign"]["tri"][0][0] == 0x0123
        s_hi = shl_keep(resize_s(0xFFFF_F000, 32, HI_W) & mask(HI_W), 12, HI_W)
        assert p["tex_hi"]["s_start"] == s_hi
        assert p["hi_alpha"]["start"] == shl_keep(0x800000, 18, HI_W)
        assert p["grads"]["w_grad"]["start"] == resize_s(0x8000_0001, 32, 48) & mask(48)
        assert p["grads"]["alpha_grad"]["start"] == 0x800000
        assert p["grads"]["s_grad"]["start"] == resize_s(0xFFFF_F000, 32, 48) & mask(48)
        assert pack(FBZ_COLOR_PATH_LAYOUT, p["config"]["fbz_color_path"]) == \
            0x0ABCDEF1 & mask(FBZ_COLOR_PATH_LAYOUT.size)
        await ctx.tick()
        assert ctx.get(ftri.valid) == 0

        # a floatShadow beat overrides the shadow; the fixed register is untouched
        ctx.set(dut.float_shadow.valid, 1)
        ctx.set(dut.float_shadow.payload.address, 0x034)
        ctx.set(dut.float_shadow.payload.raw, -5)
        ctx.set(dut.float_shadow.payload.address, 0x034)
        await ctx.tick()
        ctx.set(dut.float_shadow.payload.address, 0x03C)
        ctx.set(dut.float_shadow.payload.raw, -(1 << 49))
        await ctx.tick()
        ctx.set(dut.float_shadow.valid, 0)
        put_cmd(ctx, dut, write=True, addr=0x100, data=0)
        await ctx.tick()
        put_cmd(ctx, dut, valid=0)
        p = unpack(layout, get_payload(ctx, ftri))
        assert p["tri_with_sign"]["sign_bit"] == 0
        assert p["tex_hi"]["s_start"] == (-5) & mask(HI_W)              # raw.resize(46)
        assert p["grads"]["w_grad"]["start"] == (-(1 << 49)) & mask(48)  # 50 -> 60 -> 48 bits
        assert p["grads"]["s_grad"]["start"] == resize_s(0xFFFF_F000, 32, 48) & mask(48)
        assert raw_of(ctx, dut.triangle_geometry.start_values.start_s) == 0xFFFF_F000
        await ctx.tick()

        # triangleCMD ignores the shadows
        put_cmd(ctx, dut, write=True, addr=0x080, data=0)
        await ctx.tick()
        put_cmd(ctx, dut, valid=0)
        assert ctx.get(tri.valid) == 1 and ctx.get(ftri.valid) == 0
        p = unpack(layout, get_payload(ctx, tri))
        assert p["tex_hi"]["s_start"] == s_hi
        assert p["grads"]["w_grad"]["start"] == resize_s(0x8000_0001, 32, 48) & mask(48)

    run(dut, tb)


def test_packed_tex_tables_match_python_model():
    dut = RegisterBank(CFG)
    layout = triangle_setup_input_layout(CFG)
    tri = dut.commands.triangle_cmd_stream
    rng = random.Random(7)

    async def tb(ctx):
        set_defaults(ctx, dut)
        for _ in range(6):
            tex_base = rng.getrandbits(24)
            tex_base1, tex_base2, tex_base38 = (rng.getrandbits(24) for _ in range(3))
            tformat = rng.randrange(16)
            aspect = rng.randrange(4)
            s_wider = rng.randrange(2)
            multibase = rng.randrange(2)
            t_lod = (multibase << 24) | (aspect << 21) | (s_wider << 20) | rng.getrandbits(20)
            for addr, v in [(0x30C, tex_base), (0x310, tex_base1), (0x314, tex_base2),
                            (0x318, tex_base38), (0x300, tformat << 8), (0x304, t_lod)]:
                await bmb_write(ctx, dut, addr, v)
            await ctx.tick().repeat(3)      # comb -> texTablesReg -> triangleConfigReg
            put_cmd(ctx, dut, write=True, addr=0x080, data=0)
            await ctx.tick()
            put_cmd(ctx, dut, valid=0)
            p = unpack(layout, get_payload(ctx, tri))
            want = tlt.compute(tlt.TexConfig(
                tex_base_addr=tex_base & mask(19), tex_base_addr1=tex_base1 & mask(19),
                tex_base_addr2=tex_base2 & mask(19), tex_base_addr38=tex_base38 & mask(19),
                tformat=tformat, tlod_aspect=aspect, tlod_s_is_wider=bool(s_wider),
                tlod_multibase=bool(multibase)))
            got = p["config"]["tex_tables"]
            assert got["tex_base"] == list(want.tex_base)
            assert got["tex_end"] == list(want.tex_end)
            assert got["tex_shift"] == list(want.tex_shift)
            assert p["config"]["tmu_tex_base_addr"] == tex_base
            assert p["config"]["tmu_tlod"] == t_lod & mask(27)
            await ctx.tick()

    run(dut, tb)


# ---------------------------------------------------------------------------
# 3. trace replay against a cycle-accurate Python model
# ---------------------------------------------------------------------------


def fpxx2afix_raw(bits: int) -> int:
    """``Fpxx2AFix(20 bits, 30 bits, float32)``: floor(value * 2^30), saturated to 51 bits.

    NaN/Inf saturate by sign (``tests/test_fpxx.py``); the arithmetic shift
    makes negatives truncate towards minus infinity; anything below 2^-30 in
    magnitude is zero, not -1, because the converter short-circuits on the
    exponent before shifting.
    """
    sign = bits >> 31
    exp = (bits >> 23) & 0xFF
    full = (1 << 23) | (bits & 0x7FFFFF)
    # `shift = (intNrBits - 1 + bias) - exp`; `shift >= 50` (exp <= 96) is a
    # hard zero for both signs (Fpxx2AFix.scala: `when(shift >= total || !ge0)`)
    if exp <= 127 - 30 - 1:
        return 0
    e = exp - 127 - 23 + 30
    v = Fraction(full) * (Fraction(2) ** e)
    if sign:
        v = -v
    raw = v.numerator // v.denominator          # floor
    lo, hi = -(1 << 50), (1 << 50) - 1
    return max(lo, min(hi, raw))


def float_corrected(raw: int, shift: int) -> int:
    """``PciFifo.floatCorrected``: arithmetic shift, +1 for a negative with dropped bits."""
    shifted = raw >> shift
    has_remainder = shift in (26, 18, 12) and (raw & mask(shift)) != 0
    return shifted + 1 if raw < 0 and has_remainder else shifted


def pci_float_write(alias: regdef.FloatAlias, bits: int) -> tuple[int, int, int]:
    """(target addr, 32-bit bus data, 50-bit shadow raw) for a float-alias write."""
    raw = fpxx2afix_raw(bits)
    return alias.target_addr, float_corrected(raw, alias.shift) & 0xFFFFFFFF, raw & mask(50)


def frontdoor_register_address(pci_addr: int) -> int:
    """PCI byte address -> RegisterBank address: CoreCpuFrontdoor + AddressRemapper."""
    a = ((pci_addr >> 14) & 0xFF) << 14 | (pci_addr & 0x3FF)       # chip field forced to 0
    base = a & 0xFFF
    if (a >> 21) & 1:
        if 0x088 <= base <= 0x0FC:
            return regdef.REMAP_TABLE[base]
        return (a & ~(1 << 21)) & 0xFFF
    return a & 0xFFF


def trace_register_writes(path: Path) -> list[tuple[int, int]]:
    """The register-space writes of a packed demo trace, as (12-bit addr, data)."""
    raw = path.read_bytes()
    out = []
    for i in range(len(raw) // 8):
        a, v = struct.unpack_from("<II", raw, i * 8)
        if (a >> 22) & 3 == 0:
            out.append((frontdoor_register_address(a), v))
    return out


class _Pipe:
    """``s2mPipe().m2sPipe()`` on one stream, cycle by cycle."""

    def __init__(self):
        self.r_valid_n = 1
        self.r_data = 0
        self.r_valid = 0
        self.r_data2 = 0

    @property
    def src_ready(self):
        return self.r_valid_n

    def outputs(self):
        return self.r_valid, self.r_data2

    def step(self, src_valid: int, src_payload: int, out_ready: int):
        mid_valid = src_valid | (not self.r_valid_n)
        mid_payload = src_payload if self.r_valid_n else self.r_data
        mid_ready = out_ready | (not self.r_valid)
        # s2m
        nxt_valid_n = self.r_valid_n
        if src_valid:
            nxt_valid_n = 0
        if mid_ready:
            nxt_valid_n = 1
        if self.r_valid_n:
            self.r_data = src_payload
        self.r_valid_n = nxt_valid_n
        # m2s
        if mid_ready:
            self.r_valid = int(mid_valid)
            self.r_data2 = mid_payload


class RegisterBankModel:
    """Cycle-accurate model of ``RegisterBank.scala`` for ``Config.voodoo1()``.

    Inputs other than the bus, the float-shadow flow and the stream readies
    are held at ``set_defaults`` values.
    """

    PCI_FIFO_FREE = 64
    MEM_FIFO_FREE = 0xFFFF

    def __init__(self):
        self.regs: dict[int, int] = {r.addr: r.reset_value for r in regdef.registers(False)}
        self.shadow = {addr: 0 for addr in FLOAT_SHADOW_ADDRS}
        self.gamma = [((i * 8) << 16) | ((i * 8) << 8) | (i * 8) for i in range(32)] + [0xFFFFFF]
        self.pipes = {addr: _Pipe() for addr in regdef.command_addresses()}
        self.layout = triangle_setup_input_layout(CFG)
        self.cfg_reg = self._capture_config(None)
        self.tex_reg = self._tex_tables()
        self.prev_fire = 0
        self.rdata = 0

    # -- register file ----------------------------------------------------------

    def _field_word(self, reg: regdef.Register, accesses) -> int:
        w = 0
        for f in reg.fields:
            if f.access in accesses:
                w |= self.regs[reg.addr] & f.mask
        return w

    def field(self, addr: int, name: str) -> int:
        f = regdef.by_addr(addr).field(name)
        return (self.regs[addr] & f.mask) >> f.lo

    def read(self, addr: int) -> int:
        try:
            reg = regdef.by_addr(addr)
        except KeyError:
            return 0
        if reg.sim_only or not reg.fields:
            return 0
        if addr == 0x000:
            return (min(self.PCI_FIFO_FREE, 63) | (1 << 6) | (self.MEM_FIFO_FREE << 12))
        if reg.category is None:          # statistics: inputs held at 0
            return 0
        return self._field_word(reg, (Access.RW,))

    def write(self, addr: int, data: int, strb: int):
        try:
            reg = regdef.by_addr(addr)
        except KeyError:
            return
        bm = 0
        for i in range(4):
            if (strb >> i) & 1:
                bm |= 0xFF << (8 * i)
        stored = 0
        for f in reg.fields:
            if f.access in (Access.RW, Access.WO):
                stored |= f.mask
        old = self.regs[addr]
        self.regs[addr] = ((old & ~bm) | (data & bm)) & stored

    # -- the payload ------------------------------------------------------------

    def _afix(self, name: str) -> int:
        reg = regdef.by_name(name)
        return self.regs[reg.addr]

    def _capture_config(self, tex_reg) -> dict:
        r = self.regs
        cfg = {
            "fbz_color_path": r[0x104] & mask(FBZ_COLOR_PATH_LAYOUT.size),
            "fog_mode": r[0x108] & mask(FOG_MODE_LAYOUT.size),
            "alpha_mode": r[0x10C] & mask(ALPHA_MODE_LAYOUT.size),
            "enable_clipping": self.field(0x110, "enableClipping"),
            "clip_left": self.field(0x118, "clipLeftX"),
            "clip_right": self.field(0x118, "clipRightX"),
            "clip_low_y": self.field(0x11C, "clipLowY"),
            "clip_high_y": self.field(0x11C, "clipHighY"),
            "tmu_texture_mode": r[0x300] & mask(12),
            "tmu_tex_base_addr": r[0x30C],
            "tmu_tex_base_addr1": r[0x310],
            "tmu_tex_base_addr2": r[0x314],
            "tmu_tex_base_addr38": r[0x318],
            "tmu_tlod": r[0x304] & mask(27),
            "tmud_sd_x": r[0x054],
            "tmud_td_x": r[0x058],
            "tmud_sd_y": r[0x074],
            "tmud_td_y": r[0x078],
            "tex_tables": tex_reg if tex_reg is not None else
            {"tex_base": [0] * 9, "tex_end": [0] * 9, "tex_shift": [0] * 9},
        }
        return cfg

    def _tex_tables(self) -> dict:
        r = self.regs
        t = tlt.compute(tlt.TexConfig(
            tex_base_addr=r[0x30C] & mask(19), tex_base_addr1=r[0x310] & mask(19),
            tex_base_addr2=r[0x314] & mask(19), tex_base_addr38=r[0x318] & mask(19),
            tformat=(r[0x300] >> 8) & 0xF, tlod_aspect=(r[0x304] >> 21) & 3,
            tlod_s_is_wider=bool((r[0x304] >> 20) & 1), tlod_multibase=bool((r[0x304] >> 24) & 1)))
        return {"tex_base": list(t.tex_base), "tex_end": list(t.tex_end),
                "tex_shift": list(t.tex_shift)}

    def triangle_payload(self, sign_bit: int, use_float_shadow: bool) -> int:
        g = self._afix
        sh = self.shadow

        def sel(addr, fixed):
            return sh[addr] if use_float_shadow else fixed

        def grad(start, dx, dy, w, sw=None):
            return {"start": resize_s(start, sw or w, w) & mask(w),
                    "d": [resize_s(dx, sw or w, w) & mask(w), resize_s(dy, sw or w, w) & mask(w)]}

        w_start = sel(0x03C, resize_s(g("startW"), 32, 60))
        w_dx = sel(0x05C, resize_s(g("dWdX"), 32, 60))
        w_dy = sel(0x07C, resize_s(g("dWdY"), 32, 60))
        hi = lambda addr, name, shift: sel(addr, shl_keep(resize_s(g(name), 32, HI_W) & mask(HI_W), shift, HI_W)) & mask(HI_W)  # noqa: E731
        payload = {
            "tri_with_sign": {
                "tri": [[g("vertexAx"), g("vertexAy")], [g("vertexBx"), g("vertexBy")],
                        [g("vertexCx"), g("vertexCy")]],
                "sign_bit": sign_bit,
            },
            "grads": {
                "red_grad": grad(g("startR"), g("dRdX"), g("dRdY"), 24),
                "green_grad": grad(g("startG"), g("dGdX"), g("dGdY"), 24),
                "blue_grad": grad(g("startB"), g("dBdX"), g("dBdY"), 24),
                "depth_grad": grad(g("startZ"), g("dZdX"), g("dZdY"), 32),
                "alpha_grad": grad(g("startA"), g("dAdX"), g("dAdY"), 24),
                "w_grad": grad(w_start, w_dx, w_dy, 48, sw=60),
                "s_grad": grad(g("startS"), g("dSdX"), g("dSdY"), 48, sw=32),
                "t_grad": grad(g("startT"), g("dTdX"), g("dTdY"), 48, sw=32),
            },
            "hi_alpha": {"start": hi(0x030, "startA", 18), "d_ad_x": hi(0x050, "dAdX", 18),
                         "d_ad_y": hi(0x070, "dAdY", 18)},
            "tex_hi": {"s_start": hi(0x034, "startS", 12), "t_start": hi(0x038, "startT", 12),
                       "d_sd_x": hi(0x054, "dSdX", 12), "d_td_x": hi(0x058, "dTdX", 12),
                       "d_sd_y": hi(0x074, "dSdY", 12), "d_td_y": hi(0x078, "dTdY", 12)},
            "config": self.cfg_reg,
        }
        return pack(self.layout, payload)

    # -- one clock ----------------------------------------------------------------

    def outputs(self) -> dict:
        """Combinational outputs at the start of the cycle (before this cycle's inputs)."""
        return {"rsp_valid": self.prev_fire, "rsp_data": self.rdata,
                "streams": {a: p.outputs() for a, p in self.pipes.items()},
                "command_ready": [p.src_ready for _, p in sorted(self.pipes.items())]}

    def step(self, *, cmd_valid: int, write: bool, addr: int, data: int, strb: int,
             fs_valid: int, fs_addr: int, fs_raw: int, readies: dict[int, int]):
        # -- combinational: command pulses and their payloads
        srcs = {}
        for a, pipe in self.pipes.items():
            hit = cmd_valid and write and addr == a
            payload = 0
            if hit:
                if a in (0x080, 0x100):
                    payload = self.triangle_payload((data >> 31) & 1, a == 0x100)
                elif a == 0x120:
                    payload = data & 0xFFFFFFFF
            srcs[a] = (int(hit), payload)
        # -- sequential
        next_cfg = self._capture_config(self.tex_reg)
        next_tex = self._tex_tables()
        next_rdata = self.read(addr) if (cmd_valid and not write) else 0
        for a, pipe in self.pipes.items():
            pipe.step(*srcs[a], readies[a])
        if fs_valid:
            if fs_addr in self.shadow:
                w = 60 if fs_addr in (0x03C, 0x05C, 0x07C) else HI_W
                self.shadow[fs_addr] = resize_s(fs_raw, 50, w) & mask(w)
        elif cmd_valid and write:
            if addr == 0x228:
                idx = (data >> 24) & 0x3F
                if idx <= 32:
                    self.gamma[idx] = data & 0xFFFFFF
            if addr in FLOAT_SHADOW_ADDRS:
                _, shift = FLOAT_SHADOW_ADDRS[addr]
                w = 60 if shift == 0 else HI_W
                self.shadow[addr] = shl_keep(resize_s(data, 32, w) & mask(w), shift, w)
        if cmd_valid and write:
            self.write(addr, data, strb)
        self.cfg_reg = next_cfg
        self.tex_reg = next_tex
        self.rdata = next_rdata
        self.prev_fire = int(cmd_valid)


def pack(layout, value) -> int:
    """Inverse of :func:`unpack`; an ``int`` is taken as already packed."""
    if isinstance(value, int):
        return value
    if isinstance(layout, data.ArrayLayout):
        elem, w = layout.elem_shape, _width(layout.elem_shape)
        word = 0
        for i, v in enumerate(value):
            word |= (pack(elem, v) & mask(w)) << (i * w)
        return word
    if isinstance(layout, data.Layout):
        word = 0
        for key, f in layout:
            word |= (pack(f.shape, value[key]) & mask(_width(f.shape))) << f.offset
        return word
    return int(value)


def _replay_events(writes, rng, *, gap_max: int):
    """Turn (addr, data) writes into per-cycle bus/flow stimulus, with a read-back
    of each register after its write and random idle gaps."""
    events = []
    for addr, data in writes:
        if addr in regdef.float_aliases():
            target, data32, raw50 = pci_float_write(regdef.float_alias_by_addr(addr), data)
            events.append(dict(cmd_valid=1, write=True, addr=target, data=data32, strb=0xF,
                               fs_valid=1, fs_addr=target, fs_raw=raw50))
            read_addr = target
        else:
            events.append(dict(cmd_valid=1, write=True, addr=addr, data=data, strb=0xF,
                               fs_valid=0, fs_addr=0, fs_raw=0))
            read_addr = addr
        for _ in range(rng.randrange(gap_max + 1)):
            events.append(dict(cmd_valid=0, write=False, addr=0, data=0, strb=0,
                               fs_valid=0, fs_addr=0, fs_raw=0))
        events.append(dict(cmd_valid=1, write=False, addr=read_addr, data=0, strb=0,
                           fs_valid=0, fs_addr=0, fs_raw=0))
    return events


def _replay(trace: Path, *, limit: int | None, seed: int, ready_density: float,
            gap_max: int = 2):
    writes = trace_register_writes(trace)[:limit]
    assert writes, trace
    rng = random.Random(seed)
    events = _replay_events(writes, rng, gap_max=gap_max)
    dut = RegisterBank(CFG)
    model = RegisterBankModel()
    stream_ports = dict(zip(regdef.command_addresses(), streams(dut)))
    # `responses` counts every rsp.valid: BmbBusInterface answers *writes* too
    # (BmbBusInterface.scala:135, `rsp.valid := RegNext(cmd.fire)`), so it is
    # one per bus command; `reads` is the subset that answered a read.
    stats = {"reads": 0, "responses": 0, "commands": 0, "cycles": 0}
    prev_was_read = False

    async def tb(ctx):
        set_defaults(ctx, dut)
        for ev in events:
            readies = {a: int(rng.random() < ready_density) for a in stream_ports}
            for a, s in stream_ports.items():
                ctx.set(s.ready, readies[a])
            put_cmd(ctx, dut, valid=ev["cmd_valid"], write=ev["write"], addr=ev["addr"],
                    data=ev["data"], strb=ev["strb"])
            ctx.set(dut.float_shadow.valid, ev["fs_valid"])
            ctx.set(dut.float_shadow.payload.address, ev["fs_addr"])
            ctx.set(dut.float_shadow.payload.raw, sx(ev["fs_raw"], 50))
            # compare this cycle's outputs (settled from the previous edge)
            want = model.outputs()
            assert ctx.get(dut.bus.rsp.valid) == want["rsp_valid"]
            if want["rsp_valid"]:
                got = ctx.get(dut.bus.rsp.payload.fragment.data)
                assert got == want["rsp_data"], f"rsp data {got:#x} != {want['rsp_data']:#x}"
                stats["responses"] += 1
                if prev_was_read:
                    stats["reads"] += 1
            prev_was_read = bool(ev["cmd_valid"]) and not ev["write"]
            for i, cr in enumerate(want["command_ready"]):
                assert ctx.get(dut.command_ready[i]) == cr
            for a, s in stream_ports.items():
                valid, payload = want["streams"][a]
                assert ctx.get(s.valid) == valid, f"stream {a:#x} valid"
                if valid and a in (0x080, 0x100, 0x120):
                    got = get_payload(ctx, s)
                    if got != payload:
                        lay = triangle_setup_input_layout(CFG) if a != 0x120 else None
                        diff = {} if lay is None else {
                            k: (g, w) for (k, g), (_, w) in
                            zip(unpack(lay, got).items(), unpack(lay, payload).items()) if g != w}
                        pytest.fail(f"stream {a:#x} payload differs: {diff}")
                    stats["commands"] += 1
            model.step(**ev, readies=readies)
            await ctx.tick()
            stats["cycles"] += 1
        # drain, then compare every stored register field with the model
        for a, s in stream_ports.items():
            ctx.set(s.ready, 1)
        put_cmd(ctx, dut, valid=0)
        await ctx.tick().repeat(3)
        for reg in regdef.registers(include_sim_only=False):
            for f in reg.fields:
                path = output_path(reg, f)
                if path is None:
                    continue
                port = dut._port(path)
                got = ctx.get(port.raw) & mask(f.width) if hasattr(port, "raw") else ctx.get(port)
                assert got == model.field(reg.addr, f.name), f"{reg.name}.{f.name}"
        for i in range(33):
            assert ctx.get(dut.gamma_lut[i]) == model.gamma[i]

    run(dut, tb)
    return stats


def _demo(name: str) -> Path:
    p = DEMO_DIR / name
    if not p.is_file():
        pytest.skip(f"{p} not present")
    return p


def test_replay_test04_register_prefix():
    """test04: the SST-1 init sequence, the fixed-point vertex/gradient setup
    and 31 ``ftriangleCMD``s fed through the float aliases."""
    stats = _replay(_demo("demo_test04.bin"), limit=None, seed=1, ready_density=1.0)
    assert stats["commands"] == 31 + 12     # every ftriangleCMD and nopCMD of the trace
    # 930 writes, each followed by a read-back; the last read's response
    # lands after the loop, so 929 read responses and 1859 in total
    assert stats["reads"] == 930 - 1
    assert stats["responses"] == 2 * 930 - 1


def test_replay_test04_with_backpressure():
    stats = _replay(_demo("demo_test04.bin"), limit=None, seed=2, ready_density=0.7, gap_max=3)
    assert stats["reads"] == 930 - 1
    assert stats["responses"] == 2 * 930 - 1


def test_replay_logo_spin_prefix():
    stats = _replay(_demo("demo_logo_spin.bin"), limit=1200, seed=3, ready_density=0.9)
    assert stats["reads"] == 1200 - 1
    assert stats["responses"] == 2 * 1200 - 1
    assert stats["commands"] >= 20


def test_float_alias_model_matches_fpxx2afix():
    """Pin :func:`fpxx2afix_raw` against the real converter on the trace's floats."""
    from avoodoo.lib.fpxx import FpxxConfig
    from avoodoo.lib.fpxx2afix import Fpxx2AFix

    values = set()
    for p in ("demo_test04.bin", "demo_logo_spin.bin"):
        path = DEMO_DIR / p
        if path.is_file():
            for addr, data in trace_register_writes(path):
                if addr in regdef.float_aliases():
                    values.add(data)
    values |= {0x00000000, 0x80000000, 0x3F800000, 0xBF800000, 0x00000001, 0x80000001,
               0x7F800000, 0xFF800000, 0x4A000000, 0xCA000000, 0x3F000001, 0xBF000001}
    values = sorted(values)
    if not values:
        pytest.skip("no demo traces")
    dut = Fpxx2AFix(20, 30, FpxxConfig.float32(), pipe_stages=1)
    got = {}

    async def tb(ctx):
        ctx.set(dut.result.ready, 1)
        for bits in values:
            ctx.set(dut.op.valid, 1)
            ctx.set(dut.op.payload.as_value(), bits)
            await ctx.tick()
            ctx.set(dut.op.valid, 0)
            await ctx.tick()
            got[bits] = sx(ctx.get(dut.result.payload.number.raw), 51)

    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    sim.run()
    for bits in values:
        assert got[bits] == fpxx2afix_raw(bits), f"{bits:#010x}"
