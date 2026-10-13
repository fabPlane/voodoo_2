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
    // Register RX beats between the PCIe hard block and the BAR target (same
    // accept-unless-wait handshake, full throughput): the block's TL_RX_DATA
    // pins otherwise fed the target's decode and enables combinationally, and
    // whether that closed at 75 MHz depended on where placement put the target.
    case class RxBeat() extends Bundle {
      val sop, eop = Bool()
      val valid = Bits(PcieTlpBarTarget.tlpValidWidth bits)
      val data = Bits(PcieTlpBarTarget.tlpDataWidth bits)
      val bardec = Bits(PcieTlpBarTarget.barDecWidth bits)
    }
    val rxIn = Stream(RxBeat())
    rxIn.valid := io.tlRxValid.orR
    rxIn.sop := io.tlRxSop
    rxIn.eop := io.tlRxEop
    rxIn.payload.valid := io.tlRxValid
    rxIn.data := io.tlRxData
    rxIn.bardec := io.tlRxBardec
    io.tlRxWait := !rxIn.ready
    val rx = rxIn.m2sPipe()
    bar.io.tlRxSop := rx.sop
    bar.io.tlRxEop := rx.eop
    bar.io.tlRxValid := rx.valid ? rx.payload.valid | B(0, PcieTlpBarTarget.tlpValidWidth bits)
    bar.io.tlRxData := rx.data
    bar.io.tlRxBardec := rx.bardec
    rx.ready := !bar.io.tlRxWait
    bar.io.tlTxCreditsCpl := RegNext(io.tlTxCreditsCpl)
    bar.io.tlCfgBusdev := RegNext(io.tlCfgBusdev)

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
    // Registered: TL_TX_CREDITS -> TX data was a failing path; a cycle-old
    // credit bit is fine for the DMA's coarse "posted credits available" gate.
    dma.io.postedCreditOk := RegNext(io.tlTxCreditsP(31)) init (False)
    dma.io.busdev := RegNext(io.tlCfgBusdev)
    dma.io.regWrite := bar.io.localWrite
    dma.io.regAddr := bar.io.localAddr
    dma.io.regWdata := bar.io.localWdata
    val axiDebug = Bits(32 bits)
    // Window 31 offset 0x64: DDR3 throttles for stability testing, [7:0]
    // idle cycles after every native command, [11:8] max reads in flight
    // (0 = all 8). Set before a run; the DDR side samples it through a
    // synchronizer.
    val ddrThrottle = Reg(Bits(32 bits)) init (B(8, 32 bits))
    when(bar.io.localWrite && bar.io.localAddr === 0x64) { ddrThrottle := bar.io.localWdata }
    // On-chip supply / temperature monitor. Also sent with every frame-DMA
    // status write, so the host logs it without touching the BAR:
    // 0x68 / extra 0: vcc [15:0] | temp [31:16]; 0x6C / extra 1: vccMin |
    // tempMax since configuration; 0x70 / extra 2: samples. VCC [V] =
    // vcc / 1024, temperature [degC] = temp / 4 (see AdcMonitor).
    // The monitor runs in the core clock domain (the PCIe domain is tight
    // around the hard block); its slowly changing outputs cross over here.
    val adcNowIn, adcExtremesIn, adcSamplesIn = Bits(32 bits)
    val adcNow = BufferCC(adcNowIn, init = B(0, 32 bits))
    val adcExtremes = BufferCC(adcExtremesIn, init = B(0, 32 bits))
    val adcSamples = BufferCC(adcSamplesIn, init = B(0, 32 bits))
    dma.io.extraStatus(0) := adcNow
    dma.io.extraStatus(1) := adcExtremes
    dma.io.extraStatus(2) := adcSamples
    bar.io.localRdata := dma.io.regRdata
    when(bar.io.localAddr === 0x60) { bar.io.localRdata := axiDebug }
    when(bar.io.localAddr === 0x64) { bar.io.localRdata := ddrThrottle }
    when(bar.io.localAddr === 0x68) { bar.io.localRdata := adcNow }
    when(bar.io.localAddr === 0x6c) { bar.io.localRdata := adcExtremes }
    when(bar.io.localAddr === 0x70) { bar.io.localRdata := adcSamples }
  }

  val adcMon = new ClockingArea(coreCd) {
    val adc = AdcMonitor()
    val now = RegNext(adc.io.temp.asBits ## adc.io.vcc.asBits)
    val extremes = RegNext(adc.io.tempMax.asBits ## adc.io.vccMin.asBits)
    val samples = RegNext(adc.io.samples.asBits)
  }
  pcie.adcNowIn := adcMon.now
  pcie.adcExtremesIn := adcMon.extremes
  pcie.adcSamplesIn := adcMon.samples

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
  // Registered: the FIFO's BSRAM output fed the PCIe TX data directly and
  // missed 75 MHz (B61). The word held in the register counts as available.
  val videoWords = pcieCd(videoFifo.io.pop.m2sPipe())
  pcie.dma.io.words << videoWords
  pcie.dma.io.wordsAvailable := pcieCd((videoFifo.io.popOccupancy +^ videoWords.valid.asUInt).resized)
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
  // W / R buffering per direction: 32 words, four 32-byte bursts.
  private val burstBeats = 32
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
  private val throttle = BufferCC(pcie.ddrThrottle, init = B(8, 32 bits))
  ddr.io.cmdGap := throttle(7 downto 0).asUInt
  ddr.io.maxReads := throttle(11 downto 8).asUInt
  // Scanout FIFO (4096 pixels) below 3/4: let the display's reads through
  // the DDR throttle, ahead of writes (black frames at gap 32 otherwise; at
  // 1/2 a mid-frame underflow still blanked frames during texture uploads).
  private val scanoutLow = coreCd(RegNext(core.io.hdmi.fifoLevel < c.hdmiRefillLow, init = True))
  ddr.io.urgent := BufferCC(scanoutLow, init = True)

  // Window 31 offset 0x60: AXI valid/ready levels of the memory paths, so a
  // hung board shows which channel is stuck. [9:0] core tex, [19:10] core fb
  // (core clock), [29:20] fb at the DDR adapter (DDR clock); each group is
  // ar.v ar.r r.v r.r aw.v aw.r w.v w.r b.v b.r from bit 0 up.
  private def handshakes(a: Axi4): Bits = Cat(
    a.b.ready, a.b.valid, a.w.ready, a.w.valid, a.aw.ready, a.aw.valid,
    a.r.ready, a.r.valid, a.ar.ready, a.ar.valid
  )
  val coreAxiLevels = coreCd(RegNext(handshakes(core.io.memFb) ## handshakes(core.io.memTex)))
  val ddrAxiLevels = RegNext(handshakes(ddr.io.fb))
  pcie.axiDebug := pcieCd(B"00" ## BufferCC(ddrAxiLevels) ## BufferCC(coreAxiLevels))

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
