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
      // Four write-buffer slots per plane (was 2): a filled run no longer waits
      // for the previous drain. Quake II depth clear 231k -> 154k busy cycles
      // (one word = two pixels a clock), cut-frame replay -7.3%.
      fbWriteBufferCount = 4,
      // Depth writes on their own AXI write port (CoreGowin memAuxW); B115-
      // B121 had the port but nothing drove it.
      separateAuxWriteMem = true,
      useFbReadCache = true,
      useTexFillCache = false,
      // Direct-mapped texture cache in BSRAM (TmuSmallTextureCache): the
      // full fill cache does not fit, but without any cache every texel is a
      // DDR3 round trip. 512 lines (16 KiB per tap lane): with 256, fills
      // still held the texture path ~14% of cycles under GLQuake (B118).
      // 64-byte texture cache lines, 256 of them (same 16 KB): Quake II
      // cut-frame replay -3.1% (misses roughly halve; 32 KB would be -4.2%).
      texSmallCacheLines = 256,
      texSmallLineWords = 16,
      // 8 queued prefetch hints (was 2, dropped when full): -1.3% in replay.
      texHintQueueDepth = 8,
      // Span queue between the walker (which issues the framebuffer prefetch)
      // and the span rasterizer: the prefetch's lead. 16 instead of 4 halved
      // fill waits in trace replay at 120-cycle memory latency.
      spanQueueDepth = sys.env.get("GOWIN_SPAN_QUEUE").map(_.toInt).getOrElse(16),
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
      // Two TMUs run in lockstep after a synchronous fork and need more slack:
      // 8 took +10% cycles there, 12 +0.5%.
      texSampleQueueDepth = if (sys.env.get("GOWIN_TMU_COUNT").exists(_.toInt > 1)) 12 else 8,
      // Two TMUs: cache hints compare tags only (four 512:1 lineValid reads
      // less per TMU; same cycles in GLQuake trace replay).
      texHintValidCheck = sys.env.get("GOWIN_TMU_COUNT").forall(_.toInt < 2),
      // TMU1 holds GLQuake's lightmaps: 256 lines missed twice as often as
      // 512 (multitexture GLQuake replay: 512 lines -7.3% cycles).
      tmu1SmallCacheLines = 256,
      // Prefetch lead: the TMU let 15 samples into flight, ~9 of them in its
      // own pipe, so line-fill hints ran only a few samples ahead of a ~40
      // cycle fill. 47 in flight (48-entry request FIFO): Quake II cut-frame
      // replay -10.6% busy cycles, TMU input stall 285k -> 3k. 8 fill slots
      // gave -13.8% but failed timing on all six placements (B171).
      texMaxOutstanding = 47,
      texFillRequestWindow = 48,
      texSmallFillSlots = 4,
      // Voodoo 2 triangle setup engine (sVx ... sDrawTriCMD); Glide uses it
      // with SST_V2SETUP=1, the old register path keeps working.
      // Fast fills (Q2 / GLQuake clear depth every frame: ~154k core cycles
      // at two pixels a clock) as whole DDR lines.
      fbFillOffload = !sys.env.contains("GOWIN_NO_FILL_OFFLOAD"),
      setupEngine = false, // B175/B176: misses timing at current utilisation (86% CLS); sim only for now
      // Two TMUs: one data copy per tap pair, split into word-parity banks
      // (half the texture cache data BSRAM; same cycles in replay).
      texCachePairedBanks = sys.env.get("GOWIN_TMU_COUNT").exists(_.toInt > 1),
      // GOWIN_FBZ_PER_TRIANGLE: fbzMode travels with the pixels instead of
      // draining the pipeline on each write (GLQuake ~220 per frame).
      fbzModePerTriangle = sys.env.contains("GOWIN_FBZ_PER_TRIANGLE"),
      // fbzMode rewrites of the current value skip the drain (GLQuake ~205 of
      // ~220 a frame; drains in a multitexture frame 75 -> 5).
      skipRedundantFbzSync = true,
      // Deep PCI FIFO (the real chip backs its 64 entries with a memory FIFO
      // in framebuffer RAM). 64 ran empty while the host waited on status
      // reads (B163f: rasterizer idle ~29%); 512 (same 4 BSRAMs) gave B164e
      // +2.7%. With posted BAR writes (B166a) the FIFO still alternated
      // between full (big triangles) and empty (runs of small ones: no
      // triangle 20% of cycles): 2048 entries, ~14 BSRAMs.
      pciFifoDepth = 2048,
      // fbiInit2 bit 4 triple buffering (Voodoo 2); off until software sets it.
      tripleBuffering = true,
      // Two TMUs: no triangleCMD register copy (~2.3k FFs); register writes
      // wait for setup instead, same cycles in GLQuake trace replay.
      triangleSnapshot = sys.env.get("GOWIN_TMU_COUNT").forall(_.toInt < 2),
      // Two TMUs: per-pixel TMU / pixel config as a slot tag (~1.4k FFs).
      pixelConfigSlots = sys.env.get("GOWIN_TMU_COUNT").exists(_.toInt > 1)
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
          // GOWIN_TMU_QUEUES_BSRAM (two-TMU builds): the cache's 16-deep
          // request window and the sample-request queue (12 deep: 2.4k FFs
          // per TMU as registers, B153).
          sys.env.get("GOWIN_TMU_QUEUES_BSRAM")
            .map(_ => Seq("io_sampleRequest_translated_fifo", "sampleRequestBasePre_translated_fifo"))
            .getOrElse(Nil)
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
