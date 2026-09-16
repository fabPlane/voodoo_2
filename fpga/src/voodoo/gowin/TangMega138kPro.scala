package voodoo.gowin

import spinal.core._
import spinal.lib._
import voodoo.Config

/** Sipeed Tang Mega 138K Pro Dock fabric.
  *
  * Vendor IP (PCIe controller, DDR3 PHY, DVI TX, PLLs) lives in the Verilog
  * board wrapper. This component is the SpinalVoodoo side: TLP BAR0 -> AXI-Lite
  * -> Core, AXI4 framebuffer/texture -> Gowin DDR3 native UI, parallel RGB out
  * to DVI_TX_Top.
  */
case class TangMega138kPro(c: Config) extends Component {
  noIoPrefix()

  val io = new Bundle {
    val pcieClk = in Bool ()
    val pcieReset = in Bool ()
    val pcieLinkup = in Bool ()
    val tlRxSop = in Bool ()
    val tlRxEop = in Bool ()
    val tlRxValid = in Bits (PcieTlpBarTarget.tlpValidWidth bits)
    val tlRxData = in Bits (PcieTlpBarTarget.tlpDataWidth bits)
    val tlRxBardec = in Bits (PcieTlpBarTarget.barDecWidth bits)
    val tlRxWait = out Bool ()
    val tlTxSop = out Bool ()
    val tlTxEop = out Bool ()
    val tlTxValid = out Bits (PcieTlpBarTarget.tlpValidWidth bits)
    val tlTxData = out Bits (PcieTlpBarTarget.tlpDataWidth bits)
    val tlTxWait = in Bool ()
    val tlTxCreditsCpl = in Bits (32 bits)
    val tlCfgBusdev = in UInt (13 bits)

    val ddr = master(GowinDdrNative())

    val hdmiClk = in Bool ()
    val hdmiReset = in Bool ()
    val hdmiR = out UInt (8 bits)
    val hdmiG = out UInt (8 bits)
    val hdmiB = out UInt (8 bits)
    val hdmiHs = out Bool ()
    val hdmiVs = out Bool ()
    val hdmiDe = out Bool ()

    val ledLink = out Bool ()
    val ledCalib = out Bool ()
    val ledWedge = out Bool ()
    val ledHeartbeat = out Bool ()
  }

  val core = CoreGowin(c)
  val pcieCd = ClockDomain(
    clock = io.pcieClk,
    reset = io.pcieReset,
    config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)
  )

  val pcie = new ClockingArea(pcieCd) {
    val bar = PcieTlpBarTarget()
    bar.io.tlRxSop := io.tlRxSop
    bar.io.tlRxEop := io.tlRxEop
    bar.io.tlRxValid := io.tlRxValid
    bar.io.tlRxData := io.tlRxData
    bar.io.tlRxBardec := io.tlRxBardec
    io.tlRxWait := bar.io.tlRxWait
    io.tlTxSop := bar.io.tlTxSop
    io.tlTxEop := bar.io.tlTxEop
    io.tlTxValid := bar.io.tlTxValid
    io.tlTxData := bar.io.tlTxData
    bar.io.tlTxWait := io.tlTxWait
    bar.io.tlTxCreditsCpl := io.tlTxCreditsCpl
    bar.io.tlCfgBusdev := io.tlCfgBusdev
  }

  val barCc = AxiLite4ClockCrossing(
    PcieTlpBarTarget.axiConfig(),
    pcieCd,
    ClockDomain.current
  )
  val ddr = Axi4ToGowinDdrNative(CoreGowin.fbAxiConfig(c), CoreGowin.texAxiConfig(c))

  pcie.bar.io.axi <> barCc.io.input
  core.io.bar <> barCc.io.output
  ddr.io.fb << core.io.memFb
  ddr.io.tex << core.io.memTex
  io.ddr <> ddr.io.ddr

  core.io.hdmi.clock := io.hdmiClk
  core.io.hdmi.reset := io.hdmiReset
  io.hdmiR := core.io.hdmi.video.rgb.r
  io.hdmiG := core.io.hdmi.video.rgb.g
  io.hdmiB := core.io.hdmi.video.rgb.b
  io.hdmiHs := core.io.hdmi.video.hSync
  io.hdmiVs := core.io.hdmi.video.vSync
  io.hdmiDe := core.io.hdmi.video.de

  val beat = CounterFreeRun(1 << 24)
  io.ledLink := !io.pcieLinkup
  io.ledCalib := !io.ddr.initCalibComplete
  io.ledWedge := !core.io.hostWedged
  io.ledHeartbeat := !beat.msb
}
