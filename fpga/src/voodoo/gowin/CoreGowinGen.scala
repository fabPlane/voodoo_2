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
  override def impl(pc: PhaseContext): Unit = pc.walkDeclarations {
    case mem: Mem[_] if mem.initialContent == null =>
      val depth = mem.wordCount
      val small = depth <= smallMaxDepth && depth * mem.getWidth <= smallMaxBits
      if (depth <= registersMaxDepth || small)
        mem.addAttribute("syn_ramstyle", "registers")
    case _ =>
  }
}

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
      useTexFillCache = false
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
