"""``PciFifo`` -- the 64-entry PCI write FIFO between the host and the register bank.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/bus/PciFifo.scala`` (654 lines,
SpinalHDL 1.15.0), instantiated by ``Core.scala:102`` as::

    PciFifo(
      busParams        = RegisterBank.bmbParams(c),            # 12-bit addr, 32-bit data
      categories       = regBank.busif.getCategories,          # 158 entries
      floatAliases     = regBank.busif.getFloatAliases,        # 30 entries
      commandAddresses = regBank.busif.getCommandStreamReady.keys.toSeq.sorted  # 5
    )

and spliced between ``AddressRemapper.io.output`` and ``RegisterBank.io.bus``.
The three tables come from :mod:`avoodoo.frontend.regdef`
(:func:`~avoodoo.frontend.regdef.categories`,
:func:`~avoodoo.frontend.regdef.float_aliases`,
:func:`~avoodoo.frontend.regdef.command_addresses`), which
``tests/test_regdef.py`` pins against the elaborated ground truth and
``tests/test_pci_fifo.py`` re-pins against the decode constants in the
generated ``PciFifo_spinal.v`` when that file is around.

What it does, in the Scala's own order (the ``elaborate`` below is written
area by area against it):

``decode``
    Address categorisation, purely combinational on ``cpuSide.cmd.address``:
    ``fifoBypass`` (default **true** -- an address nobody registered goes
    straight through), ``syncRequired``, and the float-alias triple
    ``isFloatAlias`` / ``floatTargetAddr`` / ``floatShiftAmount = 30 - fracBits``.
    The Scala builds these in an ``addPrePopTask``; that is an elaboration
    ordering device, not behaviour, so here they are just emitted first.
``ingress``
    A 16-entry ``StreamFifo`` in front of the float converter.  CPU writes
    that are not ``fifoBypass`` win over texture writes for the single push
    port; a texture write is only accepted while no queueable CPU write is
    presenting.
``floatConvert``
    One ``Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1)`` plus an
    ``inFlight`` flag and two **reset-less** registers (``targetAddr``,
    ``shift``) captured on ``op.fire``.
``queuePush``
    The mux onto the 64-entry FIFO: a finished conversion (with the
    ``floatCorrected`` shift-and-round and the 50-bit shadow copy of the raw
    result) beats a plain staged write.  The staged entry is popped on the
    same ``queuedWriteTx.fire`` in either case.
``pciQueue``
    ``StreamFifo(QueuedWrite(), depth = 64)``; ``io.pciFifoFree`` is its
    ``availability`` (7 bits, so 64 is representable).
``drainControl``
    ``issuedWrite = pciFifo.pop.haltWhen(!canDrain).m2sPipe()`` -- one
    registered issue stage after the FIFO.  ``canDrain`` drops for a
    ``syncRequired`` entry while ``pipelineBusy``, and for a command-stream
    address whose ``commandReady`` bit is low.  ``fifoEmpty`` covers both the
    FIFO and the issue register.
``drainRoute`` / ``regSideMux``
    Texture entries leave on ``texDrain``; register entries are muxed onto
    ``regSide.cmd`` *behind* any CPU passthrough (a read, or a ``fifoBypass``
    write), which always has priority.  ``floatShadow`` and ``syncDrained``
    pulse on the drain beat.
``response``
    A FIFO'd or direct write gets its ``SUCCESS`` response one cycle after
    ``cmd.fire``; a read sets ``pendingRead`` and forwards the ``regSide``
    response instead.  The drain's own responses are consumed (``rsp.ready``
    tied high).
``cpuReady``
    ``cmd.ready`` is ``stageFifo.push.ready`` for a queueable write, and is
    dropped for a read while one is pending or while ``regSide.cmd.ready`` is
    low.

Reset discipline (plan §6): Spinal ``Reg``/``RegNext`` without ``init`` have
no reset, so ``floatConvert.targetAddr``, ``floatConvert.shift``, the
``m2sPipe`` payload register inside ``issuedWrite`` and the ``RegNext`` of
``cmd.source`` are ``reset_less=True`` here.  ``inFlight``, ``pendingRead``,
the response ``RegNext(...) init(False)`` and every FIFO pointer do reset.

The ``GenerationFlags.formal`` block (assertions on ``fifoEmpty``, the drain
routing, the passthrough/drain mux, ``pendingRead`` forwarding, the
float-correction datapath, and the ``formalStrong`` occupancy / ordering
trackers) is dropped from the RTL per plan §2 and re-expressed as per-cycle
checks in ``tests/test_pci_fifo.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Mapping, Sequence

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.fpxx import FpxxConfig
from avoodoo.lib.fpxx2afix import Fpxx2AFix
from avoodoo.lib.membus import CMD_READ, CMD_WRITE, RSP_SUCCESS, MemBusParameter, MemBusSignature
from avoodoo.lib.stream import Fifo, FlowSignature, Stream, connect_stream, fire, halt_when, m2s_pipe

__all__ = [
    "TEX_PCI_ADDR_WIDTH",
    "FLOAT_SHADOW_RAW_WIDTH",
    "FLOAT_CONVERT_INT_BITS",
    "FLOAT_CONVERT_FRAC_BITS",
    "STAGE_FIFO_DEPTH",
    "PCI_FIFO_DEPTH",
    "TexWritePayload",
    "FloatShadowWrite",
    "FloatAliasConfig",
    "StagedWrite",
    "QueuedWrite",
    "PciFifo",
]

#: ``TexWritePayload.pciAddr`` -- 23-bit PCI offset within texture space.
TEX_PCI_ADDR_WIDTH = 23
#: ``FloatShadowWrite.raw`` / ``QueuedWrite.floatShadowRaw`` -- ``SInt(50 bits)``.
FLOAT_SHADOW_RAW_WIDTH = 50
#: ``Fpxx2AFix(20 bits, 30 bits, ...)`` (PciFifo.scala:200).
FLOAT_CONVERT_INT_BITS = 20
FLOAT_CONVERT_FRAC_BITS = 30
#: ``StreamFifo(StagedWrite(), depth = 16)`` (PciFifo.scala:183).
STAGE_FIFO_DEPTH = 16
#: ``StreamFifo(QueuedWrite(), depth = 64)`` (PciFifo.scala:276).
PCI_FIFO_DEPTH = 64


# ---------------------------------------------------------------------------
# bundles
# ---------------------------------------------------------------------------


class TexWritePayload(data.StructLayout):
    """``case class TexWritePayload()`` -- the texture write on both sides.

    Field order is the Scala declaration order (LSB first).
    """

    def __init__(self):
        super().__init__({
            "pci_addr": unsigned(TEX_PCI_ADDR_WIDTH),
            "data": unsigned(32),
            "mask": unsigned(4),
        })

    def __eq__(self, other):
        return type(other) is TexWritePayload

    def __hash__(self):
        return hash(TexWritePayload)

    def __repr__(self):
        return "TexWritePayload()"


class FloatShadowWrite(data.StructLayout):
    """``case class FloatShadowWrite(busAddrWidth)`` -- the ``floatShadow`` Flow."""

    def __init__(self, bus_addr_width: int):
        self.bus_addr_width = bus_addr_width
        super().__init__({
            "address": unsigned(bus_addr_width),
            "raw": signed(FLOAT_SHADOW_RAW_WIDTH),
        })

    def __eq__(self, other):
        return type(other) is FloatShadowWrite and other.bus_addr_width == self.bus_addr_width

    def __hash__(self):
        return hash((FloatShadowWrite, self.bus_addr_width))

    def __repr__(self):
        return f"FloatShadowWrite({self.bus_addr_width})"


@dataclass(frozen=True)
class FloatAliasConfig:
    """``PciFifo.FloatAliasConfig(targetAddr, intBits, fracBits)``.

    :func:`~avoodoo.frontend.regdef.float_aliases` hands over
    :class:`~avoodoo.frontend.regdef.FloatAlias` objects, which carry the same
    three attributes; :class:`PciFifo` reads them by name, so either works.
    """

    target_addr: int
    int_bits: int
    frac_bits: int


class StagedWrite(data.StructLayout):
    """``private case class StagedWrite()`` -- the ingress FIFO entry."""

    def __init__(self, bus_addr_width: int, bus_data_width: int):
        super().__init__({
            "address": unsigned(bus_addr_width),
            "data": unsigned(bus_data_width),
            "mask": unsigned(bus_data_width // 8),
            "sync_required": unsigned(1),
            "is_texture": unsigned(1),
            "tex_pci_addr": unsigned(TEX_PCI_ADDR_WIDTH),
            "is_float_alias": unsigned(1),
            "float_target_addr": unsigned(bus_addr_width),
            "float_shift_amount": unsigned(5),
        })


class QueuedWrite(data.StructLayout):
    """``private case class QueuedWrite()`` -- the 64-entry FIFO entry."""

    def __init__(self, bus_addr_width: int, bus_data_width: int):
        super().__init__({
            "address": unsigned(bus_addr_width),
            "data": unsigned(bus_data_width),
            "mask": unsigned(bus_data_width // 8),
            "sync_required": unsigned(1),
            "is_texture": unsigned(1),
            "tex_pci_addr": unsigned(TEX_PCI_ADDR_WIDTH),
            "has_float_shadow": unsigned(1),
            "float_shadow_raw": signed(FLOAT_SHADOW_RAW_WIDTH),
        })


# ---------------------------------------------------------------------------
# the component
# ---------------------------------------------------------------------------


class PciFifo(wiring.Component):
    """``case class PciFifo(busParams, categories, floatAliases, commandAddresses)``.

    Parameters
    ----------
    bus_params
        ``BmbParameter`` of both buses (``RegisterBank.bmbParams``: 12-bit
        address, 32-bit data, 4-bit source, no context).
    categories
        ``addr -> RegisterCategory``; anything with ``fifo_bypass`` and
        ``sync_required`` attributes.  Addresses **absent** from the map are
        ``fifoBypass`` (PciFifo.scala:456).
    float_aliases
        ``float addr -> FloatAliasConfig`` (or ``regdef.FloatAlias``).
    command_addresses
        The addresses with command-stream drain blocking, in
        ``io.commandReady`` index order.

    Ports (Spinal ``io`` names in brackets)
    ---------------------------------------
    cpu_side : In(MemBusSignature)          ``slave(Bmb)`` [cpuSide]
    reg_side : Out(MemBusSignature)         ``master(Bmb)`` [regSide]
    tex_write : In(stream(TexWritePayload)) [texWrite]
    tex_drain : Out(stream(TexWritePayload)) [texDrain]
    pipeline_busy : In(1)                   [pipelineBusy]
    command_ready : In(1).array(n)          [commandReady], absent when n == 0
    fifo_empty : Out(1)                     [fifoEmpty]
    pci_fifo_free : Out(7)                  [pciFifoFree]
    was_enqueued : Out(1)                   [wasEnqueued]
    was_enqueued_addr : In(addr)            [wasEnqueuedAddr]
    sync_drained : Out(1)                   [syncDrained]
    float_shadow : Out(Flow(FloatShadowWrite)) [floatShadow]
    """

    def __init__(
        self,
        bus_params: MemBusParameter,
        categories: Mapping[int, object],
        float_aliases: Mapping[int, object],
        command_addresses: Sequence[int],
        formal_strong: bool = True,
    ):
        self.bus_params = bus_params
        self.categories = dict(categories)
        self.float_aliases = dict(float_aliases)
        self.command_addresses = tuple(int(a) for a in command_addresses)
        #: Accepted for signature parity with the Scala; the formal block it
        #: gates is not emitted into the RTL (see the module docstring).
        self.formal_strong = formal_strong

        self.bus_addr_width = bus_params.access.address_width
        self.bus_data_width = bus_params.access.data_width
        if not bus_params.access.can_write or not bus_params.access.can_mask:
            raise ValueError("PciFifo needs a writable, maskable bus")
        for addr in self.categories:
            if not 0 <= addr < (1 << self.bus_addr_width):
                raise ValueError(f"category address {addr:#x} does not fit the bus")
        for addr, cfg in self.float_aliases.items():
            if not 0 <= addr < (1 << self.bus_addr_width):
                raise ValueError(f"float alias address {addr:#x} does not fit the bus")
            if not 0 <= FLOAT_CONVERT_FRAC_BITS - cfg.frac_bits < 32:
                raise ValueError(f"float alias {addr:#x}: fracBits {cfg.frac_bits} out of range")

        ports = {
            "cpu_side": In(MemBusSignature(bus_params)),
            "reg_side": Out(MemBusSignature(bus_params)),
            "tex_write": In(stream.Signature(TexWritePayload())),
            "tex_drain": Out(stream.Signature(TexWritePayload())),
            "pipeline_busy": In(1),
            "fifo_empty": Out(1),
            "pci_fifo_free": Out(7),
            "was_enqueued": Out(1),
            "was_enqueued_addr": In(self.bus_addr_width),
            "sync_drained": Out(1),
            "float_shadow": Out(FlowSignature(FloatShadowWrite(self.bus_addr_width))),
        }
        # `in Vec(Bool(), commandAddresses.size)`: Spinal emits no port at all
        # for an empty Vec, so neither do we.
        if self.command_addresses:
            ports["command_ready"] = In(1).array(len(self.command_addresses))
        super().__init__(ports)

    # -- helpers ------------------------------------------------------------

    @property
    def fifo_queue_addresses(self) -> tuple[int, ...]:
        """``categories.collect { case (addr, cat) if !cat.fifoBypass => addr }``."""
        return tuple(a for a, c in self.categories.items() if not c.fifo_bypass)

    @property
    def sync_addresses(self) -> tuple[int, ...]:
        """``categories.collect { case (addr, cat) if cat.syncRequired => addr }``."""
        return tuple(a for a, c in self.categories.items() if c.sync_required)

    @staticmethod
    def float_corrected(m: Module, raw_sint, shift, *, name: str = "floatCorrected"):
        """``floatCorrected(rawSInt, shift)`` (PciFifo.scala:152).

        ``rawSInt >> shift`` (arithmetic), plus one when the value is negative
        and any of the bits shifted out were set -- but only for the three
        shift amounts the register set actually uses (26, 18, 12); any other
        shift truncates toward minus infinity.  The width of the sum is the
        width of ``rawSInt``, as Spinal's ``SInt + Int`` gives.
        """
        width = len(raw_sint)
        shifted = Signal(signed(width), name=f"{name}_shifted")
        m.d.comb += shifted.eq(raw_sint >> shift)
        has_remainder = Signal(name=f"{name}_hasRemainder")
        m.d.comb += has_remainder.eq(0)
        with m.Switch(shift):
            with m.Case(26):
                m.d.comb += has_remainder.eq(raw_sint[:26].any())
            with m.Case(18):
                m.d.comb += has_remainder.eq(raw_sint[:18].any())
            with m.Case(12):
                m.d.comb += has_remainder.eq(raw_sint[:12].any())
        corrected = Signal(signed(width), name=name)
        m.d.comb += corrected.eq(
            Mux(raw_sint[width - 1] & has_remainder, (shifted + 1)[:width], shifted))
        return corrected

    # -- elaborate ----------------------------------------------------------

    def elaborate(self, platform):
        m = Module()
        aw = self.bus_addr_width
        dw = self.bus_data_width

        cpu_cmd = self.cpu_side.cmd
        cpu_rsp = self.cpu_side.rsp
        reg_cmd = self.reg_side.cmd
        reg_rsp = self.reg_side.rsp
        cpu_frag = cpu_cmd.payload.fragment
        reg_frag = reg_cmd.payload.fragment

        # ==================================================================
        # Address categorization (the Scala's addPrePopTask, pure comb)
        # ==================================================================
        fifo_bypass = Signal(name="pciFifo_fifoBypass")
        sync_required = Signal(name="pciFifo_syncRequired")
        is_float_alias = Signal(name="decodeInfo_isFloatAlias")
        float_target_addr = Signal(aw, name="decodeInfo_floatTargetAddr")
        float_shift_amount = Signal(5, name="decodeInfo_floatShiftAmount")

        # Default: unregistered addresses bypass FIFO
        m.d.comb += fifo_bypass.eq(1)
        with m.Switch(cpu_frag.address):
            for addr in self.fifo_queue_addresses:
                with m.Case(Const(addr, aw)):
                    m.d.comb += fifo_bypass.eq(0)

        m.d.comb += sync_required.eq(0)
        with m.Switch(cpu_frag.address):
            for addr in self.sync_addresses:
                with m.Case(Const(addr, aw)):
                    m.d.comb += sync_required.eq(1)

        # Float alias address decode
        m.d.comb += [
            is_float_alias.eq(0),
            float_target_addr.eq(0),
            float_shift_amount.eq(0),
        ]
        for float_addr, config in self.float_aliases.items():
            with m.If(cpu_frag.address == Const(float_addr, aw)):
                m.d.comb += [
                    is_float_alias.eq(1),
                    float_target_addr.eq(Const(config.target_addr, aw)),
                    float_shift_amount.eq(
                        Const(FLOAT_CONVERT_FRAC_BITS - config.frac_bits, 5)),
                ]

        # ==================================================================
        # Detection signals
        # ==================================================================
        # isWriteCmd uses cmd.valid (no ready dependency) for routing decisions.
        # doWriteImmediate uses cmd.fire for response/push (registered paths).
        is_write_cmd = Signal(name="isWriteCmd")
        do_write_immediate = Signal(name="doWriteImmediate")
        cpu_cmd_fire = Signal(name="io_cpuSide_cmd_fire")
        m.d.comb += [
            is_write_cmd.eq(cpu_cmd.valid & (cpu_frag.opcode == CMD_WRITE)),
            cpu_cmd_fire.eq(fire(cpu_cmd)),
            do_write_immediate.eq(cpu_cmd_fire & (cpu_frag.opcode == CMD_WRITE)),
        ]

        queueable_cpu_write = Signal(name="queueableCpuWrite")
        m.d.comb += queueable_cpu_write.eq(is_write_cmd & ~fifo_bypass)

        # ==================================================================
        # ingress: 16-entry staging FIFO in front of the float converter
        # ==================================================================
        staged_layout = StagedWrite(aw, dw)
        staged_write_tx = Stream(staged_layout, path=("ingress_stagedWriteTx",))
        st = staged_write_tx.payload
        # clearStaged(stagedWriteTx)
        m.d.comb += [
            staged_write_tx.valid.eq(0),
            st.address.eq(0),
            st.data.eq(0),
            st.mask.eq(0),
            st.sync_required.eq(0),
            st.is_texture.eq(0),
            st.tex_pci_addr.eq(0),
            st.is_float_alias.eq(0),
            st.float_target_addr.eq(0),
            st.float_shift_amount.eq(0),
        ]
        with m.If(queueable_cpu_write):
            m.d.comb += [
                staged_write_tx.valid.eq(cpu_cmd.valid),
                st.address.eq(cpu_frag.address),
                st.data.eq(cpu_frag.data),
                st.mask.eq(cpu_frag.mask),
                st.sync_required.eq(sync_required),
                st.is_float_alias.eq(is_float_alias),
                st.float_target_addr.eq(float_target_addr),
                st.float_shift_amount.eq(float_shift_amount),
            ]
        with m.Elif(self.tex_write.valid):
            m.d.comb += [
                staged_write_tx.valid.eq(1),
                st.data.eq(self.tex_write.payload.data),
                st.mask.eq(self.tex_write.payload.mask),
                st.is_texture.eq(1),
                st.tex_pci_addr.eq(self.tex_write.payload.pci_addr),
            ]

        stage_fifo = Fifo(staged_layout, STAGE_FIFO_DEPTH)
        m.submodules.ingress_stageFifo = stage_fifo
        connect_stream(m, stage_fifo.push, staged_write_tx)
        m.d.comb += stage_fifo.flush.eq(0)
        staged_write = stage_fifo.pop
        sw = staged_write.payload

        # ==================================================================
        # Float conversion pipeline
        # ==================================================================
        converter = Fpxx2AFix(FLOAT_CONVERT_INT_BITS, FLOAT_CONVERT_FRAC_BITS,
                              FpxxConfig.float32(), pipe_stages=1)
        m.submodules.floatConvert_converter = converter
        float_convert_in_flight = Signal(name="floatConvert_inFlight")           # RegInit(False)
        float_convert_target_addr = Signal(aw, reset_less=True,
                                           name="floatConvert_targetAddr")       # Reg, no init
        float_convert_shift = Signal(5, reset_less=True, name="floatConvert_shift")  # Reg, no init

        float_write_request = Signal(name="floatConvert_writeRequest")
        m.d.comb += [
            float_write_request.eq(staged_write.valid & sw.is_float_alias),
            converter.op.valid.eq(float_write_request & ~float_convert_in_flight),
            # converter.io.op.payload.assignFromBits(stagedWrite.data)
            converter.op.payload.as_value().eq(sw.data),
        ]
        op_fire = Signal(name="io_op_fire")
        m.d.comb += op_fire.eq(fire(converter.op))
        with m.If(op_fire):
            m.d.sync += [
                float_convert_in_flight.eq(1),
                float_convert_target_addr.eq(sw.float_target_addr),
                float_convert_shift.eq(sw.float_shift_amount),
            ]

        # ==================================================================
        # FIFO push mux
        # ==================================================================
        queued_layout = QueuedWrite(aw, dw)
        queued_write_tx = Stream(queued_layout, path=("queuePush_queuedWriteTx",))
        qt = queued_write_tx.payload
        # clearQueued(queuedWriteTx)
        m.d.comb += [
            queued_write_tx.valid.eq(0),
            qt.address.eq(0),
            qt.data.eq(0),
            qt.mask.eq(0),
            qt.sync_required.eq(0),
            qt.is_texture.eq(0),
            qt.tex_pci_addr.eq(0),
            qt.has_float_shadow.eq(0),
            qt.float_shadow_raw.eq(0),
        ]

        # val rawSInt = floatConverter.io.result.number.raw.asSInt  (51 bits)
        raw_sint = converter.result.payload.number.raw
        corrected = self.float_corrected(m, raw_sint, float_convert_shift)

        result_pending = Signal(name="when_PciFifo_l228")
        m.d.comb += result_pending.eq(float_convert_in_flight & converter.result.valid)
        with m.If(result_pending):
            # Push converted float data with remapped integer address
            m.d.comb += [
                queued_write_tx.valid.eq(1),
                qt.address.eq(float_convert_target_addr),
                qt.data.eq(corrected[:dw]),                  # corrected.resize(32 bits).asBits
                qt.mask.eq(0b1111),
                qt.has_float_shadow.eq(1),
                qt.float_shadow_raw.eq(raw_sint[:FLOAT_SHADOW_RAW_WIDTH]),  # rawSInt.resize(50)
            ]
        with m.Elif(staged_write.valid & ~sw.is_float_alias):
            m.d.comb += [
                queued_write_tx.valid.eq(1),
                qt.address.eq(sw.address),
                qt.data.eq(sw.data),
                qt.mask.eq(sw.mask),
                qt.sync_required.eq(sw.sync_required),
                qt.is_texture.eq(sw.is_texture),
                qt.tex_pci_addr.eq(sw.tex_pci_addr),
            ]

        # Converted float writes must be lossless. Hold the result until the FIFO
        # accepts it, which backpressures the host while conversion/output is busy.
        m.d.comb += converter.result.ready.eq(queued_write_tx.ready)
        with m.If(fire(converter.result)):
            m.d.sync += float_convert_in_flight.eq(0)

        queued_write_tx_fire = Signal(name="queuePush_queuedWriteTx_fire")
        m.d.comb += queued_write_tx_fire.eq(fire(queued_write_tx))

        m.d.comb += stage_fifo.pop.ready.eq(0)
        with m.If(staged_write.valid & ~sw.is_float_alias):
            m.d.comb += stage_fifo.pop.ready.eq(queued_write_tx_fire)
        with m.If(float_convert_in_flight & converter.result.valid):
            m.d.comb += stage_fifo.pop.ready.eq(queued_write_tx_fire)

        # Texture writes queue behind CPU writes but no longer stall behind
        # float conversion bubbles.
        m.d.comb += self.tex_write.ready.eq(~queueable_cpu_write & stage_fifo.push.ready)

        # wasEnqueued: detect when a specific register write was actually enqueued
        m.d.comb += self.was_enqueued.eq(
            queued_write_tx_fire & ~qt.is_texture & (qt.address == self.was_enqueued_addr))

        # ==================================================================
        # 64-entry PCI FIFO
        # ==================================================================
        pci_fifo = Fifo(queued_layout, PCI_FIFO_DEPTH)
        m.submodules.pciQueue_fifo = pci_fifo
        connect_stream(m, pci_fifo.push, queued_write_tx)
        m.d.comb += pci_fifo.flush.eq(0)
        m.d.comb += self.pci_fifo_free.eq(pci_fifo.availability)
        pop = pci_fifo.pop
        pp = pop.payload

        # ==================================================================
        # Drain blocking logic
        # ==================================================================
        can_drain = Signal(name="drainControl_canDrain")
        m.d.comb += can_drain.eq(1)

        # Registered issue stage directly after the PCI FIFO.
        issued_write = m2s_pipe(m, halt_when(m, pop, ~can_drain),
                                name="drainControl_issuedWrite_pipe")
        iw = issued_write.payload

        with m.If(pp.sync_required & self.pipeline_busy):
            m.d.comb += can_drain.eq(0)

        command_stream_blocked = Signal(name="drainControl_commandStreamBlocked")
        m.d.comb += command_stream_blocked.eq(0)
        with m.If(command_stream_blocked):
            m.d.comb += can_drain.eq(0)

        fifo_empty = Signal(name="pciFifo_fifoEmpty")
        m.d.comb += [
            fifo_empty.eq(~pop.valid & ~issued_write.valid),
            self.fifo_empty.eq(fifo_empty),
        ]

        drained_write = issued_write
        drained_write_fire = Signal(name="drainControl_issuedWrite_fire")
        m.d.comb += drained_write_fire.eq(fire(drained_write))

        # ==================================================================
        # Drain routing: register writes vs texture writes
        # ==================================================================
        is_tex_drain = Signal(name="drainRoute_isTexDrain")
        is_reg_drain = Signal(name="drainRoute_isRegDrain")
        m.d.comb += [
            is_tex_drain.eq(drained_write.valid & iw.is_texture),
            is_reg_drain.eq(drained_write.valid & ~iw.is_texture),
            self.tex_drain.valid.eq(is_tex_drain),
            self.tex_drain.payload.pci_addr.eq(iw.tex_pci_addr),
            self.tex_drain.payload.data.eq(iw.data),
            self.tex_drain.payload.mask.eq(iw.mask),
            self.float_shadow.valid.eq(is_reg_drain & drained_write_fire & iw.has_float_shadow),
            self.float_shadow.payload.address.eq(iw.address),
            self.float_shadow.payload.raw.eq(iw.float_shadow_raw),
            self.sync_drained.eq(is_reg_drain & drained_write_fire & iw.sync_required),
        ]

        # ==================================================================
        # Master bus (regSide) mux
        # ==================================================================
        # Direct writes (FIFO=No) -- register bypass.  Uses isWriteCmd
        # (cmd.valid) not doWriteImmediate (cmd.fire) to avoid the
        # combinational loop cmd.ready -> fire -> doWriteDirect ->
        # cpuPassthrough -> stall -> cmd.ready.
        do_write_direct = Signal(name="regSideMux_doWriteDirect")
        cpu_passthrough = Signal(name="regSideMux_cpuPassthrough")
        do_reg_drain = Signal(name="regSideMux_doRegDrain")
        m.d.comb += [
            do_write_direct.eq(is_write_cmd & fifo_bypass),
            cpu_passthrough.eq(cpu_cmd.valid & ((cpu_frag.opcode == CMD_READ) | do_write_direct)),
            do_reg_drain.eq(is_reg_drain & ~cpu_passthrough),
            reg_cmd.valid.eq(cpu_passthrough | do_reg_drain),
        ]
        if "source" in reg_frag.shape().members:
            m.d.comb += reg_frag.source.eq(cpu_frag.source)
        if "context" in reg_frag.shape().members:
            m.d.comb += reg_frag.context.eq(cpu_frag.context)

        # `io.regSide.cmd.last := True` is assigned inside both branches rather
        # than once, unconditionally: a constant driven into a single *bit* of
        # a packed port becomes a bare connection, which yosys writes as
        # `always @* port[0] = 1'h1;` -- an empty sensitivity list that
        # Verilator never executes, so the lockstep diff saw `last == 0`.
        # Inside the branches it is part of the muxed process and has one.
        with m.If(do_reg_drain):
            m.d.comb += [
                reg_cmd.payload.last.eq(1),
                reg_frag.opcode.eq(CMD_WRITE),
                reg_frag.address.eq(iw.address),
                reg_frag.data.eq(iw.data),
                reg_frag.mask.eq(iw.mask),
                reg_frag.length.eq(3),
            ]
        with m.Else():
            m.d.comb += [
                reg_cmd.payload.last.eq(1),
                reg_frag.opcode.eq(cpu_frag.opcode),
                reg_frag.address.eq(cpu_frag.address),
                reg_frag.data.eq(cpu_frag.data),
                reg_frag.mask.eq(cpu_frag.mask),
                reg_frag.length.eq(cpu_frag.length),
            ]

        m.d.comb += drained_write.ready.eq(
            (is_reg_drain & ~cpu_passthrough & reg_cmd.ready) | (is_tex_drain & self.tex_drain.ready))

        # ==================================================================
        # Response routing
        # ==================================================================
        # CPU passthrough response: forward from regSide back to cpuSide.
        # FIFO'd write: generate immediate response at enqueue time.
        # Drain response: consumed/discarded.
        m.d.comb += reg_rsp.ready.eq(1)

        cpu_rsp_frag = cpu_rsp.payload.fragment
        reg_rsp_frag = reg_rsp.payload.fragment

        # RegNext(doWriteImmediate || (cmd.fire && READ)) init(False)
        rsp_valid_reg = Signal(name="_zz_io_cpuSide_rsp_valid")
        m.d.sync += rsp_valid_reg.eq(
            do_write_immediate | (cpu_cmd_fire & (cpu_frag.opcode == CMD_READ)))
        # RegNext(io.cpuSide.cmd.source) -- no init
        has_source = "source" in cpu_frag.shape().members
        if has_source:
            source_reg_next = Signal(len(cpu_frag.source), reset_less=True,
                                     name="io_cpuSide_cmd_payload_fragment_source_regNext")
            m.d.sync += source_reg_next.eq(cpu_frag.source)
        has_context = "context" in cpu_frag.shape().members
        if has_context:
            context_reg_next = Signal(len(cpu_frag.context), reset_less=True,
                                      name="io_cpuSide_cmd_payload_fragment_context_regNext")
            m.d.sync += context_reg_next.eq(cpu_frag.context)

        m.d.comb += [
            cpu_rsp.valid.eq(rsp_valid_reg),
            cpu_rsp_frag.opcode.eq(RSP_SUCCESS),
        ]
        if has_source:
            m.d.comb += cpu_rsp_frag.source.eq(source_reg_next)
        if has_context:
            m.d.comb += cpu_rsp_frag.context.eq(context_reg_next)

        pending_read = Signal(name="response_pendingRead")                        # RegInit(False)
        with m.If(cpu_cmd_fire & (cpu_frag.opcode == CMD_READ)):
            m.d.sync += pending_read.eq(1)
        with m.If(pending_read & reg_rsp.valid):
            m.d.sync += pending_read.eq(0)

        # `io.cpuSide.rsp.last := True` -- in both branches, for the reason
        # given at `io.regSide.cmd.last` above.
        with m.If(pending_read):
            m.d.comb += [
                cpu_rsp.payload.last.eq(1),
                cpu_rsp.valid.eq(reg_rsp.valid),
                cpu_rsp_frag.data.eq(reg_rsp_frag.data),
                cpu_rsp_frag.opcode.eq(reg_rsp_frag.opcode),
            ]
            if has_source:
                m.d.comb += cpu_rsp_frag.source.eq(reg_rsp_frag.source)
        with m.Else():
            m.d.comb += [
                cpu_rsp.payload.last.eq(1),
                cpu_rsp_frag.data.eq(0),
            ]

        # ==================================================================
        # Stall logic
        # ==================================================================
        is_read_cmd = cpu_cmd.valid & (cpu_frag.opcode == CMD_READ)
        m.d.comb += cpu_cmd.ready.eq(1)
        with m.If(queueable_cpu_write):
            m.d.comb += cpu_cmd.ready.eq(stage_fifo.push.ready)
        with m.If(is_read_cmd & pending_read):
            m.d.comb += cpu_cmd.ready.eq(0)
        with m.If(is_read_cmd & ~pending_read & ~reg_cmd.ready):
            m.d.comb += cpu_cmd.ready.eq(0)

        # ==================================================================
        # Command stream drain blocking (the tail of the addPrePopTask)
        # ==================================================================
        for idx, addr in enumerate(self.command_addresses):
            with m.If(pp.address == Const(addr, aw)):
                m.d.comb += command_stream_blocked.eq(~self.command_ready[idx])

        # ==================================================================
        # Simulation probes (not ports): the internals the Scala formal block
        # asserts on, so tests/test_pci_fifo.py can check the same invariants
        # per cycle in pysim.  Nothing here changes the emitted RTL.
        # ==================================================================
        self.probes = SimpleNamespace(
            fifo_bypass=fifo_bypass,
            sync_required=sync_required,
            is_float_alias=is_float_alias,
            float_target_addr=float_target_addr,
            float_shift_amount=float_shift_amount,
            is_write_cmd=is_write_cmd,
            queueable_cpu_write=queueable_cpu_write,
            staged_write_tx=staged_write_tx,
            stage_fifo=stage_fifo,
            staged_write=staged_write,
            converter=converter,
            float_convert_in_flight=float_convert_in_flight,
            float_convert_target_addr=float_convert_target_addr,
            float_convert_shift=float_convert_shift,
            float_write_request=float_write_request,
            corrected=corrected,
            queued_write_tx=queued_write_tx,
            pci_fifo=pci_fifo,
            can_drain=can_drain,
            command_stream_blocked=command_stream_blocked,
            issued_write=issued_write,
            drained_write=drained_write,
            is_tex_drain=is_tex_drain,
            is_reg_drain=is_reg_drain,
            do_write_direct=do_write_direct,
            cpu_passthrough=cpu_passthrough,
            do_reg_drain=do_reg_drain,
            pending_read=pending_read,
        )
        return m
