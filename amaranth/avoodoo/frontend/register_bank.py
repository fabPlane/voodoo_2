"""Voodoo Graphics register bank.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/frontend/RegisterBank.scala`` (1280
lines).  The register *declarations* come from the table in
:mod:`avoodoo.frontend.regdef` (plan task 2.1) and are expanded through
:class:`avoodoo.bus.bus_interface.BusInterface` (the ``BmbBusInterface`` /
regif port, task 1.6); the twelve pieces the table cannot express are
hand-written below, each against its Scala lines:

1. the status read path with ``io.pciFifoFree.sat(1)`` (:204-232);
2. ``clutData`` -> the 33-entry ``gammaLut`` (:90-96, :128-136);
3. the YIQ palette path on NCC I/Q writes with ``data[31]`` (:138-158);
4. the twelve float shadow registers and their two write sources (:98-191);
5. ``triangleCMD`` / ``ftriangleCMD`` payload assembly (:467-606, :652-673);
6. command-stream buffering and the ``commandReady`` ordering (:720-726,
   BmbBusInterface.scala:170-190);
7. ``triangleConfigReg`` and the packed ``TexLayoutTables.compute`` register
   (:641, :1193-1207, :1235);
8. trace IDs under ``config.trace.enabled`` (:86-88, :690-697);
9. ``syncPulse`` / ``fifoEmpty`` (:194-199);
10. the ``GenerationFlags.simulation`` debug registers (:1098, :1123) --
    the ``simulation`` constructor flag, off by default, on for ``CoreSim``;
11. the field naming: upstream renames every field ``sim_reg_%03x_f%d`` and
    ``simPublic()``s it (:1240-1249) so the Verilator harness can poke it.
    Here every written field is an ordinary output port instead (the
    ``dbg``-bundle approach of plan §4.3), so nothing depends on internal
    names; :func:`spinal_port_overrides` maps the Spinal spellings onto those
    ports for the lockstep diff;
12. ``RegisterBank.bmbParams`` / ``externalBmbParams`` (:1258-1280), which
    are :func:`avoodoo.core.membus_params.register_bank_params` /
    :func:`~avoodoo.core.membus_params.register_bank_external_params` --
    re-exported here as :func:`bmb_params` / :func:`external_bmb_params`.

Port structure
--------------

The Scala ``io`` bundle maps one to one; the ``.asOutput()`` fields that
Spinal lifts to ports of the top component are grouped the way the Scala
``Area``\\ s are, so ``registerBank.renderConfig.fbzMode.enableClipping``
reads ``rb.render_config.fbz_mode.enable_clipping`` here::

    bus                  In(MemBusSignature(bmb_params(c)))        slave(Bmb(...))
    status_inputs        In(StatusInputs())
    swap_displayed_buffer, swaps_pending, statistics (CoreStats), debug (CoreDebug),
    pci_fifo_empty, pci_fifo_free, swap_cmd_enqueued, sync_drained, float_shadow (Flow),
    draw_routing (FbRouting)                                        the other inputs
    sync_pulse, fifo_empty, command_ready[5], palette_write (Flow), gamma_lut[33]
    triangle_geometry.{vertices,start_values,grad_x,grad_y}.<reg>   AFix outputs
    commands.{triangle,ftriangle,nop,fastfill,swapbuffer}_cmd_stream, swap_vsync_enable, swap_interval
    render_config.*, fog_table[64].{dfog,fog}, init.*, tmu_config.*, ncc_table.table<N>_<k>[4]

Register-stage structure, for the waveform reader: every ``RW``/``WO`` field
is one register with the regif masked write; ``reg_rdata`` is the registered
read mux; the five command streams are ``s2mPipe().m2sPipe()`` (payload
registers unreset, as upstream); ``triangleConfigReg`` and
``triangleTexTablesReg`` are two unreset registers refreshed every cycle, so a
triangle command sees the render config as it was one cycle before the write
and the texture tables as they were two cycles before.
"""

from __future__ import annotations

import re

from amaranth import Cat, Const, Module, Mux, Signal, signed, unsigned
from amaranth.lib import data, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.bus.bus_interface import NO_DATA, BusInterface, FieldInst, RegInst
from avoodoo.bus.register_category import RegisterCategory
from avoodoo.config import Config
from avoodoo.core import membus_params as _mp
from avoodoo.core.core_types import (
    ALPHA_MODE_LAYOUT,
    FBZ_COLOR_PATH_LAYOUT,
    FOG_MODE_LAYOUT,
    CoreDebug,
    CoreStats,
    FbRouting,
    FloatShadowWriteLayout,
    PaletteWrite,
    StatusInputs,
    TraceOrigin,
    gradient_bundle_layout,
    hi_alpha_layout,
    hi_tex_coords_layout,
    input_gradient_layout,
    per_triangle_config_layout,
    tex_layout_tables_layout,
    triangle_setup_input_layout,
)
from avoodoo.frontend import regdef
from avoodoo.frontend.regdef import Access, Field, Register
from avoodoo.lib.fixed import AFixShape, AFixValue
from avoodoo.lib.membus import MemBusParameter, MemBusSignature
from avoodoo.lib.stream import FlowSignature, Signature as StreamSignature
from avoodoo.texture.tex_layout_tables import TexConfigLayout, compute_tables

__all__ = [
    "RegisterBank",
    "bmb_params",
    "external_bmb_params",
    "output_path",
    "spinal_sim_reg_name",
    "spinal_port_overrides",
    "FLOAT_SHADOW_ADDRS",
]


# ---------------------------------------------------------------------------
# Parameters (RegisterBank.scala:1258-1280)
# ---------------------------------------------------------------------------


def bmb_params(c: Config | None = None) -> MemBusParameter:
    """``RegisterBank.bmbParams(c)`` -- 12 address bits, the 4 KiB register window."""
    return _mp.register_bank_params(c)


def external_bmb_params(c: Config | None = None) -> MemBusParameter:
    """``RegisterBank.externalBmbParams(c)`` -- 22 address bits so bit 21 reaches the remapper."""
    return _mp.register_bank_external_params(c)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

_BOUNDARY_1 = re.compile(r"(.)([A-Z][a-z]+)")
_BOUNDARY_2 = re.compile(r"([a-z0-9])([A-Z])")


def _snake(name: str) -> str:
    """``tools.diff.naming.camel_to_snake``, copied so ``avoodoo`` does not import ``tools``.

    ``tests/test_register_bank.py`` asserts the two agree on every name used
    here, so the diff harness's mechanical rule and these port names cannot
    drift apart.
    """
    if name.isdigit():
        return name
    out = _BOUNDARY_1.sub(r"\1_\2", name)
    out = _BOUNDARY_2.sub(r"\1_\2", out)
    return out.lower()


_FOG_ENTRY = re.compile(r"^entry(\d+)(Dfog|Fog)$")
_NCC = re.compile(r"^nccTable([01])([YIQ])([0-3])$")

#: The two command registers whose 32-bit field is *not* ``.asOutput()``
#: (RegisterBank.scala:700-701).
_NON_OUTPUT_FIELD_ADDRS = frozenset({0x120, 0x124})


def output_path(reg: Register, f: Field) -> tuple[str, ...] | None:
    """Signature path of the output port carrying ``reg.f``, or ``None``.

    A field is an output when the Scala calls ``.asOutput()`` on it: every
    ``WO``/``RW`` field except the two command-data words.  The grouping is
    the Scala ``Area`` the declaration sits in.
    """
    if f.access is Access.RO or reg.addr in _NON_OUTPUT_FIELD_ADDRS:
        return None
    a = reg.addr
    if 0x008 <= a <= 0x07C:
        area = ("vertices" if a <= 0x01C else "start_values" if a <= 0x03C
                else "grad_x" if a <= 0x05C else "grad_y")
        return ("triangle_geometry", area, _snake(f.name))
    if a == 0x128:
        return ("commands", _snake(f.name))
    if 0x104 <= a <= 0x148:
        if a in (0x110, 0x114):
            return ("render_config", _snake(reg.name), _snake(f.name))
        return ("render_config", _snake(f.name))
    if 0x160 <= a <= 0x1DC:
        mo = _FOG_ENTRY.match(f.name)
        assert mo, f.name
        return ("fog_table", mo.group(1), mo.group(2).lower())
    if 0x200 <= a <= 0x24C:
        if len(reg.fields) == 1 and f.name == reg.name:
            return ("init", _snake(reg.name))
        fname = f.name
        if fname.startswith(reg.name + "_"):
            fname = fname[len(reg.name) + 1:]
        return ("init", _snake(reg.name), _snake(fname))
    if 0x300 <= a <= 0x320:
        return ("tmu_config", _snake(f.name))
    if 0x324 <= a <= 0x380:
        mo = _NCC.match(f.name)
        assert mo, f.name
        return ("ncc_table", f"table{mo.group(1)}_{mo.group(2).lower()}", mo.group(3))
    raise ValueError(f"{reg.name}.{f.name}: no output group for 0x{a:03x}")


def spinal_field_index(reg: Register, f: Field) -> int:
    """regif's index of ``f`` in its slice, counting auto-inserted ``NA`` padding."""
    idx = 0
    pos = 0
    for g in sorted(reg.fields, key=lambda g: g.lo):
        if g.lo > pos:
            idx += 1
        if g is f:
            return idx
        idx += 1
        pos = g.hi + 1
    raise KeyError(f.name)


def spinal_sim_reg_name(reg: Register, f: Field) -> str:
    """The ``sim_reg_%03x_f%d`` name upstream gives the field (RegisterBank.scala:1245)."""
    return f"sim_reg_{reg.addr:03x}_f{spinal_field_index(reg, f)}"


def spinal_port_overrides(*, simulation: bool = False) -> dict[str, str]:
    """Spinal ``sim_reg_*`` output port -> Amaranth leaf, for ``tools/diff/registry.py``.

    Only the non-AFix ``.asOutput()`` fields appear as ``sim_reg_*`` ports on
    the Spinal side: an AFix field is exported through the ``AFix`` wrapper
    signal, which keeps its Area path (``triangleGeometry_vertices_vertexAx``)
    and therefore pairs by the mechanical rule.
    """
    out: dict[str, str] = {}
    for reg in regdef.registers(include_sim_only=simulation):
        for f in reg.fields:
            if f.qformat is not None:
                continue
            path = output_path(reg, f)
            if path is None:
                continue
            out[spinal_sim_reg_name(reg, f)] = "__".join(path)
    return out


# ---------------------------------------------------------------------------
# Signature building
# ---------------------------------------------------------------------------


def _reg_layout(reg: Register) -> data.StructLayout:
    """Output fields of a multi-field register as one layout, in bit order."""
    fields = {}
    for f in sorted(reg.fields, key=lambda f: f.lo):
        path = output_path(reg, f)
        assert path is not None
        fields[path[-1]] = unsigned(f.width)
    return data.StructLayout(fields)


def _group_members(group: str) -> dict:
    """``Out(...)`` members for the registers whose :func:`output_path` starts with ``group``.

    A register with one output field becomes a flat ``Out(width)``; several
    become a ``StructLayout`` named after the register.  The special groups
    (triangle geometry, fog table, NCC tables) are built by hand.
    """
    members: dict = {}
    for reg in regdef.registers(include_sim_only=False):
        outs = [(f, output_path(reg, f)) for f in reg.fields]
        outs = [(f, p) for f, p in outs if p is not None and p[0] == group]
        if not outs:
            continue
        if all(len(p) == 2 for _, p in outs):
            for f, p in outs:
                members[p[1]] = Out(unsigned(f.width))
        else:
            name = outs[0][1][1]
            assert all(len(p) == 3 and p[1] == name for _, p in outs), reg.name
            members[name] = Out(_reg_layout(reg))
    return members


def _triangle_geometry_signature() -> wiring.Signature:
    areas: dict[str, dict] = {"vertices": {}, "start_values": {}, "grad_x": {}, "grad_y": {}}
    for reg in regdef.registers():
        if not 0x008 <= reg.addr <= 0x07C:
            continue
        f = reg.fields[0]
        path = output_path(reg, f)
        assert path is not None and f.qformat is not None
        areas[path[1]][path[2]] = Out(AFixShape.from_q(f.qformat))
    return wiring.Signature({k: Out(wiring.Signature(v)) for k, v in areas.items()})


def _fog_table_shape() -> data.ArrayLayout:
    return data.ArrayLayout(data.StructLayout({"dfog": unsigned(8), "fog": unsigned(8)}), 64)


def _ncc_table_signature() -> wiring.Signature:
    return wiring.Signature({
        f"table{t}_{k}": Out(data.ArrayLayout(unsigned(32), 4))
        for t in (0, 1) for k in ("y", "i", "q")
    })


def _commands_signature(c: Config) -> wiring.Signature:
    tri = triangle_setup_input_layout(c)
    members = {
        "triangle_cmd_stream": Out(StreamSignature(tri)),
        "ftriangle_cmd_stream": Out(StreamSignature(tri)),
        "nop_cmd_stream": Out(StreamSignature(unsigned(32))),
        "fastfill_cmd_stream": Out(NO_DATA),
        "swapbuffer_cmd_stream": Out(NO_DATA),
    }
    members.update(_group_members("commands"))
    return wiring.Signature(members)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resize_s(v, width: int):
    """``SInt.resize(width)``: sign-extend when growing, keep the low bits when shrinking."""
    v = v.as_signed()
    if width <= len(v):
        return v[:width].as_signed()
    return Cat(v, v[-1].replicate(width - len(v))).as_signed()


def _shl_keep(v, n: int):
    """``|<<``: a width-preserving left shift."""
    w = len(v)
    return (v.as_unsigned() << n)[:w]


def _raw(x):
    return x.raw if isinstance(x, AFixValue) else (x.as_value() if hasattr(x, "as_value") else x)


#: Float shadow register per address (RegisterBank.scala:111-126); the value is
#: the left shift a *fixed* write gets (:159-190) and the width.
FLOAT_SHADOW_ADDRS: dict[int, tuple[str, int]] = {
    0x030: ("floatShadowStartA", 18),
    0x034: ("floatShadowStartS", 12),
    0x038: ("floatShadowStartT", 12),
    0x03C: ("floatShadowStartW", 0),
    0x050: ("floatShadowDAdX", 18),
    0x054: ("floatShadowDSdX", 12),
    0x058: ("floatShadowDTdX", 12),
    0x05C: ("floatShadowDWdX", 0),
    0x070: ("floatShadowDAdY", 18),
    0x074: ("floatShadowDSdY", 12),
    0x078: ("floatShadowDTdY", 12),
    0x07C: ("floatShadowDWdY", 0),
}

_PALETTE_ADDRS = (0x334, 0x338, 0x33C, 0x340, 0x344, 0x348, 0x34C, 0x350)
_PALETTE_ODD_ADDRS = (0x338, 0x340, 0x348, 0x350)


# ---------------------------------------------------------------------------
# The component
# ---------------------------------------------------------------------------


class RegisterBank(wiring.Component):
    """``case class RegisterBank(config: Config)``.

    Parameters
    ----------
    config : Config
        The hardware configuration; ``Config.voodoo1()`` when omitted, as the
        other core modules do for the diff registry.
    simulation : bool
        ``GenerationFlags.simulation``: adds the read-only ``debugBusy``
        (0x240) and ``writePathDebug`` (0x250) registers.  Off by default, so
        the port list and decode match the synthesis elaboration
        (``GenOne RegisterBank``); ``core_sim.py`` turns it on.
    """

    def __init__(self, config: Config | None = None, *, simulation: bool = False):
        c = Config.voodoo1() if config is None else config
        self.config = c
        self.simulation = bool(simulation)
        self._tex_coords_hi_width = AFixShape.from_q(c.tex_coords_hi_format).bit_width
        self._bus_params = bmb_params(c)
        addr_width = self._bus_params.access.address_width

        members = {
            "bus": In(MemBusSignature(self._bus_params)),
            "status_inputs": In(StatusInputs()),
            "swap_displayed_buffer": In(2),
            "swaps_pending": In(3),
            "statistics": In(CoreStats()),
            "sync_pulse": Out(1),
            "debug": In(CoreDebug()),
            "pci_fifo_empty": In(1),
            "pci_fifo_free": In(7),
            "swap_cmd_enqueued": In(1),
            "sync_drained": In(1),
            "fifo_empty": Out(1),
            "command_ready": Out(1).array(len(regdef.COMMAND_STREAMS)),
            "float_shadow": In(FlowSignature(FloatShadowWriteLayout(addr_width))),
            "draw_routing": In(FbRouting(c)),
            "palette_write": Out(FlowSignature(PaletteWrite())),
            "gamma_lut": Out(24).array(33),
            # the `.asOutput()` register fields, grouped by Scala Area
            "triangle_geometry": Out(_triangle_geometry_signature()),
            "commands": Out(_commands_signature(c)),
            "render_config": Out(wiring.Signature(_group_members("render_config"))),
            "fog_table": Out(_fog_table_shape()),
            "init": Out(wiring.Signature(_group_members("init"))),
            "tmu_config": Out(wiring.Signature(_group_members("tmu_config"))),
            "ncc_table": Out(_ncc_table_signature()),
        }
        super().__init__(members)

    # -- port lookup ---------------------------------------------------------

    def _port(self, path: tuple[str, ...]):
        obj = self
        for seg in path:
            obj = obj[int(seg)] if seg.isdigit() else getattr(obj, seg)
        return obj

    # -- elaboration ---------------------------------------------------------

    def elaborate(self, platform):
        m = Module()
        c = self.config
        hi_w = self._tex_coords_hi_width
        bus = self.bus
        cmd = bus.cmd.payload.fragment

        m.d.comb += [
            self.palette_write.valid.eq(0),
            self.palette_write.payload.address.eq(0),
            self.palette_write.payload.data.eq(0),
        ]

        # Create the bus interface for regif -- shared across all Areas
        busif = BusInterface(m, bus, reg_pre="VDO")
        self.busif = busif

        # -- trace IDs (:86-88) ------------------------------------------------
        trace = c.trace.enabled
        triangle_draw_trace_id = Signal(32, name="triangleDrawTraceId") if trace else None
        triangle_primitive_trace_id = Signal(32, name="trianglePrimitiveTraceId") if trace else None

        # -- gammaLut (:90-96) -------------------------------------------------
        gamma_lut = []
        for i in range(33):
            value = 0xFFFFFF if i == 32 else ((i * 8) << 16) | ((i * 8) << 8) | (i * 8)
            gamma_lut.append(Signal(24, name=f"gammaLut_{i}", init=value))
            m.d.comb += self.gamma_lut[i].eq(gamma_lut[i])

        # -- float shadow registers (:98-109) ----------------------------------
        float_shadow = {}
        for addr, (name, shift) in FLOAT_SHADOW_ADDRS.items():
            # the three W registers are SInt(60 bits), the rest texCoordsHiWidth
            width = 60 if shift == 0 else hi_w
            float_shadow[addr] = Signal(signed(width), name=name)
        self.float_shadow_regs = float_shadow     # for tests

        # -- syncPulse / fifoEmpty (:194-199) ----------------------------------
        sync_pulse = Signal(name="syncPulse")
        m.d.comb += self.sync_pulse.eq(sync_pulse)
        m.d.sync += sync_pulse.eq(self.sync_drained)
        m.d.comb += self.fifo_empty.eq(self.pci_fifo_empty)

        # -- the register table -----------------------------------------------
        # Every register in regdef, through the same busif calls the Scala
        # makes; the field storage is collected per (addr, field name) so the
        # hand-written areas below can reach it.
        regs: dict[int, RegInst] = {}
        fields: dict[tuple[int, str], FieldInst] = {}
        afix: dict[str, AFixValue] = {}       # triangle geometry, by register name
        command_streams: dict[int, object] = {}
        command_regs: dict[int, RegInst] = {}

        # the payload builders are closures over signals declared further down
        payload_builders = {}

        def build_triangle(use_float_shadow: bool):
            def build(reg, payload):
                payload_builders[reg.addr] = (payload, use_float_shadow)
            return build

        def build_nop(reg, payload):
            m.d.comb += payload.eq(cmd.data)

        for reg in regdef.registers(include_sim_only=self.simulation):
            cs = regdef.command_by_addr(reg.addr) if reg.command else None
            if cs is not None:
                if cs.payload == "TriangleSetup.Input":
                    inst, strm = busif.new_command_reg_with_payload(
                        reg.addr, reg.name, reg.category, triangle_setup_input_layout(c),
                        build_triangle(use_float_shadow=(reg.name == "ftriangleCMD")))
                elif cs.payload == "Bits(32)":
                    inst, strm = busif.new_command_reg_with_payload(
                        reg.addr, reg.name, reg.category, unsigned(32), build_nop)
                else:
                    inst, strm = busif.new_command_reg(reg.addr, reg.name, reg.category)
                command_streams[reg.addr] = strm
                command_regs[reg.addr] = inst
            elif reg.category is not None:
                inst = busif.new_reg_at_with_category(reg.addr, reg.name, reg.category)
            else:
                inst = busif.new_reg_at(reg.addr, reg.name)
            regs[reg.addr] = inst

            for f in reg.fields:
                if f.qformat is not None:
                    v = inst.afix_field_at(f.lo, AFixShape.from_q(f.qformat), f.access, f.reset,
                                           name=f.name)
                    busif.with_float_alias(v)
                    afix[reg.name] = v
                else:
                    inst.field_at(f.lo, f.width, f.access, f.reset, name=f.name)
                fields[(reg.addr, f.name)] = inst.fields[-1]
                path = output_path(reg, f)
                if path is not None:
                    port = self._port(path)
                    m.d.comb += _raw(port).eq(inst.fields[-1].signal)

        def fld(addr: int, name: str):
            return fields[(addr, name)].signal

        # -- 1. status (:204-232) ----------------------------------------------
        st = self.status_inputs
        pci_fifo_free = self.pci_fifo_free
        m.d.comb += [
            # io.pciFifoFree.sat(1): 7-bit 0..64 saturated to 6 bits
            fld(0x000, "pciFifoFree").eq(Mux(pci_fifo_free[6], Const(0x3F, 6), pci_fifo_free[:6])),
            fld(0x000, "vRetrace").eq(st.v_retrace),
            fld(0x000, "fbiBusy").eq(self.debug.pipeline_busy),
            fld(0x000, "trexBusy").eq(self.debug.pipeline_busy),
            fld(0x000, "sstBusy").eq(self.debug.pipeline_busy),
            fld(0x000, "displayedBuffer").eq(self.swap_displayed_buffer),
            fld(0x000, "memFifoFree").eq(st.mem_fifo_free),
            fld(0x000, "swapsPending").eq(self.swaps_pending),
            fld(0x000, "pciInterrupt").eq(st.pci_interrupt),
        ]

        # -- statistics (:952-980) ---------------------------------------------
        stats = self.statistics
        m.d.comb += [
            fld(0x14C, "statsPixelsIn").eq(stats.pixels_in),
            fld(0x150, "statsChromaFail").eq(stats.chroma_fail),
            fld(0x154, "statsZFuncFail").eq(stats.z_func_fail),
            fld(0x158, "statsAFuncFail").eq(stats.a_func_fail),
            fld(0x15C, "statsPixelsOut").eq(stats.pixels_out),
        ]

        # -- 10. GenerationFlags.simulation (:1098, :1123) ---------------------
        if self.simulation:
            m.d.comb += [
                fld(0x240, "debugBusy").eq(self.debug.busy.as_value()),
                fld(0x250, "writePathDebug").eq(self.debug.write_path.as_value()),
            ]

        # -- 2/3/4. clutData, palette, float shadow writes (:111-191) ----------
        write_fire = busif.cmd_fire & (cmd.opcode == 1)
        fs = self.float_shadow
        with m.If(fs.valid):
            with m.Switch(fs.payload.address):
                for addr, (name, _shift) in FLOAT_SHADOW_ADDRS.items():
                    with m.Case(addr):
                        m.d.sync += float_shadow[addr].eq(
                            _resize_s(fs.payload.raw, len(float_shadow[addr])))
        with m.Elif(write_fire):
            with m.If(cmd.address == 0x228):
                clut_index = cmd.data[24:30]
                with m.If(clut_index <= 32):
                    with m.Switch(clut_index):
                        for i in range(33):
                            with m.Case(i):
                                m.d.sync += gamma_lut[i].eq(cmd.data[0:24])

            palette_reg_hit = Signal(name="paletteRegHit")
            m.d.comb += palette_reg_hit.eq(0)
            with m.Switch(cmd.address):
                for addr in _PALETTE_ADDRS:
                    with m.Case(addr):
                        m.d.comb += palette_reg_hit.eq(1)
            with m.If(palette_reg_hit & cmd.data[31]):
                even_entry = cmd.data[23:31] & Const(0xFE, 8)
                is_odd_reg = Const(0)
                for addr in _PALETTE_ODD_ADDRS:
                    is_odd_reg = is_odd_reg | (cmd.address == addr)
                m.d.comb += [
                    self.palette_write.valid.eq(1),
                    self.palette_write.payload.address.eq(even_entry | is_odd_reg),
                    self.palette_write.payload.data.eq(cmd.data[0:24]),
                ]
            with m.Switch(cmd.address):
                for addr, (name, shift) in FLOAT_SHADOW_ADDRS.items():
                    with m.Case(addr):
                        reg_ = float_shadow[addr]
                        ext = _resize_s(cmd.data, len(reg_))
                        m.d.sync += reg_.eq(_shl_keep(ext, shift) if shift else ext)

        # -- 7. triangleConfigReg + packed tex layout (:641, :1193-1207, :1235) --
        triangle_config_reg = Signal(per_triangle_config_layout(c), name="triangleConfigReg",
                                     reset_less=True)
        tex_tables_reg = None
        if c.packed_tex_layout:
            tex_cfg = Signal(TexConfigLayout(), name="texCfg")
            tmu_texture_mode = fld(0x300, "textureMode")
            tmu_t_lod = fld(0x304, "tLOD")
            m.d.comb += [
                tex_cfg.tex_base_addr.eq(fld(0x30C, "texBaseAddr")[0:19]),
                tex_cfg.tex_base_addr1.eq(fld(0x310, "texBaseAddr1")[0:19]),
                tex_cfg.tex_base_addr2.eq(fld(0x314, "texBaseAddr2")[0:19]),
                tex_cfg.tex_base_addr38.eq(fld(0x318, "texBaseAddr38")[0:19]),
                tex_cfg.tformat.eq(tmu_texture_mode[8:12]),
                tex_cfg.t_lod_aspect.eq(tmu_t_lod[21:23]),
                tex_cfg.t_lod_s_is_wider.eq(tmu_t_lod[20]),
                tex_cfg.t_lod_multibase.eq(tmu_t_lod[24]),
            ]
            # TexLayoutTables.compute(texCfg): the shared hardware table builder
            tex_tables_comb = compute_tables(m, tex_cfg, name="texTablesComb")
            tex_tables_reg = Signal(tex_layout_tables_layout(), name="triangleTexTablesReg",
                                    reset_less=True)
            m.d.sync += tex_tables_reg.eq(tex_tables_comb)

        cfg_next = Signal(per_triangle_config_layout(c), name="triangleConfigNext")
        self._capture_triangle_config(m, cfg_next, fld, afix, tex_tables_reg)
        m.d.sync += triangle_config_reg.eq(cfg_next)

        # -- 5. triangleCMD / ftriangleCMD payloads (:467-606, :652-673) -------
        for addr, (payload, use_float_shadow) in payload_builders.items():
            self._build_triangle_command_input(
                m, payload, sign_bit=cmd.data[31], use_float_shadow=use_float_shadow,
                afix=afix, float_shadow=float_shadow, triangle_config_reg=triangle_config_reg,
                trace_ids=(triangle_draw_trace_id, triangle_primitive_trace_id))

        # -- 6. command streams to ports, commandReady order (:712-726) --------
        stream_ports = {
            0x080: self.commands.triangle_cmd_stream,
            0x100: self.commands.ftriangle_cmd_stream,
            0x120: self.commands.nop_cmd_stream,
            0x124: self.commands.fastfill_cmd_stream,
            0x128: self.commands.swapbuffer_cmd_stream,
        }
        for addr, port in stream_ports.items():
            strm = command_streams[addr]
            m.d.comb += [port.valid.eq(strm.valid), strm.ready.eq(port.ready)]
            if len(_raw(port.payload)):
                m.d.comb += _raw(port.payload).eq(_raw(strm.payload))
        for idx, (addr, ready) in enumerate(sorted(busif.get_command_stream_ready().items())):
            m.d.comb += self.command_ready[idx].eq(ready)

        # -- 8. trace IDs (:690-697) --------------------------------------------
        if trace:
            swap = self.commands.swapbuffer_cmd_stream
            with m.If(swap.valid & swap.ready):
                m.d.sync += triangle_draw_trace_id.eq(triangle_draw_trace_id + 1)
            with m.If(self.commands.triangle_cmd_stream.valid
                      | self.commands.ftriangle_cmd_stream.valid):
                m.d.sync += triangle_primitive_trace_id.eq(triangle_primitive_trace_id + 1)

        # -- the read mux ----------------------------------------------------------
        busif.finalize()

        # Cross-check the metadata the busif collected against the table that
        # PciFifo is built from (regdef is the single source of truth).
        want = regdef.categories(include_sim_only=False)
        got = busif.get_categories()
        assert got == want, "busif categories diverge from regdef"
        assert tuple(busif.get_command_stream_ready()) == regdef.command_addresses()
        assert set(busif.get_float_aliases()) == set(regdef.float_aliases())
        return m

    # -- captureTriangleConfig (:555-584) -------------------------------------

    def _capture_triangle_config(self, m, cfg, fld, afix, tex_tables_reg):
        c = self.config
        fbz_color_path = fld(0x104, "fbzColorPath")
        fog_mode = fld(0x108, "fogMode")
        alpha_mode = fld(0x10C, "alphaMode")
        m.d.comb += [
            # b.assignFromBits(fbzColorPath.resized) etc.
            cfg.fbz_color_path.as_value().eq(fbz_color_path[:FBZ_COLOR_PATH_LAYOUT.size]),
            cfg.fog_mode.as_value().eq(fog_mode[:FOG_MODE_LAYOUT.size]),
            cfg.alpha_mode.as_value().eq(alpha_mode[:ALPHA_MODE_LAYOUT.size]),
            cfg.enable_clipping.eq(fld(0x110, "enableClipping")),
            cfg.clip_left.eq(fld(0x118, "clipLeftX")),
            cfg.clip_right.eq(fld(0x118, "clipRightX")),
            cfg.clip_low_y.eq(fld(0x11C, "clipLowY")),
            cfg.clip_high_y.eq(fld(0x11C, "clipHighY")),
            cfg.tmu_texture_mode.eq(fld(0x300, "textureMode")[0:12]),
            cfg.tmu_tex_base_addr.eq(fld(0x30C, "texBaseAddr")),
            cfg.tmu_tex_base_addr1.eq(fld(0x310, "texBaseAddr1")),
            cfg.tmu_tex_base_addr2.eq(fld(0x314, "texBaseAddr2")),
            cfg.tmu_tex_base_addr38.eq(fld(0x318, "texBaseAddr38")),
            cfg.tmu_tlod.eq(fld(0x304, "tLOD")[0:27]),
            cfg.tmud_sd_x.raw.eq(afix["dSdX"].raw),
            cfg.tmud_td_x.raw.eq(afix["dTdX"].raw),
            cfg.tmud_sd_y.raw.eq(afix["dSdY"].raw),
            cfg.tmud_td_y.raw.eq(afix["dTdY"].raw),
        ]
        if c.packed_tex_layout:
            m.d.comb += cfg.tex_tables.eq(tex_tables_reg)

    # -- buildTriangleCommandInput (:586-606) ------------------------------------

    def _build_triangle_command_input(self, m, payload, *, sign_bit, use_float_shadow, afix,
                                      float_shadow, triangle_config_reg, trace_ids):
        c = self.config
        hi_w = self._tex_coords_hi_width
        g = afix

        def sel(shadow_addr: int, fixed):
            """``Mux(useFloatShadow, floatShadowX, fixed)`` -- resolved at elaboration."""
            return float_shadow[shadow_addr] if use_float_shadow else fixed

        # TriangleSetup.buildInput: the six vertex raws and the sign bit
        tri = payload.tri_with_sign
        m.d.comb += [
            tri.tri[0][0].raw.eq(g["vertexAx"].raw),
            tri.tri[0][1].raw.eq(g["vertexAy"].raw),
            tri.tri[1][0].raw.eq(g["vertexBx"].raw),
            tri.tri[1][1].raw.eq(g["vertexBy"].raw),
            tri.tri[2][0].raw.eq(g["vertexCx"].raw),
            tri.tri[2][1].raw.eq(g["vertexCy"].raw),
            tri.sign_bit.eq(sign_bit),
        ]

        # captureTriangleGradients (:521-553) via captureTriangleGradientsFrom (:467-477):
        # grad.start.raw := start.asSInt.resize(width) for each of the eight
        sources = [
            ("red_grad", g["startR"].raw, g["dRdX"].raw, g["dRdY"].raw),
            ("green_grad", g["startG"].raw, g["dGdX"].raw, g["dGdY"].raw),
            ("blue_grad", g["startB"].raw, g["dBdX"].raw, g["dBdY"].raw),
            ("depth_grad", g["startZ"].raw, g["dZdX"].raw, g["dZdY"].raw),
            ("alpha_grad", g["startA"].raw, g["dAdX"].raw, g["dAdY"].raw),
            ("w_grad",
             sel(0x03C, _resize_s(g["startW"].raw, 60)),
             sel(0x05C, _resize_s(g["dWdX"].raw, 60)),
             sel(0x07C, _resize_s(g["dWdY"].raw, 60))),
            ("s_grad", g["startS"].raw, g["dSdX"].raw, g["dSdY"].raw),
            ("t_grad", g["startT"].raw, g["dTdX"].raw, g["dTdY"].raw),
        ]
        for name, start, dx, dy in sources:
            grad = getattr(payload.grads, name)
            m.d.comb += [
                grad.start.raw.eq(_resize_s(start, len(grad.start.raw))),
                grad.d[0].raw.eq(_resize_s(dx, len(grad.d[0].raw))),
                grad.d[1].raw.eq(_resize_s(dy, len(grad.d[1].raw))),
            ]

        # captureTriangleHiAlpha (:505-519): startA.raw.asSInt.resize(hi) |<< 18
        hi_a = payload.hi_alpha
        m.d.comb += [
            hi_a.start.raw.eq(sel(0x030, _shl_keep(_resize_s(g["startA"].raw, hi_w), 18))),
            hi_a.d_ad_x.raw.eq(sel(0x050, _shl_keep(_resize_s(g["dAdX"].raw, hi_w), 18))),
            hi_a.d_ad_y.raw.eq(sel(0x070, _shl_keep(_resize_s(g["dAdY"].raw, hi_w), 18))),
        ]

        # captureTriangleHiTexCoords (:479-503): startS.raw.asSInt.resize(hi) |<< 12
        hi_t = payload.tex_hi
        m.d.comb += [
            hi_t.s_start.raw.eq(sel(0x034, _shl_keep(_resize_s(g["startS"].raw, hi_w), 12))),
            hi_t.t_start.raw.eq(sel(0x038, _shl_keep(_resize_s(g["startT"].raw, hi_w), 12))),
            hi_t.d_sd_x.raw.eq(sel(0x054, _shl_keep(_resize_s(g["dSdX"].raw, hi_w), 12))),
            hi_t.d_td_x.raw.eq(sel(0x058, _shl_keep(_resize_s(g["dTdX"].raw, hi_w), 12))),
            hi_t.d_sd_y.raw.eq(sel(0x074, _shl_keep(_resize_s(g["dSdY"].raw, hi_w), 12))),
            hi_t.d_td_y.raw.eq(sel(0x078, _shl_keep(_resize_s(g["dTdY"].raw, hi_w), 12))),
        ]

        # config := triangleConfigReg
        m.d.comb += payload.config.eq(triangle_config_reg)

        if c.trace.enabled:
            draw_id, primitive_id = trace_ids
            m.d.comb += [
                payload.trace.valid.eq(1),
                payload.trace.origin.eq(TraceOrigin.triangle),
                payload.trace.draw_id.eq(draw_id),
                payload.trace.primitive_id.eq(primitive_id),
            ]
