package voodoo.diff

import java.nio.file.{Files, Paths}

import spinal.core._
import spinal.lib.bus.bmb._
import voodoo._
import voodoo.artix.CoreArtix
import voodoo.bus.{PciFifo, RegisterCategory}
import voodoo.core.{CoreCpuFrontdoor, FramebufferMemSubsystem, PixelPipeline, TextureMemSubsystem}
import voodoo.framebuffer.{
  FramebufferPlaneBuffer,
  FramebufferPlaneDirectReader,
  FramebufferPlaneReader
}
import voodoo.frontend.{AddressRemapper, Lfb, RegisterBank, SwapBuffer}
import voodoo.hdmi.{
  HdmiCdcFramebufferScanout,
  HdmiFramebufferScanout,
  HdmiTestPattern,
  HdmiTestPatternScanout,
  TmdsEncoder,
  TmdsSerializer,
  VideoScanout
}
import voodoo.math.{Fpxx2AFix, FpxxConfig}
import voodoo.pixel.{ColorCombine, Dither, Fog, FramebufferAccess, Write}
import voodoo.raster.{Fastfill, FastfillWordWriter, FastfillWrite, Rasterizer, SpanWalker, TriangleSetup}
import voodoo.texture.{Tmu, TmuNoTextureCache, TmuTexelDecoder, TmuTextureCache}

/** GenOne: elaborate ONE upstream SpinalVoodoo component and write `<outDir>/<Name>_spinal.v`.
  *
  * This is the Spinal half of the differential harness in docs/amaranth-rewrite-plan.md §4.2:
  * the Amaranth port of the same module is generated as `<Name>_am`, both are instantiated side
  * by side, driven with identical stimulus, and their outputs compared.
  *
  * Usage (from `fpga/`):
  * {{{
  *   scala-cli run . --main-class voodoo.diff.GenOne -- StreamWhile
  *   scala-cli run . --main-class voodoo.diff.GenOne -- Rasterizer rtl/diff
  *   scala-cli run . --main-class voodoo.diff.GenOne -- --list
  *   scala-cli run . --main-class voodoo.diff.GenOne -- --all rtl/diff
  * }}}
  *
  * Options:
  *   --list          print the registry (buildable entries and stubs) and exit
  *   --all           generate every buildable entry into `outDir`
  *   --dump-regmeta  elaborate RegisterBank(c) and write its register/decode table as JSON
  *                   (default `rtl/diff/RegisterBank_regmeta.json`) — the ground truth that the
  *                   Amaranth `avoodoo.frontend.regdef` table is cross-checked against
  *   --async-reset   use ASYNC/HIGH reset (upstream `GenSupport.simVerilog` default) instead of
  *                   the SYNC/HIGH default used here
  *
  * Conventions:
  *   - The TOP module is renamed `<Name>_spinal`; sub-module names are left exactly as Spinal
  *     generates them, so a `verilator --top-module <Name>_spinal` lint sees the real hierarchy.
  *   - Reset defaults to SYNC / active HIGH. That matches this repo's Gowin target
  *     (`fpga/src/voodoo/gowin/CoreGowinGen.scala`: `GowinGenSupport.verilog`) and Amaranth's own
  *     default synchronous reset. Upstream's sim config
  *     (`ext/SpinalVoodoo/src/voodoo/GenSupport.scala`: `simVerilog`) leaves Spinal's ASYNC
  *     default in place — pass `--async-reset` to reproduce that.
  *   - `inlineRom = true` so ROM contents land in the Verilog instead of `.bin` sidecars; the
  *     Amaranth side emits inline memory initialisation too.
  *   - Parameters are exactly what `Core(Config.voodoo1())` passes (see Core.scala,
  *     PixelPipeline.scala and the subsystem files); where a component takes only `c`, the
  *     unmodified `Config.voodoo1()` is used.
  */
object GenOne {

  /** The configuration the real core is built with. */
  val c: Config = Config.voodoo1()

  /** CoreSim's own config: Core.scala's config with scanout disabled (CoreSim.scala default,
    * also what `CoreSimGen` produces with no CLI flags).
    */
  val simConfig: Config = Config.voodoo1().copy(enableHdmiScanout = false)

  /** `CoreSimGen` default memory timing (SimBmbMemory.scala defaults). */
  val simTiming: SimMemoryTiming = SimMemoryTiming()

  // ------------------------------------------------------------------------------------------
  // Registry
  // ------------------------------------------------------------------------------------------

  /** One registry row.
    *
    *   - `build == None` means "known module, parameters not wired up yet".
    *   - `prepare` runs BEFORE the Spinal elaboration starts. Spinal keeps global elaboration
    *     state, so anything that itself needs to elaborate (PciFifo's register metadata) has to
    *     happen outside the `generate` closure, otherwise Spinal dies with
    *     "dslClockDomain stack is not empty".
    */
  case class Entry(build: Option[() => Component], note: String, prepare: () => Unit = () => ())

  private def ok(note: String)(build: => Component) = Entry(Some(() => build), note)
  private def okPrepared(note: String)(prepare: => Unit)(build: => Component) =
    Entry(Some(() => build), note, () => prepare)
  private def todo(note: String) = Entry(None, note)

  lazy val registry: Seq[(String, Entry)] = Seq(
    // ---- diff-only wrappers -------------------------------------------------------------
    "StreamWhile" -> ok("wrapper: StreamWhileDiffWrap (StreamWhile is an object, not a Component)")(
      StreamWhileDiffWrap()
    ),
    "StreamWhileDiffWrap" -> ok("alias of StreamWhile")(StreamWhileDiffWrap()),
    "Fpxx" -> ok("wrapper: FpxxDiffWrap (Fpxx is a Bundle, not a Component); float32 as in PciFifo")(
      FpxxDiffWrap(FpxxConfig.float32())
    ),
    "FpxxDiffWrap" -> ok("alias of Fpxx")(FpxxDiffWrap(FpxxConfig.float32())),
    "AfixOps" -> ok("AFix op-by-op reference for avoodoo.lib.fixed (plan 0.2)")(AfixOps(c)),

    // ---- math ---------------------------------------------------------------------------
    "Fpxx2AFix" -> ok("PciFifo.scala:200 — Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1)")(
      new Fpxx2AFix(20 bits, 30 bits, FpxxConfig.float32(), pipeStages = 1)
    ),

    // ---- bus ------------------------------------------------------------------------------
    "PciFifo" -> okPrepared(
      "Core.scala:102 — busParams/categories/floatAliases/commandAddresses from RegisterBank(c).busif"
    )(RegisterBankMeta.prime(c)) {
      val m = RegisterBankMeta.get(c)
      PciFifo(
        busParams = RegisterBank.bmbParams(c),
        categories = m.categories,
        floatAliases = m.floatAliases,
        commandAddresses = m.commandAddresses
      )
    },
    "BmbArbiter" -> ok("CoreSim.scala:38 — 5 framebuffer masters, lowerFirstPriority = true")(
      BmbArbiter(
        inputsParameter = Seq.fill(5)(Core.fbMemBmbParams(c)),
        outputParameter = Core.fbMemBmbParams(c),
        lowerFirstPriority = true
      )
    ),
    "BmbArbiterSingle" -> ok(
      "FramebufferMemSubsystem.scala:45 — makeFbSingleWriteBridge: one PlaneBuffer master " +
        "(sourceWidth 1) widened onto fbMemBmbParams (sourceWidth 4); exercises the " +
        "`portCount == 1` bypass and its weakAssignFrom resizes"
    )(
      BmbArbiter(
        inputsParameter = Seq(FramebufferPlaneBuffer.bmbParams(c)),
        outputParameter = Core.fbMemBmbParams(c),
        lowerFirstPriority = true
      )
    ),
    "BmbSourceRemover" -> ok(
      "De10MemBackend.scala:480 — BmbSourceRemover(Core.fbMemBmbParams(c)); the only " +
        "parameterisation in the tree (:497 is the same shape with texMemBmbParams)"
    )(
      BmbSourceRemover(Core.fbMemBmbParams(c))
    ),
    "BmbAligner" -> ok(
      "CoreArtix.scala:226 — BmbToAxi4Mem(Core.fbMemBmbParams(c), burstLog2 = 8); " +
        "CoreGowin.scala:44 builds the same one"
    )(
      BmbAligner(Core.fbMemBmbParams(c), CoreArtix.axiBurstLog2)
    ),
    "BmbLengthFixer" -> ok(
      "CoreArtix.scala:227 — BmbLengthFixer(aligner.op, burstLog2 = 8), i.e. the second half " +
        "of BmbToAxi4Mem(Core.fbMemBmbParams(c), 8)"
    )(
      BmbLengthFixer(
        BmbAligner.outputParameter(Core.fbMemBmbParams(c).access, CoreArtix.axiBurstLog2)
          .toBmbParameter(),
        CoreArtix.axiBurstLog2
      )
    ),
    "BmbAlignedSpliter" -> ok(
      "FramebufferPlaneReader.scala:98 — BmbAlignedSpliter(internalBmbParams(c), " +
        "1 << c.memBurstLengthWidth). NOTE: needBurstSplitter(Config.voodoo1()) is FALSE " +
        "(((800+1)/2)*4 = 1600 <= 2048), so the shipping core contains no instance; these are " +
        "the parameters it would be built with."
    )(
      BmbAlignedSpliter(
        FramebufferPlaneReader.internalBmbParams(c),
        1 << c.memBurstLengthWidth
      )
    ),
    "BmbContextRemover" -> ok(
      "FramebufferPlaneReader.scala:100 — BmbContextRemover(splitterBmbParams(c), " +
        "pendingMax = issueQueueDepth = 64). Same needBurstSplitter caveat as BmbAlignedSpliter."
    )(
      BmbContextRemover(
        BmbParameter(
          BmbAlignedSpliter.outputParameter(
            FramebufferPlaneReader.internalBmbParams(c).access,
            1 << c.memBurstLengthWidth
          )
        ),
        pendingMax = 64
      )
    ),
    "BmbBusInterface" -> todo(
      "TODO params: not a Component — a spinal.lib.bus.regif adapter built inside RegisterBank. " +
        "Diff it through RegisterBank."
    ),

    // ---- frontend ---------------------------------------------------------------------------
    "AddressRemapper" -> ok(
      "Core.scala:100 — AddressRemapper(RegisterBank.externalBmbParams(c), RegisterBank.bmbParams(c))"
    )(
      AddressRemapper(RegisterBank.externalBmbParams(c), RegisterBank.bmbParams(c))
    ),
    "SwapBuffer" -> ok("Core.scala:119 — SwapBuffer() (formalStrong defaults to false)")(
      SwapBuffer()
    ),
    "RegisterBank" -> ok("Core.scala:101 — RegisterBank(c)")(RegisterBank(c)),
    "Lfb" -> ok("PixelPipeline — Lfb(c)")(Lfb(c)),
    "CoreCpuFrontdoor" -> ok("Core.scala:110 — CoreCpuFrontdoor(c)")(CoreCpuFrontdoor(c)),

    // ---- raster ------------------------------------------------------------------------------
    "TriangleSetup" -> ok("PixelPipeline.scala:221 — TriangleSetup(c)")(TriangleSetup(c)),
    "Rasterizer" -> ok("PixelPipeline.scala:222 — Rasterizer(c) (formalStrong = false)")(
      Rasterizer(c)
    ),
    "SpanWalker" -> ok("Rasterizer.scala — SpanWalker(c, formalStrong = false)")(
      SpanWalker(c, formalStrong = false)
    ),
    "SpanRasterizer" -> ok(
      "Rasterizer.scala:22 — Rasterizer.SpanRasterizer(c), the per-pixel StreamWhile loop. " +
        "Diffable on its own so the raster arithmetic can be proven before SpanWalker lands; " +
        "`Rasterizer` covers it again as a sub-module."
    )(
      Rasterizer.SpanRasterizer(c)
    ),
    "Fastfill" -> ok("PixelPipeline — Fastfill(c)")(Fastfill(c)),
    "FastfillWordWriter" -> ok("Fastfill — FastfillWordWriter(c)")(FastfillWordWriter(c)),
    "FastfillWrite" -> ok("PixelPipeline — FastfillWrite(c)")(FastfillWrite(c)),

    // ---- pixel -------------------------------------------------------------------------------
    "ColorCombine" -> ok("PixelPipeline.scala:225 — ColorCombine(c)")(ColorCombine(c)),
    "Fog" -> ok("PixelPipeline.scala:412 — Fog(c)")(Fog(c)),
    "Dither" -> ok("PixelPipeline.scala:521 — Dither() (no parameters)")(Dither()),
    "FramebufferAccess" -> ok("PixelPipeline.scala:226 — FramebufferAccess(c)")(
      FramebufferAccess(c)
    ),
    "Write" -> ok("PixelPipeline.scala:227 — Write(c) (writeColor / writeAux are the same module)")(
      Write(c)
    ),

    // ---- texture -----------------------------------------------------------------------------
    "TmuTexelDecoder" -> ok("Tmu — TmuTexelDecoder(c)")(TmuTexelDecoder(c)),
    "TmuTextureCache" -> ok("Tmu.scala:597 — TmuTextureCache(c) when c.useTexFillCache")(
      TmuTextureCache(c)
    ),
    "TmuNoTextureCache" -> ok(
      "Tmu.scala:597 — TmuNoTextureCache when !c.useTexFillCache; the module `require`s it, " +
        "so this entry uses Config.voodoo1().copy(useTexFillCache = false)"
    )(
      TmuNoTextureCache(c.copy(useTexFillCache = false))
    ),
    "Tmu" -> ok("PixelPipeline.scala:294 — Tmu(c)")(Tmu(c)),
    "TextureMem" -> todo(
      "TODO params: TextureMem.scala holds only objects/bundles (DownloadConfig, WriteCmd). " +
        "The hardware lives in TextureMemSubsystem."
    ),
    "TextureMemSubsystem" -> ok("Core.scala:112 — TextureMemSubsystem(c)")(TextureMemSubsystem(c)),

    // ---- framebuffer --------------------------------------------------------------------------
    "FramebufferPlaneBuffer" -> ok(
      "FramebufferMemSubsystem.scala:86 — FramebufferPlaneBuffer(c) (formalStrong defaults true)"
    )(FramebufferPlaneBuffer(c)),
    "FramebufferPlaneReader" -> ok("FramebufferMemSubsystem.scala:78 — FramebufferPlaneReader(c)")(
      FramebufferPlaneReader(c)
    ),
    "FramebufferPlaneDirectReader" -> ok(
      "FramebufferMemSubsystem.scala:82 — FramebufferPlaneDirectReader(c)"
    )(FramebufferPlaneDirectReader(c)),
    "FramebufferMemSubsystem" -> ok("Core.scala:111 — FramebufferMemSubsystem(c)")(
      FramebufferMemSubsystem(c)
    ),

    // ---- hdmi scanout --------------------------------------------------------------------------
    "VideoScanout" -> ok("HdmiScanout.scala:133 — VideoScanout() (VideoTiming() default)")(
      VideoScanout()
    ),
    "HdmiTestPattern" -> ok("HdmiScanout.scala:189 — HdmiTestPattern()")(HdmiTestPattern()),
    "HdmiFramebufferScanout" -> ok(
      "HdmiScanout.scala:657 — HdmiFramebufferScanout(Config.voodoo1()), timing cea720x480p"
    )(HdmiFramebufferScanout(c)),
    "HdmiCdcFramebufferScanout" -> ok(
      "Core.scala:113 — HdmiCdcFramebufferScanout(c, pixelRepeatX = c.hdmiScanoutPixelRepeatX)"
    )(HdmiCdcFramebufferScanout(c, pixelRepeatX = c.hdmiScanoutPixelRepeatX)),
    "HdmiTestPatternScanout" -> ok("HdmiScanout.scala:581 — HdmiTestPatternScanout()")(
      HdmiTestPatternScanout()
    ),
    "TmdsEncoder" -> ok("HdmiScanout.scala:522 — TmdsEncoder()")(TmdsEncoder()),
    "TmdsSerializer" -> ok("HdmiScanout.scala:620 — TmdsSerializer()")(TmdsSerializer()),

    // ---- pipeline / top -------------------------------------------------------------------------
    "PixelPipeline" -> ok(
      "Core.scala:110 — PixelPipeline(c), inside DiffHierWrap: CoreDebug's fbAccess / " +
        "fbColorCache* / pipelineBusySources outputs are never driven upstream, which only a " +
        "TOP component may not do"
    )(
      new DiffHierWrap("pixelPipeline", PixelPipeline(c))
    ),
    "SimBmbMemory" -> ok("CoreSim.scala:32 — SimBmbMemory(fbMemBmbParams, 4 MiB, default timing)")(
      SimBmbMemory(Core.fbMemBmbParams(simConfig), 4 * 1024 * 1024, simTiming)
    ),
    "SimBmbMemorySmall" -> ok(
      "SimBmbMemory with the CoreSim parameters but a 64 KiB array instead of 4 MiB — the " +
        "diffable size. Everything except `wordAddrWidth` is depth-independent, and the " +
        "4 MiB version is not practical to lockstep: 1 048 576 words x 4 byte lanes blows up " +
        "both the Amaranth `write_verilog` and the Verilator build. With a 26-bit address and " +
        "a 14-bit word index, random addresses alias — identically on both sides."
    )(
      SimBmbMemory(Core.fbMemBmbParams(simConfig), 64 * 1024, simTiming)
    ),
    "Core" -> ok("CoreSim.scala:29 — Core(c)")(Core(c)),
    "CoreSim" -> ok(
      "CoreSimGen with no flags — CoreSim(Config.voodoo1().copy(enableHdmiScanout = false), SimMemoryTiming())"
    )(CoreSim(simConfig, simTiming))
  )

  lazy val byName: Map[String, Entry] = registry.toMap

  // ------------------------------------------------------------------------------------------
  // Generation
  // ------------------------------------------------------------------------------------------

  def spinalConfig(outDir: String, asyncReset: Boolean): SpinalConfig =
    SpinalConfig(
      mode = Verilog,
      targetDirectory = outDir,
      inlineRom = true,
      defaultConfigForClockDomains = ClockDomainConfig(
        resetKind = if (asyncReset) ASYNC else SYNC,
        resetActiveLevel = HIGH
      )
    )

  /** Elaborate `name` and write `<outDir>/<name>_spinal.v`. Returns the generated file path. */
  def generate(name: String, outDir: String, asyncReset: Boolean = false): String = {
    val entry = byName.getOrElse(
      name,
      throw new IllegalArgumentException(
        s"Unknown module '$name'. Run with --list to see the registry."
      )
    )
    val build = entry.build.getOrElse(
      throw new IllegalArgumentException(s"Module '$name' is a stub: ${entry.note}")
    )
    entry.prepare()
    Files.createDirectories(Paths.get(outDir))
    val topName = s"${name}_spinal"
    val report = spinalConfig(outDir, asyncReset).generate {
      val comp = build()
      comp.setDefinitionName(topName)
      comp
    }
    GenSupport.mirrorRomSidecars(outDir, report.toplevelName)
    s"$outDir/$topName.v"
  }

  def printList(): Unit = {
    val width = registry.map(_._1.length).max
    println(s"GenOne registry (${registry.count(_._2.build.isDefined)} buildable, " +
      s"${registry.count(_._2.build.isEmpty)} stubs):")
    registry.foreach { case (n, e) =>
      val mark = if (e.build.isDefined) "  " else "! "
      println(f"$mark${n.padTo(width, ' ')}  ${e.note}")
    }
  }

  def main(args: Array[String]): Unit = {
    val asyncReset = args.contains("--async-reset")
    val positional = args.filterNot(_.startsWith("--"))

    if (args.contains("--dump-regmeta")) {
      val out = positional.headOption.getOrElse("rtl/diff/RegisterBank_regmeta.json")
      RegisterBankMeta.dumpJson(c, out)
      println(s"[GenOne] wrote $out")
      return
    }

    if (args.contains("--list") || (positional.isEmpty && !args.contains("--all"))) {
      printList()
      if (positional.isEmpty && !args.contains("--all") && !args.contains("--list")) {
        println()
        println(
          "usage: GenOne <ModuleName> [outDir]   " +
            "(or --all [outDir], --list, --dump-regmeta [outFile])"
        )
        sys.exit(1)
      }
      return
    }

    if (args.contains("--all")) {
      val outDir = positional.headOption.getOrElse("rtl/diff")
      val failures = scala.collection.mutable.ArrayBuffer[(String, String)]()
      registry.filter(_._2.build.isDefined).foreach { case (n, _) =>
        println(s"[GenOne] $n")
        try println(s"[GenOne] wrote ${generate(n, outDir, asyncReset)}")
        catch {
          case e: Throwable =>
            println(s"[GenOne] FAILED $n: ${e.getClass.getSimpleName}: ${e.getMessage}")
            failures += ((n, s"${e.getClass.getSimpleName}: ${e.getMessage}"))
        }
      }
      if (failures.nonEmpty) {
        println(s"[GenOne] ${failures.size} failure(s):")
        failures.foreach { case (n, m) => println(s"[GenOne]   $n — $m") }
        sys.exit(1)
      }
      return
    }

    val name = positional(0)
    val outDir = if (positional.length > 1) positional(1) else "rtl/diff"
    println(s"[GenOne] wrote ${generate(name, outDir, asyncReset)}")
  }
}

/** PciFifo's constructor arguments are register-bank metadata (`regBank.busif.getCategories` and
  * friends in Core.scala:102). They are plain Scala values, but they only exist once
  * `RegisterBank` has elaborated. This object elaborates RegisterBank once into a scratch
  * directory and caches the metadata so `GenOne PciFifo` produces a top-level PciFifo with the
  * real address decode table rather than a toy one.
  */
object RegisterBankMeta {

  /** One `busif.slices` field, flattened to plain Scala values. */
  case class FieldDump(
      name: String,
      hi: Int,
      lo: Int,
      access: String,
      resetValue: BigInt,
      doc: String
  )

  /** One `busif.slices` entry (a register), flattened to plain Scala values. */
  case class RegDump(addr: BigInt, name: String, fields: Seq[FieldDump])

  case class Meta(
      categories: Map[BigInt, RegisterCategory],
      floatAliases: Map[BigInt, PciFifo.FloatAliasConfig],
      commandAddresses: Seq[BigInt],
      registers: Seq[RegDump]
  )

  private var cached: Option[Meta] = None

  /** Elaborate RegisterBank now (outside any other Spinal elaboration) and cache the metadata. */
  def prime(c: Config): Unit = { get(c); () }

  def get(c: Config): Meta = cached.getOrElse {
    val scratch = Files.createTempDirectory("voodoo-diff-regbank-meta")
    var captured: Meta = null
    SpinalConfig(mode = Verilog, targetDirectory = scratch.toString).generate {
      val rb = RegisterBank(c)
      captured = Meta(
        categories = rb.busif.getCategories,
        floatAliases = rb.busif.getFloatAliases,
        commandAddresses = rb.busif.getCommandStreamReady.keys.toSeq.sorted,
        registers = rb.busif.slices.toSeq
          .map { slice =>
            RegDump(
              addr = slice.getAddr(),
              name = slice.getName(),
              fields = slice.getFields().map { f =>
                FieldDump(
                  name = f.getName(),
                  hi = f.getSection().max,
                  lo = f.getSection().min,
                  access = f.getAccessType().toString,
                  resetValue = f.getResetValue(),
                  doc = f.getDoc()
                )
              }
            )
          }
          .sortBy(_.addr)
      )
      rb
    }
    cached = Some(captured)
    captured
  }

  private def esc(s: String): String =
    s.flatMap {
      case '"'            => "\\\""
      case '\\'           => "\\\\"
      case '\n'           => "\\n"
      case ch if ch < ' ' => f"\\u${ch.toInt}%04x"
      case ch             => ch.toString
    }

  private def hex(a: BigInt): String = f"0x$a%03x"

  /** Write the elaborated register/decode table as JSON.
    *
    * This is the ground truth for `amaranth/avoodoo/frontend/regdef.py`; the committed copy
    * lives at `amaranth/tests/data/registerbank_regmeta.json` so `pytest` needs no scala-cli.
    * Note the elaboration runs WITHOUT `GenerationFlags.simulation`, so the sim-only debug
    * registers (0x240 debugBusy, 0x250 writePathDebug) are absent here by construction.
    */
  def dumpJson(c: Config, path: String): Unit = {
    val m = get(c)
    val sb = new StringBuilder
    sb ++= "{\n"
    sb ++= "  \"source\": \"fpga/src/voodoo/diff/GenOne.scala --dump-regmeta (RegisterBank(Config.voodoo1()))\",\n"
    sb ++= "  \"simulation\": false,\n"
    sb ++= "  \"note\": \"register/field 'name' comes from regif's SymbolName macro, i.e. the " +
      "enclosing Scala val, so helper-built registers all read 'newRegAtWithCategory' / " +
      "'fifoNoSyncFloatReg'. Logical names live in regdef.py; match on addr + section + " +
      "access + reset + doc.\",\n"

    sb ++= "  \"registers\": [\n"
    sb ++= m.registers
      .map { r =>
        val fs = r.fields
          .map { f =>
            s"""      {"name": "${esc(f.name)}", "hi": ${f.hi}, "lo": ${f.lo}, """ +
              s""""access": "${esc(f.access)}", "reset": ${f.resetValue}, "doc": "${esc(f.doc)}"}"""
          }
          .mkString(",\n")
        s"""    {"addr": ${r.addr}, "addr_hex": "${hex(r.addr)}", "name": "${esc(r.name)}",\n""" +
          s"""     "fields": [\n$fs\n     ]}"""
      }
      .mkString(",\n")
    sb ++= "\n  ],\n"

    sb ++= "  \"categories\": [\n"
    sb ++= m.categories.toSeq
      .sortBy(_._1)
      .map { case (a, cat) =>
        s"""    {"addr": $a, "addr_hex": "${hex(a)}", "fifoBypass": ${cat.fifoBypass}, """ +
          s""""syncRequired": ${cat.syncRequired}}"""
      }
      .mkString(",\n")
    sb ++= "\n  ],\n"

    sb ++= "  \"floatAliases\": [\n"
    sb ++= m.floatAliases.toSeq
      .sortBy(_._1)
      .map { case (a, cfg) =>
        s"""    {"addr": $a, "addr_hex": "${hex(a)}", "targetAddr": ${cfg.targetAddr}, """ +
          s""""targetAddr_hex": "${hex(cfg.targetAddr)}", "intBits": ${cfg.intBits}, """ +
          s""""fracBits": ${cfg.fracBits}, "shift": ${30 - cfg.fracBits}}"""
      }
      .mkString(",\n")
    sb ++= "\n  ],\n"

    sb ++= "  \"commandAddresses\": ["
    sb ++= m.commandAddresses.map(_.toString).mkString(", ")
    sb ++= "]\n}\n"

    val out = Paths.get(path)
    Option(out.getParent).foreach(Files.createDirectories(_))
    Files.write(out, sb.toString.getBytes("UTF-8"))
  }
}
