"""MemBus (Bmb) adapter for a regif-style register file.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/bus/BmbBusInterface.scala`` (268
lines), which is a ``spinal.lib.bus.regif.BusIf`` implementation plus the
Voodoo-specific metadata exports, and of the part of ``spinal.lib.bus.regif``
(``RegInst`` / ``RegSlice`` / ``FieldDescr``, SpinalHDL 1.15.0) that
``RegisterBank`` actually reaches: ``newRegAt``, ``field`` / ``fieldAt`` with
``AccessType.RO`` / ``WO`` / ``RW``, ``hitDoWrite``, the byte-strobe write
masks and the registered read mux.  Everything else in regif (``W1P``,
``RC``, secure firewalls, the documentation generators) is not used by the
core and is not here.

It is not a ``Component``: the Scala is a helper class that owns signals
inside whichever component instantiates it, and so is this -- the owner
passes its :class:`~amaranth.Module` and its ``bus`` interface (an
:class:`~avoodoo.lib.membus.MemBusSignature` seen from the slave side), calls
``new_reg_at*`` per register, and finally :meth:`BusInterface.finalize` to
emit the read mux.  Signal names follow the Scala (``busif_reg_rdata``,
``write_hit_0x0110``, ...) so waveforms line up.

Protocol (BmbBusInterface.scala:98-151)::

    bus.cmd.ready := True                                 -- PciFifo stalls, not us
    bus.rsp.valid := RegNext(bus.cmd.fire) init False
    bus.rsp.last := True ; bus.rsp.opcode := SUCCESS
    bus.rsp.source := RegNext(bus.cmd.source)            -- plain Reg: no reset
    bus.rsp.data := reg_rdata                            -- registered read mux

    askWrite = cmd.valid && opcode == WRITE ; doWrite = cmd.fire && ...
    askRead  = cmd.valid && opcode == READ  ; doRead  = cmd.fire && ...
    wmask(8i+7 downto 8i) = wstrb(i) ? 0xff : 0 ; wmaskn = ~wmask

Per field (regif ``RegSlice`` for ``RW`` / ``WO``)::

    when(write_hit) { f := (f & wmaskn(hi downto lo)) | (wdata(hi downto lo) & wmask(hi downto lo)) }

Read mux (regif ``BusIf.readGenerator``), read off ``RegisterBank_spinal.v``::

    when(askRead) {
      switch(cmd.address) {
        is(addr) { reg_rdata := <RO/RW fields at their bits, WO and NA as 0>
                   reg_rderr := <any WO field> }        -- one case per register with >= 1 field
        default  { reg_rdata := 0 ; reg_rderr := False }
      }
    } otherwise { reg_rdata := 0 ; reg_rderr := False }

``reg_rderr`` is kept for fidelity but drives nothing, exactly as upstream
(the Verilog has no port for it).

The two command-register constructors buffer their pulse stream with
``src.s2mPipe().m2sPipe()`` and record ``src.ready`` -- the s2m stage's
``!rValid`` -- per address for ``RegisterBank.io.commandReady`` /
``PciFifo.io.commandReady`` (BmbBusInterface.scala:170-190).
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Callable, Optional

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.hdl import ShapeCastable
from amaranth.lib import data, stream

from avoodoo.bus.register_category import FIFO_NO_SYNC, RegisterCategory
from avoodoo.frontend.regdef import Access, FLOAT_ALIAS_OFFSET, FloatAlias
from avoodoo.lib.fixed import AFixShape, AFixValue
from avoodoo.lib.membus import CMD_READ, CMD_WRITE, RSP_SUCCESS
from avoodoo.lib.stream import Stream, m2s_pipe, s2m_pipe

__all__ = [
    "FieldInst",
    "RegInst",
    "BusInterface",
    "NO_DATA",
]

#: ``Stream(NoData)``: ``spinal.lib.NoData`` is an empty Bundle, so Spinal emits
#: only ``valid``/``ready``; the zero-width Amaranth payload is dropped by the
#: lockstep harness (``tools/diff/amports.py``) and is otherwise inert.  Same
#: spelling as ``avoodoo.frontend.swap_buffer.NO_DATA``.
NO_DATA = stream.Signature(unsigned(0))


# ---------------------------------------------------------------------------
# Fields and registers
# ---------------------------------------------------------------------------


@dataclass
class FieldInst:
    """One regif field: a bit range of a 32-bit register and its storage.

    ``signal`` is the register (``RW`` / ``WO``) or the combinational wire the
    owner drives (``RO``).  ``afix`` is the :class:`~avoodoo.lib.fixed.AFixValue`
    wrapped around it when the field was declared with a fixed-point prototype
    (``VoodooRegInst.field(that: AFix, ...)``, BmbBusInterface.scala:50-59).
    """

    name: str
    lo: int
    width: int
    access: Access
    reset: int
    signal: Signal
    afix: Optional[AFixValue] = None
    #: regif's own field index for this register, counting the ``NA`` padding
    #: regif inserts in front of a ``fieldAt`` that leaves a gap -- the ``N``
    #: of upstream's ``sim_reg_%03x_f<N>`` names.
    spinal_index: int = 0

    @property
    def hi(self) -> int:
        return self.lo + self.width - 1


class RegInst:
    """``RegInst`` / ``RegSlice`` (regif) as seen through ``VoodooRegInst``.

    Created by :meth:`BusInterface.new_reg_at`; fields are added with
    :meth:`field_at` / :meth:`afix_field_at` and the write side is emitted
    immediately, in the owner's module.
    """

    def __init__(self, busif: "BusInterface", addr: int, name: str):
        self.busif = busif
        self.addr = addr
        self.name = name
        self.fields: list[FieldInst] = []
        self._next_index = 0
        self._next_bit = 0
        # regif: `hitDoRead` / `hitDoWrite` = address match && doRead/doWrite
        self.hit_do_read = Signal(name=f"read_hit_0x{addr:04x}")
        self.hit_do_write = Signal(name=f"write_hit_0x{addr:04x}")
        m = busif.m
        m.d.comb += [
            self.hit_do_read.eq((busif.cmd_address == addr) & busif.do_read),
            self.hit_do_write.eq((busif.cmd_address == addr) & busif.do_write),
        ]

    # -- field declaration ---------------------------------------------------

    def _place(self, lo: int, width: int) -> int:
        """Advance the regif cursor; return this field's regif index."""
        if lo < self._next_bit:
            raise ValueError(
                f"{self.name}: field at bit {lo} overlaps or precedes bit {self._next_bit}")
        if lo > self._next_bit:
            self._next_index += 1          # the `fieldNA` regif inserts for the gap
        idx = self._next_index
        self._next_index += 1
        self._next_bit = lo + width
        if self._next_bit > self.busif.bus_data_width:
            raise ValueError(f"{self.name}: field [{lo + width - 1}:{lo}] exceeds the bus width")
        return idx

    def field_at(self, lo: int, width: int, access: Access, reset: int = 0, *,
                 name: str, signed_: bool = False) -> Signal:
        """``fieldAt(bitOffset, that, accessType, resetValue, doc)``.

        ``RO`` returns a combinational :class:`Signal` for the owner to drive;
        ``RW`` / ``WO`` return the storage register, already given its masked
        write.  ``reset`` is the regif ``section`` argument, i.e. the reset
        value.
        """
        access = Access(access)
        idx = self._place(lo, width)
        shape = signed(width) if signed_ else unsigned(width)
        sig_name = f"{self.name}_{name}"
        if access is Access.RO:
            sig = Signal(shape, name=sig_name)
        elif access in (Access.RW, Access.WO):
            sig = Signal(shape, name=sig_name, init=reset)
            busif = self.busif
            hi = lo + width
            m = busif.m
            raw = sig.as_unsigned()   # the masking is bitwise; keep it unsigned
            with m.If(self.hit_do_write):
                m.d.sync += sig.eq(
                    (raw & busif.wmaskn[lo:hi]) | (busif.write_data[lo:hi] & busif.wmask[lo:hi]))
        else:
            raise ValueError(f"{self.name}.{name}: access {access} is not RO/RW/WO")
        inst = FieldInst(name, lo, width, access, reset, sig, spinal_index=idx)
        self.fields.append(inst)
        return sig

    def afix_field_at(self, lo: int, shape: AFixShape, access: Access, reset: int = 0, *,
                      name: str) -> AFixValue:
        """``VoodooRegInst.fieldAt(bitOffset, that: AFix, ...)`` (BmbBusInterface.scala:61).

        The storage is an ``SInt``/``UInt`` of the prototype's raw width and the
        returned value is ``AFix(proto)`` with ``raw := base.asBits``.
        """
        base = self.field_at(lo, shape.bit_width, access, reset, name=name,
                             signed_=shape.signed)
        afix = AFixValue(shape, base)
        self.fields[-1].afix = afix
        return afix

    # -- read side -------------------------------------------------------------

    def read_word(self) -> tuple:
        """``(bits, has_wo)`` -- the 32-bit read-back word and the ``rderr`` flag."""
        pieces = []
        pos = 0
        has_wo = False
        for f in sorted(self.fields, key=lambda f: f.lo):
            if f.lo > pos:
                pieces.append(Const(0, f.lo - pos))
            if f.access is Access.WO:
                pieces.append(Const(0, f.width))
                has_wo = True
            else:
                pieces.append(f.signal)
            pos = f.lo + f.width
        if pos < self.busif.bus_data_width:
            pieces.append(Const(0, self.busif.bus_data_width - pos))
        return Cat(*pieces), has_wo


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class BusInterface:
    """``BmbBusInterface(bus, sizeMap, regPre, withSecFireWall)``.

    Parameters
    ----------
    m : Module
        The owner's module; every signal and statement lands in it.
    bus
        The owner's ``bus`` port, an ``In(MemBusSignature(p))`` (i.e. the
        slave side: ``cmd`` in, ``rsp`` out).
    """

    def __init__(self, m: Module, bus, *, reg_pre: str = ""):
        self.m = m
        self.bus = bus
        self.reg_pre = reg_pre
        p = bus.signature.parameter if hasattr(bus.signature, "parameter") else None
        self.parameter = p
        cmd = bus.cmd.payload.fragment
        rsp = bus.rsp.payload.fragment
        self.bus_data_width = len(cmd.data)
        self.bus_addr_width = len(cmd.address)
        self.strb_width = self.bus_data_width // 8

        self.slices: list[RegInst] = []
        self._by_addr: dict[int, RegInst] = {}
        self._register_categories: dict[int, RegisterCategory] = {}
        self._command_stream_ready: dict[int, Signal] = {}
        self._float_aliases: dict[int, FloatAlias] = {}
        self._last_created_reg_addr = -1
        self._finalized = False

        # Error and data registers (BmbBusInterface.scala:105-109)
        self.reg_rderr = Signal(name="busif_reg_rderr")
        self.reg_rdata = Signal(self.bus_data_width, name="busif_reg_rdata")   # init defaultReadBits = 0
        self.bus_rdata = Signal(self.bus_data_width, name="busif_bus_rdata")

        # BMB supports byte strobes via the mask field
        self.wstrb = Signal(self.strb_width, name="busif_wstrb")
        self.wmask = Signal(self.bus_data_width, name="busif_wmask")
        self.wmaskn = Signal(self.bus_data_width, name="busif_wmaskn")

        # Transaction signals -- all writes are immediate (PciFifo did the ordering)
        self.cmd_fire = Signal(name="io_bus_cmd_fire")
        self.ask_write = Signal(name="busif_askWrite")
        self.ask_read = Signal(name="busif_askRead")
        self.do_write = Signal(name="busif_doWrite")
        self.do_read = Signal(name="busif_doRead")
        self.write_data = cmd.data
        self.cmd_address = cmd.address

        cmd_fire_reg_next = Signal(name="io_bus_cmd_fire_regNext")
        m.d.sync += cmd_fire_reg_next.eq(self.cmd_fire)
        # RegNext(bus.cmd.source): plain Reg, no reset (plan §6)
        source_reg_next = Signal(len(cmd.source), name="io_bus_cmd_payload_fragment_source_regNext",
                                 reset_less=True)
        m.d.sync += source_reg_next.eq(cmd.source)

        m.d.comb += [
            self.cmd_fire.eq(bus.cmd.valid & bus.cmd.ready),
            self.ask_write.eq(bus.cmd.valid & (cmd.opcode == CMD_WRITE)),
            self.ask_read.eq(bus.cmd.valid & (cmd.opcode == CMD_READ)),
            self.do_write.eq(self.cmd_fire & (cmd.opcode == CMD_WRITE)),
            self.do_read.eq(self.cmd_fire & (cmd.opcode == CMD_READ)),
            self.wstrb.eq(cmd.mask),
            rsp.data.eq(self.bus_rdata),
            self.bus_rdata.eq(self.reg_rdata),
            # Simple response: one cycle after any command
            bus.rsp.valid.eq(cmd_fire_reg_next),
            bus.rsp.payload.last.eq(1),
            rsp.opcode.eq(RSP_SUCCESS),
            rsp.source.eq(source_reg_next),
            # Always ready -- PciFifo handles stall logic
            bus.cmd.ready.eq(1),
        ]
        if "context" in rsp.shape().members:
            context_reg_next = Signal(len(cmd.context), name="io_bus_cmd_payload_fragment_context_regNext",
                                      reset_less=True)
            m.d.sync += context_reg_next.eq(cmd.context)
            m.d.comb += rsp.context.eq(context_reg_next)

        # initStrbMasks()
        for i in range(self.strb_width):
            m.d.comb += [
                self.wmask[8 * i: 8 * i + 8].eq(Mux(self.wstrb[i], Const(0xFF, 8), Const(0, 8))),
                self.wmaskn[8 * i: 8 * i + 8].eq(Mux(self.wstrb[i], Const(0, 8), Const(0xFF, 8))),
            ]

    # -- register creation -----------------------------------------------------

    def new_reg_at(self, addr: int, name: str) -> RegInst:
        """``newRegAt(addr, name)`` -- a register with no category entry."""
        if self._finalized:
            raise RuntimeError("BusInterface.finalize() already called")
        if addr in self._by_addr:
            raise ValueError(f"register at 0x{addr:03x} declared twice ({name})")
        if addr % (self.bus_data_width // 8):
            raise ValueError(f"{name}: 0x{addr:03x} is not word aligned")
        reg = RegInst(self, addr, name)
        self.slices.append(reg)
        self._by_addr[addr] = reg
        return reg

    def new_reg_at_with_category(self, addr: int, name: str,
                                 category: RegisterCategory) -> RegInst:
        """``newRegAtWithCategory`` (BmbBusInterface.scala:156)."""
        self._register_categories[addr] = category
        self._last_created_reg_addr = addr
        return self.new_reg_at(addr, name)

    def with_float_alias(self, target: FieldInst | AFixValue | None, int_bits: int | None = None,
                         frac_bits: int | None = None, *, name: str = "") -> None:
        """``field.withFloatAlias()`` (BmbBusInterface.scala:218-236).

        Registers ``lastCreatedRegAddr + 0x080`` as a ``fifoNoSync`` float
        alias of the last register.  Metadata only -- the IEEE-754 conversion
        lives in ``PciFifo``.  With no explicit widths the field must be an
        AFix; ``intWidth`` / ``fracWidth`` are its ``QFormat``'s
        ``non_fraction`` / ``fraction``.
        """
        if self._last_created_reg_addr < 0:
            raise RuntimeError("with_float_alias must be called after new_reg_at_with_category")
        if int_bits is None or frac_bits is None:
            afix = target.afix if isinstance(target, FieldInst) else target
            if not isinstance(afix, AFixValue):
                raise TypeError("withFloatAlias() without arguments requires an AFix field")
            shape = afix.afix_shape
            int_bits, frac_bits = shape.int_width, shape.frac_width
        target_addr = self._last_created_reg_addr
        float_addr = target_addr + FLOAT_ALIAS_OFFSET
        reg = self._by_addr[target_addr]
        self._float_aliases[float_addr] = FloatAlias(
            addr=float_addr, name=name or ("f" + reg.name), target_addr=target_addr,
            target_name=reg.name, int_bits=int_bits, frac_bits=frac_bits)
        self._register_categories[float_addr] = FIFO_NO_SYNC

    def _buffered_command_stream(self, addr: int, src, name: str):
        """``bufferedCommandStream`` (BmbBusInterface.scala:170): ``s2mPipe().m2sPipe()``."""
        m = self.m
        s2m = s2m_pipe(m, src, name=f"{name}_s2m")
        out = m2s_pipe(m, s2m, name=f"{name}_m2s")
        # src.ready is the s2m stage's `!rValid`: registered, so safe to export
        self._command_stream_ready[addr] = src.ready
        return out

    def new_command_reg(self, addr: int, name: str, category: RegisterCategory):
        """``newCommandReg`` -- ``(RegInst, Stream[NoData])`` pulsing on ``hitDoWrite``."""
        self._register_categories[addr] = category
        reg = self.new_reg_at(addr, name)
        pulse = Stream(unsigned(0), path=(f"{name}_pulseStream",))
        self.m.d.comb += pulse.valid.eq(reg.hit_do_write)
        return reg, self._buffered_command_stream(addr, pulse, name)

    def new_command_reg_with_payload(self, addr: int, name: str, category: RegisterCategory,
                                     payload_shape, build_payload: Callable):
        """``newCommandRegWithPayload`` (BmbBusInterface.scala:196).

        ``build_payload(reg, payload)`` fills the stream's payload
        combinationally, in the write cycle; the same s2m/m2s buffering as
        :meth:`new_command_reg` follows.
        """
        self._register_categories[addr] = category
        reg = self.new_reg_at(addr, name)
        src = Stream(payload_shape, path=(f"{name}_src",))
        self.m.d.comb += src.valid.eq(reg.hit_do_write)
        build_payload(reg, src.payload)
        return reg, self._buffered_command_stream(addr, src, name)

    # -- read mux --------------------------------------------------------------

    def finalize(self) -> None:
        """Emit the registered read mux over every slice (``BusIf.readGenerator``)."""
        if self._finalized:
            raise RuntimeError("BusInterface.finalize() called twice")
        self._finalized = True
        m = self.m
        with m.If(self.ask_read):
            with m.Switch(self.cmd_address):
                for reg in self.slices:
                    if not reg.fields:
                        # A slice with only regif's auto-inserted NA padding
                        # (triangleCMD / ftriangleCMD) is left out of the case.
                        continue
                    word, has_wo = reg.read_word()
                    with m.Case(reg.addr):
                        m.d.sync += [self.reg_rdata.eq(word), self.reg_rderr.eq(has_wo)]
                with m.Default():
                    m.d.sync += [self.reg_rdata.eq(0), self.reg_rderr.eq(0)]
        with m.Else():
            m.d.sync += [self.reg_rdata.eq(0), self.reg_rderr.eq(0)]

    # -- metadata exports for PciFifo ------------------------------------------

    def get_categories(self) -> dict[int, RegisterCategory]:
        """``getCategories`` -- addr -> category, float aliases included."""
        return dict(sorted(self._register_categories.items()))

    def get_float_aliases(self) -> dict[int, FloatAlias]:
        """``getFloatAliases`` -- float addr -> alias config."""
        return dict(sorted(self._float_aliases.items()))

    def get_command_stream_ready(self) -> dict[int, Signal]:
        """``getCommandStreamReady`` -- addr -> the pulse stream's ``ready``."""
        return dict(sorted(self._command_stream_ready.items()))

    def slice_at(self, addr: int) -> RegInst:
        return self._by_addr[addr]
