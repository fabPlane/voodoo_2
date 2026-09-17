package voodoo.gowin

import spinal.core._
import spinal.lib._
import voodoo.Config
import voodoo.hdmi.{VideoScanout, VideoTiming}

/** Sipeed Tang Retro Console 138K fabric.
  *
  * No PCIe. After DDR3 calibration a USB-pad menu offers packed Glide2x SDK
  * traces (logo spin, test04, test16, alphabet) plus an OTHER DEMOS submenu
  * of publisher DOS Glide titles (info pages; too big for on-chip ROM).
  * DemoAxiLitePlayer is the AXI-Lite master onto CoreGowin. HDMI is 640x480
  * with a text overlay while the menu is open, plus a live two-line debug HUD.
  *
  * USB gamepads arrive from the BL616 as TangCore UART command 0x09 (hid1/hid2,
  * SNES layout). The UART itself is NESTang `uart_fixed` + `iosys_hid` in the
  * Verilog wrapper (2 Mbps, 8N2 TX). A full-speed CDC ACM gadget on Soft-USB
  * Type-C (S1) streams a text log of calib / wedge / player index / last BAR write.
  */
case class TangConsole138k(c: Config) extends Component {
  noIoPrefix()

  val io = new Bundle {
    val ddr = master(
      GowinDdrNative(
        addrWidth = GowinAddressMap.consoleDdrNativeAddrWidth,
        dataWidth = GowinAddressMap.consoleDdrNativeDataWidth
      )
    )

    /** BL616 USB HID pads (TangCore cmd 0x09). UART lives in Verilog iosys_hid. */
    val hid1 = in Bits (16 bits)
    val hid2 = in Bits (16 bits)
    val keyUserN = in Bool ()
    val keyResetN = in Bool ()
    /** Counters from the BL616 TangCore UART parser in the Verilog wrapper. */
    val uartLink = in Bool ()
    val uartLastCmd = in Bits (8 bits)
    val uartRxCount = in UInt (16 bits)
    val uartHidCount = in UInt (16 bits)
    /** Latched by holding the case MODE button during FPGA startup. */
    val debugMode = out Bool ()

    val hdmiClk = in Bool ()
    val hdmiReset = in Bool ()
    val hdmiR = out UInt (8 bits)
    val hdmiG = out UInt (8 bits)
    val hdmiB = out UInt (8 bits)
    val hdmiHs = out Bool ()
    val hdmiVs = out Bool ()
    val hdmiDe = out Bool ()

    val ctrlClk = in Bool ()
    val ctrlReset = in Bool ()

    val ledCalib = out Bool ()
    val ledDone = out Bool ()
    val ledWedge = out Bool ()
    val ledHeartbeat = out Bool ()

    /** Full-speed CDC gadget on Soft-USB Type-C (S1). */
    val usbOnline = in Bool ()
    val debugTx = master(Flow(Bits(8 bits)))
  }

  val core = CoreGowin(c)
  val ddr = Axi4ToGowinDdrNative(
    CoreGowin.fbAxiConfig(c),
    CoreGowin.texAxiConfig(c),
    nativeDataWidth = GowinAddressMap.consoleDdrNativeDataWidth,
    nativeAddrWidth = GowinAddressMap.consoleDdrNativeAddrWidth
  )
  val player = DemoAxiLitePlayer(DemoTraceRom.banks(), DemoCatalog.playerLoopAt)
  val ctrlCd = ClockDomain(
    clock = io.ctrlClk,
    reset = io.ctrlReset,
    config = ClockDomain.current.config
  )
  val ctrl = new ClockingArea(ctrlCd) {
    val menu = DemoMenu(
      DemoCatalog.mainCount,
      DemoCatalog.otherCount,
      DemoCatalog.mainPlayable,
      DemoCatalog.otherPlayable,
      DemoCatalog.otherDemosIndex
    )

    val buttons = ConsoleButtons()
    buttons.io.modeN := io.keyUserN
    buttons.io.resetN := io.keyResetN
    val extra = Bits(SnesPad.width bits)
    extra := 0
    when(buttons.io.menuDown) {
      extra(SnesPad.Down) := True
    }
    when(buttons.io.menuSelect) {
      extra(SnesPad.A) := True
    }

    // hid1/hid2 are produced on ctrlClk by iosys_hid; BufferCC them in case
    // the wrapper samples a cycle later than this domain.
    val hid1 = BufferCC(io.hid1, init = B(0, 16 bits))
    val hid2 = BufferCC(io.hid2, init = B(0, 16 bits))
    val hidPads =
      hid1(SnesPad.width - 1 downto 0) | hid2(SnesPad.width - 1 downto 0)
    val pads = hidPads | extra

    menu.io.pads := pads
    menu.io.ready := True
    val cursor4 = menu.io.cursor.resize(4 bits)
    val hid16 = pads.resize(16 bits)
    val bankLut = Vec(
      DemoCatalog.playerBankByCursor.map(b => U(b, player.io.select.getWidth bits))
    )
    val mainSel = bankLut(menu.io.cursor.resize(log2Up(DemoCatalog.mainCount max 2) bits))
    val playerSel = Mux(menu.io.other, U(0, player.io.select.getWidth bits), mainSel)
    val loopNow =
      !menu.io.other &&
        menu.io.cursor === U(DemoCatalog.logoIndex, menu.io.cursor.getWidth bits)
  }

  io.debugMode := ctrl.buttons.io.debugMode

  val usePlayer = BufferCC(ctrl.menu.io.playing, init = False)
  player.io.enable := usePlayer
  player.io.loop := BufferCC(ctrl.loopNow, init = False)
  player.io.select := BufferCC(ctrl.playerSel, U(0, player.io.select.getWidth bits))
  player.io.start := PulseCCByToggle(ctrl.menu.io.start, ctrlCd, ClockDomain.current)
  core.io.bar << player.io.axi

  ddr.io.fb << core.io.memFb
  ddr.io.tex << core.io.memTex
  io.ddr <> ddr.io.ddr

  core.io.hdmi.clock := io.hdmiClk
  core.io.hdmi.reset := io.hdmiReset

  val lastBarAddr = Reg(UInt(32 bits)) init (0)
  val lastBarData = Reg(Bits(32 bits)) init (0)
  val barWrites = Reg(UInt(16 bits)) init (0)
  when(core.io.bar.aw.fire) {
    lastBarAddr := core.io.bar.aw.addr.resized
  }
  when(core.io.bar.w.fire) {
    lastBarData := core.io.bar.w.data
    barWrites := barWrites + 1
  }

  val hdmiCd = ClockDomain(
    clock = io.hdmiClk,
    reset = io.hdmiReset,
    config = ClockDomain.current.config
  )
  val hdmiOut = new ClockingArea(hdmiCd) {
    val overlay = BufferCC(ctrl.menu.io.overlay, init = True)
    val screen = BufferCC(ctrl.menu.io.screen)
    // Own DMT 640x480 counters. Core status.x/y live in the DDR/UI clock
    // domain (and sit at 0 while that fabric is in reset), which painted a
    // solid OSD background and no glyphs.
    val scan = VideoScanout(VideoTiming.dmt640x480)
    scan.io.enable := True
    val osd = TextOsd(DemoOsd.screens)
    osd.io.x := scan.io.x.resized
    osd.io.y := scan.io.y.resized
    osd.io.de := scan.io.active
    osd.io.enable := overlay
    osd.io.screen := screen.resized
    val hud = DebugHud()
    hud.io.x := scan.io.x.resized
    hud.io.y := scan.io.y.resized
    hud.io.de := scan.io.active
    hud.io.calib := BufferCC(io.ddr.initCalibComplete, init = False)
    hud.io.wedge := BufferCC(core.io.hostWedged, init = False)
    hud.io.usbOnline := BufferCC(io.usbOnline, init = False)
    hud.io.overlay := overlay
    hud.io.playing := BufferCC(ctrl.menu.io.playing, init = False)
    hud.io.debugMode := BufferCC(ctrl.buttons.io.debugMode, init = False)
    hud.io.modeKey := BufferCC(ctrl.buttons.io.modePressed, init = False)
    hud.io.resetKey := BufferCC(ctrl.buttons.io.resetPressed, init = False)
    hud.io.cursor := BufferCC(ctrl.cursor4, U(0, 4 bits))
    hud.io.hid := BufferCC(ctrl.hid16, B(0, 16 bits))
    hud.io.pc := BufferCC(player.io.index.resize(32 bits), U(0, 32 bits))
    hud.io.barAddr := BufferCC(lastBarAddr, U(0, 32 bits))
    hud.io.barData := BufferCC(lastBarData, B(0, 32 bits))
    hud.io.barCount := BufferCC(barWrites, U(0, 16 bits))
    hud.io.uartLink := BufferCC(io.uartLink, init = False)
    hud.io.uartLastCmd := BufferCC(io.uartLastCmd, B(0, 8 bits))
    hud.io.uartRxCount := BufferCC(io.uartRxCount, U(0, 16 bits))
    hud.io.uartHidCount := BufferCC(io.uartHidCount, U(0, 16 bits))
    val beat = CounterFreeRun(1 << 23)
    io.ledHeartbeat := !beat.msb
    when(overlay) {
      io.hdmiHs := scan.io.hSync
      io.hdmiVs := scan.io.vSync
      io.hdmiDe := scan.io.active
      when(hud.io.hit) {
        io.hdmiR := hud.io.r
        io.hdmiG := hud.io.g
        io.hdmiB := hud.io.b
      } otherwise {
        io.hdmiR := osd.io.r
        io.hdmiG := osd.io.g
        io.hdmiB := osd.io.b
      }
    } otherwise {
      io.hdmiHs := core.io.hdmi.video.hSync
      io.hdmiVs := core.io.hdmi.video.vSync
      io.hdmiDe := core.io.hdmi.video.de
      when(hud.io.debugMode && hud.io.hit) {
        io.hdmiR := hud.io.r
        io.hdmiG := hud.io.g
        io.hdmiB := hud.io.b
      } otherwise {
        io.hdmiR := core.io.hdmi.video.rgb.r
        io.hdmiG := core.io.hdmi.video.rgb.g
        io.hdmiB := core.io.hdmi.video.rgb.b
      }
    }
  }

  io.ledCalib := !io.ddr.initCalibComplete
  io.ledDone := !BufferCC(ctrl.menu.io.info, init = False)
  io.ledWedge := !core.io.hostWedged

  val log = CoreDebugLog()
  log.io.calib := io.ddr.initCalibComplete
  log.io.wedge := core.io.hostWedged
  log.io.overlay := BufferCC(ctrl.menu.io.overlay, init = True)
  log.io.playing := BufferCC(ctrl.menu.io.playing, init = False)
  log.io.info := BufferCC(ctrl.menu.io.info, init = False)
  log.io.cursor := BufferCC(ctrl.cursor4)
  log.io.pc := player.io.index.resize(32 bits)
  log.io.barAddr := lastBarAddr
  log.io.barData := lastBarData
  log.io.barCount := barWrites
  log.io.hid := BufferCC(ctrl.hid16)
  log.io.usbOnline := BufferCC(io.usbOnline, init = False)
  log.io.debugMode := BufferCC(ctrl.buttons.io.debugMode, init = False)
  log.io.modeKey := BufferCC(ctrl.buttons.io.modePressed, init = False)
  log.io.resetKey := BufferCC(ctrl.buttons.io.resetPressed, init = False)
  log.io.uartLink := BufferCC(io.uartLink, init = False)
  log.io.uartLastCmd := BufferCC(io.uartLastCmd, B(0, 8 bits))
  log.io.uartRxCount := BufferCC(io.uartRxCount, U(0, 16 bits))
  log.io.uartHidCount := BufferCC(io.uartHidCount, U(0, 16 bits))
  io.debugTx << log.io.tx
}
