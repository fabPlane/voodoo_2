"""``gen/gen_core_sim.py``'s ``strip_meminit``: uninitialised RAMs go, ROMs stay.

Phase 0.5 stripped every ``$meminit_v2`` cell, which was right for a stub
with two empty RAMs and wrong for the real core, whose dither ROMs and TMU
LUTs carry their tables in exactly the same kind of cell.  Phase 7.2 made
the rule "all-zero ``DATA`` only"; this pins it on a two-memory toy so a
regression shows up here rather than as a black Glide screenshot.
"""

from __future__ import annotations

import pytest
from amaranth import Module, Signal, unsigned
from amaranth.back import rtlil
from amaranth.hdl import Elaboratable
from amaranth.lib import memory

from gen.gen_core_sim import KEEP_WORDS_LIMIT, _count_initial, strip_meminit


class _TwoMemories(Elaboratable):
    def __init__(self, ram_depth: int = 64, rom_depth: int = 16, rom_zero: bool = False):
        self.ram_depth, self.rom_depth, self.rom_zero = ram_depth, rom_depth, rom_zero
        self.addr = Signal(8)
        self.out = Signal(32)

    def elaborate(self, platform):
        m = Module()
        m.submodules.ram = ram = memory.Memory(shape=unsigned(32), depth=self.ram_depth, init=[])
        rom_init = [0] * self.rom_depth if self.rom_zero else [i * 3 + 1 for i in range(self.rom_depth)]
        m.submodules.rom = rom = memory.Memory(shape=unsigned(32), depth=self.rom_depth, init=rom_init)
        rp = ram.read_port()
        wp = ram.write_port()
        rr = rom.read_port()
        m.d.comb += [rp.addr.eq(self.addr), rr.addr.eq(self.addr), wp.addr.eq(self.addr),
                     wp.data.eq(self.out), wp.en.eq(self.addr[0]),
                     self.out.eq(rp.data ^ rr.data)]
        return m


def _rtlil(dut) -> str:
    return rtlil.convert(dut, ports=[dut.addr, dut.out])


def test_strips_only_the_all_zero_initialiser():
    text = _rtlil(_TwoMemories())
    assert text.count("cell $meminit") == 2
    out, removed = strip_meminit(text)
    assert removed == 1
    assert out.count("cell $meminit") == 1
    # The surviving cell is the ROM's, contents intact.
    assert '\\MEMID "\\\\rom"' in out
    assert '\\MEMID "\\\\ram"' not in out.split("cell $meminit")[1]


def test_an_explicit_all_zero_rom_is_stripped_too():
    """Verilator's ``--x-initial fast`` zeroes it anyway, so nothing is lost."""
    out, removed = strip_meminit(_rtlil(_TwoMemories(rom_zero=True)))
    assert removed == 2
    assert "cell $meminit" not in out


def test_a_deep_memory_with_contents_is_refused():
    depth = KEEP_WORDS_LIMIT + 1
    text = _rtlil(_TwoMemories(ram_depth=4, rom_depth=depth))
    with pytest.raises(SystemExit, match="non-zero"):
        strip_meminit(text)


def test_count_initial_blocks_and_lines():
    verilog = "\n".join([
        "module m;",
        "  reg [3:0] rom [1:0];",
        "  initial begin",
        "    rom[0] = 4'h1;",
        "    rom[1] = 4'h2;",
        "  end",
        "  initial x = 1;",
        "endmodule",
    ])
    assert _count_initial(verilog) == (2, 3)
