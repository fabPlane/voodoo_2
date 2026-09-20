"""SpinalHDL clock-domain-crossing idioms for Amaranth.

Transliteration of ``spinal/lib/CrossClock.scala`` (``BufferCC``,
``PulseCCByToggle``, ``ResetCtrl.asyncAssertSyncDeassert``),
``spinal/lib/Utils.scala`` (``ClockDomainPimped.withBufferedResetFrom``) and
``spinal/core/ClockDomain.scala`` (``ClockDomain``, ``ClockingArea``,
``crossClockBufferPushToPopResetGen``), SpinalHDL **1.15.0**, read from
``spinalhdl-{lib,core}_2.12-1.15.0-sources.jar``.

What the design actually uses (grepped over ``ext/SpinalVoodoo/src`` +
``fpga/src``, 2026-09-20):

``BufferCC`` -- 56 call sites, every one of them the plain two-flop form.
    ``bufferDepth`` is **never** passed: no call site overrides the default of
    2 (``BufferCC.defaultDepth = ScopeProperty(2)``), and no ``ClockDomain``
    in the tree carries a ``CrossClockBufferDepth`` tag, so
    ``finalBufferDepth`` is 2 everywhere. ``randBoot``, ``inputAttributes``,
    ``allBufAttributes``, ``BufferCC.withTag`` and ``BufferCC.falsePath`` are
    unused by our code (``withTag`` is used *inside* ``PulseCCByToggle``).
    With ``init``: ``hdmi/HdmiScanout.scala`` :459-519 (10),
    ``gowin/TangConsole138k.scala`` :117-288 (38),
    ``gowin/GowinDdrNativeClockCrossing.scala`` :43,44,73 (3),
    ``gowin/ConsoleButtons.scala`` :29,30 (2), ``gowin/Bl616Link.scala`` :29.
    Without ``init`` (3): ``TangConsole138k.scala`` :172 (``screen``),
    :275 (``cursor4``), :280 (``hid16``) -- see note 2 below, they differ in
    the emitted Verilog, not in behaviour after reset.

``ClockDomain(clock =, reset =, config = ClockDomain.current.config)`` -- the
    only construction form used: ``HdmiScanout.scala`` :345 (``hdmiCd``),
    ``TangConsole138k.scala`` :71 (``ddrCd``), :89 (``ctrlCd``), :165
    (``hdmiCd``), plus ``TangMega138kPro.scala`` :53 and
    ``artix/AmdAc701.scala`` :49 (``pcieCd``). Never ``ClockDomain.internal``,
    never a clock enable, never a soft reset, never ``BOOT`` reset.
    ``ClockDomain.current.config`` is the ``SpinalConfig`` default set in
    ``fpga/src/voodoo/gowin/CoreGowinGen.scala`` :12-15,
    ``ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)`` -- which
    is exactly an Amaranth :class:`~amaranth.hdl.ClockDomain` at its defaults,
    so :func:`clock_domain` needs no polarity or kind juggling.

``ClockingArea`` -- 34 uses; in Amaranth this is
    :class:`~amaranth.hdl.DomainRenamer` over a submodule, or writing
    ``m.d[name]`` directly. :func:`clocking_area` is the renamer.

``ResetCtrl`` / ``asyncAssertSyncDeassert`` -- never called by name, but
    reached implicitly: ``StreamFifoCC`` and ``PulseCCByToggle`` both default
    ``withOutputBufferedReset`` / ``withPopBufferedReset`` to
    ``ClockDomain.crossClockBufferPushToPopResetGen``, whose default is
    **true**, so every ``StreamFifoCC`` (6 instances: the ``HdmiScanout``
    pixel FIFO and the five ``AxiLite4ClockCrossing`` channel FIFOs) and both
    ``PulseCCByToggle`` instances build a reset synchroniser on their output
    domain, driven from the *input* domain's reset.
    :func:`buffered_reset_domain` is that, and
    ``avoodoo.lib.stream.FifoCC`` already does it inline.

``GowinDdrNativeClockCrossing`` (``fpga/src/voodoo/gowin/``) is a bundled-data
    crossing: a request toggle and an ack toggle each go through a
    ``BufferCC(init = False)``, and the multi-bit payload registers are held
    stable across the round trip and tagged ``crossClockDomain`` so the
    timing tool treats them as a false path rather than a real synchroniser.
    :func:`cross_clock_payload` is the tag.

``AxiLite4ClockCrossing`` (``fpga/src/voodoo/gowin/``) has no CDC of its own:
    it is five ``StreamFifoCC(depth = 4)``, which is
    ``avoodoo.lib.stream.FifoCC``. Nothing to add here; Phase 1.5 ports the
    component itself.

Not ported (unused by the design): ``BufferCCBlackBox``, ``BufferCC.withTag``
as a public entry point, ``BufferCC.falsePath``, ``FlowCCByToggle``,
``StreamCCByToggle``, ``ResetCtrl.asyncAssertSyncDeassertDrive``, the ``BOOT``
reset kind branch of ``withBufferedResetFrom``, ``inputSync``, and
non-``HIGH`` reset polarities.
"""

from __future__ import annotations

from amaranth import ClockDomain, ClockSignal, Module, ResetSignal, Signal
from amaranth.hdl import DomainRenamer
from amaranth.lib import cdc as _amcdc
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.stream import _assign, _val

__all__ = [
    "DEFAULT_BUFFER_DEPTH",
    "BufferCC",
    "buffer_cc",
    "PulseCCByToggle",
    "pulse_cc_by_toggle",
    "clock_domain",
    "clocking_area",
    "reset_synchronizer",
    "buffered_reset_domain",
    "cross_clock_payload",
    "SPINAL_VS_AMARANTH_CDC",
]


#: ``BufferCC.defaultDepth = ScopeProperty(2)``. No call site in this design
#: overrides it and no clock domain carries a ``CrossClockBufferDepth`` tag,
#: so every crossing in the Spinal build is exactly two flops.
DEFAULT_BUFFER_DEPTH = 2

#: Synthesis attributes put on every synchroniser flop. Spinal tags the first
#: buffer ``crossClockDomain`` and the rest ``crossClockBuffer``, which its
#: constraint generator turns into SDC; Amaranth has no constraint generator,
#: so the attributes ride along on the signals and the hand-written
#: ``fpga/hw/gowin/console/*.sdc`` remains the authority (see note 5).
SYNC_FF_ATTRS = {"async_reg": "true"}


def _shape_of(value):
    v = _val(value)
    return v.shape() if hasattr(v, "shape") else len(v)


class BufferCC(wiring.Component):
    """``spinal.lib.BufferCC`` -- a chain of ``buffer_depth`` flops.

    ::

        buffers = Vec(Reg(dataType, init), finalBufferDepth)
        buffers(0) := io.dataIn
        for (i <- 1 until finalBufferDepth) buffers(i) := buffers(i - 1)
        io.dataOut := buffers.last

    ``init`` maps onto the register reset value *and* onto whether the flops
    are resettable at all: Spinal's ``Reg(dataType, init)`` with a ``null``
    init emits registers with **no reset**, which is
    ``reset_less = True`` here. Latency is ``buffer_depth`` cycles of the
    destination clock either way.
    """

    def __init__(self, shape, *, init=None, buffer_depth=None, attrs=None):
        depth = DEFAULT_BUFFER_DEPTH if buffer_depth is None else buffer_depth
        if depth < 1:
            raise ValueError(f"buffer_depth must be >= 1, not {depth}")
        self._shape = shape
        self._init = init
        self.buffer_depth = depth
        self._attrs = SYNC_FF_ATTRS if attrs is None else attrs
        super().__init__({
            "data_in": In(shape),
            "data_out": Out(shape),
        })

    def elaborate(self, platform):
        m = Module()
        flops = [
            Signal(self._shape, name=f"buffers_{i}",
                   init=0 if self._init is None else self._init,
                   reset_less=self._init is None, attrs=self._attrs)
            for i in range(self.buffer_depth)
        ]
        src = self.data_in
        for flop in flops:
            m.d.sync += _assign(flop, src)
            src = flop
        m.d.comb += _assign(self.data_out, flops[-1])
        return m


def buffer_cc(m, value, *, init=None, buffer_depth=None, o_domain="sync",
              attrs=None, name=None):
    """``BufferCC(input, init, bufferDepth)``: returns the synchronised value.

    A thin wrapper over :class:`amaranth.lib.cdc.FFSynchronizer`, which emits
    the identical flop chain. ``init = None`` means Spinal's ``init = null``,
    i.e. reset-less flops; any other value makes them reset to it, which is
    what 53 of the 56 ``BufferCC`` call sites in this design ask for.
    """
    depth = DEFAULT_BUFFER_DEPTH if buffer_depth is None else buffer_depth
    out = Signal(_shape_of(value), name=name or "buffercc")
    sync = _amcdc.FFSynchronizer(
        _val(value), _val(out), o_domain=o_domain,
        init=0 if init is None else init,
        reset_less=init is None, stages=depth)
    if name is None:
        m.submodules += sync
    else:
        m.submodules[name] = sync
    return out


class PulseCCByToggle(wiring.Component):
    """``spinal.lib.PulseCCByToggle``.

    ::

        inArea (clockIn) : target = RegInit(False) toggleWhen(io.pulseIn)
        outArea(clockOut): target = BufferCC.withTag(inArea.target, False)
                           io.pulseOut := target.edge(False)

    ``Bool.edge(initAt)`` is ``this ^ RegNext(this).init(initAt)``, i.e. *any*
    edge, so one input pulse becomes one output pulse regardless of direction.
    ``clockOut`` is ``withOptionalBufferedResetFrom(clockIn)`` with the
    condition defaulting to ``ClockDomain.crossClockBufferPushToPopResetGen``
    (**true**), so the output side runs off a reset synchronised from the
    input domain -- :func:`buffered_reset_domain`.

    Used at ``hdmi/HdmiScanout.scala`` :507 (``newFrame``, hdmi -> core) and
    ``gowin/TangConsole138k.scala`` :143 (``menu.start``, ctrl -> core).
    """

    def __init__(self, *, i_domain="sync", o_domain="sync",
                 with_output_buffered_reset=True):
        self.i_domain = i_domain
        self.o_domain = o_domain
        self.with_output_buffered_reset = bool(with_output_buffered_reset)
        super().__init__({
            "pulse_in": In(1),
            "pulse_out": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        target = Signal(name="inArea_target")
        with m.If(self.pulse_in):
            m.d[self.i_domain] += target.eq(~target)

        out_domain = self.o_domain
        if self.with_output_buffered_reset:
            out_domain = buffered_reset_domain(
                m, "pulsecc_out", clk=ClockSignal(self.o_domain),
                reset_from=self.i_domain).name

        synced = buffer_cc(m, target, init=0, o_domain=out_domain,
                           name="outArea_target")
        prev = Signal(name="outArea_target_regNext")
        m.d[out_domain] += prev.eq(synced)
        m.d.comb += self.pulse_out.eq(synced ^ prev)
        return m


def pulse_cc_by_toggle(m, pulse_in, *, i_domain="sync", o_domain="sync",
                       with_output_buffered_reset=True, name=None):
    """``PulseCCByToggle(input, clockIn, clockOut)``."""
    cc = PulseCCByToggle(i_domain=i_domain, o_domain=o_domain,
                         with_output_buffered_reset=with_output_buffered_reset)
    if name is None:
        m.submodules += cc
    else:
        m.submodules[name] = cc
    m.d.comb += cc.pulse_in.eq(pulse_in)
    return cc.pulse_out


def clock_domain(m, name, *, clk, rst=None, async_reset=False, local=False):
    """A Spinal-style ``ClockDomain(clock =, reset =, config = ...)``.

    The reset kind and polarity follow ``CoreGowinGen``'s ``SpinalConfig``
    (``fpga/src/voodoo/gowin/CoreGowinGen.scala`` :12-15,
    ``ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)``), which
    is what ``ClockDomain.current.config`` resolves to at every
    ``ClockDomain(...)`` site in the tree. An Amaranth clock domain is
    synchronous-reset, active-high by default, so the defaults here match one
    for one; ``async_reset = True`` is only for the reset synchronisers of
    :func:`buffered_reset_domain`, which Spinal builds with an explicit
    ``resetKind = ASYNC`` override of its own.

    ``rst = None`` makes a reset-less domain (Spinal's ``withReset = false``);
    nothing in the design does that today.
    """
    cd = ClockDomain(name, async_reset=async_reset, local=local,
                     reset_less=rst is None)
    m.domains += cd
    m.d.comb += cd.clk.eq(clk)
    if rst is not None:
        m.d.comb += cd.rst.eq(rst)
    return cd


def clocking_area(domain):
    """``new ClockingArea(cd) { ... }`` -> ``DomainRenamer`` for a submodule.

    Statements written straight into the parent module go in ``m.d[domain]``
    instead; the renamer is for whole sub-elaboratables, which is how every
    ``ClockingArea`` in ``TangConsole138k`` is shaped.
    """
    return DomainRenamer({"sync": domain})


def reset_synchronizer(m, arst, *, domain="sync", stages=None, name=None):
    """``ResetCtrl.asyncAssertSyncDeassert``, active-high in and out.

    Spinal builds a sampler clock domain that is a copy of ``domain`` but with
    ``resetKind = ASYNC`` and ``reset = arst``, then runs a
    ``BufferCC(input = False, init = True, bufferDepth)`` in it: the flops
    assert asynchronously with ``arst`` and shift a zero in, so deassertion
    takes ``stages`` cycles of ``domain``'s clock.
    :class:`amaranth.lib.cdc.ResetSynchronizer` is the identical circuit --
    ``stages`` flops, ``init = 1``, in a local ``async_reset`` domain -- and
    it also drives ``ResetSignal(domain)``, which Spinal leaves to the caller.
    ``domain`` must therefore be an async-reset domain.
    """
    stages = DEFAULT_BUFFER_DEPTH if stages is None else stages
    sync = _amcdc.ResetSynchronizer(arst, domain=domain, stages=stages)
    if name is None:
        m.submodules += sync
    else:
        m.submodules[name] = sync
    return sync


def buffered_reset_domain(m, name, *, clk, reset_from, stages=None):
    """``cd.withBufferedResetFrom(resetCd)`` -- returns the new ClockDomain.

    ``ClockDomain.crossClockBufferPushToPopResetGen`` defaults to **true**, so
    ``StreamFifoCC``'s pop side and ``PulseCCByToggle``'s output side both run
    off this rather than off the parent's reset for that clock: a local
    domain, clocked by ``clk``, whose reset is ``reset_from``'s reset pushed
    through :func:`reset_synchronizer` (async assert, sync deassert).

    ``reset_from`` is a domain name; its ``ResetSignal`` is the source.
    ``avoodoo.lib.stream.FifoCC`` open-codes the same three lines.
    """
    cd = ClockDomain(name, async_reset=True, local=True)
    m.domains += cd
    m.d.comb += cd.clk.eq(clk)
    reset_synchronizer(m, ResetSignal(reset_from), domain=name, stages=stages)
    return cd


def cross_clock_payload(*signals):
    """``sig.addTag(crossClockDomain)`` on a bundled-data payload register.

    ``GowinDdrNativeClockCrossing`` holds its request/response payload
    registers stable while a toggle crosses, and tags them so the timing tool
    does not chase the (deliberately unsynchronised) data path. Spinal turns
    the tag into SDC; Amaranth carries it as a signal attribute and the
    constraint stays hand-written in ``fpga/hw/gowin/console/*.sdc`` -- so
    this is documentation with a hook, not a constraint. Returns the signals
    so it can wrap a declaration.
    """
    for sig in signals:
        _val(sig).attrs["async_reg"] = "true"
    return signals[0] if len(signals) == 1 else signals


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

#: Differences between Spinal's CDC library and Amaranth's that a "looks
#: equivalent" substitution would get wrong. Phase 8 (Gowin Console timing)
#: is the consumer of notes 2, 4 and 5.
SPINAL_VS_AMARANTH_CDC = """
1. Latency matches exactly. amaranth.lib.cdc.FFSynchronizer defaults to
   stages = 2 and BufferCC to defaultDepth = 2, and both are a plain shift
   register with a combinational output tap, so a change on the source is
   visible on the destination after exactly 2 destination-clock edges. No call
   site in this design overrides the depth (checked: no `bufferDepth`
   argument, no CrossClockBufferDepth tag on any ClockDomain), so 2 is the
   number everywhere.

2. `init` means two different things at once in Spinal and they have to be
   ported together. `BufferCC(x, init = False)` builds `Reg(dataType, False)`:
   with the design's SYNC/HIGH ClockDomainConfig that is a *synchronously
   resettable* flop pair, so the synchroniser reads False for as long as the
   destination domain is in reset. `BufferCC(x)` (init = null) builds
   `Reg(dataType)`: no reset at all, and no `initial` either, so in Verilog
   simulation it starts X. Amaranth's FFSynchronizer is `reset_less = True` by
   default *and* always emits an init value, so the straight translation of
   the 53 init'd call sites needs `reset_less = False`, and the 3 un-init'd
   ones (TangConsole138k.scala:172 `screen`, :275 `cursor4`, :280 `hid16`)
   come out with a defined 0 at power-on where Spinal has X. That is a
   difference in the emitted Verilog, not in post-reset behaviour, but it will
   show up in a Verilator X-propagation comparison, so the diff harness has to
   know about those three.

3. Amaranth has no equivalent of Spinal's ClockDomainConfig, because an
   Amaranth ClockDomain is always active-high and is sync- or async-reset by
   construction. The design's SpinalConfig is SYNC/HIGH
   (CoreGowinGen.scala:12-15), which is the Amaranth default, so `clock_domain`
   is a one-to-one port. The single place where the Spinal build deviates from
   its own global config is inside ResetCtrl.asyncAssertSyncDeassert, which
   copies the domain with `resetKind = ASYNC` -- reproduced by
   `buffered_reset_domain`'s `async_reset = True`.

4. The reset synchroniser is structurally the same circuit in both, and the
   deassertion latency is the same 2 cycles: Spinal runs BufferCC(input =
   False, init = True) in an ASYNC/HIGH sampler domain; Amaranth's
   ResetSynchronizer runs `stages` flops with init = 1 in a local
   `async_reset` domain, shifting 0 in. The emitted Verilog differs in shape:
   Spinal instantiates a *separate module* (BufferCC, carrying
   `(* keep_hierarchy = "TRUE" *)`) and returns a Bool the caller assigns to
   the domain's reset, while Amaranth inlines the flops into the parent and
   drives ResetSignal(domain) itself. Same flops, same edges, different
   hierarchy -- so a netlist-level diff of the Console top will not match on
   module boundaries even when the logic does, and the Phase 8 comparison has
   to be at the flop level.

5. Constraints do not come across. Spinal tags the first synchroniser flop
   `crossClockDomain`, the rest `crossClockBuffer`, the reset synchroniser's
   flops `crossClockFalsePath(destType = RESET)`, and the
   GowinDdrNativeClockCrossing payload registers `crossClockDomain`; its
   constraint generator turns those into timing exceptions. Amaranth emits no
   such thing. `SYNC_FF_ATTRS` / `cross_clock_payload` put an `async_reg`
   attribute on the flops so they are at least findable, but the real
   false-path and max-delay constraints have to stay in the hand-written
   fpga/hw/gowin/console SDC, and Phase 8 must check that the Amaranth flop
   names the SDC matches are still the names Amaranth emits.

6. amaranth.lib.cdc.PulseSynchronizer is NOT a substitute for Spinal's
   PulseCCByToggle: it re-times the pulse through its own toggle *and* a
   feedback path, and it can drop pulses at a different rate. PulseCCByToggle
   is a bare toggle plus BufferCC plus an any-edge detector, with no feedback
   and no input-side handshake, so an input pulse closer together than the
   destination period is simply lost. Ported verbatim as
   `PulseCCByToggle` here.
"""
