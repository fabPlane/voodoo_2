package voodoo.kintex

import spinal.core._
import spinal.core.internals.{PhaseContext, PhaseNetlist}
import spinal.lib._
import voodoo.{Config, GenSupport}
import voodoo.gowin.{GowinDdrNative, GowinGenSupport, TangMega138kPro}

/** AMD Kintex-7 (XC7K160T / XC7K325T, FFG676) PCIe card fabric.
  *
  * The Pro Dock fabric (TangMega138kPro: TLP BAR target, frame DMA, DDR
  * adapter, Voodoo core) unchanged, so the host software and its BAR map
  * (1 MiB BAR0, window register at 0x250, window 31 local registers) carry
  * over. Only the vendor edges differ:
  *   - PCIe: pcie_7x 64-bit AXI4-Stream, converted to Gowin TL beats here.
  *   - DDR3: MIG 7-series native UI. Same command codes, 256-bit word
  *     (32-bit DDR3, 4:1, BL8), active-high write mask, in-order reads
  *     (MIG ORDERING = STRICT). MIG app_addr counts 32-bit DQ words, the
  *     Gowin address counts 32-byte words: app_addr = ddrAddr << 3, done
  *     in board_top.v.
  *   - Supply / temperature: board_top's XADC monitor (xadc_monitor.v)
  *     in the Gowin AdcMonitor format (BAR 0x68-0x70, DMA status 0-2).
  *   - No own DDR3 controller debug port (0x9C reads 0): MIG calibrates
  *     itself and has no gate maps to tune.
  *
  * Implicit clock domain: MIG ui_clk / ui_clk_sync_rst (DDR adapter).
  */
case class Kintex7Voodoo(c: Config) extends Component {
  noIoPrefix()
  import XilinxPcieTlp._

  val io = new Bundle {
    val coreClk = in Bool ()
    val coreReset = in Bool ()

    // pcie_7x user side (user_clk_out, user_reset_out, user_lnk_up).
    val pcieClk = in Bool ()
    val pcieReset = in Bool ()
    val pcieLinkup = in Bool ()
    val rxData = in Bits (dataWidth bits)
    val rxKeep = in Bits (keepWidth bits)
    val rxLast = in Bool ()
    val rxValid = in Bool ()
    val rxReady = out Bool ()
    val rxBarHit = in Bits (barHitWidth bits)
    val txData = out Bits (dataWidth bits)
    val txKeep = out Bits (keepWidth bits)
    val txLast = out Bool ()
    val txValid = out Bool ()
    val txReady = in Bool ()
    val cfgBusNumber = in Bits (8 bits)
    val cfgDeviceNumber = in Bits (5 bits)

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

    // xadc_monitor.v (DDR UI clock domain; the fabric resynchronises).
    val adcNow = in Bits (32 bits)
    val adcExtremes = in Bits (32 bits)
    val adcSamples = in Bits (32 bits)
  }

  val fabric = TangMega138kPro(c, gowinAdc = false)
  fabric.io.coreClk := io.coreClk
  fabric.io.coreReset := io.coreReset
  fabric.io.pcieClk := io.pcieClk
  fabric.io.pcieReset := io.pcieReset
  fabric.io.pcieLinkup := io.pcieLinkup
  fabric.io.hdmiClk := io.hdmiClk
  fabric.io.hdmiReset := io.hdmiReset

  val pcieCd = ClockDomain(
    clock = io.pcieClk,
    reset = io.pcieReset,
    config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)
  )
  val pcie = new ClockingArea(pcieCd) {
    val rx = XilinxPcieRxToGowin()
    rx.io.rxData := io.rxData
    rx.io.rxKeep := io.rxKeep
    rx.io.rxLast := io.rxLast
    rx.io.rxValid := io.rxValid
    io.rxReady := rx.io.rxReady
    rx.io.rxBarHit := io.rxBarHit
    fabric.io.tlRxSop := rx.io.tlRxSop
    fabric.io.tlRxEop := rx.io.tlRxEop
    fabric.io.tlRxValid := rx.io.tlRxValid
    fabric.io.tlRxData := rx.io.tlRxData
    fabric.io.tlRxBardec := rx.io.tlRxBardec
    rx.io.tlRxWait := fabric.io.tlRxWait

    val tx = GowinTxToXilinxPcie()
    tx.io.tlTxSop := fabric.io.tlTxSop
    tx.io.tlTxEop := fabric.io.tlTxEop
    tx.io.tlTxValid := fabric.io.tlTxValid
    tx.io.tlTxData := fabric.io.tlTxData
    fabric.io.tlTxWait := tx.io.tlTxWait
    io.txData := tx.io.txData
    io.txKeep := tx.io.txKeep
    io.txLast := tx.io.txLast
    io.txValid := tx.io.txValid
    tx.io.txReady := io.txReady

    // The 7-series block holds a TLP in its TX buffer until the link
    // partner has credits (s_axis_tx_tready backs up), so both credit
    // gates are always open. Bit 31 is all the fabric looks at.
    fabric.io.tlTxCreditsCpl := B"32'x80000000"
    fabric.io.tlTxCreditsP := B"32'x80000000"
    fabric.io.tlCfgBusdev := RegNext((io.cfgBusNumber ## io.cfgDeviceNumber).asUInt)
  }

  io.ddr <> fabric.io.ddr
  fabric.io.ddrDbgRdata := 0
  fabric.io.adcNow := io.adcNow
  fabric.io.adcExtremes := io.adcExtremes
  fabric.io.adcSamples := io.adcSamples

  io.hdmiR := fabric.io.hdmiR
  io.hdmiG := fabric.io.hdmiG
  io.hdmiB := fabric.io.hdmiB
  io.hdmiHs := fabric.io.hdmiHs
  io.hdmiVs := fabric.io.hdmiVs
  io.hdmiDe := fabric.io.hdmiDe

  io.ledLink := fabric.io.ledLink
  io.ledCalib := fabric.io.ledCalib
  io.ledWedge := fabric.io.ledWedge
  io.ledHeartbeat := fabric.io.ledHeartbeat
}

/** Memory placement for Vivado (and yosys synth_xilinx).
  *
  * Gowin has no LUT-RAM, so GowinMemStylePhase forces shallow / small RAMs
  * to flip-flops. 7-series LUTs make 64 x 1 RAMs (32 x 6 per slice), so
  * the same memories go to distributed RAM here: no flip-flop per bit, no
  * 36K block for a 16-deep FIFO. Very shallow ones (<= 4) stay registers.
  * Dual-clock FIFOs work as LUT-RAM too (write port clocked, read async),
  * but are left to the tool like on Gowin.
  */
class XilinxMemStylePhase(registersMaxDepth: Int, distMaxDepth: Int, distMaxBits: Int) extends PhaseNetlist {
  override def impl(pc: PhaseContext): Unit = pc.walkDeclarations {
    case mem: Mem[_] if mem.component != null && mem.initialContent == null =>
      val depth = mem.wordCount
      if (depth <= registersMaxDepth)
        mem.addAttribute("ram_style", "registers")
      else if (depth <= distMaxDepth && depth * mem.getWidth <= distMaxBits)
        mem.addAttribute("ram_style", "distributed")
    case _ =>
  }
}

object KintexGenSupport {
  // Same core configuration as the Pro Dock (GOWIN_TMU_COUNT etc. apply);
  // only the memory mapping pass differs.
  def config: Config = if (sys.env.contains("KINTEX_FULL")) fullConfig else GowinGenSupport.config

  // KINTEX_FULL: everything the dock build has (rtl_b199: two TMUs, 1024-line
  // texture caches for both, line validity as a tag generation) plus what it
  // leaves out only for GW5AST area or timing: the Voodoo 2 setup engine
  // (B175/B176 missed timing at 86% CLS), 8 texture fill slots (B171: -13.8%
  // Quake II replay cycles, failed every placement), the triangleCMD register
  // copy and hint lineValid checks.
  def fullConfig: Config = GowinGenSupport.config.copy(
    tmuCount = 2,
    texSampleQueueDepth = 12,
    texCachePairedBanks = true,
    pixelConfigSlots = true,
    texSmallCacheLines = 1024,
    tmu1SmallCacheLines = 1024,
    texValidEpochBits = 6,
    setupEngine = true,
    texSmallFillSlots = 8,
    triangleSnapshot = true,
    texHintValidCheck = true
  )

  def verilog(targetDirectory: String = "rtl/kintex"): SpinalConfig =
    SpinalConfig(
      mode = Verilog,
      targetDirectory = targetDirectory,
      defaultConfigForClockDomains = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)
    ).addTransformationPhase(
      // 64-deep LUT-RAM is one LUT per bit; past ~8 kbit a block RAM
      // (36 kbit) is cheaper than the LUTs.
      new XilinxMemStylePhase(registersMaxDepth = 4, distMaxDepth = 64, distMaxBits = 8192)
    )
}

object Kintex7VoodooGen extends App {
  val targetDirectory = args.headOption.getOrElse("rtl/kintex")
  val report = KintexGenSupport.verilog(targetDirectory).generate(Kintex7Voodoo(KintexGenSupport.config))
  GenSupport.mirrorRomSidecars(targetDirectory, report.toplevelName)
}
