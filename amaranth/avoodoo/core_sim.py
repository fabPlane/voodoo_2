"""**STUB** — Phase 0.5 placeholder for the Amaranth ``CoreSim``.

Scala counterpart: ``ext/SpinalVoodoo/src/voodoo/CoreSim.scala``.

This is *not* the Voodoo core.  It exists so Phase 0.5 can prove the copied
Verilator/Glide harness in ``amaranth/emu/sim/`` compiles, links and starts
against an Amaranth top with the right port list and the right memory
backdoor.  Phase 7.2 replaces the body wholesale; whoever does that should
keep:

* the signature, which lives in :mod:`avoodoo.core_sim_ports` — import it,
  do not retype it;
* the two simulation RAM submodule names (``fbWriteRam``, ``texRam``) and the
  ``mem`` array inside each, because the C harness addresses them by the
  Verilator path those names produce;
* the ``dbg_*`` port semantics described in :mod:`avoodoo.core_sim_ports`.

What the stub actually does:

* accepts every ``io_cpuBus`` command in one cycle and answers with a
  single-beat response on the next;
* reads of register offset 0 (the SST-1 status register) return ``0x3F`` —
  "FIFO empty, not busy, no swaps pending" — so ``sim_idle_wait()`` in the
  harness terminates instead of spinning for five million cycles.  Every
  other register read returns 0;
* reads and writes in ``0x400000..0x7FFFFF`` (LFB) and ``0x800000..0xFFFFFF``
  (texture) go straight to the corresponding simulation RAM, byte-masked.
  That is enough to keep the two memory arrays alive through yosys and to
  give ``sim_read_fb`` something to observe.

Reset is **asynchronous, active high** by default, matching the upstream sim
build the golden screenshots came from (``CoreSim.json`` records
``reset_kind: async``; see ``docs/amaranth/spinal-naming-and-harness.md`` §0).
"""

from __future__ import annotations

from amaranth import ClockDomain, ClockSignal, Cat, Module, Mux, ResetSignal, Signal, unsigned
from amaranth.lib import memory, wiring

from avoodoo.core_sim_ports import (
    FB_RAM_INSTANCE,
    FB_WORDS,
    RAM_ARRAY_NAME,
    TEX_RAM_INSTANCE,
    TEX_WORDS,
    core_sim_signature,
)

__all__ = ["CoreSim", "SimRamStub"]

_BMB_OPCODE_WRITE = 1
_STATUS_IDLE = 0x3F


class SimRamStub(wiring.Component):
    """Byte-enabled synchronous RAM holding the array the C harness pokes.

    The only thing that matters structurally is that the ``Memory`` is added
    as ``m.submodules.<RAM_ARRAY_NAME>``: yosys emits it as
    ``reg [31:0] mem [depth-1:0]`` inside this module, and Verilator's
    ``--public-flat-rw`` then exposes it as
    ``CoreSim__DOT__<instance>__DOT__mem``.

    Phase 7.2 will replace this with the real ``SimBmbMemory`` port (BMB
    interface plus the ``SimMemoryTiming`` latency model).  Keep the Memory
    submodule name.
    """

    def __init__(self, depth: int):
        self._depth = depth
        addr_width = (depth - 1).bit_length()
        super().__init__(
            wiring.Signature(
                {
                    "addr": wiring.In(addr_width),
                    "wdata": wiring.In(32),
                    "wmask": wiring.In(4),
                    "we": wiring.In(1),
                    "rdata": wiring.Out(32),
                }
            )
        )

    @property
    def depth(self) -> int:
        return self._depth

    def elaborate(self, platform):
        m = Module()
        mem = memory.Memory(shape=unsigned(32), depth=self._depth, init=[])
        setattr(m.submodules, RAM_ARRAY_NAME, mem)
        wp = mem.write_port(granularity=8)
        rp = mem.read_port()
        m.d.comb += [
            wp.addr.eq(self.addr),
            wp.data.eq(self.wdata),
            wp.en.eq(Cat(*(self.we & self.wmask[i] for i in range(4)))),
            rp.addr.eq(self.addr),
            self.rdata.eq(rp.data),
        ]
        return m


class CoreSim(wiring.Component):
    """Phase 0.5 stub with the Spinal ``CoreSim`` port list plus ``dbg``."""

    def __init__(self, *, async_reset: bool = True):
        self._async_reset = async_reset
        super().__init__(core_sim_signature())

    def elaborate(self, platform):
        m = Module()

        # The harness drives `clk`/`reset` as ordinary ports, so build the
        # `sync` domain by hand rather than letting Amaranth append its own
        # `clk`/`rst` pair (which would collide on the first name and use the
        # wrong spelling for the second).
        m.domains += ClockDomain("sync", async_reset=self._async_reset)
        m.d.comb += ClockSignal("sync").eq(self.clk)
        m.d.comb += ResetSignal("sync").eq(self.reset)

        fb = SimRamStub(FB_WORDS)
        tex = SimRamStub(TEX_WORDS)
        setattr(m.submodules, FB_RAM_INSTANCE, fb)
        setattr(m.submodules, TEX_RAM_INSTANCE, tex)

        addr = self.io_cpuBus_cmd_payload_fragment_address
        is_write = self.io_cpuBus_cmd_payload_fragment_opcode == _BMB_OPCODE_WRITE
        is_tex = addr[23]
        is_lfb = ~addr[23] & addr[22]

        rsp_valid = Signal()
        rsp_source = Signal(4)
        rsp_status = Signal()  # last accepted read hit register offset 0
        rsp_from_tex = Signal()
        rsp_from_ram = Signal()

        cmd_ready = ~rsp_valid | self.io_cpuBus_rsp_ready
        fire = self.io_cpuBus_cmd_valid & cmd_ready

        m.d.comb += [
            self.io_cpuBus_cmd_ready.eq(cmd_ready),
            self.io_cpuBus_rsp_valid.eq(rsp_valid),
            self.io_cpuBus_rsp_payload_last.eq(1),
            self.io_cpuBus_rsp_payload_fragment_source.eq(rsp_source),
            self.io_cpuBus_rsp_payload_fragment_opcode.eq(0),
            self.io_cpuBus_rsp_payload_fragment_data.eq(
                Mux(
                    rsp_from_ram,
                    Mux(rsp_from_tex, tex.rdata, fb.rdata),
                    Mux(rsp_status, _STATUS_IDLE, 0),
                )
            ),
        ]

        # Both RAMs see the command address every cycle; the read port is
        # synchronous, so its data lands exactly when rsp_valid comes up.
        m.d.comb += [
            fb.addr.eq(addr[2:22]),
            fb.wdata.eq(self.io_cpuBus_cmd_payload_fragment_data),
            fb.wmask.eq(self.io_cpuBus_cmd_payload_fragment_mask),
            fb.we.eq(fire & is_write & is_lfb),
            tex.addr.eq(addr[2:23]),
            tex.wdata.eq(self.io_cpuBus_cmd_payload_fragment_data),
            tex.wmask.eq(self.io_cpuBus_cmd_payload_fragment_mask),
            tex.we.eq(fire & is_write & is_tex),
        ]

        with m.If(rsp_valid & self.io_cpuBus_rsp_ready):
            m.d.sync += rsp_valid.eq(0)
        with m.If(fire):
            m.d.sync += [
                rsp_valid.eq(1),
                rsp_source.eq(self.io_cpuBus_cmd_payload_fragment_source),
                rsp_status.eq(~is_write & (addr[2:24] == 0)),
                rsp_from_ram.eq(~is_write & (is_tex | is_lfb)),
                rsp_from_tex.eq(is_tex),
            ]

        # --- dbg bundle -------------------------------------------------
        swap_count = Signal(2, init=0)
        with m.If(self.dbg_swap_count_we):
            m.d.sync += swap_count.eq(self.dbg_swap_count_set)

        m.d.comb += [
            self.dbg_swap_count.eq(swap_count),
            self.dbg_pci_cmd_ready.eq(cmd_ready),
            self.dbg_frontdoor_cmd_ready.eq(cmd_ready),
            self.dbg_pci_fifo_free.eq(_STATUS_IDLE),
            self.dbg_sync_drained.eq(1),
            self.dbg_pipeline_busy.eq(0),
        ]

        # Sim-only inputs the stub does not model yet; read them so Amaranth
        # does not warn about unused signature members.
        m.d.comb += []
        _ = self.io_vRetrace, self.io_flushFbCaches, self.io_cpuBus_cmd_payload_last
        _ = self.io_cpuBus_cmd_payload_fragment_length

        return m
