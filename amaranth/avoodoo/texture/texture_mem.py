"""``object TextureMem`` -- the CPU texture-download write path.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/texture/TextureMem.scala``
(SpinalHDL 1.15.0).  The Scala file is an ``object`` of bundles and helper
functions, not a ``Component`` (``GenOne`` says so too), so this module has
no ``wiring.Component`` either: :class:`~avoodoo.core.texture_mem_subsystem
.TextureMemSubsystem` elaborates the pieces in its own ``Module``.

=====================================  =======================================
Scala                                  here
=====================================  =======================================
``DownloadConfig(c)``                  :class:`DownloadConfig`
``DownloadConfig.fromRegisterBank``    :meth:`DownloadConfig.from_register_bank`
``WriteCmd(c)``                        :class:`WriteCmd`
``WriteCmd.PackedCpuWritePrep``        :class:`PackedCpuWritePrep`
``WriteCmd.LinearCpuWritePrep``        :class:`LinearCpuWritePrep`
``WriteCmd.toBmb``                     :func:`write_cmd_to_membus`
``WriteCmd.fromPciDrain``              :func:`write_cmd_from_pci_drain`
=====================================  =======================================

Field names are ``tools.diff.naming.camel_to_snake`` of the Spinal names and
declaration order is Spinal's (``core/core_types.py`` conventions), so the
lockstep harness pairs ``io_downloadConfig_texBaseAddr38`` with the
``download_config__tex_base_addr38`` slice of ``TextureMemSubsystem``'s packed
port without an override.

What ``fromPciDrain`` does (``c.packedTexLayout``, true for ``voodoo1``):

1. Decode the raw 23-bit PCI offset the host wrote at: ``lod = [20:17]``,
   ``t = [16:9]``, and ``s`` from the low bits with the texel width folded
   in (16-bit formats, ``textureMode[31]`` "sequential 8-bit", or the
   default 8-bit packing).  ``tmuValid`` requires ``[22:21] == 0`` -- the
   TMU0 window; TMU1/2 writes are dropped.
2. Look the LOD up in :func:`~avoodoo.texture.tex_layout_tables.compute_tables`
   (``texBase``/``texShift``), register everything (``m2sPipe``).
3. ``throwWhen(!validWrite)``: LOD 9..15 and non-TMU0 writes vanish here,
   *after* the first register stage, so they still cost a cycle each.
4. Form the packed SRAM byte address ``lodBase + s*bpp + (t << rowShift)``
   in 22 bits, register again (``m2sPipe``), and hand a :class:`WriteCmd` on.

Two register stages, both plain ``m2sPipe``: ``rValid`` resets, the payload
registers do not (``reset_less``, see the plan §6 rule and
``lib/stream.py:M2SPipe``).

One Spinal detail the port reproduces on purpose: the 9-entry ``Vec`` is read
with the 4-bit ``lod``, and Spinal's generated ``case`` sends indices 9..15 to
the ``default`` branch, which is entry **8**.  Those writes are thrown away
one stage later, so nothing observable depends on it, but the intermediate
register would differ in a waveform if the Amaranth read gave ``x``/0
instead.
"""

from __future__ import annotations

from amaranth.hdl import Cat, Const, Module, Signal, unsigned
from amaranth.lib import data

from avoodoo.config import Config
from avoodoo.lib.membus import CMD_WRITE, MemBus, MemBusParameter
from avoodoo.lib.stream import m2s_pipe, throw_when, translate_with
from avoodoo.texture.tex_layout_tables import ARGB8332, TexConfigLayout, compute_tables

__all__ = [
    "DownloadConfig",
    "WriteCmd",
    "PackedCpuWritePrep",
    "LinearCpuWritePrep",
    "write_cmd_to_membus",
    "write_cmd_from_pci_drain",
]


# ---------------------------------------------------------------------------
# DownloadConfig
# ---------------------------------------------------------------------------


class DownloadConfig(data.StructLayout):
    """``case class DownloadConfig(c: Config)`` -- the TMU registers the CPU
    write path needs, 24-bit base addresses as the register bank holds them
    (the datasheet defines only ``[18:0]``; ``fromPciDrain`` uses just those).
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "texture_mode": unsigned(32),
            "tex_base_addr": unsigned(24),
            "tex_base_addr1": unsigned(24),
            "tex_base_addr2": unsigned(24),
            "tex_base_addr38": unsigned(24),
            "t_lod": unsigned(32),
        })

    #: ``regBank.tmuConfig.<field>`` -> :class:`DownloadConfig` field, in the
    #: order ``fromRegisterBank`` assigns them.  The register-bank attribute
    #: names are the ``camel_to_snake`` spellings of ``RegisterBank.scala:1142``'s
    #: ``tmuConfig`` area (``textureMode`` -> ``texture_mode``, ``tLOD`` ->
    #: ``t_lod``), which is also what :data:`avoodoo.frontend.regdef.REGISTERS`
    #: names them.
    REGISTER_FIELDS = (
        ("texture_mode", "texture_mode"),
        ("tex_base_addr", "tex_base_addr"),
        ("tex_base_addr1", "tex_base_addr1"),
        ("tex_base_addr2", "tex_base_addr2"),
        ("tex_base_addr38", "tex_base_addr38"),
        ("t_lod", "t_lod"),
    )

    @classmethod
    def from_register_bank(cls, m: Module, c: Config, tmu_config, *,
                           name: str = "downloadConfig"):
        """``DownloadConfig.fromRegisterBank(c, regBank)``.

        ``tmu_config`` is the register bank's ``tmuConfig`` area -- any object
        with ``texture_mode``, ``tex_base_addr``, ``tex_base_addr1``,
        ``tex_base_addr2``, ``tex_base_addr38`` and ``t_lod`` attributes (the
        Phase 2.2 ``RegisterBank`` port exposes exactly those).  Returns a
        fresh ``Signal(DownloadConfig(c))`` driven combinationally.
        """
        cfg = Signal(cls(c), name=name)
        for dst, src in cls.REGISTER_FIELDS:
            m.d.comb += cfg[dst].eq(getattr(tmu_config, src))
        return cfg


# ---------------------------------------------------------------------------
# WriteCmd and its two pipeline payloads
# ---------------------------------------------------------------------------


class WriteCmd(data.StructLayout):
    """``case class WriteCmd(c: Config)`` -- one masked 32-bit SRAM write."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "address": unsigned(c.address_width),
            "data": unsigned(32),
            "mask": unsigned(4),
        })


class PackedCpuWritePrep(data.StructLayout):
    """``private case class PackedCpuWritePrep(c)`` -- first stage payload."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "valid_write": unsigned(1),
            "lod_base": unsigned(22),
            "lod_shift": unsigned(4),
            "s": unsigned(8),
            "t": unsigned(8),
            "is16bit": unsigned(1),
            "data": unsigned(32),
            "mask": unsigned(4),
        })


class LinearCpuWritePrep(data.StructLayout):
    """``private case class LinearCpuWritePrep(c)`` -- the ``!packedTexLayout``
    first-stage payload."""

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "valid_write": unsigned(1),
            "address": unsigned(c.address_width),
            "data": unsigned(32),
            "mask": unsigned(4),
        })


# ---------------------------------------------------------------------------
# WriteCmd.toBmb
# ---------------------------------------------------------------------------


def write_cmd_to_membus(m: Module, cmd_in, bmb_params: MemBusParameter, source: int,
                        *, name: str = "bmb"):
    """``WriteCmd.toBmb(cmdIn, bmbParams, source)``.

    A single-beat masked write per command: ``opcode = WRITE``, ``length = 3``
    (four bytes), ``last = True``, ``source`` fixed, responses accepted
    unconditionally (``rsp.ready := True``).  Returns a master-side
    :func:`~avoodoo.lib.membus.MemBus`.
    """
    a = bmb_params.access
    bmb = MemBus(bmb_params, path=(name,))
    frag = bmb.cmd.payload.fragment
    m.d.comb += [
        bmb.cmd.valid.eq(cmd_in.valid),
        frag.address.eq(cmd_in.payload.address),           # .resize(addressWidth)
        frag.opcode.eq(CMD_WRITE),
        frag.length.eq(Const(3, a.length_width)),
        frag.data.eq(cmd_in.payload.data),
        frag.mask.eq(cmd_in.payload.mask),
        bmb.cmd.payload.last.eq(1),
        cmd_in.ready.eq(bmb.cmd.ready),
        bmb.rsp.ready.eq(1),
    ]
    if a.source_width > 0:
        m.d.comb += frag.source.eq(Const(source, a.source_width))
    elif source != 0:
        raise ValueError(f"source {source} on a bus with sourceWidth 0")
    return bmb


# ---------------------------------------------------------------------------
# WriteCmd.fromPciDrain
# ---------------------------------------------------------------------------


def _vec_read(m, vec, index, width: int, *, name: str):
    """Spinal ``Vec(idx)`` with an index that can exceed the last entry.

    Spinal emits a ``case`` over the valid indices with the *last* entry as
    ``default``; Amaranth's ``Array`` read would be undefined there.
    """
    out = Signal(width, name=name)
    with m.Switch(index):
        for i in range(len(vec) - 1):
            with m.Case(i):
                m.d.comb += out.eq(vec[i])
        with m.Default():
            m.d.comb += out.eq(vec[len(vec) - 1])
    return out


def write_cmd_from_pci_drain(m: Module, c: Config, drain, cfg, *, name: str = "cpuTexWrite"):
    """``WriteCmd.fromPciDrain(c, drain, cfg)`` -> ``Stream[WriteCmd]``.

    ``drain`` is the ``PciFifo.texDrain`` stream
    (:class:`avoodoo.core.core_types.TexWritePayload`), ``cfg`` a
    :class:`DownloadConfig` view.  Two ``m2sPipe`` stages; see the module
    docstring for the decode.
    """
    aw = c.address_width
    pci_addr = drain.payload.pci_addr
    # val tmuValid = pciAddr(22 downto 21) === 0
    tmu_valid = pci_addr[21:23] == 0

    if c.packed_tex_layout:
        tex_cfg = Signal(TexConfigLayout(), name="texCfg")
        m.d.comb += [
            tex_cfg.tex_base_addr.eq(cfg.tex_base_addr[:19]),
            tex_cfg.tex_base_addr1.eq(cfg.tex_base_addr1[:19]),
            tex_cfg.tex_base_addr2.eq(cfg.tex_base_addr2[:19]),
            tex_cfg.tex_base_addr38.eq(cfg.tex_base_addr38[:19]),
            tex_cfg.tformat.eq(cfg.texture_mode[8:12]),
            tex_cfg.t_lod_aspect.eq(cfg.t_lod[21:23]),
            tex_cfg.t_lod_s_is_wider.eq(cfg.t_lod[20]),
            tex_cfg.t_lod_multibase.eq(cfg.t_lod[24]),
        ]
        write_tables = compute_tables(m, tex_cfg, name="writeTables")

        # val is16bit = texCfg.tformat >= Tmu.TextureFormat.ARGB8332
        is16bit = Signal(name="is16bit")
        m.d.comb += is16bit.eq(tex_cfg.tformat >= ARGB8332)
        # val seq8 = cfg.textureMode(31)
        seq8 = cfg.texture_mode[31]
        # val lod = pciAddr(20 downto 17); val t = pciAddr(16 downto 9)
        lod = pci_addr[17:21]
        t = pci_addr[9:17]
        s = Signal(8, name="s")
        # `pciAddr >> 1` is 22 bits, `& 0xfe` pads the literal to that width,
        # `.resize(8 bits)` keeps the low byte.
        with m.If(is16bit):
            m.d.comb += s.eq(((pci_addr >> 1) & 0xFE)[:8])
        with m.Elif(seq8):
            m.d.comb += s.eq((pci_addr & 0xFC)[:8])
        with m.Else():
            m.d.comb += s.eq(((pci_addr >> 1) & 0xFC)[:8])

        # val drainValid = lod <= 8 && tmuValid
        drain_valid = (lod <= 8) & tmu_valid

        prep = Signal(PackedCpuWritePrep(c), name="prep")
        m.d.comb += [
            prep.valid_write.eq(drain_valid),
            prep.lod_base.eq(_vec_read(m, write_tables.tex_base, lod, 22, name="lodBase")),
            prep.lod_shift.eq(_vec_read(m, write_tables.tex_shift, lod, 4, name="lodShift")),
            prep.s.eq(s),
            prep.t.eq(t),
            prep.is16bit.eq(is16bit),
            prep.data.eq(drain.payload.data),
            prep.mask.eq(drain.payload.mask),
        ]
        prepared = m2s_pipe(m, translate_with(m, drain, prep), name=f"{name}Prep")

        # prepared.throwWhen(!prepared.payload.validWrite).translateWith { ... }.m2sPipe()
        p = prepared.payload
        sram_addr = Signal(aw, name="sramAddr")
        # prep.t.resize(22 bits) << shift, .resize(22 bits)
        t22 = Cat(p.t, Const(0, 22 - 8))
        with m.If(p.is16bit):
            # (prep.lodShift +^ U(1)).resize(5 bits)
            shift16 = (p.lod_shift + Const(1, 1))[:5]
            m.d.comb += sram_addr.eq(
                ((p.lod_base + Cat(Const(0, 1), p.s))[:22]
                 + (t22 << shift16)[:22])[:22])
        with m.Else():
            m.d.comb += sram_addr.eq(
                ((p.lod_base + p.s)[:22] + (t22 << p.lod_shift)[:22])[:22])

        cmd = Signal(WriteCmd(c), name="cmd")
        m.d.comb += [
            cmd.address.eq(sram_addr),
            cmd.data.eq(p.data),
            cmd.mask.eq(p.mask),
        ]
        thrown = throw_when(m, prepared, ~p.valid_write)
        return m2s_pipe(m, translate_with(m, thrown, cmd), name=f"{name}Cmd")

    # ---- !packedTexLayout: linear layout, one base register per LOD group --
    lod = pci_addr[17:21]
    base_reg = Signal(24, name="baseReg")
    m.d.comb += base_reg.eq(cfg.tex_base_addr)
    with m.If(cfg.t_lod[24]):
        with m.Switch(lod):
            with m.Case(1):
                m.d.comb += base_reg.eq(cfg.tex_base_addr1)
            with m.Case(2):
                m.d.comb += base_reg.eq(cfg.tex_base_addr2)
            with m.Default():
                with m.If(lod >= 3):
                    m.d.comb += base_reg.eq(cfg.tex_base_addr38)
    # ((baseReg(18 downto 0) << 3) +^ pciAddr).resize(addressWidth)
    flat_sram_addr = (Cat(Const(0, 3), base_reg[:19]) + pci_addr)[:aw]

    prep = Signal(LinearCpuWritePrep(c), name="prep")
    m.d.comb += [
        prep.valid_write.eq(tmu_valid),
        prep.address.eq(flat_sram_addr),
        prep.data.eq(drain.payload.data),
        prep.mask.eq(drain.payload.mask),
    ]
    prepared = m2s_pipe(m, translate_with(m, drain, prep), name=f"{name}Prep")

    p = prepared.payload
    cmd = Signal(WriteCmd(c), name="cmd")
    m.d.comb += [
        cmd.address.eq(p.address),
        cmd.data.eq(p.data),
        cmd.mask.eq(p.mask),
    ]
    thrown = throw_when(m, prepared, ~p.valid_write)
    return m2s_pipe(m, translate_with(m, thrown, cmd), name=f"{name}Cmd")
