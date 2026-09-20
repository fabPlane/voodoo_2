"""Phase 1.4 unit tests for :mod:`avoodoo.lib.cdc`.

Multi-clock ``pysim``: the tests drive two independent clocks and count
*destination-clock edges*, because the thing being pinned is latency in
destination cycles, which is what has to agree with Spinal's ``BufferCC``
(``defaultDepth = 2``) for the plan's §4.2 lockstep diff to line up.

The Verilog-level comparison against Spinal's ``ResetCtrl`` -- what Phase 8
needs to know for the Gowin Console build -- is checked structurally at the
bottom and written up in :data:`avoodoo.lib.cdc.SPINAL_VS_AMARANTH_CDC`.
"""

import pytest
from amaranth import ClockDomain, ClockSignal, Module, Signal, unsigned
from amaranth.back import verilog
from amaranth.hdl import DomainRenamer
from amaranth.sim import Simulator

from avoodoo.lib import cdc

FAST = 1e-6
SLOW = 2.5e-6


async def tick(ctx, *outputs, domain="sync"):
    """``ctx.tick(...).sample(...)`` minus the ``(clk_hit, rst_active)`` prefix.

    Sampling happens just before the active edge, so what comes back is the
    pre-edge (this cycle's combinational) value of each signal.
    """
    return (await ctx.tick(domain).sample(*outputs))[2:]


class TwoDomains:
    """A top with an ``a`` and a ``b`` domain and testbench-settable resets.

    ``ResetSignal("x")`` cannot be assigned from a testbench, so each
    domain's reset is driven combinationally from a plain signal the
    testbench can poke.
    """

    def __init__(self):
        self.m = Module()
        self.cd_a = ClockDomain("a")
        self.cd_b = ClockDomain("b")
        self.m.domains += self.cd_a
        self.m.domains += self.cd_b
        self.rst_a = Signal(name="rst_a")
        self.rst_b = Signal(name="rst_b")
        self.m.d.comb += [self.cd_a.rst.eq(self.rst_a),
                          self.cd_b.rst.eq(self.rst_b)]

    def run(self, tb, *, domains=(("a", FAST), ("b", SLOW))):
        sim = Simulator(self.m)
        for name, period in domains:
            sim.add_clock(period, domain=name)
        sim.add_testbench(tb)
        sim.run()


# ==========================================================================
# BufferCC
# ==========================================================================


@pytest.mark.parametrize("depth", [2, 3, 4])
def test_buffer_cc_latency_is_exactly_depth_destination_edges(depth):
    """``BufferCC``'s output follows the input after ``bufferDepth`` edges."""
    t = TwoDomains()
    src = Signal(8)
    dst = cdc.buffer_cc(t.m, src, init=0, buffer_depth=depth, o_domain="b",
                        name="crossing")

    async def tb(ctx):
        await ctx.tick("b")
        ctx.set(src, 0xA5)
        for k in range(depth + 2):
            value, = await tick(ctx, dst, domain="b")
            expected = 0xA5 if k >= depth else 0x00
            assert value == expected, f"edge {k}: {value:#04x} != {expected:#04x}"

    t.run(tb)


def test_buffer_cc_default_depth_is_two():
    """``BufferCC.defaultDepth`` is 2, and no call site in the design changes it."""
    assert cdc.DEFAULT_BUFFER_DEPTH == 2
    t = TwoDomains()
    src = Signal()
    dst = cdc.buffer_cc(t.m, src, init=0, o_domain="b")

    async def tb(ctx):
        await ctx.tick("b")
        ctx.set(src, 1)
        assert (await tick(ctx, dst, domain="b"))[0] == 0
        assert (await tick(ctx, dst, domain="b"))[0] == 0
        assert (await tick(ctx, dst, domain="b"))[0] == 1

    t.run(tb)


def test_buffer_cc_with_init_is_held_by_the_destination_reset():
    """``BufferCC(x, init = False)`` builds resettable flops (SYNC/HIGH)."""
    t = TwoDomains()
    src = Signal(4)
    dst = cdc.buffer_cc(t.m, src, init=0, o_domain="b")

    async def tb(ctx):
        ctx.set(src, 0xF)
        ctx.set(t.rst_b, 1)
        for _ in range(6):
            value, = await tick(ctx, dst, domain="b")
            assert value == 0, "an init'd BufferCC reads its init while in reset"
        ctx.set(t.rst_b, 0)
        await ctx.tick("b")
        await ctx.tick("b")
        value, = await tick(ctx, dst, domain="b")
        assert value == 0xF

    t.run(tb)


def test_buffer_cc_without_init_ignores_the_destination_reset():
    """``BufferCC(x)`` (``init = null``) builds reset-less flops.

    Three call sites do this (``TangConsole138k.scala`` :172 ``screen``,
    :275 ``cursor4``, :280 ``hid16``). They keep shifting while the
    destination domain is held in reset, which an ``init``'d crossing does
    not -- the observable half of note 2 in ``SPINAL_VS_AMARANTH_CDC``.
    """
    t = TwoDomains()
    src = Signal(4)
    dst = cdc.buffer_cc(t.m, src, o_domain="b")

    async def tb(ctx):
        ctx.set(src, 0xF)
        ctx.set(t.rst_b, 1)
        for _ in range(2):
            await ctx.tick("b")
        value, = await tick(ctx, dst, domain="b")
        assert value == 0xF, "reset-less flops shift through reset"

    t.run(tb)


def test_buffer_cc_init_value_is_the_reset_value():
    t = TwoDomains()
    src = Signal(8)
    dst = cdc.buffer_cc(t.m, src, init=0xFF, o_domain="b")

    async def tb(ctx):
        ctx.set(t.rst_b, 1)
        value, = await tick(ctx, dst, domain="b")
        assert value == 0xFF
        ctx.set(t.rst_b, 0)
        ctx.set(src, 0x01)
        await ctx.tick("b")
        await ctx.tick("b")
        value, = await tick(ctx, dst, domain="b")
        assert value == 0x01

    t.run(tb)


def test_buffer_cc_component_matches_the_function():
    """The ``wiring.Component`` form emits the same flop chain."""
    t = TwoDomains()
    t.m.submodules.cc = cc = DomainRenamer({"sync": "b"})(
        cdc.BufferCC(unsigned(8), init=0))
    src = Signal(8)
    t.m.d.comb += cc.data_in.eq(src)

    async def tb(ctx):
        await ctx.tick("b")
        ctx.set(src, 0x3C)
        assert (await tick(ctx, cc.data_out, domain="b"))[0] == 0
        assert (await tick(ctx, cc.data_out, domain="b"))[0] == 0
        assert (await tick(ctx, cc.data_out, domain="b"))[0] == 0x3C

    t.run(tb)


def test_buffer_cc_rejects_a_zero_depth():
    with pytest.raises(ValueError):
        cdc.BufferCC(unsigned(1), buffer_depth=0)


# ==========================================================================
# reset synchroniser / buffered reset domain
# ==========================================================================


def test_reset_synchronizer_deasserts_after_two_destination_edges():
    """``ResetCtrl.asyncAssertSyncDeassert``: async assert, 2-cycle release."""
    m = Module()
    cd_a = ClockDomain("a")
    m.domains += cd_a
    arst = Signal()
    m.d.comb += cd_a.rst.eq(arst)
    cd_b = cdc.buffered_reset_domain(m, "b", clk=ClockSignal("a"), reset_from="a")
    counter = Signal(8)
    m.d.b += counter.eq(counter + 1)

    async def tb(ctx):
        ctx.set(arst, 1)
        await ctx.tick("a")
        await ctx.tick("a")
        assert ctx.get(cd_b.rst) == 1
        assert ctx.get(counter) == 0
        ctx.set(arst, 0)
        # Two destination edges of deassertion latency, exactly as Spinal's
        # BufferCC(input = False, init = True, bufferDepth = 2).
        assert (await tick(ctx, cd_b.rst, domain="a"))[0] == 1
        assert (await tick(ctx, cd_b.rst, domain="a"))[0] == 1
        assert (await tick(ctx, cd_b.rst, domain="a"))[0] == 0
        await ctx.tick("a")
        assert ctx.get(counter) >= 1

    sim = Simulator(m)
    sim.add_clock(FAST, domain="a")
    sim.add_testbench(tb)
    sim.run()


def test_buffered_reset_domain_is_async_reset_and_asserts_immediately():
    """The source reset takes the domain down without a destination edge."""
    m = Module()
    cd_a = ClockDomain("a")
    cd_b = ClockDomain("b")
    m.domains += cd_a
    m.domains += cd_b
    arst = Signal()
    m.d.comb += cd_a.rst.eq(arst)
    cd_bx = cdc.buffered_reset_domain(m, "bx", clk=ClockSignal("b"),
                                      reset_from="a")
    assert cd_bx.async_reset is True, "Spinal copies the CD with resetKind = ASYNC"
    counter = Signal(8)
    m.d.bx += counter.eq(counter + 1)

    async def tb(ctx):
        ctx.set(arst, 0)
        for _ in range(8):
            await ctx.tick("b")
        assert ctx.get(counter) > 0
        # Assert the *source* reset between two destination edges: an async
        # assertion shows up straight away.
        ctx.set(arst, 1)
        await ctx.delay(SLOW / 8)
        assert ctx.get(cd_bx.rst) == 1

    sim = Simulator(m)
    sim.add_clock(FAST, domain="a")
    sim.add_clock(SLOW, domain="b")
    sim.add_testbench(tb)
    sim.run()


def test_reset_synchronizer_stage_count_follows_buffer_depth():
    m = Module()
    cd_a = ClockDomain("a")
    m.domains += cd_a
    arst = Signal()
    m.d.comb += cd_a.rst.eq(arst)
    cd_b = cdc.buffered_reset_domain(m, "b", clk=ClockSignal("a"),
                                     reset_from="a", stages=3)
    counter = Signal(4)
    m.d.b += counter.eq(counter + 1)

    async def tb(ctx):
        ctx.set(arst, 1)
        await ctx.tick("a")
        ctx.set(arst, 0)
        assert (await tick(ctx, cd_b.rst, domain="a"))[0] == 1
        assert (await tick(ctx, cd_b.rst, domain="a"))[0] == 1
        assert (await tick(ctx, cd_b.rst, domain="a"))[0] == 1
        assert (await tick(ctx, cd_b.rst, domain="a"))[0] == 0

    sim = Simulator(m)
    sim.add_clock(FAST, domain="a")
    sim.add_testbench(tb)
    sim.run()


# ==========================================================================
# PulseCCByToggle
# ==========================================================================


def _pulse_top(*, buffered_reset):
    t = TwoDomains()
    pulse_in = Signal()
    out = cdc.pulse_cc_by_toggle(t.m, pulse_in, i_domain="a", o_domain="b",
                                 with_output_buffered_reset=buffered_reset,
                                 name="pcc")
    return t, pulse_in, out


def test_pulse_cc_by_toggle_moves_one_pulse():
    """One source pulse becomes exactly one destination pulse."""
    t, pulse_in, out = _pulse_top(buffered_reset=False)

    async def tb(ctx):
        for _ in range(4):
            await ctx.tick("b")
        ctx.set(pulse_in, 1)
        await ctx.tick("a")
        ctx.set(pulse_in, 0)
        hits = 0
        for _ in range(12):
            value, = await tick(ctx, out, domain="b")
            hits += int(bool(value))
        assert hits == 1, f"exactly one destination pulse, got {hits}"

    t.run(tb)


def test_pulse_cc_by_toggle_is_any_edge_so_two_pulses_give_two():
    """``Bool.edge(False)`` is ``this ^ RegNext(this)``: both toggle directions."""
    t, pulse_in, out = _pulse_top(buffered_reset=False)

    async def tb(ctx):
        for _ in range(4):
            await ctx.tick("b")
        hits = 0
        for _ in range(2):
            ctx.set(pulse_in, 1)
            await ctx.tick("a")
            ctx.set(pulse_in, 0)
            for _ in range(8):
                value, = await tick(ctx, out, domain="b")
                hits += int(bool(value))
        assert hits == 2

    t.run(tb)


def test_pulse_cc_by_toggle_defaults_to_a_buffered_output_reset():
    """``crossClockBufferPushToPopResetGen`` defaults to **true**."""
    t, pulse_in, out = _pulse_top(buffered_reset=True)

    async def tb(ctx):
        for _ in range(6):
            await ctx.tick("b")
        ctx.set(pulse_in, 1)
        await ctx.tick("a")
        ctx.set(pulse_in, 0)
        hits = 0
        for _ in range(12):
            value, = await tick(ctx, out, domain="b")
            hits += int(bool(value))
        assert hits == 1

    t.run(tb)


# ==========================================================================
# Spinal-style clock domains
# ==========================================================================


def test_clock_domain_defaults_match_the_spinal_config():
    """``ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)``."""
    m = Module()
    cd_a = ClockDomain("a")
    m.domains += cd_a
    rst = Signal()
    cd = cdc.clock_domain(m, "ddr", clk=ClockSignal("a"), rst=rst)
    assert cd.async_reset is False, "resetKind = SYNC"
    assert cd.rst is not None, "the domain is resettable"
    counter = Signal(4)
    m.d.ddr += counter.eq(counter + 1)

    async def tb(ctx):
        ctx.set(rst, 1)
        for _ in range(3):
            await ctx.tick("ddr")
        assert ctx.get(counter) == 0, "active-HIGH reset holds the domain"
        ctx.set(rst, 0)
        for _ in range(3):
            await ctx.tick("ddr")
        assert ctx.get(counter) == 3

    sim = Simulator(m)
    sim.add_clock(FAST, domain="a")
    sim.add_testbench(tb)
    sim.run()


def test_clock_domain_without_reset_is_reset_less():
    m = Module()
    clk = Signal()
    cd = cdc.clock_domain(m, "free", clk=clk)
    assert cd.rst is None


def test_clocking_area_renames_sync():
    """``new ClockingArea(cd) { ... }`` is a ``DomainRenamer``."""
    m = Module()
    cd = ClockDomain("ctrl")
    m.domains += cd
    inner = Module()
    q = Signal(4)
    inner.d.sync += q.eq(q + 1)
    m.submodules.area = cdc.clocking_area("ctrl")(inner)

    async def tb(ctx):
        for _ in range(3):
            await ctx.tick("ctrl")
        assert ctx.get(q) == 3

    sim = Simulator(m)
    sim.add_clock(FAST, domain="ctrl")
    sim.add_testbench(tb)
    sim.run()


def test_cross_clock_payload_tags_the_registers():
    """``addTag(crossClockDomain)`` on the bundled-data payload registers.

    ``GowinDdrNativeClockCrossing.scala`` :33-35 tags six request registers
    this way; the tag is what stops the timing tool chasing a path that is
    deliberately unsynchronised.
    """
    req_addr = Signal(32)
    req_data = Signal(64)
    cdc.cross_clock_payload(req_addr, req_data)
    assert req_addr.attrs.get("async_reg") == "true"
    assert req_data.attrs.get("async_reg") == "true"
    single = Signal(3)
    assert cdc.cross_clock_payload(single) is single


# ==========================================================================
# emitted Verilog -- the Phase 8 notes
# ==========================================================================


def test_buffer_cc_emits_exactly_depth_flops():
    m = Module()
    cd_b = ClockDomain("b")
    m.domains += cd_b
    src = Signal(8)
    dst = cdc.buffer_cc(m, src, init=0, buffer_depth=2, o_domain="b", name="cc")
    text = verilog.convert(m, name="top", ports=[cd_b.clk, cd_b.rst, src, dst])
    assert "stage0" in text and "stage1" in text
    assert "stage2" not in text


def test_reset_synchronizer_verilog_differs_from_spinals_in_hierarchy_only():
    """Structural note for Phase 8.

    Spinal's ``ResetCtrl.asyncAssertSyncDeassert`` instantiates a separate
    ``BufferCC`` module carrying ``(* keep_hierarchy = "TRUE" *)`` and hands
    the caller a ``Bool`` to assign to the domain's reset; Amaranth inlines
    the two flops into the parent and drives ``ResetSignal(domain)`` itself.
    Same flops, same two destination edges of release, different module
    boundary -- so the Console netlist comparison has to be at flop level.
    And Amaranth emits none of Spinal's ``crossClock*`` timing exceptions, so
    the false-path and max-delay constraints stay hand-written in
    ``fpga/hw/gowin/console``.
    """
    m = Module()
    cd_a = ClockDomain("a")
    m.domains += cd_a
    cdc.buffered_reset_domain(m, "b", clk=ClockSignal("a"), reset_from="a")
    counter = Signal(4)
    m.d.b += counter.eq(counter + 1)

    text = verilog.convert(m, name="top", ports=[cd_a.clk, cd_a.rst, counter])
    # Asynchronous assertion: the synchroniser's flops are sensitive to the
    # reset edge as well as the clock edge.
    assert text.count("posedge") >= 2
    # No keep_hierarchy and no crossClock* constraint -- keeps the note honest.
    assert "keep_hierarchy" not in text
    assert "crossClock" not in text


def test_notes_mention_the_phase_8_constraint_gap():
    assert "keep_hierarchy" in cdc.SPINAL_VS_AMARANTH_CDC
    assert "reset_less" in cdc.SPINAL_VS_AMARANTH_CDC
