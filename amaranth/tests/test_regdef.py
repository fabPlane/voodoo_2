"""Consistency checks on ``avoodoo.frontend.regdef`` plus a cross-check against
the real SpinalHDL elaboration.

The ground truth in ``tests/data/registerbank_regmeta.json`` is produced by::

    cd fpga && scala-cli run . --main-class voodoo.diff.GenOne -- \\
        --dump-regmeta ../amaranth/tests/data/registerbank_regmeta.json

``--dump-regmeta`` (added to ``fpga/src/voodoo/diff/GenOne.scala`` for this
task) elaborates ``RegisterBank(Config.voodoo1())`` and writes out
``busif.slices`` together with the three metadata maps ``Core.scala:102`` hands
to ``PciFifo``: ``getCategories``, ``getFloatAliases`` and
``getCommandStreamReady``. It was verified against the generated
``fpga/rtl/diff/PciFifo_spinal.v`` — 143 FIFO-queued addresses, 71 sync
addresses, 30 float aliases and 5 command addresses, all identical — so the
JSON really is the decode table the RTL carries. Committing it keeps this test
free of scala-cli.

Two known, deliberate differences between the JSON and the table:

* the JSON has no 0x240 / 0x250 debug registers, because ``--dump-regmeta``
  elaborates without ``GenerationFlags.simulation``; ``regdef`` marks those
  ``sim_only`` and the checks below exclude them;
* the JSON carries regif's auto-inserted ``NA`` padding fields and takes
  register/field *names* from the ``SymbolName`` macro (so helper-built
  registers all read ``newRegAtWithCategory``). Names are therefore not
  compared; bit sections, access types, reset values and docs are.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avoodoo.config import Config
from avoodoo.frontend import regdef
from avoodoo.frontend.regdef import Access


GROUND_TRUTH = Path(__file__).parent / "data" / "registerbank_regmeta.json"


@pytest.fixture(scope="module")
def truth() -> dict:
    with GROUND_TRUTH.open() as fp:
        return json.load(fp)


# ---------------------------------------------------------------------------
# Internal consistency
# ---------------------------------------------------------------------------


def test_addresses_unique_aligned_and_in_range():
    seen = set()
    for reg in regdef.REGISTERS:
        assert reg.addr not in seen, f"duplicate address 0x{reg.addr:03x}"
        seen.add(reg.addr)
        assert reg.addr % 4 == 0
        assert 0 <= reg.addr < regdef.REGISTER_SPACE_BYTES
        assert reg.width == regdef.BUS_DATA_WIDTH


def test_registers_sorted_by_address():
    addrs = [r.addr for r in regdef.REGISTERS]
    assert addrs == sorted(addrs)


def test_fields_fit_and_do_not_overlap():
    for reg in regdef.REGISTERS:
        occupied = 0
        for f in reg.fields:
            assert f.width >= 1, f"{reg.name}.{f.name}"
            assert f.lo >= 0
            assert f.hi < reg.width, f"{reg.name}.{f.name} runs past bit 31"
            assert not occupied & f.mask, f"{reg.name}.{f.name} overlaps"
            occupied |= f.mask
            assert f.reset >> f.width == 0, f"{reg.name}.{f.name} reset too wide"


def test_field_names_unique_per_register():
    for reg in regdef.REGISTERS:
        names = [f.name for f in reg.fields]
        assert len(names) == len(set(names)), f"{reg.name}: duplicate field name"


def test_register_names_unique():
    names = [r.name for r in regdef.REGISTERS]
    assert len(names) == len(set(names))


def test_qformat_widths_match_config():
    """Fixed-point fields carry the QFormat their AFix prototype was built with."""
    c = Config.voodoo1()
    expected = {
        "vertexAx": c.vertex_format,
        "startR": c.v_color_format,
        "startZ": c.v_depth_format,
        "startS": c.tex_coords_format,
        "startW": c.w_format,
        "dWdY": c.w_format,
    }
    for name, fmt in expected.items():
        f = regdef.by_name(name).fields[0]
        assert f.qformat == fmt, name
        assert f.width == fmt.width
    for reg, f in regdef.iter_fields():
        if f.qformat is not None:
            assert f.qformat.width == f.width, f"{reg.name}.{f.name}"


def test_lookup_helpers_agree_with_the_table():
    for reg in regdef.REGISTERS:
        assert regdef.by_addr(reg.addr) is reg
        assert regdef.by_name(reg.name) is reg
    with pytest.raises(KeyError):
        regdef.by_addr(0x004)
    with pytest.raises(KeyError):
        regdef.by_name("vRetrace")


def test_command_registers_and_streams_line_up():
    stream_addrs = regdef.command_addresses()
    assert stream_addrs == (0x080, 0x100, 0x120, 0x124, 0x128)
    for cmd in regdef.COMMAND_STREAMS:
        reg = regdef.by_addr(cmd.addr)
        assert reg.command == cmd.name, f"0x{cmd.addr:03x}"
        assert reg.category in (regdef.FIFO_NO_SYNC, regdef.FIFO_WITH_SYNC)
    # every register that claims a command has a stream, and vice versa
    from_regs = tuple(sorted(r.addr for r in regdef.REGISTERS if r.command))
    assert from_regs == stream_addrs
    # ready_index is the rank of the address (RegisterBank.scala:722)
    for index, addr in enumerate(stream_addrs):
        assert regdef.command_by_addr(addr).ready_index == index


def test_swapbuffer_is_the_was_enqueued_address():
    assert regdef.WAS_ENQUEUED_ADDR == 0x128
    reg = regdef.by_addr(regdef.WAS_ENQUEUED_ADDR)
    assert reg.name == "swapbufferCMD"
    assert reg.category is regdef.FIFO_WITH_SYNC


def test_float_aliases_are_target_plus_0x80():
    assert regdef.FLOAT_ALIAS_OFFSET == 0x080
    for alias in regdef.FLOAT_ALIASES:
        assert alias.addr == alias.target_addr + regdef.FLOAT_ALIAS_OFFSET
        target = regdef.by_addr(alias.target_addr)
        assert alias.target_name == target.name
        assert alias.name == "f" + target.name
        fmt = target.fields[0].qformat
        assert fmt is not None
        assert (alias.int_bits, alias.frac_bits) == (fmt.non_fraction, fmt.fraction)
        assert alias.shift == regdef.FLOAT_CONVERT_FRAC_BITS - alias.frac_bits
        assert 0 <= alias.shift < 32
        # an alias address is never also a register address
        with pytest.raises(KeyError):
            regdef.by_addr(alias.addr)


def test_categories_cover_every_categorised_register_and_alias():
    cats = regdef.categories()
    for reg in regdef.registers(include_sim_only=False):
        if reg.category is None:
            assert reg.addr not in cats, f"0x{reg.addr:03x} should be uncategorised"
        else:
            assert cats[reg.addr] is reg.category
    for alias in regdef.FLOAT_ALIASES:
        assert cats[alias.addr] is regdef.FIFO_NO_SYNC
    assert len(cats) == len(
        [r for r in regdef.registers(include_sim_only=False) if r.category]
    ) + len(regdef.FLOAT_ALIASES)


def test_uncategorised_addresses_fall_through_to_bypass():
    assert regdef.effective_category(0x000) is regdef.BYPASS_FIFO  # status
    assert regdef.effective_category(0x14C) is regdef.BYPASS_FIFO  # fbiPixelsIn
    assert regdef.effective_category(0x004) is regdef.BYPASS_FIFO  # hole
    assert regdef.effective_category(0x008) is regdef.FIFO_NO_SYNC
    assert regdef.effective_category(0x110) is regdef.FIFO_WITH_SYNC
    assert regdef.effective_category(0x200) is regdef.BYPASS_FIFO


def test_category_flags_match_upstream_names():
    assert regdef.FIFO_NO_SYNC.as_tuple() == (False, False)
    assert regdef.FIFO_WITH_SYNC.as_tuple() == (False, True)
    assert regdef.BYPASS_FIFO.as_tuple() == (True, False)
    assert regdef.READ_ONLY.as_tuple() == regdef.FIFO_NO_SYNC.as_tuple()
    assert {c.spinal_name for c in regdef.categories().values()} <= {
        "fifoNoSync",
        "fifoWithSync",
        "bypassFifo",
    }


def test_remap_table_is_a_permutation_of_the_float_alias_range():
    assert len(regdef.REMAP_TABLE) == 30
    assert set(regdef.REMAP_TABLE) == set(regdef.REMAP_TABLE.values())
    assert set(regdef.REMAP_TABLE) == {a.addr for a in regdef.FLOAT_ALIASES}


def test_sim_only_registers_are_marked():
    sim_only = {r.addr for r in regdef.REGISTERS if r.sim_only}
    assert sim_only == {0x240, 0x250}
    for addr in sim_only:
        assert regdef.by_addr(addr).category is None


# ---------------------------------------------------------------------------
# Cross-check against the Spinal elaboration
# ---------------------------------------------------------------------------


def _truth_registers(truth: dict) -> dict[int, dict]:
    return {r["addr"]: r for r in truth["registers"]}


def test_ground_truth_provenance(truth):
    assert truth["simulation"] is False
    assert "GenOne.scala --dump-regmeta" in truth["source"]


def test_same_register_addresses_as_spinal(truth):
    ours = {r.addr for r in regdef.registers(include_sim_only=False)}
    theirs = set(_truth_registers(truth))
    assert ours == theirs, (
        f"only in regdef: {sorted(hex(a) for a in ours - theirs)}; "
        f"only in Spinal: {sorted(hex(a) for a in theirs - ours)}"
    )


def test_same_fields_as_spinal(truth):
    """Bit sections, access types, reset values and docs, register by register.

    regif's auto-inserted ``NA`` padding is dropped from the Spinal side:
    ``regdef`` declares only the fields RegisterBank.scala declares.
    """
    theirs = _truth_registers(truth)
    for reg in regdef.registers(include_sim_only=False):
        spinal = [f for f in theirs[reg.addr]["fields"] if f["access"] != "NA"]
        ours = sorted(reg.fields, key=lambda f: f.lo)
        assert len(ours) == len(spinal), (
            f"0x{reg.addr:03x} {reg.name}: {len(ours)} declared fields, "
            f"Spinal has {len(spinal)} non-NA"
        )
        for f, s in zip(ours, spinal):
            where = f"0x{reg.addr:03x} {reg.name}.{f.name}"
            assert (f.hi, f.lo) == (s["hi"], s["lo"]), where
            assert f.access.value == s["access"], where
            assert f.reset == s["reset"], f"{where}: {f.reset} != {s['reset']}"
            assert f.doc == s["doc"], f"{where}: {f.doc!r} != {s['doc']!r}"


def test_command_registers_declare_no_fields_like_spinal(truth):
    """triangleCMD / ftriangleCMD carry a payload, not a field."""
    theirs = _truth_registers(truth)
    for addr in (0x080, 0x100):
        assert regdef.by_addr(addr).fields == ()
        assert all(f["access"] == "NA" for f in theirs[addr]["fields"])


def test_same_categories_as_spinal(truth):
    ours = {a: c.as_tuple() for a, c in regdef.categories().items()}
    theirs = {c["addr"]: (c["fifoBypass"], c["syncRequired"]) for c in truth["categories"]}
    assert ours == theirs


def test_same_float_aliases_as_spinal(truth):
    ours = {
        a.addr: (a.target_addr, a.int_bits, a.frac_bits, a.shift)
        for a in regdef.FLOAT_ALIASES
    }
    theirs = {
        a["addr"]: (a["targetAddr"], a["intBits"], a["fracBits"], a["shift"])
        for a in truth["floatAliases"]
    }
    assert ours == theirs


def test_same_command_addresses_as_spinal(truth):
    assert list(regdef.command_addresses()) == truth["commandAddresses"]


def test_spinal_decode_entry_counts(truth):
    """The numbers PciFifo's address decode actually switches over."""
    cats = regdef.categories()
    assert len(cats) == len(truth["categories"]) == 158
    queued = [a for a, c in cats.items() if not c.fifo_bypass]
    synced = [a for a, c in cats.items() if c.sync_required]
    assert len(queued) == 143
    assert len(synced) == 71
    assert len(regdef.FLOAT_ALIASES) == 30
    assert len(regdef.command_addresses()) == 5


# ---------------------------------------------------------------------------
# The generated map must match the table
# ---------------------------------------------------------------------------


def test_register_map_doc_is_up_to_date():
    """``docs/amaranth/register-map.md`` is generated; regenerate it after edits.

        cd amaranth && uv run python -m gen.gen_register_map
    """
    from gen import gen_register_map

    assert gen_register_map.OUT.exists(), f"{gen_register_map.OUT} is missing"
    assert gen_register_map.OUT.read_text(encoding="utf-8") == gen_register_map.render()


def test_datasheet_cross_check_finds_only_the_known_gaps():
    """The only SST-1 register the core does not implement is vRetrace (0x204)."""
    from gen import gen_register_map

    datasheet = gen_register_map.parse_datasheet(gen_register_map.DATASHEET)
    assert len(datasheet) == 162
    covered = {r.addr for r in regdef.REGISTERS} | {a.addr for a in regdef.FLOAT_ALIASES}
    assert sorted(a for a in datasheet if a not in covered) == [0x204]
    extra = sorted(a for a in covered if a not in datasheet)
    assert extra == [0x240, 0x244, 0x248, 0x24C, 0x250]
