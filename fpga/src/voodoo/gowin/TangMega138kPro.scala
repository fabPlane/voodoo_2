package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi.{Axi4, Axi4CC}
import voodoo.Config

/** Sipeed Tang Mega 138K Pro Dock fabric.
  *
  * Vendor IP (PCIe controller, DDR3 PHY, DVI TX, PLLs) lives in the Verilog
  * board wrapper. This component is the SpinalVoodoo side: TLP BAR0 -> AXI-Lite
  * -> Core, AXI4 framebuffer/texture -> Gowin DDR3 native UI, parallel RGB out
  * to DVI_TX_Top.
  *
  * Clock domains: the implicit domain is the DDR3 UI clock (100 MHz) and only
  * runs the native-port adapter. The Voodoo core runs on coreClk (50 MHz, the
  * SST-1 graphics clock); framebuffer / texture AXI cross into the DDR3
  * domain through Axi4CC FIFOs.
  */
case class TangMega138kPro(c: Config) extends Component {
  noIoPrefix()

  val io = new Bundle {
    val coreClk = in Bool ()
    val coreReset = in Bool ()
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

  val coreCd = ClockDomain(
    clock = io.coreClk,
    reset = io.coreReset,
    config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)
  )
  val core = coreCd(CoreGowin(c))
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
    coreCd
  )
  val ddr = Axi4ToGowinDdrNative(CoreGowin.fbAxiConfig(c), CoreGowin.texAxiConfig(c))
  // One full burst (2^axiBurstLog2 beats) of W / R buffering per direction.
  private val burstBeats = 1 << CoreGowin.axiBurstLog2
  val fbCc = Axi4CC(CoreGowin.fbAxiConfig(c), coreCd, ClockDomain.current,
    arFifoSize = 4, awFifoSize = 4, rFifoSize = burstBeats, wFifoSize = burstBeats, bFifoSize = 4)
  val texCc = Axi4CC(CoreGowin.texAxiConfig(c), coreCd, ClockDomain.current,
    arFifoSize = 4, awFifoSize = 4, rFifoSize = burstBeats, wFifoSize = burstBeats, bFifoSize = 4)

  pcie.bar.io.axi <> barCc.io.input
  core.io.bar <> barCc.io.output
  fbCc.io.input << core.io.memFb
  texCc.io.input << core.io.memTex
  // Register the channels between the CDC FIFOs and the adapter: the
  // request pop-side valid and the R push-side ready otherwise fed the
  // adapter's state / address decode combinationally.
  private def staged(a: Axi4): Axi4 =
    a.pipelined(StreamPipe.FULL, StreamPipe.FULL, StreamPipe.NONE, StreamPipe.FULL, StreamPipe.FULL)
  ddr.io.fb << staged(fbCc.io.output)
  ddr.io.tex << staged(texCc.io.output)
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
