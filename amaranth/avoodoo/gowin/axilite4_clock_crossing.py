"""``AxiLite4ClockCrossing`` -- two-clock AXI4-Lite crossing, five ``StreamFifoCC``.

Transliteration of ``fpga/src/voodoo/gowin/AxiLite4ClockCrossing.scala`` (37
lines), plan Phase 1.5.  ``TangMega138kPro.scala:76`` and
``artix/AmdAc701.scala:60`` build it as
``AxiLite4ClockCrossing(AxiLite4ToBmbBridge.axiConfig(barAddressWidth), pcieCd,
coreCd)`` with the default ``depth = 4``; the Console's BAR arrives in the
core clock already, so ``TangConsole138k`` has no instance.

Depth is small; the TLP BAR target only has one outstanding transaction.

Each channel is one :class:`avoodoo.lib.stream.FifoCC` (the ``StreamFifoCC``
port): ``aw``/``w``/``ar`` push in ``input_domain`` and pop in
``output_domain``, ``b``/``r`` the other way round.

**Pop-side reset.**  ``StreamFifoCC`` runs its pop side in
``popClock.withBufferedResetFrom(pushClock)`` (the
``crossClockBufferPushToPopResetGen`` default is true).  In SpinalHDL 1.15.0
that is ``ResetCtrl.asyncAssertSyncDeassertCreateCd`` (``CrossClock.scala:294``):
``clockCd.copy(reset = <2-flop BufferCC of the push reset, async-asserted>)``
-- i.e. the *bridge* asserts asynchronously, but the pop domain keeps its own
``resetKind`` (``SYNC`` for every domain in this design), so the FIFO's pop
registers are ``always @(posedge clk) if (reset_synchronized) ...``.  And
because ``withBufferedResetFrom`` goes through ``globalCache((cd, resetCd,
depth))`` (``Utils.scala:1391``), all FIFOs crossing the same pair of domains
share one synchronised reset.

:class:`avoodoo.lib.stream.FifoCC`'s own ``with_pop_buffered_reset`` declares
that pop domain ``async_reset=True`` instead, which the single-clock lockstep
diff caught (seed 3, cycle 0): with ``rst`` raised while a pop read is
pending, Spinal's sync reset still lets that read land on the first reset
edge and Amaranth's async reset does not, so the unreset RAM read register
(``ram_spinal_port1`` / ``pop.payload``) holds different stale data for the
next seed.  This component therefore builds the synchronised-reset domains
itself, exactly as the Scala does -- one sync-reset local domain per
(push, pop) pair, its reset from a :class:`amaranth.lib.cdc.ResetSynchronizer`
on the push domain's reset -- and hands them to ``FifoCC`` with
``with_pop_buffered_reset=False``.  ``FifoCC`` itself is Phase 1.1's and is
left alone; the discrepancy is recorded in the plan's Phase 1 checklist.

With ``input_domain == output_domain == "sync"`` the component collapses to a
single-clock design -- five gray-pointer FIFOs plus one shared reset
synchroniser -- which is how it is lockstep-diffed, because the
differential harness drives one clock (``tools/diff/harness.py`` pairs only
``clk``/``reset``).  The two-clock behaviour is covered by pysim only
(``tests/test_axi.py``).
"""

from __future__ import annotations

from amaranth import ClockDomain, ClockSignal, Module, ResetSignal
from amaranth.lib import cdc, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.axi import AxiLite4Config, AxiLite4Signature
from avoodoo.lib.stream import FifoCC, connect_stream

__all__ = ["AxiLite4ClockCrossing"]


class AxiLite4ClockCrossing(wiring.Component):
    """``case class AxiLite4ClockCrossing(config, inputCd, outputCd, depth = 4)``.

    ``input`` is the slave port in ``input_domain``; ``output`` the master
    port in ``output_domain``.
    """

    def __init__(self, config: AxiLite4Config, *, input_domain: str = "sync",
                 output_domain: str = "sync", depth: int = 4):
        self.config = config
        self.input_domain = input_domain
        self.output_domain = output_domain
        self.depth = depth
        super().__init__({
            "input": In(AxiLite4Signature(config)),
            "output": Out(AxiLite4Signature(config)),
        })

    def elaborate(self, platform):
        m = Module()
        i, o = self.input, self.output
        idom, odom = self.input_domain, self.output_domain

        # `popClock.withBufferedResetFrom(pushClock)`: a copy of the pop
        # domain (same clock, same *synchronous* reset kind) whose reset is the
        # async-assert / sync-deassert 2-flop buffer of the push reset.  One
        # per (push, pop) pair, as Spinal's globalCache makes it.
        buffered: dict[tuple[str, str], str] = {}

        def buffered_pop(push_dom: str, pop_dom: str) -> str:
            key = (push_dom, pop_dom)
            if key not in buffered:
                name = f"{pop_dom}_reset_from_{push_dom}"
                m.domains += ClockDomain(name, local=True)
                m.d.comb += ClockSignal(name).eq(ClockSignal(pop_dom))
                m.submodules[f"{name}_synchronizer"] = cdc.ResetSynchronizer(
                    ResetSignal(push_dom), domain=name)
                buffered[key] = name
            return buffered[key]

        def cc(name, push_dom, pop_dom, shape):
            f = FifoCC(shape, self.depth, push_domain=push_dom,
                       pop_domain=buffered_pop(push_dom, pop_dom),
                       with_pop_buffered_reset=False)
            m.submodules[name] = f
            return f

        aw = cc("aw", idom, odom, i.aw.payload.shape())
        w = cc("w", idom, odom, i.w.payload.shape())
        ar = cc("ar", idom, odom, i.ar.payload.shape())
        b = cc("b", odom, idom, o.b.payload.shape())
        r = cc("r", odom, idom, o.r.payload.shape())

        connect_stream(m, aw.push, i.aw)
        connect_stream(m, o.aw, aw.pop)
        connect_stream(m, w.push, i.w)
        connect_stream(m, o.w, w.pop)
        connect_stream(m, ar.push, i.ar)
        connect_stream(m, o.ar, ar.pop)
        connect_stream(m, b.push, o.b)
        connect_stream(m, i.b, b.pop)
        connect_stream(m, r.push, o.r)
        connect_stream(m, i.r, r.pop)
        return m
