"""``BmbToAxi4Mem`` -- ``MemBus`` master to AXI4 master for the DDR3 controller.

Transliteration of ``object BmbToAxi4Mem`` / ``case class BmbToAxi4Mem`` in
``fpga/src/voodoo/artix/CoreArtix.scala`` (lines 197-232) and of
``spinal/lib/bus/bmb/BmbToAxi4Bridge.scala`` ``BmbToAxi4SharedBridge``
(SpinalHDL 1.15.0), the library component it is built on; plus the
``CoreArtix.axiBurstLog2`` / ``fbAxiConfig`` / ``texAxiConfig`` helpers
(``CoreArtix.scala:236-244``).  Plan Phase 1.5.

The chain is ``BmbAligner -> BmbLengthFixer -> BmbToAxi4SharedBridge ->
toAxi4()``: every burst is aligned and split to ``2**burst_log2`` bytes so
nothing crosses a 4 KiB boundary or exceeds AXI's 256-beat limit, then
bridged.  ``CoreGowin.scala:44-45`` builds two: one on ``fbMemBmbParams``
(five arbitrated framebuffer masters) and one on ``texMemBmbParams``, both
with ``burstLog2 = 8`` (256-byte bursts, 64 beats of 32 bits).

Registers without a reset (plain ``Reg``/``RegNextWhen`` in the Scala) are
``reset_less`` here: ``states(i).write`` and ``rspSelReadLast``, plus the
half-pipe payload registers inside :func:`avoodoo.lib.stream.half_pipe`.
"""

from __future__ import annotations

from amaranth import Const, Module, Mux, Signal, unsigned
from amaranth.lib import data, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.membus_params import (AXI_BURST_LOG2, fb_mem_bmb_params,
                                        tex_mem_bmb_params)
from avoodoo.lib.axi import (BURST_INCR, CACHE_DEFAULT, PROT_DEFAULT, RESP_OKAY,
                             Axi4Config, Axi4SharedSignature, Axi4Signature,
                             connect_axi4, shared_to_axi4)
from avoodoo.lib.membus import (CMD_WRITE, RSP_ERROR, RSP_SUCCESS, Aligner,
                                LengthFixer, MemBusParameter, MemBusSignature,
                                connect, first_flag, log2_up,
                                transfer_beat_count_minus_one)
from avoodoo.lib.stream import (Stream, arbitration_from, fire, fork2, half_pipe,
                                halt_when, queue, throw_when)

__all__ = [
    "AXI_BURST_LOG2",
    "shared_bridge_axi_config",
    "aligned_parameter",
    "axi_config",
    "fb_axi_config",
    "tex_axi_config",
    "MemBusToAxi4SharedBridge",
    "MemBusToAxi4Mem",
]


# --------------------------------------------------------------------------
# parameter helpers
# --------------------------------------------------------------------------


def shared_bridge_axi_config(p: MemBusParameter) -> Axi4Config:
    """``BmbToAxi4SharedBridge.getAxi4Config(bmbConfig)``.

    ``idWidth = 0`` with ``useId = true`` -- the ``id`` fields exist in the
    Scala bundle but are zero bits wide, so no Verilog port carries them and
    the layouts here omit them.  ``useQos``/``useRegion``/``useLock`` off.
    """
    a = p.access
    return Axi4Config(
        address_width=a.address_width,
        data_width=a.data_width,
        id_width=0,
        use_id=True,
        use_size=True,
        use_len=True,
        use_last=True,
        use_resp=True,
        use_strb=True,
        use_prot=True,
        use_cache=True,
        use_qos=False,
        use_region=False,
        use_burst=True,
        use_lock=False,
    )


def aligned_parameter(p: MemBusParameter, burst_log2: int) -> MemBusParameter:
    """``BmbToAxi4Mem.alignedParameter(p, burstLog2)`` -- after aligner + fixer."""
    aligned = Aligner.output_access_parameter(p.access, burst_log2)
    return LengthFixer.output_access_parameter(aligned, burst_log2).to_mem_bus_parameter()


def axi_config(p: MemBusParameter, burst_log2: int) -> Axi4Config:
    """``BmbToAxi4Mem.axiConfig(p, burstLog2)``."""
    return shared_bridge_axi_config(aligned_parameter(p, burst_log2))


def fb_axi_config(c: Config) -> Axi4Config:
    """``CoreArtix.fbAxiConfig(c)`` (``CoreGowin.fbAxiConfig`` is the same)."""
    return axi_config(fb_mem_bmb_params(c), AXI_BURST_LOG2)


def tex_axi_config(c: Config) -> Axi4Config:
    """``CoreArtix.texAxiConfig(c)``."""
    return axi_config(tex_mem_bmb_params(c), AXI_BURST_LOG2)


# --------------------------------------------------------------------------
# BmbToAxi4Bridge.scala -- BmbToAxi4SharedBridge
# --------------------------------------------------------------------------


class MemBusToAxi4SharedBridge(wiring.Component):
    """``spinal.lib.bus.bmb.BmbToAxi4SharedBridge(bmbConfig, pendingMax = 31, halfRateAw = true)``.

    One ``CounterUpDown(pendingMax + 1)`` and a ``write`` flag per source id
    track what is in flight; ``hazard`` stalls a command that would mix
    reads and writes in flight for one source, or exceed ``pendingMax``.  The
    command is forked (asynchronously) into an address beat -- the first beat
    of a burst only -- and, for writes, the data beats.  Per-direction
    ``Info`` FIFOs (32 deep) carry ``source``/``context`` back to the
    response, through a ``halfPipe`` (``halfRateAw``).  Responses: ``rspSelLock``
    holds the read/write choice for the length of an ``r`` burst.
    """

    def __init__(self, bmb_config: MemBusParameter, pending_max: int = 31,
                 half_rate_aw: bool = True):
        self.bmb_config = bmb_config
        self.pending_max = pending_max
        self.half_rate_aw = bool(half_rate_aw)
        self.axi_config = shared_bridge_axi_config(bmb_config)
        super().__init__({
            "input": In(MemBusSignature(bmb_config)),
            "output": Out(Axi4SharedSignature(self.axi_config)),
        })

    def _info_layout(self) -> data.StructLayout:
        """``case class Info { source; context }``."""
        a = self.bmb_config.access
        f: dict = {}
        if a.source_width > 0:
            f["source"] = unsigned(a.source_width)
        if a.context_width > 0:
            f["context"] = unsigned(a.context_width)
        if not f:
            raise NotImplementedError(
                "BmbToAxi4SharedBridge with neither source nor context: the Info "
                "FIFO would carry a zero-width payload; no parameter set in the "
                "design does this")
        return data.StructLayout(f)

    def elaborate(self, platform):
        m = Module()
        a = self.bmb_config.access
        in_cmd, in_rsp = self.input.cmd, self.input.rsp
        cmd_frag, rsp_frag = in_cmd.payload.fragment, in_rsp.payload.fragment
        out = self.output
        pending_max = self.pending_max
        counter_w = log2_up(pending_max + 1)

        def source_is(sid: int):
            if a.source_width == 0:
                return Const(1)
            return cmd_frag.source == sid

        def rsp_source_is(sid: int):
            if a.source_width == 0:
                return Const(1)
            return rsp_frag.source == sid

        is_write = cmd_frag.opcode == CMD_WRITE
        cmd_fire = fire(in_cmd)
        rsp_fire = fire(in_rsp)

        pending_write = Signal(name="pendingWrite")
        pending_counter = Signal(counter_w, name="pendingCounter")
        m.d.comb += [pending_write.eq(0), pending_counter.eq(0)]   # assignDontCare

        # `states` -- one CounterUpDown(pendingMax + 1) + write flag per source.
        for sid in a.sources:
            pfx = f"states_{sid}"
            inc_when = source_is(sid) & cmd_fire & in_cmd.payload.last
            dec_when = rsp_source_is(sid) & rsp_fire & in_rsp.payload.last
            counter = _counter_up_down(m, pending_max + 1, inc_when, dec_when,
                                       name=f"{pfx}_counter")
            write = Signal(name=f"{pfx}_write", reset_less=True)
            with m.If(source_is(sid) & cmd_fire):
                m.d.sync += write.eq(is_write)
            with m.If(source_is(sid)):
                m.d.comb += [pending_write.eq(write), pending_counter.eq(counter)]

        hazard = Signal(name="hazard")
        m.d.comb += hazard.eq(((is_write != pending_write) & (pending_counter != 0))
                              | (pending_counter == pending_max))
        halted = halt_when(m, in_cmd, hazard)
        cmd_fork, data_fork = fork2(m, halted, synchronous=False,
                                    name="io_input_cmd_haltWhen_fork2")
        first = first_flag(m, in_cmd, name="io_input_cmd_payload_first")
        cmd_stage = throw_when(m, cmd_fork, ~first)
        data_stage = throw_when(m, data_fork,
                                ~(data_fork.payload.fragment.opcode == CMD_WRITE))

        info_layout = self._info_layout()
        write_cmd_info = Stream(info_layout, path=("writeCmdInfo",))
        read_cmd_info = Stream(info_layout, path=("readCmdInfo",))
        stage_frag = cmd_stage.payload.fragment
        stage_is_write = stage_frag.opcode == CMD_WRITE
        m.d.comb += [
            write_cmd_info.valid.eq(fire(cmd_stage) & stage_is_write),
            read_cmd_info.valid.eq(fire(cmd_stage) & ~stage_is_write),
        ]
        for info in (write_cmd_info, read_cmd_info):
            for name in info_layout.members:
                m.d.comb += info.payload[name].eq(stage_frag[name])

        fifo_depth = 1 << log2_up(pending_max)
        write_q = queue(m, write_cmd_info, fifo_depth, name="writeCmdInfo_fifo")
        if self.half_rate_aw:
            write_rsp_info = half_pipe(m, write_q, name="writeRspInfo_halfPipe")
        else:
            from avoodoo.lib.stream import m2s_pipe, s2m_pipe
            write_rsp_info = s2m_pipe(m, m2s_pipe(m, write_q, name="writeRspInfo_m2s"),
                                      name="writeRspInfo_s2m")
        read_rsp_info = half_pipe(m, queue(m, read_cmd_info, fifo_depth,
                                           name="readCmdInfo_fifo"),
                                  name="readRspInfo_halfPipe")

        arw = out.arw.payload
        arbitration_from(m, out.arw,
                         halt_when(m, cmd_stage, ~write_cmd_info.ready | ~read_cmd_info.ready))
        m.d.comb += [
            arw.burst.eq(BURST_INCR),
            arw.write.eq(is_write),
            arw.addr.eq(cmd_frag.address),
            arw.len.eq(transfer_beat_count_minus_one(self.bmb_config, cmd_frag)),
            arw.size.eq(log2_up(a.byte_count)),
            arw.prot.eq(PROT_DEFAULT),
            arw.cache.eq(CACHE_DEFAULT),
        ]

        arbitration_from(m, out.w, data_stage)
        m.d.comb += [
            out.w.payload.data.eq(data_stage.payload.fragment.data),
            out.w.payload.strb.eq(data_stage.payload.fragment.mask),
            out.w.payload.last.eq(data_stage.payload.last),
        ]

        rsp_sel_lock = Signal(name="rspSelLock")
        with m.If(out.r.valid | out.b.valid):
            m.d.sync += rsp_sel_lock.eq(1)
        with m.If((fire(out.r) & out.r.payload.last) | fire(out.b)):
            m.d.sync += rsp_sel_lock.eq(0)
        rsp_sel_read_last = Signal(name="rspSelReadLast", reset_less=True)
        with m.If(~rsp_sel_lock):
            m.d.sync += rsp_sel_read_last.eq(out.r.valid)
        rsp_sel_read = Signal(name="rspSelRead")
        m.d.comb += rsp_sel_read.eq(Mux(rsp_sel_lock, rsp_sel_read_last, out.r.valid))

        last_fire = rsp_fire & in_rsp.payload.last
        m.d.comb += [
            out.b.ready.eq(in_rsp.ready & ~rsp_sel_read & write_rsp_info.valid),
            out.r.ready.eq(in_rsp.ready & rsp_sel_read & read_rsp_info.valid),
            write_rsp_info.ready.eq(last_fire & ~rsp_sel_read),
            read_rsp_info.ready.eq(last_fire & rsp_sel_read),
        ]

        m.d.comb += rsp_frag.data.eq(out.r.payload.data)
        with m.If(rsp_sel_read):
            m.d.comb += [
                in_rsp.valid.eq(out.r.valid & read_rsp_info.valid),
                in_rsp.payload.last.eq(out.r.payload.last),
            ]
            for name in info_layout.members:
                m.d.comb += rsp_frag[name].eq(read_rsp_info.payload[name])
        with m.Else():
            m.d.comb += [
                in_rsp.valid.eq(out.b.valid & write_rsp_info.valid),
                in_rsp.payload.last.eq(1),
            ]
            for name in info_layout.members:
                m.d.comb += rsp_frag[name].eq(write_rsp_info.payload[name])
        with m.If(Mux(rsp_sel_read, out.r.payload.resp == RESP_OKAY,
                      out.b.payload.resp == RESP_OKAY)):
            m.d.comb += rsp_frag.opcode.eq(RSP_SUCCESS)
        with m.Else():
            m.d.comb += rsp_frag.opcode.eq(RSP_ERROR)
        return m


def _counter_up_down(m: Module, state_count: int, inc_when, dec_when, *, name: str):
    """``CounterUpDown(stateCount, incWhen, decWhen)`` (``Counter.scala``, 1.15.0).

    ``new Counter(0, stateCount - 1, CounterDirection.Both)``: for a
    power-of-two ``stateCount`` the ``stepTrick`` path applies -- ``step`` is
    ``1`` on increment-only, ``maxValue`` (i.e. ``-1``) on decrement-only,
    ``0`` otherwise, and ``valueNext = value + step`` wraps naturally.  The
    non-pow2 case is not ported (``pendingMax = 31`` is the only caller).
    Returns the registered ``value``.
    """
    if state_count & (state_count - 1):
        raise NotImplementedError("CounterUpDown is only ported for pow2 state counts")
    w = log2_up(state_count)
    will_increment = Signal(name=f"{name}_willIncrement")
    will_decrement = Signal(name=f"{name}_willDecrement")
    value = Signal(w, name=f"{name}_value")
    value_next = Signal(w, name=f"{name}_valueNext")
    inc_only = Signal(name=f"{name}_incOnly")
    dec_only = Signal(name=f"{name}_decOnly")
    step = Signal(w, name=f"{name}_step")
    m.d.comb += [
        will_increment.eq(inc_when),
        will_decrement.eq(dec_when),
        inc_only.eq(will_increment & ~will_decrement),
        dec_only.eq(will_decrement & ~will_increment),
        step.eq(Mux(inc_only, 1, Mux(dec_only, (1 << w) - 1, 0))),
        value_next.eq(value + step),
    ]
    m.d.sync += value.eq(value_next)
    return value


# --------------------------------------------------------------------------
# CoreArtix.scala -- BmbToAxi4Mem
# --------------------------------------------------------------------------


class MemBusToAxi4Mem(wiring.Component):
    """``case class BmbToAxi4Mem(p: BmbParameter, burstLog2: Int)``.

    ``aligner`` -> ``fixer`` -> ``bridge`` -> ``toAxi4()`` -> ``output``.
    """

    def __init__(self, p: MemBusParameter, burst_log2: int):
        self.p = p
        self.burst_log2 = burst_log2
        self.axi_config = axi_config(p, burst_log2)
        super().__init__({
            "input": In(MemBusSignature(p)),
            "output": Out(Axi4Signature(self.axi_config)),
        })

    def elaborate(self, platform):
        m = Module()
        aligner = Aligner(self.p, self.burst_log2)
        fixer = LengthFixer(aligner.op, self.burst_log2)
        bridge = MemBusToAxi4SharedBridge(fixer.op)
        m.submodules.aligner = aligner
        m.submodules.fixer = fixer
        m.submodules.bridge = bridge

        connect(m, aligner.input, self.input)
        connect(m, fixer.input, aligner.output)
        connect(m, bridge.input, fixer.output)
        connect_axi4(m, self.output, shared_to_axi4(m, bridge.output))
        return m
