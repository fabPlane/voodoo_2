"""Voodoo Graphics register definition table (pure data, no Amaranth).

Port of the register *declarations* in
``ext/SpinalVoodoo/src/voodoo/frontend/RegisterBank.scala`` (1280 lines) plus the
metadata the regif adapter
``ext/SpinalVoodoo/src/voodoo/bus/BmbBusInterface.scala`` collects for
``ext/SpinalVoodoo/src/voodoo/bus/PciFifo.scala``
(``getCategories`` / ``getFloatAliases`` / ``getCommandStreamReady``) and the
category type in ``ext/SpinalVoodoo/src/voodoo/bus/RegisterCategory.scala``.

Upstream builds this table as a side effect of elaborating ``RegisterBank``:
every ``busif.newRegAt*`` call appends to ``busif.slices`` and, for the
categorised variants, to the ``registerCategories`` / ``floatAliases`` /
``commandStreamReady`` maps that ``Core.scala:102`` then hands to ``PciFifo``.
Here the same information is plain data, so both ``register_bank.py`` (Phase
2.2) and ``pci_fifo.py`` can consume one source of truth and
``docs/amaranth/register-map.md`` can be generated from it.

The table is checked against the real elaboration by
``amaranth/tests/test_regdef.py``, which compares it to
``amaranth/tests/data/registerbank_regmeta.json`` — produced by
``cd fpga && scala-cli run . --main-class voodoo.diff.GenOne -- --dump-regmeta``.

What is *not* here, because it is behaviour rather than layout: the read mux
and the write side effects themselves. Each register records its write effects
as prose in :attr:`Register.effects`; the logic is Phase 2.2's job. See the
module docstring section "Not table-expressible" at the bottom of this file.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field as _dc_field
from typing import Iterator, Mapping, Optional, Sequence

from avoodoo.config import QFormat, SQ, Config

__all__ = [
    "Access",
    "RegisterCategory",
    "FIFO_NO_SYNC",
    "FIFO_WITH_SYNC",
    "BYPASS_FIFO",
    "READ_ONLY",
    "Field",
    "Register",
    "FloatAlias",
    "CommandStream",
    "REGISTERS",
    "FLOAT_ALIASES",
    "COMMAND_STREAMS",
    "FLOAT_ALIAS_OFFSET",
    "FLOAT_CONVERT_INT_BITS",
    "FLOAT_CONVERT_FRAC_BITS",
    "WAS_ENQUEUED_ADDR",
    "REGISTER_SPACE_BYTES",
    "BUS_ADDRESS_WIDTH",
    "BUS_DATA_WIDTH",
    "EXTERNAL_BUS_ADDRESS_WIDTH",
    "REMAP_TABLE",
    "DECODE_QUIRKS",
    "by_addr",
    "by_name",
    "registers",
    "categories",
    "float_aliases",
    "float_alias_by_addr",
    "command_addresses",
    "command_by_addr",
    "decode_addresses",
    "effective_category",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class Access(str, enum.Enum):
    """``spinal.lib.bus.regif.AccessType``, the subset RegisterBank uses.

    The string values are exactly what ``AccessType.toString`` prints, so they
    compare directly against the ``--dump-regmeta`` ground truth.
    """

    RO = "RO"
    WO = "WO"
    RW = "RW"
    #: auto-inserted padding (`RegSlice.fieldNA`). Never declared here; the
    #: cross-check filters it out of the ground truth.
    NA = "NA"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ``RegisterCategory`` and its four values live in
# ``avoodoo/bus/register_category.py`` (the port of ``bus/RegisterCategory.scala``,
# plain data like this file); they are re-exported here so the table and its
# consumers keep reading ``regdef.FIFO_NO_SYNC`` etc.
#
# ``fifoNoSync`` and ``readOnly`` are the same value (RegisterCategory.scala:36
# and :55); the ``spinal_name`` keeps the declaration-site spelling so the
# generated map reads like the Scala.  RegisterBank never passes ``readOnly``:
# the read-only registers are declared with the plain ``newRegAt`` and so get
# **no** category entry at all (see :func:`effective_category`).
from avoodoo.bus.register_category import (  # noqa: E402
    BYPASS_FIFO,
    FIFO_NO_SYNC,
    FIFO_WITH_SYNC,
    READ_ONLY,
    RegisterCategory,
)


@dataclass(frozen=True)
class Field:
    """One ``RegInst.field`` / ``fieldAt`` declaration."""

    name: str
    lo: int
    width: int
    access: Access
    reset: int = 0
    doc: str = ""
    #: Set when the field carries an ``AFix`` fixed-point value; this is the
    #: ``QFormat`` the Scala ``AFix(config.<x>Format)`` prototype was built
    #: with, and ``width`` equals its raw bit width.
    qformat: Optional[QFormat] = None

    @property
    def hi(self) -> int:
        return self.lo + self.width - 1

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lo

    def __post_init__(self) -> None:
        if self.width < 1:
            raise ValueError(f"field {self.name}: width {self.width} < 1")
        if self.lo < 0:
            raise ValueError(f"field {self.name}: lo {self.lo} < 0")
        if self.qformat is not None and self.qformat.width != self.width:
            raise ValueError(
                f"field {self.name}: qformat {self.qformat} is "
                f"{self.qformat.width} bits, field is {self.width}"
            )
        if self.reset >> self.width:
            raise ValueError(
                f"field {self.name}: reset 0x{self.reset:x} does not fit in "
                f"{self.width} bits"
            )


@dataclass(frozen=True)
class Register:
    """One ``busif.newRegAt*`` declaration (one 32-bit word)."""

    addr: int
    name: str
    doc: str
    fields: tuple[Field, ...]
    #: ``None`` for the registers declared with the plain ``newRegAt``
    #: (status and the five statistics counters): they are absent from
    #: ``getCategories`` and therefore hit PciFifo's "unregistered addresses
    #: bypass FIFO" default (PciFifo.scala:456).
    category: Optional[RegisterCategory] = None
    width: int = 32
    #: Name of the command stream this register pulses, if any.
    command: Optional[str] = None
    #: Present only under ``GenerationFlags.simulation``.
    sim_only: bool = False
    #: Write-side effects that are *not* "store the field" — described here,
    #: implemented in ``register_bank.py``.
    effects: tuple[str, ...] = ()
    #: Free-form notes (datasheet deviations, ignored bits, …).
    notes: tuple[str, ...] = ()

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"{self.name} has no field {name!r}")

    @property
    def declared_mask(self) -> int:
        m = 0
        for f in self.fields:
            m |= f.mask
        return m

    @property
    def reset_value(self) -> int:
        v = 0
        for f in self.fields:
            v |= f.reset << f.lo
        return v

    def __post_init__(self) -> None:
        if self.addr % 4:
            raise ValueError(f"{self.name}: address 0x{self.addr:03x} not word aligned")
        seen = 0
        for f in self.fields:
            if f.hi >= self.width:
                raise ValueError(
                    f"{self.name}.{f.name}: [{f.hi}:{f.lo}] outside "
                    f"{self.width}-bit register"
                )
            if seen & f.mask:
                raise ValueError(f"{self.name}.{f.name}: overlaps an earlier field")
            seen |= f.mask


@dataclass(frozen=True)
class FloatAlias:
    """A ``.withFloatAlias()`` entry (BmbBusInterface.scala:222).

    A write to :attr:`addr` (= ``target_addr + 0x080``) is *not* a register of
    its own: PciFifo converts the IEEE-754 single at the write data into the
    fixed-point format of the target register and enqueues that instead
    (PciFifo.scala:199-247). Conversion is
    ``Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1)`` — i.e. always to
    an ``SQ(50, 30)`` intermediate — followed by an arithmetic right shift of
    :attr:`shift` = ``30 - frac_bits`` with a "+1 if negative and any bit was
    shifted out" correction (``PciFifo.floatCorrected``, PciFifo.scala:152).
    The unshifted 50-bit result is also broadcast back to RegisterBank as the
    ``floatShadow`` write, which keeps the extra precision for
    ``ftriangleCMD``.

    ``int_bits`` / ``frac_bits`` are ``AFix.intWidth`` / ``AFix.fracWidth`` of
    the target register's prototype, i.e. ``QFormat.non_fraction`` and
    ``QFormat.fraction``.
    """

    addr: int
    name: str
    target_addr: int
    target_name: str
    int_bits: int
    frac_bits: int
    doc: str = ""
    #: Every float alias address is registered as ``fifoNoSync``
    #: (BmbBusInterface.scala:227).
    category: RegisterCategory = FIFO_NO_SYNC

    @property
    def shift(self) -> int:
        """``30 - fracBits`` — PciFifo.scala:482."""
        return FLOAT_CONVERT_FRAC_BITS - self.frac_bits

    @property
    def qformat(self) -> QFormat:
        return SQ(self.int_bits + self.frac_bits, self.frac_bits)


@dataclass(frozen=True)
class CommandStream:
    """A ``newCommandReg`` / ``newCommandRegWithPayload`` stream."""

    addr: int
    name: str
    payload: str
    doc: str
    #: Index into ``io.commandReady`` / ``PciFifo.io.commandReady``. Upstream
    #: sorts ``getCommandStreamReady`` by address (RegisterBank.scala:722), so
    #: this is just the rank of ``addr``.
    ready_index: int


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: ``BmbBusInterface.FLOAT_ALIAS_OFFSET`` (BmbBusInterface.scala:95).
FLOAT_ALIAS_OFFSET = 0x080
#: ``Fpxx2AFix(20 bits, 30 bits, ...)`` in PciFifo.scala:200.
FLOAT_CONVERT_INT_BITS = 20
FLOAT_CONVERT_FRAC_BITS = 30
#: ``Core.scala:210`` — ``pciFifo.io.wasEnqueuedAddr := U(0x128)``. PciFifo
#: raises ``wasEnqueued`` when a *non-texture* write to this address is pushed
#: into the 64-entry FIFO (PciFifo.scala:270); Core feeds it to
#: ``SwapBuffer.io.swapCmdEnqueued`` and ``RegisterBank.io.swapCmdEnqueued``.
WAS_ENQUEUED_ADDR = 0x128
#: ``SizeMapping(0x000, 4 KiB)`` in RegisterBank.scala:83.
REGISTER_SPACE_BYTES = 4096
#: ``RegisterBank.bmbParams`` (RegisterBank.scala:1258).
BUS_ADDRESS_WIDTH = 12
BUS_DATA_WIDTH = 32
#: ``RegisterBank.externalBmbParams`` (RegisterBank.scala:1270) — 22 bits so
#: bit 21 (the remap selector) survives as far as ``AddressRemapper``.
EXTERNAL_BUS_ADDRESS_WIDTH = 22

_C: Config = Config.voodoo1()

_VERTEX = _C.vertex_format          # SQ(16, 4)   — datasheet 12.4
_COLOR = _C.v_color_format          # SQ(24, 12)  — datasheet 12.12
_DEPTH = _C.v_depth_format          # SQ(32, 12)  — datasheet 20.12
_TEXC = _C.tex_coords_format        # SQ(32, 18)  — datasheet 14.18
_W = _C.w_format                    # SQ(32, 30)  — datasheet 2.30


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------


def _geom(addr: int, name: str, fmt: QFormat, reset: int, doc: str) -> Register:
    """``RegisterBank.triangleGeometry.fifoNoSyncFloatReg`` (RegisterBank.scala:238).

    ``newRegAtWithCategory(addr, name, fifoNoSync).field(AFix(fmt), WO, reset,
    doc).withFloatAlias().asOutput()`` — one AFix field at bit 0, WO, plus a
    float alias at ``addr + 0x080``.
    """
    return Register(
        addr=addr,
        name=name,
        doc=doc,
        category=FIFO_NO_SYNC,
        fields=(Field(name, 0, fmt.width, Access.WO, reset, doc, fmt),),
    )


def _fog_pair(addr: int, index: int) -> Register:
    """``RegisterBank.fogEntryPair`` (RegisterBank.scala:614). Two fog entries per word."""
    e0, e1 = index * 2, index * 2 + 1
    return Register(
        addr=addr,
        name=f"fogTable{e0}",
        doc=f"Fog table entries {e0} and {e1}",
        category=FIFO_WITH_SYNC,
        fields=(
            Field(f"entry{e0}Dfog", 0, 8, Access.WO, 0, f"Fog entry {e0} delta"),
            Field(f"entry{e0}Fog", 8, 8, Access.WO, 0, f"Fog entry {e0} value"),
            Field(f"entry{e1}Dfog", 16, 8, Access.WO, 0, f"Fog entry {e1} delta"),
            Field(f"entry{e1}Fog", 24, 8, Access.WO, 0, f"Fog entry {e1} value"),
        ),
    )


def _ncc(table: int, kind: str, base_addr: int) -> list[Register]:
    """``RegisterBank.nccBlock`` (RegisterBank.scala:629). Four 32-bit words."""
    out = []
    for i in range(4):
        name = f"nccTable{table}{kind}{i}"
        doc = f"NCC table {table} {kind}{i}"
        effects: tuple[str, ...] = ()
        notes: tuple[str, ...] = ()
        if kind in ("I", "Q"):
            effects = (
                "if data[31] is set this is a YIQ palette write, not an NCC "
                "write: io.paletteWrite fires with address = "
                "(data[30:23] & 0xfe) | (1 if the register is the odd one of "
                "its pair) and data = data[23:0] (RegisterBank.scala:138-158)",
            )
            notes = (
                "palette decode covers 0x334..0x350 only, i.e. NCC table 0's "
                "I and Q blocks; table 1 (0x364..0x380) has no palette path",
            )
        out.append(
            Register(
                addr=base_addr + i * 4,
                name=name,
                doc=doc,
                category=FIFO_WITH_SYNC,
                fields=(Field(name, 0, 32, Access.WO, 0, doc),),
                effects=effects,
                notes=notes,
            )
        )
    return out


def _bits32(
    addr: int,
    name: str,
    doc: str,
    category: RegisterCategory,
    access: Access = Access.WO,
    **kw: object,
) -> Register:
    return Register(
        addr=addr,
        name=name,
        doc=doc,
        category=category,
        fields=(Field(name, 0, 32, access, 0, doc),),
        **kw,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

_REGISTERS: list[Register] = [
    # -- Status (RegisterBank.scala:204) ------------------------------------
    Register(
        addr=0x000,
        name="status",
        doc="SST-1 status",
        category=None,
        fields=(
            Field("pciFifoFree", 0, 6, Access.RO, 0x3F, "PCI FIFO freespace"),
            Field("vRetrace", 6, 1, Access.RO, 0, "Vertical retrace"),
            Field("fbiBusy", 7, 1, Access.RO, 0, "FBI graphics engine busy"),
            Field("trexBusy", 8, 1, Access.RO, 0, "TREX busy"),
            Field("sstBusy", 9, 1, Access.RO, 0, "SST-1 busy"),
            Field("displayedBuffer", 10, 2, Access.RO, 0, "Displayed buffer"),
            Field("memFifoFree", 12, 16, Access.RO, 0xFFFF, "Memory FIFO freespace"),
            Field("swapsPending", 28, 3, Access.RO, 0, "Swap buffers pending"),
            Field("pciInterrupt", 31, 1, Access.RO, 0, "PCI interrupt generated"),
        ),
        effects=(
            "read-only: every field is driven combinationally from an io input "
            "(RegisterBank.scala:206-231); pciFifoFree is io.pciFifoFree.sat(1), "
            "saturating the 7-bit 0..64 free count to 6 bits",
        ),
        notes=("no category entry, so PciFifo treats it as FIFO=No",),
    ),
    # -- Triangle geometry (RegisterBank.scala:237) -------------------------
    _geom(0x008, "vertexAx", _VERTEX, 0, "Vertex A X coordinate"),
    _geom(0x00C, "vertexAy", _VERTEX, 0, "Vertex A Y coordinate"),
    _geom(0x010, "vertexBx", _VERTEX, 0, "Vertex B X coordinate"),
    _geom(0x014, "vertexBy", _VERTEX, 0, "Vertex B Y coordinate"),
    _geom(0x018, "vertexCx", _VERTEX, 0, "Vertex C X coordinate"),
    _geom(0x01C, "vertexCy", _VERTEX, 0, "Vertex C Y coordinate"),
    _geom(0x020, "startR", _COLOR, 255 << 12, "Starting red value (12.12 fixed)"),
    _geom(0x024, "startG", _COLOR, 255 << 12, "Starting green value (12.12 fixed)"),
    _geom(0x028, "startB", _COLOR, 255 << 12, "Starting blue value (12.12 fixed)"),
    _geom(0x02C, "startZ", _DEPTH, 0, "Starting Z depth (20.12 fixed)"),
    _geom(0x030, "startA", _COLOR, 0, "Starting alpha value (12.12 fixed)"),
    _geom(0x034, "startS", _TEXC, 0, "Starting S texture coord (14.18 fixed)"),
    _geom(0x038, "startT", _TEXC, 0, "Starting T texture coord (14.18 fixed)"),
    _geom(0x03C, "startW", _W, 0, "Starting W value (2.30 fixed)"),
    _geom(0x040, "dRdX", _COLOR, 0, "Red gradient dR/dX (12.12 fixed)"),
    _geom(0x044, "dGdX", _COLOR, 0, "Green gradient dG/dX (12.12 fixed)"),
    _geom(0x048, "dBdX", _COLOR, 0, "Blue gradient dB/dX (12.12 fixed)"),
    _geom(0x04C, "dZdX", _DEPTH, 0, "Z gradient dZ/dX (20.12 fixed)"),
    _geom(0x050, "dAdX", _COLOR, 0, "Alpha gradient dA/dX (12.12 fixed)"),
    _geom(0x054, "dSdX", _TEXC, 0, "S texture gradient dS/dX (14.18 fixed)"),
    _geom(0x058, "dTdX", _TEXC, 0, "T texture gradient dT/dX (14.18 fixed)"),
    _geom(0x05C, "dWdX", _W, 0, "W gradient dW/dX (2.30 fixed)"),
    _geom(0x060, "dRdY", _COLOR, 0, "Red gradient dR/dY (12.12 fixed)"),
    _geom(0x064, "dGdY", _COLOR, 0, "Green gradient dG/dY (12.12 fixed)"),
    _geom(0x068, "dBdY", _COLOR, 0, "Blue gradient dB/dY (12.12 fixed)"),
    _geom(0x06C, "dZdY", _DEPTH, 0, "Z gradient dZ/dY (20.12 fixed)"),
    _geom(0x070, "dAdY", _COLOR, 0, "Alpha gradient dA/dY (12.12 fixed)"),
    _geom(0x074, "dSdY", _TEXC, 0, "S texture gradient dS/dY (14.18 fixed)"),
    _geom(0x078, "dTdY", _TEXC, 0, "T texture gradient dT/dY (14.18 fixed)"),
    _geom(0x07C, "dWdY", _W, 0, "W gradient dW/dY (2.30 fixed)"),
    # -- Commands (RegisterBank.scala:651) ----------------------------------
    Register(
        addr=0x080,
        name="triangleCMD",
        doc="Execute TRIANGLE command (fixed-point geometry)",
        category=FIFO_NO_SYNC,
        command="triangleCMD",
        fields=(),
        effects=(
            "pulse: newCommandRegWithPayload(TriangleSetup.Input) — the whole "
            "triangle state is sampled in the write cycle by "
            "buildTriangleCommandInput(io.bus.cmd.data(31), useFloatShadow = "
            "False) (RegisterBank.scala:652-662, :586)",
            "data[31] is the area sign bit, not a stored field",
        ),
        notes=(
            "declares no regif field, so the elaborated slice shows one "
            "auto-inserted NA [31:0] padding field",
        ),
    ),
    Register(
        addr=0x100,
        name="ftriangleCMD",
        doc="Execute TRIANGLE command (float shadow geometry)",
        category=FIFO_NO_SYNC,
        command="ftriangleCMD",
        fields=(),
        effects=(
            "same as triangleCMD but with useFloatShadow = True, so S/T/W/A "
            "come from the 50-bit floatShadow registers instead of the "
            "truncated fixed registers (RegisterBank.scala:663-673)",
        ),
        notes=("no regif field; NA [31:0] padding in the elaborated slice",),
    ),
    Register(
        addr=0x120,
        name="nopCMD",
        doc="Execute NOP command",
        category=FIFO_WITH_SYNC,
        command="nopCMD",
        fields=(Field("nopCmdData", 0, 32, Access.RW, 0, "NOP command data"),),
        effects=("pulse with the written word as payload (RegisterBank.scala:674)",),
    ),
    Register(
        addr=0x124,
        name="fastfillCMD",
        doc="Execute FASTFILL command",
        category=FIFO_WITH_SYNC,
        command="fastfillCMD",
        fields=(Field("fastfillCmdData", 0, 32, Access.RW, 0, "Fastfill command data"),),
        effects=("pulse, no payload (newCommandReg, RegisterBank.scala:685)",),
    ),
    Register(
        addr=0x128,
        name="swapbufferCMD",
        doc="Execute SWAPBUFFER command",
        category=FIFO_WITH_SYNC,
        command="swapbufferCMD",
        fields=(
            Field("swapVsyncEnable", 0, 1, Access.RW, 0, "Vsync synchronization enable"),
            Field("swapInterval", 1, 8, Access.RW, 0, "Swap interval (vsyncs to wait)"),
        ),
        effects=(
            "pulse, no payload (newCommandReg, RegisterBank.scala:687)",
            "this is the address PciFifo watches for wasEnqueued "
            "(Core.scala:210) -> SwapBuffer.swapCmdEnqueued",
            "with config.trace.enabled the fire also bumps triangleDrawTraceId "
            "(RegisterBank.scala:691)",
        ),
    ),
    # -- Render configuration (RegisterBank.scala:732) ----------------------
    _bits32(0x104, "fbzColorPath", "Color combine path control", FIFO_NO_SYNC, Access.RW),
    _bits32(0x108, "fogMode", "Fog mode control", FIFO_NO_SYNC, Access.RW),
    _bits32(0x10C, "alphaMode", "Alpha test and blend control", FIFO_NO_SYNC, Access.RW),
    Register(
        addr=0x110,
        name="fbzMode",
        doc="RGB buffer and depth-buffer control",
        category=FIFO_WITH_SYNC,
        fields=(
            Field("enableClipping", 0, 1, Access.RW, 0, "Enable clipping rectangle"),
            Field("enableChromaKey", 1, 1, Access.RW, 0, "Enable chroma-keying"),
            Field("enableStipple", 2, 1, Access.RW, 0, "Enable stipple register masking"),
            Field("wBufferSelect", 3, 1, Access.RW, 0, "W-Buffer Select (0=Z-value, 1=W-value)"),
            Field("enableDepthBuffer", 4, 1, Access.RW, 0, "Enable depth-buffering"),
            Field("depthFunction", 5, 3, Access.RW, 0, "Depth-buffer function"),
            Field("enableDithering", 8, 1, Access.RW, 0, "Enable dithering"),
            Field("rgbBufferMask", 9, 1, Access.RW, 0, "RGB buffer write mask"),
            Field("auxBufferMask", 10, 1, Access.RW, 0, "Depth/alpha buffer write mask"),
            Field("ditherAlgorithm", 11, 1, Access.RW, 0, "Dither algorithm (0=4x4, 1=2x2)"),
            Field("enableStipplePattern", 12, 1, Access.RW, 0, "Enable Stipple pattern masking"),
            Field("enableAlphaMask", 13, 1, Access.RW, 0, "Enable Alpha-channel mask"),
            Field("drawBuffer", 14, 2, Access.RW, 0, "Draw buffer (0=Front, 1=Back, 2-3=Reserved)"),
            Field("enableDepthBias", 16, 1, Access.RW, 0, "Enable depth-biasing"),
            Field("yOrigin", 17, 1, Access.RW, 0, "Y origin (0=top, 1=bottom)"),
            Field("enableAlphaPlanes", 18, 1, Access.RW, 0, "Enable alpha planes"),
            Field(
                "enableDitherSubtract",
                19,
                1,
                Access.RW,
                0,
                "Enable alpha-blending dither subtraction",
            ),
            Field(
                "depthSourceSelect",
                20,
                1,
                Access.RW,
                0,
                "Depth source (0=normal, 1=zaColor[15:0])",
            ),
            Field("reserved", 21, 11, Access.RW, 0, "Reserved"),
        ),
    ),
    Register(
        addr=0x114,
        name="lfbMode",
        doc="Linear frame buffer mode control",
        category=FIFO_WITH_SYNC,
        fields=(
            Field("writeFormat", 0, 4, Access.RW, 0, "Linear frame buffer write format"),
            Field(
                "writeBufferSelect",
                4,
                2,
                Access.RW,
                0,
                "Write buffer select (0=front, 1=back, 2-3=reserved)",
            ),
            Field(
                "readBufferSelect",
                6,
                2,
                Access.RW,
                0,
                "Read buffer select (0=front, 1=back, 2=depth/alpha, 3=reserved)",
            ),
            Field(
                "pixelPipelineEnable",
                8,
                1,
                Access.RW,
                0,
                "Enable pixel pipeline for LFB writes",
            ),
            Field("rgbaLanes", 9, 2, Access.RW, 0, "RGBA lanes (0=ARGB, 1=ABGR, 2=RGBA, 3=BGRA)"),
            Field("wordSwapWrites", 11, 1, Access.RW, 0, "16-bit word swap LFB writes"),
            Field("byteSwizzleWrites", 12, 1, Access.RW, 0, "Byte swizzle LFB writes"),
            Field("yOrigin", 13, 1, Access.RW, 0, "LFB Y origin (0=top, 1=bottom)"),
            Field("wSelect", 14, 1, Access.RW, 0, "LFB write W select (0=LFB, 1=zaColor[15:0])"),
            Field("wordSwapReads", 15, 1, Access.RW, 0, "16-bit word swap LFB reads"),
            Field("byteSwizzleReads", 16, 1, Access.RW, 0, "Byte swizzle LFB reads"),
            Field("reserved", 17, 15, Access.RW, 0, "Reserved"),
        ),
    ),
    Register(
        addr=0x118,
        name="clipLeftRight",
        doc="Left and right clipping boundaries",
        category=FIFO_WITH_SYNC,
        fields=(
            Field("clipRightX", 0, 10, Access.RW, 0x3FF, "Right clip boundary"),
            Field("clipLeftX", 16, 10, Access.RW, 0, "Left clip boundary"),
        ),
    ),
    Register(
        addr=0x11C,
        name="clipLowYHighY",
        doc="Top and bottom clipping boundaries",
        category=FIFO_WITH_SYNC,
        fields=(
            Field("clipHighY", 0, 10, Access.RW, 0x3FF, "Bottom clip boundary"),
            Field("clipLowY", 16, 10, Access.RW, 0, "Top clip boundary"),
        ),
    ),
    _bits32(0x12C, "fogColor", "RGBA fog color", FIFO_WITH_SYNC),
    _bits32(0x130, "zaColor", "Z/alpha constant for fills", FIFO_WITH_SYNC),
    _bits32(0x134, "chromaKey", "Chroma key color", FIFO_WITH_SYNC),
    _bits32(0x140, "stipple", "Stipple pattern", FIFO_WITH_SYNC, Access.RW),
    _bits32(0x144, "color0", "Constant color 0", FIFO_WITH_SYNC, Access.RW),
    _bits32(0x148, "color1", "Constant color 1", FIFO_WITH_SYNC, Access.RW),
    # -- Statistics (RegisterBank.scala:952) --------------------------------
    Register(
        addr=0x14C,
        name="fbiPixelsIn",
        doc="Pixel counter: pixels processed",
        category=None,
        fields=(Field("statsPixelsIn", 0, 24, Access.RO, 0, "Pixels entering pipeline"),),
        effects=("driven from io.statistics.pixelsIn",),
        notes=("plain newRegAt: no category entry, so PciFifo treats it as FIFO=No",),
    ),
    Register(
        addr=0x150,
        name="fbiChromaFail",
        doc="Pixel counter: failed chroma key test",
        category=None,
        fields=(Field("statsChromaFail", 0, 24, Access.RO, 0, "Pixels failed chroma key test"),),
        effects=("driven from io.statistics.chromaFail",),
        notes=("plain newRegAt: no category entry, so PciFifo treats it as FIFO=No",),
    ),
    Register(
        addr=0x154,
        name="fbiZfuncFail",
        doc="Pixel counter: failed Z test",
        category=None,
        fields=(Field("statsZFuncFail", 0, 24, Access.RO, 0, "Pixels failed Z test"),),
        effects=("driven from io.statistics.zFuncFail",),
        notes=("plain newRegAt: no category entry, so PciFifo treats it as FIFO=No",),
    ),
    Register(
        addr=0x158,
        name="fbiAfuncFail",
        doc="Pixel counter: failed alpha test",
        category=None,
        fields=(Field("statsAFuncFail", 0, 24, Access.RO, 0, "Pixels failed alpha test"),),
        effects=("driven from io.statistics.aFuncFail",),
        notes=("plain newRegAt: no category entry, so PciFifo treats it as FIFO=No",),
    ),
    Register(
        addr=0x15C,
        name="fbiPixelsOut",
        doc="Pixel counter: pixels drawn",
        category=None,
        fields=(Field("statsPixelsOut", 0, 24, Access.RO, 0, "Pixels written to framebuffer"),),
        effects=("driven from io.statistics.pixelsOut",),
        notes=("plain newRegAt: no category entry, so PciFifo treats it as FIFO=No",),
    ),
]

# -- Fog table, 0x160..0x1DC (RegisterBank.scala:983) -----------------------
_REGISTERS += [_fog_pair(0x160 + i * 4, i) for i in range(32)]

# -- Initialisation area, 0x200..0x24C (RegisterBank.scala:991) -------------
_REGISTERS += [
    Register(
        addr=0x200,
        name="fbiInit4",
        doc="FBI hardware initialization (register 4)",
        category=BYPASS_FIFO,
        fields=(
            Field(
                "fbiInit4_pciReadWaitStates",
                0,
                1,
                Access.RW,
                0,
                "PCI read wait states: 0=1 wait state, 1=2 wait states",
            ),
        ),
    ),
    _bits32(0x208, "backPorch", "Display back porch timing", BYPASS_FIFO),
    Register(
        addr=0x20C,
        name="videoDimensions",
        doc="Video screen dimensions",
        category=BYPASS_FIFO,
        fields=(
            Field("hDisp", 0, 12, Access.WO, 0, "Horizontal display width - 1"),
            Field("vDisp", 16, 12, Access.WO, 0, "Vertical display height"),
        ),
    ),
    Register(
        addr=0x210,
        name="fbiInit0",
        doc="FBI hardware initialization (register 0)",
        category=BYPASS_FIFO,
        fields=(
            Field(
                "fbiInit0_vgaPassthrough",
                0,
                1,
                Access.RW,
                0,
                "VGA passthrough: 0=VGA blocked, 1=VGA passed",
            ),
            Field("fbiInit0_graphicsReset", 1, 1, Access.RW, 0, "Graphics reset"),
        ),
    ),
    Register(
        addr=0x214,
        name="fbiInit1",
        doc="FBI hardware initialization (register 1)",
        category=BYPASS_FIFO,
        fields=(
            Field(
                "fbiInit1_pciWriteWaitStates",
                1,
                1,
                Access.RW,
                0,
                "PCI write wait states: 0=fast, 1=slow",
            ),
            Field("fbiInit1_multiSst", 2, 1, Access.RW, 0, "Multi-SST (SLI) mode [V1]"),
            Field(
                "fbiInit1_videoTilesX",
                4,
                4,
                Access.RW,
                10,
                "Video tiles in X / 2 (stride = val * 64 pixels)",
            ),
            Field("fbiInit1_videoReset", 8, 1, Access.RW, 0, "Video timing reset"),
        ),
        notes=("yOriginSwap lives in fbiInit3, not here (RegisterBank.scala:1041)",),
    ),
    Register(
        addr=0x218,
        name="fbiInit2",
        doc="FBI hardware initialization (register 2)",
        category=BYPASS_FIFO,
        fields=(
            Field(
                "fbiInit2_swapAlgorithm",
                9,
                2,
                Access.RW,
                0,
                "Swap algorithm: 0=DAC vsync, 1=DAC data, 2=PCI FIFO stall, 3=SLI sync",
            ),
            Field("fbiInit2_bufferOffset", 11, 9, Access.RW, 0, "Buffer offset in 4KB units"),
        ),
    ),
    Register(
        addr=0x21C,
        name="fbiInit3",
        doc="FBI hardware initialization (register 3)",
        category=BYPASS_FIFO,
        fields=(
            Field(
                "fbiInit3_remapEnable",
                0,
                1,
                Access.RW,
                0,
                "Enable register address remapping",
            ),
            Field(
                "fbiInit3_yOriginSwap",
                22,
                10,
                Access.RW,
                0,
                "Y origin swap subtraction value",
            ),
        ),
        notes=(
            "remapEnable is an output of RegisterBank but AddressRemapper does "
            "not read it: the remap is keyed purely on address bit 21 "
            "(AddressRemapper.scala:82)",
        ),
    ),
    Register(
        addr=0x220,
        name="hSync",
        doc="Horizontal sync timing generator",
        category=BYPASS_FIFO,
        fields=(
            Field("hSyncOn", 0, 8, Access.WO, 0, "Horizontal sync start"),
            Field("hSyncOff", 16, 10, Access.WO, 0, "Horizontal sync end"),
        ),
    ),
    Register(
        addr=0x224,
        name="vSync",
        doc="Vertical sync timing generator",
        category=BYPASS_FIFO,
        fields=(
            Field("vSyncOn", 0, 16, Access.WO, 0, "Vertical sync start (lines)"),
            Field("vSyncOff", 16, 16, Access.WO, 0, "Vertical sync end (lines)"),
        ),
    ),
    Register(
        addr=0x228,
        name="clutData",
        doc="Gamma CLUT entry write",
        category=BYPASS_FIFO,
        fields=(Field("clutData", 0, 32, Access.WO, 0, "Gamma CLUT entry write"),),
        effects=(
            "side effect: with index = data[29:24], if index <= 32 then "
            "gammaLut[index] := data[23:0] (RegisterBank.scala:131-136). The "
            "33-entry gammaLut resets to i*8 replicated over RGB for i in "
            "0..31 and 0xffffff for entry 32 (RegisterBank.scala:90-95)",
        ),
    ),
    _bits32(0x22C, "dacData", "DAC register data", BYPASS_FIFO),
    _bits32(0x230, "maxRgbDelta", "Max RGB difference for video filtering", BYPASS_FIFO),
    Register(
        addr=0x240,
        name="debugBusy",
        doc="Internal pipeline busy-source debug bitfield",
        category=None,
        sim_only=True,
        fields=(
            Field(
                "debugBusy",
                0,
                32,
                Access.RO,
                0,
                "Internal pipeline busy-source debug bitfield",
            ),
        ),
        effects=("driven from io.debug.busy.bitsValue",),
        notes=(
            "inside GenerationFlags.simulation (RegisterBank.scala:1098), so "
            "absent from a synthesis elaboration and from the --dump-regmeta "
            "ground truth",
        ),
    ),
    Register(
        addr=0x244,
        name="fbiInit5",
        doc="FBI hardware initialization (register 5)",
        category=BYPASS_FIFO,
        fields=(
            Field("fbiInit5_multiCvg", 14, 1, Access.RW, 0, "Multi-chip coverage (SLI for V2)"),
        ),
    ),
    Register(
        addr=0x248,
        name="fbiInit6",
        doc="FBI hardware initialization (register 6)",
        category=BYPASS_FIFO,
        fields=(
            Field(
                "fbiInit6_blockWidthExtend",
                30,
                1,
                Access.RW,
                0,
                "Extends block width calculation",
            ),
        ),
    ),
    Register(
        addr=0x24C,
        name="fbiInit7",
        doc="FBI hardware initialization (register 7)",
        category=BYPASS_FIFO,
        fields=(
            Field(
                "fbiInit7_cmdFifoEnable",
                8,
                1,
                Access.RW,
                0,
                "Enable command FIFO mode [V2+]",
            ),
        ),
    ),
    Register(
        addr=0x250,
        name="writePathDebug",
        doc="Write-side pipeline and arbiter handshake debug bitfield",
        category=None,
        sim_only=True,
        fields=(
            Field(
                "writePathDebug",
                0,
                32,
                Access.RO,
                0,
                "Write-side pipeline and arbiter handshake debug bitfield",
            ),
        ),
        effects=("driven from io.debug.writePath.bitsValue",),
        notes=(
            "inside GenerationFlags.simulation (RegisterBank.scala:1123), so "
            "absent from a synthesis elaboration and from the --dump-regmeta "
            "ground truth",
        ),
    ),
    # -- TMU configuration, 0x300..0x320 (RegisterBank.scala:1142) ----------
    _bits32(0x300, "textureMode", "Texture mode", FIFO_NO_SYNC),
    _bits32(0x304, "tLOD", "LOD configuration", FIFO_NO_SYNC),
    _bits32(0x308, "tDetail", "Detail texture params", FIFO_NO_SYNC),
    Register(
        addr=0x30C,
        name="texBaseAddr",
        doc="Texture base address",
        category=FIFO_NO_SYNC,
        fields=(Field("texBaseAddr", 0, 24, Access.WO, 0, "Texture base address"),),
        effects=(
            "relocation: CPU texture *reads* through the 0x800000+ PCI window "
            "are offset by this register — CoreCpuFrontdoor.scala:46-48 does "
            "texReadSramAddr = (texBaseAddr[18:0] << 3) + pciOffset[22:0]. CPU "
            "texture *writes* are not relocated; they carry the raw PCI offset "
            "through PciFifo.texDrain",
            "with config.packedTexLayout the low 19 bits also feed "
            "TexLayoutTables.compute (RegisterBank.scala:1193-1207)",
        ),
        notes=("the datasheet defines only bits [18:0]; the register is 24 bits here",),
    ),
    Register(
        addr=0x310,
        name="texBaseAddr_1",
        doc="Texture base address (supplemental LOD 1)",
        category=FIFO_NO_SYNC,
        fields=(Field("texBaseAddr1", 0, 24, Access.WO, 0, "Texture base address for LOD 1"),),
    ),
    Register(
        addr=0x314,
        name="texBaseAddr_2",
        doc="Texture base address (supplemental LOD 2)",
        category=FIFO_NO_SYNC,
        fields=(Field("texBaseAddr2", 0, 24, Access.WO, 0, "Texture base address for LOD 2"),),
    ),
    Register(
        addr=0x318,
        name="texBaseAddr_3_8",
        doc="Texture base address (supplemental LOD 3-8)",
        category=FIFO_NO_SYNC,
        fields=(
            Field(
                "texBaseAddr38",
                0,
                24,
                Access.WO,
                0,
                "Texture base address for LOD 3 through 8",
            ),
        ),
    ),
    _bits32(0x31C, "trexInit0", "TMU init 0", FIFO_WITH_SYNC),
    _bits32(0x320, "trexInit1", "TMU init 1", FIFO_WITH_SYNC),
]

# -- NCC tables, 0x324..0x380 (RegisterBank.scala:1213) ---------------------
_REGISTERS += _ncc(0, "Y", 0x324)
_REGISTERS += _ncc(0, "I", 0x334)
_REGISTERS += _ncc(0, "Q", 0x344)
_REGISTERS += _ncc(1, "Y", 0x354)
_REGISTERS += _ncc(1, "I", 0x364)
_REGISTERS += _ncc(1, "Q", 0x374)

#: Every declared register, sorted by address.
REGISTERS: tuple[Register, ...] = tuple(sorted(_REGISTERS, key=lambda r: r.addr))


# ---------------------------------------------------------------------------
# Float aliases (BmbBusInterface.scala:222, applied by triangleGeometry)
# ---------------------------------------------------------------------------


def _build_float_aliases() -> tuple[FloatAlias, ...]:
    out = []
    for reg in REGISTERS:
        if reg.category is not FIFO_NO_SYNC or not (0x008 <= reg.addr <= 0x07C):
            continue
        fmt = reg.fields[0].qformat
        assert fmt is not None, reg.name
        out.append(
            FloatAlias(
                addr=reg.addr + FLOAT_ALIAS_OFFSET,
                name="f" + reg.name,
                target_addr=reg.addr,
                target_name=reg.name,
                int_bits=fmt.non_fraction,
                frac_bits=fmt.fraction,
                doc=f"{reg.doc} (IEEE-754 single)",
            )
        )
    return tuple(out)


#: The 30 ``addr + 0x080`` float aliases of the triangle geometry registers.
FLOAT_ALIASES: tuple[FloatAlias, ...] = _build_float_aliases()


# ---------------------------------------------------------------------------
# Command streams (RegisterBank.scala:651)
# ---------------------------------------------------------------------------

COMMAND_STREAMS: tuple[CommandStream, ...] = (
    CommandStream(0x080, "triangleCMD", "TriangleSetup.Input", "Execute TRIANGLE command", 0),
    CommandStream(0x100, "ftriangleCMD", "TriangleSetup.Input", "Execute TRIANGLE command", 1),
    CommandStream(0x120, "nopCMD", "Bits(32)", "Execute NOP command", 2),
    CommandStream(0x124, "fastfillCMD", "NoData", "Execute FASTFILL command", 3),
    CommandStream(0x128, "swapbufferCMD", "NoData", "Execute SWAPBUFFER command", 4),
)


# ---------------------------------------------------------------------------
# Address-decode quirks
# ---------------------------------------------------------------------------

#: ``AddressRemapper.remapTable`` (AddressRemapper.scala:28) — when PCI address
#: bit 21 is set and the low 12 bits land in 0x088..0x0fc, the RGBZASTW float
#: layout is translated to the standard layout before RegisterBank sees it.
#: Listed here as data; the logic belongs to ``frontend/address_remapper.py``.
REMAP_TABLE: Mapping[int, int] = {
    0x088: 0x088, 0x08C: 0x08C, 0x090: 0x090, 0x094: 0x094, 0x098: 0x098, 0x09C: 0x09C,
    0x0A0: 0x0A0, 0x0A4: 0x0C0, 0x0A8: 0x0E0,
    0x0AC: 0x0A4, 0x0B0: 0x0C4, 0x0B4: 0x0E4,
    0x0B8: 0x0A8, 0x0BC: 0x0C8, 0x0C0: 0x0E8,
    0x0C4: 0x0AC, 0x0C8: 0x0CC, 0x0CC: 0x0EC,
    0x0D0: 0x0B0, 0x0D4: 0x0D0, 0x0D8: 0x0F0,
    0x0DC: 0x0B4, 0x0E0: 0x0D4, 0x0E4: 0x0F4,
    0x0E8: 0x0B8, 0x0EC: 0x0D8, 0x0F0: 0x0F8,
    0x0F4: 0x0BC, 0x0F8: 0x0DC, 0x0FC: 0x0FC,
}

#: Prose notes about how a PCI address becomes one of the addresses above.
DECODE_QUIRKS: tuple[str, ...] = (
    "PCI BAR split (CoreCpuFrontdoor.scala:22-27): cpuAddr[23:22] selects "
    "0 = register set (0x000000-0x3fffff), 1 = LFB (0x400000-0x7fffff), "
    "2 and 3 = texture memory (0x800000-0xffffff). Texture writes are pushed "
    "into the same PCI FIFO as register writes, tagged isTexture.",
    "chip-select field: the datasheet divides the 22-bit register address into "
    "wrap[21:14] / chip[13:10] / register[9:2] / byte[1:0] (sst-1.md section 5). "
    "CoreCpuFrontdoor.scala:36 rebuilds the address as "
    "cpuAddr[21:14] ## 4'b0000 ## cpuAddr[9:0], i.e. it *forces the chip field "
    "to zero*. Every chip selector therefore aliases onto the same register, "
    "which is what a single-FBI + single-TREX implementation wants.",
    "texture-register mirrors: because of the zeroed chip field there is only "
    "one copy of the TREX registers (0x300-0x380). On real hardware those are "
    "mirrored per TREX chip and the chip field picks which ones are written; "
    "here a write addressed at TREX #1 or #2 lands on the one TMU register set.",
    "wrap field: bits [20:14] survive into the 22-bit external bus but are "
    "then truncated by AddressRemapper (AddressRemapper.scala:95-99 resize to "
    "12 bits), so all 256 wraps alias. Bit 21 is the exception: it selects the "
    "RGBZASTW remap (see REMAP_TABLE) and is cleared for everything else.",
    "the register file therefore only ever sees 10 significant address bits "
    "(0x000-0x3fc) even though the BMB port is 12 bits wide "
    "(RegisterBank.scala:1258).",
    f"0x{WAS_ENQUEUED_ADDR:03x} (swapbufferCMD) is wired to "
    "PciFifo.io.wasEnqueuedAddr in Core.scala:210; PciFifo raises wasEnqueued "
    "when a non-texture write to it is pushed into the FIFO "
    "(PciFifo.scala:270), and Core feeds that to SwapBuffer and back into "
    "RegisterBank as swapCmdEnqueued.",
    "texBaseAddr relocation: CPU reads of texture memory are offset by "
    "texBaseAddr[18:0] << 3 (CoreCpuFrontdoor.scala:46-48); CPU writes are "
    "not, they carry the raw 23-bit PCI offset.",
    "addresses with no entry in the category map (status, the five statistics "
    "counters, and every hole in the map) default to fifoBypass = True in "
    "PciFifo (PciFifo.scala:456), so they are never queued.",
)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

_BY_ADDR: Mapping[int, Register] = {r.addr: r for r in REGISTERS}
_BY_NAME: Mapping[str, Register] = {r.name: r for r in REGISTERS}
_ALIAS_BY_ADDR: Mapping[int, FloatAlias] = {a.addr: a for a in FLOAT_ALIASES}
_CMD_BY_ADDR: Mapping[int, CommandStream] = {c.addr: c for c in COMMAND_STREAMS}


def registers(include_sim_only: bool = True) -> tuple[Register, ...]:
    """Every register, sorted by address."""
    if include_sim_only:
        return REGISTERS
    return tuple(r for r in REGISTERS if not r.sim_only)


def by_addr(addr: int) -> Register:
    """The register at ``addr``. Raises :class:`KeyError` if there is none."""
    return _BY_ADDR[addr]


def by_name(name: str) -> Register:
    """The register called ``name``."""
    return _BY_NAME[name]


def float_alias_by_addr(addr: int) -> FloatAlias:
    return _ALIAS_BY_ADDR[addr]


def command_by_addr(addr: int) -> CommandStream:
    return _CMD_BY_ADDR[addr]


def categories(include_sim_only: bool = False) -> dict[int, RegisterCategory]:
    """``BmbBusInterface.getCategories`` as a dict.

    Registers declared with the plain ``newRegAt`` contribute nothing, exactly
    as upstream; the float alias addresses do contribute (``fifoNoSync``).
    ``include_sim_only`` defaults to ``False`` because the sim-only debug
    registers use the plain ``newRegAt`` anyway — it is here for symmetry.
    """
    out: dict[int, RegisterCategory] = {}
    for reg in registers(include_sim_only):
        if reg.category is not None:
            out[reg.addr] = reg.category
    for alias in FLOAT_ALIASES:
        out[alias.addr] = alias.category
    return dict(sorted(out.items()))


def float_aliases() -> dict[int, FloatAlias]:
    """``BmbBusInterface.getFloatAliases`` as a dict keyed by float address."""
    return dict(sorted(_ALIAS_BY_ADDR.items()))


def command_addresses() -> tuple[int, ...]:
    """``BmbBusInterface.getCommandStreamReady.keys.toSeq.sorted``.

    The order is the order of ``io.commandReady`` (RegisterBank.scala:722) and
    of ``PciFifo.io.commandReady``.
    """
    return tuple(sorted(_CMD_BY_ADDR))


def decode_addresses(include_sim_only: bool = False) -> tuple[int, ...]:
    """Every address PciFifo's decode switches over: registers + float aliases."""
    return tuple(sorted(categories(include_sim_only)))


def effective_category(addr: int, include_sim_only: bool = False) -> RegisterCategory:
    """What PciFifo actually does with ``addr``, holes included.

    Unregistered addresses fall through to ``fifoBypass = True``
    (PciFifo.scala:456), which is *not* the same as ``bypassFifo`` being
    declared — hence :func:`categories` keeping them out of the map.
    """
    cat = categories(include_sim_only).get(addr)
    return cat if cat is not None else BYPASS_FIFO


def iter_fields() -> Iterator[tuple[Register, Field]]:
    for reg in REGISTERS:
        for f in reg.fields:
            yield reg, f


# ---------------------------------------------------------------------------
# Not table-expressible
# ---------------------------------------------------------------------------
#
# Everything below has to be hand-written in ``frontend/register_bank.py``;
# the table only records that it exists (see ``Register.effects``).
#
#  * status (0x000) read path — RegisterBank.scala:204-232, including
#    ``io.pciFifoFree.sat(1)``.
#  * clutData (0x228) -> 33-entry gammaLut — RegisterBank.scala:90-96, 128-136.
#  * NCC I/Q writes with data[31] set -> io.paletteWrite —
#    RegisterBank.scala:138-158.
#  * the twelve 50-bit float shadow registers and their two write sources
#    (PciFifo floatShadow drain, or a direct fixed write shifted left) —
#    RegisterBank.scala:98-191.
#  * triangleCMD / ftriangleCMD payload assembly (buildTriangleCommandInput,
#    captureTriangleGradients, captureTriangleHiTexCoords, captureTriangleHiAlpha,
#    captureTriangleConfig) — RegisterBank.scala:467-606, 652-673.
#  * the s2mPipe + m2sPipe command-stream buffering and the commandReady vector
#    ordering — BmbBusInterface.scala:170-190, RegisterBank.scala:720-726.
#  * triangleConfigReg and the TexLayoutTables.compute pipeline —
#    RegisterBank.scala:641, 1193-1207, 1235.
#  * trace IDs under config.trace.enabled — RegisterBank.scala:86-88, 690-697.
#  * syncPulse = RegNext(io.syncDrained) and fifoEmpty passthrough —
#    RegisterBank.scala:194-199.
#  * the BMB read/response protocol itself (cmd.ready always True, rsp.valid =
#    RegNext(cmd.fire), the read mux over the slices) —
#    BmbBusInterface.scala:98-151.
