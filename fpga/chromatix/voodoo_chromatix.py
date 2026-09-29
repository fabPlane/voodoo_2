#!/usr/bin/env python3
#
# SpinalVoodoo on ChromatiX — LiteX SoC entry point.
#
# SPDX-License-Identifier: BSD-2-Clause

"""
Build ChromatiX with SpinalVoodoo in place of the Game Boy core:

  make -C fpga rtl-chromatix
  ./fpga/chromatix/voodoo_chromatix.py --with-voodoo --build --no-compile

Requires LiteX + the chromatix package (`pip install -e ext/chromatix`). Gowin EDA
is only needed for a real bitstream (`--build` without `--no-compile`).
"""

import argparse
import os
import sys
from types import SimpleNamespace

# Python 3.12+ broke migen's bytecode name tracer (CALL/CACHE opcodes). Patch before
# importing LiteX CSR modules so SoCController / our CSRs can elaborate.
def _patch_migen_tracer_for_py312():
    import dis
    import inspect
    import migen.fhdl.tracer as tracer
    if getattr(tracer, "_voodoo_py312_patched", False):
        return
    _call = set(tracer._call_opcodes) | {
        "CALL", "CALL_KW", "CALL_FUNCTION", "CALL_FUNCTION_KW",
        "CALL_FUNCTION_EX", "CALL_METHOD",
    }
    def get_var_name(frame):
        code = frame.f_code
        call_index = frame.f_lasti
        instrs = list(dis.get_instructions(code))
        call_i = None
        for i, inst in enumerate(instrs):
            if inst.offset <= call_index and inst.opname in _call:
                call_i = i
            if inst.offset > call_index:
                break
        if call_i is None:
            return None
        for inst in instrs[call_i + 1:]:
            if inst.opname in ("STORE_NAME", "STORE_ATTR", "STORE_FAST", "STORE_DEREF"):
                return inst.argval
            if (inst.opname in tracer._load_build_opcodes
                    or inst.opname.startswith("LOAD_")
                    or inst.opname.startswith("CACHE")
                    or inst.opname in ("NOP", "EXTENDED_ARG", "PUSH_NULL", "COPY", "SWAP")):
                continue
            return None
        return None
    tracer.get_var_name = get_var_name
    tracer._voodoo_py312_patched = True

_patch_migen_tracer_for_py312()

# Repo paths: chromatix package + our gateware wrapper.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_CHROMATIX = os.path.join(_REPO, "ext", "chromatix")
sys.path.insert(0, _CHROMATIX)
sys.path.insert(0, _HERE)

from migen import *
from migen.genlib.io import CRG as _  # noqa: F401 — keep migen visible

from litex.gen import *
from litex.build.generic_platform import *
from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.soc_core import SoCMini
from litex.soc.integration.builder import Builder
from litex.soc.interconnect import wishbone

from chromatix_platform import Platform
from chromatix.gateware.crg import CRG
from chromatix.gateware.buttons import Buttons, BUTTONS
from chromatix.gateware.debug import DebugControl
from chromatix.gateware.misc import TickGenerator, StatusLed, ESP32Control
from chromatix.gateware.lcd import ST7785Init, load_st7785_sequence
from chromatix.gateware.video import VideoPipeline
from chromatix.gateware.memory import MemorySystem
from chromatix.gateware.usb_device import USBDevice

from gateware.voodoo_core import ModRetroChromatic, VoodooCoreCSR

# Keep sys on gClk (same as stock Game Boy mode) so CRG / video match ChromatiX.
GCLK_FREQ = int(33.55432e6 / 4)


class VoodooSoC(SoCMini):
    """ChromatiX fabric with SpinalVoodoo replacing the MiSTer Game Boy core."""

    def __init__(self, platform, with_debug_bridge=False, with_usb=False, uvc_frames=None):
        sys_clk_freq = GCLK_FREQ
        SoCMini.__init__(self, platform,
            clk_freq      = sys_clk_freq,
            ident         = "ChromatiX SoC (SpinalVoodoo)",
            ident_version = True,
            cpu_type      = None,
        )

        self.crg = crg = CRG(platform, sys_clk="gclk")

        clk_24   = platform.request("clk_24")
        clk_27   = platform.request("clk_27")
        buttons  = platform.request("buttons")
        rgb_led  = platform.request("rgb_led")
        power    = platform.request("power")
        esp32    = platform.request("esp32_ctrl")
        esp_uart = platform.request("esp32_uart0")
        qspi     = platform.request("qspi")
        ps       = platform.request("ps")
        cart     = platform.request("cart")
        lcd      = platform.request("lcd")
        i2s      = platform.request("i2s")
        ir       = platform.request("ir")
        link     = platform.request("link")
        usb      = platform.request("usb")
        hdmi     = platform.request("hdmi")
        sdio_ls  = platform.request("sdio_ls")
        _ = (clk_27, power)  # requested for constraints / future use
        self.comb += sdio_ls.eq(1)

        gb_lcd_clkena = Signal()
        gb_lcd_data   = Signal(15)
        gb_lcd_mode   = Signal(2)
        gb_lcd_on     = Signal()
        gb_lcd_vsync  = Signal()
        h_wr_burst_q  = Signal(16)
        h_wr_burst_q2 = Signal(16)
        h_gb_newline  = Signal()
        h_gb_address  = Signal(23)
        h_gb_write    = Signal()
        h_gb_data     = Signal(16)
        h_draw_osd    = Signal()
        lcd_init_done = Signal()
        lcd_enable_uvc = Signal()
        lcd_db_uvc    = Signal(18)
        left          = Signal(16)
        right         = Signal(16)
        menu_disabled = Signal(reset=1)
        low_battery   = Signal()
        system_control = Signal(16)
        debug_system  = Signal(32)

        self.ticks = ticks = ClockDomainsRenamer("gclk")(TickGenerator())
        memrst = Signal(reset=1)
        self.sync.xclk += memrst.eq(~crg.pll.locked)

        self.status_led = status_led = ClockDomainsRenamer("xclk")(StatusLed(rgb_led))
        self.comb += [
            status_led.reset.eq(~crg.pll.locked),
            status_led.blink.eq(ticks.counter[4]),
        ]

        self.esp32_ctrl = ClockDomainsRenamer("gclk")(ESP32Control(esp32, esp_uart))
        # ESP32Control also has a `phy` sync block (USB UART path). Without USBDevice
        # there is no phy domain — alias it to gclk so the design elaborates.
        self.cd_phy = ClockDomain("phy")
        self.comb += [
            self.cd_phy.clk.eq(ClockSignal("gclk")),
            self.cd_phy.rst.eq(0),
        ]
        self.buttons = btns_phy = ClockDomainsRenamer("gclk")(Buttons(buttons))
        self.debug_ctrl = debug_ctrl = DebugControl()
        btns = SimpleNamespace()
        for name in BUTTONS:
            btn = Signal(name=f"btn_{name}")
            self.comb += btn.eq(getattr(btns_phy, name) | getattr(debug_ctrl, name))
            setattr(btns, name, btn)
        btn_menu = Signal()
        self.comb += btn_menu.eq(buttons.menu & ~debug_ctrl.menu)

        self.st7785_init = st7785_init = ClockDomainsRenamer("pclk")(ST7785Init(load_st7785_sequence()))
        self.comb += [
            st7785_init.reset.eq(memrst),
            lcd.spi_csx.eq(st7785_init.lcd_cs),
            lcd.spi_sclk.eq(st7785_init.lcd_sck),
            lcd.spi_sda.eq(st7785_init.lcd_sda_sdi),
            lcd.reset.eq(st7785_init.lcd_rst),
            lcd_init_done.eq(st7785_init.lcd_init_done),
        ]

        self.video = video = VideoPipeline(lcd)
        self.comb += [
            video.gb_clkena.eq(gb_lcd_clkena),
            video.gb_data.eq(gb_lcd_data),
            video.gb_mode.eq(gb_lcd_mode),
            video.gb_on.eq(gb_lcd_on),
            video.gb_vsync.eq(gb_lcd_vsync),
            h_gb_newline.eq(video.fb_new_line),
            h_gb_address.eq(video.fb_address),
            h_gb_write.eq(video.fb_write),
            h_gb_data.eq(video.fb_data),
            video.fb_prev.eq(h_wr_burst_q),
            video.osd_data.eq(h_wr_burst_q2),
            h_draw_osd.eq(video.draw_osd),
            video.menu_disabled.eq(menu_disabled),
            video.lcd_init_done.eq(lcd_init_done),
            video.lcd_en.eq(1),
            video.frame_blend.eq(0),
            video.correct_lcd.eq(0),
            video.correct_uvc.eq(0),
            video.voltage_low.eq(low_battery),
            video.low_batt_mode.eq(0),
            video.show_timer.eq(0),
            video.run_timer.eq(0),
            video.reset_timer.eq(0),
            video.second.eq(ticks.second),
            video.percent.eq(ticks.percent),
            video.debug_system.eq(debug_system),
            video.debug_on.eq(0),
            lcd_enable_uvc.eq(video.uvc_en),
            lcd_db_uvc.eq(video.uvc_db),
        ]

        self.memory = memory = MemorySystem(qspi, ps, with_bus=False)
        self.comb += [
            memory.reset.eq(memrst),
            memory.gb_new_line.eq(h_gb_newline),
            memory.gb_address.eq(h_gb_address),
            memory.gb_write.eq(h_gb_write),
            memory.gb_data.eq(h_gb_data),
            memory.h_valid.eq(gb_lcd_clkena),
            memory.h_hsync.eq(gb_lcd_mode[1]),
            memory.h_vsync.eq(gb_lcd_vsync),
            h_wr_burst_q.eq(memory.fb_data),
            h_wr_burst_q2.eq(memory.osd_data),
        ]

        # SpinalVoodoo fabric: BAR on Wishbone, GB LCD into ChromatiX video.
        # hClk is resetless in ChromatiX CRG — hold scanout out of reset with 0.
        self.voodoo = voodoo = ModRetroChromatic(
            platform, btns, menu=~btn_menu,
            hdmi_clk=ClockSignal("hclk"),
            hdmi_reset=Constant(0),
        )
        self.voodoo_csr = VoodooCoreCSR(voodoo.host_wedged, voodoo.heartbeat)
        self.bus.add_slave(name="voodoo_bar", slave=voodoo.bus, region=SoCRegion(
            origin = 0x50000000,
            size   = 16*1024*1024,
            mode   = "rw",
        ))
        self.comb += [
            gb_lcd_clkena.eq(voodoo.gb_clkena),
            gb_lcd_data.eq(voodoo.gb_data),
            gb_lcd_mode.eq(voodoo.gb_mode),
            gb_lcd_on.eq(voodoo.gb_on),
            gb_lcd_vsync.eq(voodoo.gb_vsync),
            left.eq(0),
            right.eq(0),
        ]

        # Idle the cartridge / IR / link (same as BIOS demo).
        for pad in [cart.d, cart.rst, link.clk]:
            self.specials += TSTriple(len(pad)).get_tristate(pad)
        self.comb += [
            cart.a.eq(0), cart.clk.eq(0), cart.cs.eq(1),
            cart.rd.eq(1), cart.wr.eq(1), cart.data_dir_e.eq(1),
            ir.led.eq(0), link.out.eq(0), link.sd.eq(0),
            i2s.bclk.eq(1),
            hdmi.d_p[2].eq(0), hdmi.d_n[2].eq(h_draw_osd),
            hdmi.d_p[1].eq(0), hdmi.d_n[1].eq(gb_lcd_on),
            hdmi.d_p[0].eq(gb_lcd_vsync), hdmi.d_n[0].eq(gb_lcd_mode[1]),
            hdmi.clk_p.eq(gb_lcd_clkena), hdmi.clk_n.eq(h_gb_write),
        ]

        # USB UVC/UAC/CDC (optional — needs amaranth-yosys to convert LUNA at build time).
        if with_usb:
            self.usb = USBDevice(platform, clk_24, usb,
                with_cdc_stream = with_debug_bridge,
                **({} if uvc_frames is None else {"uvc_frames": uvc_frames}))
            if with_debug_bridge:
                from litex.soc.cores.uart import UARTBone
                from litex.soc.interconnect import stream
                class CDCStreamPHY: pass
                cdc_phy = CDCStreamPHY()
                self.cdc_rx = stream.ClockDomainCrossing([("data", 8)], cd_from="phy", cd_to="sys")
                self.cdc_tx = stream.ClockDomainCrossing([("data", 8)], cd_from="sys", cd_to="phy")
                cdc_phy.source = self.cdc_rx.source
                cdc_phy.sink   = self.cdc_tx.sink
                self.uartbone = UARTBone(phy=cdc_phy, clk_freq=sys_clk_freq, address_width=self.bus.address_width)
                self.bus.add_master(name="uartbone", master=self.uartbone.wishbone)
        else:
            _ = (clk_24, usb, lcd_enable_uvc, lcd_db_uvc, left, right)  # reserved for USB path


def main():
    parser = argparse.ArgumentParser(description="ChromatiX + SpinalVoodoo")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--no-compile", action="store_true")
    parser.add_argument("--with-voodoo", action="store_true", default=True,
                        help="Use SpinalVoodoo (default for this entry point).")
    parser.add_argument("--with-debug-bridge", action="store_true", default=False)
    parser.add_argument("--with-usb", action="store_true", default=False,
                        help="Include ChromatiX USB (UVC/UAC/CDC); needs amaranth-yosys.")
    parser.add_argument("--uvc-sizes", default="320x288,160x144")
    parser.add_argument("--toolchain", default="gowin")
    parser.add_argument("--gowin-path", default=os.environ.get("GOWIN_PATH"))
    args = parser.parse_args()

    if args.gowin_path:
        gowin_path = os.path.expanduser(args.gowin_path)
        os.environ["PATH"] = os.path.join(gowin_path, "bin") + os.pathsep + os.environ.get("PATH", "")
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    platform = Platform(toolchain=args.toolchain)
    # Timing constraints (copied from upstream chromatix.py to avoid importing the SoC top).
    from chromatix.gateware import sources as _  # noqa: F401
    USB_SCLK_NET = "usb_hs_clk"
    TIMING_CONSTRAINTS = [
        'create_generated_clock -name xclk2 -source [get_ports {clk_fpga}] -master_clock clk_fpga -divide_by 1 -multiply_by 4 [get_pins {PLLA/CLKOUT0}]',
        'create_generated_clock -name pclk  -source [get_ports {clk_fpga}] -master_clock clk_fpga -divide_by 1 -multiply_by 1 [get_pins {PLLA/CLKOUT1}]',
        'create_generated_clock -name hclk  -source [get_ports {clk_fpga}] -master_clock clk_fpga -divide_by 2 -multiply_by 1 [get_pins {PLLA/CLKOUT2}]',
        'create_generated_clock -name gclk  -source [get_ports {clk_fpga}] -master_clock clk_fpga -divide_by 4 -multiply_by 1 [get_pins {PLLA/CLKOUT3}]',
        'create_generated_clock -name xclk  -source [get_ports {clk_fpga}] -master_clock clk_fpga -divide_by 1 -multiply_by 2 [get_pins {PLLA/CLKOUT4}]',
        'create_clock -name sclk -period 25 [get_ports {qspi_clk}]',
        'create_generated_clock -name PHY_CLKOUT -source [get_ports {clk_24}] -master_clock clk_24 -divide_by 16 -multiply_by 40 [get_pins {usb_pll/CLKOUT1}]',
        'create_generated_clock -name fclk_960M  -source [get_ports {clk_24}] -master_clock clk_24 -divide_by 1  -multiply_by 40 [get_pins {usb_pll/CLKOUT0}]',
        'create_clock -name usbintsclk -period 8.333 [get_nets {{{usb_sclk}}}]',
        'set_clock_groups -asynchronous -group [get_clocks {PHY_CLKOUT}] -group [get_clocks {fclk_960M}]',
        'set_clock_groups -asynchronous -group [get_clocks {PHY_CLKOUT}] -group [get_clocks {usbintsclk}]',
        'set_clock_groups -asynchronous -group [get_clocks {fclk_960M}] -group [get_clocks {usbintsclk}]',
    ]
    constraints = [c.replace("{{{usb_sclk}}}", "{" + USB_SCLK_NET + "}") for c in TIMING_CONSTRAINTS]
    original = platform.toolchain.build_timing_constraints
    def build_timing_constraints(toolchain, vns):
        sdc = original(vns)
        with open(sdc[0], "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(constraints) + "\n")
        return sdc
    from types import MethodType
    platform.toolchain.build_timing_constraints = MethodType(build_timing_constraints, platform.toolchain)

    soc = VoodooSoC(platform,
        with_debug_bridge = args.with_debug_bridge,
        with_usb          = args.with_usb,
        uvc_frames = [tuple(int(v) for v in size.split("x")) for size in args.uvc_sizes.split(",")],
    )
    builder = Builder(soc, output_dir=os.path.join(_HERE, "build"), csr_csv=os.path.join(_HERE, "csr.csv"),
                      compile_software=False)
    if args.build:
        builder.build(build_name="chromatix_voodoo", run=not args.no_compile)


if __name__ == "__main__":
    main()
