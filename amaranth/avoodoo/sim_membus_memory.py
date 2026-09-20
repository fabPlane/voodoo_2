"""``SimMemBusMemory`` -- the simulation-only RAM behind the core's MemBus.

Port of ``ext/SpinalVoodoo/src/voodoo/SimBmbMemory.scala`` (plan Phase 1.4).
``CoreSim.scala:32-35`` instantiates two of them, 4 MiB behind the framebuffer
arbiter and 8 MiB on the texture bus, both with the default
:class:`SimMemoryTiming`.

Plan §6 allows exactly two simulation-only modules under ``avoodoo/``:
``core_sim.py`` and this one.

Differences from the Scala that are deliberate, not drift
---------------------------------------------------------

* **One memory, not four byte lanes.**  Spinal writes ``Mem(Bits(8 bits))``
  four times (``ram_symbol0..3``) because a byte-enabled ``Mem`` is what its
  backend emits anyway.  Amaranth/yosys keep a byte-granular
  :class:`amaranth.lib.memory.Memory` as a single ``reg [31:0] mem [0:N-1]``,
  which is *better* for the Verilator harness: one index per word instead of
  four.  ``avoodoo/core_sim_ports.py`` owns that contract
  (``RAM_ARRAY_NAME = "mem"``, ``FB_MEM_PATH``/``TEX_MEM_PATH``), the C side is
  ``amaranth/emu/sim/harness/sim_harness.cpp``, and
  ``tests/test_core_sim_ports.py`` keeps the two spellings honest.  **The
  Memory must stay ``m.submodules.mem`` inside this component**, or every
  ``sim_read_fb`` / ``sim_write_*_bulk`` call in the Glide harness stops
  working.
* The read port is combinational (``readAsync`` in the Scala), so the response
  data is available in the same cycle the state machine declares it valid.

Behaviour, cycle for cycle
--------------------------

Idle accepts a command.  A read latches the word address and the beat count
(``(length + 1) / 4``) and streams beats back, decrementing on each accepted
response; a write burst stays in ``WriteCmd`` accepting beats until ``last``,
then emits a single response.  ``source`` and ``context`` are captured from
the first beat and echoed on every response, which is what makes the
:class:`avoodoo.lib.membus.Arbiter` in front of it work.

**The write address comes from the current beat, not from a burst counter.**
That matches how ``FramebufferPlaneBuffer`` drives write bursts (it increments
``cmd.address`` per beat) and it is why an ``Aligner``/``LengthFixer``-style
constant-address burst must not be pointed straight at this model.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from amaranth import Cat, Const, Module, Mux, Signal, unsigned
from amaranth.lib import memory, wiring
from amaranth.lib.wiring import In

from avoodoo.core_sim_ports import RAM_ARRAY_NAME
from avoodoo.lib.membus import (CMD_READ, CMD_WRITE, RSP_SUCCESS, MemBusParameter,
                                MemBusSignature, _has, log2_up)

__all__ = ["SimMemoryTiming", "SimMemBusMemory"]


_MODES = ("onchip", "ideal", "fixed", "burst")


@dataclass(frozen=True)
class SimMemoryTiming:
    """``case class SimMemoryTiming`` -- the latency model.

    ``mode``:

    ``onchip`` / ``ideal``
        zero latency everywhere (the default, and what the golden Glide
        screenshots were produced with).
    ``fixed``
        every read beat costs ``fixed_read_latency`` cycles.
    ``burst``
        the first beat of a read costs ``burst_setup_latency``, later beats
        ``burst_beat_latency`` -- a crude DRAM model.

    ``write_latency`` applies to the single response of a write burst in every
    mode except the zero-latency ones (the Scala always applies it; with the
    default 0 that is the same thing).
    """

    mode: str = "onchip"
    fixed_read_latency: int = 0
    burst_setup_latency: int = 12
    burst_beat_latency: int = 1
    write_latency: int = 0

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError(f"mode must be one of {_MODES}, got {self.mode!r}")
        for name in ("fixed_read_latency", "burst_setup_latency",
                     "burst_beat_latency", "write_latency"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")

    @property
    def max_latency(self) -> int:
        return max(self.fixed_read_latency, self.burst_setup_latency,
                   self.burst_beat_latency, self.write_latency)


class _OpState(enum.IntEnum):
    """``object OpState extends SpinalEnum`` -- binary encoding, same order."""

    IDLE = 0
    READ_RSP = 1
    WRITE_CMD = 2
    WRITE_RSP = 3


class SimMemBusMemory(wiring.Component):
    """``voodoo.SimBmbMemory`` (``ext/SpinalVoodoo/src/voodoo/SimBmbMemory.scala``)."""

    def __init__(self, p: MemBusParameter, size: int,
                 timing: SimMemoryTiming | None = None):
        if size <= 0 or size % 4:
            raise ValueError(f"size must be a positive multiple of 4, got {size}")
        if p.access.data_width != 32:
            raise NotImplementedError("SimBmbMemory is hard-wired to 32-bit data")
        self.p = p
        self.size = size
        self.timing = timing if timing is not None else SimMemoryTiming()
        self.word_count = size // 4
        self.word_addr_width = log2_up(self.word_count)
        self.burst_count_width = p.access.length_width
        self.latency_width = log2_up(self.timing.max_latency + 1)
        super().__init__({"bus": In(MemBusSignature(p))})

    # -- readLatency(firstBeat) -------------------------------------------

    def _read_latency(self, first_beat: bool) -> int:
        t = self.timing
        if t.mode == "fixed":
            return t.fixed_read_latency
        if t.mode == "burst":
            return t.burst_setup_latency if first_beat else t.burst_beat_latency
        return 0

    def elaborate(self, platform):
        m = Module()
        a = self.p.access
        cmd = self.bus.cmd
        rsp = self.bus.rsp
        cmd_f = cmd.payload.fragment
        rsp_f = rsp.payload.fragment
        lw = self.latency_width

        mem = memory.Memory(shape=unsigned(32), depth=self.word_count, init=[])
        setattr(m.submodules, RAM_ARRAY_NAME, mem)
        wp = mem.write_port(granularity=8)
        rp = mem.read_port(domain="comb")

        cmd_word_addr = cmd_f.address[2:2 + self.word_addr_width]
        cmd_byte_count = (cmd_f.length + 1)[:a.length_width + 1]
        cmd_word_count = (cmd_byte_count >> 2)[:self.burst_count_width]

        state = Signal(_OpState, init=_OpState.IDLE)
        current_word_addr = Signal(self.word_addr_width, name="currentWordAddr")
        words_remaining = Signal(self.burst_count_width, name="wordsRemaining")
        rsp_delay = Signal(lw, name="rspDelay")
        rsp_source = Signal(max(a.source_width, 1), name="rspSource")
        has_context = a.context_width > 0
        rsp_context = Signal(a.context_width, name="rspContext") if has_context else None

        read_active = state == _OpState.READ_RSP
        rsp_valid = ((state == _OpState.READ_RSP) | (state == _OpState.WRITE_RSP)) \
            & (rsp_delay == 0)

        m.d.comb += [
            rp.addr.eq(current_word_addr),
            cmd.ready.eq((state == _OpState.IDLE) | (state == _OpState.WRITE_CMD)),
            rsp.valid.eq(rsp_valid),
            rsp.payload.last.eq(~read_active | (words_remaining == 1)),
            rsp_f.opcode.eq(RSP_SUCCESS),
        ]
        if _has(rsp_f, "data"):
            m.d.comb += rsp_f.data.eq(Mux(read_active, rp.data, Const(0, 32)))
        if _has(rsp_f, "source"):
            m.d.comb += rsp_f.source.eq(rsp_source)
        if has_context and _has(rsp_f, "context"):
            m.d.comb += rsp_f.context.eq(rsp_context)

        do_cmd = cmd.valid & cmd.ready
        do_read_cmd = do_cmd & (cmd_f.opcode == CMD_READ)
        do_write_cmd = do_cmd & (cmd_f.opcode == CMD_WRITE)
        do_rsp = rsp.valid & rsp.ready

        cmd_source = cmd_f.source if _has(cmd_f, "source") else Const(0, 1)

        # `when(state === Idle && io.bus.cmd.valid)` -- note: valid, not fire.
        with m.If((state == _OpState.IDLE) & cmd.valid):
            m.d.sync += rsp_source.eq(cmd_source)
            if has_context:
                m.d.sync += rsp_context.eq(cmd_f.context)

        mask = cmd_f.mask if _has(cmd_f, "mask") else Const(0xF, 4)
        m.d.comb += [
            wp.addr.eq(cmd_word_addr),
            wp.data.eq(cmd_f.data if _has(cmd_f, "data") else Const(0, 32)),
            wp.en.eq(Cat(*(do_write_cmd & mask[i] for i in range(4)))),
        ]

        with m.If(do_read_cmd):
            m.d.sync += [
                state.eq(_OpState.READ_RSP),
                current_word_addr.eq(cmd_word_addr),
                words_remaining.eq(cmd_word_count),
                rsp_delay.eq(self._read_latency(first_beat=True)),
            ]

        with m.If(do_write_cmd):
            with m.If(state == _OpState.IDLE):
                m.d.sync += rsp_source.eq(cmd_source)
                if has_context:
                    m.d.sync += rsp_context.eq(cmd_f.context)
            m.d.sync += words_remaining.eq(1)
            with m.If(cmd.payload.last):
                m.d.sync += [
                    state.eq(_OpState.WRITE_RSP),
                    rsp_delay.eq(self.timing.write_latency),
                ]
            with m.Else():
                m.d.sync += [state.eq(_OpState.WRITE_CMD), rsp_delay.eq(0)]

        with m.If(((state == _OpState.READ_RSP) | (state == _OpState.WRITE_RSP))
                  & (rsp_delay != 0)):
            m.d.sync += rsp_delay.eq(rsp_delay - 1)

        with m.If(do_rsp):
            with m.If((state == _OpState.READ_RSP) & (words_remaining > 1)):
                m.d.sync += [
                    current_word_addr.eq(current_word_addr + 1),
                    words_remaining.eq(words_remaining - 1),
                    rsp_delay.eq(self._read_latency(first_beat=False)),
                ]
            with m.Else():
                m.d.sync += [
                    state.eq(_OpState.IDLE),
                    words_remaining.eq(0),
                    rsp_delay.eq(0),
                ]
        return m
