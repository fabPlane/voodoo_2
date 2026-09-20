"""``SwapBuffer`` -- the ``swapbufferCMD`` (0x128) command handler.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/frontend/SwapBuffer.scala``
(SpinalHDL 1.15.0), instantiated by ``Core.scala:119`` as ``SwapBuffer()``
inside the ``controlPlane`` Area.

The Voodoo1 double-buffer swap: with vsync disabled the swap completes the
cycle after the command is accepted; with vsync enabled it blocks until the
retrace counter has passed ``swapInterval`` vertical retraces.

Three things the Scala comments call out and the transliteration keeps:

* ``fifoWithSync`` guarantees the pipeline is idle before the command arrives,
  so ``cmd.ready`` may be a bare ``state == Idle`` -- the command is accepted
  unconditionally and the wait happens in this component's own state.
* the command stream (out of ``s2mPipe``) raises ``valid`` one cycle *before*
  the register bank has updated ``vsyncEnable``/``swapInterval``, which is why
  there is a ``SAMPLING`` state that does nothing but burn that cycle.
* ``swapCompleted`` is a combinational signal, and the ``swapsPendingReg``
  update that reads it is written *above* the ``switch`` that drives it.  A
  SpinalHDL ``Bool`` is a net, not a variable, so the read sees the resolved
  value including the later assignment; the Amaranth ``m.d.comb`` default plus
  overrides below has exactly that behaviour, and the statement order is kept
  anyway so the two Verilogs read alike.

Register reset values: every register in the Scala is a ``RegInit`` / ``Reg
... init(...)`` / ``RegNext ... init(...)``, i.e. all six *do* have a reset, so
none of them needs ``reset_less=True`` (contrast ``bus/stream_while.py``,
whose ``state`` is a plain ``Reg``).

The ``GenerationFlags.formal`` block is dropped from the RTL per plan §2/§3;
``tests/test_swap_buffer.py`` asserts the same invariants every cycle in
simulation and Phase 9.1 re-expresses them for SymbiYosys.
"""

from __future__ import annotations

from amaranth import Module, Signal, unsigned
from amaranth.lib import enum, stream, wiring
from amaranth.lib.wiring import In, Out

__all__ = ["State", "SwapBuffer"]

#: ``slave Stream(NoData)``.  ``spinal.lib.NoData`` is an empty ``Bundle``, so
#: Spinal emits only ``io_cmd_valid`` / ``io_cmd_ready``; the zero-width
#: Amaranth payload is emitted by yosys as ``input [-1:0] cmd__payload``, which
#: the lockstep harness treats as no port at all (``tools/diff/amports.py``
#: drops zero-width leaves, commit 1a96914) and Verilator only warns about.
#: Kept as a real ``stream.Signature`` rather than a bespoke valid/ready
#: signature so it composes with every ``amaranth.lib.stream`` consumer --
#: RegisterBank's ``swapbufferCMD`` command stream will be one.
NO_DATA = stream.Signature(unsigned(0))


class State(enum.Enum, shape=unsigned(2)):
    """``object State extends SpinalEnum { val Idle, Sampling, Waiting }``.

    Spinal's default ``native`` encoding numbers the elements in declaration
    order, so ``Idle`` is 0 and ``RegInit(State.Idle)`` resets to all-zero --
    which is also what an Amaranth ``Signal(State)`` does by default.
    """

    Idle = 0
    Sampling = 1
    Waiting = 2


class SwapBuffer(wiring.Component):
    """``case class SwapBuffer(formalStrong: Boolean = false)``.

    Parameters
    ----------
    dbg_load_port : bool
        **Not in the Scala.**  Adds ``swap_count_set`` / ``swap_count_we``, a
        one-cycle load path into ``swap_count_reg``.  The upstream Verilator
        harness pokes ``…controlPlane_swapBuffer__DOT__swapCountReg`` directly
        (``sim_set_swap_count()``); the Amaranth ``CoreSim`` has no
        hierarchical-name contract and exposes ``dbg_swap_count_set`` /
        ``dbg_swap_count_we`` as real ports instead
        (:mod:`avoodoo.core_sim_ports`, ``DBG_PORTS``).  ``core_sim.py`` builds
        its ``SwapBuffer`` with ``dbg_load_port=True`` and wires those two
        ports straight through; every other instantiation, the lockstep diff
        against the Spinal Verilog included, leaves it ``False`` so the port
        list is identical to the Scala one.

        The load wins over the state machine's own increment in the same
        cycle, and its only callers (``emu/test/trace_test.cpp``,
        ``tools/sim_trace_capture.cpp``) strobe it at trace-replay boundaries
        where a clock edge is not observable.

        The matching *read* side needs nothing extra: ``dbg_swap_count`` is
        ``swap_count``, which is already ``swapCountReg`` combinationally.

    Attributes
    ----------
    cmd : In(stream.Signature(unsigned(0)))
        ``slave Stream(NoData)``; see :data:`NO_DATA`.
    v_retrace, vsync_enable, swap_interval, swap_cmd_enqueued : In
    waiting, swap_count, swaps_pending : Out
    """

    def __init__(self, *, dbg_load_port: bool = False):
        self.dbg_load_port = bool(dbg_load_port)
        members = {
            "cmd": In(NO_DATA),
            "v_retrace": In(1),
            "vsync_enable": In(1),
            "swap_interval": In(8),
            "swap_cmd_enqueued": In(1),

            "waiting": Out(1),
            "swap_count": Out(2),
            "swaps_pending": Out(3),
        }
        if self.dbg_load_port:
            members["swap_count_set"] = In(2)
            members["swap_count_we"] = In(1)
        super().__init__(members)

    def elaborate(self, platform):
        m = Module()

        # val state = RegInit(State.Idle)
        state = Signal(State, init=State.Idle)
        # val retraceCounter   = Reg(UInt(8 bits)) init (0)
        retrace_counter = Signal(8)
        # val swapCountReg     = Reg(UInt(2 bits)) init (0)
        swap_count_reg = Signal(2)
        # val swapsPendingReg  = Reg(UInt(3 bits)) init (0)
        swaps_pending_reg = Signal(3)
        # val vRetracePrev     = RegNext(io.vRetrace) init (False)
        v_retrace_prev = Signal()
        # val capturedInterval = Reg(UInt(8 bits)) init (0)
        captured_interval = Signal(8)

        m.d.sync += v_retrace_prev.eq(self.v_retrace)

        # val vRetraceRising = io.vRetrace && !vRetracePrev
        v_retrace_rising = Signal()
        m.d.comb += v_retrace_rising.eq(self.v_retrace & ~v_retrace_prev)

        # val swapCompleted = False   (a net; the switch below overrides it)
        swap_completed = Signal()
        m.d.comb += swap_completed.eq(0)

        # when(io.swapCmdEnqueued && !swapCompleted) { swapsPendingReg := +1 }
        # elsewhen (!io.swapCmdEnqueued && swapCompleted) { swapsPendingReg := -1 }
        with m.If(self.swap_cmd_enqueued & ~swap_completed):
            m.d.sync += swaps_pending_reg.eq(swaps_pending_reg + 1)
        with m.Elif(~self.swap_cmd_enqueued & swap_completed):
            m.d.sync += swaps_pending_reg.eq(swaps_pending_reg - 1)

        m.d.comb += [
            self.waiting.eq(state != State.Idle),
            self.swap_count.eq(swap_count_reg),
            self.swaps_pending.eq(swaps_pending_reg),
            self.cmd.ready.eq(state == State.Idle),
        ]

        cmd_fire = self.cmd.valid & self.cmd.ready

        with m.Switch(state):
            with m.Case(State.Idle):
                with m.If(cmd_fire):
                    m.d.sync += state.eq(State.Sampling)

            with m.Case(State.Sampling):
                with m.If(~self.vsync_enable):
                    m.d.sync += swap_count_reg.eq(swap_count_reg + 1)
                    m.d.comb += swap_completed.eq(1)
                    m.d.sync += state.eq(State.Idle)
                with m.Else():
                    m.d.sync += [
                        state.eq(State.Waiting),
                        retrace_counter.eq(0),
                        captured_interval.eq(self.swap_interval),
                    ]

            with m.Case(State.Waiting):
                with m.If(v_retrace_rising):
                    with m.If(retrace_counter >= captured_interval):
                        m.d.sync += swap_count_reg.eq(swap_count_reg + 1)
                        m.d.comb += swap_completed.eq(1)
                        m.d.sync += state.eq(State.Idle)
                    with m.Else():
                        m.d.sync += retrace_counter.eq(retrace_counter + 1)

        # Not in the Scala: the CoreSim debug load port (see __init__).
        # Placed last so it overrides the increments above in the same cycle.
        if self.dbg_load_port:
            with m.If(self.swap_count_we):
                m.d.sync += swap_count_reg.eq(self.swap_count_set)

        return m
