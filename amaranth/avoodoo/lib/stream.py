"""SpinalHDL ``Stream`` semantics for Amaranth.

Transliteration of ``spinal/lib/Stream.scala`` and ``spinal/lib/Flow.scala``
(SpinalHDL **1.15.0**, read from ``spinalhdl-lib_2.12-1.15.0-sources.jar``),
together with the few ``spinal/lib/Counter.scala`` and ``spinal/lib/Utils.scala``
(``OHMasking``, ``MuxOH``, ``OHToUInt``) pieces the ported helpers need.

Scope rule (plan §6, Phase 1.1): only what the core actually instantiates is
ported, but what is ported is ported *exactly* -- same registers, same
combinational ready/valid paths, same occupancy timing -- because every module
is later lockstep-diffed against the Spinal Verilog (plan §4.2).

What the core uses (``ext/SpinalVoodoo/src/voodoo`` + ``fpga/src/voodoo``,
de10 wrapper excluded, counted 2026-09-20)::

    s2mPipe                      38   m2sPipe                      23
    map                          73   translateWith                37
    stage()                      17   queue(n)                     18
    StreamFifo                   14   StreamFifoCC                  6
    StreamJoin                    9   throwWhen                     8
    StreamFork2(synchronous=true) 6   StreamFork(5, synchronous=false) 1
    StreamDemux                   2   haltWhen  2   takeWhen  2
    StreamFork2(default async)    1   StreamArbiterFactory.assumeOhInput 1
    halfPipe                      1   Counter (StreamWhile, ...)

Not used by the core and therefore **not** ported: ``StreamFifoLowLatency``,
``StreamCCByToggle``, ``StreamWidthAdapter``, ``StreamTransactionExtender``,
``StreamUnpacker``/``Packer``, ``StreamDispatcherSequential``, ``repeat``,
``slowdown``, ``delay``, ``validPipe``, ``forkSerial``, ``toStream``,
``queueLowLatency``, ``queueOfReg``, ``formal*``. ``StreamMux`` and the
``lowerFirst``/``roundRobin``/``sequentialOrder`` arbitration policies and the
``transactionLock``/``fragmentLock``/``noLock`` lock policies are ported even
though the core only reaches for ``assumeOhInput`` + the implied (inert)
``transactionLock``: they were an explicit Phase 1.1 deliverable and the
``MemBus`` arbiter in Phase 1.2 is the next customer.

Semantic notes versus the Amaranth standard library -- these matter for the
lockstep diff, see :data:`SPINAL_VS_AMARANTH` at the bottom of this module.
"""

from __future__ import annotations

from amaranth import Array, Cat, Const, Module, Mux, Signal, unsigned
from amaranth.hdl import ClockDomain, ClockSignal, DomainRenamer, ResetSignal
from amaranth.lib import cdc, data, memory, stream, wiring
from amaranth.lib.wiring import In, Out
from amaranth.utils import ceil_log2

__all__ = [
    "Signature",
    "Stream",
    "FlowSignature",
    "Flow",
    "payload_shape_of",
    "fire",
    "is_stall",
    "is_free",
    "connect_stream",
    "arbitration_from",
    "comb_stage",
    "translate_with",
    "map_payload",
    "free_run",
    "set_idle",
    "set_blocked",
    "as_flow",
    "to_flow_fire",
    "continue_when",
    "halt_when",
    "throw_when",
    "discard_when",
    "take_when",
    "clear_valid_when",
    "M2SPipe",
    "S2MPipe",
    "HalfPipe",
    "m2s_pipe",
    "s2m_pipe",
    "half_pipe",
    "stage",
    "Fork",
    "fork",
    "fork2",
    "Join",
    "join",
    "Demux",
    "demux",
    "demux2",
    "Mux_",
    "mux",
    "Arbiter",
    "arbiter_on",
    "Fifo",
    "queue",
    "queue_with_occupancy",
    "DISTRIBUTED_RAM_ATTRS",
    "spinal_fifo_ram_attrs",
    "FifoCC",
    "queue_cc",
    "Counter",
    "oh_masking_first",
    "oh_masking_round_robin",
    "oh_to_uint",
    "mux_oh",
    "SPINAL_VS_AMARANTH",
]


# --------------------------------------------------------------------------
# small value helpers
# --------------------------------------------------------------------------


def _val(x):
    """The raw :class:`Value` behind a payload, which may be a ``data.View``."""
    return x.as_value() if isinstance(x, data.View) else x


def _assign(dst, src):
    return _val(dst).eq(_val(src))


def _is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def payload_shape_of(iface):
    """Payload shape of a stream/flow interface (a plain shape or a layout)."""
    payload = iface.payload
    return payload.shape() if hasattr(payload, "shape") else unsigned(len(payload))


# --------------------------------------------------------------------------
# Stream / Flow
# --------------------------------------------------------------------------

#: ``spinal.lib.Stream`` maps 1:1 onto Amaranth's minimal stream signature:
#: master-driven ``valid``/``payload``, slave-driven ``ready``. Re-exported so
#: ported modules only ever import from here.
Signature = stream.Signature


def Stream(payload_shape, *, path=None):
    """``Stream(T)``: a free-standing stream interface for internal signals."""
    return stream.Signature(payload_shape).create(path=path, src_loc_at=1)


class FlowSignature(wiring.Signature):
    """``spinal.lib.Flow``: ``valid`` + ``payload``, no back-pressure."""

    def __init__(self, payload_shape, *, payload_init=None):
        self._payload_shape = payload_shape
        super().__init__({
            "payload": Out(payload_shape, init=payload_init),
            "valid": Out(1),
        })

    @property
    def payload_shape(self):
        return self._payload_shape

    def __eq__(self, other):
        return type(other) is type(self) and other._payload_shape == self._payload_shape

    def __hash__(self):
        return hash((type(self), repr(self._payload_shape)))

    def __repr__(self):
        return f"FlowSignature({self._payload_shape!r})"


def Flow(payload_shape, *, path=None):
    """``Flow(T)``: a free-standing flow interface for internal signals."""
    return FlowSignature(payload_shape).create(path=path, src_loc_at=1)


def fire(s):
    """``Stream.fire`` = ``valid & ready`` (``Flow.fire`` = ``valid``)."""
    ready = getattr(s, "ready", None)
    return s.valid if ready is None else (s.valid & ready)


def is_stall(s):
    """``Stream.isStall`` = ``valid && !ready``."""
    return s.valid & ~s.ready


def is_free(s):
    """``Stream.isFree`` = ``!valid || ready``."""
    return ~s.valid | s.ready


def connect_stream(m, sink, source):
    """``sink << source``: ``Stream.connectFrom``, no registering.

    Written as plain combinational assignments rather than
    :func:`amaranth.lib.wiring.connect` so it also works between an interface
    owned by this module and a sub-component port.
    """
    m.d.comb += [
        sink.valid.eq(source.valid),
        source.ready.eq(sink.ready),
        _assign(sink.payload, source.payload),
    ]
    return source


def arbitration_from(m, sink, source):
    """``Stream.arbitrationFrom``: forward ``valid``/``ready`` only."""
    m.d.comb += [sink.valid.eq(source.valid), source.ready.eq(sink.ready)]


def comb_stage(m, source):
    """``Stream.combStage``: a renaming pass-through, no logic."""
    out = Stream(payload_shape_of(source))
    connect_stream(m, out, source)
    return out


def translate_with(m, source, payload):
    """``Stream.translateWith(that)``: same arbitration, new payload value."""
    shape = payload.shape() if hasattr(payload, "shape") else unsigned(len(payload))
    out = Stream(shape)
    arbitration_from(m, out, source)
    m.d.comb += _assign(out.payload, payload)
    return out


def map_payload(m, source, translate):
    """``Stream.map(f)`` / ``~~``: ``translate`` maps the payload value."""
    return translate_with(m, source, translate(source.payload))


def free_run(m, s):
    """``Stream.freeRun``: ``ready := True``."""
    m.d.comb += s.ready.eq(1)
    return s


def set_idle(m, s):
    """``Stream.setIdle``: ``valid := False`` (payload left at its default)."""
    m.d.comb += s.valid.eq(0)
    return s


def set_blocked(m, s):
    """``Stream.setBlocked``: ``ready := False``."""
    m.d.comb += s.ready.eq(0)
    return s


def as_flow(m, source):
    """``Stream.asFlow``: valid/payload copied, ``ready`` left untouched."""
    out = Flow(payload_shape_of(source))
    m.d.comb += [out.valid.eq(source.valid), _assign(out.payload, source.payload)]
    return out


def to_flow_fire(m, source):
    """``Stream.toFlowFire``: flow valid on ``fire``, ``ready`` untouched."""
    out = Flow(payload_shape_of(source))
    m.d.comb += [out.valid.eq(fire(source)), _assign(out.payload, source.payload)]
    return out


def continue_when(m, source, cond):
    """``Stream.continueWhen(cond)`` / ``&``: block the stream while ``!cond``."""
    out = Stream(payload_shape_of(source))
    m.d.comb += [
        out.valid.eq(source.valid & cond),
        source.ready.eq(out.ready & cond),
        _assign(out.payload, source.payload),
    ]
    return out


def halt_when(m, source, cond):
    """``Stream.haltWhen(cond)`` = ``continueWhen(!cond)``."""
    return continue_when(m, source, ~cond)


def throw_when(m, source, cond):
    """``Stream.throwWhen(cond)``: drop transactions while ``cond``.

    Spinal writes ``next << this`` and then overrides inside ``when(cond)``;
    the override order is reproduced here (the ``m.If`` wins).
    """
    out = Stream(payload_shape_of(source))
    m.d.comb += [
        out.valid.eq(source.valid),
        source.ready.eq(out.ready),
        _assign(out.payload, source.payload),
    ]
    with m.If(cond):
        m.d.comb += [out.valid.eq(0), source.ready.eq(1)]
    return out


def discard_when(m, source, cond):
    """``Stream.discardWhen`` -- Spinal's preferred alias of ``throwWhen``."""
    return throw_when(m, source, cond)


def take_when(m, source, cond):
    """``Stream.takeWhen(cond)`` = ``throwWhen(!cond)``."""
    return throw_when(m, source, ~cond)


def clear_valid_when(m, source, cond):
    """``Stream.clearValidWhen(cond)``: mask ``valid``, keep ``ready`` linked."""
    out = Stream(payload_shape_of(source))
    m.d.comb += [
        out.valid.eq(source.valid & ~cond),
        _assign(out.payload, source.payload),
        source.ready.eq(out.ready),
    ]
    return out


# --------------------------------------------------------------------------
# spinal.lib.Counter (the Up / Wrap flavour, which is what `Counter(n)` builds)
# --------------------------------------------------------------------------


class Counter:
    """``spinal.lib.Counter(stateCount)`` -- ``new Counter(0, stateCount - 1)``.

    Direction ``Up``, boundary policy ``Wrap``, so::

        w                  = log2Up(stateCount)
        willOverflowIfInc := value === end
        willOverflow      := willOverflowIfInc && willIncrement
        valueNext         := value + willIncrement
        when(willOverflow)  { valueNext := 0 }   # omitted when stateCount is pow2
        when(willClear)     { valueNext := 0 }
        value              = RegNext(valueNext) init(0)

    Not a :class:`~amaranth.hdl.Elaboratable`: like the Scala ``Area`` it emits
    its logic into the caller's :class:`Module` at construction time, and
    :meth:`increment` / :meth:`clear` are combinational pulses the caller
    drives from inside ``m.If`` blocks.
    """

    def __init__(self, m, state_count, *, name="counter"):
        if state_count < 1:
            raise ValueError(f"state_count must be >= 1, not {state_count}")
        self.state_count = state_count
        self.start = 0
        self.end = state_count - 1
        # Spinal: log2Up(end + 1). log2Up(1) == 0; Amaranth has no use for a
        # zero-width register, so a one-state counter gets one bit.
        self.width = max(1, ceil_log2(state_count))

        self.value = Signal(self.width, name=f"{name}_value")
        self.value_next = Signal(self.width, name=f"{name}_valueNext")
        self.will_increment = Signal(name=f"{name}_willIncrement")
        self.will_clear = Signal(name=f"{name}_willClear")
        self.will_overflow_if_inc = Signal(name=f"{name}_willOverflowIfInc")
        self.will_overflow = Signal(name=f"{name}_willOverflow")

        m.d.comb += [
            self.will_overflow_if_inc.eq(self.value == self.end),
            self.will_overflow.eq(self.will_overflow_if_inc & self.will_increment),
            self.value_next.eq(self.value + self.will_increment),
        ]
        # `naturalWrap` in Counter.scala: for a power-of-two state count with
        # start == 0 the truncating add already wraps, and Spinal emits no
        # comparator.
        if not _is_pow2(state_count):
            with m.If(self.will_overflow):
                m.d.comb += self.value_next.eq(self.start)
        with m.If(self.will_clear):
            m.d.comb += self.value_next.eq(self.start)
        m.d.sync += self.value.eq(self.value_next)

    def increment(self, m):
        m.d.comb += self.will_increment.eq(1)

    def clear(self, m):
        m.d.comb += self.will_clear.eq(1)


# --------------------------------------------------------------------------
# spinal.lib.OHMasking / MuxOH / OHToUInt
# --------------------------------------------------------------------------


def oh_masking_first(value):
    """``OHMasking.first``: ``input & ~(input - 1)``, keep the lowest set bit."""
    v = _val(value)
    return v & ~(v - 1)


def oh_masking_round_robin(requests, oh_priority):
    """``OHMasking.roundRobin(requests, ohPriority)``.

    ``doubleRequests = requests ## requests``;
    ``doubleGrant = doubleRequests & ~(doubleRequests - ohPriority)``;
    result is the OR of the two halves of ``doubleGrant``.
    """
    req = _val(requests)
    width = len(req)
    double_requests = Cat(req, req)
    double_grant = double_requests & ~(double_requests - _val(oh_priority))
    return double_grant[width:] | double_grant[:width]


def oh_to_uint(one_hot):
    """``spinal.lib.OHToUInt``: OR the indices of the set bits."""
    bits = _val(one_hot)
    width = max(1, ceil_log2(len(bits)))
    out = []
    for k in range(width):
        terms = [bits[i] for i in range(len(bits)) if (i >> k) & 1]
        out.append(Const(0, 1) if not terms else _reduce_or(terms))
    return Cat(*out)


def _reduce_or(terms):
    acc = terms[0]
    for t in terms[1:]:
        acc = acc | t
    return acc


def mux_oh(one_hot, inputs):
    """``spinal.lib.MuxOH``: two inputs become a plain ``Mux``, more an index."""
    sel = _val(one_hot)
    if len(inputs) == 2:
        return Mux(sel[0], _val(inputs[0]), _val(inputs[1]))
    return Array([_val(i) for i in inputs])[oh_to_uint(sel)]


# --------------------------------------------------------------------------
# pipes
# --------------------------------------------------------------------------


class M2SPipe(wiring.Component):
    """``Stream.m2sPipe`` -- cut ``valid``/``payload`` with registers, latency 1.

    ::

        rValid = RegNextWhen(i.valid, i.ready) init(False)
        rData  = RegNextWhen(i.payload, holdPayload ? i.fire : i.ready)
        i.ready := o.ready ; if collapsBubble: i.ready setWhen(!o.valid)
        o.valid := rValid ; o.payload := rData
        if flush: rValid clearWhen(flush)

    ``stage()`` is the same thing under another name.
    """

    def __init__(self, payload_shape, *, collaps_bubble=True, hold_payload=False,
                 with_flush=False, init_payload=None):
        self._shape = payload_shape
        self._collaps_bubble = bool(collaps_bubble)
        self._hold_payload = bool(hold_payload)
        self._with_flush = bool(with_flush)
        self._init_payload = init_payload
        ports = {
            "i": In(stream.Signature(payload_shape)),
            "o": Out(stream.Signature(payload_shape)),
        }
        if with_flush:
            ports["flush"] = In(1)
        super().__init__(ports)

    def elaborate(self, platform):
        m = Module()
        r_valid = Signal(name="rValid")
        # `initNull(initPayload)`: reset only when an initPayload was given.
        # See the reset_less note in S2MPipe.
        if self._init_payload is not None:
            r_data = Signal(self._shape, name="rData", init=self._init_payload)
        else:
            r_data = Signal(self._shape, name="rData", reset_less=True)

        with m.If(self.i.ready):
            m.d.sync += r_valid.eq(self.i.valid)
        load = fire(self.i) if self._hold_payload else self.i.ready
        with m.If(load):
            m.d.sync += _assign(r_data, self.i.payload)
        if self._with_flush:
            with m.If(self.flush):
                m.d.sync += r_valid.eq(0)

        m.d.comb += self.i.ready.eq(self.o.ready)
        if self._collaps_bubble:
            with m.If(~self.o.valid):
                m.d.comb += self.i.ready.eq(1)
        m.d.comb += [self.o.valid.eq(r_valid), _assign(self.o.payload, r_data)]
        return m


class S2MPipe(wiring.Component):
    """``Stream.s2mPipe`` -- cut the ``ready`` path with a register, latency 0.

    ::

        rValidN = RegInit(True) clearWhen(i.valid) setWhen(o.ready)
        rData   = RegNextWhen(i.payload, i.ready)
        i.ready := rValidN
        o.valid := i.valid || !rValidN
        o.payload := Mux(rValidN, i.payload, rData)
        if flush: rValidN setWhen(flush)

    Note the Scala evaluation order: ``setWhen`` is registered after
    ``clearWhen``, so ``o.ready`` wins over ``i.valid``, and ``flush`` wins over
    both. That order is reproduced literally below.
    """

    def __init__(self, payload_shape, *, with_flush=False, save_power=False):
        self._shape = payload_shape
        self._with_flush = bool(with_flush)
        self._save_power = bool(save_power)
        ports = {
            "i": In(stream.Signature(payload_shape)),
            "o": Out(stream.Signature(payload_shape)),
        }
        if with_flush:
            ports["flush"] = In(1)
        super().__init__(ports)

    def elaborate(self, platform):
        m = Module()
        r_valid_n = Signal(init=1, name="rValidN")
        # `RegNextWhen(payload, ...)` is a plain `Reg`, not a `RegInit`: Spinal
        # leaves it unreset. Amaranth resets every Signal to its init unless
        # told otherwise, and an unreset-vs-zero payload register is exactly
        # what the plan-0.4 lockstep diff catches (it found the same thing in
        # StreamWhile's `state`). Every plain Spinal `Reg` ported here is
        # `reset_less=True`.
        r_data = Signal(self._shape, name="rData", reset_less=True)

        with m.If(self.i.valid):
            m.d.sync += r_valid_n.eq(0)
        with m.If(self.o.ready):
            m.d.sync += r_valid_n.eq(1)
        if self._with_flush:
            with m.If(self.flush):
                m.d.sync += r_valid_n.eq(1)

        load = (fire(self.i) & ~self.o.ready) if self._save_power else self.i.ready
        with m.If(load):
            m.d.sync += _assign(r_data, self.i.payload)

        m.d.comb += [
            self.i.ready.eq(r_valid_n),
            self.o.valid.eq(self.i.valid | ~r_valid_n),
            _assign(self.o.payload,
                    Mux(r_valid_n, _val(self.i.payload), _val(r_data))),
        ]
        return m


class HalfPipe(wiring.Component):
    """``Stream.halfPipe`` -- cut every path, at half the bandwidth.

    ::

        rValid = RegInit(False) setWhen(i.valid) clearWhen(o.fire)
        rData  = RegNextWhen(i.payload, i.ready)
        i.ready := !rValid ; o.valid := rValid ; o.payload := rData
        if flush: rValid clearWhen(flush)
    """

    def __init__(self, payload_shape, *, with_flush=False):
        self._shape = payload_shape
        self._with_flush = bool(with_flush)
        ports = {
            "i": In(stream.Signature(payload_shape)),
            "o": Out(stream.Signature(payload_shape)),
        }
        if with_flush:
            ports["flush"] = In(1)
        super().__init__(ports)

    def elaborate(self, platform):
        m = Module()
        r_valid = Signal(name="rValid")
        # unreset `Reg`; see the note in S2MPipe.
        r_data = Signal(self._shape, name="rData", reset_less=True)

        with m.If(self.i.valid):
            m.d.sync += r_valid.eq(1)
        with m.If(fire(self.o)):
            m.d.sync += r_valid.eq(0)
        if self._with_flush:
            with m.If(self.flush):
                m.d.sync += r_valid.eq(0)
        with m.If(self.i.ready):
            m.d.sync += _assign(r_data, self.i.payload)

        m.d.comb += [
            self.i.ready.eq(~r_valid),
            self.o.valid.eq(r_valid),
            _assign(self.o.payload, r_data),
        ]
        return m


def _attach(m, sub, name):
    if name is None:
        m.submodules += sub
    else:
        m.submodules[name] = sub
    return sub


def m2s_pipe(m, source, *, flush=None, collaps_bubble=True, hold_payload=False,
             init_payload=None, name=None):
    """``source.m2sPipe(...)``."""
    pipe = M2SPipe(payload_shape_of(source), collaps_bubble=collaps_bubble,
                   hold_payload=hold_payload, with_flush=flush is not None,
                   init_payload=init_payload)
    _attach(m, pipe, name)
    connect_stream(m, pipe.i, source)
    if flush is not None:
        m.d.comb += pipe.flush.eq(flush)
    return pipe.o


def s2m_pipe(m, source, *, flush=None, save_power=False, name=None):
    """``source.s2mPipe(...)``."""
    pipe = S2MPipe(payload_shape_of(source), with_flush=flush is not None,
                   save_power=save_power)
    _attach(m, pipe, name)
    connect_stream(m, pipe.i, source)
    if flush is not None:
        m.d.comb += pipe.flush.eq(flush)
    return pipe.o


def half_pipe(m, source, *, flush=None, name=None):
    """``source.halfPipe(...)``."""
    pipe = HalfPipe(payload_shape_of(source), with_flush=flush is not None)
    _attach(m, pipe, name)
    connect_stream(m, pipe.i, source)
    if flush is not None:
        m.d.comb += pipe.flush.eq(flush)
    return pipe.o


def stage(m, source, *, name=None):
    """``source.stage()`` -- ``m2sPipe`` with a nicer name in the HDL."""
    return m2s_pipe(m, source, name=name)


# --------------------------------------------------------------------------
# fork / join / demux / mux / arbiter
# --------------------------------------------------------------------------


class Fork(wiring.Component):
    """``StreamFork`` / ``StreamForkArea``.

    ``synchronous=True`` (the core's usual choice, via ``StreamFork2``)::

        i.ready := AND(o[*].ready)
        o[*].valid := i.valid && i.ready        # note: valid depends on ready
        o[*].payload := i.payload

    ``synchronous=False`` keeps a ``linkEnable`` bit per output so the outputs
    may consume at different times; ``i.ready`` is only high once every output
    has taken (or is taking) the value::

        linkEnable = Vec(RegInit(True), n)
        i.ready := True
        for i: when(!o(i).ready && linkEnable(i)) { i.ready := False }
        for i: o(i).valid := i.valid && linkEnable(i); o(i).payload := i.payload
               when(o(i).fire) { linkEnable(i) := False }
        when(i.ready) { linkEnable := all True }   # written last, so it wins
    """

    def __init__(self, payload_shape, port_count, *, synchronous=False):
        if port_count < 1:
            raise ValueError("port_count must be >= 1")
        self._shape = payload_shape
        self.port_count = port_count
        self.synchronous = bool(synchronous)
        super().__init__({
            "i": In(stream.Signature(payload_shape)),
            "outputs": Out(stream.Signature(payload_shape)).array(port_count),
        })

    def elaborate(self, platform):
        m = Module()
        outs = self.outputs
        if self.port_count == 1:
            connect_stream(m, outs[0], self.i)
            return m

        if self.synchronous:
            ready = outs[0].ready
            for o in outs[1:]:
                ready = ready & o.ready
            m.d.comb += self.i.ready.eq(ready)
            for o in outs:
                m.d.comb += [
                    o.valid.eq(self.i.valid & self.i.ready),
                    _assign(o.payload, self.i.payload),
                ]
            return m

        link_enable = [Signal(init=1, name=f"linkEnable_{k}")
                       for k in range(self.port_count)]
        m.d.comb += self.i.ready.eq(1)
        for k, o in enumerate(outs):
            with m.If(~o.ready & link_enable[k]):
                m.d.comb += self.i.ready.eq(0)
        for k, o in enumerate(outs):
            m.d.comb += [
                o.valid.eq(self.i.valid & link_enable[k]),
                _assign(o.payload, self.i.payload),
            ]
            with m.If(fire(o)):
                m.d.sync += link_enable[k].eq(0)
        with m.If(self.i.ready):
            m.d.sync += [le.eq(1) for le in link_enable]
        return m


def fork(m, source, port_count, *, synchronous=False, name=None):
    """``StreamFork(input, portCount, synchronous)`` -> list of streams."""
    f = Fork(payload_shape_of(source), port_count, synchronous=synchronous)
    _attach(m, f, name)
    connect_stream(m, f.i, source)
    return list(f.outputs)


def fork2(m, source, *, synchronous=False, name=None):
    """``StreamFork2(input, synchronous)`` -> ``(a, b)``."""
    a, b = fork(m, source, 2, synchronous=synchronous, name=name)
    return a, b


def join_layout(shapes):
    """Payload layout of ``StreamJoin(a, b, ...)`` -- Spinal's ``TupleBundleN``."""
    return data.StructLayout({f"_{k + 1}": s for k, s in enumerate(shapes)})


class Join(wiring.Component):
    """``StreamJoin``: fire only when every input is valid.

    ::

        o.valid := AND(i[*].valid)
        i[*].ready := o.fire
        o.payload._k := i[k].payload

    Because ``i.ready`` is ``o.fire`` and ``o.fire`` includes ``o.ready``, the
    inputs see ``ready`` only when the join itself fires; this is the Spinal
    behaviour and is why the core buffers join inputs (``.queue()``/``.stage()``)
    before joining.

    The inputs may have different payload shapes, so they are separate ports
    ``i0``, ``i1``, ... (also reachable as the list :attr:`inputs`) rather than
    an array; the output payload is Spinal's ``TupleBundleN`` layout, fields
    ``_1``, ``_2``, ...
    """

    def __init__(self, shapes):
        self._shapes = list(shapes)
        self.layout = join_layout(self._shapes)
        ports = {f"i{k}": In(stream.Signature(s)) for k, s in enumerate(self._shapes)}
        ports["o"] = Out(stream.Signature(self.layout))
        super().__init__(ports)

    @property
    def inputs(self):
        return [getattr(self, f"i{k}") for k in range(len(self._shapes))]

    def elaborate(self, platform):
        m = Module()
        ins = self.inputs
        valid = ins[0].valid
        for s in ins[1:]:
            valid = valid & s.valid
        m.d.comb += self.o.valid.eq(valid)
        out_fire = fire(self.o)
        for k, s in enumerate(ins):
            m.d.comb += [
                s.ready.eq(out_fire),
                _assign(self.o.payload[f"_{k + 1}"], s.payload),
            ]
        return m


def join(m, *sources, name=None):
    """``StreamJoin(a, b, ...)`` -> a stream of ``{_1: a, _2: b, ...}``."""
    j = Join([payload_shape_of(s) for s in sources])
    _attach(m, j, name)
    for port, src in zip(j.inputs, sources):
        connect_stream(m, port, src)
    return j.o


class Demux(wiring.Component):
    """``StreamDemux``: route the input to ``outputs[select]``.

    ::

        i.ready := False
        for k: o(k).payload := i.payload
               when(k =/= select) { o(k).valid := False }
               otherwise          { o(k).valid := i.valid; i.ready := o(k).ready }
    """

    def __init__(self, payload_shape, port_count):
        self._shape = payload_shape
        self.port_count = port_count
        super().__init__({
            "select": In(max(1, ceil_log2(port_count))),
            "i": In(stream.Signature(payload_shape)),
            "outputs": Out(stream.Signature(payload_shape)).array(port_count),
        })

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.i.ready.eq(0)
        for k, o in enumerate(self.outputs):
            m.d.comb += _assign(o.payload, self.i.payload)
            with m.If(self.select != k):
                m.d.comb += o.valid.eq(0)
            with m.Else():
                m.d.comb += [o.valid.eq(self.i.valid), self.i.ready.eq(o.ready)]
        return m


def demux(m, source, select, port_count, *, name=None):
    """``StreamDemux(input, select, portCount)`` -> list of streams."""
    d = Demux(payload_shape_of(source), port_count)
    _attach(m, d, name)
    connect_stream(m, d.i, source)
    m.d.comb += d.select.eq(select)
    return list(d.outputs)


def demux2(m, source, select, *, name=None):
    """``StreamDemux.two(input, select)`` -- the pair, each through a combStage."""
    outs = demux(m, source, select, 2, name=name)
    return comb_stage(m, outs[0]), comb_stage(m, outs[1])


class Mux_(wiring.Component):
    """``StreamMux``: select one of ``inputs`` (named with a trailing underscore
    so it does not shadow :func:`amaranth.hdl.Mux`).

    ::

        for k: i(k).ready := (select === k) && o.ready
        o.valid   := i(select).valid
        o.payload := i(select).payload

    Unused by the core today; ported because it is a Phase 1.1 deliverable.
    """

    def __init__(self, payload_shape, port_count):
        self._shape = payload_shape
        self.port_count = port_count
        super().__init__({
            "select": In(max(1, ceil_log2(port_count))),
            "inputs": In(stream.Signature(payload_shape)).array(port_count),
            "o": Out(stream.Signature(payload_shape)),
        })

    def elaborate(self, platform):
        m = Module()
        for k, i in enumerate(self.inputs):
            m.d.comb += i.ready.eq((self.select == k) & self.o.ready)
        valids = Array([i.valid for i in self.inputs])
        payloads = Array([_val(i.payload) for i in self.inputs])
        m.d.comb += [
            self.o.valid.eq(valids[self.select]),
            _assign(self.o.payload, payloads[self.select]),
        ]
        return m


def mux(m, select, sources, *, name=None):
    """``StreamMux(select, inputs)``."""
    mx = Mux_(payload_shape_of(sources[0]), len(sources))
    _attach(m, mx, name)
    for port, src in zip(mx.inputs, sources):
        connect_stream(m, port, src)
    m.d.comb += mx.select.eq(select)
    return mx.o


#: Arbitration policies of ``StreamArbiter``.
ARBITRATION_POLICIES = ("lowerFirst", "roundRobin", "sequentialOrder", "assumeOhInput")
#: Lock policies of ``StreamArbiter``.
LOCK_POLICIES = ("transactionLock", "fragmentLock", "noLock")


class Arbiter(wiring.Component):
    """``StreamArbiter`` built the way ``StreamArbiterFactory`` builds it.

    Defaults match ``StreamArbiterFactory``: ``lowerFirst`` + ``transactionLock``.
    The core's single use is ``StreamArbiterFactory.assumeOhInput.on(...)``
    (``PixelPipeline.scala:245``); for ``assumeOhInput`` the lock logic is
    elaborated away by Spinal (``lockFlagEnable`` is false), which is
    reproduced here.

    ``fragmentLock`` requires the payload to carry a ``last`` field -- see
    :mod:`avoodoo.lib.fragment`.
    """

    def __init__(self, payload_shape, port_count, *, arbitration="lowerFirst",
                 lock="transactionLock"):
        if arbitration not in ARBITRATION_POLICIES:
            raise ValueError(f"unknown arbitration policy {arbitration!r}")
        if lock not in LOCK_POLICIES:
            raise ValueError(f"unknown lock policy {lock!r}")
        self._shape = payload_shape
        self.port_count = port_count
        self.arbitration = arbitration
        self.lock = lock
        # StreamArbiter.scala:
        #   lockFlagEnable     = portCount > 1 && lock != NoLock && arb != AssumeOhInput
        #   maskLockFlagEnable = lockFlagEnable, or portCount > 1 when RoundRobin
        self.lock_flag_enable = (port_count > 1 and lock != "noLock"
                                 and arbitration != "assumeOhInput")
        self.mask_lock_flag_enable = (port_count > 1 if arbitration == "roundRobin"
                                      else self.lock_flag_enable)
        super().__init__({
            "inputs": In(stream.Signature(payload_shape)).array(port_count),
            "o": Out(stream.Signature(payload_shape)),
            "chosen": Out(max(1, ceil_log2(port_count))),
            "chosen_oh": Out(port_count),
        })

    def elaborate(self, platform):
        m = Module()
        n = self.port_count
        ins = self.inputs

        mask_proposal = Signal(n, name="maskProposal")
        locked = Signal(name="locked") if self.lock_flag_enable else None
        if self.mask_lock_flag_enable:
            # RoundRobin is the only policy that gives `maskLocked` an init
            # (`maskLocked(i) init(i == n - 1)`); for the others it is a plain
            # unreset `Reg(Vec(Bool, n))`. See the note in S2MPipe.
            if self.arbitration == "roundRobin":
                mask_locked = Signal(n, name="maskLocked", init=1 << (n - 1))
            else:
                mask_locked = Signal(n, name="maskLocked", reset_less=True)
        else:
            mask_locked = None

        if self.lock_flag_enable:
            mask_routed = Signal(n, name="maskRouted")
            m.d.comb += mask_routed.eq(Mux(locked, mask_locked, mask_proposal))
        else:
            mask_routed = mask_proposal

        if self.mask_lock_flag_enable:
            with m.If(self.o.valid):
                m.d.sync += mask_locked.eq(mask_routed)

        valids = Cat(*[i.valid for i in ins])
        if self.arbitration == "lowerFirst":
            m.d.comb += mask_proposal.eq(oh_masking_first(valids))
        elif self.arbitration == "assumeOhInput":
            m.d.comb += mask_proposal.eq(valids)
        elif self.arbitration == "sequentialOrder":
            if n > 1:
                counter = Counter(m, n, name="seqCounter")
                with m.If(fire(self.o)):
                    counter.increment(m)
                m.d.comb += mask_proposal.eq(1 << counter.value)
        elif self.arbitration == "roundRobin":
            if self.mask_lock_flag_enable:
                # ohPriority = maskLocked rotated up by one bit index.
                priority = Cat(mask_locked[n - 1], mask_locked[: n - 1])
                m.d.comb += mask_proposal.eq(
                    oh_masking_round_robin(valids, priority))

        if locked is not None:
            if self.lock == "transactionLock":
                with m.If(self.o.valid):
                    m.d.sync += locked.eq(1)
                with m.If(fire(self.o)):
                    m.d.sync += locked.eq(0)
            elif self.lock == "fragmentLock":
                with m.If(self.o.valid):
                    m.d.sync += locked.eq(1)
                with m.If(fire(self.o) & self.o.payload.last):
                    m.d.sync += locked.eq(0)

        if n == 1:
            connect_stream(m, self.o, ins[0])
            m.d.comb += [self.chosen.eq(0), self.chosen_oh.eq(1)]
            return m

        m.d.comb += [
            self.o.valid.eq((valids & mask_routed).any()),
            _assign(self.o.payload,
                    mux_oh(mask_routed, [i.payload for i in ins])),
        ]
        for k, i in enumerate(ins):
            m.d.comb += i.ready.eq(mask_routed[k] & self.o.ready)
        m.d.comb += [
            self.chosen_oh.eq(mask_routed),
            self.chosen.eq(oh_to_uint(mask_routed)),
        ]
        return m


def arbiter_on(m, sources, *, arbitration="lowerFirst", lock="transactionLock",
               name=None):
    """``StreamArbiterFactory.<policy>.on(inputs)`` -- returns the combStage
    output, exactly as ``StreamArbiterFactory.on`` does."""
    a = Arbiter(payload_shape_of(sources[0]), len(sources),
                arbitration=arbitration, lock=lock)
    _attach(m, a, name)
    for port, src in zip(a.inputs, sources):
        connect_stream(m, port, src)
    return comb_stage(m, a.o)


# --------------------------------------------------------------------------
# StreamFifo
# --------------------------------------------------------------------------

#: The attribute ``force_distributed_fifo_ram.py`` stamps on wide, shallow
#: FIFO arrays in the Spinal Console build.
DISTRIBUTED_RAM_ATTRS = {"syn_ramstyle": "distributed_ram"}


def spinal_fifo_ram_attrs(width: int, depth: int) -> dict:
    """``force_distributed_fifo_ram.should_force(width, depth)`` as ``mem_attrs``.

    Returns :data:`DISTRIBUTED_RAM_ATTRS` for ``width >= 24 and depth <= 64``
    (the Spinal build's rule) and ``{}`` otherwise, so a port can write
    ``Fifo(shape, depth, mem_attrs=spinal_fifo_ram_attrs(len(shape), depth))``
    and get the same BSRAM/SSRAM split the Spinal bitstream has. ``width`` is
    the packed payload width in bits.
    """
    return dict(DISTRIBUTED_RAM_ATTRS) if (width >= 24 and depth <= 64) else {}


class Fifo(wiring.Component):
    """``spinal.lib.StreamFifo`` (the 1.8.2 redesign shipped in 1.15.0).

    ``StreamFifo(T, depth, latency = 2)`` -- the only form the core uses --
    means ``withAsyncRead = latency < 2`` and ``withBypass = latency == 0``,
    i.e. the default is a **synchronous-read RAM plus an output ``m2sPipe``**.
    All three latencies are ported; ``forFMax`` and ``useVec`` are not (the
    core never asks for them) and raise.

    Ports mirror ``io``: ``push``, ``pop``, ``flush``, ``occupancy``,
    ``availability``. The core reads ``occupancy`` in five places
    (``FramebufferPlaneReader``/``PlaneBuffer``/``PlaneDirectReader``),
    ``availability`` once (``PciFifo``: ``io.pciFifoFree``) and drives ``flush``
    on the two ``FramebufferPlaneBuffer`` slot FIFOs, so none of the three is
    optional.

    Occupancy timing, which is the subtle part and the reason this is not
    :class:`amaranth.lib.fifo.SyncFIFOBuffered`:

    * for a power-of-two ``depth`` (``withExtraMsb``) the pointers carry one
      extra MSB and ``occupancy := push - popOnIo`` is *combinational from the
      pointer registers*, so it changes the cycle **after** a push or a pop;
    * ``popOnIo`` is ``RegNextWhen(pop, readArbitration.fire)``, i.e. the pop
      pointer as sampled when the output register hands the payload over --
      the entry sitting in the output pipe still counts as occupied;
    * for a non-power-of-two ``depth`` occupancy is a separate up/down counter
      clocked by ``push.fire``/``pop.fire``, which gives the same one-cycle-late
      visibility but is a different netlist;
    * ``full`` is computed against ``popOnIo``, not ``pop``, so the usable
      capacity is ``depth`` -- the output register does not add a slot.

    ``mem_attrs`` is forwarded to :class:`amaranth.lib.memory.Memory` as its
    ``attrs`` and lands on the RAM array in the emitted Verilog. It replaces
    ``fpga/hw/gowin/console/force_distributed_fifo_ram.py``, which rewrites
    the Spinal Verilog after the fact to put ``syn_ramstyle = "distributed_ram"``
    on every FIFO array with ``width >= 24 and depth <= 64`` (Gowin maps a
    WxD SDP RAM onto ``ceil(W/9)`` BSRAM blocks, so the wide, shallow
    ``FramebufferPlaneReader`` FIFOs would blow the 138K's 340-block cap). In
    the Amaranth tree the attribute is set at the instantiation instead --
    see :func:`spinal_fifo_ram_attrs` for the same rule as a helper. Plan 0.3
    verified that Gowin EDA honours the attribute from Amaranth's Verilog.
    """

    def __init__(self, payload_shape, depth, *, latency=2, with_async_read=None,
                 with_bypass=None, allow_extra_msb=True, use_vec=False,
                 for_fmax=False, mem_attrs=None):
        if depth < 0:
            raise ValueError("depth must be >= 0")
        if not 0 <= latency <= 2:
            raise ValueError("latency must be 0, 1 or 2")
        if for_fmax:
            raise NotImplementedError("forFMax is unused by the core")
        if use_vec:
            raise NotImplementedError("useVec is unused by the core")
        self._shape = payload_shape
        self.depth = depth
        self.latency = latency
        self.with_async_read = (latency < 2) if with_async_read is None else bool(with_async_read)
        self.with_bypass = (latency == 0) if with_bypass is None else bool(with_bypass)
        if self.with_bypass and not self.with_async_read:
            raise ValueError("withBypass requires withAsyncRead")
        self.allow_extra_msb = bool(allow_extra_msb)
        self.with_extra_msb = self.allow_extra_msb and _is_pow2(depth)
        self.mem_attrs = dict(mem_attrs) if mem_attrs else {}
        super().__init__({
            "push": In(stream.Signature(payload_shape)),
            "pop": Out(stream.Signature(payload_shape)),
            "flush": In(1),
            "occupancy": Out(ceil_log2(depth + 1)),
            "availability": Out(ceil_log2(depth + 1)),
        })

    def elaborate(self, platform):
        m = Module()
        depth = self.depth

        if depth == 0:
            connect_stream(m, self.pop, self.push)
            m.d.comb += [self.occupancy.eq(0), self.availability.eq(0)]
            return m

        if depth == 1:
            # `oneStage`: a single m2sPipe with a flush, plus an optional bypass.
            do_flush = Signal(name="doFlush")
            m.d.comb += do_flush.eq(self.flush)
            pipe = M2SPipe(self._shape, with_flush=True)
            m.submodules.buffer = pipe
            connect_stream(m, pipe.i, self.push)
            m.d.comb += pipe.flush.eq(do_flush)
            connect_stream(m, self.pop, pipe.o)
            m.d.comb += [
                self.occupancy.eq(pipe.o.valid),
                self.availability.eq(~pipe.o.valid),
            ]
            if self.with_bypass:
                with m.If(~pipe.o.valid):
                    m.d.comb += [
                        self.pop.valid.eq(self.push.valid),
                        _assign(self.pop.payload, self.push.payload),
                    ]
                    with m.If(self.pop.ready):
                        m.d.comb += do_flush.eq(1)
            return m

        addr_w = ceil_log2(depth)
        ptr_w = addr_w + int(self.with_extra_msb)

        do_push = Signal(name="doPush")
        do_pop = Signal(name="doPop")
        full = Signal(name="full")
        empty = Signal(name="empty")
        push_ptr = Signal(ptr_w, name="pushPtr")
        pop_ptr = Signal(ptr_w, name="popPtr")
        pop_on_io = Signal(ptr_w, name="popOnIo")
        occupancy = Signal(ceil_log2(depth + 1), name="ptrOccupancy")

        # wentUp = RegNextWhen(doPush, doPush =/= doPop) init(False) clearWhen(flush)
        went_up = Signal(name="wentUp")
        with m.If(do_push != do_pop):
            m.d.sync += went_up.eq(do_push)
        with m.If(self.flush):
            m.d.sync += went_up.eq(0)

        if self.with_extra_msb:
            m.d.comb += [
                full.eq((push_ptr ^ pop_on_io ^ depth) == 0),
                empty.eq(push_ptr == pop_ptr),
            ]
        else:
            m.d.comb += [
                full.eq((push_ptr == pop_on_io) & went_up),
                empty.eq((push_ptr == pop_ptr) & ~went_up),
            ]

        with m.If(do_push):
            m.d.sync += push_ptr.eq(push_ptr + 1)
            if not _is_pow2(depth):
                with m.If(push_ptr == depth - 1):
                    m.d.sync += push_ptr.eq(0)
        with m.If(do_pop):
            m.d.sync += pop_ptr.eq(pop_ptr + 1)
            if not _is_pow2(depth):
                with m.If(pop_ptr == depth - 1):
                    m.d.sync += pop_ptr.eq(0)
        with m.If(self.flush):
            m.d.sync += [push_ptr.eq(0), pop_ptr.eq(0)]

        push_fire = Signal(name="pushFire")
        pop_fire = Signal(name="popFire")
        m.d.comb += [push_fire.eq(fire(self.push)), pop_fire.eq(fire(self.pop))]

        if self.with_extra_msb:
            m.d.comb += occupancy.eq(push_ptr - pop_on_io)
        else:
            counter = Signal(ceil_log2(depth + 1), name="occupancyCounter")
            m.d.sync += counter.eq(counter + push_fire - pop_fire)
            with m.If(self.flush):
                m.d.sync += counter.eq(0)
            m.d.comb += occupancy.eq(counter)

        mem = memory.Memory(shape=self._shape, depth=depth, init=[],
                            attrs=self.mem_attrs or None)
        m.submodules.ram = mem

        # push side
        m.d.comb += [self.push.ready.eq(~full), do_push.eq(push_fire)]
        wp = mem.write_port()
        m.d.comb += [
            wp.en.eq(push_fire),
            wp.addr.eq(push_ptr),
            _assign(wp.data, self.push.payload),
        ]

        # pop side
        address_gen = Stream(unsigned(addr_w))
        m.d.comb += [
            address_gen.valid.eq(~empty),
            address_gen.payload.eq(pop_ptr),
        ]
        addr_fire = Signal(name="addressGenFire")
        m.d.comb += [addr_fire.eq(fire(address_gen)), do_pop.eq(addr_fire)]

        if not self.with_async_read:
            read_arbitration = m2s_pipe(m, address_gen, flush=self.flush,
                                        name="readArbitration")
            rp = mem.read_port(domain="sync")
            m.d.comb += [rp.addr.eq(address_gen.payload), rp.en.eq(addr_fire)]
            m.d.comb += [
                self.pop.valid.eq(read_arbitration.valid),
                read_arbitration.ready.eq(self.pop.ready),
                _assign(self.pop.payload, rp.data),
            ]
            pop_reg = Signal(ptr_w, name="popReg")
            with m.If(fire(read_arbitration)):
                m.d.sync += pop_reg.eq(pop_ptr)
            with m.If(self.flush):
                m.d.sync += pop_reg.eq(0)
            m.d.comb += pop_on_io.eq(pop_reg)
        else:
            rp = mem.read_port(domain="comb")
            m.d.comb += rp.addr.eq(address_gen.payload)
            m.d.comb += [
                self.pop.valid.eq(address_gen.valid),
                address_gen.ready.eq(self.pop.ready),
                _assign(self.pop.payload, rp.data),
            ]
            m.d.comb += pop_on_io.eq(pop_ptr)
            if self.with_bypass:
                with m.If(empty):
                    m.d.comb += [
                        self.pop.valid.eq(self.push.valid),
                        _assign(self.pop.payload, self.push.payload),
                    ]
                    with m.If(self.pop.ready):
                        m.d.comb += do_push.eq(0)

        m.d.comb += [
            self.occupancy.eq(occupancy),
            self.availability.eq(depth - occupancy),
        ]
        return m


def queue(m, source, depth, *, latency=2, flush=None, mem_attrs=None, name=None):
    """``source.queue(size, latency)`` -> the FIFO's pop stream."""
    f = Fifo(payload_shape_of(source), depth, latency=latency, mem_attrs=mem_attrs)
    _attach(m, f, name)
    connect_stream(m, f.push, source)
    if flush is not None:
        m.d.comb += f.flush.eq(flush)
    return f.pop


def queue_with_occupancy(m, source, depth, *, latency=2, flush=None, mem_attrs=None,
                         name=None):
    """``source.queueWithOccupancy(size)`` -> ``(pop, occupancy)``."""
    f = Fifo(payload_shape_of(source), depth, latency=latency, mem_attrs=mem_attrs)
    _attach(m, f, name)
    connect_stream(m, f.push, source)
    if flush is not None:
        m.d.comb += f.flush.eq(flush)
    return f.pop, f.occupancy


# --------------------------------------------------------------------------
# StreamFifoCC
# --------------------------------------------------------------------------


def to_gray(value):
    """``spinal.lib.toGray``: ``x ^ (x >> 1)``."""
    v = _val(value)
    return v ^ (v >> 1)


def from_gray(m, gray, *, name="fromGray"):
    """``spinal.lib.fromGray``: XOR-prefix back to binary."""
    width = len(gray)
    out = Signal(width, name=name)
    m.d.comb += out[width - 1].eq(gray[width - 1])
    for i in reversed(range(width - 1)):
        m.d.comb += out[i].eq(out[i + 1] ^ gray[i])
    return out


class FifoCC(wiring.Component):
    """``spinal.lib.StreamFifoCC`` -- gray-pointer dual-clock FIFO.

    Used by ``HdmiScanout`` (pixel CDC) and by the five
    ``AxiLite4ClockCrossing`` channel FIFOs, so it is ported here even though
    Phase 1.1 only names the single-clock helpers.

    Pop side is the same ``addressGen -> m2sPipe -> sync read`` shape as
    :class:`Fifo`, so ``pop`` has latency 2 and ``popOccupancy`` is derived from
    ``ptrToOccupancy = RegNextWhen(popPtr, readArbitration.fire)``.

    ``with_pop_buffered_reset`` reproduces
    ``popClock.withOptionalBufferedResetFrom(pushClock)``, whose Spinal default
    (``ClockDomain.crossClockBufferPushToPopResetGen``) is **true**: the pop
    domain runs off an async-assert / sync-deassert copy of the *push* reset.
    Set it to ``False`` to keep the parent's pop-domain reset.
    """

    def __init__(self, payload_shape, depth, *, push_domain="sync", pop_domain="sync",
                 with_pop_buffered_reset=True, mem_attrs=None):
        if not (_is_pow2(depth) and depth >= 2):
            raise ValueError("StreamFifoCC depth must be a power of two >= 2")
        self._shape = payload_shape
        self.depth = depth
        self.mem_attrs = dict(mem_attrs) if mem_attrs else {}
        self.push_domain = push_domain
        self.pop_domain = pop_domain
        self.with_pop_buffered_reset = bool(with_pop_buffered_reset)
        self.ptr_width = ceil_log2(depth) + 1
        super().__init__({
            "push": In(stream.Signature(payload_shape)),
            "pop": Out(stream.Signature(payload_shape)),
            "push_occupancy": Out(ceil_log2(depth + 1)),
            "pop_occupancy": Out(ceil_log2(depth + 1)),
        })

    def _is_full(self, a, b):
        w = self.ptr_width
        return (a[w - 2:] == ~b[w - 2:]) & (a[: w - 2] == b[: w - 2])

    def elaborate(self, platform):
        m = Module()
        depth, w = self.depth, self.ptr_width
        addr_w = ceil_log2(depth)

        pop_dom = self.pop_domain
        if self.with_pop_buffered_reset:
            pop_dom = "avoodoo_fifocc_pop"
            m.domains += ClockDomain(pop_dom, local=True, async_reset=True)
            m.d.comb += ClockSignal(pop_dom).eq(ClockSignal(self.pop_domain))
            m.submodules.pop_reset = cdc.ResetSynchronizer(
                ResetSignal(self.push_domain), domain=pop_dom)

        mem = memory.Memory(shape=self._shape, depth=depth, init=[],
                            attrs=self.mem_attrs or None)
        m.submodules.ram = mem

        push_to_pop_gray = Signal(w, name="pushToPopGray")
        pop_to_push_gray = Signal(w, name="popToPushGray")

        # ---- push clock domain ----
        push_ptr = Signal(ceil_log2(2 * depth), name="pushPtr")
        push_ptr_plus = Signal.like(push_ptr, name="pushPtrPlus")
        push_ptr_gray = Signal(w, name="pushPtrGray")
        pop_ptr_gray_cc = Signal(w, name="popPtrGray_cc")
        m.d.comb += push_ptr_plus.eq(push_ptr + 1)

        m.submodules.pop_to_push = cdc.FFSynchronizer(
            pop_to_push_gray, pop_ptr_gray_cc, o_domain=self.push_domain, init=0)

        push_fire = Signal(name="pushFire")
        m.d.comb += [
            self.push.ready.eq(~self._is_full(push_ptr_gray, pop_ptr_gray_cc)),
            push_fire.eq(fire(self.push)),
        ]
        wp = mem.write_port(domain=self.push_domain)
        m.d.comb += [wp.en.eq(push_fire), wp.addr.eq(push_ptr),
                     _assign(wp.data, self.push.payload)]
        with m.If(push_fire):
            m.d[self.push_domain] += [
                push_ptr.eq(push_ptr_plus),
                push_ptr_gray.eq(to_gray(push_ptr_plus)),
            ]
        pop_ptr_bin_in_push = from_gray(m, pop_ptr_gray_cc, name="popPtrBinPush")
        m.d.comb += self.push_occupancy.eq(push_ptr - pop_ptr_bin_in_push)

        # ---- pop clock domain ----
        pop_ptr = Signal(ceil_log2(2 * depth), name="popPtr")
        pop_ptr_plus = Signal.like(pop_ptr, name="popPtrPlus")
        push_ptr_gray_cc = Signal(w, name="pushPtrGray_cc")
        m.d.comb += pop_ptr_plus.eq(pop_ptr + 1)

        m.submodules.push_to_pop = cdc.FFSynchronizer(
            push_to_pop_gray, push_ptr_gray_cc, o_domain=pop_dom, init=0)

        pop_ptr_gray = Signal(w, name="popPtrGray")
        m.d.comb += pop_ptr_gray.eq(to_gray(pop_ptr))

        address_gen = Stream(unsigned(addr_w))
        m.d.comb += [
            address_gen.valid.eq(pop_ptr_gray != push_ptr_gray_cc),
            address_gen.payload.eq(pop_ptr),
        ]
        addr_fire = Signal(name="addressGenFire")
        m.d.comb += addr_fire.eq(fire(address_gen))
        with m.If(addr_fire):
            m.d[pop_dom] += pop_ptr.eq(pop_ptr_plus)

        pipe = M2SPipe(unsigned(addr_w))
        # M2SPipe is written against `sync`; the pop side does not run there.
        m.submodules.read_arbitration = (
            pipe if pop_dom == "sync" else DomainRenamer({"sync": pop_dom})(pipe))
        connect_stream(m, pipe.i, address_gen)
        read_arbitration = pipe.o

        rp = mem.read_port(domain=pop_dom)
        m.d.comb += [rp.addr.eq(address_gen.payload), rp.en.eq(addr_fire)]
        m.d.comb += [
            self.pop.valid.eq(read_arbitration.valid),
            read_arbitration.ready.eq(self.pop.ready),
            _assign(self.pop.payload, rp.data),
        ]

        ptr_to_push = Signal(w, name="ptrToPush")
        ptr_to_occupancy = Signal.like(pop_ptr, name="ptrToOccupancy")
        with m.If(fire(read_arbitration)):
            m.d[pop_dom] += [
                ptr_to_push.eq(pop_ptr_gray),
                ptr_to_occupancy.eq(pop_ptr),
            ]
        push_ptr_bin_in_pop = from_gray(m, push_ptr_gray_cc, name="pushPtrBinPop")
        m.d.comb += self.pop_occupancy.eq(push_ptr_bin_in_pop - ptr_to_occupancy)

        m.d.comb += [
            push_to_pop_gray.eq(push_ptr_gray),
            pop_to_push_gray.eq(ptr_to_push),
        ]
        return m


def queue_cc(m, source, depth, *, push_domain="sync", pop_domain="sync",
             with_pop_buffered_reset=True, mem_attrs=None, name=None):
    """``source.queue(size, pushClock, popClock)``."""
    f = FifoCC(payload_shape_of(source), depth, push_domain=push_domain,
               pop_domain=pop_domain,
               with_pop_buffered_reset=with_pop_buffered_reset,
               mem_attrs=mem_attrs)
    _attach(m, f, name)
    connect_stream(m, f.push, source)
    return f.pop


# --------------------------------------------------------------------------
# findings for the lockstep diff
# --------------------------------------------------------------------------

#: Differences between SpinalHDL's stream/fifo library and Amaranth's that were
#: found while porting. Recorded here (and in the plan's Phase 1 checklist)
#: because they are exactly the places where a "looks equivalent" substitution
#: would break the cycle-exact diff of plan §4.2.
SPINAL_VS_AMARANTH = """
1. amaranth.lib.fifo.SyncFIFOBuffered is NOT a drop-in for StreamFifo.
   - It has no `flush`, which the core drives on FramebufferPlaneBuffer's two
     slot FIFOs (FramebufferPlaneBuffer.scala:110-111).
   - It has no `availability` (PciFifo.scala:279 maps it to io.pciFifoFree).
   - `level` counts what is in the storage plus the output register and updates
     from the same registers Amaranth uses for r_rdy; Spinal's `occupancy` for
     a pow2 depth is `pushPtr - popOnIo` where `popOnIo` is
     RegNextWhen(popPtr, readArbitration.fire), so the two disagree on the
     cycle a pop fires. The core compares occupancy against 0 to build `busy`
     (FramebufferPlaneReader.scala:406) and shifts it into the HUD debug word
     (:377-381), so the disagreement is observable.
   - SyncFIFOBuffered's internal storage is depth-1 deep with a bypass around
     an empty fifo; StreamFifo(latency=2) has no bypass at all and always pays
     two cycles from push to pop valid.
   Conclusion: avoodoo.lib.stream.Fifo is a transliteration, not a wrapper.

2. amaranth.lib.stream.Signature is @final, so Stream cannot be subclassed to
   add Spinal's ~100 helper methods. They are module-level functions here that
   take the Module as their first argument.

3. Spinal's StreamFork(synchronous=True) drives `outputs.valid := i.valid &&
   i.ready`, i.e. output valid depends combinationally on output ready. That is
   illegal in AXI and unusual in Amaranth, but it is what the core relies on in
   six places, so it is reproduced verbatim. Do not "fix" it.

4. Spinal `when` blocks override earlier assignments to the same register;
   several helpers depend on that order (s2mPipe: setWhen(o.ready) after
   clearWhen(i.valid); halfPipe: clearWhen after setWhen; StreamForkArea:
   `when(i.ready){ linkEnable := True }` after the per-output clears; StreamFifo
   flush after the pointer increments). The Amaranth ports keep the same
   statement order for the same reason.

5. amaranth.lib.cdc.FFSynchronizer defaults to 2 stages, matching Spinal's
   BufferCC default, but Spinal's StreamFifoCC also derives the pop domain's
   reset from the push domain by default
   (ClockDomain.crossClockBufferPushToPopResetGen defaults to *true*). FifoCC
   reproduces that with a local async-reset domain plus
   amaranth.lib.cdc.ResetSynchronizer; the Console top must be checked against
   the Spinal Verilog for reset polarity (plan decision 6).
"""
