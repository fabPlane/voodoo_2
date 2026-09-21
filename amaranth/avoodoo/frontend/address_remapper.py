"""``AddressRemapper`` -- the RGBZASTW register-layout translator.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/frontend/AddressRemapper.scala``
(SpinalHDL 1.15.0), instantiated by ``Core.scala:99`` as::

    AddressRemapper(RegisterBank.externalBmbParams(c), RegisterBank.bmbParams(c))

and spliced between ``CoreCpuFrontdoor.io.regBus`` and ``PciFifo.io.cpuSide``.

When ``fbiInit3.remap`` is enabled the host writes the float triangle
registers in RGBZASTW order at PCI addresses with bit 21 set.  This component
sits in front of ``RegisterBank`` and rewrites those addresses into the
standard layout, so nothing downstream has to know the remap exists.  It is
purely combinational -- no register, no latency, ``cmd.ready`` straight
through and the response bus wired across unchanged.

Address handling, in the Scala's own order:

=========================  ======================================================
``inputAddr[21] == 0``     ``translatedAddr := inputAddr.resized`` -- the 22-bit
                           address truncated to the 12-bit register bus.
bit 21, in 0x088..0x0fc    the :data:`~avoodoo.frontend.regdef.REMAP_TABLE`
                           lookup, defaulting to ``baseAddr`` for the holes
                           between the 30 word-aligned entries.
bit 21, anything else      ``inputAddr & ~(1 << 21)`` then resized to 12 bits,
                           which -- because the resize truncates -- is just
                           ``baseAddr`` again.  Transliterated as written
                           rather than folded, so the Verilog matches.
=========================  ======================================================

The remap table itself is not repeated here: it is
:data:`avoodoo.frontend.regdef.REMAP_TABLE`, extracted as data by plan task
2.1, and ``tests/test_address_remapper.py`` cross-checks it against the Scala.
"""

from __future__ import annotations

from amaranth import Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from avoodoo.bus.routing import drive_cmd_from
from avoodoo.frontend.regdef import REMAP_TABLE
from avoodoo.lib.membus import MemBusParameter, MemBusSignature
from avoodoo.lib.stream import connect_stream

__all__ = ["bmb_params", "external_bmb_params", "AddressRemapper"]


# ---------------------------------------------------------------------------
# RegisterBank's two bus parameter sets (RegisterBank.scala:1258 / :1270)
# ---------------------------------------------------------------------------
#
# One definition, in `avoodoo/core/membus_params.py` alongside every other
# `*BmbParams` of the design; `frontend/register_bank.py` re-exports the same
# two under the Scala names, and so does this module for its callers
# (`Core.scala:100` builds the remapper from `RegisterBank.externalBmbParams`
# / `RegisterBank.bmbParams`).

from avoodoo.core.membus_params import (  # noqa: E402
    register_bank_external_params as external_bmb_params,
    register_bank_params as bmb_params,
)


#: ``baseAddr >= 0x088 && baseAddr <= 0x0fc`` (AddressRemapper.scala:76).
REMAP_RANGE_LO = 0x088
REMAP_RANGE_HI = 0x0FC

#: The bit that selects the RGBZASTW layout.
REMAP_BIT = 21


class AddressRemapper(wiring.Component):
    """``case class AddressRemapper(inputParams, outputParams)``.

    Attributes
    ----------
    input : In(MemBusSignature(input_params))
        ``slave(Bmb(inputParams))``.
    output : Out(MemBusSignature(output_params))
        ``master(Bmb(outputParams))``.
    """

    def __init__(self, input_params: MemBusParameter, output_params: MemBusParameter):
        self.input_params = input_params
        self.output_params = output_params
        if input_params.access.address_width <= REMAP_BIT:
            raise ValueError(
                f"inputParams.addressWidth must carry bit {REMAP_BIT}; got "
                f"{input_params.access.address_width}")
        super().__init__({
            "input": In(MemBusSignature(input_params)),
            "output": Out(MemBusSignature(output_params)),
        })

    def elaborate(self, platform):
        m = Module()

        # val inputAddr = io.input.cmd.address
        input_addr = self.input.cmd.payload.fragment.address
        # val baseAddr  = inputAddr(11 downto 0)
        base_addr = input_addr[:12]
        # val hasBit21  = inputAddr(21)
        has_bit21 = input_addr[REMAP_BIT]

        # val inRemapRange = baseAddr >= 0x088 && baseAddr <= 0x0fc
        in_remap_range = Signal()
        m.d.comb += in_remap_range.eq(
            (base_addr >= REMAP_RANGE_LO) & (base_addr <= REMAP_RANGE_HI))

        output_addr_width = self.output_params.access.address_width
        translated_addr = Signal(output_addr_width, name="translatedAddr")

        with m.If(has_bit21):
            with m.If(in_remap_range):
                # default, then one `is()` per table entry
                m.d.comb += translated_addr.eq(base_addr)
                with m.Switch(base_addr):
                    for frm, to in REMAP_TABLE.items():
                        with m.Case(Const(frm, 12)):
                            m.d.comb += translated_addr.eq(
                                Const(to, output_addr_width))
            with m.Else():
                # (inputAddr & ~(U(1, w bits) |<< 21)).resized
                mask = Const(
                    ~(1 << REMAP_BIT) & ((1 << len(input_addr)) - 1),
                    len(input_addr))
                m.d.comb += translated_addr.eq(input_addr & mask)
        with m.Else():
            m.d.comb += translated_addr.eq(input_addr)

        # BmbRouting.driveCmdFrom(io.output, io.input, io.input.cmd.valid, translatedAddr)
        drive_cmd_from(m, self.output, self.input,
                       self.input.cmd.valid, translated_addr)
        # io.input.cmd.ready := io.output.cmd.ready
        m.d.comb += self.input.cmd.ready.eq(self.output.cmd.ready)

        # io.input.rsp << io.output.rsp
        connect_stream(m, self.input.rsp, self.output.rsp)

        return m
