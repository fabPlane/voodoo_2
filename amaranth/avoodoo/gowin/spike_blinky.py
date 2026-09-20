"""Phase 0.3 Gowin Verilog compatibility spike.

Small enough to place and route in minutes, but it exercises the three things
that could make Amaranth's Verilog unusable with Gowin EDA 1.9.11.03:

* **escaped identifiers / ``initial`` blocks** — amaranth-yosys emits ``\\$...``
  style names for internal nets and an ``initial`` block for register and
  memory power-up state. ``gw_sh`` must accept both.
* **memory inference with an attribute** — an ``amaranth.lib.memory.Memory``
  carrying ``syn_ramstyle = "distributed_ram"``. If the attribute survives the
  Amaranth -> Verilog -> Gowin synth path, the BSRAM budget can be managed
  from Python and ``fpga/hw/gowin/force_distributed_fifo_ram.py`` is not
  needed for the Amaranth tree (see plan 8.3).
* **``FFSynchronizer``** — the CDC primitive every ported module uses.

Behaviour on the Tang Retro Console 138K:

* ``led_n[0]`` blinks at ~1.5 Hz off the 50 MHz clock. Holding the USER key
  (``key0_n``, active low) freezes it — that proves the synchronizer is live.
* ``led_n[1]`` is lit (driven low) while the RAM self-test is passing. It goes
  dark permanently on the first read-back mismatch.

Ports are deliberately plain (no records, no external reset) so
``amaranth/hw/spike/spike.cst`` can reuse the pin names from
``fpga/hw/gowin-open/vendor-reference/blinky.cst`` verbatim.
"""

from amaranth import ClockDomain, ClockSignal, Module, Signal
from amaranth.hdl import Cat
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

__all__ = ["SpikeBlinky", "RAM_DEPTH", "RAM_WIDTH", "pattern"]

RAM_DEPTH = 64
RAM_WIDTH = 32


def pattern(addr: int) -> int:
    """Reference model of the self-test pattern (also used by the unit test)."""
    return ((addr * 0x01010101) ^ 0xDEADBEEF) & 0xFFFFFFFF


class SpikeBlinky(wiring.Component):
    clk50: In(1)
    key0_n: In(1)
    led_n: Out(2)

    def __init__(self, blink_bit: int = 24):
        self._blink_bit = blink_bit
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        # The board feeds a free-running 50 MHz clock and no reset pin, so the
        # design owns its `sync` domain rather than exposing clk/rst ports.
        m.domains.sync = ClockDomain("sync", reset_less=True)
        m.d.comb += ClockSignal("sync").eq(self.clk50)

        # --- USER key, synchronised into `sync` ----------------------------
        key0_sync = Signal(init=1)
        m.submodules.key0_ff = FFSynchronizer(self.key0_n, key0_sync, init=1)
        held = Signal()
        m.d.comb += held.eq(~key0_sync)

        # --- blinker -------------------------------------------------------
        counter = Signal(self._blink_bit + 1)
        with m.If(~held):
            m.d.sync += counter.eq(counter + 1)

        # --- 64 x 32 distributed RAM, written then read back in a loop -----
        mem = Memory(
            shape=RAM_WIDTH,
            depth=RAM_DEPTH,
            init=[],
            attrs={"syn_ramstyle": "distributed_ram"},
        )
        m.submodules.mem = mem
        wr = mem.write_port()
        rd = mem.read_port()

        addr = Signal(range(RAM_DEPTH))
        expected = Signal(RAM_WIDTH)
        # Deliberately a *fail* flag, not a pass flag: its power-up value is 0
        # either way, so the LED reads correctly even if Gowin drops the
        # `initial` block that Amaranth emits for register init.
        bad = Signal()

        word = Signal(RAM_WIDTH)
        m.d.comb += word.eq((addr * 0x01010101) ^ 0xDEADBEEF)
        m.d.comb += [wr.addr.eq(addr), wr.data.eq(word), rd.addr.eq(addr)]
        m.d.sync += expected.eq(word)

        with m.FSM():
            with m.State("FILL"):
                m.d.comb += wr.en.eq(1)
                m.d.sync += addr.eq(addr + 1)
                with m.If(addr == RAM_DEPTH - 1):
                    m.d.sync += addr.eq(0)
                    m.next = "READ_WARMUP"

            # One cycle for the synchronous read port to present mem[0].
            with m.State("READ_WARMUP"):
                m.d.sync += addr.eq(addr + 1)
                m.next = "CHECK"

            with m.State("CHECK"):
                with m.If(rd.data != expected):
                    m.d.sync += bad.eq(1)
                m.d.sync += addr.eq(addr + 1)
                with m.If(addr == 0):
                    # Wrapped: the whole array has been compared, go again.
                    m.d.sync += addr.eq(0)
                    m.next = "FILL"

        # Both LEDs are active low: 0 = lit.
        #   led_n[0] = blink (inverted counter bit)
        #   led_n[1] = bad   (0 = lit = self-test still passing)
        m.d.comb += self.led_n.eq(Cat(~counter[self._blink_bit], bad))
        return m
