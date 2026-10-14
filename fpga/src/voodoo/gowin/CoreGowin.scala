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

  // The 5-way fb arbiter appends its input index to the BMB source, so the
  // shared side (and the AXI ID) must be the wider fbMemSharedBmbParams;
  // with fbMemBmbParams the upper source bits were dropped and responses
  // went to the wrong reader (e.g. LFB reads into scanout), wedging the port.
  def fbAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.fbMemSharedBmbParams(c), burstLog2)
  def texAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.texMemBmbParams(c), burstLog2)
}

case class CoreGowin(c: Config, axiBurstLog2: Int = CoreGowin.axiBurstLog2) extends Component {
  val io = new Bundle {
    val bar = slave(AxiLite4(AxiLite4ToBmbBridge.axiConfig(CoreGowin.barAddressWidth)))
    val memFb = master(Axi4(CoreGowin.fbAxiConfig(c, axiBurstLog2)))
    val memTex = master(Axi4(CoreGowin.texAxiConfig(c, axiBurstLog2)))
    val hdmi = master(HdmiScanoutPort(c))
    val hostWedged = out Bool ()
    // Host has a raw-DDR window selected: drain the fb write buffer and drop
    // texture caches so raw reads see, and rendering uses, current memory.
    val rawActive = in Bool ()
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

  // Two levels: the colour-read port (HDMI scanout at top priority inside
  // it, then colour reads and LFB reads) goes before the four rendering
  // ports, which round-robin among themselves. With one 5-way round-robin
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
    inputsParameter = Seq.fill(4)(renderParams),
    outputParameter = renderOutParams,
    lowerFirstPriority = false
  )
  val fbArbiter = BmbArbiter(
    inputsParameter = Seq(renderParams, renderOutParams),
    outputParameter = Core.fbMemSharedBmbParams(c),
    lowerFirstPriority = true
  )
  val fbAxi = BmbToAxi4Mem(Core.fbMemSharedBmbParams(c), axiBurstLog2)
  val texAxi = BmbToAxi4Mem(Core.texMemBmbParams(c), axiBurstLog2, splitPages = false)
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
  fbArbiter.io.inputs(0) <> cut(core.io.fbColorReadMem)
  renderArbiter.io.inputs(0) <> cut(core.io.fbMemWrite)
  renderArbiter.io.inputs(1) <> cut(core.io.fbColorWriteMem)
  renderArbiter.io.inputs(2) <> cut(core.io.fbAuxWriteMem)
  renderArbiter.io.inputs(3) <> cut(core.io.fbAuxReadMem)
  fbArbiter.io.inputs(1) <> renderArbiter.io.output
  fbAxi.io.input <> cut(fbArbiter.io.output)
  texAxi.io.input <> cut(core.io.texMem)
  io.memFb << fbAxi.io.output
  texReloc.io.input << texAxi.io.output
  io.memTex << texReloc.io.output

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
