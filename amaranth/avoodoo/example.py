"""A trivial Elaboratable, only here so ``gen/gen_module.py`` and the test
suite have something to chew on before any real module is ported.

Delete once Phase 1 lands something real.
"""

from amaranth import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = ["Counter"]


class Counter(wiring.Component):
    """Free-running counter with a synchronous enable and a carry-out."""

    def __init__(self, width: int = 8):
        self._width = width
        super().__init__({
            "en": In(1, init=1),
            "count": Out(width),
            "carry": Out(1),
        })

    @property
    def width(self) -> int:
        return self._width

    def elaborate(self, platform):
        m = Module()
        next_count = Signal(self._width + 1)
        m.d.comb += next_count.eq(self.count + 1)
        with m.If(self.en):
            m.d.sync += self.count.eq(next_count)
        m.d.comb += self.carry.eq(self.en & next_count[self._width])
        return m
