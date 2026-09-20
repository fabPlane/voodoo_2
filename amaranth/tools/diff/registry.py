"""Which Amaranth module diffs against which Spinal module.

One row per diffable module (plan §4.2).  The Spinal half is a key in
``voodoo.diff.GenOne``'s registry (``fpga/src/voodoo/diff/GenOne.scala``); the
Amaranth half is an ``import.path:Attr`` spec plus the constructor arguments
that reproduce the *same* parameters -- for the core proper that always means
``Config.voodoo1()``, which the factory reads for itself.

``overrides`` is the per-module escape hatch required by plan §4.2 step 4: a
Spinal port name whose Amaranth twin the mechanical rule in
:mod:`tools.diff.naming` cannot derive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from avoodoo.config import Config
from avoodoo.core import membus_params as _mp
from avoodoo.lib.membus import Aligner as _Aligner, MemBusParameter as _MemBusParameter

__all__ = ["DiffEntry", "REGISTRY", "resolve", "names"]

# Parameters for the Phase 1.2 MemBus rows.  They are values, not factories,
# because the adapters are library components that take a MemBusParameter --
# unlike the core modules, which read Config.voodoo1() themselves.
_C = Config.voodoo1()
_FB = _mp.fb_mem_bmb_params(_C)
_ALIGNER_OP = _MemBusParameter(
    _Aligner.output_access_parameter(_FB.access, _mp.AXI_BURST_LOG2))


@dataclass(frozen=True)
class DiffEntry:
    spinal: str
    """``GenOne`` registry key; the Verilog lands in ``<Name>_spinal.v``."""

    factory: str | None
    """``package.module:Attr`` producing the Amaranth Elaboratable."""

    note: str

    params: dict = field(default_factory=dict)
    """Constructor keyword arguments for ``factory``."""

    portmap: str | None = None
    """Committed port-map stem under ``fpga/src/voodoo/diff/portmaps/``.

    Defaults to ``spinal``.  ``GenOne`` aliases two names onto one component
    (``Fpxx`` / ``FpxxDiffWrap``), and only one spelling got committed, so the
    alias rows point at the committed file.
    """

    overrides: dict[str, str] = field(default_factory=dict)
    """Spinal port name -> Amaranth port name, for the non-derivable cases."""

    pending: str | None = None
    """Non-``None`` means the Amaranth side does not exist yet; the string says
    who owns it.  ``harness.py`` refuses to run such a row with this message."""

    @property
    def portmap_stem(self) -> str:
        return self.portmap or self.spinal


def _address_remapper_params() -> dict:
    """``AddressRemapper``'s two ``BmbParameter``s, as ``Core.scala:100`` builds them.

    The other rows' factories read ``Config.voodoo1()`` for themselves, but
    ``AddressRemapper`` is a faithful transliteration of a Scala ``case class``
    with two required parameters and no defaults, so the caller supplies them
    -- here, exactly as ``Core`` does.
    """
    from avoodoo.frontend.address_remapper import bmb_params, external_bmb_params

    return {"input_params": external_bmb_params(_C), "output_params": bmb_params(_C)}


REGISTRY: dict[str, DiffEntry] = {
    # --- Phase 1.3 ------------------------------------------------------
    "FpxxDiffWrap": DiffEntry(
        spinal="FpxxDiffWrap",
        factory="tools.diff.wrappers:FpxxDiffWrap",
        note="Fpxx predicate/helper set, FpxxConfig.float32() as in PciFifo "
             "(ext/SpinalVoodoo/src/voodoo/math/Fpxx.scala -> avoodoo/lib/fpxx.py)",
        # No overrides: every port is a flat io_ leaf, so camelCase -> snake_case
        # is exact (io_aFullMant -> a_full_mant, io_constNan -> const_nan).
    ),
    "Fpxx": DiffEntry(
        spinal="Fpxx",
        factory="tools.diff.wrappers:FpxxDiffWrap",
        note="alias of FpxxDiffWrap (GenOne registers both spellings)",
        portmap="FpxxDiffWrap",
    ),
    "Fpxx2AFix": DiffEntry(
        spinal="Fpxx2AFix",
        factory="avoodoo.lib.fpxx2afix:Fpxx2AFix",
        note="PciFifo.scala:200 — Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1) "
             "(ext/SpinalVoodoo/src/voodoo/math/Fpxx2AFix.scala)",
        params={"int_nr_bits": 20, "frac_nr_bits": 30, "pipe_stages": 1},
        # io_op_payload_{mant,exp,sign} pair against slices of the single packed
        # Amaranth port `op__payload` (FpxxLayout); tools/diff/amports.py does
        # that without an override.
    ),
    # --- Phase 1.1 (agent p1-stream owns avoodoo/bus/stream_while.py) ----
    "StreamWhileDiffWrap": DiffEntry(
        spinal="StreamWhileDiffWrap",
        factory="avoodoo.bus.stream_while:StreamWhileDiffWrap",
        note="decrement-to-zero loop, width=8 maxIterations=16 "
             "(ext/SpinalVoodoo/src/voodoo/bus/StreamWhile.scala)",
        portmap="StreamWhile",  # the committed map was generated from the alias
    ),
    "StreamWhile": DiffEntry(
        spinal="StreamWhile",
        factory="avoodoo.bus.stream_while:StreamWhileDiffWrap",
        note="alias of StreamWhileDiffWrap",
        portmap="StreamWhile",
    ),
    # --- Phase 2.3 ------------------------------------------------------
    "SwapBuffer": DiffEntry(
        spinal="SwapBuffer",
        factory="avoodoo.frontend.swap_buffer:SwapBuffer",
        note="Core.scala:119 — SwapBuffer() (formalStrong defaults to false) "
             "(ext/SpinalVoodoo/src/voodoo/frontend/SwapBuffer.scala)",
        # `dbg_load_port` is deliberately left at its default: it adds two
        # ports the Scala does not have, and only `core_sim.py` sets it.
        # io_cmd carries `NoData`, so Spinal emits only io_cmd_valid/ready;
        # `swap_buffer.NoDataStreamSignature` matches that exactly rather than
        # carrying a zero-width payload the harness would find unpairable.
    ),
    "AddressRemapper": DiffEntry(
        spinal="AddressRemapper",
        factory="avoodoo.frontend.address_remapper:AddressRemapper",
        note="Core.scala:100 — AddressRemapper(RegisterBank.externalBmbParams(c), "
             "RegisterBank.bmbParams(c)) "
             "(ext/SpinalVoodoo/src/voodoo/frontend/AddressRemapper.scala)",
        params=_address_remapper_params(),
        # Every Spinal leaf
        # (io_input_cmd_payload_fragment_address, …) pairs against a bit slice
        # of the packed Amaranth `input__cmd__payload`; amports.py does that.
    ),
    # --- Phase 3 (agent p3-rasterizer owns avoodoo/raster/rasterizer.py) -
    "SpanRasterizer": DiffEntry(
        spinal="SpanRasterizer",
        factory="avoodoo.raster.rasterizer:SpanRasterizer",
        note="Rasterizer.scala:129 — Rasterizer.SpanRasterizer(Config.voodoo1()); the "
             "per-pixel StreamWhile loop and every AFix fixTo in the raster front end. "
             "Diffable before SpanWalker lands, which `Rasterizer` needs.",
        # No overrides: all 259 Rasterizer leaves (and this module's subset of
        # them) pair mechanically; tests/test_rasterizer.py asserts that.
    ),
    "Rasterizer": DiffEntry(
        spinal="Rasterizer",
        factory="avoodoo.raster.rasterizer:Rasterizer",
        note="PixelPipeline.scala:222 — Rasterizer(Config.voodoo1()), formalStrong = false "
             "(ext/SpinalVoodoo/src/voodoo/raster/Rasterizer.scala)",
    ),
    # --- Phase 3 / Phase 4 (agent p4-fogwrite) ---------------------------
    "Fog": DiffEntry(
        spinal="Fog",
        factory="avoodoo.pixel.fog:Fog",
        note="PixelPipeline.scala:412 — Fog(c) "
             "(ext/SpinalVoodoo/src/voodoo/pixel/Fog.scala -> avoodoo/pixel/fog.py)",
        # No overrides: 163 ports, every one derivable. `io_fogTable_<N>` pairs
        # against the `data.ArrayLayout(unsigned(16), 64)` port `fog_table`,
        # which amports.py expands to `fog_table__0` .. `fog_table__63`, and
        # `io_input_payload_depth` against the AFix leaf `input__payload__depth`.
    ),
    "Write": DiffEntry(
        spinal="Write",
        factory="avoodoo.pixel.write:Write",
        note="PixelPipeline.scala:227 — Write(c), writeColor / writeAux are the "
             "same module (ext/SpinalVoodoo/src/voodoo/pixel/Write.scala -> "
             "avoodoo/pixel/write.py)",
        # Purely combinational: no clk/reset ports on either side, so
        # --valid-density/--ready-density drive it and every input is
        # re-randomised each cycle. The Scala io bundles are `i`/`o`, not `io`,
        # so the paths keep that first segment: i__from_pipeline__payload__*.
    ),
    # --- Phase 4 (agent p4-dither owns avoodoo/pixel/dither.py) ----------
    "Dither": DiffEntry(
        spinal="Dither",
        factory="avoodoo.pixel.dither:Dither",
        note="Write.scala — Dither(), no parameters; three 5120-entry ROMs from "
             "avoodoo/pixel/dither_tables.py "
             "(ext/SpinalVoodoo/src/voodoo/pixel/Dither.scala -> avoodoo/pixel/dither.py)",
        # No overrides: the two streams are the only ports and every Spinal
        # leaf pairs with a field of the packed Amaranth payload —
        # io_input_payload_use2x2 -> input__payload__use2x2 (no case boundary
        # around the digits), io_output_payload_ditR -> output__payload__dit_r.
        # Note the ROM read-data registers and the four `linked` registers have
        # no reset on either side (plain Spinal `Reg`), so the outputs are only
        # equal from reset if the enables match exactly; they do, and the
        # harness resetting before every seed is what proves it.
    ),
    # --- Phase 3 ---------------------------------------------------------
    "SpanWalker": DiffEntry(
        spinal="SpanWalker",
        factory="avoodoo.raster.span_walker:SpanWalker",
        note="SpanWalker(c, formalStrong = false), Config.voodoo1() "
             "(ext/SpinalVoodoo/src/voodoo/raster/SpanWalker.scala)",
        # No overrides: all 229 Spinal leaves under i_payload_* / o_payload_*
        # map onto slices of the packed Amaranth `i__payload` / `o__payload`
        # by the mechanical camelCase -> snake_case rule, widths included
        # (checked against fpga/rtl/diff/SpanWalker_spinal.v).
    ),
    # --- Phase 5 ---------------------------------------------------------
    "TmuTexelDecoder": DiffEntry(
        spinal="TmuTexelDecoder",
        factory="avoodoo.texture.tmu_texel_decoder:TmuTexelDecoder",
        note="texel format decode + bilinear blend, Config.voodoo1() "
             "(ext/SpinalVoodoo/src/voodoo/texture/TmuTexelDecoder.scala)",
        # The generic driver covers this module's two odd inputs on its own:
        # `paletteWrite` is a Flow (valid, no ready), so it is randomised every
        # cycle and the 256-entry RAMs are fully written within a few thousand
        # cycles, interleaved with the P8/AP88 lookups; `nccTables` is 688 bits
        # of free input, re-randomised every cycle, which is a strictly harder
        # stimulus than the register-written tables the real core supplies.
    ),
    # --- Phase 3 ---------------------------------------------------------
    "TriangleSetup": DiffEntry(
        spinal="TriangleSetup",
        factory="avoodoo.raster.triangle_setup:TriangleSetup",
        note="PixelPipeline.scala:221 — TriangleSetup(c), Config.voodoo1() "
             "(ext/SpinalVoodoo/src/voodoo/raster/TriangleSetup.scala)",
        # No overrides. Both payloads are one packed Amaranth port each
        # (`i__payload` 2129 bits, `o__payload` 2504), and amports.py walks
        # the data.Layout to pair every Spinal leaf with a slice. The only
        # names worth double-checking are the camelCase-heavy config leaves
        # (`tmudSdX` -> `tmud_sd_x`, `tLOD`/`tmuTLOD` -> `t_lod`/`tmu_tlod`,
        # `clipLowY` -> `clip_low_y`), and they come out of
        # tools/diff/naming.camel_to_snake unchanged.
    ),
    # --- Phase 0.2 -------------------------------------------------------
    "AfixOps": DiffEntry(
        spinal="AfixOps",
        factory="avoodoo.lib.afix_ops:AfixOps",
        note="AFix op-by-op reference, 282 ports, Config.voodoo1() formats "
             "(fpga/src/voodoo/diff/AfixOps.scala -> avoodoo/lib/fixed.py)",
    ),
    # --- Phase 1.2 / 1.4: MemBus (Bmb clone) -----------------------------
    # These take their BmbParameter as a constructor argument rather than
    # reading Config themselves (they are library components, not core
    # modules), so the parameters are built here from
    # avoodoo/core/membus_params.py -- the same functions the Scala call
    # sites use, and the same ones tests/test_membus.py checks.
    # No `overrides` anywhere: every Spinal leaf path (input/output/inputs_N,
    # cmd/rsp, payload, fragment, field) snake_cases straight onto the
    # Amaranth member path, and `io_outputBurstLast` -> `output_burst_last`.
    "BmbArbiter": DiffEntry(
        spinal="BmbArbiter",
        factory="avoodoo.lib.membus:Arbiter",
        params=dict(inputs_parameter=[_FB] * 5, output_parameter=_FB,
                    lower_first_priority=True),
        note="CoreSim.scala:37 / CoreGowin.scala:39 / CoreArtix.scala:259 — five "
             "framebuffer masters, lowerFirstPriority (spinal/lib/bus/bmb/"
             "BmbArbiter.scala -> avoodoo/lib/membus.py:Arbiter)",
    ),
    "BmbArbiterSingle": DiffEntry(
        spinal="BmbArbiterSingle",
        factory="avoodoo.lib.membus:Arbiter",
        params=dict(inputs_parameter=[_mp.plane_buffer_params(_C)],
                    output_parameter=_FB, lower_first_priority=True),
        note="FramebufferMemSubsystem.scala:45 — the portCount == 1 bypass, which "
             "is a pure weakAssignFrom resize (sourceWidth 1 -> 4 on cmd, 4 -> 1 "
             "on rsp) and has no arbiter at all",
    ),
    "BmbSourceRemover": DiffEntry(
        spinal="BmbSourceRemover",
        factory="avoodoo.lib.membus:SourceRemover",
        params=dict(p=_FB),
        note="De10MemBackend.scala:480 — source folded into the low bits of a new "
             "4-bit context (spinal/lib/bus/bmb/BmbSourceRemover.scala)",
    ),
    "BmbAligner": DiffEntry(
        spinal="BmbAligner",
        factory="avoodoo.lib.membus:Aligner",
        params=dict(ip=_FB, alignment_width=_mp.AXI_BURST_LOG2),
        note="CoreArtix.scala:226 / CoreGowin.scala:44 — first half of "
             "BmbToAxi4Mem(fbMemBmbParams, 8): 256-byte alignment, context grows "
             "to 21 bits (spinal/lib/bus/bmb/BmbSpecificBridges.scala)",
    ),
    "BmbLengthFixer": DiffEntry(
        spinal="BmbLengthFixer",
        factory="avoodoo.lib.membus:LengthFixer",
        params=dict(ip=_ALIGNER_OP, fixed_width=_mp.AXI_BURST_LOG2),
        note="CoreArtix.scala:227 — second half of BmbToAxi4Mem: 16 fixed 256-byte "
             "sub-bursts, context 21 -> 23 bits "
             "(spinal/lib/bus/bmb/BmbSpecificBridges.scala)",
    ),
    "BmbAlignedSpliter": DiffEntry(
        spinal="BmbAlignedSpliter",
        factory="avoodoo.lib.membus:AlignedSplitter",
        params=dict(ip=_mp.plane_reader_internal_params(_C),
                    length_max=1 << _C.mem_burst_length_width),
        note="FramebufferPlaneReader.scala:98 (Scala spells it 'Spliter'). "
             "needBurstSplitter(Config.voodoo1()) is FALSE, so the shipping core "
             "holds no instance — these are the parameters it would get",
    ),
    "BmbContextRemover": DiffEntry(
        spinal="BmbContextRemover",
        factory="avoodoo.lib.membus:ContextRemover",
        params=dict(p=_mp.plane_reader_splitter_params(_C), pending_max=64),
        note="FramebufferPlaneReader.scala:100 — pendingMax = issueQueueDepth = 64. "
             "Same needBurstSplitter caveat as BmbAlignedSpliter "
             "(spinal/lib/bus/bmb/BmbContextRemover.scala)",
    ),
    "SimBmbMemorySmall": DiffEntry(
        spinal="SimBmbMemorySmall",
        factory="avoodoo.sim_membus_memory:SimMemBusMemory",
        params=dict(p=_FB, size=64 * 1024),
        note="CoreSim.scala:32's SimBmbMemory at a diffable 64 KiB instead of 4 MiB "
             "(ext/SpinalVoodoo/src/voodoo/SimBmbMemory.scala). Two deliberate "
             "differences, neither observable at the ports: the array is 64 KiB, "
             "and Spinal splits a byte-enabled Mem into ram_symbol0..3 while "
             "Amaranth/yosys keep one 32-bit `mem`. Both start all-zero.",
    ),
    "SimBmbMemory": DiffEntry(
        spinal="SimBmbMemory",
        factory="avoodoo.sim_membus_memory:SimMemBusMemory",
        params=dict(p=_FB, size=4 * 1024 * 1024),
        note="the real CoreSim instance, 4 MiB — diff it as SimBmbMemorySmall instead",
        pending=(
            "not lockstep-diffable at this size: 1 048 576 words makes "
            "`amaranth_yosys` write_verilog run for minutes and emit a file "
            "Verilator then has to chew through, and the Spinal side is four "
            "1 Mi-entry byte arrays on top. Use MOD=SimBmbMemorySmall (64 KiB, "
            "same parameters and timing); only `wordAddrWidth` differs, and the "
            "logic is otherwise depth-independent. Revisit if a machine with "
            "time to burn wants the full-size run."
        ),
    ),
}


def names() -> list[str]:
    return sorted(REGISTRY)


def resolve(name: str) -> DiffEntry:
    try:
        return REGISTRY[name]
    except KeyError:
        raise SystemExit(
            f"unknown diff module {name!r}; known: {', '.join(names())}"
        ) from None
