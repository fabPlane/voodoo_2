"""Top-level port contract for the Amaranth ``CoreSim``.

Scala counterpart: ``ext/SpinalVoodoo/src/voodoo/CoreSim.scala``.

Two groups of ports live here:

1. **The Spinal port list**, reproduced name-for-name and width-for-width from
   ``fpga/src/voodoo/diff/portmaps/CoreSim.json`` (19 ports: 17 ``io_*`` plus
   ``clk``/``reset``).  Keeping these identical is what lets the Verilator
   Glide harness drive an Amaranth core with the same C++ it uses for the
   Spinal one.  ``tests/test_core_sim_ports.py`` re-checks the table against
   that JSON, so a Spinal-side change cannot drift away unnoticed.

2. **The ``dbg`` bundle** — the replacement for the hierarchical
   ``top->rootp->CoreSim__DOT__…`` taps the upstream harness uses.  See
   ``docs/amaranth/spinal-naming-and-harness.md`` §2.1 for the inventory of
   live taps and ``amaranth/emu/sim/harness/README.md`` for the C-side
   contract.  Only the *scalars* are ports; the two multi-megabyte simulation
   RAMs stay memory arrays and are reached through Verilator's
   ``--public-flat-rw`` (see ``FB_MEM_PATH`` / ``TEX_MEM_PATH`` below).

This module deliberately contains no behaviour.  ``avoodoo/core_sim.py`` is a
Phase 0.5 stub that will be thrown away in Phase 7.2; the real implementation
imports the signature from here so the port contract survives the rewrite.
"""

from __future__ import annotations

from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = [
    "SPINAL_IO_PORTS",
    "DBG_PORTS",
    "CLOCK_PORTS",
    "core_sim_signature",
    "FB_BYTES",
    "TEX_BYTES",
    "FB_WORDS",
    "TEX_WORDS",
    "FB_RAM_INSTANCE",
    "TEX_RAM_INSTANCE",
    "RAM_ARRAY_NAME",
    "FB_MEM_PATH",
    "TEX_MEM_PATH",
]


# --------------------------------------------------------------------------
# 1. The Spinal port list
# --------------------------------------------------------------------------

#: ``(name, width, flow)`` for every non-clock port of Spinal ``CoreSim``.
#: Flow is from the point of view of the component, i.e. ``In`` == Verilog
#: ``input``.  Order follows ``CoreSim.json``, which follows the Scala
#: declaration order; Verilator binds by name, so the order is documentation
#: only.
SPINAL_IO_PORTS: tuple[tuple[str, int, object], ...] = (
    ("io_cpuBus_cmd_valid", 1, In),
    ("io_cpuBus_cmd_ready", 1, Out),
    ("io_cpuBus_cmd_payload_last", 1, In),
    ("io_cpuBus_cmd_payload_fragment_source", 4, In),
    ("io_cpuBus_cmd_payload_fragment_opcode", 1, In),
    ("io_cpuBus_cmd_payload_fragment_address", 24, In),
    ("io_cpuBus_cmd_payload_fragment_length", 2, In),
    ("io_cpuBus_cmd_payload_fragment_data", 32, In),
    ("io_cpuBus_cmd_payload_fragment_mask", 4, In),
    ("io_cpuBus_rsp_valid", 1, Out),
    ("io_cpuBus_rsp_ready", 1, In),
    ("io_cpuBus_rsp_payload_last", 1, Out),
    ("io_cpuBus_rsp_payload_fragment_source", 4, Out),
    ("io_cpuBus_rsp_payload_fragment_opcode", 1, Out),
    ("io_cpuBus_rsp_payload_fragment_data", 32, Out),
    ("io_vRetrace", 1, In),
    ("io_flushFbCaches", 1, In),
)

#: Clock and reset.  Spinal never renames these (doc §0), and the harness
#: drives ``top->clk`` / ``top->reset`` directly.  They are ordinary component
#: members here because the Amaranth ``sync`` domain would otherwise emit
#: ``clk``/``rst`` itself and collide on the first name while getting the
#: second one wrong.  ``core_sim.py`` creates the domain and ties it to these.
CLOCK_PORTS: tuple[tuple[str, int, object], ...] = (
    ("clk", 1, In),
    ("reset", 1, In),
)


# --------------------------------------------------------------------------
# 2. The dbg bundle
# --------------------------------------------------------------------------

# Names are flat (`dbg_pci_cmd_ready`, not a nested `dbg` signature member),
# because Amaranth joins nested signature paths with a *double* underscore:
# a member `dbg` containing `pci_cmd_ready` emits `dbg__pci_cmd_ready`.  Flat
# members give the single-underscore spelling the harness and the naming doc
# both assume.
#
# Each entry replaces one live hierarchical tap in the upstream harness:
#
#   dbg_pci_cmd_ready        core_1__DOT__pciFifo_1_io_cpuSide_cmd_ready
#   dbg_frontdoor_cmd_ready  core_1__DOT__frontdoor_io_cpuBus_cmd_ready
#   dbg_pci_fifo_free        core_1__DOT__pciFifo_1_io_pciFifoFree   (UInt 7)
#   dbg_sync_drained         core_1__DOT__pciFifo_1_io_syncDrained
#   dbg_pipeline_busy        core_1__DOT__pixelPipeline_1_io_debug_pipelineBusy
#
# `swapCountReg` is the only internal the upstream harness *writes*, so it gets
# a load path rather than a bare observation port: assert `dbg_swap_count_we`
# with `dbg_swap_count_set` for one clock and the register takes the value.
# `dbg_swap_count` reads it back combinationally.
DBG_PORTS: tuple[tuple[str, int, object], ...] = (
    ("dbg_pci_cmd_ready", 1, Out),
    ("dbg_frontdoor_cmd_ready", 1, Out),
    ("dbg_pci_fifo_free", 7, Out),
    ("dbg_sync_drained", 1, Out),
    ("dbg_pipeline_busy", 1, Out),
    ("dbg_swap_count", 2, Out),
    ("dbg_swap_count_set", 2, In),
    ("dbg_swap_count_we", 1, In),
)


def core_sim_signature() -> wiring.Signature:
    """The complete ``CoreSim`` signature: Spinal ports + clock + ``dbg``."""
    members = {}
    for name, width, flow in (*SPINAL_IO_PORTS, *CLOCK_PORTS, *DBG_PORTS):
        members[name] = flow(width)
    return wiring.Signature(members)


# --------------------------------------------------------------------------
# 3. The memory backdoor
# --------------------------------------------------------------------------

# Sizes come from CoreSim.scala: `simRam(..., 4 * 1024 * 1024)` for the
# framebuffer and `8 * 1024 * 1024` for the texture memory.  The harness's
# FB_WORD_COUNT / TEX_WORD_COUNT must agree with these.
FB_BYTES = 4 * 1024 * 1024
TEX_BYTES = 8 * 1024 * 1024
FB_WORDS = FB_BYTES // 4
TEX_WORDS = TEX_BYTES // 4

# Amaranth submodule names.  These are part of the harness contract: Verilator
# turns `m.submodules.fbWriteRam` plus the `m.submodules.<RAM_ARRAY_NAME>`
# Memory inside it into the C++ member spelled by FB_MEM_PATH below.  They are
# spelled the Spinal way (camelCase) on purpose, so the two harnesses differ
# only in the array shape, not in the path.
FB_RAM_INSTANCE = "fbWriteRam"
TEX_RAM_INSTANCE = "texRam"
RAM_ARRAY_NAME = "mem"

#: Verilator ``rootp`` member for each simulation RAM.  Unlike Spinal — which
#: splits a byte-enabled ``Mem`` into ``ram_symbol0..3`` — Amaranth/yosys emit
#: one ``reg [31:0] mem [N-1:0]``, so this is a single ``VlUnpacked<IData, N>``
#: and a bulk transfer is one index per word instead of four.
FB_MEM_PATH = f"CoreSim__DOT__{FB_RAM_INSTANCE}__DOT__{RAM_ARRAY_NAME}"
TEX_MEM_PATH = f"CoreSim__DOT__{TEX_RAM_INSTANCE}__DOT__{RAM_ARRAY_NAME}"
