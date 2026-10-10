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
    val tlTxCreditsP = in Bits (32 bits)
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
    val bar = PcieTlpBarTarget(addressWidth = PcieTlpBarTarget.rawAddressWidth)
    bar.io.tlRxSop := io.tlRxSop
    bar.io.tlRxEop := io.tlRxEop
    bar.io.tlRxValid := io.tlRxValid
    bar.io.tlRxData := io.tlRxData
    bar.io.tlRxBardec := io.tlRxBardec
    io.tlRxWait := bar.io.tlRxWait
    bar.io.tlTxCreditsCpl := io.tlTxCreditsCpl
    bar.io.tlCfgBusdev := io.tlCfgBusdev

    // TX port shared by BAR read completions (priority) and frame DMA writes.
    val txArb = TlTxArbiter()
    txArb.io.inputs(0).sop := bar.io.tlTxSop
    txArb.io.inputs(0).eop := bar.io.tlTxEop
    txArb.io.inputs(0).valid := bar.io.tlTxValid
    txArb.io.inputs(0).data := bar.io.tlTxData
    bar.io.tlTxWait := txArb.io.inputs(0).waitReq
    io.tlTxSop := txArb.io.output.sop
    io.tlTxEop := txArb.io.output.eop
    io.tlTxValid := txArb.io.output.valid
    io.tlTxData := txArb.io.output.data
    txArb.io.output.waitReq := io.tlTxWait

    val dma = FrameDmaWriter()
    dma.io.tx <> txArb.io.inputs(1)
    dma.io.postedCreditOk := io.tlTxCreditsP(31)
    dma.io.busdev := io.tlCfgBusdev
    dma.io.regWrite := bar.io.localWrite
    dma.io.regAddr := bar.io.localAddr
    dma.io.regWdata := bar.io.localWdata
    bar.io.localRdata := dma.io.regRdata
  }

  // HDMI scanout -> RGB565 words -> TLP clock (see FrameDma).
  val hdmiCd = ClockDomain(
    clock = io.hdmiClk,
    reset = io.hdmiReset,
    config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)
  )
  val videoTap = new ClockingArea(hdmiCd) {
    val tap = ScanoutTap()
    tap.io.r := core.io.hdmi.video.rgb.r
    tap.io.g := core.io.hdmi.video.rgb.g
    tap.io.b := core.io.hdmi.video.rgb.b
    tap.io.de := core.io.hdmi.video.de
    tap.io.vs := core.io.hdmi.video.vSync
    tap.io.enable := BufferCC(pcie.dma.io.enable, init = False)
    tap.io.decimate := BufferCC(pcie.dma.io.decimate, init = U(0, 8 bits))
  }
  val videoFifo = StreamFifoCC(FrameDma.fifoWord, 128, hdmiCd, pcieCd)
  videoFifo.io.push << videoTap.tap.io.push
  pcie.dma.io.words << videoFifo.io.pop
  pcie.dma.io.wordsAvailable := videoFifo.io.popOccupancy.resized
  pcie.dma.io.overflowPulse := PulseCCByToggle(videoTap.tap.io.overflow, hdmiCd, pcieCd)

  val barCc = AxiLite4ClockCrossing(
    PcieTlpBarTarget.axiConfig(PcieTlpBarTarget.rawAddressWidth),
    pcieCd,
    coreCd
  )
  // BAR windows 16-31 (address bit 24) bypass the Voodoo and reach DDR
  // 0x000000-0xffffff directly (fb at 0, textures at 4 MiB): trace replay
  // loads saved fb/texture state and reads frames back through it.
  val raw = new ClockingArea(coreCd) {
    val split = AxiLite4RawSplit(PcieTlpBarTarget.rawAddressWidth)
    val toAxi = AxiLite4ToAxi4Single(split.outConfig, CoreGowin.fbAxiConfig(c))
    val merge = Axi4TwoToOne(CoreGowin.fbAxiConfig(c))
    split.io.raw <> toAxi.io.input
    merge.io.inputs(1) << toAxi.io.output
    core.io.rawActive := BufferCC(pcie.bar.io.window.msb, init = False)
  }
  val fbDdrConfig = raw.merge.outConfig
  val ddr = Axi4ToGowinDdrNative(fbDdrConfig, CoreGowin.texAxiConfig(c))
  // One full burst (2^axiBurstLog2 beats) of W / R buffering per direction.
  private val burstBeats = 1 << CoreGowin.axiBurstLog2
  val fbCc = Axi4CC(fbDdrConfig, coreCd, ClockDomain.current,
    arFifoSize = 4, awFifoSize = 4, rFifoSize = burstBeats, wFifoSize = burstBeats, bFifoSize = 4)
  val texCc = Axi4CC(CoreGowin.texAxiConfig(c), coreCd, ClockDomain.current,
    arFifoSize = 4, awFifoSize = 4, rFifoSize = burstBeats, wFifoSize = burstBeats, bFifoSize = 4)

  pcie.bar.io.axi <> barCc.io.input
  raw.split.io.input <> barCc.io.output
  core.io.bar <> raw.split.io.core
  raw.merge.io.inputs(0) << core.io.memFb
  fbCc.io.input << raw.merge.io.output
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
