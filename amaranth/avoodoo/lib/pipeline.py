"""SpinalHDL ``spinal.lib.misc.pipeline`` semantics for Amaranth.

Transliteration of ``spinal/lib/misc/pipeline/{Node,Link,StageLink,DirectLink,
S2MLink,CtrlLink,Builder,Misc,package}.scala`` (SpinalHDL **1.15.0**, read from
``spinalhdl-lib_2.12-1.15.0-sources.jar``).

Who uses it in this repo (grepped 2026-09-20, ``misc.pipeline`` import):

``ext/SpinalVoodoo/src/voodoo/pixel/ColorCombine.scala``
    34 ``Payload``\\ s (35 with ``TRACE``, which is ``null`` unless
    ``c.trace.enabled``), ``val n0..n8 = Node()``, eight ``StageLink(nK, nK+1)``
    at their defaults (``collapseBubble = true``, ``holdPayload = false``),
    ``n0.driveFrom(io.input)``, ``n8.driveTo(io.output)``, ``Builder(links)``.
    No ``CtrlLink``, no halt/throw, no ``insert`` (it writes ``nK(PAYLOAD)``
    directly), no ``up``/``down`` accessors.

``ext/SpinalVoodoo/src/voodoo/math/Fpxx2AFix.scala``
    two ``new Node { ... }`` areas, ``n0.arbitrateFrom(io.op)``,
    ``n1.arbitrateTo(io.result)``, seven ``insert(...)`` calls on ``n0`` read
    as ``n0.x`` from ``n1``, and ``StageLink(n0, n1)`` when ``pipeStages >= 1``
    else ``DirectLink(n0, n1)``, then ``Builder(c01)``.

``frontend/RegisterBank.scala`` and ``bus/BmbBusInterface.scala`` were on the
Phase 1.4 brief as pipeline users; they are **not**. Neither imports
``spinal.lib.misc.pipeline``. What they use is ``spinal.lib.bus.regif``'s
``busif.newCommandRegWithPayload`` (RegisterBank.scala:653/664/675), i.e. the
register-interface command-stream helper, which Phase 2.1/1.6 owns. Those two
files need nothing from this module.

So the ported surface is: :class:`Payload`, :class:`Node` (arbitration,
``insert``, the ``is_*`` status accessors), :class:`StageLink`,
:class:`DirectLink`, :class:`CtrlLink` and :func:`Builder`. :class:`S2MLink` is
ported too -- it is the pipeline-shaped twin of ``avoodoo.lib.stream.s2m_pipe``
and the natural way to buy back a cycle around a ``CtrlLink`` halt -- and is
marked below as "not used by the core". Deliberately **not** ported:
``ForkLink``, ``JoinLink``, ``NodesBuilder``, ``StagePipeline``,
``StageCtrlPipeline``, ``NodeMirror``, ``CtrlLink.forkStream``/``forkFlow``,
sub-keyed payload lanes beyond the plain ``sub_key`` argument, and the
``defaultKey`` deprecation machinery.

Structure produced (this is the part the plan's §4.2 lockstep diff holds us
to)::

    StageLink(up, down), up.ready created, collapse_bubble=True
        down.valid : Reg init 0   `when(up.is_ready) down.valid := up.is_valid`
        down(p)    : Reg NO reset `when(up.is_ready) down(p) := up(p)`  p in matches
        up.ready   := down.ready ; setWhen(!down.is_valid)

which is bit-for-bit ``Stream.m2sPipe(collapsBubble = true)`` -- see
``avoodoo.lib.stream.M2SPipe`` -- replicated once per payload that is *live*
across the link. Liveness is the whole point of the library: ``matches`` is
``down.from_up ∩ up.from_down``, i.e. a payload is registered on a link only
between the node that first touches it and the node that last touches it.
``ColorCombine`` declares 34 payloads over 8 links (272 potential stage
registers, with ``TRACE`` excluded because ``c.trace.enabled`` is off) and
only 150 of those (payload, link) pairs survive; see
``tests/test_pipeline.py::test_color_combine_skeleton_registers``.

The reset asymmetry above is Spinal's and it matters: ``down.valid.setAsReg()
init(False)`` is resettable, ``down(p).setAsReg()`` is a plain ``Reg`` with no
init at all, so the payload registers hold X through reset in the Spinal
Verilog. Payload signals here are therefore ``reset_less = True``; the same
trap is written up in ``avoodoo/lib/fpxx2afix.py``, whose hand-written stage
this module reproduces.

``CtrlLink`` is pure combinational rewiring of one node pair::

    down.valid := up.valid ; up.ready := down.is_ready
    when(halts)        { down.valid := False ; up.ready := False }
    when(duplicates)   { up.ready   := False }
    when(terminates)   { down.valid := False }
    when(ignoresReady) { up.ready   := True  }

plus, through :meth:`Builder`'s upward propagation pass, ``throwWhen``'s
``forgetOne`` reaching the *upstream* ``StageLink``, which is what actually
removes a thrown transaction from a stalled pipeline (see
:data:`SPINAL_PIPELINE_NOTES`).
"""

from __future__ import annotations

import itertools

from amaranth import Module, Signal

# `_assign`/`_val` are this package's own payload-assignment helpers (they cope
# with data.View payloads); `Stream`/`fire` come along for the doctests and for
# callers that build the driving streams.
from avoodoo.lib.stream import Stream, _assign, _val, fire  # noqa: F401

__all__ = [
    "Payload",
    "NamedTypeKey",
    "Node",
    "Link",
    "StageLink",
    "DirectLink",
    "S2MLink",
    "CtrlLink",
    "Builder",
    "PipelineReport",
    "SPINAL_PIPELINE_NOTES",
]


# --------------------------------------------------------------------------
# tiny ordered set -- Spinal uses mutable.LinkedHashSet and the emission order
# of the stage registers follows it, so the order is not incidental.
# --------------------------------------------------------------------------


class _OSet:
    """Insertion-ordered set, the ``LinkedHashSet`` the Scala uses."""

    __slots__ = ("_d",)

    def __init__(self, items=()):
        self._d = dict.fromkeys(items)

    def add(self, item):
        self._d[item] = None

    def update(self, items):
        for item in items:
            self._d[item] = None

    def intersect(self, other):
        """``LinkedHashSet.intersect``: keeps *this* set's order."""
        return [k for k in self._d if k in other]

    def __contains__(self, item):
        return item in self._d

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __repr__(self):
        return f"_OSet({list(self._d)!r})"


def _shape_of(value):
    v = _val(value)
    return v.shape() if hasattr(v, "shape") else len(v)


def _or_reduce(terms):
    """``Seq[Bool].orR``; ``None`` for an empty sequence (Scala's ``Option``)."""
    terms = [t for t in terms if t is not None]
    if not terms:
        return None
    acc = terms[0]
    for t in terms[1:]:
        acc = acc | t
    return acc


# --------------------------------------------------------------------------
# Payload / NamedTypeKey
# --------------------------------------------------------------------------


class Payload:
    """``spinal.lib.misc.pipeline.Payload`` (= ``spinal.core.NamedType``).

    A pure key. It carries only the *shape* of the per-node signal; the signal
    itself is created lazily, once per node that touches the payload, by
    :meth:`Node.__call__`. Identity is object identity, exactly as in Scala
    where a ``Payload`` is a singleton ``val``.
    """

    __slots__ = ("shape", "name", "_id")
    _counter = itertools.count()

    def __init__(self, shape, *, name=None):
        self.shape = shape
        self._id = next(Payload._counter)
        self.name = name if name is not None else f"PAYLOAD_{self._id}"

    def __repr__(self):
        return f"Payload({self.name}, {self.shape!r})"


class NamedTypeKey:
    """``NamedTypeKey(tpe, key)``: a payload, optionally per-lane sub-keyed."""

    __slots__ = ("tpe", "key")

    def __init__(self, tpe, key=None):
        self.tpe = tpe
        self.key = key

    def __eq__(self, other):
        return (isinstance(other, NamedTypeKey)
                and other.tpe is self.tpe and other.key == self.key)

    def __hash__(self):
        return hash((id(self.tpe), self.key))

    def __str__(self):
        return self.tpe.name if self.key is None else f"{self.tpe.name}_{self.key}"

    def __repr__(self):
        return f"NamedTypeKey({self})"


def _as_key(key, default_key, sub_key=None):
    if isinstance(key, NamedTypeKey):
        return key
    return NamedTypeKey(key, default_key if sub_key is None else sub_key)


class _Unset:
    def __repr__(self):
        return "<unset>"


_UNSET = _Unset()


# --------------------------------------------------------------------------
# Node
# --------------------------------------------------------------------------


class Node:
    """``spinal.lib.misc.pipeline.Node``.

    Not an :class:`~amaranth.hdl.Elaboratable`: like the Scala ``Area`` it
    emits into the caller's :class:`Module`, which it is handed at
    construction. ``valid``/``ready``/``cancel`` and the ``is_*`` status
    signals are created *on demand*, because the links branch on whether they
    exist (``StageLink`` emits an unconditional register load when its upstream
    node has no ``ready``, and a ``when(up.is_ready)``-gated one when it does).
    """

    def __init__(self, m, name="node", *, default_key=None):
        self._m = m
        self.name = name
        self.default_key = default_key

        self.key_to_data = {}          # NamedTypeKey -> Signal / data.View
        self.from_up = _OSet()
        self.from_down = _OSet()
        self.up = None                 # Link feeding this node
        self.down = None               # Link fed by this node

        self._ctrl_valid = None
        self._ctrl_ready = None
        self._ctrl_cancel = None
        self._forget_one = None
        self._forget_one_supported = False
        self._status = {}

    # -- ctrl signals (created on demand) ----------------------------------

    @property
    def valid(self):
        if self._ctrl_valid is None:
            self._ctrl_valid = Signal(name=f"{self.name}_valid")
        return self._ctrl_valid

    @property
    def ready(self):
        if self._ctrl_ready is None:
            self._ctrl_ready = Signal(name=f"{self.name}_ready")
        return self._ctrl_ready

    @property
    def cancel(self):
        if self._ctrl_cancel is None:
            self._ctrl_cancel = Signal(name=f"{self.name}_cancel")
        return self._ctrl_cancel

    def _forget_one_create(self, value=_UNSET):
        """``ctrl.forgetOneCreate(value)``.

        Scala's default argument is ``Some(Bool())``: a *fresh undriven* Bool
        that the calling link drives later in its ``build`` (``DirectLink``,
        ``S2MLink``). ``CtrlLink`` instead passes the value, and ``None``
        (Scala's ``Option.empty``) removes the signal.
        """
        if value is _UNSET:
            self._forget_one = Signal(name=f"{self.name}_forgetOne")
        elif value is None:
            self._forget_one = None
        else:
            sig = Signal(name=f"{self.name}_forgetOne")
            self._m.d.comb += sig.eq(value)
            self._forget_one = sig

    # -- status signals (created on demand, driven by _build) --------------

    def _st(self, which):
        sig = self._status.get(which)
        if sig is None:
            sig = Signal(name=f"{self.name}_{which}")
            self._status[which] = sig
        return sig

    @property
    def is_valid(self):
        """``isValid``: read-only accessor of :attr:`valid` (``True`` if none)."""
        return self._st("isValid")

    @property
    def is_ready(self):
        """``isReady``: read-only accessor of :attr:`ready` (``True`` if none)."""
        return self._st("isReady")

    @property
    def is_cancel(self):
        """``isCancel``: read-only accessor of :attr:`cancel` (``False`` if none)."""
        return self._st("isCancel")

    @property
    def is_firing(self):
        """``isFiring``: the transaction is moving forward for real."""
        return self._st("isFiring")

    @property
    def is_moving(self):
        """``isMoving``: last cycle the transaction is present on this node."""
        return self._st("isMoving")

    @property
    def is_canceling(self):
        """``isCanceling`` = ``isValid && isCancel``."""
        return self._st("isCanceling")

    # -- payload access ----------------------------------------------------

    def __call__(self, key, sub_key=None):
        """``node(PAYLOAD)`` / ``node(PAYLOAD, subKey)``.

        Creates the per-node signal the first time it is asked for, and
        records the key in ``key_to_data`` -- which is what
        :func:`Builder`'s propagation passes use to work out where each
        payload is live, so *reading* a payload at a node extends its
        register chain to that node just as writing it does.
        """
        k = _as_key(key, self.default_key, sub_key)
        data = self.key_to_data.get(k)
        if data is None:
            # reset_less: a payload signal is either combinational (at the
            # node that writes it) or a stage register, and Spinal's stage
            # registers are `down(p).setAsReg()` with **no** init -- unlike
            # `down.valid.setAsReg() init(False)`. Amaranth's default zero
            # reset would make the two disagree during and just after reset,
            # which the plan's §4.2 diff would (correctly) call a mismatch.
            data = Signal(k.tpe.shape, name=f"{self.name}_{k}", reset_less=True)
            self.key_to_data[k] = data
        return data

    def insert(self, value, *, name=None):
        """``Node.insert(that)``: a new :class:`Payload` driven from ``value``."""
        p = Payload(_shape_of(value), name=name)
        self._m.d.comb += _assign(self(p), value)
        return p

    # -- stream / flow arbitration ----------------------------------------

    def arbitrate_from(self, source):
        """``arbitrateFrom(Stream)`` / ``(Flow)``.

        ``valid := that.valid``; for a stream also
        ``that.ready := isReady || isCancel`` and ``forgetOneSupported``.
        """
        m = self._m
        m.d.comb += self.valid.eq(source.valid)
        if hasattr(source, "ready"):
            m.d.comb += source.ready.eq(self.is_ready | self.is_cancel)
            self._forget_one_supported = True

    def drive_from(self, source, con=None):
        """``driveFrom(that){ (self, payload) => ... }``."""
        self.arbitrate_from(source)
        if con is not None:
            con(self, source.payload)

    def arbitrate_to(self, sink):
        """``arbitrateTo(Stream)``: ``that.valid := isValid``; ``ready := that.ready``."""
        m = self._m
        m.d.comb += sink.valid.eq(self.is_valid)
        if hasattr(sink, "ready"):
            m.d.comb += self.ready.eq(sink.ready)

    def drive_to(self, sink, con=None):
        """``driveTo(that){ (payload, self) => ... }``."""
        self.arbitrate_to(sink)
        if con is not None:
            con(sink.payload, self)

    # -- build -------------------------------------------------------------

    def _build(self):
        m = self._m
        if not self._forget_one_supported and self._forget_one is not None:
            raise ValueError(f"{self.name} doesn't support ctrl.forgetOne")

        has_cancel = self._ctrl_cancel is not None
        if "isFiring" in self._status:
            m.d.comb += self._status["isFiring"].eq(
                (self.is_valid & self.is_ready & ~self.is_cancel) if has_cancel
                else (self.is_valid & self.is_ready))
        if "isMoving" in self._status:
            m.d.comb += self._status["isMoving"].eq(
                (self.is_valid & (self.is_ready | self.is_cancel)) if has_cancel
                else (self.is_valid & self.is_ready))
        if "isValid" in self._status:
            m.d.comb += self._status["isValid"].eq(
                self._ctrl_valid if self._ctrl_valid is not None else 1)
        if "isReady" in self._status:
            m.d.comb += self._status["isReady"].eq(
                self._ctrl_ready if self._ctrl_ready is not None else 1)
        if "isCancel" in self._status:
            m.d.comb += self._status["isCancel"].eq(
                self._ctrl_cancel if self._ctrl_cancel is not None else 0)
        if "isCanceling" in self._status:
            m.d.comb += self._status["isCanceling"].eq(
                (self.is_valid & self.is_cancel) if has_cancel else 0)

    def __repr__(self):
        return f"<Node {self.name}>"


# --------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------


class Link:
    """``spinal.lib.misc.pipeline.Link``."""

    #: cycles of latency this link adds on the up -> down path.
    latency = 0

    ups = ()
    downs = ()

    @property
    def _m(self):
        return self.ups[0]._m

    def node_setup(self):
        pass

    def propagate_down(self):
        raise NotImplementedError

    def propagate_up(self):
        raise NotImplementedError

    def build(self):
        raise NotImplementedError

    def propagate_down_all(self):
        for up in self.ups:
            for down in self.downs:
                down.from_up.update(up.from_up)
                down.from_up.update(up.key_to_data.keys())

    def propagate_up_all(self):
        for up in self.ups:
            for down in self.downs:
                up.from_down.update(down.from_down)
                up.from_down.update(down.key_to_data.keys())

    def matches(self):
        """``down.fromUp.payload intersect up.fromDown.payload``."""
        return self.downs[0].from_up.intersect(self.ups[0].from_down)

    @staticmethod
    def connect_datas(m, up, down):
        for k in down.from_up.intersect(up.from_down):
            m.d.comb += _assign(down(k), up(k))


class _PairLink(Link):
    def __init__(self, up, down):
        if up._m is not down._m:
            raise ValueError("both nodes of a link must live in the same Module")
        self.up = up
        self.down = down
        self.ups = (up,)
        self.downs = (down,)
        down.up = self
        up.down = self
        #: filled in by build(): the payload keys that got a stage register
        self.registered = []

    def __repr__(self):
        return f"<{type(self).__name__} {self.up.name}->{self.down.name}>"


class StageLink(_PairLink):
    """``StageLink(up, down)``: registers on ``valid``/payloads, arbitrated ready.

    ``collapse_bubble`` (Spinal's ``collapseBubble``, default ``True``, cleared
    by ``withoutCollapse()``) adds ``up.ready setWhen(!down.is_valid)`` so a
    hole in the pipeline is filled in the same cycle -- the
    ``Stream.m2sPipe(collapsBubble = true)`` behaviour.

    ``hold_payload`` (``withPayloadHold()``) loads the payload register on
    ``up.is_valid && up.is_ready`` instead of ``up.is_ready``, keeping the last
    payload stable while a bubble passes.
    """

    latency = 1

    def __init__(self, up, down, *, collapse_bubble=True, hold_payload=False):
        super().__init__(up, down)
        self.collapse_bubble = bool(collapse_bubble)
        self.hold_payload = bool(hold_payload)

    def without_collapse(self):
        self.collapse_bubble = False
        return self

    def with_payload_hold(self):
        self.hold_payload = True
        return self

    def propagate_down(self):
        self.propagate_down_all()
        if self.up._ctrl_valid is not None:
            self.down.valid
        self.down._forget_one_supported = True

    def propagate_up(self):
        self.propagate_up_all()
        if self.down._ctrl_ready is not None:
            self.up.ready

    def build(self):
        m, up, down = self._m, self.up, self.down
        matches = self.matches()
        self.registered = list(matches)

        if up._ctrl_ready is None:
            # No back-pressure at all: the registers load every cycle.
            if down._ctrl_valid is not None:
                m.d.sync += down.valid.eq(up.is_valid)
            for k in matches:
                m.d.sync += _assign(down(k), up(k))
        else:
            # Statement order is Spinal's: the forgetOne clear is emitted
            # *before* the `when(up.isReady)` load, so a reload wins over it.
            if down._forget_one is not None and down._ctrl_valid is not None:
                with m.If(down._forget_one):
                    m.d.sync += down.valid.eq(0)
            if down._ctrl_valid is not None:
                with m.If(up.is_ready):
                    m.d.sync += down.valid.eq(up.is_valid)
            load = (up.is_valid & up.is_ready) if self.hold_payload else up.is_ready
            with m.If(load):
                for k in matches:
                    m.d.sync += _assign(down(k), up(k))

        if up._ctrl_ready is not None:
            m.d.comb += up.ready.eq(down.ready)
            if self.collapse_bubble:
                with m.If(~down.is_valid):
                    m.d.comb += up.ready.eq(1)


class DirectLink(_PairLink):
    """``DirectLink(up, down)``: wires only, zero latency.

    ``Fpxx2AFix(pipeStages = 0)`` builds this instead of a :class:`StageLink`.
    """

    latency = 0

    def propagate_down(self):
        self.propagate_down_all()
        if self.up._ctrl_valid is not None:
            self.down.valid
        self.up._forget_one_supported = True

    def propagate_up(self):
        self.propagate_up_all()
        if self.down._forget_one is not None:
            self.up._forget_one_create()
        if self.down._ctrl_cancel is not None:
            self.up.cancel
        if self.down._ctrl_ready is not None:
            self.up.ready

    def build(self):
        m, up, down = self._m, self.up, self.down
        if down._ctrl_valid is not None:
            m.d.comb += down.valid.eq(up.is_valid)
        if up._ctrl_ready is not None:
            m.d.comb += up.ready.eq(down.ready)
        if up._forget_one is not None:
            m.d.comb += up._forget_one.eq(down._forget_one)
        if up._ctrl_cancel is not None:
            m.d.comb += up.cancel.eq(down.cancel)
        self.registered = []
        self.connect_datas(m, up, down)


class S2MLink(_PairLink):
    """``S2MLink(up, down)``: cuts the ``ready`` path, zero latency.

    **Not used by the SpinalVoodoo core** -- ported because it is the
    pipeline-shaped twin of ``avoodoo.lib.stream.s2m_pipe`` (same skid buffer,
    same ``rValid``/``rData`` names) and the usual way to break the
    combinational ``halt -> up.ready`` path a :class:`CtrlLink` creates.
    """

    latency = 0

    def propagate_down(self):
        self.propagate_down_all()
        if self.up._ctrl_valid is not None:
            self.down.valid
        self.down._forget_one_supported = True

    def propagate_up(self):
        self.propagate_up_all()
        if self.down._forget_one is not None:
            self.up._forget_one_create()
        if self.down._ctrl_cancel is not None:
            self.up.cancel
        self.up.ready

    def build(self):
        m, up, down = self._m, self.up, self.down
        matches = self.matches()
        self.registered = list(matches)

        r_valid = Signal(name=f"{self.up.name}_s2m_rValid")
        with m.If(up.is_valid):
            m.d.sync += r_valid.eq(1)
        with m.If(down.ready):
            m.d.sync += r_valid.eq(0)

        r_data = {}
        for k in matches:
            # RegNextWhen with no init -> reset-less, like the stage registers.
            buf = Signal(k.tpe.shape, name=f"{up.name}_{k}_s2mBuffer",
                         reset_less=True)
            r_data[k] = buf
            with m.If(up.ready):
                m.d.sync += _assign(buf, up(k))

        m.d.comb += up.ready.eq(~r_valid)
        m.d.comb += down.valid.eq(up.is_valid | r_valid)
        with m.If(r_valid):
            for k in matches:
                m.d.comb += _assign(down(k), r_data[k])
        with m.Else():
            for k in matches:
                m.d.comb += _assign(down(k), up(k))

        if down._forget_one is not None:
            with m.If(down._forget_one):
                m.d.sync += r_valid.eq(0)
            m.d.comb += up._forget_one.eq(down._forget_one & ~r_valid)
        if down._ctrl_cancel is not None:
            m.d.comb += up.cancel.eq(down.cancel & ~r_valid)


class CtrlLink(_PairLink):
    """``CtrlLink(up, down)``: flow-control and bypass between two nodes.

    Zero latency and zero registers of its own; everything it does is
    combinational overrides of ``down.valid`` / ``up.ready`` plus per-payload
    bypasses. What makes ``throw_when`` more than ``terminate_when`` is the
    ``forgetOne``/``cancel`` it pushes *upstream* during
    :func:`Builder`'s propagation pass.
    """

    latency = 0

    def __init__(self, up=None, down=None, *, m=None, name="ctrl"):
        if up is None or down is None:
            if m is None:
                raise TypeError("CtrlLink() without nodes needs m=<Module>")
            up = Node(m, f"{name}_up") if up is None else up
            down = Node(m, f"{name}_down") if down is None else down
        super().__init__(up, down)
        self.name = name
        self.bypasses = {}
        self.halts = []
        self.duplicates = []
        self.terminates = []
        self.ignores_ready = []
        self.forgets_one = []
        self.cancels = []

    # -- CtrlApi -----------------------------------------------------------

    @property
    def impact_valid(self):
        return bool(self.halts) or bool(self.terminates)

    @property
    def impact_ready(self):
        return bool(self.halts) or bool(self.duplicates)

    def __call__(self, key, sub_key=None):
        """``ctrl(PAYLOAD)`` is ``ctrl.down(PAYLOAD)``."""
        return self.down(key, sub_key)

    def insert(self, value, *, name=None):
        return self.down.insert(value, name=name)

    def _request(self, cond, prefix):
        sig = Signal(name=f"{self.name}_{prefix}")
        self._m.d.comb += sig.eq(cond)
        return sig

    def halt_when(self, cond):
        """Block the transaction: clears ``up.ready`` *and* ``down.valid``."""
        sig = self._request(cond, "haltRequest")
        self.halts.append(sig)
        return sig

    def duplicate_when(self, cond):
        """Replay the transaction downstream: clears ``up.ready`` only."""
        sig = self._request(cond, "duplicateRequest")
        self.duplicates.append(sig)
        return sig

    def terminate_when(self, cond):
        """Hide the transaction from downstream: clears ``down.valid`` only."""
        sig = self._request(cond, "terminateRequest")
        self.terminates.append(sig)
        return sig

    def forget_one_when(self, cond):
        """Ask the upstream to drop its current transaction."""
        sig = self._request(cond, "forgetsSingleRequest")
        self.forgets_one.append(sig)
        return sig

    def ignore_ready_when(self, cond):
        """Force ``up.ready`` high regardless of downstream."""
        sig = self._request(cond, "ignoreReadyRequest")
        self.ignores_ready.append(sig)
        return sig

    def throw_when(self, cond, *, using_ready=False):
        """Remove the transaction from the pipeline entirely.

        ``terminate`` + ``cancel`` + (``forgetOne`` | ``ignoreReady``). The
        ``forgetOne`` is the load-bearing half: it reaches the upstream
        ``StageLink``, whose valid register is then cleared even while the
        pipeline is stalled, so the thrown transaction does not come back when
        the stall lifts.
        """
        flag = self._request(cond, "throwWhen")
        self.terminates.append(flag)
        self.cancels.append(flag)
        if using_ready:
            self.ignores_ready.append(flag)
        else:
            self.forgets_one.append(flag)
        return flag

    def bypass(self, key, sub_key=None, *, preserve=True):
        """Override one payload between ``up`` and ``down``.

        Returns the overriding signal, defaulted to ``up(key)``; assign it
        again inside an ``m.If`` to inject a value (the CPU data-hazard
        pattern Spinal documents).
        """
        k = _as_key(key, self.up.default_key, sub_key)
        ret = self.bypasses.get(k)
        if ret is None:
            m = self._m
            ret = Signal(k.tpe.shape, name=f"{self.name}_{k}_bypass")
            self.bypasses[k] = ret
            m.d.comb += _assign(self.down(k), ret)
            if preserve:
                m.d.comb += _assign(ret, self.up(k))
        return ret

    # -- Link --------------------------------------------------------------

    def propagate_down(self):
        self.propagate_down_all()
        if self.up._ctrl_valid is not None or self.impact_valid:
            self.down.valid
        self.down._forget_one_supported = True

    def propagate_up(self):
        self.propagate_up_all()
        self.up._forget_one_create(
            _or_reduce([self.down._forget_one, *self.forgets_one]))
        cancel = _or_reduce([self.down._ctrl_cancel, *self.cancels])
        if cancel is not None:
            self._m.d.comb += self.up.cancel.eq(cancel)
        if self.down._ctrl_ready is not None or self.impact_ready:
            self.up.ready

    def build(self):
        m, up, down = self._m, self.up, self.down
        if down._ctrl_valid is not None:
            m.d.comb += down.valid.eq(up.valid)
        if up._ctrl_ready is not None:
            m.d.comb += up.ready.eq(down.is_ready)
        if self.halts:
            with m.If(_or_reduce(self.halts)):
                m.d.comb += [down.valid.eq(0), up.ready.eq(0)]
        if self.duplicates:
            with m.If(_or_reduce(self.duplicates)):
                m.d.comb += up.ready.eq(0)
        if self.terminates:
            with m.If(_or_reduce(self.terminates)):
                m.d.comb += down.valid.eq(0)
        if up._ctrl_ready is not None and self.ignores_ready:
            with m.If(_or_reduce(self.ignores_ready)):
                m.d.comb += up.ready.eq(1)

        self.registered = []
        for k in self.matches():
            if k not in self.bypasses:
                m.d.comb += _assign(down(k), up(k))


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------


class PipelineReport:
    """What :func:`Builder` produced, for tests and for the plan's §4.2 diff.

    ``latency`` is the sum of the links' latencies, which is the
    input-to-output latency for the single-path pipelines this repo builds
    (``ColorCombine``: 8; ``Fpxx2AFix``: 1 or 0).
    """

    def __init__(self, links, nodes):
        self.links = list(links)
        self.nodes = list(nodes)

    @property
    def latency(self):
        return sum(link.latency for link in self.links)

    @property
    def valid_registers(self):
        """Nodes whose ``valid`` is a stage register (one per StageLink)."""
        return [link.down for link in self.links
                if link.latency and link.down._ctrl_valid is not None]

    @property
    def valid_register_count(self):
        return len(self.valid_registers)

    @property
    def payload_registers(self):
        """``{link: [NamedTypeKey, ...]}`` -- the surviving stage registers."""
        return {link: list(link.registered) for link in self.links
                if link.latency and link.registered}

    @property
    def payload_register_slots(self):
        """Number of (link, payload) pairs that became a register."""
        return sum(len(link.registered) for link in self.links if link.latency)

    def live_range(self, payload):
        """The links on which ``payload`` is registered, in build order."""
        return [link for link in self.links
                if any(k.tpe is payload for k in link.registered)]

    def __repr__(self):
        return (f"<PipelineReport latency={self.latency} "
                f"valid_regs={self.valid_register_count} "
                f"payload_regs={self.payload_register_slots}>")


def Builder(*links):
    """``spinal.lib.misc.pipeline.Builder``: resolve and emit the pipeline.

    Accepts links or iterables of links, matching both Scala overloads
    (``Builder(c01)`` and ``Builder(links)``). Must be called *after* all the
    per-node logic, because the propagation passes read ``key_to_data``, which
    only fills in as the user touches payloads.

    Order is the Scala's: ``nodeSetup``, then the upward pass (tail to head --
    this is where ``CtrlLink`` hands ``forgetOne``/``cancel`` to its upstream
    node, before any ``StageLink`` looks at them), then the downward pass
    (head to tail), then every link's ``build``, then every node's.
    """
    flat = []
    for item in links:
        if isinstance(item, Link):
            flat.append(item)
        else:
            flat.extend(item)
    if not flat:
        raise ValueError("Builder() needs at least one link")

    def _pass(direction):
        done = set()
        if direction == "up":
            seeds = [c for c in flat if not c.downs or all(d.down is None for d in c.downs)]
        else:
            seeds = [c for c in flat if not c.ups or all(u.up is None for u in c.ups)]
        solved = []
        while seeds:
            batch, seeds = seeds, []
            for e in batch:
                if id(e) in done:
                    # Not in the Scala; a cheap guard against re-running a
                    # propagation (which would re-create forgetOne signals) on
                    # topologies with reconvergence. Linear chains never hit it.
                    continue
                done.add(id(e))
                if direction == "up":
                    e.propagate_up()
                    for d in e.ups:
                        solved.append(d)
                        if d.up is not None and all(
                                any(u is s for s in solved) or u.down is None
                                for u in d.up.downs):
                            seeds.append(d.up)
                else:
                    e.propagate_down()
                    for d in e.downs:
                        solved.append(d)
                        if d.down is not None and all(
                                any(u is s for s in solved) or u.up is None
                                for u in d.down.ups):
                            seeds.append(d.down)
        missed = [e for e in flat if id(e) not in done]
        if missed:
            raise ValueError(f"Builder could not reach {missed} in the {direction} pass; "
                             "the links do not form a connected pipeline")

    for c in flat:
        c.node_setup()
    _pass("up")
    _pass("down")
    for c in flat:
        c.build()

    nodes = []
    for c in flat:
        for n in (*c.ups, *c.downs):
            if not any(n is other for other in nodes):
                nodes.append(n)
    for n in nodes:
        n._build()

    return PipelineReport(flat, nodes)


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

#: Behaviour of the Scala that is easy to get wrong and that the tests pin.
SPINAL_PIPELINE_NOTES = """
1. A StageLink is exactly Stream.m2sPipe(collapsBubble = true) replicated per
   live payload, *provided* the upstream node has a `ready` (i.e. something
   downstream applies back-pressure). If it does not, StageLink.build takes its
   other branch and emits an unconditional register load with no enable -- a
   Flow pipeline, not a Stream one. Which branch you get is decided by whether
   anything ever touched `node.ready`, i.e. by the presence of a `ready`
   *anywhere* downstream, so adding an arbitrateTo at the tail silently
   rewrites every register in the chain. Both branches are ported.

2. Payload registers only exist between a payload's first and last touched
   node: `matches = down.fromUp ∩ up.fromDown`, where `fromUp` accumulates
   `keyToData` forward and `fromDown` accumulates it backward. Reading
   `nK(PAYLOAD)` counts as a touch, so a stray read at the end of the pipeline
   costs a register on every intervening link. In ColorCombine this is the
   difference between 264 and 150 payload stage registers.

3. throwWhen is terminate + cancel + forgetOne, and forgetOne is the half that
   matters. terminate alone only masks down.valid for as long as the condition
   holds; forgetOne propagates up through CtrlLink.propagateUp into the
   *upstream* StageLink, which then emits `down.valid clearWhen(forgetOne)`
   before its `when(up.isReady) down.valid := up.isValid`. So: if the pipeline
   is moving, the thrown transaction is simply overwritten; if it is stalled,
   its valid register is cleared and the slot opens up. Without the forgetOne
   (i.e. with a bare terminateWhen) a stalled thrown transaction re-appears
   downstream the moment the condition deasserts.

4. forgetOne does *not* propagate further up than the first StageLink, and
   `cancel` does not propagate up through a StageLink at all (StageLink
   .propagateUp only touches `ready`). cancel therefore only affects the
   isFiring/isMoving of the CtrlLink's own up node. A throw at ctrl K removes
   the transaction sitting at K.up and nothing else -- it is not a flush.

5. haltWhen clears both down.valid and up.ready, so it is a combinational path
   from the halt condition all the way back to the pipeline's input `ready`
   (through every collapse-bubble `up.ready setWhen(!down.isValid)`). Nothing
   in the library breaks that path; S2MLink is the tool if it becomes the
   critical path (plan Phase 8 timing).

6. duplicateWhen clears up.ready only, so the same transaction is presented to
   the downstream node again next cycle -- it multiplies transactions rather
   than stalling. ignoreReadyWhen is the mirror image (up.ready forced high
   while down is stalled), and drops one.

7. The CtrlLink overrides are order-sensitive, exactly like the `when` chains
   in Stream.scala: halt, then duplicate, then terminate, then ignoreReady --
   so ignoreReady beats halt on up.ready, while halt still wins on down.valid.
   The port keeps the statement order.

8. Could not be determined from the source alone: whether Spinal's
   `ContextSwapper.outsideCondScopeData` placement of the lazily-created
   valid/ready/status signals can change emitted Verilog in ways a
   statement-order-faithful Amaranth port cannot reproduce (it hoists the
   signal *declaration*, not the assignments, so it should not -- but only the
   Phase 4 lockstep diff of ColorCombine will prove it).
"""
