"""Phase 2.3 unit tests for :mod:`avoodoo.core.cpu_frontdoor`.

There is no ``CoreCpuFrontdoorTest.scala`` upstream; the module is covered
there only through the system harness.  These tests pin the decode table
that :data:`avoodoo.frontend.regdef.DECODE_QUIRKS` documents, against a
Python model of the same rules:

* ``address[23:22]``: 0 register set, 1 LFB, 2/3 texture memory;
* the register address is rebuilt as ``addr[21:14] ## 4'b0 ## addr[9:0]``
  (chip-select field forced to zero), the LFB address is the low 22 bits;
* a texture **write** carries the raw 23-bit PCI offset on ``texWrite`` and
  is acknowledged by the frontdoor itself one cycle later; a texture
  **read** is relocated by ``(texBaseAddr[18:0] << 3) + offset``;
* ``cmd.ready`` is the selected destination's, nobody else's;
* responses: the pending texture-write ack first, then ``regBus``,
  ``lfbBus``, ``texReadBus`` in that fixed order, and an unselected bus is
  back-pressured (``rsp.ready = 0``).
"""

import random

from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.core.cpu_frontdoor import CoreCpuFrontdoor
from avoodoo.lib.membus import CMD_READ, CMD_WRITE

PERIOD = 1e-6
C = Config.voodoo1()

REG, LFB, TEX_WRITE, TEX_READ = "reg", "lfb", "tex_write", "tex_read"


# ---------------------------------------------------------------------------
# Python model (CoreCpuFrontdoor.scala:22-48 / DECODE_QUIRKS)
# ---------------------------------------------------------------------------


def decode(address: int, opcode: int, tex_base_addr: int = 0) -> tuple[str, int]:
    """``(destination, translated address)`` for one command."""
    target = (address >> 22) & 0x3
    if target == 0:
        return REG, ((address >> 14) & 0xFF) << 14 | (address & 0x3FF)
    if target == 1:
        return LFB, address & 0x3FFFFF
    offset = address & 0x7FFFFF
    if opcode & 1:
        return TEX_WRITE, offset
    base = tex_base_addr & 0x7FFFF
    return TEX_READ, ((base << 3) + offset) & ((1 << 26) - 1)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _bus_cmd(ctx, dut, *, valid=1, opcode=CMD_WRITE, address=0, data=0, mask=0xF,
             source=0, length=3, last=1):
    cmd = dut.cpu_bus.cmd
    f = cmd.payload.fragment
    ctx.set(cmd.valid, valid)
    ctx.set(cmd.payload.last, last)
    ctx.set(f.opcode, opcode)
    ctx.set(f.address, address)
    ctx.set(f.data, data)
    ctx.set(f.mask, mask)
    ctx.set(f.source, source)
    ctx.set(f.length, length)


def _dest_readies(ctx, dut, reg=1, lfb=1, tex_write=1, tex_read=1):
    ctx.set(dut.reg_bus.cmd.ready, reg)
    ctx.set(dut.lfb_bus.cmd.ready, lfb)
    ctx.set(dut.tex_write.ready, tex_write)
    ctx.set(dut.tex_read_bus.cmd.ready, tex_read)


def _valids(ctx, dut) -> dict:
    return {
        REG: ctx.get(dut.reg_bus.cmd.valid),
        LFB: ctx.get(dut.lfb_bus.cmd.valid),
        TEX_WRITE: ctx.get(dut.tex_write.valid),
        TEX_READ: ctx.get(dut.tex_read_bus.cmd.valid),
    }


def _addresses(ctx, dut) -> dict:
    return {
        REG: ctx.get(dut.reg_bus.cmd.payload.fragment.address),
        LFB: ctx.get(dut.lfb_bus.cmd.payload.fragment.address),
        TEX_WRITE: ctx.get(dut.tex_write.payload.pci_addr),
        TEX_READ: ctx.get(dut.tex_read_bus.cmd.payload.fragment.address),
    }


def _rsp(ctx, bus, *, valid=0, data=0, source=0, opcode=0, last=1):
    r = bus.rsp
    ctx.set(r.valid, valid)
    ctx.set(r.payload.last, last)
    ctx.set(r.payload.fragment.opcode, opcode)
    ctx.set(r.payload.fragment.data, data)
    ctx.set(r.payload.fragment.source, source)


def run(tb):
    dut = CoreCpuFrontdoor(C)
    sim = Simulator(dut)
    sim.add_clock(PERIOD)

    async def bench(ctx):
        await tb(ctx, dut)

    sim.add_testbench(bench)
    sim.run()


# ---------------------------------------------------------------------------
# the decode table
# ---------------------------------------------------------------------------

DIRECTED = [
    # (address, opcode, tex_base_addr) -- one per DECODE_QUIRKS line
    (0x000000, CMD_WRITE, 0),
    (0x3FFFFF, CMD_READ, 0),
    (0x000104, CMD_WRITE, 0),          # fbiInit0, chip 0
    (0x000904, CMD_WRITE, 0),          # fbiInit0, chip 2 -> aliases onto 0x104
    (0x003D04, CMD_WRITE, 0),          # chip 15 -> same
    (0x200088, CMD_WRITE, 0),          # bit 21 (remap) survives into regBus
    (0x004104, CMD_WRITE, 0),          # wrap 1 survives (AddressRemapper drops it)
    (0x400000, CMD_WRITE, 0),
    (0x7FFFFF, CMD_READ, 0),
    (0x432100, CMD_READ, 0),           # fbiMemSize probe
    (0x800000, CMD_WRITE, 0),
    (0x800000, CMD_READ, 0),
    (0xFFFFFF, CMD_WRITE, 0),
    (0xFFFFFF, CMD_READ, 0),
    (0xC00010, CMD_READ, 0x123456),    # texBaseAddr relocation
    (0xC00010, CMD_READ, 0xFFFFFF),    # only [18:0] of texBaseAddr counts
    (0xFFFFFF, CMD_READ, 0x07FFFF),    # (0x7ffff << 3) + 0x7fffff needs 24 bits
]


def test_decode_table_directed_and_random():
    rng = random.Random(2301)
    cases = list(DIRECTED) + [
        (rng.getrandbits(24), rng.getrandbits(1), rng.getrandbits(24)) for _ in range(400)
    ]

    async def tb(ctx, dut):
        _dest_readies(ctx, dut)
        for address, opcode, tex_base in cases:
            data, mask, source = rng.getrandbits(32), rng.getrandbits(4), rng.getrandbits(4)
            ctx.set(dut.tex_base_addr, tex_base)
            _bus_cmd(ctx, dut, opcode=opcode, address=address, data=data, mask=mask,
                     source=source)
            dest, want_addr = decode(address, opcode, tex_base)
            valids = _valids(ctx, dut)
            assert valids == {k: int(k == dest) for k in valids}, (hex(address), opcode)
            assert _addresses(ctx, dut)[dest] == want_addr, (hex(address), opcode, dest)
            assert ctx.get(dut.cpu_bus.cmd.ready) == 1
            # the rest of the command is copied verbatim to the Bmb destinations
            if dest == REG:
                f = dut.reg_bus.cmd.payload.fragment
            elif dest == LFB:
                f = dut.lfb_bus.cmd.payload.fragment
            elif dest == TEX_READ:
                f = dut.tex_read_bus.cmd.payload.fragment
            else:
                f = None
            if f is not None:
                assert ctx.get(f.opcode) == opcode
                assert ctx.get(f.data) == data
                assert ctx.get(f.mask) == mask
                assert ctx.get(f.source) == source
                assert ctx.get(f.length) == 3
            else:
                assert ctx.get(dut.tex_write.payload.data) == data
                assert ctx.get(dut.tex_write.payload.mask) == mask
            await ctx.tick()
            # a tex write leaves a one-cycle acknowledgement behind; drain it
            if dest == TEX_WRITE:
                _bus_cmd(ctx, dut, valid=0)
                assert ctx.get(dut.cpu_bus.rsp.valid) == 1
                assert ctx.get(dut.cpu_bus.rsp.payload.fragment.source) == source
                await ctx.tick()
                assert ctx.get(dut.cpu_bus.rsp.valid) == 0

    run(tb)


def test_idle_bus_drives_no_destination():
    async def tb(ctx, dut):
        _dest_readies(ctx, dut)
        for address in (0x000000, 0x400000, 0x800000):
            for opcode in (CMD_READ, CMD_WRITE):
                _bus_cmd(ctx, dut, valid=0, opcode=opcode, address=address)
                assert not any(_valids(ctx, dut).values())
                assert ctx.get(dut.invalidate) == 0
                await ctx.tick()

    run(tb)


# ---------------------------------------------------------------------------
# ready mux
# ---------------------------------------------------------------------------


def test_cmd_ready_is_the_selected_destinations_only():
    rng = random.Random(7)

    async def tb(ctx, dut):
        for _ in range(300):
            address, opcode = rng.getrandbits(24), rng.getrandbits(1)
            readies = {k: rng.getrandbits(1) for k in (REG, LFB, TEX_WRITE, TEX_READ)}
            _dest_readies(ctx, dut, readies[REG], readies[LFB], readies[TEX_WRITE],
                          readies[TEX_READ])
            _bus_cmd(ctx, dut, valid=rng.getrandbits(1), opcode=opcode, address=address)
            dest, _ = decode(address, opcode)
            # combinational: ready does not depend on valid
            assert ctx.get(dut.cpu_bus.cmd.ready) == readies[dest]
            await ctx.tick()

    run(tb)


# ---------------------------------------------------------------------------
# texture-write acknowledgement + invalidate
# ---------------------------------------------------------------------------


def test_tex_write_ack_and_invalidate():
    async def tb(ctx, dut):
        _dest_readies(ctx, dut, tex_write=0)
        _bus_cmd(ctx, dut, opcode=CMD_WRITE, address=0x900000, source=0xA)
        # not accepted while texWrite is back-pressured: no invalidate, no ack
        assert ctx.get(dut.invalidate) == 0
        assert ctx.get(dut.cpu_bus.cmd.ready) == 0
        await ctx.tick()
        assert ctx.get(dut.cpu_bus.rsp.valid) == 0

        _dest_readies(ctx, dut, tex_write=1)
        assert ctx.get(dut.invalidate) == 1         # fires with cmd.fire, same cycle
        assert ctx.get(dut.cpu_bus.rsp.valid) == 0  # ack is registered
        await ctx.tick()
        _bus_cmd(ctx, dut, valid=0)
        assert ctx.get(dut.invalidate) == 0
        assert ctx.get(dut.cpu_bus.rsp.valid) == 1
        assert ctx.get(dut.cpu_bus.rsp.payload.fragment.source) == 0xA
        assert ctx.get(dut.cpu_bus.rsp.payload.fragment.opcode) == 0
        assert ctx.get(dut.cpu_bus.rsp.payload.last) == 1
        assert ctx.get(dut.cpu_bus.rsp.payload.fragment.data) == 0
        # one cycle only, whatever rsp.ready says (Scala quirk, kept)
        ctx.set(dut.cpu_bus.rsp.ready, 0)
        await ctx.tick()
        assert ctx.get(dut.cpu_bus.rsp.valid) == 0

    run(tb)


def test_back_to_back_tex_writes_lose_the_second_ack():
    """``when(texWriteAccepted) {pending := True}; when(pending) {pending := False}``
    -- last assignment wins, so a write accepted while the previous ack is
    being presented clears the flag.  Documented behaviour, not a target."""

    async def tb(ctx, dut):
        _dest_readies(ctx, dut)
        ctx.set(dut.cpu_bus.rsp.ready, 1)
        _bus_cmd(ctx, dut, opcode=CMD_WRITE, address=0x800000, source=1)
        await ctx.tick()                              # write 1 accepted
        _bus_cmd(ctx, dut, opcode=CMD_WRITE, address=0x800004, source=2)
        assert ctx.get(dut.cpu_bus.rsp.valid) == 1    # ack 1
        assert ctx.get(dut.cpu_bus.rsp.payload.fragment.source) == 1
        await ctx.tick()                              # write 2 accepted, ack 1 cleared
        _bus_cmd(ctx, dut, valid=0)
        assert ctx.get(dut.cpu_bus.rsp.valid) == 0    # ack 2 never appears
        await ctx.tick()
        assert ctx.get(dut.cpu_bus.rsp.valid) == 0

    run(tb)


# ---------------------------------------------------------------------------
# response routing
# ---------------------------------------------------------------------------


def test_rsp_priority_and_backpressure():
    rng = random.Random(11)

    async def tb(ctx, dut):
        _dest_readies(ctx, dut)
        _bus_cmd(ctx, dut, valid=0)
        buses = {REG: dut.reg_bus, LFB: dut.lfb_bus, TEX_READ: dut.tex_read_bus}
        for _ in range(500):
            v = {k: rng.getrandbits(1) for k in buses}
            d = {k: rng.getrandbits(32) for k in buses}
            s = {k: rng.getrandbits(4) for k in buses}
            o = {k: rng.getrandbits(1) for k in buses}
            l = {k: rng.getrandbits(1) for k in buses}
            for k, b in buses.items():
                _rsp(ctx, b, valid=v[k], data=d[k], source=s[k], opcode=o[k], last=l[k])
            cpu_ready = rng.getrandbits(1)
            ctx.set(dut.cpu_bus.rsp.ready, cpu_ready)

            selected = next((k for k in (REG, LFB, TEX_READ) if v[k]), None)
            r = dut.cpu_bus.rsp
            if selected is None:
                assert ctx.get(r.valid) == 0
                assert ctx.get(r.payload.fragment.data) == 0
                assert ctx.get(r.payload.fragment.source) == 0
                assert ctx.get(r.payload.fragment.opcode) == 0
                assert ctx.get(r.payload.last) == 1
            else:
                assert ctx.get(r.valid) == 1
                assert ctx.get(r.payload.fragment.data) == d[selected]
                assert ctx.get(r.payload.fragment.source) == s[selected]
                assert ctx.get(r.payload.fragment.opcode) == o[selected]
                assert ctx.get(r.payload.last) == l[selected]
            for k, b in buses.items():
                want = cpu_ready if k == selected else 0
                assert ctx.get(b.rsp.ready) == want, (k, selected)
            await ctx.tick()

    run(tb)


def test_pending_tex_ack_beats_every_downstream_response():
    async def tb(ctx, dut):
        _dest_readies(ctx, dut)
        ctx.set(dut.cpu_bus.rsp.ready, 1)
        for b in (dut.reg_bus, dut.lfb_bus, dut.tex_read_bus):
            _rsp(ctx, b, valid=1, data=0xDEADBEEF, source=0xF)
        _bus_cmd(ctx, dut, opcode=CMD_WRITE, address=0x800000, source=5)
        await ctx.tick()
        _bus_cmd(ctx, dut, valid=0)
        r = dut.cpu_bus.rsp
        assert ctx.get(r.valid) == 1
        assert ctx.get(r.payload.fragment.source) == 5
        assert ctx.get(r.payload.fragment.data) == 0
        for b in (dut.reg_bus, dut.lfb_bus, dut.tex_read_bus):
            assert ctx.get(b.rsp.ready) == 0
        await ctx.tick()
        # ack gone: regBus wins over the other two
        assert ctx.get(r.payload.fragment.source) == 0xF
        assert ctx.get(dut.reg_bus.rsp.ready) == 1
        assert ctx.get(dut.lfb_bus.rsp.ready) == 0
        assert ctx.get(dut.tex_read_bus.rsp.ready) == 0

    run(tb)
