#
# SpinalVoodoo on ChromatiX (ModRetro Chromatic / GW5A-25).
#
# SPDX-License-Identifier: BSD-2-Clause

"""Instance wrapper for ModRetroChromatic.v plus Wishbone/AXI helpers."""

import os

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.fsm import FSM, NextState, NextValue

from litex.gen import *
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import *


RTL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "rtl", "chromatix"))


def add_voodoo_sources(platform):
    path = os.path.join(RTL_DIR, "ModRetroChromatic.v")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} missing — run `make -C fpga rtl-chromatix` first "
            "(scala-cli + ext/SpinalVoodoo submodule)."
        )
    for name in sorted(os.listdir(RTL_DIR)):
        if name.endswith(".v"):
            platform.add_source(os.path.join(RTL_DIR, name))
    # Spinal ROM sidecars sit next to the Verilog; Gowin/Yosys pick them up by name.
    return path


class VoodooCoreCSR(LiteXModule):
    def __init__(self, host_wedged, heartbeat):
        self.status = CSRStatus(2, description="bit0=host_wedged, bit1=heartbeat")
        self.specials += MultiReg(Cat(host_wedged, heartbeat), self.status.status)


class Axi4MemStub(LiteXModule):
    """Single-outstanding AXI4 slave that ACKs writes and returns zero on reads.

    Used so `--with-voodoo` can elaborate / `--no-compile` before the PSRAM
    arbiter port is wired. Replace with a Wishbone→PSRAM bridge for hardware.
    """

    def __init__(self, aw_valid, aw_ready, aw_addr, aw_len,
                 w_valid, w_ready, w_data, w_strb, w_last,
                 b_valid, b_ready, b_resp,
                 ar_valid, ar_ready, ar_addr, ar_len,
                 r_valid, r_ready, r_data, r_resp, r_last):
        beat = Signal(8)
        self.comb += [
            aw_ready.eq(0), w_ready.eq(0), b_valid.eq(0), b_resp.eq(0),
            ar_ready.eq(0), r_valid.eq(0), r_data.eq(0), r_resp.eq(0), r_last.eq(0),
        ]
        self.fsm = fsm = FSM(reset_state="IDLE")
        self.submodules += fsm
        fsm.act("IDLE",
            If(aw_valid,
                aw_ready.eq(1),
                NextValue(beat, aw_len),
                NextState("WDATA"),
            ).Elif(ar_valid,
                ar_ready.eq(1),
                NextValue(beat, ar_len),
                NextState("RDATA"),
            )
        )
        fsm.act("WDATA",
            w_ready.eq(1),
            If(w_valid,
                If(w_last | (beat == 0),
                    NextState("B"),
                ).Else(
                    NextValue(beat, beat - 1),
                )
            )
        )
        fsm.act("B",
            b_valid.eq(1),
            If(b_ready, NextState("IDLE")),
        )
        fsm.act("RDATA",
            r_valid.eq(1),
            r_last.eq(beat == 0),
            If(r_ready,
                If(beat == 0,
                    NextState("IDLE"),
                ).Else(
                    NextValue(beat, beat - 1),
                )
            )
        )


class WishboneToAxiLite(LiteXModule):
    """One-outstanding Wishbone slave → AXI4-Lite master (Spinal payload_ naming)."""

    def __init__(self, wb, addr_width=24):
        self.aw_valid = Signal()
        self.aw_ready = Signal()
        self.aw_addr = Signal(addr_width)
        self.aw_prot = Signal(3)
        self.w_valid = Signal()
        self.w_ready = Signal()
        self.w_data = Signal(32)
        self.w_strb = Signal(4)
        self.b_valid = Signal()
        self.b_ready = Signal()
        self.b_resp = Signal(2)
        self.ar_valid = Signal()
        self.ar_ready = Signal()
        self.ar_addr = Signal(addr_width)
        self.ar_prot = Signal(3)
        self.r_valid = Signal()
        self.r_ready = Signal()
        self.r_data = Signal(32)
        self.r_resp = Signal(2)

        self.comb += [
            self.aw_valid.eq(0), self.w_valid.eq(0), self.b_ready.eq(0),
            self.ar_valid.eq(0), self.r_ready.eq(0),
            self.aw_prot.eq(0), self.ar_prot.eq(0),
            self.aw_addr.eq(Cat(Constant(0, 2), wb.adr)[:addr_width]),
            self.ar_addr.eq(Cat(Constant(0, 2), wb.adr)[:addr_width]),
            self.w_data.eq(wb.dat_w),
            self.w_strb.eq(wb.sel),
            wb.dat_r.eq(self.r_data),
        ]
        self.fsm = fsm = FSM(reset_state="IDLE")
        self.submodules += fsm
        fsm.act("IDLE",
            If(wb.cyc & wb.stb,
                If(wb.we, NextState("WR")).Else(NextState("AR"))
            )
        )
        fsm.act("WR",
            self.aw_valid.eq(1),
            self.w_valid.eq(1),
            If(self.aw_ready & self.w_ready, NextState("B"))
            .Elif(self.aw_ready, NextState("W"))
            .Elif(self.w_ready, NextState("AW"))
        )
        fsm.act("AW", self.aw_valid.eq(1), If(self.aw_ready, NextState("B")))
        fsm.act("W",  self.w_valid.eq(1),  If(self.w_ready,  NextState("B")))
        fsm.act("B",
            self.b_ready.eq(1),
            If(self.b_valid, wb.ack.eq(1), NextState("IDLE"))
        )
        fsm.act("AR",
            self.ar_valid.eq(1),
            If(self.ar_ready, NextState("R"))
        )
        fsm.act("R",
            self.r_ready.eq(1),
            If(self.r_valid, wb.ack.eq(1), NextState("IDLE"))
        )


class ModRetroChromatic(LiteXModule):
    """LiteX wrapper around SpinalHDL ModRetroChromatic."""

    def __init__(self, platform, buttons, menu, hdmi_clk, hdmi_reset):
        self.bus = wishbone.Interface(data_width=32, address_width=30, addressing="word")

        self.gb_clkena = Signal()
        self.gb_data = Signal(15)
        self.gb_mode = Signal(2)
        self.gb_on = Signal()
        self.gb_vsync = Signal()
        self.host_wedged = Signal()
        self.heartbeat = Signal()

        add_voodoo_sources(platform)

        self.submodules.bar = bar = WishboneToAxiLite(self.bus, addr_width=24)

        # Framebuffer / texture AXI stubs (replace with PSRAM bridges for hardware).
        fb = _AxiMasterPins()
        tex = _AxiMasterPins()
        self.submodules.fb_stub = Axi4MemStub(
            fb.aw_valid, fb.aw_ready, fb.aw_addr, fb.aw_len,
            fb.w_valid, fb.w_ready, fb.w_data, fb.w_strb, fb.w_last,
            fb.b_valid, fb.b_ready, fb.b_resp,
            fb.ar_valid, fb.ar_ready, fb.ar_addr, fb.ar_len,
            fb.r_valid, fb.r_ready, fb.r_data, fb.r_resp, fb.r_last,
        )
        self.submodules.tex_stub = Axi4MemStub(
            tex.aw_valid, tex.aw_ready, tex.aw_addr, tex.aw_len,
            tex.w_valid, tex.w_ready, tex.w_data, tex.w_strb, tex.w_last,
            tex.b_valid, tex.b_ready, tex.b_resp,
            tex.ar_valid, tex.ar_ready, tex.ar_addr, tex.ar_len,
            tex.r_valid, tex.r_ready, tex.r_data, tex.r_resp, tex.r_last,
        )

        self.specials += Instance("ModRetroChromatic",
            i_clk   = ClockSignal("sys"),
            i_reset = ResetSignal("sys"),

            i_hdmiClk   = hdmi_clk,
            i_hdmiReset = hdmi_reset,

            # AXI4-Lite BAR (Spinal payload_ naming).
            i_bar_aw_valid        = bar.aw_valid,
            o_bar_aw_ready        = bar.aw_ready,
            i_bar_aw_payload_addr = bar.aw_addr,
            i_bar_aw_payload_prot = bar.aw_prot,
            i_bar_w_valid         = bar.w_valid,
            o_bar_w_ready         = bar.w_ready,
            i_bar_w_payload_data  = bar.w_data,
            i_bar_w_payload_strb  = bar.w_strb,
            o_bar_b_valid         = bar.b_valid,
            i_bar_b_ready         = bar.b_ready,
            o_bar_b_payload_resp  = bar.b_resp,
            i_bar_ar_valid        = bar.ar_valid,
            o_bar_ar_ready        = bar.ar_ready,
            i_bar_ar_payload_addr = bar.ar_addr,
            i_bar_ar_payload_prot = bar.ar_prot,
            o_bar_r_valid         = bar.r_valid,
            i_bar_r_ready         = bar.r_ready,
            o_bar_r_payload_data  = bar.r_data,
            o_bar_r_payload_resp  = bar.r_resp,

            # Framebuffer AXI4 master → stub.
            o_memFb_aw_valid         = fb.aw_valid,
            i_memFb_aw_ready         = fb.aw_ready,
            o_memFb_aw_payload_addr  = fb.aw_addr,
            o_memFb_aw_payload_len   = fb.aw_len,
            o_memFb_aw_payload_size  = fb.aw_size,
            o_memFb_aw_payload_burst = fb.aw_burst,
            o_memFb_aw_payload_cache = fb.aw_cache,
            o_memFb_aw_payload_prot  = fb.aw_prot,
            o_memFb_w_valid          = fb.w_valid,
            i_memFb_w_ready          = fb.w_ready,
            o_memFb_w_payload_data   = fb.w_data,
            o_memFb_w_payload_strb   = fb.w_strb,
            o_memFb_w_payload_last   = fb.w_last,
            i_memFb_b_valid          = fb.b_valid,
            o_memFb_b_ready          = fb.b_ready,
            i_memFb_b_payload_resp   = fb.b_resp,
            o_memFb_ar_valid         = fb.ar_valid,
            i_memFb_ar_ready         = fb.ar_ready,
            o_memFb_ar_payload_addr  = fb.ar_addr,
            o_memFb_ar_payload_len   = fb.ar_len,
            o_memFb_ar_payload_size  = fb.ar_size,
            o_memFb_ar_payload_burst = fb.ar_burst,
            o_memFb_ar_payload_cache = fb.ar_cache,
            o_memFb_ar_payload_prot  = fb.ar_prot,
            i_memFb_r_valid          = fb.r_valid,
            o_memFb_r_ready          = fb.r_ready,
            i_memFb_r_payload_data   = fb.r_data,
            i_memFb_r_payload_resp   = fb.r_resp,
            i_memFb_r_payload_last   = fb.r_last,

            o_memTex_aw_valid         = tex.aw_valid,
            i_memTex_aw_ready         = tex.aw_ready,
            o_memTex_aw_payload_addr  = tex.aw_addr,
            o_memTex_aw_payload_len   = tex.aw_len,
            o_memTex_aw_payload_size  = tex.aw_size,
            o_memTex_aw_payload_burst = tex.aw_burst,
            o_memTex_aw_payload_cache = tex.aw_cache,
            o_memTex_aw_payload_prot  = tex.aw_prot,
            o_memTex_w_valid          = tex.w_valid,
            i_memTex_w_ready          = tex.w_ready,
            o_memTex_w_payload_data   = tex.w_data,
            o_memTex_w_payload_strb   = tex.w_strb,
            o_memTex_w_payload_last   = tex.w_last,
            i_memTex_b_valid          = tex.b_valid,
            o_memTex_b_ready          = tex.b_ready,
            i_memTex_b_payload_resp   = tex.b_resp,
            o_memTex_ar_valid         = tex.ar_valid,
            i_memTex_ar_ready         = tex.ar_ready,
            o_memTex_ar_payload_addr  = tex.ar_addr,
            o_memTex_ar_payload_len   = tex.ar_len,
            o_memTex_ar_payload_size  = tex.ar_size,
            o_memTex_ar_payload_burst = tex.ar_burst,
            o_memTex_ar_payload_cache = tex.ar_cache,
            o_memTex_ar_payload_prot  = tex.ar_prot,
            i_memTex_r_valid          = tex.r_valid,
            o_memTex_r_ready          = tex.r_ready,
            i_memTex_r_payload_data   = tex.r_data,
            i_memTex_r_payload_resp   = tex.r_resp,
            i_memTex_r_payload_last   = tex.r_last,

            o_gbClkena = self.gb_clkena,
            o_gbData   = self.gb_data,
            o_gbMode   = self.gb_mode,
            o_gbOn     = self.gb_on,
            o_gbVsync  = self.gb_vsync,

            i_btnA      = buttons.a,
            i_btnB      = buttons.b,
            i_btnUp     = buttons.dpad_up,
            i_btnDown   = buttons.dpad_down,
            i_btnLeft   = buttons.dpad_left,
            i_btnRight  = buttons.dpad_right,
            i_btnStart  = buttons.start,
            i_btnSelect = buttons.sel,
            i_btnMenu   = menu,

            o_hostWedged = self.host_wedged,
            o_heartbeat  = self.heartbeat,
        )


class _AxiMasterPins:
    def __init__(self, addr_width=26):
        self.aw_valid = Signal()
        self.aw_ready = Signal()
        self.aw_addr = Signal(addr_width)
        self.aw_len = Signal(8)
        self.aw_size = Signal(3)
        self.aw_burst = Signal(2)
        self.aw_cache = Signal(4)
        self.aw_prot = Signal(3)
        self.w_valid = Signal()
        self.w_ready = Signal()
        self.w_data = Signal(32)
        self.w_strb = Signal(4)
        self.w_last = Signal()
        self.b_valid = Signal()
        self.b_ready = Signal()
        self.b_resp = Signal(2)
        self.ar_valid = Signal()
        self.ar_ready = Signal()
        self.ar_addr = Signal(addr_width)
        self.ar_len = Signal(8)
        self.ar_size = Signal(3)
        self.ar_burst = Signal(2)
        self.ar_cache = Signal(4)
        self.ar_prot = Signal(3)
        self.r_valid = Signal()
        self.r_ready = Signal()
        self.r_data = Signal(32)
        self.r_resp = Signal(2)
        self.r_last = Signal()
