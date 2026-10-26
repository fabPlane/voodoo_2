package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.amba4.axilite._
import spinal.lib.bus.bmb._
import voodoo.{Config, Core}
import voodoo.artix.{AxiLite4ToBmbBridge, BmbToAxi4Mem, CoreArtix}
import voodoo.hdmi.HdmiScanoutPort

/** Gowin wrapper around Core. Same host and memory contracts as CoreArtix:
  * AXI4-Lite for the PCIe BAR, AXI4 for framebuffer and texture traffic into
  * the Gowin DDR3 controller, parallel RGB for the DVI TX IP.
  *
  * Texture AXI addresses are relocated to GowinAddressMap.texMemBase so a
  * single DDR3 controller can hold both colour/depth and textures.
  */
object CoreGowin {
  val barAddressWidth = CoreArtix.barAddressWidth
  // AXI bursts are aligned to 32 bytes: one 256-bit DDR3 native transfer on
  // the Pro Dock. BmbAligner pads every access to this size, and the core's
  // texel / depth / colour accesses are 4 bytes, so with CoreArtix's 256-byte
  // bursts each one cost 64 beats and eight serial DDR commands (~20 us per
  // GLQuake pixel). Long bursts (scanout, write-buffer drains) are split into
  // 32-byte pieces, which the one-native-command-at-a-time bridge did anyway.
  val axiBurstLog2 = 5
  // Transactions in flight per AXI port bridge (BmbToAxi4Mem): the DDR adapter
  // keeps 8 reads in flight; the default 31 cost ~1.3k flip-flops per port.
  val axiPendingMax = 8
  // Fill / texture ports: span prefetches (16 spans in flight per reader) and
  // texture prefetch fills keep more 32-byte pieces outstanding than 8; the
  // fill readers' memory commands waited ~23% of cycles (B110).
  val fillPendingMax = 16
  // Colour / depth fill ports: at 48 MHz a DDR round trip is ~40 core cycles
  // and the fill readers could not issue their next span 23% / 33% of cycles
  // (B124); their span windows are 32 deep now.
  val fbFillPendingMax = 32

  // The 5-way fb arbiter appends its input index to the BMB source, so the
  // shared side (and the AXI ID) must be the wider fbMemSharedBmbParams;
  // with fbMemBmbParams the upper source bits were dropped and responses
  // went to the wrong reader (e.g. LFB reads into scanout), wedging the port.
  def fbAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.fbMemSharedBmbParams(c), burstLog2)
  def texAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.texMemBmbParams(c), burstLog2)
  def scanAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.fbMemBmbParams(c), burstLog2)
  // Depth (aux) fills read through their own AXI port / DDR adapter port,
  // next to the fb port's colour fills (B83: the head returned line waited
  // for the fb R side ~18% of DDR cycles).
  def auxAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.fbMemBmbParams(c), burstLog2)
  // Colour fills + LFB reads (core.io.fbColorReadMem) read through their own
  // port too (B84: colour writes blocked ~41% behind them on the fb port);
  // memFb then only carries render writes.
  def colAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.fbMemBmbParams(c), burstLog2)
  // Depth write-buffer drains (core.io.fbAuxWriteMem) through their own
  // write port: colour and depth writes shared memFb's one 32-bit W channel
  // at the core clock, and the depth writes waited ~20% of cycles (B115).
  def auxWAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.fbMemBmbParams(c), burstLog2)
}

case class CoreGowin(c: Config, axiBurstLog2: Int = CoreGowin.axiBurstLog2) extends Component {
  val io = new Bundle {
    val bar = slave(AxiLite4(AxiLite4ToBmbBridge.axiConfig(CoreGowin.barAddressWidth)))
    val memFb = master(Axi4(CoreGowin.fbAxiConfig(c, axiBurstLog2)))
    val memTex = master(Axi4(CoreGowin.texAxiConfig(c, axiBurstLog2)))
    // HDMI scanout reads (Config.separateScanoutMem).
    val memScan = if (c.separateScanoutMem) master(Axi4(CoreGowin.scanAxiConfig(c, axiBurstLog2))) else null
    val memAux = master(Axi4(CoreGowin.auxAxiConfig(c, axiBurstLog2)))
    val memCol = master(Axi4(CoreGowin.colAxiConfig(c, axiBurstLog2)))
    val memAuxW = master(Axi4(CoreGowin.auxWAxiConfig(c, axiBurstLog2)))
    val hdmi = master(HdmiScanoutPort(c))
    val hostWedged = out Bool ()
    // Host has a raw-DDR window selected: drain the fb write buffer and drop
    // texture caches so raw reads see, and rendering uses, current memory.
    val rawActive = in Bool ()
    // Config.fbFillOffload: fast fill rectangles (DDR byte addresses; the
    // framebuffer sits at 0) and one fillDone cycle per finished one.
    val fill = if (c.fbFillOffload) master Stream (voodoo.raster.FastfillWordWriter.FillCmd(c)) else null
    val fillDone = if (c.fbFillOffload) in Bool () else null
  }

  val core = Core(c)
  // A full-screen colour+aux clear can keep a sync command behind the PCI
  // FIFO for seconds. Keep a watchdog for real lockups, but do not treat
  // normal framebuffer draining as a wedged host bus. Proven on the Console
  // DDR bring-up (console-ddr-297-calib).
  val barBridge = AxiLite4ToBmbBridge(
    CoreGowin.barAddressWidth,
    Core.cpuBmbParams,
    timeoutLog2 = 30
  )

  // Two levels: the colour-read port (colour fills and LFB reads; the HDMI
  // scanout too unless Config.separateScanoutMem) goes before the three
  // write ports, which round-robin among themselves. Depth fills have their
  // own AXI port (memAux) since B84. With one 5-way round-robin
  // the scanout got 1/5 of the turns and, once rendering got fast, ran short
  // (half the frames blanked mid-frame, B50). Fixed priority with writes
  // first deadlocked scanout on the Console; the scanout stops asking when
  // its FIFO is full, so it cannot starve the others.
  private val renderParams = Core.fbMemBmbParams(c)
  private val renderOutParams = BmbParameter(
    addressWidth = renderParams.access.addressWidth,
    dataWidth = renderParams.access.dataWidth,
    sourceWidth = renderParams.access.sourceWidth + 2,
    contextWidth = renderParams.access.contextWidth,
    lengthWidth = renderParams.access.lengthWidth,
    canRead = true,
    canWrite = true,
    alignment = BmbParameter.BurstAlignement.BYTE
  )
  val renderArbiter = BmbArbiter(
    inputsParameter = Seq.fill(2)(renderParams),
    outputParameter = renderOutParams,
    lowerFirstPriority = false
  )
  val fbArbiter = BmbArbiter(
    inputsParameter = Seq(renderParams, renderOutParams),
    outputParameter = Core.fbMemSharedBmbParams(c),
    lowerFirstPriority = true
  )
  // Render writes (colour + depth write buffers, LFB): 16 pieces in flight
  // like the fill ports; with 8 the write ports were blocked 11% / 19% of
  // cycles once reads dropped (B114).
  val fbAxi = BmbToAxi4Mem(Core.fbMemSharedBmbParams(c), axiBurstLog2, pendingMax = CoreGowin.fillPendingMax)
  // Two TMUs x 4 fills x two 32-byte pieces = 16: the old cap exactly
  // (GOWIN_TEX_PENDING).
  val texAxi = BmbToAxi4Mem(Core.texMemBmbParams(c), axiBurstLog2, splitPages = false,
    pendingMax = sys.env.get("GOWIN_TEX_PENDING").map(_.toInt).getOrElse(CoreGowin.fillPendingMax))
  val texReloc = Axi4AddressRelocate(
    CoreGowin.texAxiConfig(c, axiBurstLog2),
    GowinAddressMap.texMemBase
  )

  io.bar <> barBridge.io.axi
  // Host register writes went bridge -> PCI FIFO ingress -> register bank in
  // one ~30 ns chain; register the CPU bus in between.
  core.io.cpuBus <> barBridge.io.cpuBus.pipelined(cmdValid = true, cmdReady = true, rspValid = true)
  io.hostWedged := barBridge.io.wedged

  // Register every BMB hop (cmd valid + ready, rsp valid). Without these the
  // core's internal arbiters, this 5-way arbiter and the AXI aligner formed
  // one ~50 ns combinational valid/ready chain on Gowin.
  private def cut(b: Bmb): Bmb = b.pipelined(cmdValid = true, cmdReady = true, rspValid = true)
  val colAxi = BmbToAxi4Mem(Core.fbMemBmbParams(c), axiBurstLog2, pendingMax = CoreGowin.fbFillPendingMax)
  colAxi.io.input <> cut(core.io.fbColorReadMem)
  io.memCol << colAxi.io.output
  private val unusedFbIn = fbArbiter.io.inputs(0)
  unusedFbIn.cmd.valid := False
  unusedFbIn.cmd.payload.assignFromBits(B(0, widthOf(unusedFbIn.cmd.payload) bits))
  unusedFbIn.rsp.ready := True
  renderArbiter.io.inputs(0) <> cut(core.io.fbMemWrite)
  renderArbiter.io.inputs(1) <> cut(core.io.fbColorWriteMem)
  val auxWAxi = BmbToAxi4Mem(Core.fbMemBmbParams(c), axiBurstLog2, pendingMax = CoreGowin.fillPendingMax)
  auxWAxi.io.input <> cut(core.io.fbAuxWriteMem)
  io.memAuxW << auxWAxi.io.output
  fbArbiter.io.inputs(1) <> renderArbiter.io.output
  fbAxi.io.input <> cut(fbArbiter.io.output)
  texAxi.io.input <> cut(core.io.texMem)
  io.memFb << fbAxi.io.output
  val auxAxi = BmbToAxi4Mem(Core.fbMemBmbParams(c), axiBurstLog2, pendingMax = CoreGowin.fbFillPendingMax)
  auxAxi.io.input <> cut(core.io.fbAuxReadMem)
  io.memAux << auxAxi.io.output
  if (c.separateScanoutMem) {
    val scanAxi = BmbToAxi4Mem(Core.fbMemBmbParams(c), axiBurstLog2, pendingMax = CoreGowin.axiPendingMax)
    scanAxi.io.input <> cut(core.io.fbScanoutMem)
    io.memScan << scanAxi.io.output
  }
  texReloc.io.input << texAxi.io.output
  io.memTex << texReloc.io.output

  if (c.fbFillOffload) {
    io.fill << core.io.fillOffload
    core.io.fillDone := io.fillDone
  }
  core.io.statusInputs.vRetrace := core.io.hdmi.status.frameDone
  core.io.statusInputs.memFifoFree := 0xffff
  core.io.statusInputs.pciInterrupt := False
  core.io.hdmi.clock := io.hdmi.clock
  core.io.hdmi.reset := io.hdmi.reset
  io.hdmi.video := core.io.hdmi.video
  io.hdmi.status := core.io.hdmi.status
  io.hdmi.underflow := core.io.hdmi.underflow
  io.hdmi.fifoLevel := core.io.hdmi.fifoLevel

  core.io.fbBaseAddr := GowinAddressMap.fbMemBase
  core.io.flushFbCaches := io.rawActive
  core.io.invalidateTexCaches := io.rawActive
}

/** Adds a constant to AXI4 AW/AR addresses. Used to park textures above the
  * framebuffer in the same DDR3.
  */
case class Axi4AddressRelocate(config: Axi4Config, offset: BigInt) extends Component {
  val io = new Bundle {
    val input = slave(Axi4(config))
    val output = master(Axi4(config))
  }

  private def relocate(addr: UInt): UInt =
    (addr.resized + U(offset, config.addressWidth bits)).resized

  io.output.aw << io.input.aw
  io.output.aw.addr.allowOverride()
  io.output.aw.addr := relocate(io.input.aw.addr)

  io.output.ar << io.input.ar
  io.output.ar.addr.allowOverride()
  io.output.ar.addr := relocate(io.input.ar.addr)

  io.output.w << io.input.w
  io.input.b << io.output.b
  io.input.r << io.output.r
}
