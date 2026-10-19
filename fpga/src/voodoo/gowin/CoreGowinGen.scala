package voodoo.gowin

import spinal.core._
import spinal.core.internals.{PhaseContext, PhaseNetlist}
import voodoo.{Config, GenSupport}

/** Moves shallow or small inferred RAMs off BSRAM via `syn_ramstyle`.
  *
  * GowinSynthesis puts any `reg [..] mem [..]` into BSRAM and slices wide
  * words into one 18K tile per ~36 bits, regardless of depth. Upstream's
  * shallow, very wide stream FIFOs (rasterizer 2 x 2713, span walker
  * 4 x 1439) then burn ~90 tiles for ~11 kbit of state and PnR overflows
  * the 340 tiles on GW5AST-138. Those go to registers, as do small
  * (<= 32 deep, <= 4 kbit) memories. Everything else stays in BSRAM: as
  * registers the TMU / 32-deep FIFOs cost ~40k FFs and nothing routes.
  * (Putting the span walker FIFO back in BSRAM routed worse, not better.) GowinSynthesis 1.9.12 ignores
  * "distributed_ram" for these, so there is no LUT-RAM middle ground.
  * ROMs are left alone. Done here so ext/SpinalVoodoo stays untouched.
  */
class GowinMemStylePhase(
    registersMaxDepth: Int,
    smallMaxDepth: Int,
    smallMaxBits: Int,
    bsramComponents: Seq[String] = Nil
) extends PhaseNetlist {
  // Clock-crossing FIFOs (StreamFifoCC: Axi4CC, AXI-Lite CDC, HDMI pixel
  // FIFO) write in one domain and read in another. Forcing those to
  // registers lost every other 256-byte AXI write burst on the board (the
  // 4-deep AW FIFO in Axi4CC); sim was clean. Leave dual-clock mems alone.
  private def isDualClock(mem: Mem[_]): Boolean = {
    val cds = scala.collection.mutable.LinkedHashSet[ClockDomain]()
    mem.foreachStatements {
      case s: MemWrite => cds += s.clockDomain
      case s: MemReadSync => cds += s.clockDomain
      case s: MemReadWrite => cds += s.clockDomain
      case _ =>
    }
    cds.size > 1
  }

  override def impl(pc: PhaseContext): Unit = pc.walkDeclarations {
    case mem: Mem[_] if mem.component != null && mem.component.hasTag(GowinKeepBsram) =>
    case mem: Mem[_] if mem.component != null && bsramComponents.contains(mem.component.getName()) =>
    case mem: Mem[_] if mem.initialContent == null && !isDualClock(mem) =>
      val depth = mem.wordCount
      val small = depth <= smallMaxDepth && depth * mem.getWidth <= smallMaxBits
      if (depth <= registersMaxDepth || small)
        mem.addAttribute("syn_ramstyle", "registers")
    case _ =>
  }
}

/** Tag on a component (e.g. a StreamFifo) whose memories must stay in BSRAM:
  * a shallow but very wide FIFO as registers fans its write enable out to
  * every bit (the DDR bridge's 8 x 256-bit read FIFO missed 100 MHz).
  */
object GowinKeepBsram extends SpinalTag

object GowinGenSupport {
  // Same as the DE10 top, minus the texture fill cache: with it the design
  // packs GW5AST-138 to ~96% CLS and does not route. The uncached path
  // (TmuNoTextureCache) renders the same pixels, just slower, and the
  // one-outstanding DDR3 bridge is the bottleneck anyway at bring-up.
  val config: Config = Config
    .voodoo1()
    .copy(
      useFbWriteBuffer = true,
      fbWriteBufferLineWords = 256,
      // Depth writes on their own AXI write port (CoreGowin memAuxW); B115-
      // B121 had the port but nothing drove it.
      separateAuxWriteMem = true,
      useFbReadCache = true,
      useTexFillCache = false,
      // Direct-mapped texture cache in BSRAM (TmuSmallTextureCache): the
      // full fill cache does not fit, but without any cache every texel is a
      // DDR3 round trip. 512 lines (16 KiB per tap lane): with 256, fills
      // still held the texture path ~14% of cycles under GLQuake (B118).
      texSmallCacheLines = 512,
      // Span queue between the walker (which issues the framebuffer prefetch)
      // and the span rasterizer: the prefetch's lead. 16 instead of 4 halved
      // fill waits in trace replay at 120-cycle memory latency.
      spanQueueDepth = 16,
      // More HDMI scanout FIFO margin: refilling only from 1024 left too
      // little once rendering competed for memory (frames blanked
      // mid-frame, B52). 3072-3840 hung the scanout reader for good (B53).
      // 16K-pixel FIFO (~0.65 ms of display): occasional memory stalls
      // outlast 4K pixels (~15% of frames blanked mid-frame on B52/B54).
      hdmiFifoDepth = 16384,
      // The cached scanout reader stalled for good under GLQuake (B53, B58:
      // black HDMI while rendering went on); fetch whole lines instead.
      scanoutLineFetcher = true,
      separateScanoutMem = true,
      hdmiPrefill = 8192,
      hdmiRefillLow = 8192,
      hdmiRefillHigh = 14336,
      // debugBusy/writePathDebug readable over PCIe (0x240/0x250) for live hangs.
      hwDebugRegs = true,
      // GOWIN_TMU_COUNT=2: two TMUs (texture combine, TMU1 texture memory 2 MB
      // above TMU0's); the host shim needs SIM_DUAL_TMU=1 to address TMU1.
      tmuCount = sys.env.get("GOWIN_TMU_COUNT").map(_.toInt).getOrElse(1),
      // TMU sample queue: 8 costs nothing in GLQuake trace replay at 80-cycle
      // texture latency (16: same cycles, 4: +0.5%, 2: +29%), ~1.2k FFs less
      // per TMU than 16.
      texSampleQueueDepth = 8,
      // Two TMUs: cache hints compare tags only (four 512:1 lineValid reads
      // less per TMU; same cycles in GLQuake trace replay).
      texHintValidCheck = sys.env.get("GOWIN_TMU_COUNT").forall(_.toInt < 2)
    )

  def verilog(targetDirectory: String = "rtl/gowin"): SpinalConfig =
    SpinalConfig(
      mode = Verilog,
      targetDirectory = targetDirectory,
      defaultConfigForClockDomains = ClockDomainConfig(
        resetKind = SYNC,
        resetActiveLevel = HIGH
      )
    ).addTransformationPhase(
      new GowinMemStylePhase(
        registersMaxDepth = 4,
        smallMaxDepth = 32,
        // 1024 (B129) moved ~8.6 kbit of FIFOs (TMU sample-request queues,
        // the PCI FIFO, ...) to BSRAM: CLS 83% -> 76% but BSRAM +44 and only
        // 1 of 6 placements closed (BSRAM enables on pipeline-busy / PCI FIFO
        // control paths, core Fmax 42-48 MHz). Kept at 4096.
        smallMaxBits = 4096,
        // Except the TMU's two 16 x 134-bit sample-request queues: ~4.3k FFs
        // as registers, 8 BSRAMs here, and their enables are plain stream
        // handshakes rather than pipeline-busy / PCI FIFO control.
        // The TMU's per-triangle config FIFO (16 x ~136 bits) replaces ~1.1k
        // front-end stage flops per TMU only as BSRAM.
        bsramComponents = Seq("metaCfgFifo") ++
          sys.env.get("GOWIN_TMU_QUEUES_BSRAM").map(_ => Seq("sampleRequestBasePre_translated_fifo", "io_sampleRequest_translated_fifo")).getOrElse(Nil)
      )
    )
}

object CoreGowinGen extends App {
  val targetDirectory = "rtl/gowin"
  val report = GowinGenSupport
    .verilog(targetDirectory)
    .generate(
      CoreGowin(GowinGenSupport.config)
    )
  GenSupport.mirrorRomSidecars(targetDirectory, report.toplevelName)
}

object TangMega138kProGen extends App {
  // Optional first argument overrides the output directory (scratch builds).
  val targetDirectory = args.headOption.getOrElse("rtl/gowin")
  val report = GowinGenSupport
    .verilog(targetDirectory)
    .generate(
      TangMega138kPro(GowinGenSupport.config)
    )
  GenSupport.mirrorRomSidecars(targetDirectory, report.toplevelName)
}
