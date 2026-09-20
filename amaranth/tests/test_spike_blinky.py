"""Phase 0.3 spike: simulate the blinky before spending PnR time on it, and
assert the Verilog actually carries the RAM-style attribute.
"""

from amaranth.sim import Simulator

from avoodoo.gowin.spike_blinky import RAM_DEPTH, SpikeBlinky, pattern
from gen import gen_module

BLINK_BIT = 3
HALF_PERIOD = 10e-9


def _run(dut, steps):
    """Toggle clk50 ourselves: the design drives its own `sync` clock, so the
    simulator cannot own that domain."""
    samples = []

    async def tb(ctx):
        ctx.set(dut.key0_n, 1)
        for step in range(steps):
            hold = yield_hold(step)
            ctx.set(dut.key0_n, 0 if hold else 1)
            ctx.set(dut.clk50, 1)
            await ctx.delay(HALF_PERIOD)
            ctx.set(dut.clk50, 0)
            await ctx.delay(HALF_PERIOD)
            samples.append(ctx.get(dut.led_n))

    def yield_hold(step):
        return step >= steps - 64

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    return samples


def test_ram_selftest_passes_and_led_blinks():
    dut = SpikeBlinky(blink_bit=BLINK_BIT)
    # fill (64) + warm-up (1) + check (64), run three full loops.
    samples = _run(dut, 3 * (2 * RAM_DEPTH + 2) + 64)

    free_running = samples[: 2 * RAM_DEPTH]
    held = samples[-32:]

    # led_n[1] low = self-test still passing, for the whole run.
    assert all((s >> 1) & 1 == 0 for s in samples), "RAM read-back mismatch"

    # led_n[0] toggles while the key is released ...
    assert len({s & 1 for s in free_running}) == 2
    # ... and is frozen while the USER key is held (FFSynchronizer is live).
    assert len({s & 1 for s in held}) == 1


def test_pattern_reference_model():
    assert pattern(0) == 0xDEADBEEF
    assert all(0 <= pattern(a) < 2**32 for a in range(RAM_DEPTH))
    assert len({pattern(a) for a in range(RAM_DEPTH)}) == RAM_DEPTH


def test_verilog_carries_ram_attribute_and_top_ports(tmp_path):
    path = gen_module.emit("SpikeBlinky", tmp_path)
    text = path.read_text()
    assert "syn_ramstyle" in text and "distributed_ram" in text
    assert "module SpikeBlinky(" in text
    for port in ("clk50", "key0_n", "led_n"):
        assert port in text
    # No external clk/rst ports: the design owns its sync domain.
    assert "input clk," not in text
