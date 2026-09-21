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
from dataclasses import replace as _replace

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
# Phase 1.5 (AXI bridges): the BAR width and the post-aligner/fixer parameter
# that BmbToAxi4SharedBridge sees inside BmbToAxi4Mem.
from avoodoo.lib.axilite4_to_membus import axi_config as _lite_cfg, bar_address_width as _bar_aw
from avoodoo.lib.membus_to_axi4_mem import aligned_parameter as _aligned_parameter
from avoodoo.lib.membus_to_axi4_mem import tex_axi_config as _tex_axi_config

_BAR_AW = _bar_aw()
_FIXER_OP = _aligned_parameter(_FB, _mp.AXI_BURST_LOG2)
# Phase 8.0: `TmuNoTextureCache` requires `!useTexFillCache` (GenOne's row
# uses `Config.voodoo1().copy(useTexFillCache = false)`), and the Tang Console
# builds `Config.voodoo1().copy(...)` exactly as `CoreGowinGen.scala`
# (`TangConsole138kGen`, l. 65-80) spells it -- mirrored by `GenOne.consoleConfig`.
_C_NO_TEX_CACHE = _replace(_C, use_tex_fill_cache=False)
_C_CONSOLE = _replace(_C, use_fb_write_buffer=False, fb_write_buffer_line_words=16,
                      use_fb_read_cache=False, use_tex_fill_cache=False,
                      tex_fill_request_window=2, hdmi_scanout_pixel_repeat_x=4,
                      hdmi_scanout_dmt640x480=True)


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

    pinned: dict[str, int] = field(default_factory=dict)
    """Spinal input port name -> constant it is held at for the whole run.

    The stopgap for an input the random driver must not touch (a ``source``
    field the DUT only accepts in one state, a mode bit that would otherwise
    flip mid-transaction).  Pinned ports are removed from the random
    generator; a stream's ``valid``/``ready`` can be pinned too.
    """

    responder: str | None = None
    """``package.module:factory`` for a per-row stimulus model.

    The general answer to "this input stream is really a slave response":
    the driver instantiates the factory once per seed and, every cycle, lets
    the object observe the DUT and drive the inputs it claims. Protocol and
    timing are documented on :class:`tools.diff.responders.Responder`; a worked
    example is ``tools.diff.responders``.
    """

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


def _register_bank_overrides() -> dict[str, str]:
    """``sim_reg_<addr>_f<N>`` -> Amaranth leaf; see the RegisterBank row."""
    from avoodoo.frontend.register_bank import spinal_port_overrides

    return spinal_port_overrides()


def _pci_fifo_params() -> dict:
    """``PciFifo``'s four constructor arguments, as ``Core.scala:102`` builds them.

    ``GenOne`` elaborates the Spinal side with the real ``RegisterBank(c).busif``
    metadata (``RegisterBankMeta``); the Amaranth side takes the same three
    tables from :mod:`avoodoo.frontend.regdef`, which ``tests/test_regdef.py``
    pins against that elaboration.
    """
    from avoodoo.frontend import regdef

    return {
        "bus_params": _mp.register_bank_params(_C),
        "categories": regdef.categories(),
        "float_aliases": regdef.float_aliases(),
        "command_addresses": regdef.command_addresses(),
    }


def _core_sim_overrides() -> dict[str, str]:
    """``CoreSim``'s Amaranth ports are spelled exactly like the Spinal ones
    (:mod:`avoodoo.core_sim_ports`), so every Spinal name maps to itself."""
    from avoodoo.core_sim_ports import SPINAL_IO_PORTS

    return {name: name for name, _, _ in SPINAL_IO_PORTS}


def _hdmi_small_timing():
    """The ``VideoTiming`` of ``HdmiScanoutTest.scala`` "starts FIFO pixels at HDMI
    frame origin" (8x4 active, 12x7 total), as ``GenOne``'s
    ``HdmiCdcFramebufferScanoutSmall`` builds it."""
    from avoodoo.hdmi.scanout import VideoTiming

    return VideoTiming(active_width=8, active_height=4, h_front_porch=1, h_sync=1,
                       h_back_porch=2, v_front_porch=1, v_sync=1, v_back_porch=1)


REGISTRY: dict[str, DiffEntry] = {
    # --- Phase 1.6 ------------------------------------------------------
    "PciFifo": DiffEntry(
        spinal="PciFifo",
        factory="avoodoo.bus.pci_fifo:PciFifo",
        note="Core.scala:102 — PciFifo(RegisterBank.bmbParams(c), busif.getCategories "
             "(158), busif.getFloatAliases (30), busif.getCommandStreamReady.keys (5)) "
             "(ext/SpinalVoodoo/src/voodoo/bus/PciFifo.scala -> avoodoo/bus/pci_fifo.py)",
        params=_pci_fifo_params(),
        # No overrides: the two Bmb buses pair like AddressRemapper's, the
        # Vec `io_commandReady_<N>` pairs against `In(1).array(5)` ->
        # `command_ready__<N>`, and the `floatShadow` Flow's leaves land in
        # the packed `float_shadow__payload` (address, then the signed 50-bit
        # `raw`). The stimulus caveat is in tests/diff/test_diff_pci_fifo.py:
        # a 12-bit random address hits the 158 decoded registers ~4% of the
        # time, the 30 float aliases ~0.7%, and the 5 command addresses
        # ~0.1%, so the sync / command-blocked drain paths are exercised but
        # thinly per run — hence the 1e6-cycle gate rather than the short one.
    ),
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
        # io_cmd carries `NoData`, so Spinal emits only io_cmd_valid/ready; the
        # Amaranth `cmd` is stream.Signature(unsigned(0)) and its zero-width
        # `cmd__payload` is dropped by amports.py, not paired.
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
    # --- Phase 2.2 ------------------------------------------------------
    "RegisterBank": DiffEntry(
        spinal="RegisterBank",
        factory="avoodoo.frontend.register_bank:RegisterBank",
        note="Core.scala:101 — RegisterBank(c), Config.voodoo1(), simulation = False "
             "(ext/SpinalVoodoo/src/voodoo/frontend/RegisterBank.scala -> "
             "avoodoo/frontend/register_bank.py; BmbBusInterface.scala -> "
             "avoodoo/bus/bus_interface.py)",
        # The io_ bundle, the five `commands_*CmdStream_*` streams and the 30
        # AFix `triangleGeometry_<area>_<reg>` outputs all pair mechanically.
        # The remaining 231 Spinal outputs are the `.asOutput()` register
        # fields, which upstream renames `sim_reg_%03x_f%d` for the Verilator
        # harness (RegisterBank.scala:1240-1249); the Amaranth side exposes
        # them as ordinary ports grouped by Scala Area (`render_config__
        # fbz_mode__enable_clipping`), so `spinal_port_overrides()` derives the
        # map from regdef -- field index counted the way regif inserts its NA
        # padding, which tests/test_register_bank.py pins against the
        # committed --dump-regmeta ground truth.
        overrides=_register_bank_overrides(),
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
    # --- Phase 4 (avoodoo/pixel/color_combine.py) -------------------------
    "ColorCombine": DiffEntry(
        spinal="ColorCombine",
        factory="avoodoo.pixel.color_combine:ColorCombine",
        note="PixelPipeline.scala:225 — ColorCombine(c), Config.voodoo1(); the "
             "spinal.lib.misc.pipeline user (n0..n8, eight StageLinks, 34 payloads) "
             "(ext/SpinalVoodoo/src/voodoo/pixel/ColorCombine.scala -> "
             "avoodoo/pixel/color_combine.py)",
        # No overrides: the two streams are the only ports, and all 126
        # payload leaves (io_input_payload_config_alphaMselect ->
        # input__payload__config__alpha_mselect, io_input_payload_color0Alpha
        # -> input__payload__color0_alpha, the AFix leaf io_input_payload_depth
        # -> input__payload__depth, ...) pair against slices of the packed
        # Amaranth `input__payload` / `output__payload` mechanically.
        # The 150 payload stage registers are reset-less on both sides (plain
        # Spinal `Reg`), the eight `valid` registers reset to 0; the harness
        # resetting before every seed is what proves the enables match.
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
    # --- Phase 4 (agent p4-fbaccess owns avoodoo/pixel/framebuffer_access.py) --
    "FramebufferAccess": DiffEntry(
        spinal="FramebufferAccess",
        factory="avoodoo.pixel.framebuffer_access:FramebufferAccess",
        note="PixelPipeline.scala:226 — FramebufferAccess(c), Config.voodoo1(); the "
             "StreamFork(5, synchronous = false) / StreamDemux / StreamJoin fetch "
             "adapter, depth test and alpha blend with two comb-read dsub ROMs "
             "(ext/SpinalVoodoo/src/voodoo/pixel/FramebufferAccess.scala -> "
             "avoodoo/pixel/framebuffer_access.py)",
        # No overrides: io_fbReadColorReq_* -> fb_read_color_req__*,
        # io_zFuncFail -> z_func_fail, and the Fog.Output / Output payload
        # leaves (io_input_payload_wDepth -> input__payload__w_depth,
        # io_output_payload_newDepth -> output__payload__new_depth, the AFix
        # leaf io_input_payload_depth) pair against the packed payloads.
        # The bare generator drives the two read responses at random, which
        # the module accepts whenever its 4-deep response queues have room
        # (nothing parks: an unmatched response just waits for the next
        # pixel that needs one, identically on both sides); the responder
        # row below answers real requests instead.
    ),
    "FramebufferAccessMem": DiffEntry(
        spinal="FramebufferAccessMem",
        portmap="FramebufferAccess",
        factory="avoodoo.pixel.framebuffer_access:FramebufferAccess",
        note="FramebufferAccess behind tests/diff/framebuffer_access_stimulus.py: "
             "a plane memory answers every fbReadColorReq / fbReadAuxReq beat in "
             "order from a lazily random image, with random latency and idle "
             "cycles, so each response is the one its pixel asked for and the "
             "pixel/read/output counts are reported per seed",
        responder="tests.diff.framebuffer_access_stimulus:plane_memory",
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
    # --- Phase 3 (fastfill, 3 files: avoodoo/raster/fastfill*.py) ---------
    "Fastfill": DiffEntry(
        spinal="Fastfill",
        factory="avoodoo.raster.fastfill:Fastfill",
        note="PixelPipeline — Fastfill(c), Config.voodoo1(); the StreamWhile clip-rectangle "
             "pixel generator (ext/SpinalVoodoo/src/voodoo/raster/Fastfill.scala -> "
             "avoodoo/raster/fastfill.py)",
        # No overrides: `i` is Stream(NoData) (zero-width payload, no Spinal
        # port -- tests/diff/test_diff_nodata.py is the proof that pairs), the
        # `o_payload_coords_0/_1` Vec pairs against the ArrayLayout slices of
        # `o__payload`, and the four clip inputs are bare `clipLeft` ->
        # `clip_left`.
    ),
    "FastfillWrite": DiffEntry(
        spinal="FastfillWrite",
        factory="avoodoo.raster.fastfill_write:FastfillWrite",
        note="PixelPipeline — FastfillWrite(c), Config.voodoo1(); Fastfill.Output -> "
             "Write.PreDither with capture-on-cmdFire "
             "(ext/SpinalVoodoo/src/voodoo/raster/FastfillWrite.scala -> "
             "avoodoo/raster/fastfill_write.py)",
        # No overrides: `io_regs_*` (24 leaves incl. the FbzMode bundle) pair
        # against slices of the packed `regs` input, `io_pixels_payload_coords_0`
        # against `pixels__payload__coords__0`, `io_output_payload_coords_x`
        # against `output__payload__coords__x` (PixelCoords is a Bundle, not
        # a Vec, on the PreDither side).  `captured` is reset-less on both
        # sides; `ffActive` resets.
    ),
    "FastfillWordWriter": DiffEntry(
        spinal="FastfillWordWriter",
        factory="avoodoo.raster.fastfill_word_writer:FastfillWordWriter",
        note="Fastfill — FastfillWordWriter(c), Config.voodoo1(); word-pair fastfill writer "
             "with two Dither instances, StreamFork2 x3, StreamJoin x2, m2sPipe, halfPipe "
             "(ext/SpinalVoodoo/src/voodoo/raster/FastfillWordWriter.scala -> "
             "avoodoo/raster/fastfill_word_writer.py)",
        # No overrides: `io_cmd` is Stream(NoData), `io_regs_*` pairs like
        # FastfillWrite's, `io_colorWrite_payload_*` / `io_auxWrite_payload_*`
        # against the packed FramebufferWriteReq payloads, and the five status
        # outputs are bare leaves.  The random driver's `io_cmd_valid` at
        # --valid-density retriggers the walk as soon as `active` drops, and
        # the two write streams' `ready` at --ready-density exercise the
        # halfPipe / synchronous-fork drain path; the width-10 clip inputs are
        # free, so most rectangles are large and many are empty.
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
    "TmuTextureCache": DiffEntry(
        spinal="TmuTextureCache",
        factory="avoodoo.texture.tmu_texture_cache:TmuTextureCache",
        note="Tmu.scala:597 — TmuTextureCache(c) when c.useTexFillCache, Config.voodoo1(): "
             "16 slots x 2 ways x 8-word lines, 16-deep request window "
             "(ext/SpinalVoodoo/src/voodoo/texture/TmuTextureCache.scala -> "
             "avoodoo/texture/tmu_texture_cache.py)",
        # `io.sampleFetch.valid.setName("dbgDataValid")` renames the output
        # port itself in the Spinal Verilog (the only markSimulationOnly alias
        # that reaches the ports), so ports.py sees a bare scalar; map it back
        # onto the stream's valid.  io_sampleFetch_ready still classifies as
        # the stream's ready and is driven at --ready-density.
        overrides={"dbgDataValid": "sample_fetch__valid"},
        # Random 26-bit tap addresses never revisit a line (16 x 32 bytes out
        # of 64 MiB), and a random io_texRead_rsp parks on haltWhen(!fill.active)
        # or ends a burst on a random `last`; the responder answers each cmd
        # with exactly lineWords beats and drives io_sampleRequest from a small
        # address pool so hits, bilinear multi-fills, continue fills and epoch
        # invalidation all happen in lockstep.
        responder="tests.diff.tmu_texture_cache_stimulus:cache_traffic",
    ),
    # --- Phase 8.0: the Console's uncached texture path --------------------
    "TmuNoTextureCache": DiffEntry(
        spinal="TmuNoTextureCache",
        factory="avoodoo.texture.tmu_no_texture_cache:TmuNoTextureCache",
        params=dict(c=_C_NO_TEX_CACHE),
        note="Tmu.scala:597 — TmuNoTextureCache(c) when !c.useTexFillCache, "
             "Config.voodoo1().copy(useTexFillCache = false): one 4-byte read per "
             "distinct tap word, 16-deep request window, 16-deep word queue "
             "(ext/SpinalVoodoo/src/voodoo/texture/TmuTextureCache.scala:865 -> "
             "avoodoo/texture/tmu_no_texture_cache.py)",
        # io_texRead_rsp is a slave response (the join only takes it against a
        # queued word request; a random `last` would drop beats at random on
        # both sides, and a random held beat with nothing queued parks it), so
        # the responder answers each cmd with exactly its one-word burst.  It
        # also drives io_sampleRequest with taps that mostly share words (so
        # `currentMask` folds several taps into one read) and some scattered.
        responder="tests.diff.tmu_no_texture_cache_stimulus:direct_traffic",
    ),
    "TmuNoTextureCacheConsole": DiffEntry(
        spinal="TmuNoTextureCacheConsole",
        factory="avoodoo.texture.tmu_no_texture_cache:TmuNoTextureCache",
        params=dict(c=_C_CONSOLE),
        note="TmuNoTextureCache with the Tang Console's config (CoreGowinGen.scala:73): "
             "texFillRequestWindow = 2",
        responder="tests.diff.tmu_no_texture_cache_stimulus:direct_traffic",
    ),
    "TmuConsole": DiffEntry(
        spinal="TmuConsole",
        factory="avoodoo.texture.tmu:Tmu",
        params=dict(c=_C_CONSOLE),
        note="PixelPipeline.scala:294 — Tmu(c) with the Tang Console's config "
             "(CoreGowinGen.scala:65-80: useTexFillCache = false -> TmuNoTextureCache, "
             "texFillRequestWindow = 2): the TMU the bitstream has; same stimulus as Tmu",
        responder="tests.diff.tmu_stimulus:tmu_traffic",
    ),
    "Tmu": DiffEntry(
        spinal="Tmu",
        factory="avoodoo.texture.tmu:Tmu",
        note="PixelPipeline.scala:294 — Tmu(c), Config.voodoo1(): the whole texture "
             "unit -- S/T/W front end, reciprocal + log2 ROMs, LOD, coordinate and "
             "packed address generation, the 16-deep request FIFO, TmuTextureCache "
             "and TmuTexelDecoder inside, bypass/in-flight accounting "
             "(ext/SpinalVoodoo/src/voodoo/texture/Tmu.scala -> avoodoo/texture/tmu.py)",
        # The cache only takes an io_texRead_rsp beat while a fill is
        # outstanding, so a random held beat parks that stream; the responder
        # answers each cmd with the burst it asked for.  It also drives
        # io_input with texture-shaped pixels (per-seed table set-ups, a random
        # walk in S/T, sane 1/W) so cache *hits* happen -- random 48-bit S/T
        # never revisit a line -- with one pixel in twenty "wild" (arithmetic
        # inputs random, tables consistent; none fully random, see the stimulus
        # module) for the bypass / clamp-to-zero / wrap corners, and pulses
        # io_invalidate rarely.
        responder="tests.diff.tmu_stimulus:tmu_traffic",
    ),
    "TmuMemOnly": DiffEntry(
        spinal="TmuMemOnly",
        portmap="Tmu",
        factory="avoodoo.texture.tmu:Tmu",
        note="Tmu with every arithmetic input random: S, T, 1/W, the gradients, "
             "the LOD register and the mode bits are raw random patterns on every "
             "pixel (every clz / shift / wrap path, both signs of 1/W, zero), only "
             "the packed tables stay consistent with the format so the cache can "
             "resolve the taps -- a fully random io_input parks it on the first "
             "address outside every LOD (34 pixels per 1e6 cycles)",
        responder="tests.diff.tmu_stimulus:tmu_wild",
    ),
    "TextureMemSubsystem": DiffEntry(
        spinal="TextureMemSubsystem",
        factory="avoodoo.core.texture_mem_subsystem:TextureMemSubsystem",
        note="Core.scala:112 — TextureMemSubsystem(c), Config.voodoo1(): the CPU "
             "texture-download write path (TextureMem.scala) plus the 3-way "
             "TMU/CPU-write/CPU-read arbitration onto texMem "
             "(ext/SpinalVoodoo/src/voodoo/core/TextureMemSubsystem.scala -> "
             "avoodoo/core/texture_mem_subsystem.py, avoodoo/texture/texture_mem.py)",
        # No overrides: io_downloadConfig_texBaseAddr38 -> the
        # download_config__tex_base_addr38 slice, io_tmuTexRead_cmd_payload_fragment_*
        # -> slices of the packed tmu_tex_read__cmd__payload (no data/mask: the
        # TMU bus is canWrite = false on both sides).  The generic driver
        # randomises tex_mem.rsp.source, so the response demux is exercised
        # with every route tag including the unmapped 3; the "rsp echoes cmd
        # source" contract is the memory's, not this module's, and both sides
        # route the same way regardless.
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
    # --- Phase 6 (agent p6-planebuffer owns avoodoo/framebuffer/plane_buffer.py)
    # Three rows, one module: `Config.voodoo1()` has useFbWriteBuffer = false,
    # so the plain entry (what CoreSim and TangConsole138k build) is the
    # 8-deep direct queue only; the two Wb rows elaborate the write combiner
    # with the Dock/Artix parameters and with the throughput test's smallest
    # line, which is the one random stimulus can fill (2 words) and so the
    # only one that reaches the sequential-append / full-line-drain paths.
    # No overrides: every leaf pairs mechanically, the 20 stat counters as
    # io_writeSingleWordDrainStartAtZeroCount ->
    # write_single_word_drain_start_at_zero_count.
    "FramebufferPlaneBuffer": DiffEntry(
        spinal="FramebufferPlaneBuffer",
        factory="avoodoo.framebuffer.plane_buffer:FramebufferPlaneBuffer",
        note="FramebufferMemSubsystem.scala:86 — FramebufferPlaneBuffer(c), "
             "Config.voodoo1() (useFbWriteBuffer = false: direct 8-deep queue) "
             "(ext/SpinalVoodoo/src/voodoo/framebuffer/FramebufferPlaneBuffer.scala)",
    ),
    "FramebufferPlaneBufferWb": DiffEntry(
        spinal="FramebufferPlaneBufferWb",
        factory="avoodoo.framebuffer.plane_buffer:FramebufferPlaneBuffer",
        params=dict(c=_replace(_C, use_fb_write_buffer=True,
                               fb_write_buffer_line_words=256)),
        note="CoreGowinGen.scala:28 / CoreArtixGen.scala:29 — the write combiner "
             "as the Dock/Mega/Artix cores build it: two 255-deep slot FIFOs",
    ),
    "FramebufferPlaneBufferWbSmall": DiffEntry(
        spinal="FramebufferPlaneBufferWbSmall",
        factory="avoodoo.framebuffer.plane_buffer:FramebufferPlaneBuffer",
        params=dict(c=_replace(_C, address_width=12, mem_burst_length_width=4,
                               fb_write_buffer_line_words=2,
                               use_fb_write_buffer=True, use_tex_fill_cache=False)),
        note="test/FramebufferPlaneBufferThroughputTest.scala's config — 12-bit "
             "addresses, 2-word lines (slot FIFOs are one m2sPipe each)",
    ),
    # The two *Local rows are GenOne aliases of the Wb rows with a responder on io_writeReq
    # (tests/diff/plane_buffer_stimulus.py) that addresses each write relative
    # to the last one, so appends, merges, full-line drains and 256-beat
    # bursts happen in lockstep too. flush / cmd.ready / rsp stay random.
    "FramebufferPlaneBufferWbLocal": DiffEntry(
        spinal="FramebufferPlaneBufferWbLocal",
        portmap="FramebufferPlaneBufferWb",
        factory="avoodoo.framebuffer.plane_buffer:FramebufferPlaneBuffer",
        params=dict(c=_replace(_C, use_fb_write_buffer=True,
                               fb_write_buffer_line_words=256)),
        note="FramebufferPlaneBufferWb with locality-biased writes: the row that "
             "fills 256-word lines and drains them as one burst",
        responder="tests.diff.plane_buffer_stimulus:local_writes_256",
    ),
    "FramebufferPlaneBufferWbSmallLocal": DiffEntry(
        spinal="FramebufferPlaneBufferWbSmallLocal",
        portmap="FramebufferPlaneBufferWbSmall",
        factory="avoodoo.framebuffer.plane_buffer:FramebufferPlaneBuffer",
        params=dict(c=_replace(_C, address_width=12, mem_burst_length_width=4,
                               fb_write_buffer_line_words=2,
                               use_fb_write_buffer=True, use_tex_fill_cache=False)),
        note="FramebufferPlaneBufferWbSmall with locality-biased writes",
        responder="tests.diff.plane_buffer_stimulus:local_writes_2",
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
    # --- Phase 2.3 (agent p2-lfb-frontdoor) ------------------------------
    "Lfb": DiffEntry(
        spinal="Lfb",
        factory="avoodoo.frontend.lfb:Lfb",
        note="PixelPipeline.scala — Lfb(c), Config.voodoo1() "
             "(ext/SpinalVoodoo/src/voodoo/frontend/Lfb.scala -> avoodoo/frontend/lfb.py)",
        # No overrides. `io_regs_*` (lfbMode/fbzMode/alphaMode/fogMode/
        # fogColor/zaColor/writeRouting/readRouting) is one packed 266-bit
        # `regs` port (core_types.LfbRegs) whose leaves amports.py slices;
        # both output payloads are packed the same way. The generic driver
        # re-randomises `regs` every cycle, so writeFormat / rgbaLanes / the
        # swap+swizzle bits change under a captured command -- harsher than
        # the register bank, and exactly what the decode mux is measured on.
        # Reads: fbReadBus.rsp is a free input stream, so the two fetches
        # complete whenever the driver raises rsp.valid in RFETCH1/2, and the
        # RWAIT gate (pciFifoEmpty && !pipelineBusy) is random too.
    ),
    # --- Phase 4 assembly (avoodoo/core/pixel_pipeline.py) -----------------
    "PixelPipeline": DiffEntry(
        spinal="PixelPipeline",
        factory="avoodoo.core.pixel_pipeline:PixelPipeline",
        note="Core.scala:110 — PixelPipeline(c), Config.voodoo1(), inside GenOne's "
             "DiffHierWrap (the nine never-assigned CoreDebug words are not ports on "
             "either side; io_debug_pipelineBusySources is, via the working-tree edit) "
             "(ext/SpinalVoodoo/src/voodoo/core/PixelPipeline.scala -> "
             "avoodoo/core/pixel_pipeline.py)",
        # No overrides: io_controls_paletteRegs_table0I0 -> the packed
        # controls__palette_regs__table0_i0 slice, io_controls_fogTable_<N> ->
        # controls__fog_table__<N>, io_controls_nccTables_table0_y_<N> ->
        # controls__ncc_tables__table0__y__<N>, io_debug_busy_* /
        # io_debug_writePath_* -> debug__busy__* / debug__write_path__*.
        # The responder drives io_triangleCmd / io_ftriangleCmd with real
        # triangles (consistent packed texture tables -- a random tex_tables
        # word parks the TMU cache on continue-fills, see tmu_stimulus.py),
        # answers io_texRead, io_colorReadRsp / io_auxReadRsp in order, and
        # counts what went through; everything else (controls, lfbBus,
        # fastfillCmd, paletteWrite, the write/prefetch readies) stays random.
        responder="tests.diff.pixel_pipeline_stimulus:pipeline_traffic",
    ),
    "CoreCpuFrontdoor": DiffEntry(
        spinal="CoreCpuFrontdoor",
        factory="avoodoo.core.cpu_frontdoor:CoreCpuFrontdoor",
        note="Core.scala:110 — CoreCpuFrontdoor(c), Config.voodoo1() "
             "(ext/SpinalVoodoo/src/voodoo/core/CoreCpuFrontdoor.scala -> "
             "avoodoo/core/cpu_frontdoor.py)",
        # No overrides: four Bmb buses plus the TexWritePayload stream, all
        # pairing through the packed cmd/rsp payloads; `io_texBaseAddr` ->
        # `tex_base_addr`. Almost entirely combinational -- the only
        # registers are texWriteRspPending (reset) and texWriteRspSource
        # (plain Reg, reset_less) -- so the interesting cycles are the ones
        # where a texture write is accepted while the previous one's
        # acknowledgement is still pending.
    ),
    # --- Phase 6 (agent p6-planereader) ----------------------------------
    # The generic driver deadlocks FramebufferPlaneReader within ~30 cycles
    # (a held random source=1 rsp beat with no direct miss outstanding is
    # never accepted) and never produces a cache hit (a random 26-bit read
    # address equals the next prefetched lane once in 2**26), so the main
    # row carries tests/diff/plane_reader_stimulus.py: an in-order memory
    # slave on io_mem_rsp plus span-shaped io_prefetchReq/io_readReq traffic.
    # The *MemOnly row keeps prefetch/read random (mostly skips and misses)
    # behind the same memory slave, and *Random is the bare generator kept
    # for the record. cmd.ready and readRsp.ready stay random everywhere.
    "FramebufferPlaneReader": DiffEntry(
        spinal="FramebufferPlaneReader",
        factory="avoodoo.framebuffer.plane_reader:FramebufferPlaneReader",
        note="FramebufferMemSubsystem.scala:78 — FramebufferPlaneReader(c), "
             "Config.voodoo1(); needBurstSplitter is false so io.mem IS internalMem "
             "(ext/SpinalVoodoo/src/voodoo/framebuffer/FramebufferPlaneReader.scala "
             "-> avoodoo/framebuffer/plane_reader.py)",
        responder="tests.diff.plane_reader_stimulus:reader_traffic_800",
        # No overrides: io_prefetchReq_payload_{startAddress,endAddress} ->
        # slices of `prefetch_req__payload`, io_cacheDebugReadAddr ->
        # `cache_debug_read_addr`, the Bmb leaves as for BmbAligner.
    ),
    "FramebufferPlaneReaderNoDirectMiss": DiffEntry(
        spinal="FramebufferPlaneReaderNoDirectMiss",
        portmap="FramebufferPlaneReader",
        factory="avoodoo.framebuffer.plane_reader:FramebufferPlaneReader",
        params=dict(allow_direct_miss=False),
        note="FramebufferMemSubsystem.scala:98 — FramebufferPlaneReader(c, allowDirectMiss "
             "= false), the scanout reader: a read behind the consume pointer waits "
             "instead of issuing a direct miss -- forever, so the traffic is the "
             "scanout's: strictly in order (drops only, no repeats/random addresses)",
        responder="tests.diff.plane_reader_stimulus:reader_traffic_800_in_order",
    ),
    "FramebufferPlaneReaderMemOnly": DiffEntry(
        spinal="FramebufferPlaneReaderMemOnly",
        portmap="FramebufferPlaneReader",
        factory="avoodoo.framebuffer.plane_reader:FramebufferPlaneReader",
        note="FramebufferPlaneReader with only the memory slave attached: random "
             "spans and random read addresses, i.e. forward skips, direct misses "
             "and inverted spans, all with well-formed responses",
        responder="tests.diff.plane_reader_stimulus:mem_slave",
    ),
    "FramebufferPlaneReaderRandom": DiffEntry(
        spinal="FramebufferPlaneReaderRandom",
        portmap="FramebufferPlaneReader",
        factory="avoodoo.framebuffer.plane_reader:FramebufferPlaneReader",
        note="FramebufferPlaneReader under the bare random generator — passes, but "
             "proves ~30 live cycles per seed before the rsp stream parks; kept so "
             "the deadlock stays reproducible (see tests/diff/plane_reader_stimulus.py)",
    ),
    "FramebufferPlaneDirectReader": DiffEntry(
        spinal="FramebufferPlaneDirectReader",
        factory="avoodoo.framebuffer.plane_direct_reader:FramebufferPlaneDirectReader",
        note="FramebufferMemSubsystem.scala:82 — FramebufferPlaneDirectReader(c), "
             "Config.voodoo1(), same FramebufferPlaneReader.bmbParams(c) bus "
             "(ext/SpinalVoodoo/src/voodoo/framebuffer/FramebufferPlaneDirectReader.scala "
             "-> avoodoo/framebuffer/plane_direct_reader.py)",
        # The bare generator is fine here: the direct reader ignores rsp.source,
        # so a random rsp beat is accepted whenever its laneQueue is non-empty
        # (probe: 7445 cmd / 6244 readRsp fires in 20k cycles).
    ),
    "FramebufferPlaneDirectReaderMem": DiffEntry(
        spinal="FramebufferPlaneDirectReaderMem",
        portmap="FramebufferPlaneDirectReader",
        factory="avoodoo.framebuffer.plane_direct_reader:FramebufferPlaneDirectReader",
        note="FramebufferPlaneDirectReader behind the in-order memory slave, so "
             "every rsp beat answers a real command",
        responder="tests.diff.plane_reader_stimulus:mem_slave",
    ),
    # --- Phase 6 (agent p6-fbmemsub) --------------------------------------
    # The whole framebuffer memory front end: two plane buffers, three cached
    # readers, four arbiters, fourteen s2mPipes.  The generic driver parks it
    # the way it parks FramebufferPlaneReader (a random rsp beat on a bus with
    # nothing outstanding is never accepted), so the main row carries
    # tests/diff/framebuffer_mem_subsystem_stimulus.py: an in-order memory
    # slave on each of the four live fbMem ports (echoing `source`, which the
    # arbiters route responses by), span-shaped prefetch/read traffic on the
    # colour, aux and scanout readers (the scanout one strictly in order,
    # since allowDirectMiss = false makes a read behind the pointer wait for
    # ever), and single-beat lfbReadBus commands.  Writes, flush and every
    # `ready` stay with the generator.
    "FramebufferMemSubsystem": DiffEntry(
        spinal="FramebufferMemSubsystem",
        factory="avoodoo.core.framebuffer_mem_subsystem:FramebufferMemSubsystem",
        note="Core.scala:111 — FramebufferMemSubsystem(c), Config.voodoo1(): "
             "useFbWriteBuffer = false (one-input write bridges, fbMemWrite tied "
             "off), useFbReadCache = true (cached colour/aux readers) "
             "(ext/SpinalVoodoo/src/voodoo/core/FramebufferMemSubsystem.scala -> "
             "avoodoo/core/framebuffer_mem_subsystem.py)",
        responder="tests.diff.framebuffer_mem_subsystem_stimulus:subsystem_traffic",
        # No overrides: io_stats_<x> / io_status_<x> pair against slices of the
        # packed `stats` / `status` StructLayout ports, the five Bmb masters
        # and lfbReadBus as for BmbArbiter, the streams as for the readers.
    ),
    "FramebufferMemSubsystemRandom": DiffEntry(
        spinal="FramebufferMemSubsystemRandom",
        portmap="FramebufferMemSubsystem",
        factory="avoodoo.core.framebuffer_mem_subsystem:FramebufferMemSubsystem",
        note="FramebufferMemSubsystem under the bare random generator: the read "
             "buses park within a few hundred cycles (see the main row), the "
             "write path and lfbReadBus keep going; kept so that stays reproducible",
    ),
    # --- Phase 1.5: AXI boundary bridges (fpga/src, not upstream) ---------
    # Every Spinal leaf (io_axi_aw_payload_addr, io_output_arw_payload_write,
    # ...) pairs against a slice of the packed Amaranth stream payload
    # (`axi__aw__payload`, `output__arw__payload`) through amports.py; the
    # AxiLite4/Axi4 layouts in avoodoo/lib/axi.py follow the Scala bundle
    # declaration order, so no overrides.
    "Axi4ToAxiLite4": DiffEntry(
        spinal="Axi4ToAxiLite4",
        factory="avoodoo.lib.axi4_to_axilite4:Axi4ToAxiLite4",
        params=dict(address_width=_BAR_AW),
        note="AmdAc701.scala:56 — Axi4ToAxiLite4(CoreArtix.barAddressWidth), idWidth 4 "
             "(fpga/src/voodoo/artix/Axi4ToAxiLite4.scala -> avoodoo/lib/axi4_to_axilite4.py)",
    ),
    "AxiLite4ToBmbBridge": DiffEntry(
        spinal="AxiLite4ToBmbBridge",
        factory="avoodoo.lib.axilite4_to_membus:AxiLite4ToMemBusBridge",
        params=dict(address_width=_BAR_AW, bmb_params=_mp.cpu_bmb_params()),
        note="CoreGowin.scala:37 — AxiLite4ToBmbBridge(barAddressWidth, Core.cpuBmbParams), "
             "timeoutLog2 = 20 (fpga/src/voodoo/artix/CoreArtix.scala -> "
             "avoodoo/lib/axilite4_to_membus.py)",
    ),
    "AxiLite4ToBmbBridgeFastTimeout": DiffEntry(
        spinal="AxiLite4ToBmbBridgeFastTimeout",
        factory="avoodoo.lib.axilite4_to_membus:AxiLite4ToMemBusBridge",
        params=dict(address_width=_BAR_AW, bmb_params=_mp.cpu_bmb_params(), timeout_log2=4),
        note="same bridge, timeoutLog2 = 4: the wedged / timeout / droppedWrites paths are "
             "reachable by random stimulus (run it with --ready-density 0.1 too)",
    ),
    "BmbToAxi4SharedBridge": DiffEntry(
        spinal="BmbToAxi4SharedBridge",
        factory="avoodoo.lib.membus_to_axi4_mem:MemBusToAxi4SharedBridge",
        params=dict(bmb_config=_FIXER_OP),
        note="CoreArtix.scala:228 — BmbToAxi4SharedBridge(fixer.op), pendingMax 31, halfRateAw "
             "(spinal/lib/bus/bmb/BmbToAxi4Bridge.scala -> avoodoo/lib/membus_to_axi4_mem.py)",
    ),
    "BmbToAxi4Mem": DiffEntry(
        spinal="BmbToAxi4Mem",
        factory="avoodoo.lib.membus_to_axi4_mem:MemBusToAxi4Mem",
        params=dict(p=_FB, burst_log2=_mp.AXI_BURST_LOG2),
        note="CoreGowin.scala:44 — BmbToAxi4Mem(Core.fbMemBmbParams(c), 8): aligner + fixer + "
             "shared bridge + toAxi4 (fpga/src/voodoo/artix/CoreArtix.scala)",
    ),
    "BmbToAxi4MemTex": DiffEntry(
        spinal="BmbToAxi4MemTex",
        factory="avoodoo.lib.membus_to_axi4_mem:MemBusToAxi4Mem",
        params=dict(p=_mp.tex_mem_bmb_params(_C), burst_log2=_mp.AXI_BURST_LOG2),
        note="CoreGowin.scala:45 — BmbToAxi4Mem(Core.texMemBmbParams(c), 8), sourceWidth 6",
    ),
    "AxiLite4ClockCrossing": DiffEntry(
        spinal="AxiLite4ClockCrossing",
        factory="avoodoo.gowin.axilite4_clock_crossing:AxiLite4ClockCrossing",
        params=dict(config=_lite_cfg(_BAR_AW)),
        note="TangMega138kPro.scala:76 / AmdAc701.scala:60 — five StreamFifoCC(depth 4); both "
             "domains are `sync` here because the harness drives one clock, so this proves the "
             "FIFO logic and the buffered pop reset, not the asynchrony (pysim covers that) "
             "(fpga/src/voodoo/gowin/AxiLite4ClockCrossing.scala -> "
             "avoodoo/gowin/axilite4_clock_crossing.py)",
    ),
    "Axi4AddressRelocate": DiffEntry(
        spinal="Axi4AddressRelocate",
        factory="avoodoo.gowin.axi4_address_relocate:Axi4AddressRelocate",
        params=dict(config=_tex_axi_config(_C), offset=4 * 1024 * 1024),
        note="CoreGowin.scala:46 — Axi4AddressRelocate(texAxiConfig(c), GowinAddressMap.texMemBase "
             "= 4 MiB); combinational, no clk/reset on either side "
             "(fpga/src/voodoo/gowin/CoreGowin.scala -> avoodoo/gowin/axi4_address_relocate.py)",
    ),
    # --- Phase 7.1 (hdmi/scanout.py) ----------------------------------------
    # All seven components of HdmiScanout.scala.  The harness drives one
    # clock, but HdmiCdcFramebufferScanout takes its HDMI clock and reset as
    # plain `in Bool` ports (io_hdmiClock / io_hdmiReset), so a responder
    # (tests/diff/hdmi_scanout_stimulus.py) toggles the pixel clock at a
    # per-seed ratio and pulses the HDMI reset -- both Verilator models see the
    # identical edge sequence, so the whole two-domain design is diffed in
    # lockstep, CDC FIFO and synchronisers included.
    "VideoScanout": DiffEntry(
        spinal="VideoScanout",
        factory="avoodoo.hdmi.scanout:VideoScanout",
        note="HdmiScanout.scala:133 — VideoScanout() (VideoTiming() = dmt640x480); `enable` "
             "random every cycle, so the counters mostly restart -- see VideoScanoutRun "
             "(ext/SpinalVoodoo/src/voodoo/hdmi/HdmiScanout.scala -> avoodoo/hdmi/scanout.py)",
    ),
    "VideoScanoutRun": DiffEntry(
        spinal="VideoScanoutRun",
        portmap="VideoScanout",
        factory="avoodoo.hdmi.scanout:VideoScanout",
        note="VideoScanout with io_enable pinned high: 800 x 525 free-running, "
             "2.4 frames per 1e6 cycles",
        pinned={"io_enable": 1},
    ),
    "HdmiTestPattern": DiffEntry(
        spinal="HdmiTestPattern",
        factory="avoodoo.hdmi.scanout:HdmiTestPattern",
        note="HdmiScanout.scala:189 — HdmiTestPattern(); combinational, random x/y/active",
    ),
    "HdmiFramebufferScanout": DiffEntry(
        spinal="HdmiFramebufferScanout",
        factory="avoodoo.hdmi.scanout:HdmiFramebufferScanout",
        note="HdmiScanout.scala:657 — HdmiFramebufferScanout(Config.voodoo1()), cea720x480p; "
             "single clock; regs, swap and readRsp from the generator",
    ),
    "HdmiFramebufferScanoutScene": DiffEntry(
        spinal="HdmiFramebufferScanoutScene",
        portmap="HdmiFramebufferScanout",
        factory="avoodoo.hdmi.scanout:HdmiFramebufferScanout",
        note="HdmiFramebufferScanout with io_regs_* held as register-like scenes "
             "(tests/diff/hdmi_scanout_stimulus.py) so contentActive spans whole frames",
        responder="tests.diff.hdmi_scanout_stimulus:framebuffer_scene",
    ),
    "HdmiCdcFramebufferScanout": DiffEntry(
        spinal="HdmiCdcFramebufferScanout",
        factory="avoodoo.hdmi.scanout:HdmiCdcFramebufferScanout",
        params=dict(pixel_repeat_x=_C.hdmi_scanout_pixel_repeat_x),
        note="Core.scala:133 — HdmiCdcFramebufferScanout(c, pixelRepeatX = "
             "c.hdmiScanoutPixelRepeatX), cea720x480p, fifo 4096/2048/1024/3072; responder: "
             "HDMI clock+reset, register scenes, in-order readRsp slave",
        responder="tests.diff.hdmi_scanout_stimulus:cdc_scene",
    ),
    "HdmiCdcFramebufferScanoutRandom": DiffEntry(
        spinal="HdmiCdcFramebufferScanoutRandom",
        portmap="HdmiCdcFramebufferScanout",
        factory="avoodoo.hdmi.scanout:HdmiCdcFramebufferScanout",
        params=dict(pixel_repeat_x=_C.hdmi_scanout_pixel_repeat_x),
        note="HdmiCdcFramebufferScanout with only the HDMI clock/reset claimed; regs and "
             "readRsp random every cycle: unsolicited responses wrap `outstanding`, so "
             "readReq never fires and only the FIFO-full / prefetch / HDMI paths run",
        responder="tests.diff.hdmi_scanout_stimulus:cdc_clock_only",
    ),
    "HdmiCdcFramebufferScanoutSmall": DiffEntry(
        spinal="HdmiCdcFramebufferScanoutSmall",
        factory="avoodoo.hdmi.scanout:HdmiCdcFramebufferScanout",
        params=dict(timing=_hdmi_small_timing(), fifo_depth=64, prefill_level=8,
                    refill_low_level=8, refill_high_level=48),
        note="HdmiScanoutTest.scala 'starts FIFO pixels at HDMI frame origin' build: 8x4 "
             "active in a 12x7 raster, fifo 64/8/8/48 -- thousands of frames per 1e6 "
             "cycles, so frameAligned / newFrame / end-of-frame reload are exercised",
        responder="tests.diff.hdmi_scanout_stimulus:cdc_scene_small",
    ),
    "TmdsEncoder": DiffEntry(
        spinal="TmdsEncoder",
        factory="avoodoo.hdmi.scanout:TmdsEncoder",
        note="HdmiScanout.scala:522 — TmdsEncoder(); random data/control/dataEnable",
    ),
    "HdmiTestPatternScanout": DiffEntry(
        spinal="HdmiTestPatternScanout",
        factory="avoodoo.hdmi.scanout:HdmiTestPatternScanout",
        note="HdmiScanout.scala:581 — HdmiTestPatternScanout(); no inputs, free-running",
    ),
    "TmdsSerializer": DiffEntry(
        spinal="TmdsSerializer",
        factory="avoodoo.hdmi.scanout:TmdsSerializer",
        note="HdmiScanout.scala:620 — TmdsSerializer(); random load/symbols",
    ),
    # --- Phase 7.2 (avoodoo/core/core.py, avoodoo/core_sim.py) ------------
    "Core": DiffEntry(
        spinal="Core",
        factory="avoodoo.core.core:Core",
        note="CoreSim.scala:29 / GenOne — Core(Config.voodoo1()), enableHdmiScanout = "
             "true so the scanout prefetches through fbColorReadMem "
             "(ext/SpinalVoodoo/src/voodoo/Core.scala -> avoodoo/core/core.py)",
        # No overrides: io_cpuBus_* / io_fb*Mem_* / io_texMem_* pair through the
        # packed MemBus payloads, io_statusInputs_* / io_hdmi_video_* /
        # io_hdmi_status_* are slices of the packed StructLayout ports,
        # io_hdmi_clock / io_hdmi_reset -> hdmi__clock / hdmi__reset.  The
        # responder (tests/diff/core_stimulus.py) is a memory slave on each of
        # the six memory buses (source echoed, so both arbiter layers demux),
        # the HDMI pixel clock/reset, and a CPU that issues register writes
        # (mostly to decoded addresses, with sane fbzMode/clip windows so
        # triangles stay small), real triangle commands, LFB writes/reads and
        # texture writes/reads through io_cpuBus; statusInputs, flushFbCaches
        # and every ready stay random.
        responder="tests.diff.core_stimulus:core_traffic",
        # A moving base strands the in-order scanout reader during its
        # prefill (core_stimulus.py has the mechanism); CoreSim ties it to 0.
        pinned={"io_fbBaseAddr": 0},
    ),
    "CoreRandom": DiffEntry(
        spinal="CoreRandom",
        portmap="Core",
        factory="avoodoo.core.core:Core",
        note="Core under the memory slaves and the HDMI clock only, io_cpuBus from the "
             "bare generator: random 24-bit addresses, so mostly texture writes and "
             "undecoded register writes, with the odd garbage triangle",
        responder="tests.diff.core_stimulus:core_random",
    ),
    "CoreSim": DiffEntry(
        spinal="CoreSim",
        factory="avoodoo.core_sim:CoreSim",
        params=dict(async_reset=False),
        note="CoreSimGen with no flags — CoreSim(Config.voodoo1().copy(enableHdmiScanout "
             "= false), SimMemoryTiming()); GenOne emits it SYNC-reset, hence "
             "async_reset=False here (ext/SpinalVoodoo/src/voodoo/CoreSim.scala -> "
             "avoodoo/core_sim.py)",
        # The Amaranth port list IS the Spinal one (core_sim_ports.py), so the
        # mechanical camel_to_snake rule would look for cpu_bus__cmd__valid
        # where the port is literally io_cpuBus_cmd_valid: identity overrides.
        overrides=_core_sim_overrides(),
        pinned={"dbg_swap_count_we": 0, "dbg_swap_count_set": 0},
        pending=(
            "not lockstep-diffable at this size: the 4 MiB + 8 MiB SimBmbMemory "
            "arrays (see the SimBmbMemory row) make the Spinal side four 1 Mi-entry "
            "and four 2 Mi-entry byte arrays and the Amaranth side, through "
            "gen_module's plain write_verilog, 3.1 M `initial` lines. The wrapper "
            "also has no way to hold the Amaranth-only dbg_* inputs. Core is the "
            "diffed row; CoreSim's own wiring is covered by tests/test_core.py "
            "(pysim smoke) and the Glide/trace parity gate (plan 7.3)."
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
