"""``Axi4AddressRelocate`` -- add a constant to AXI4 ``AW``/``AR`` addresses.

Transliteration of ``case class Axi4AddressRelocate(config, offset)`` in
``fpga/src/voodoo/gowin/CoreGowin.scala`` (lines 80-103), plan Phase 1.5.
``CoreGowin.scala:46`` builds it as
``Axi4AddressRelocate(CoreGowin.texAxiConfig(c), GowinAddressMap.texMemBase)``
to park textures above the framebuffer in the same DDR3
(``texMemBase = fbMemBase + fbMemBytes = 4 MiB``; ``avoodoo/gowin/address_map.py``
is Phase 8.1's, so the offset is a plain constructor argument here).

Purely combinational: no clock, no reset, no registers.  The add wraps at
``address_width`` bits, as ``(addr.resized + U(offset, addressWidth bits)).resized``
does.
"""

from __future__ import annotations

from amaranth import Module
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.axi import Axi4Config, Axi4Signature, connect_axi4

__all__ = ["Axi4AddressRelocate"]


class Axi4AddressRelocate(wiring.Component):
    """``Axi4AddressRelocate(config: Axi4Config, offset: BigInt)``."""

    def __init__(self, config: Axi4Config, offset: int):
        self.config = config
        self.offset = offset
        super().__init__({
            "input": In(Axi4Signature(config)),
            "output": Out(Axi4Signature(config)),
        })

    def elaborate(self, platform):
        m = Module()
        aw = self.config.address_width
        offset = self.offset & ((1 << aw) - 1)

        def relocate(addr):
            return (addr + offset)[:aw]

        # `io.output.aw << io.input.aw` (and ar), then `addr.allowOverride()`
        # and the relocated address -- one connect for the whole bus, then the
        # two address overrides, which in Amaranth is simply the later assignment.
        connect_axi4(m, self.output, self.input)
        m.d.comb += [
            self.output.aw.payload.addr.eq(relocate(self.input.aw.payload.addr)),
            self.output.ar.payload.addr.eq(relocate(self.input.ar.payload.addr)),
        ]
        return m
