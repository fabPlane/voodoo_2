"""Phase 2.3 unit tests for :mod:`avoodoo.frontend.address_remapper`.

Ports the intent of ``ext/SpinalVoodoo/test/AddressRemapperTest.scala``: its
ten ``testRemap`` groups become :func:`translate` calls over the same 40
address pairs, driven through the real ``MemBus`` command payload instead of
Spinal's ``dut.io.input.cmd.address #= ...``.

The Scala test only ever looks at ``io.output.cmd.address``.  Three things it
does not check are added here, because the component is a bus splice and
getting them wrong is silent:

* the rest of the command payload (opcode, length, data, mask, last, source)
  is passed through untouched -- ``BmbRouting.driveCmdFrom`` copies it;
* ``valid``/``ready`` are a straight wire in both directions, so the splice
  adds no latency and no bubble;
* the response channel is connected back unchanged.

The table itself is cross-checked against
:data:`avoodoo.frontend.regdef.REMAP_TABLE`, which plan task 2.1 extracted
from ``RegisterBank.scala``/``AddressRemapper.scala`` independently.
"""

import gc

import pytest
from amaranth.hdl import Elaboratable, Fragment
from amaranth.sim import Simulator

from avoodoo.config import Config
from avoodoo.frontend.address_remapper import (
    REMAP_RANGE_HI,
    REMAP_RANGE_LO,
    AddressRemapper,
    bmb_params,
    external_bmb_params,
)
from avoodoo.frontend.regdef import REMAP_TABLE

#: Settling delay between stimulus and sampling.  There is no clock here:
#: ``AddressRemapper`` is purely combinational, so the design has no ``sync``
#: domain at all and ``Simulator.add_clock`` would raise.
SETTLE = 1e-6
C = Config.voodoo1()


def make_dut():
    return AddressRemapper(external_bmb_params(C), bmb_params(C))


# ---------------------------------------------------------------------------
# the exact address pairs of AddressRemapperTest.scala
# ---------------------------------------------------------------------------

#: ``test("Standard addresses pass through unchanged")``
PASS_THROUGH = [
    (0x000088, 0x088),   # fvertexAx
    (0x0000A0, 0x0A0),   # fstartR
    (0x0000C0, 0x0C0),   # fdRdX
    (0x000100, 0x100),   # fbiInit0
]

#: the nine remapped-value tests, in the Scala's order
REMAPPED = [
    # vertices stay put
    (0x200088, 0x088), (0x20008C, 0x08C), (0x200090, 0x090),
    (0x200094, 0x094), (0x200098, 0x098), (0x20009C, 0x09C),
    # red
    (0x2000A0, 0x0A0), (0x2000A4, 0x0C0), (0x2000A8, 0x0E0),
    # green
    (0x2000AC, 0x0A4), (0x2000B0, 0x0C4), (0x2000B4, 0x0E4),
    # blue
    (0x2000B8, 0x0A8), (0x2000BC, 0x0C8), (0x2000C0, 0x0E8),
    # z
    (0x2000C4, 0x0AC), (0x2000C8, 0x0CC), (0x2000CC, 0x0EC),
    # alpha
    (0x2000D0, 0x0B0), (0x2000D4, 0x0D0), (0x2000D8, 0x0F0),
    # s
    (0x2000DC, 0x0B4), (0x2000E0, 0x0D4), (0x2000E4, 0x0F4),
    # t
    (0x2000E8, 0x0B8), (0x2000EC, 0x0D8), (0x2000F0, 0x0F8),
    # w
    (0x2000F4, 0x0BC), (0x2000F8, 0x0DC), (0x2000FC, 0x0FC),
]

#: ``test("Bit 21 set but outside remap range clears bit 21")``
BIT21_OUT_OF_RANGE = [
    (0x200000, 0x000),   # status
    (0x200004, 0x004),   # intrCtrl
    (0x200100, 0x100),   # fbiInit0
    (0x200200, 0x200),
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def set_cmd(ctx, dut, *, address, opcode=1, length=3, data=0x12345678,
            mask=0xF, last=1, source=0):
    frag = dut.input.cmd.payload.fragment
    ctx.set(dut.input.cmd.payload.last, last)
    ctx.set(frag.address, address)
    ctx.set(frag.opcode, opcode)
    ctx.set(frag.length, length)
    ctx.set(frag.data, data)
    ctx.set(frag.mask, mask)
    ctx.set(frag.source, source)


def translate(addresses):
    """Drive each address in and return the ``output.cmd.address`` seen.

    Combinational, exactly as the Scala test assumes (``sleep(1)`` then read),
    so a whole batch runs inside one simulation.
    """
    dut = make_dut()
    got = []

    async def tb(ctx):
        ctx.set(dut.input.cmd.valid, 1)
        ctx.set(dut.output.cmd.ready, 1)
        for address in addresses:
            set_cmd(ctx, dut, address=address)
            got.append(ctx.get(dut.output.cmd.payload.fragment.address))
            await ctx.delay(SETTLE)

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    return got


def check(pairs):
    ins = [a for a, _ in pairs]
    want = [b for _, b in pairs]
    got = translate(ins)
    bad = [f"0x{a:06x} -> got 0x{g:03x}, want 0x{w:03x}"
           for (a, w), g in zip(pairs, got) if g != w]
    assert not bad, "; ".join(bad)


# ---------------------------------------------------------------------------
# the ported Scala cases
# ---------------------------------------------------------------------------


def test_standard_addresses_pass_through_unchanged():
    check(PASS_THROUGH)


def test_remapped_addresses_translate_correctly():
    """All nine remap groups at once (vertices, R, G, B, Z, A, S, T, W)."""
    check(REMAPPED)


def test_bit21_outside_remap_range_clears_bit21():
    check(BIT21_OUT_OF_RANGE)


# ---------------------------------------------------------------------------
# cross-check against the independently extracted table
# ---------------------------------------------------------------------------


def test_remap_table_matches_regdef():
    """``REMAPPED`` (from the Scala test) must agree with ``REMAP_TABLE``."""
    from_test = {a & 0xFFF: b for a, b in REMAPPED}
    assert from_test == dict(REMAP_TABLE)


def test_remap_table_covers_every_word_in_the_range():
    """0x088..0x0fc step 4 is 30 entries and the table has all of them."""
    expected = set(range(REMAP_RANGE_LO, REMAP_RANGE_HI + 1, 4))
    assert set(REMAP_TABLE) == expected
    assert len(REMAP_TABLE) == 30


def test_remap_table_is_a_permutation_of_the_standard_layout():
    """RGBZASTW -> standard is a bijection; a duplicate target would alias."""
    assert len(set(REMAP_TABLE.values())) == len(REMAP_TABLE)
    assert set(REMAP_TABLE.values()) == set(REMAP_TABLE)


def test_whole_bit21_range_against_the_table():
    """Every 4-byte address in 0x000..0x3fc, with bit 21 set.

    Outside 0x088..0x0fc the answer is the address with bit 21 cleared; inside
    it is the table (and the Scala's default branch, ``baseAddr``, for any
    address that has no entry -- there are none at word alignment).
    """
    pairs = []
    for base in range(0x000, 0x400, 4):
        if REMAP_RANGE_LO <= base <= REMAP_RANGE_HI:
            pairs.append(((1 << 21) | base, REMAP_TABLE[base]))
        else:
            pairs.append(((1 << 21) | base, base))
    check(pairs)


def test_unaligned_addresses_inside_the_range_fall_back_to_base():
    """A non-multiple-of-4 in range hits ``translatedAddr := baseAddr``."""
    pairs = [((1 << 21) | base, base)
             for base in range(REMAP_RANGE_LO, REMAP_RANGE_HI + 1)
             if base % 4]
    check(pairs)


def test_upper_address_bits_are_truncated_not_decoded():
    """Bits 20:12 (the datasheet's ``wrap`` field) are dropped by the resize.

    ``regdef.DECODE_QUIRKS`` says so in prose; this is the RTL proof.
    """
    pairs = []
    for wrap in range(1, 1 << 9):
        addr = (wrap << 12) | 0x0A4
        pairs.append((addr, 0x0A4))                    # no bit 21
        pairs.append(((1 << 21) | addr, 0x0C0))        # bit 21: fdRdX remap
    check(pairs)


# ---------------------------------------------------------------------------
# the bus splice itself
# ---------------------------------------------------------------------------


def test_command_payload_passes_through_unchanged():
    dut = make_dut()
    seen = {}

    async def tb(ctx):
        ctx.set(dut.input.cmd.valid, 1)
        ctx.set(dut.output.cmd.ready, 1)
        set_cmd(ctx, dut, address=0x2000A4, opcode=1, length=2,
                data=0xDEADBEEF, mask=0xA, last=1, source=0xB)
        await ctx.delay(SETTLE)
        frag = dut.output.cmd.payload.fragment
        seen.update(
            address=ctx.get(frag.address),
            opcode=ctx.get(frag.opcode),
            length=ctx.get(frag.length),
            data=ctx.get(frag.data),
            mask=ctx.get(frag.mask),
            source=ctx.get(frag.source),
            last=ctx.get(dut.output.cmd.payload.last),
            valid=ctx.get(dut.output.cmd.valid),
        )

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()

    assert seen == {
        "address": 0x0C0, "opcode": 1, "length": 2, "data": 0xDEADBEEF,
        "mask": 0xA, "source": 0xB, "last": 1, "valid": 1,
    }


@pytest.mark.parametrize("valid,ready", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_handshake_is_a_straight_wire(valid, ready):
    dut = make_dut()
    seen = {}

    async def tb(ctx):
        ctx.set(dut.input.cmd.valid, valid)
        ctx.set(dut.output.cmd.ready, ready)
        await ctx.delay(SETTLE)
        seen["out_valid"] = ctx.get(dut.output.cmd.valid)
        seen["in_ready"] = ctx.get(dut.input.cmd.ready)

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    assert seen == {"out_valid": valid, "in_ready": ready}


def test_response_channel_passes_through_unchanged():
    dut = make_dut()
    seen = {}

    async def tb(ctx):
        rsp_in = dut.output.rsp
        rsp_out = dut.input.rsp
        ctx.set(rsp_in.valid, 1)
        ctx.set(rsp_in.payload.last, 1)
        ctx.set(rsp_in.payload.fragment.opcode, 1)
        ctx.set(rsp_in.payload.fragment.data, 0xCAFEBABE)
        ctx.set(rsp_in.payload.fragment.source, 5)
        ctx.set(rsp_out.ready, 1)
        await ctx.delay(SETTLE)
        seen.update(
            valid=ctx.get(rsp_out.valid),
            last=ctx.get(rsp_out.payload.last),
            opcode=ctx.get(rsp_out.payload.fragment.opcode),
            data=ctx.get(rsp_out.payload.fragment.data),
            source=ctx.get(rsp_out.payload.fragment.source),
            back_ready=ctx.get(rsp_in.ready),
        )

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    assert seen == {"valid": 1, "last": 1, "opcode": 1, "data": 0xCAFEBABE,
                    "source": 5, "back_ready": 1}


def test_no_registers_no_latency():
    """A pure splice: the design must have no clock domain and no flip-flop."""
    dut = make_dut()
    frag = Fragment.get(dut, None)
    assert "sync" not in frag.domains, "AddressRemapper must be combinational"

    from amaranth.back import verilog
    text = verilog.convert(make_dut(), name="AddressRemapper_am")
    assert "posedge" not in text


def test_input_params_must_carry_bit21():
    # `_MustUse__silence` is how Amaranth's own test suite suppresses the
    # UnusedElaboratable warning for a component that is built to fail.
    Elaboratable._MustUse__silence = True
    try:
        with pytest.raises(ValueError, match="bit 21"):
            AddressRemapper(bmb_params(C), bmb_params(C))
        gc.collect()          # the warning is emitted from __del__
    finally:
        Elaboratable._MustUse__silence = False
