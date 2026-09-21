"""Phase 1.6 unit tests for :mod:`avoodoo.bus.pci_fifo`.

Three groups.

1. **Directed sequences** porting the intent of
   ``ext/SpinalVoodoo/test/voodoo/utils/PciFifoTest.scala`` against the same
   miniature register map its ``PciFifoTestHarness`` builds (command register
   at 0x010, FIFO register at 0x014 with float alias 0x094, FIFO=No register
   at 0x020, Sync=Yes register at 0x030).  The Scala harness sits a real
   ``BmbBusInterface`` behind ``regSide``; here a tiny scripted register-file
   slave (:class:`RegSlave`) plays that part, so the checks are on the
   ``regSide`` bus beats themselves rather than on register contents.

   One number differs from the Scala on purpose: its "queue up to 64
   commands" tests predate the 16-entry ingress ``stageFifo``
   (PciFifo.scala:183) and count 65 accepted writes with a 1-entry buffer
   behind the command stream.  With the ingress stage the module itself
   accepts ``64 + 16 = 80`` writes before ``cmd.ready`` drops, and that is
   what is asserted -- it is what the Verilator diff sees too.

2. **The formal invariants** of ``PciFifo.scala``'s ``GenerationFlags.formal``
   block and of ``PciFifoFormalTest.scala``, re-expressed as per-cycle checks
   under randomised stimulus that keeps the formal DUT's assumptions
   (``formalAssumesSlave`` on ``cpuSide.cmd`` / ``texWrite`` -- payload held
   while ``valid && !ready``; ``regSide.rsp.valid`` only while a read is
   outstanding).  The RTL exposes the internals the asserts name through
   ``PciFifo.probes`` (simulation-only handles, not ports).

   The ``formalStrong`` occupancy tracker is ported with its timing made
   explicit rather than verbatim: the Scala asserts
   ``pciFifoFree + (pushes - drains) === 64`` every cycle, but ``drains`` is
   ``issuedWrite.fire`` -- one registered stage *after* the FIFO's pop -- so
   as written it is off by one whenever the issue register holds an entry.
   ``StreamFifo.availability`` is ``64 - (pushPtr - popOnIo)``, both pointer
   registers, so the exact statement is: ``64 - pciFifoFree`` equals the
   pushes minus the FIFO pops that fired in *earlier* cycles, and the issue
   register holds exactly ``pops - drains`` (0 or 1).  Both are asserted.

3. **The datapath reference**: an independent Python model of
   ``Fpxx2AFix(20, 30, float32)`` plus ``floatCorrected`` (PciFifo.scala:152),
   checked against the converted ``regSide`` data and the ``floatShadow``
   raw word for a sweep of float32 patterns and every shift the register set
   uses (26, 18, 12, 0).

Cycle-exactness against the Spinal Verilog is not this file's job: that is
``tests/diff/test_diff_pci_fifo.py`` / ``make diff MOD=PciFifo``.
"""

from __future__ import annotations

import random
import re
import struct
from collections import deque
from pathlib import Path

import pytest
from amaranth.hdl import Fragment
from amaranth.sim import Simulator

from avoodoo.bus.pci_fifo import (
    FLOAT_CONVERT_FRAC_BITS, PCI_FIFO_DEPTH, STAGE_FIFO_DEPTH, FloatAliasConfig, PciFifo,
    TexWritePayload)
from avoodoo.core.membus_params import register_bank_params
from avoodoo.frontend import regdef
from avoodoo.frontend.regdef import BYPASS_FIFO, FIFO_NO_SYNC, FIFO_WITH_SYNC
from avoodoo.lib.membus import CMD_READ, CMD_WRITE, RSP_SUCCESS

PERIOD = 1e-8

# ---------------------------------------------------------------------------
# the PciFifoTestHarness register map (PciFifoTest.scala:44-63)
# ---------------------------------------------------------------------------

CMD_REG = 0x010        # newCommandReg, fifoNoSync
FIFO_REG = 0x014       # fifoNoSync, .withFloatAlias(12, 4)
FLOAT_REG = 0x094      # = FIFO_REG + FLOAT_ALIAS_OFFSET
DIRECT_REG = 0x020     # bypassFifo
SYNC_REG = 0x030       # fifoWithSync
UNMAPPED = 0x3F0       # not registered: falls through to fifoBypass

SMALL_CATEGORIES = {
    CMD_REG: FIFO_NO_SYNC,
    FIFO_REG: FIFO_NO_SYNC,
    DIRECT_REG: BYPASS_FIFO,
    SYNC_REG: FIFO_WITH_SYNC,
    FLOAT_REG: FIFO_NO_SYNC,
}
SMALL_FLOAT_ALIASES = {FLOAT_REG: FloatAliasConfig(FIFO_REG, 12, 4)}
SMALL_COMMANDS = (CMD_REG,)

BUS = register_bank_params()
ADDR_W = BUS.access.address_width
CAPACITY = PCI_FIFO_DEPTH + STAGE_FIFO_DEPTH


def make_small() -> PciFifo:
    return PciFifo(BUS, SMALL_CATEGORIES, SMALL_FLOAT_ALIASES, SMALL_COMMANDS)


def make_real() -> PciFifo:
    """The Core.scala:102 instance: the full regdef tables."""
    return PciFifo(BUS, regdef.categories(), regdef.float_aliases(),
                   regdef.command_addresses())


def f2b(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def to_signed(raw: int, width: int) -> int:
    raw &= (1 << width) - 1
    return raw - (1 << width) if raw >> (width - 1) else raw


# ---------------------------------------------------------------------------
# reference datapath: Fpxx2AFix(20, 30, float32) + floatCorrected
# ---------------------------------------------------------------------------

_BIAS = 127
_INT_BITS = 20
_FRAC_BITS = FLOAT_CONVERT_FRAC_BITS
_TOTAL = _INT_BITS + _FRAC_BITS           # 50
_MANT_W = 23 + 2                          # hidden bit + sign position
_RAW_W = _TOTAL + 1                       # 51


def fpxx2afix_ref(bits: int) -> int:
    """``Fpxx2AFix.scala`` for ``(20 bits, 30 bits, float32)``: the 51-bit raw.

    Crude on purpose, exactly like the hardware: no NaN/Inf/denormal handling,
    truncation toward minus infinity, saturation only on the ``shift < 0``
    branch (see ``avoodoo/lib/fpxx2afix.py``).
    """
    sign = (bits >> 31) & 1
    exp = (bits >> 23) & 0xFF
    mant = bits & 0x7FFFFF
    ge0 = exp >= max(_BIAS - _FRAC_BITS, 0) and not (exp == 0 and mant == 0)
    shift = (_INT_BITS - 1 + _BIAS) - exp
    mant_full = (1 << 23) | mant
    mant_2c = -mant_full if sign else mant_full
    if shift >= _TOTAL or not ge0:
        return 0
    if shift < 0:
        return (1 << _TOTAL) - 1 if mant_2c >= 0 else -(1 << _TOTAL)
    widened = mant_2c << (_RAW_W - _MANT_W)
    return to_signed(widened >> shift, _RAW_W)


def float_corrected_ref(raw: int, shift: int) -> int:
    """``PciFifo.floatCorrected`` (PciFifo.scala:152) on a 51-bit signed raw."""
    shifted = raw >> shift
    has_remainder = False
    if shift in (26, 18, 12):
        has_remainder = (raw & ((1 << shift) - 1)) != 0
    corrected = shifted + 1 if (raw < 0 and has_remainder) else shifted
    return to_signed(corrected, _RAW_W)


def float_alias_ref(bits: int, frac_bits: int) -> tuple[int, int]:
    """``(regSide data, floatShadow raw)`` for a float alias write of ``bits``."""
    raw = fpxx2afix_ref(bits)
    shift = _FRAC_BITS - frac_bits
    data = float_corrected_ref(raw, shift) & 0xFFFFFFFF
    shadow = to_signed(raw, 50)
    return data, shadow


# ---------------------------------------------------------------------------
# simulation helpers
# ---------------------------------------------------------------------------


def run(dut, tb, *, extra=()):
    sim = Simulator(dut)
    sim.add_clock(PERIOD)
    sim.add_testbench(tb)
    for p in extra:
        sim.add_testbench(p, background=True)
    sim.run()


def set_cmd(ctx, dut, *, valid=0, opcode=CMD_WRITE, address=0, data=0, mask=0xF,
            source=0, length=3, last=1):
    frag = dut.cpu_side.cmd.payload.fragment
    ctx.set(dut.cpu_side.cmd.valid, valid)
    ctx.set(dut.cpu_side.cmd.payload.last, last)
    ctx.set(frag.opcode, opcode)
    ctx.set(frag.address, address)
    ctx.set(frag.data, data)
    ctx.set(frag.mask, mask)
    ctx.set(frag.source, source)
    ctx.set(frag.length, length)


def idle(ctx, dut, *, pipeline_busy=0, command_ready=None, reg_cmd_ready=1,
         tex_drain_ready=1, cpu_rsp_ready=1, was_enqueued_addr=0):
    set_cmd(ctx, dut, valid=0)
    ctx.set(dut.cpu_side.rsp.ready, cpu_rsp_ready)
    ctx.set(dut.reg_side.cmd.ready, reg_cmd_ready)
    ctx.set(dut.reg_side.rsp.valid, 0)
    ctx.set(dut.tex_write.valid, 0)
    ctx.set(dut.tex_drain.ready, tex_drain_ready)
    ctx.set(dut.pipeline_busy, pipeline_busy)
    ctx.set(dut.was_enqueued_addr, was_enqueued_addr)
    if hasattr(dut, "command_ready"):
        n = len(dut.command_ready)
        ready = [1] * n if command_ready is None else list(command_ready)
        for i in range(n):
            ctx.set(dut.command_ready[i], ready[i])


async def cpu_write(ctx, dut, address, data, *, mask=0xF, source=0):
    """One BMB write beat; returns the number of cycles ``ready`` was low."""
    set_cmd(ctx, dut, valid=1, opcode=CMD_WRITE, address=address, data=data,
            mask=mask, source=source)
    stalls = 0
    while not ctx.get(dut.cpu_side.cmd.ready):
        stalls += 1
        await ctx.tick()
    await ctx.tick()          # the beat fires on this edge
    set_cmd(ctx, dut, valid=0)
    return stalls


async def cpu_read(ctx, dut, address, *, source=0):
    set_cmd(ctx, dut, valid=1, opcode=CMD_READ, address=address, source=source)
    while not ctx.get(dut.cpu_side.cmd.ready):
        await ctx.tick()
    await ctx.tick()
    set_cmd(ctx, dut, valid=0)


async def wait_rsp(ctx, dut, limit=50):
    """The next ``cpuSide.rsp`` beat, sampled at the clock edge (a forwarded
    read response is a one-cycle pulse driven by the background slave, so
    sampling mid-cycle would race it)."""
    frag = dut.cpu_side.rsp.payload.fragment
    for _ in range(limit):
        _clk, _rst, valid, data, opcode, source, last = await ctx.tick().sample(
            dut.cpu_side.rsp.valid, frag.data, frag.opcode, frag.source,
            dut.cpu_side.rsp.payload.last)
        if valid:
            return {"data": data, "opcode": opcode, "source": source, "last": last}
    raise AssertionError("no cpuSide response")


def reg_beat(ctx, dut):
    """The ``regSide.cmd`` beat presented this cycle (None if not valid)."""
    if not ctx.get(dut.reg_side.cmd.valid):
        return None
    frag = dut.reg_side.cmd.payload.fragment
    return {
        "opcode": ctx.get(frag.opcode), "address": ctx.get(frag.address),
        "data": ctx.get(frag.data), "mask": ctx.get(frag.mask),
        "length": ctx.get(frag.length), "source": ctx.get(frag.source),
        "last": ctx.get(dut.reg_side.cmd.payload.last),
        "fire": ctx.get(dut.reg_side.cmd.valid) and ctx.get(dut.reg_side.cmd.ready),
    }


class RegSlave:
    """A scripted ``regSide`` slave: a word register file, read latency 1.

    Stands in for the Scala harness's ``BmbBusInterface`` -- ``cmd.ready`` is
    a test knob, a write lands the same cycle it fires, a read answers on the
    next cycle with ``rsp.valid`` for one cycle.
    """

    def __init__(self, dut):
        self.dut = dut
        self.regs: dict[int, int] = {}
        self.writes: list[dict] = []
        self.reads: list[int] = []
        self.ready = 1
        self._pending = None

    async def proc(self, ctx):
        dut = self.dut
        while True:
            ctx.set(dut.reg_side.cmd.ready, self.ready)
            if self._pending is not None:
                addr, source = self._pending
                ctx.set(dut.reg_side.rsp.valid, 1)
                ctx.set(dut.reg_side.rsp.payload.fragment.data, self.regs.get(addr, 0))
                ctx.set(dut.reg_side.rsp.payload.fragment.source, source)
                ctx.set(dut.reg_side.rsp.payload.fragment.opcode, RSP_SUCCESS)
                ctx.set(dut.reg_side.rsp.payload.last, 1)
                self._pending = None
            else:
                ctx.set(dut.reg_side.rsp.valid, 0)
            # Sample at the edge, not before it: the main testbench may set
            # cmd inputs after this coroutine has run for the cycle.
            frag = dut.reg_side.cmd.payload.fragment
            _clk, _rst, valid, ready, opcode, address, data, mask, source, length, last = \
                await ctx.tick().sample(dut.reg_side.cmd.valid, dut.reg_side.cmd.ready,
                                        frag.opcode, frag.address, frag.data, frag.mask,
                                        frag.source, frag.length, dut.reg_side.cmd.payload.last)
            beat = {"opcode": opcode, "address": address, "data": data, "mask": mask,
                    "source": source, "length": length, "last": last,
                    "fire": bool(valid and ready)}
            if beat["fire"]:
                if beat["opcode"] == CMD_WRITE:
                    old = self.regs.get(beat["address"], 0)
                    new = old
                    for i in range(4):
                        if beat["mask"] >> i & 1:
                            new = (new & ~(0xFF << (8 * i))) | (beat["data"] & (0xFF << (8 * i)))
                    self.regs[beat["address"]] = new
                    self.writes.append(beat)
                else:
                    self.reads.append(beat["address"])
                    self._pending = (beat["address"], beat["source"])


# ---------------------------------------------------------------------------
# 1. directed sequences (PciFifoTest.scala)
# ---------------------------------------------------------------------------


def test_cpu_read_passthrough_returns_reg_side_data():
    """"CPU read passthrough returns regSide data"."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut)
        await ctx.tick()
        await cpu_write(ctx, dut, DIRECT_REG, 0x13579BDF)
        rsp = await wait_rsp(ctx, dut)
        assert rsp["opcode"] == RSP_SUCCESS
        await ctx.tick()
        await cpu_read(ctx, dut, DIRECT_REG, source=5)
        rsp = await wait_rsp(ctx, dut)
        assert rsp["data"] == 0x13579BDF
        assert rsp["source"] == 5 and rsp["last"] == 1
        assert slave.reads == [DIRECT_REG]
        # pendingRead cleared: a second read goes through as well
        await ctx.tick()
        assert not ctx.get(dut.probes.pending_read)

    run(dut, tb, extra=[slave.proc])


def test_float_alias_write_enqueues_converted_integer_write_and_shadow():
    """"Float alias write enqueues converted integer write and shadow"."""
    dut = make_small()
    slave = RegSlave(dut)
    seen = {}

    async def tb(ctx):
        idle(ctx, dut)
        await ctx.tick()
        await cpu_write(ctx, dut, FLOAT_REG, f2b(1.5))
        for _ in range(40):
            if ctx.get(dut.float_shadow.valid):
                seen["address"] = ctx.get(dut.float_shadow.payload.address)
                seen["raw"] = ctx.get(dut.float_shadow.payload.raw)
                break
            await ctx.tick()
        assert seen["address"] == FIFO_REG
        assert seen["raw"] == 1610612736            # 1.5 * 2**30
        await ctx.tick()
        await ctx.tick()
        assert slave.regs[FIFO_REG] == 24           # 1.5 in 12.4
        assert slave.writes[-1]["address"] == FIFO_REG
        assert slave.writes[-1]["mask"] == 0xF and slave.writes[-1]["length"] == 3

    run(dut, tb, extra=[slave.proc])


def test_fifo_command_register_responds_immediately_when_enqueued():
    """"FIFO command register responds immediately when enqueued"."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut, command_ready=[0])
        await ctx.tick()
        await cpu_write(ctx, dut, CMD_REG, 0)
        # the response is registered off cmd.fire: valid on the very next cycle
        assert ctx.get(dut.cpu_side.rsp.valid) == 1
        assert ctx.get(dut.cpu_side.rsp.payload.fragment.opcode) == RSP_SUCCESS
        for _ in range(10):
            await ctx.tick()
        assert slave.writes == []                   # still queued, not drained
        assert ctx.get(dut.fifo_empty) == 0

    run(dut, tb, extra=[slave.proc])


@pytest.mark.parametrize("address,block", [(CMD_REG, "command"), (SYNC_REG, "sync")])
def test_fifo_queues_capacity_then_stalls(address, block):
    """"FIFO can queue up to 64 commands when pipeline is stalled" /
    "CPU stalls when FIFO is full (64 entries)" -- with the ingress stage
    the module holds 64 + 16 before ``cmd.ready`` drops."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        if block == "command":
            idle(ctx, dut, command_ready=[0])
        else:
            idle(ctx, dut, pipeline_busy=1)
        await ctx.tick()
        accepted = 0
        set_cmd(ctx, dut, valid=1, opcode=CMD_WRITE, address=address, data=0)
        for _ in range(CAPACITY + 40):
            ctx.set(dut.cpu_side.cmd.payload.fragment.data, accepted)
            fired = ctx.get(dut.cpu_side.cmd.ready)
            await ctx.tick()
            if fired:
                accepted += 1
        assert accepted == CAPACITY, accepted
        assert ctx.get(dut.cpu_side.cmd.ready) == 0
        assert ctx.get(dut.pci_fifo_free) == 0
        assert slave.writes == []
        set_cmd(ctx, dut, valid=0)
        # a bypass write still goes straight through while the FIFO is full
        await cpu_write(ctx, dut, DIRECT_REG, 0xDEADBEEF)
        await ctx.tick()          # let the slave coroutine log the beat
        assert slave.regs[DIRECT_REG] == 0xDEADBEEF

    run(dut, tb, extra=[slave.proc])


def test_fifo_drains_in_order_without_bubbles_when_command_stream_becomes_ready():
    """"FIFO drains when command stream becomes ready" +
    "Command drains do not insert artificial bubbles"."""
    dut = make_small()
    slave = RegSlave(dut)
    n = 10

    async def tb(ctx):
        idle(ctx, dut, command_ready=[0])
        await ctx.tick()
        for i in range(n):
            await cpu_write(ctx, dut, CMD_REG, i + 1)
            await wait_rsp(ctx, dut)
        for _ in range(5):
            await ctx.tick()
        assert slave.writes == []
        ctx.set(dut.command_ready[0], 1)
        fires = []
        for cycle in range(40):
            beat = reg_beat(ctx, dut)
            if beat is not None and beat["fire"]:
                fires.append(cycle)
            await ctx.tick()
        assert len(fires) == n
        assert fires == list(range(fires[0], fires[0] + n)), fires   # no gaps
        assert [w["data"] for w in slave.writes] == list(range(1, n + 1))
        assert all(w["address"] == CMD_REG and w["opcode"] == CMD_WRITE for w in slave.writes)
        assert ctx.get(dut.reg_side.cmd.valid) == 0
        assert ctx.get(dut.fifo_empty) == 1

    run(dut, tb, extra=[slave.proc])


def test_fifo_no_registers_bypass_fifo_and_write_immediately():
    """"FIFO=No registers bypass FIFO and write immediately" -- and so do
    unregistered addresses (fifoBypass defaults to true)."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut, command_ready=[0])
        await ctx.tick()
        for addr in (DIRECT_REG, UNMAPPED):
            set_cmd(ctx, dut, valid=1, opcode=CMD_WRITE, address=addr, data=0xDEADBEEF)
            beat = reg_beat(ctx, dut)
            assert beat is not None and beat["fire"], "direct write must appear on regSide the same cycle"
            assert beat == {"opcode": CMD_WRITE, "address": addr, "data": 0xDEADBEEF,
                            "mask": 0xF, "length": 3, "source": 0, "last": 1, "fire": True}
            assert ctx.get(dut.probes.do_write_direct) and ctx.get(dut.probes.cpu_passthrough)
            await ctx.tick()
            set_cmd(ctx, dut, valid=0)
            await ctx.tick()      # let the slave coroutine log the beat
            assert slave.regs[addr] == 0xDEADBEEF
            assert ctx.get(dut.fifo_empty) == 1

    run(dut, tb, extra=[slave.proc])


def test_sync_registers_stall_drain_while_pipeline_busy():
    """"Sync=Yes registers stall drain when pipeline is busy"."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut, pipeline_busy=1)
        await ctx.tick()
        await cpu_write(ctx, dut, SYNC_REG, 0x12345678)
        await wait_rsp(ctx, dut)
        for _ in range(10):
            assert not ctx.get(dut.sync_drained)
            await ctx.tick()
        assert slave.writes == []
        assert ctx.get(dut.fifo_empty) == 0
        assert ctx.get(dut.pci_fifo_free) == PCI_FIFO_DEPTH - 1
        ctx.set(dut.pipeline_busy, 0)
        pulses = 0
        for _ in range(10):
            pulses += ctx.get(dut.sync_drained)
            await ctx.tick()
        assert pulses == 1
        assert slave.regs[SYNC_REG] == 0x12345678
        assert ctx.get(dut.fifo_empty) == 1
        assert ctx.get(dut.pci_fifo_free) == PCI_FIFO_DEPTH

    run(dut, tb, extra=[slave.proc])


def test_texture_writes_flow_through_fifo_alongside_register_writes():
    """"Texture writes flow through FIFO alongside register writes"."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut)
        await ctx.tick()
        ctx.set(dut.tex_write.valid, 1)
        ctx.set(dut.tex_write.payload.pci_addr, 0x12345)
        ctx.set(dut.tex_write.payload.data, 0xAABBCCDD)
        ctx.set(dut.tex_write.payload.mask, 0x5)
        while not ctx.get(dut.tex_write.ready):
            await ctx.tick()
        await ctx.tick()
        ctx.set(dut.tex_write.valid, 0)
        drains = []
        for _ in range(10):
            if ctx.get(dut.tex_drain.valid) and ctx.get(dut.tex_drain.ready):
                drains.append((ctx.get(dut.tex_drain.payload.pci_addr),
                               ctx.get(dut.tex_drain.payload.data),
                               ctx.get(dut.tex_drain.payload.mask)))
            await ctx.tick()
        assert drains == [(0x12345, 0xAABBCCDD, 0x5)]
        assert slave.writes == []                   # never on the register bus
        assert ctx.get(dut.sync_drained) == 0

    run(dut, tb, extra=[slave.proc])


def test_texture_write_yields_to_queueable_cpu_write_but_not_to_bypass():
    """``texWrite.ready := !queueableCpuWrite && stageFifo.push.ready``."""
    dut = make_small()

    async def tb(ctx):
        idle(ctx, dut)
        await ctx.tick()
        ctx.set(dut.tex_write.valid, 1)
        assert ctx.get(dut.tex_write.ready) == 1
        set_cmd(ctx, dut, valid=1, opcode=CMD_WRITE, address=FIFO_REG)
        assert ctx.get(dut.tex_write.ready) == 0
        set_cmd(ctx, dut, valid=1, opcode=CMD_WRITE, address=DIRECT_REG)
        assert ctx.get(dut.tex_write.ready) == 1
        set_cmd(ctx, dut, valid=1, opcode=CMD_READ, address=FIFO_REG)
        assert ctx.get(dut.tex_write.ready) == 1

    run(dut, tb)


def test_was_enqueued_pulses_for_register_writes_to_the_watched_address_only():
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut, was_enqueued_addr=SYNC_REG)
        await ctx.tick()
        pulses = 0

        async def count(n):
            nonlocal pulses
            for _ in range(n):
                pulses += ctx.get(dut.was_enqueued)
                await ctx.tick()

        await cpu_write(ctx, dut, FIFO_REG, 1)
        await count(8)
        assert pulses == 0
        await cpu_write(ctx, dut, SYNC_REG, 2)
        await count(8)
        assert pulses == 1
        await cpu_write(ctx, dut, SYNC_REG, 3)
        await count(8)
        assert pulses == 2
        # a bypass write to the watched address never enters the FIFO
        ctx.set(dut.was_enqueued_addr, DIRECT_REG)
        await cpu_write(ctx, dut, DIRECT_REG, 4)
        await count(8)
        assert pulses == 2
        # neither does a texture write, whatever its data says
        ctx.set(dut.was_enqueued_addr, 0)
        ctx.set(dut.tex_write.valid, 1)
        ctx.set(dut.tex_write.payload.pci_addr, 0)
        await ctx.tick()
        ctx.set(dut.tex_write.valid, 0)
        await count(8)
        assert pulses == 2

    run(dut, tb, extra=[slave.proc])


def test_read_stalls_behind_pending_read_and_reg_side_ready():
    """The two read stall terms of the ``cpuReady`` area."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut)
        await ctx.tick()
        # regSide.cmd.ready low: a read is held, a queueable write is not
        slave.ready = 0
        await ctx.tick()
        set_cmd(ctx, dut, valid=1, opcode=CMD_READ, address=FIFO_REG)
        assert ctx.get(dut.cpu_side.cmd.ready) == 0
        set_cmd(ctx, dut, valid=1, opcode=CMD_WRITE, address=FIFO_REG)
        assert ctx.get(dut.cpu_side.cmd.ready) == 1
        set_cmd(ctx, dut, valid=0)
        slave.ready = 1
        await ctx.tick()
        # a read in flight blocks the next read until the response arrives
        await cpu_read(ctx, dut, FIFO_REG)
        assert ctx.get(dut.probes.pending_read) == 1
        set_cmd(ctx, dut, valid=1, opcode=CMD_READ, address=DIRECT_REG)
        assert ctx.get(dut.cpu_side.cmd.ready) == 0
        rsp = await wait_rsp(ctx, dut)
        assert rsp["opcode"] == RSP_SUCCESS
        # the response edge cleared pendingRead; the held read is now accepted
        assert ctx.get(dut.probes.pending_read) == 0
        assert ctx.get(dut.cpu_side.cmd.ready) == 1
        await ctx.tick()
        set_cmd(ctx, dut, valid=0)
        assert ctx.get(dut.probes.pending_read) == 1
        rsp = await wait_rsp(ctx, dut)
        assert rsp["opcode"] == RSP_SUCCESS
        # Upstream quirk, reproduced: `regSide.cmd.valid := cpuPassthrough ||
        # doRegDrain` is not gated by pendingRead, so a read the CPU side is
        # stalling on is *presented* (and, with regSide ready, accepted by the
        # slave) on every stalled cycle as well as on the cycle it finally
        # fires on cpuSide.  The slave therefore logs DIRECT_REG twice here.
        assert slave.reads == [FIFO_REG, DIRECT_REG, DIRECT_REG]

    run(dut, tb, extra=[slave.proc])


def test_passthrough_has_priority_over_a_register_drain():
    """A CPU read while an entry is being drained: the drain waits."""
    dut = make_small()
    slave = RegSlave(dut)

    async def tb(ctx):
        idle(ctx, dut, command_ready=[0])
        await ctx.tick()
        await cpu_write(ctx, dut, CMD_REG, 0x11)
        for _ in range(8):
            await ctx.tick()
        # entry sits in issuedWrite, blocked; present a read at the moment it
        # becomes drainable
        ctx.set(dut.command_ready[0], 1)
        set_cmd(ctx, dut, valid=1, opcode=CMD_READ, address=DIRECT_REG)
        beat = reg_beat(ctx, dut)
        assert beat["opcode"] == CMD_READ and beat["address"] == DIRECT_REG
        assert ctx.get(dut.probes.do_reg_drain) == 0
        assert ctx.get(dut.probes.drained_write.ready) == 0
        await ctx.tick()
        set_cmd(ctx, dut, valid=0)
        for _ in range(10):
            await ctx.tick()
        assert slave.reads == [DIRECT_REG]
        assert [w["data"] for w in slave.writes] == [0x11]

    run(dut, tb, extra=[slave.proc])


def test_ordering_across_register_texture_and_float_writes():
    """Everything queued leaves in the order it was enqueued (the
    ``formalStrong`` ordering tracker's intent, for one concrete mix)."""
    dut = make_small()
    slave = RegSlave(dut)
    tex_drains = []
    expect = []

    async def watch(ctx):
        while True:
            _clk, _rst, valid, ready, addr, data = await ctx.tick().sample(
                dut.tex_drain.valid, dut.tex_drain.ready,
                dut.tex_drain.payload.pci_addr, dut.tex_drain.payload.data)
            if valid and ready:
                tex_drains.append(("tex", addr, data))

    async def tb(ctx):
        idle(ctx, dut, command_ready=[0])
        await ctx.tick()
        # Block the drain so the mix accumulates, then release.
        seq = [("reg", FIFO_REG, 0x1), ("tex", 0x100, 0xA), ("float", FLOAT_REG, f2b(-2.25)),
               ("reg", SYNC_REG, 0x2), ("tex", 0x101, 0xB), ("float", FLOAT_REG, f2b(3.0)),
               ("reg", CMD_REG, 0x3), ("reg", FIFO_REG, 0x4)]
        for kind, a, d in seq:
            if kind == "tex":
                ctx.set(dut.tex_write.valid, 1)
                ctx.set(dut.tex_write.payload.pci_addr, a)
                ctx.set(dut.tex_write.payload.data, d)
                ctx.set(dut.tex_write.payload.mask, 0xF)
                while not ctx.get(dut.tex_write.ready):
                    await ctx.tick()
                await ctx.tick()
                ctx.set(dut.tex_write.valid, 0)
                expect.append(("tex", a, d))
            else:
                await cpu_write(ctx, dut, a, d)
                if kind == "float":
                    expect.append(("reg", FIFO_REG, float_alias_ref(d, 4)[0]))
                else:
                    expect.append(("reg", a, d))
            await ctx.tick()
        # nothing left the register bus yet; only texture entries could have
        # drained, and none can: the first entry (a register write to
        # CMD_REG? no -- FIFO_REG) is drainable, so let it all go now
        ctx.set(dut.command_ready[0], 1)
        for _ in range(60):
            await ctx.tick()
        got = [("reg", w["address"], w["data"]) for w in slave.writes]
        # merge the two drain logs back into one ordered list by recording
        # the order in a shared list would need timestamps; instead check
        # each stream is in order and the union matches
        assert got == [e for e in expect if e[0] == "reg"]
        assert tex_drains == [e for e in expect if e[0] == "tex"]

    run(dut, tb, extra=[slave.proc, watch])


# ---------------------------------------------------------------------------
# 2. formal invariants under random stimulus
# ---------------------------------------------------------------------------


def _q(ctx, view):
    """Read every field of a data.View as a dict."""
    return {name: ctx.get(view[name]) for name in view.shape().members}


def check_formal(ctx, dut, shadow):
    """The ``GenerationFlags.formal`` asserts, evaluated on the current cycle's
    combinational state (PciFifo.scala:502-582)."""
    p = dut.probes
    pop = p.pci_fifo.pop
    dw = p.drained_write
    dwp = dw.payload
    cpu = dut.cpu_side.cmd
    cpu_f = cpu.payload.fragment
    reg = dut.reg_side.cmd
    reg_f = reg.payload.fragment
    g = ctx.get

    fire = lambda s: g(s.valid) and g(s.ready)
    drained_fire = fire(dw)
    is_tex = g(dw.valid) and g(dwp.is_texture)
    is_reg = g(dw.valid) and not g(dwp.is_texture)

    assert g(dut.fifo_empty) == (not g(pop.valid) and not g(p.issued_write.valid))
    assert g(p.is_tex_drain) == is_tex
    assert g(p.is_reg_drain) == is_reg
    assert g(dut.tex_drain.valid) == is_tex
    assert g(dut.float_shadow.valid) == (is_reg and drained_fire and g(dwp.has_float_shadow))
    assert g(dut.sync_drained) == (is_reg and drained_fire and g(dwp.sync_required))
    assert g(dw.ready) == ((is_reg and not g(p.cpu_passthrough) and g(reg.ready))
                           or (is_tex and g(dut.tex_drain.ready)))
    assert g(reg.valid) == (g(p.cpu_passthrough) or g(p.do_reg_drain))

    if g(p.do_write_direct):
        assert g(p.cpu_passthrough)
    if g(p.cpu_passthrough):
        assert g(reg.valid) and not g(p.do_reg_drain)
        for f in ("opcode", "address", "data", "mask", "length"):
            assert g(reg_f[f]) == g(cpu_f[f]), f
        assert g(reg.payload.last) == 1
    if g(p.do_reg_drain):
        assert g(reg.valid)
        assert g(reg_f.opcode) == CMD_WRITE
        assert g(reg_f.address) == g(dwp.address)
        assert g(reg_f.data) == g(dwp.data)
        assert g(reg_f.mask) == g(dwp.mask)
        assert g(reg_f.length) == 3 and g(reg.payload.last) == 1

    if g(pop.payload.sync_required) and g(dut.pipeline_busy):
        assert not drained_fire or g(dw.valid), "haltWhen blocks the pop, not the issued beat"
        assert not fire(pop)
    if g(p.command_stream_blocked):
        assert not fire(pop)
    # (the Scala writes `assert(!drainedWrite.fire)` for both; with the m2sPipe
    # issue stage that literally holds only for the *pop* -- an already-issued
    # beat drains regardless, and that is the RTL on both sides.)

    if g(p.pending_read):
        rsp = dut.cpu_side.rsp
        rrsp = dut.reg_side.rsp
        assert g(rsp.valid) == g(rrsp.valid)
        assert g(rsp.payload.fragment.data) == g(rrsp.payload.fragment.data)
        assert g(rsp.payload.fragment.opcode) == g(rrsp.payload.fragment.opcode)
        assert g(rsp.payload.fragment.source) == g(rrsp.payload.fragment.source)
    if g(cpu.valid) and g(cpu_f.opcode) == CMD_READ and g(p.pending_read):
        assert not g(cpu.ready)

    if g(p.float_write_request) and not g(p.float_convert_in_flight):
        assert g(p.converter.op.valid)

    if g(p.float_convert_in_flight) and g(p.converter.result.valid):
        raw = to_signed(g(p.converter.result.payload.number.raw), _RAW_W)
        corrected = float_corrected_ref(raw, g(p.float_convert_shift))
        q = _q(ctx, p.queued_write_tx.payload)
        assert g(p.queued_write_tx.valid)
        assert q["address"] == g(p.float_convert_target_addr)
        assert q["data"] == corrected & 0xFFFFFFFF
        assert q["mask"] == 0xF
        assert q["sync_required"] == 0 and q["is_texture"] == 0 and q["tex_pci_addr"] == 0
        assert q["has_float_shadow"] == 1
        assert to_signed(q["float_shadow_raw"], 50) == to_signed(raw, 50)

    if g(dut.float_shadow.valid):
        assert is_reg and drained_fire and g(dwp.has_float_shadow)
        assert g(dut.float_shadow.payload.address) == g(dwp.address)
        assert g(dut.float_shadow.payload.raw) == g(dwp.float_shadow_raw)
        # PciFifoFormalTest (formalStrong): the only alias targets FIFO_REG
        assert g(dut.float_shadow.payload.address) == FIFO_REG

    # formalStrong occupancy, with the timing made explicit (module docstring)
    assert PCI_FIFO_DEPTH - g(dut.pci_fifo_free) == shadow["pushes"] - shadow["pops"]
    assert g(p.issued_write.valid) == shadow["pops"] - shadow["drains"]
    assert shadow["pops"] - shadow["drains"] in (0, 1)


def _random_run(seed, cycles, *, valid_density=0.5, ready_density=0.6):
    dut = make_small()
    rng = random.Random(seed)
    addresses = [CMD_REG, FIFO_REG, FLOAT_REG, DIRECT_REG, SYNC_REG, UNMAPPED, 0x018]
    covered = {"float": 0, "sync_block": 0, "cmd_block": 0, "tex": 0, "read": 0,
               "full": 0, "shadow": 0, "was_enqueued": 0, "direct_while_drain": 0}

    async def tb(ctx):
        p = dut.probes            # exists once Simulator() has elaborated
        # counters for the occupancy invariant (registered view: updated at the edge)
        shadow = {"pushes": 0, "pops": 0, "drains": 0}
        expect = deque()          # queuedWriteTx payloads, in push order
        cpu_armed = False
        tex_armed = False
        read_outstanding = False
        rsp_now = False
        idle(ctx, dut)
        await ctx.tick()
        for cycle in range(cycles):
            # -- free inputs -------------------------------------------------
            ctx.set(dut.pipeline_busy, int(rng.random() < 0.4))
            ctx.set(dut.command_ready[0], int(rng.random() < 0.5))
            ctx.set(dut.reg_side.cmd.ready, int(rng.random() < ready_density))
            ctx.set(dut.tex_drain.ready, int(rng.random() < ready_density))
            ctx.set(dut.cpu_side.rsp.ready, 1)
            ctx.set(dut.was_enqueued_addr, rng.choice(addresses))
            # regSide.rsp: only while a read is outstanding (formal assumption)
            rsp_now = read_outstanding and rng.random() < 0.5
            ctx.set(dut.reg_side.rsp.valid, int(rsp_now))
            ctx.set(dut.reg_side.rsp.payload.fragment.data, rng.getrandbits(32))
            ctx.set(dut.reg_side.rsp.payload.fragment.source, rng.getrandbits(4))
            ctx.set(dut.reg_side.rsp.payload.fragment.opcode, rng.getrandbits(1))
            # -- cpuSide.cmd (formalAssumesSlave: hold while stalled) ----------
            if not cpu_armed:
                cpu_armed = rng.random() < valid_density
                if cpu_armed:
                    opcode = CMD_READ if rng.random() < 0.15 else CMD_WRITE
                    addr = rng.choice(addresses)
                    data = rng.choice([rng.getrandbits(32), f2b(rng.uniform(-3000, 3000)),
                                       f2b(rng.uniform(-1, 1))])
                    set_cmd(ctx, dut, valid=1, opcode=opcode, address=addr, data=data,
                            mask=rng.getrandbits(4), source=rng.getrandbits(4))
            if not cpu_armed:
                set_cmd(ctx, dut, valid=0)
            # -- texWrite ------------------------------------------------------
            if not tex_armed:
                tex_armed = rng.random() < 0.3
                if tex_armed:
                    ctx.set(dut.tex_write.valid, 1)
                    ctx.set(dut.tex_write.payload.pci_addr, rng.getrandbits(23))
                    ctx.set(dut.tex_write.payload.data, rng.getrandbits(32))
                    ctx.set(dut.tex_write.payload.mask, rng.getrandbits(4))
            if not tex_armed:
                ctx.set(dut.tex_write.valid, 0)

            # -- invariants on the settled combinational state ------------------
            check_formal(ctx, dut, shadow)

            # -- sample the fires this edge will commit --------------------------
            g = ctx.get
            fire = lambda s: g(s.valid) and g(s.ready)
            cpu_fire = fire(dut.cpu_side.cmd)
            tex_fire = fire(dut.tex_write)
            push = fire(p.queued_write_tx)
            pop = fire(p.pci_fifo.pop)
            drain = fire(p.drained_write)
            reg_fire = fire(dut.reg_side.cmd)
            if push:
                expect.append(_q(ctx, p.queued_write_tx.payload))
                if g(dut.was_enqueued):
                    covered["was_enqueued"] += 1
            if drain:
                assert expect, "drained more than was pushed"
                assert _q(ctx, p.drained_write.payload) == expect.popleft(), \
                    "drain payload differs from the pushed one (ordering/lossless)"
            if g(dut.float_shadow.valid):
                covered["shadow"] += 1
            if reg_fire and g(dut.reg_side.cmd.payload.fragment.opcode) == CMD_READ:
                # (may already be outstanding: a read the CPU side is stalling
                # on stays presented on regSide, see the pending-read test)
                read_outstanding = True
                covered["read"] += 1
                # PciFifoFormalTest: a read on regSide is the cpu's, unmodified
                assert g(dut.reg_side.cmd.payload.fragment.address) == g(dut.cpu_side.cmd.payload.fragment.address)
                assert g(dut.reg_side.cmd.payload.fragment.source) == g(dut.cpu_side.cmd.payload.fragment.source)
            if rsp_now:
                read_outstanding = False
            if cpu_fire and g(dut.cpu_side.cmd.payload.fragment.opcode) == CMD_WRITE \
                    and g(dut.cpu_side.cmd.payload.fragment.address) == DIRECT_REG:
                # PciFifoFormalTest: a bypass write is on regSide *now*.  The
                # formal DUT ties regSide.cmd.ready high; here it is random,
                # and `cmd.ready` is 1 for a bypass write whatever regSide
                # says (PciFifo.scala:416), so the beat is *presented* on
                # every such cycle and fires only when the slave is ready --
                # a direct write into a stalled slave is dropped upstream too.
                assert g(dut.reg_side.cmd.valid)
                assert reg_fire == g(dut.reg_side.cmd.ready)
                rf = dut.reg_side.cmd.payload.fragment
                cf = dut.cpu_side.cmd.payload.fragment
                assert g(rf.address) == g(cf.address) and g(rf.data) == g(cf.data)
                assert g(rf.mask) == g(cf.mask) and g(rf.opcode) == CMD_WRITE
                if g(p.is_reg_drain):
                    covered["direct_while_drain"] += 1
            if g(p.float_convert_in_flight):
                covered["float"] += 1
            if g(p.pci_fifo.pop.valid) and g(p.pci_fifo.pop.payload.sync_required) and g(dut.pipeline_busy):
                covered["sync_block"] += 1
            if g(p.command_stream_blocked):
                covered["cmd_block"] += 1
            if tex_fire:
                covered["tex"] += 1
            if g(dut.pci_fifo_free) == 0:
                covered["full"] += 1

            await ctx.tick()

            shadow["pushes"] += int(push)
            shadow["pops"] += int(pop)
            shadow["drains"] += int(drain)
            if cpu_fire:
                cpu_armed = False
            if tex_fire:
                tex_armed = False

    run(dut, tb)
    return covered


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_holds_formal_invariants(seed):
    covered = _random_run(seed, 3000)
    for key in ("float", "sync_block", "cmd_block", "tex", "read", "shadow"):
        assert covered[key] > 0, (key, covered)


def test_random_backpressure_fills_the_fifo():
    """Slow consumers: the 64-entry FIFO must actually fill and stay lossless."""
    covered = _random_run(7, 4000, valid_density=0.9, ready_density=0.08)
    assert covered["full"] > 0, covered
    assert covered["was_enqueued"] > 0, covered


# ---------------------------------------------------------------------------
# 3. the float datapath against the reference, per shift
# ---------------------------------------------------------------------------


def _float_dut(frac_bits: int) -> tuple[PciFifo, int, int]:
    alias, target = 0x0A0, 0x020
    cats = {target: FIFO_NO_SYNC, alias: FIFO_NO_SYNC}
    return PciFifo(BUS, cats, {alias: FloatAliasConfig(target, 30 - (30 - frac_bits), frac_bits)},
                   ()), alias, target


FLOAT_PATTERNS = [
    0.0, -0.0, 1.0, -1.0, 1.5, -2.25, 0.5, 3.0, 123.456, -123.456, 2.0 ** -8 + 2.0 ** -31,
    -(2.0 ** -8 + 2.0 ** -31), 2.0 ** 19, -(2.0 ** 19), 2.0 ** 20, -(2.0 ** 20), 1e-9, -1e-9,
    65535.9375, -65535.9375, 2.0 ** -31,
]
FLOAT_BITS = [f2b(x) for x in FLOAT_PATTERNS] + [0x7F800000, 0xFF800000, 0x7FC00000,
                                                 0x00000001, 0x80000001, 0x00400000]


@pytest.mark.parametrize("frac_bits", [4, 12, 18, 30])
def test_float_alias_datapath_matches_reference(frac_bits):
    """Shift 26 / 18 / 12 (the register set's) and 0 (no remainder correction)."""
    dut, alias, target = _float_dut(frac_bits)
    slave = RegSlave(dut)
    shadows = []

    async def watch(ctx):
        while True:
            _clk, _rst, valid, addr, raw = await ctx.tick().sample(
                dut.float_shadow.valid, dut.float_shadow.payload.address,
                dut.float_shadow.payload.raw)
            if valid:
                shadows.append((addr, raw))

    async def tb(ctx):
        idle(ctx, dut)
        await ctx.tick()
        rng = random.Random(frac_bits)
        bits = FLOAT_BITS + [rng.getrandbits(32) for _ in range(40)]
        for b in bits:
            await cpu_write(ctx, dut, alias, b)
        for _ in range(4 * len(bits)):      # conversions drain at ~1 per 3 cycles
            await ctx.tick()
        assert len(slave.writes) == len(bits) and len(shadows) == len(bits)
        for b, w, (sa, sraw) in zip(bits, slave.writes, shadows):
            data, shadow = float_alias_ref(b, frac_bits)
            assert w["address"] == target and w["mask"] == 0xF
            assert w["data"] == data, (hex(b), hex(w["data"]), hex(data))
            assert sa == target and sraw == shadow, (hex(b), sraw, shadow)

    run(dut, tb, extra=[slave.proc, watch])


# ---------------------------------------------------------------------------
# elaboration / structure
# ---------------------------------------------------------------------------


def test_real_tables_elaborate_and_match_regdef():
    dut = make_real()
    assert len(dut.categories) == 158
    assert len(dut.fifo_queue_addresses) == 143
    assert len(dut.sync_addresses) == 71
    assert len(dut.float_aliases) == 30
    assert dut.command_addresses == (0x080, 0x100, 0x120, 0x124, 0x128)
    assert len(dut.command_ready) == 5
    assert len(dut.pci_fifo_free) == 7
    assert len(dut.float_shadow.payload.raw) == 50
    Fragment.get(dut, None)


def test_no_command_addresses_means_no_command_ready_port():
    dut = PciFifo(BUS, {FIFO_REG: FIFO_NO_SYNC}, {}, ())
    assert not hasattr(dut, "command_ready")
    Fragment.get(dut, None)


def test_reset_less_registers_match_spinal():
    """Plain Spinal ``Reg`` / ``RegNext`` -> ``reset_less``; ``RegInit`` -> reset."""
    frag = Fragment.get(make_small(), None)
    names = {}
    for sig in _all_signals(frag):
        names.setdefault(sig.name, sig)
    for name in ("floatConvert_targetAddr", "floatConvert_shift",
                 "io_cpuSide_cmd_payload_fragment_source_regNext"):
        assert name in names, name
        assert names[name].reset_less, f"{name} must be reset_less (plain Spinal Reg)"
    for name in ("floatConvert_inFlight", "response_pendingRead", "_zz_io_cpuSide_rsp_valid"):
        assert name in names, name
        assert not names[name].reset_less, f"{name} is a Spinal RegInit and must reset"


def _all_signals(frag):
    out = []
    for stmt_domain in frag.statements.values():
        for sig in stmt_domain._lhs_signals():
            out.append(sig)
    for sub, _name, _src in frag.subfragments:
        out.extend(_all_signals(sub))
    return out


SPINAL_V = Path(__file__).resolve().parents[2] / "fpga" / "rtl" / "diff" / "PciFifo_spinal.v"


@pytest.mark.skipif(not SPINAL_V.is_file(), reason="generated PciFifo_spinal.v absent")
def test_decode_tables_match_generated_spinal_verilog():
    """The decode constants in the Spinal netlist are exactly regdef's."""
    v = SPINAL_V.read_text()
    cond = {m.group(1): int(m.group(2), 16) for m in re.finditer(
        r"assign (when_PciFifo_l\d+(?:_\d+)?) = \(io_cpuSide_cmd_payload_fragment_address"
        r" == 12'h([0-9a-f]+)\);", v)}
    tgt = {cond[m.group(1)]: int(m.group(2), 16) for m in re.finditer(
        r"if\((when_PciFifo_l\d+(?:_\d+)?)\) begin\s*decodeInfo_floatTargetAddr = 12'h([0-9a-f]+);", v)}
    sh = {cond[m.group(1)]: int(m.group(2), 16) for m in re.finditer(
        r"if\((when_PciFifo_l\d+(?:_\d+)?)\) begin\s*decodeInfo_floatShiftAmount = 5'h([0-9a-f]+);", v)}
    byp = {int(x, 16) for x in re.findall(r"12'h([0-9a-f]+) : begin\s*pciFifo_fifoBypass = 1'b0;", v)}
    syn = {int(x, 16) for x in re.findall(r"12'h([0-9a-f]+) : begin\s*pciFifo_syncRequired = 1'b1;", v)}
    cmd = [int(x, 16) for x in re.findall(
        r"assign when_PciFifo_l\d+(?:_\d+)? = \(pciQueue_fifo_io_pop_payload_address == 12'h([0-9a-f]+)\);", v)]
    dut = make_real()
    assert {a: (al.target_addr, 30 - al.frac_bits) for a, al in dut.float_aliases.items()} \
        == {a: (tgt[a], sh[a]) for a in tgt}
    assert byp == set(dut.fifo_queue_addresses)
    assert syn == set(dut.sync_addresses)
    assert tuple(cmd) == dut.command_addresses
    Fragment.get(dut, None)
