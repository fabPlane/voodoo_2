package voodoo.chromatix

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.amba4.axilite._
import voodoo.Config
import voodoo.artix.AxiLite4ToBmbBridge
import voodoo.hdmi.VideoTiming

/** ModRetro Chromatic fabric: SpinalVoodoo Core behind AXI-Lite / AXI4, with a
  * Game Boy LCD stream for the ChromatiX video pipeline.
  *
  * Host path: AXI4-Lite BAR0 (24-bit), same contract as CoreArtix / CoreGowin.
  * LiteX (or a demo player) is the master. Memory path: two AXI4 masters into
  * the 8 MiB OPI PSRAM (framebuffer + relocated textures). Video path: the
  * core's parallel RGB scanout is windowed/downscaled to 160x144 RGB555 and
  * emitted as `gb_*` so ChromatiX can keep its blend/OSD/LCD/UVC chain.
  *
  * Buttons are level inputs (active high = pressed), matching ChromatiX
  * `buttons` after debounce. They are exported for a future on-device menu;
  * this first cut leaves the BAR idle unless the LiteX side drives it.
  */
case class ModRetroChromatic(c: Config) extends Component {
  noIoPrefix()

  val io = new Bundle {
    val bar = slave(AxiLite4(AxiLite4ToBmbBridge.axiConfig(CoreChromatix.barAddressWidth)))
    val memFb = master(Axi4(CoreChromatix.fbAxiConfig(c)))
    val memTex = master(Axi4(CoreChromatix.texAxiConfig(c)))

    val hdmiClk = in Bool ()
    val hdmiReset = in Bool ()

    val gbClkena = out Bool ()
    val gbData = out Bits (15 bits)
    val gbMode = out Bits (2 bits)
    val gbOn = out Bool ()
    val gbVsync = out Bool ()

    val btnA = in Bool ()
    val btnB = in Bool ()
    val btnUp = in Bool ()
    val btnDown = in Bool ()
    val btnLeft = in Bool ()
    val btnRight = in Bool ()
    val btnStart = in Bool ()
    val btnSelect = in Bool ()
    val btnMenu = in Bool ()

    val hostWedged = out Bool ()
    val heartbeat = out Bool ()
  }

  val core = CoreChromatix(c)
  core.io.bar << io.bar
  io.memFb << core.io.memFb
  io.memTex << core.io.memTex
  io.hostWedged := core.io.hostWedged

  core.io.hdmi.clock := io.hdmiClk
  core.io.hdmi.reset := io.hdmiReset

  // Drive a 640x480 display size into the core scanout; GbLcdFromScanout
  // downscales the active window for the Chromatic panel.
  val lcd = GbLcdFromScanout(VideoTiming.dmt640x480, outWidth = 160, outHeight = 144)
  lcd.io.video := core.io.hdmi.video
  lcd.io.statusX := core.io.hdmi.status.x
  lcd.io.statusY := core.io.hdmi.status.y
  lcd.io.statusActive := core.io.hdmi.status.active
  lcd.io.statusNewFrame := core.io.hdmi.status.newFrame

  io.gbClkena := lcd.io.gbClkena
  io.gbData := lcd.io.gbData
  io.gbMode := lcd.io.gbMode
  io.gbOn := lcd.io.gbOn
  io.gbVsync := lcd.io.gbVsync

  val beat = Reg(UInt(24 bits)) init (0)
  beat := beat + 1
  io.heartbeat := beat.msb

  // Keep button inputs referenced so synthesis does not strip the ports.
  val btnOr = io.btnA | io.btnB | io.btnUp | io.btnDown | io.btnLeft |
    io.btnRight | io.btnStart | io.btnSelect | io.btnMenu
  when(btnOr) {
    beat := beat
  }
}
