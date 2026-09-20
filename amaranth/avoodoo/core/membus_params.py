"""Every ``BmbParameter`` the design declares, in one place.

These are the companion-object values of the Scala files named against each
function.  They live here rather than inside the ported modules because the
same parameter set is read by three or four unrelated places -- the module
that owns it, the subsystem that arbitrates it, the adapter chain that
bridges it, and the differential harness -- exactly as ``Core.scala``'s
companion object is read across the Spinal tree.

Nothing here elaborates anything; it is pure parameter arithmetic, so it is
cheap to import and safe to call at registry-definition time.

Ownership note: when Phase 7.2 ports ``Core.scala`` and Phase 2 ports
``RegisterBank.scala``, those modules should import from here instead of
re-declaring, and the ``Config``-dependent ones keep taking ``c``.
"""

from __future__ import annotations

from avoodoo.config import Config
from avoodoo.lib.membus import BurstAlignment, MemBusParameter, log2_up

__all__ = [
    "cpu_bmb_params",
    "fb_mem_bmb_params",
    "tex_mem_bmb_params",
    "cpu_tex_bmb_params",
    "register_bank_params",
    "register_bank_external_params",
    "plane_buffer_params",
    "lfb_fb_read_params",
    "lfb_params",
    "plane_reader_need_burst_splitter",
    "plane_reader_internal_params",
    "plane_reader_splitter_params",
    "plane_reader_params",
    "AXI_BURST_LOG2",
]

#: ``CoreArtix.axiBurstLog2`` (CoreArtix.scala:240); ``CoreGowin`` reuses it.
AXI_BURST_LOG2 = 8


def cpu_bmb_params() -> MemBusParameter:
    """``Core.cpuBmbParams`` (Core.scala:17) -- the 16 MiB PCI BAR window."""
    return MemBusParameter.simple(address_width=24, data_width=32, source_width=4,
                                  context_width=0, length_width=2,
                                  alignment=BurstAlignment.WORD)


def fb_mem_bmb_params(c: Config) -> MemBusParameter:
    """``Core.fbMemBmbParams(c)`` (Core.scala:28).

    ``sourceWidth = 1 + log2Up(5)``: one bit for the master's own tag plus
    three route bits for the five framebuffer ports.
    """
    return MemBusParameter.simple(address_width=c.address_width, data_width=32,
                                  source_width=1 + log2_up(5), context_width=0,
                                  length_width=c.mem_burst_length_width,
                                  alignment=BurstAlignment.BYTE)


def tex_mem_bmb_params(c: Config) -> MemBusParameter:
    """``Core.texMemBmbParams(c)`` (Core.scala:39) -- ``4 + log2Up(3)``."""
    return MemBusParameter.simple(address_width=c.address_width, data_width=32,
                                  source_width=4 + log2_up(3), context_width=0,
                                  length_width=c.mem_burst_length_width,
                                  alignment=BurstAlignment.BYTE)


def cpu_tex_bmb_params() -> MemBusParameter:
    """``Core.cpuTexBmbParams`` (Core.scala:51)."""
    return MemBusParameter.simple(address_width=26, data_width=32, source_width=4,
                                  context_width=0, length_width=6,
                                  alignment=BurstAlignment.BYTE)


def register_bank_params(c: Config | None = None) -> MemBusParameter:
    """``RegisterBank.bmbParams`` (RegisterBank.scala:1258) -- 4 KiB window."""
    return MemBusParameter.simple(address_width=12, data_width=32, source_width=4,
                                  context_width=0, length_width=2,
                                  alignment=BurstAlignment.WORD)


def register_bank_external_params(c: Config | None = None) -> MemBusParameter:
    """``RegisterBank.externalBmbParams`` (RegisterBank.scala:1270).

    22 bits so address bit 21 (the "remapped registers" flag the
    ``AddressRemapper`` decodes) is visible.
    """
    return MemBusParameter.simple(address_width=22, data_width=32, source_width=4,
                                  context_width=0, length_width=2,
                                  alignment=BurstAlignment.WORD)


def plane_buffer_params(c: Config) -> MemBusParameter:
    """``FramebufferPlaneBuffer.bmbParams`` (FramebufferPlaneBuffer.scala:567)."""
    return MemBusParameter.simple(address_width=c.address_width, data_width=32,
                                  source_width=1, context_width=0,
                                  length_width=c.mem_burst_length_width,
                                  alignment=BurstAlignment.BYTE)


def lfb_fb_read_params(c: Config) -> MemBusParameter:
    """``Lfb.fbReadBmbParams`` (Lfb.scala:474)."""
    return MemBusParameter.simple(address_width=c.address_width, data_width=32,
                                  source_width=1, context_width=0,
                                  length_width=c.mem_burst_length_width,
                                  alignment=BurstAlignment.BYTE)


def lfb_params(c: Config | None = None) -> MemBusParameter:
    """``Lfb.bmbParams`` (Lfb.scala:485)."""
    return MemBusParameter.simple(address_width=22, data_width=32, source_width=4,
                                  context_width=0, length_width=2,
                                  alignment=BurstAlignment.WORD)


def plane_reader_need_burst_splitter(c: Config) -> bool:
    """``FramebufferPlaneReader.needBurstSplitter`` (…Reader.scala:431).

    **False for ``Config.voodoo1()``** (``((800+1)/2)*4 = 1600 <= 2048``), so
    the shipping core builds neither ``BmbAlignedSpliter`` nor
    ``BmbContextRemover``.
    """
    return ((c.max_fb_dims[0] + 1) // 2) * 4 > (1 << c.mem_burst_length_width)


def plane_reader_internal_params(c: Config) -> MemBusParameter:
    """``FramebufferPlaneReader.internalBmbParams`` (…Reader.scala:436)."""
    if plane_reader_need_burst_splitter(c):
        length_width = max(c.mem_burst_length_width + 1,
                           log2_up(((c.max_fb_dims[0] + 1) // 2) * 4))
    else:
        length_width = c.mem_burst_length_width
    return MemBusParameter.simple(address_width=c.address_width, data_width=32,
                                  source_width=1, context_width=0,
                                  length_width=length_width,
                                  alignment=BurstAlignment.BYTE)


def plane_reader_splitter_params(c: Config) -> MemBusParameter:
    """``FramebufferPlaneReader.splitterBmbParams`` (…Reader.scala:451)."""
    from avoodoo.lib.membus import AlignedSplitter
    return MemBusParameter(AlignedSplitter.output_access_parameter(
        plane_reader_internal_params(c).access, 1 << c.mem_burst_length_width))


def plane_reader_params(c: Config) -> MemBusParameter:
    """``FramebufferPlaneReader.bmbParams`` (…Reader.scala:456)."""
    from avoodoo.lib.membus import ContextRemover
    if plane_reader_need_burst_splitter(c):
        return ContextRemover.output_parameter(plane_reader_splitter_params(c))
    return plane_reader_internal_params(c)
