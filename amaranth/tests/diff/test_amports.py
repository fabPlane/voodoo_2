"""``tools.diff.amports`` must agree with the port names Amaranth emits.

The whole pairing step rests on being able to predict, from a signature
alone, the Verilog port each leaf lands in and its bit offset inside it.  This
test builds a component covering every shape the port uses -- nested
signatures, a ``data.StructLayout`` payload, a ``ShapeCastable`` leaf, and
``.array(...)`` at one and two dimensions -- elaborates it, and asserts that
the predicted ports are exactly the emitted ones.

The array cases are here because they were originally wrong:
``SignatureMembers.flatten()`` reports ``dimensions`` on the array member's
own row and then yields the element sub-paths with **no index**, so every
element of ``In(MemBusSignature(p)).array(5)`` collapsed onto one name and
``BmbArbiter`` could not be paired at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

from amaranth import Module
from amaranth.back import verilog
from amaranth.hdl import Fragment
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

AM_ROOT = Path(__file__).resolve().parents[2]
if str(AM_ROOT) not in sys.path:
    sys.path.insert(0, str(AM_ROOT))

from tools.diff.amports import amaranth_fields  # noqa: E402
from tools.diff.vlog import parse_ports  # noqa: E402

PAYLOAD = data.StructLayout({"a": 4, "b": 2})
INNER = wiring.Signature({"cmd": Out(stream.Signature(PAYLOAD)), "ack": In(1)})


class Shapes(wiring.Component):
    def __init__(self):
        super().__init__({
            "inputs": In(INNER).array(3),
            "single": In(INNER).array(1),   # `.array(1)` still gets an index
            "flags": Out(2).array(2),
            "grid": Out(1).array(2, 3),
            "plain": Out(5),
            "bundle": Out(PAYLOAD),
        })

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.plain.eq(self.inputs[0].cmd.payload.a)
        m.d.comb += self.bundle.eq(self.single[0].cmd.payload)
        for i in range(3):
            m.d.comb += self.inputs[i].cmd.ready.eq(1)
        m.d.comb += self.single[0].cmd.ready.eq(1)
        for i in range(2):
            m.d.comb += self.flags[i].eq(i)
            for j in range(3):
                m.d.comb += self.grid[i][j].eq(0)
        return m


def shapes() -> "Shapes":
    """A ``Shapes`` that Amaranth will not warn about going unelaborated.

    Two of the tests read the signature only; ``Fragment.get`` marks the
    elaboratable used without costing anything.
    """
    dut = Shapes()
    Fragment.get(dut, None)
    return dut


def test_predicted_ports_match_emitted_verilog(tmp_path):
    dut = shapes()
    path = tmp_path / "Shapes.v"
    path.write_text(verilog.convert(dut, name="Shapes"))

    emitted = set(parse_ports(path)) - {"clk", "rst"}
    fields = amaranth_fields(dut)

    assert {f.port for f in fields.values()} == emitted

    # every bit of every port is claimed by exactly one leaf
    covered: dict[str, int] = {}
    for f in fields.values():
        covered[f.port] = covered.get(f.port, 0) + f.width
    widths = parse_ports(path)
    for port, bits in covered.items():
        assert bits == widths[port].width, port


def test_array_indices_land_at_the_declared_level():
    fields = amaranth_fields(shapes())

    # the index goes where `.array` was declared, not at the end
    assert "inputs__0__cmd__valid" in fields
    assert "inputs__2__ack" in fields
    assert "inputs__cmd__valid" not in fields
    # `.array(1)` is indexed too
    assert "single__0__cmd__valid" in fields
    assert "single__cmd__valid" not in fields
    # multiple dimensions nest left to right
    assert "grid__1__2" in fields
    # a scalar array member is still one port per element
    assert fields["flags__1"].port == "flags__1"


def test_struct_payloads_are_packed_into_one_port():
    fields = amaranth_fields(shapes())

    a = fields["inputs__1__cmd__payload__a"]
    b = fields["inputs__1__cmd__payload__b"]
    assert a.port == b.port == "inputs__1__cmd__payload"
    assert (a.offset, a.width) == (0, 4)
    assert (b.offset, b.width) == (4, 2)
    assert a.packed and not fields["plain"].packed


def test_zero_width_leaves_are_dropped():
    """A ``stream.Signature(0)`` payload has no Spinal twin and no bits.

    It must not appear as a leaf, or ``pair_ports`` reports it as unpaired and
    there is no override that can say "ignore this". End-to-end coverage is in
    ``test_diff_nodata.py``.
    """
    from amaranth.hdl import unsigned
    from amaranth.lib import stream

    class NoData(wiring.Component):
        def __init__(self):
            super().__init__({"cmd": In(stream.Signature(unsigned(0))), "busy": Out(1)})

        def elaborate(self, platform):
            m = Module()
            m.d.comb += [self.cmd.ready.eq(1), self.busy.eq(self.cmd.valid)]
            return m

    dut = NoData()
    Fragment.get(dut, None)
    assert sorted(amaranth_fields(dut)) == ["busy", "cmd__ready", "cmd__valid"]


def test_vlog_reads_a_yosys_zero_width_range_as_zero(tmp_path):
    """``[-1:0]`` is zero bits; ``[0:7]`` is still eight."""
    from tools.diff.vlog import parse_ports

    path = tmp_path / "R.v"
    path.write_text(
        "module R (\n"
        "  input  wire [-1:0] empty,\n"
        "  input  wire [0:7]  descending,\n"
        "  input  wire [7:0]  normal,\n"
        "  output wire        bit_\n"
        ");\nendmodule\n"
    )
    widths = {n: p.width for n, p in parse_ports(path).items()}
    assert widths == {"empty": 0, "descending": 8, "normal": 8, "bit_": 1}


def test_vlog_does_not_eat_a_name_that_starts_with_a_type_keyword(tmp_path):
    """``input reg_side__cmd__ready;`` names a port ``reg_side__cmd__ready``.

    The type-keyword group used to lack a word boundary, so ``reg`` was taken
    as the type and ``_side__cmd__ready`` as the name -- and ``input [265:0]
    regs;`` became ``s``. Any Amaranth port snake_cased from a Spinal
    ``io_regBus_*`` / ``io_regs_*`` hits this, so PciFifo, Lfb and
    CoreCpuFrontdoor were all unpairable.
    """
    from tools.diff.vlog import parse_ports

    path = tmp_path / "K.v"
    path.write_text(
        "module K(clk, reg_side__cmd__ready, regs, wire_in, logical, variant);\n"
        "  input clk;\n"
        "  wire clk;\n"
        "  output reg_side__cmd__ready;\n"
        "  reg reg_side__cmd__ready;\n"
        "  input [265:0] regs;\n"
        "  wire [265:0] regs;\n"
        "  input wire_in;\n"
        "  input logical;\n"
        "  output reg variant;\n"
        "endmodule\n"
    )
    ports = parse_ports(path)
    assert {n: (p.direction, p.width) for n, p in ports.items()} == {
        "clk": ("input", 1),
        "reg_side__cmd__ready": ("output", 1),
        "regs": ("input", 266),
        "wire_in": ("input", 1),
        "logical": ("input", 1),
        "variant": ("output", 1),
    }
