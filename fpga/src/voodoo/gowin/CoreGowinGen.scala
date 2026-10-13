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
    smallMaxBits: Int
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
      useFbReadCache = true,
      useTexFillCache = false,
      // 8 KiB direct-mapped texture cache in BSRAM (TmuSmallTextureCache):
      // the full fill cache does not fit, but without any cache every texel
      // is a DDR3 round trip.
      texSmallCacheLines = 256,
      // Keep the HDMI scanout FIFO topped up: refilling only from 1024 left
      // too little margin once rendering competed for memory (frames
      // blanked mid-frame, B52).
      hdmiRefillLow = 3072,
      hdmiRefillHigh = 3840,
      // debugBusy/writePathDebug readable over PCIe (0x240/0x250) for live hangs.
      hwDebugRegs = true
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
        smallMaxBits = 4096
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
