"""AXI4 and AXI4-Lite wiring signatures -- the Gowin/Artix boundary buses.

Transliteration of ``spinal/lib/bus/amba4/axi/{Axi4,Axi4Channel,Axi4Shared}.scala``
and ``spinal/lib/bus/amba4/axilite/AxiLite4.scala`` (SpinalHDL **1.15.0**,
read from ``spinalhdl-lib_2.12-1.15.0-sources.jar``), plan §3 decision 4 and
Phase 1.5.

The internal bus is :mod:`avoodoo.lib.membus`; AXI only appears where the
core meets vendor IP -- the PCIe/TLP BAR (AXI4-Lite) and the DDR3 controller
(AXI4) -- so what is ported here is the *bundle*, its field order, and the
few connection helpers the bridges use.  Nothing from ``Axi4Crossbar``,
``Axi4Decoder``, ``Axi4Upsizer``, ``Axi4SlaveFactory`` or the ``sim``
packages is needed by ``fpga/src``.

Port naming.  A Spinal ``slave(AxiLite4(cfg))`` called ``bar`` flattens to
``io_bar_aw_valid``, ``io_bar_aw_ready``, ``io_bar_aw_payload_addr``,
``io_bar_aw_payload_prot``, ... (see ``fpga/rtl/gowin/TangConsole138k.v``,
``module CoreGowin`` / ``AxiLite4ToBmbBridge``).  The Amaranth
:class:`AxiLite4Signature` is the same tree -- ``bar.aw`` is a
``stream.Signature`` whose payload is the ``data.StructLayout`` of
``AxiLite4Ax`` -- so it emits ``bar__aw__valid``, ``bar__aw__ready`` and one
packed ``bar__aw__payload`` with ``addr`` at the LSB and ``prot`` above it.
``tools/diff/amports.py`` walks the layout and pairs each Spinal leaf with
the matching slice, exactly as it does for ``MemBus``; no override is needed.

Bit packing.  A Spinal ``Bundle`` declares its first ``val`` at the LSB of
``asBits``, and ``data.StructLayout`` lays fields out LSB-first in
declaration order, so every layout below lists the fields in the order the
Scala class declares them.  A field whose ``use*`` flag is off (or whose width
is 0 -- ``idWidth = 0`` in ``BmbToAxi4SharedBridge.getAxi4Config``) is
omitted, because Spinal emits no port for it and the differential harness
rejects an Amaranth leaf with no Spinal twin.

Direction.  Every signature is written from the **master's** point of view
(``asMaster``: ``aw``/``w``/``ar`` out, ``b``/``r`` in).  A component that is
a slave declares ``In(Axi4Signature(cfg))``; one that is a master declares
``Out(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from amaranth import Module, Mux, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.stream import _assign, arbitration_from

__all__ = [
    "RESP_OKAY",
    "RESP_EXOKAY",
    "RESP_SLVERR",
    "RESP_DECERR",
    "BURST_FIXED",
    "BURST_INCR",
    "BURST_WRAP",
    "BURST_RESERVED",
    "LOCK_NORMAL",
    "LOCK_EXCLUSIVE",
    "PROT_DEFAULT",
    "CACHE_DEFAULT",
    "BOUNDARY_WIDTH",
    "Axi4Config",
    "AxiLite4Config",
    "axi4_ax_layout",
    "axi4_aw_layout",
    "axi4_ar_layout",
    "axi4_arw_layout",
    "axi4_w_layout",
    "axi4_b_layout",
    "axi4_r_layout",
    "axilite4_ax_layout",
    "axilite4_w_layout",
    "axilite4_b_layout",
    "axilite4_r_layout",
    "Axi4Signature",
    "Axi4SharedSignature",
    "AxiLite4Signature",
    "Axi4",
    "Axi4Shared",
    "AxiLite4",
    "has_field",
    "connect_axi4",
    "connect_axilite4",
    "shared_to_axi4",
]


# --------------------------------------------------------------------------
# constants (object Axi4 / object AxiLite4)
# --------------------------------------------------------------------------

#: ``Axi4.resp`` / ``AxiLite4.resp`` (2 bits).
RESP_OKAY = 0b00
RESP_EXOKAY = 0b01
RESP_SLVERR = 0b10
RESP_DECERR = 0b11

#: ``Axi4.burst`` (2 bits).
BURST_FIXED = 0b00
BURST_INCR = 0b01
BURST_WRAP = 0b10
BURST_RESERVED = 0b11

#: ``Axi4.lock`` (1 bit for AXI4, 2 for AXI3).
LOCK_NORMAL = 0
LOCK_EXCLUSIVE = 1

#: What ``Axi4Priv.driveAx`` and ``BmbToAxi4SharedBridge`` fill in when the
#: sink has ``prot``/``cache`` and the source does not: ``B"010"`` (unprivileged,
#: non-secure, data) and ``B"1111"``.
PROT_DEFAULT = 0b010
CACHE_DEFAULT = 0b1111

#: ``Axi4.boundaryWidth`` -- a burst may not cross a 4 KiB boundary.
BOUNDARY_WIDTH = 12


def _log2_up(value: int) -> int:
    return 0 if value <= 1 else (value - 1).bit_length()


# --------------------------------------------------------------------------
# configs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Axi4Config:
    """``spinal.lib.bus.amba4.axi.Axi4Config``, same defaults.

    The three ``*IssuingCapability`` / ``readDataReorderingDepth`` hints are
    kept for shape only; nothing here reads them.
    """

    address_width: int
    data_width: int
    id_width: int = -1
    use_id: bool = True
    with_axi3: bool = False
    use_region: bool = True
    use_burst: bool = True
    use_lock: bool = True
    use_cache: bool = True
    use_size: bool = True
    use_qos: bool = True
    use_len: bool = True
    use_last: bool = True
    use_resp: bool = True
    use_prot: bool = True
    use_strb: bool = True
    use_all_strb: bool = False
    force_axi4_len: bool = False
    ar_user_width: int = -1
    aw_user_width: int = -1
    r_user_width: int = -1
    w_user_width: int = -1
    b_user_width: int = -1
    read_issuing_capability: int = -1
    write_issuing_capability: int = -1
    combined_issuing_capability: int = -1
    read_data_reordering_depth: int = -1

    def __post_init__(self) -> None:
        if self.use_id and self.id_width < 0:
            raise ValueError("You need to set id_width")
        if self.data_width not in (8, 16, 32, 64, 128, 256, 512, 1024):
            raise ValueError(f"Valid data width: 8..1024 bits, not {self.data_width}")
        if self.combined_issuing_capability < max(self.read_issuing_capability,
                                                  self.write_issuing_capability):
            raise ValueError("Inconsistent combined issuing capability")
        if self.read_data_reordering_depth > self.read_issuing_capability:
            raise ValueError("Inconsistent read data reordering depth")

    # -- derived (Axi4.scala `def`s) --------------------------------------

    @property
    def use_wid(self) -> bool:
        return self.with_axi3

    @property
    def use_ar_user(self) -> bool:
        return self.ar_user_width >= 0

    @property
    def use_aw_user(self) -> bool:
        return self.aw_user_width >= 0

    @property
    def use_r_user(self) -> bool:
        return self.r_user_width >= 0

    @property
    def use_w_user(self) -> bool:
        return self.w_user_width >= 0

    @property
    def use_b_user(self) -> bool:
        return self.b_user_width >= 0

    @property
    def arw_user_width(self) -> int:
        return max(self.ar_user_width, self.aw_user_width)

    @property
    def use_arw_user(self) -> bool:
        return self.arw_user_width >= 0

    @property
    def size_width(self) -> int:
        return 4 if self.with_axi3 else 3

    @property
    def len_width(self) -> int:
        return 4 if (self.with_axi3 and not self.force_axi4_len) else 8

    @property
    def lock_width(self) -> int:
        return 2 if self.with_axi3 else 1

    @property
    def byte_per_word(self) -> int:
        return self.data_width // 8

    @property
    def symbol_width(self) -> int:
        """``log2Up(bytePerWord)`` -- the bits below the word address."""
        return _log2_up(self.byte_per_word)

    def to_full_config(self, default_id_width: int = 1) -> "Axi4Config":
        """``toFullConfig`` -- every optional channel field switched on."""
        from dataclasses import replace
        return replace(
            self,
            id_width=self.id_width if self.use_id else default_id_width,
            use_id=True, use_region=True, use_burst=True, use_lock=True,
            use_cache=True, use_size=True, use_qos=True, use_len=True,
            use_last=True, use_resp=True, use_prot=True, use_strb=True,
        )


@dataclass(frozen=True)
class AxiLite4Config:
    """``spinal.lib.bus.amba4.axilite.AxiLite4Config``."""

    address_width: int
    data_width: int
    read_issuing_capability: int = -1
    write_issuing_capability: int = -1
    combined_issuing_capability: int = -1
    read_data_reordering_depth: int = -1

    def __post_init__(self) -> None:
        if self.data_width not in (32, 64):
            raise ValueError("Data width must be 32 or 64")

    @property
    def byte_per_word(self) -> int:
        return self.data_width // 8


# --------------------------------------------------------------------------
# payload layouts (Axi4Channel.scala / AxiLite4.scala) -- declaration order
# --------------------------------------------------------------------------


def _opt(fields: dict, name: str, width: int, present: bool = True) -> None:
    if present and width > 0:
        fields[name] = unsigned(width)


def axi4_ax_layout(c: Axi4Config, user_width: int, read_only: bool) -> data.StructLayout:
    """``class Axi4Ax(config, userWidth, readOnly)``."""
    f: dict = {"addr": unsigned(c.address_width)}
    _opt(f, "id", c.id_width, c.use_id)
    _opt(f, "region", 4, c.use_region)
    _opt(f, "len", c.len_width, c.use_len)
    _opt(f, "size", c.size_width, c.use_size)
    _opt(f, "burst", 2, c.use_burst)
    _opt(f, "lock", c.lock_width, c.use_lock)
    _opt(f, "cache", 4, c.use_cache)
    _opt(f, "qos", 4, c.use_qos)
    _opt(f, "user", user_width, user_width >= 0)
    _opt(f, "prot", 3, c.use_prot)
    _opt(f, "all_strb", 1, c.use_all_strb and not read_only)
    return data.StructLayout(f)


def axi4_aw_layout(c: Axi4Config) -> data.StructLayout:
    """``Axi4Aw(config)`` = ``Axi4Ax(config, awUserWidth, readOnly = false)``."""
    return axi4_ax_layout(c, c.aw_user_width, read_only=False)


def axi4_ar_layout(c: Axi4Config) -> data.StructLayout:
    """``Axi4Ar(config)`` = ``Axi4Ax(config, arUserWidth, readOnly = true)``."""
    return axi4_ax_layout(c, c.ar_user_width, read_only=True)


def axi4_arw_layout(c: Axi4Config) -> data.StructLayout:
    """``Axi4Arw(config)`` -- ``Axi4Ax(arwUserWidth, readOnly = false)`` + ``write``."""
    f = {k: fld.shape for k, fld in axi4_ax_layout(c, c.arw_user_width, read_only=False)}
    f["write"] = unsigned(1)
    return data.StructLayout(f)


def axi4_w_layout(c: Axi4Config) -> data.StructLayout:
    """``Axi4W(config)``: data, strb, user, last, id."""
    f: dict = {"data": unsigned(c.data_width)}
    _opt(f, "strb", c.byte_per_word, c.use_strb)
    _opt(f, "user", c.w_user_width, c.use_w_user)
    _opt(f, "last", 1, c.use_last)
    _opt(f, "id", c.id_width, c.use_wid)
    return data.StructLayout(f)


def axi4_b_layout(c: Axi4Config) -> data.StructLayout:
    """``Axi4B(config)``: id, resp, user."""
    f: dict = {}
    _opt(f, "id", c.id_width, c.use_id)
    _opt(f, "resp", 2, c.use_resp)
    _opt(f, "user", c.b_user_width, c.use_b_user)
    return data.StructLayout(f)


def axi4_r_layout(c: Axi4Config) -> data.StructLayout:
    """``Axi4R(config)``: data, id, resp, last, user."""
    f: dict = {"data": unsigned(c.data_width)}
    _opt(f, "id", c.id_width, c.use_id)
    _opt(f, "resp", 2, c.use_resp)
    _opt(f, "last", 1, c.use_last)
    _opt(f, "user", c.r_user_width, c.use_r_user)
    return data.StructLayout(f)


def axilite4_ax_layout(c: AxiLite4Config) -> data.StructLayout:
    """``AxiLite4Ax(config)``: addr, prot."""
    return data.StructLayout({"addr": unsigned(c.address_width), "prot": unsigned(3)})


def axilite4_w_layout(c: AxiLite4Config) -> data.StructLayout:
    """``AxiLite4W(config)``: data, strb."""
    return data.StructLayout({"data": unsigned(c.data_width),
                              "strb": unsigned(c.byte_per_word)})


def axilite4_b_layout(c: AxiLite4Config) -> data.StructLayout:
    """``AxiLite4B(config)``: resp."""
    return data.StructLayout({"resp": unsigned(2)})


def axilite4_r_layout(c: AxiLite4Config) -> data.StructLayout:
    """``AxiLite4R(config)``: data, resp."""
    return data.StructLayout({"data": unsigned(c.data_width), "resp": unsigned(2)})


def has_field(view, name: str) -> bool:
    """Whether an optional channel field survived the config (and has width)."""
    return name in view.shape().members


# --------------------------------------------------------------------------
# signatures (master view)
# --------------------------------------------------------------------------


class _ConfiguredSignature(wiring.Signature):
    def __init__(self, config, members: dict):
        self._config = config
        super().__init__(members)

    @property
    def config(self):
        return self._config

    def __eq__(self, other):
        return type(other) is type(self) and other._config == self._config

    def __hash__(self):
        return hash((type(self), self._config))

    def __repr__(self):
        return f"{type(self).__name__}({self._config!r})"


class Axi4Signature(_ConfiguredSignature):
    """``Axi4(config)`` seen from the master: ``aw``/``w``/``ar`` out, ``b``/``r`` in."""

    def __init__(self, config: Axi4Config):
        super().__init__(config, {
            "aw": Out(stream.Signature(axi4_aw_layout(config))),
            "w": Out(stream.Signature(axi4_w_layout(config))),
            "b": In(stream.Signature(axi4_b_layout(config))),
            "ar": Out(stream.Signature(axi4_ar_layout(config))),
            "r": In(stream.Signature(axi4_r_layout(config))),
        })


class Axi4SharedSignature(_ConfiguredSignature):
    """``Axi4Shared(config)``: one ``arw`` command channel with a ``write`` flag."""

    def __init__(self, config: Axi4Config):
        super().__init__(config, {
            "arw": Out(stream.Signature(axi4_arw_layout(config))),
            "w": Out(stream.Signature(axi4_w_layout(config))),
            "b": In(stream.Signature(axi4_b_layout(config))),
            "r": In(stream.Signature(axi4_r_layout(config))),
        })


class AxiLite4Signature(_ConfiguredSignature):
    """``AxiLite4(config)`` seen from the master."""

    def __init__(self, config: AxiLite4Config):
        super().__init__(config, {
            "aw": Out(stream.Signature(axilite4_ax_layout(config))),
            "w": Out(stream.Signature(axilite4_w_layout(config))),
            "b": In(stream.Signature(axilite4_b_layout(config))),
            "ar": Out(stream.Signature(axilite4_ax_layout(config))),
            "r": In(stream.Signature(axilite4_r_layout(config))),
        })


def Axi4(config: Axi4Config, *, path=None):  # noqa: N802 - mirrors `Axi4(cfg)`
    """A free-standing master-side AXI4 bundle, for internal wiring."""
    return Axi4Signature(config).create(path=path, src_loc_at=1)


def Axi4Shared(config: Axi4Config, *, path=None):  # noqa: N802
    return Axi4SharedSignature(config).create(path=path, src_loc_at=1)


def AxiLite4(config: AxiLite4Config, *, path=None):  # noqa: N802
    return AxiLite4Signature(config).create(path=path, src_loc_at=1)


# --------------------------------------------------------------------------
# `<<` / `>>` -- Axi4Priv.driveWeak / driveAx and the AxiLite4 connect
# --------------------------------------------------------------------------


def _drive_weak(m: Module, dst, src, name: str, *, default, allow_resize: bool,
                allow_drop: bool, what: str) -> None:
    """``Axi4Priv.driveWeak(source, sink, by, to, defaultValue, allowResize, allowDrop)``.

    ``default`` is the value to drive when the sink has the field and the
    source does not (``None`` = Spinal's ``LocatedPendingError``).
    """
    in_dst, in_src = has_field(dst, name), has_field(src, name)
    if not in_dst and not in_src:
        return
    if in_dst and not in_src:
        if default is None:
            raise ValueError(f"{what}: source has no {name!r} to drive the sink's")
        m.d.comb += dst[name].eq(default)
        return
    if in_src and not in_dst:
        if not allow_drop:
            raise ValueError(f"{what}: sink has no {name!r} to take the source's")
        return
    dw, sw = len(dst[name]), len(src[name])
    if dw != sw and not allow_resize:
        raise ValueError(f"{what}: {name!r} may not resize {sw} -> {dw}")
    m.d.comb += dst[name].eq(src[name])


def _drive_ax(m: Module, sink, source, sink_cfg: Axi4Config, source_cfg: Axi4Config,
              *, what: str) -> None:
    """``Axi4Priv.driveAx(stream, sink)`` for ``aw``/``ar`` (and ``arw`` minus ``write``)."""
    if source_cfg.id_width > sink_cfg.id_width:
        raise ValueError(f"{what}: expect source idWidth={source_cfg.id_width} <= "
                         f"sink idWidth={sink_cfg.id_width}")
    if source_cfg.address_width < sink_cfg.address_width:
        raise ValueError(f"{what}: expect source addressWidth={source_cfg.address_width} "
                         f">= sink addressWidth={sink_cfg.address_width}")
    arbitration_from(m, sink, source)
    sp, dp = source.payload, sink.payload
    m.d.comb += dp.addr.eq(sp.addr)                              # `.resized`
    _drive_weak(m, dp, sp, "id", default=0, allow_resize=True, allow_drop=False, what=what)
    _drive_weak(m, dp, sp, "region", default=0, allow_resize=False, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "len", default=0, allow_resize=False, allow_drop=False, what=what)
    _drive_weak(m, dp, sp, "size", default=sink_cfg.symbol_width, allow_resize=False,
                allow_drop=False, what=what)
    _drive_weak(m, dp, sp, "burst", default=BURST_INCR, allow_resize=False,
                allow_drop=False, what=what)
    _drive_weak(m, dp, sp, "lock", default=LOCK_NORMAL, allow_resize=False,
                allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "cache", default=0, allow_resize=False, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "qos", default=0, allow_resize=False, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "user", default=0, allow_resize=True, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "prot", default=PROT_DEFAULT, allow_resize=False,
                allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "all_strb", default=0, allow_resize=False,
                allow_drop=True, what=what)


def _drive_w(m: Module, sink, source, sink_cfg: Axi4Config, *, what: str) -> None:
    """``Axi4W.StreamPimper.drive``."""
    arbitration_from(m, sink, source)
    sp, dp = source.payload, sink.payload
    m.d.comb += dp.data.eq(sp.data)
    _drive_weak(m, dp, sp, "strb", default=(1 << sink_cfg.byte_per_word) - 1,
                allow_resize=False, allow_drop=False, what=what)
    _drive_weak(m, dp, sp, "user", default=0, allow_resize=False, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "last", default=None, allow_resize=False, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "id", default=None, allow_resize=True, allow_drop=True, what=what)


def _drive_b(m: Module, sink, source, sink_cfg: Axi4Config, source_cfg: Axi4Config,
             *, what: str) -> None:
    """``Axi4B.StreamPimper.drive``."""
    if source_cfg.id_width < sink_cfg.id_width:
        raise ValueError(f"{what}: expect source idWidth={source_cfg.id_width} >= "
                         f"sink idWidth={sink_cfg.id_width}")
    arbitration_from(m, sink, source)
    sp, dp = source.payload, sink.payload
    _drive_weak(m, dp, sp, "id", default=None, allow_resize=True, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "resp", default=RESP_OKAY, allow_resize=False,
                allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "user", default=0, allow_resize=False, allow_drop=True, what=what)


def _drive_r(m: Module, sink, source, sink_cfg: Axi4Config, source_cfg: Axi4Config,
             *, what: str) -> None:
    """``Axi4R.StreamPimper.drive``."""
    if source_cfg.id_width < sink_cfg.id_width:
        raise ValueError(f"{what}: expect source idWidth={source_cfg.id_width} >= "
                         f"sink idWidth={sink_cfg.id_width}")
    arbitration_from(m, sink, source)
    sp, dp = source.payload, sink.payload
    m.d.comb += dp.data.eq(sp.data)
    _drive_weak(m, dp, sp, "last", default=None, allow_resize=False, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "id", default=None, allow_resize=True, allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "resp", default=RESP_OKAY, allow_resize=False,
                allow_drop=True, what=what)
    _drive_weak(m, dp, sp, "user", default=0, allow_resize=False, allow_drop=True, what=what)


def _cfg_of(iface) -> Axi4Config:
    cfg = getattr(iface.signature, "config", None)
    if cfg is None:
        raise TypeError(f"{iface!r} does not carry an AXI config")
    return cfg


def connect_axi4(m: Module, sink, source) -> None:
    """``sink << source`` / ``source >> sink`` for two ``Axi4`` interfaces.

    ``source`` is the master-facing side (its ``aw``/``w``/``ar`` are consumed),
    ``sink`` the slave-facing one.  Optional fields are added, dropped or
    defaulted per ``Axi4Priv.driveWeak``'s table; width changes are refused
    where the Scala refuses them.
    """
    scfg, kcfg = _cfg_of(source), _cfg_of(sink)
    _drive_ax(m, sink.ar, source.ar, kcfg, scfg, what="ar")
    _drive_ax(m, sink.aw, source.aw, kcfg, scfg, what="aw")
    _drive_w(m, sink.w, source.w, kcfg, what="w")
    _drive_r(m, source.r, sink.r, scfg, kcfg, what="r")
    _drive_b(m, source.b, sink.b, scfg, kcfg, what="b")


def connect_axilite4(m: Module, sink, source) -> None:
    """``sink << source`` for two ``AxiLite4`` interfaces of the same config."""
    if _cfg_of(sink) != _cfg_of(source):
        raise ValueError("AxiLite4 << requires identical configs")
    for ch in ("aw", "w", "ar"):
        arbitration_from(m, getattr(sink, ch), getattr(source, ch))
        m.d.comb += _assign(getattr(sink, ch).payload, getattr(source, ch).payload)
    for ch in ("b", "r"):
        arbitration_from(m, getattr(source, ch), getattr(sink, ch))
        m.d.comb += _assign(getattr(source, ch).payload, getattr(sink, ch).payload)


def shared_to_axi4(m: Module, shared, *, name: str = "toAxi4"):
    """``Axi4Shared.toAxi4()``: split ``arw`` on its ``write`` flag.

    Returns a fresh master-side :func:`Axi4` interface::

        ret.ar.payload.assignSomeByName(arw.payload)
        ret.aw.payload.assignSomeByName(arw.payload)
        ret.ar.valid := arw.valid && !arw.write
        ret.aw.valid := arw.valid &&  arw.write
        arw.ready    := arw.write ? aw.ready | ar.ready
        w >> ret.w ; r << ret.r ; b << ret.b
    """
    cfg = _cfg_of(shared)
    ret = Axi4(cfg, path=(name,))
    arw = shared.arw.payload
    for ch in (ret.ar, ret.aw):
        for field in ch.payload.shape().members:
            if has_field(arw, field):
                m.d.comb += ch.payload[field].eq(arw[field])
    m.d.comb += [
        ret.ar.valid.eq(shared.arw.valid & ~arw.write),
        ret.aw.valid.eq(shared.arw.valid & arw.write),
        shared.arw.ready.eq(Mux(arw.write, ret.aw.ready, ret.ar.ready)),
    ]
    _drive_w(m, ret.w, shared.w, cfg, what="w")
    _drive_r(m, shared.r, ret.r, cfg, cfg, what="r")
    _drive_b(m, shared.b, ret.b, cfg, cfg, what="b")
    return ret
