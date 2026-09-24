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
  val axiBurstLog2 = CoreArtix.axiBurstLog2

  def fbAxiConfig(c: Config, burstLog2: Int = axiBurstLog2) =
    BmbToAxi4Mem.axiConfig(Core.fbMemBmbParams(c), burstLog2)
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
    val hostRequestTimeout = out Bool ()
    val hostResponseTimeout = out Bool ()
    val pipelineBusySources = out Bits (32 bits)
    val scanoutPrefetchCount = out UInt (16 bits)
    val scanoutReadReqCount = out UInt (16 bits)
    val scanoutReadRspCount = out UInt (16 bits)
    val scanoutCacheDebug = out Bits (64 bits)
    val scanoutCacheReadAddr = out UInt (c.addressWidth)
    val scanoutCacheExpectedAddr = out UInt (c.addressWidth)
    val scanoutCacheRemaining = out UInt (log2Up(c.maxFbDims._1 + 1) bits)
    val scanoutCacheOccupancy = out Bits (32 bits)
    val scanoutFillHits = out UInt (32 bits)
    val scanoutFillMisses = out UInt (32 bits)
    val scanoutFillBurstCount = out UInt (32 bits)
    val scanoutFillBurstBeats = out UInt (32 bits)
    val scanoutFillStallCycles = out UInt (32 bits)
    val scanoutSampleMeta = out Bits (32 bits)
    val scanoutSampleHash = out Bits (32 bits)
    val scanoutSampleLast = out Bits (32 bits)
    val scanoutSampleWords = out Vec(Bits(32 bits), 12)
  }

  val core = Core(c)
  // A full-screen colour+aux clear is hundreds of thousands of 32-bit writes.
  // Even with line-write combining it can legitimately keep a sync command
  // behind the PCI FIFO for seconds. Preserve a watchdog for real lockups,
  // but do not mistake normal framebuffer draining for a wedged host bus.
  val barBridge = AxiLite4ToBmbBridge(
    CoreGowin.barAddressWidth,
    Core.cpuBmbParams,
    timeoutLog2 = 30
  )

  val fbArbiter = BmbArbiter(
    inputsParameter = Seq.fill(5)(Core.fbMemBmbParams(c)),
    outputParameter = Core.fbMemBmbParams(c),
    // The Console SDRAM bridge accepts one transaction at a time.  Fixed
    // priority puts all three write ports ahead of the color/scanout and aux
    // read ports, so a continuously rendering demo can starve HDMI forever.
    // Round-robin retains transaction locking while guaranteeing scanout a
    // turn between sustained write bursts. Fixed priority for scanout did
    // not route (1281 unrouted nets).
    lowerFirstPriority = false
  )
  val fbAxi = BmbToAxi4Mem(Core.fbMemBmbParams(c), axiBurstLog2)
  val texAxi = BmbToAxi4Mem(Core.texMemBmbParams(c), axiBurstLog2)
  val texReloc = Axi4AddressRelocate(
    CoreGowin.texAxiConfig(c, axiBurstLog2),
    GowinAddressMap.texMemBase
  )

  io.bar <> barBridge.io.axi
  core.io.cpuBus <> barBridge.io.cpuBus
  io.hostWedged := barBridge.io.wedged
  io.hostRequestTimeout := barBridge.io.requestTimeoutSeen
  io.hostResponseTimeout := barBridge.io.responseTimeoutSeen
  io.pipelineBusySources := core.io.pipelineBusySources
  io.scanoutPrefetchCount := core.io.scanoutPrefetchCount
  io.scanoutReadReqCount := core.io.scanoutReadReqCount
  io.scanoutReadRspCount := core.io.scanoutReadRspCount
  io.scanoutCacheDebug := core.io.scanoutCacheDebug
  io.scanoutCacheReadAddr := core.io.scanoutCacheReadAddr
  io.scanoutCacheExpectedAddr := core.io.scanoutCacheExpectedAddr
  io.scanoutCacheRemaining := core.io.scanoutCacheRemaining
  io.scanoutCacheOccupancy := core.io.scanoutCacheOccupancy
  io.scanoutFillHits := core.io.scanoutFillHits
  io.scanoutFillMisses := core.io.scanoutFillMisses
  io.scanoutFillBurstCount := core.io.scanoutFillBurstCount
  io.scanoutFillBurstBeats := core.io.scanoutFillBurstBeats
  io.scanoutFillStallCycles := core.io.scanoutFillStallCycles
  io.scanoutSampleMeta := core.io.scanoutSampleMeta
  io.scanoutSampleHash := core.io.scanoutSampleHash
  io.scanoutSampleLast := core.io.scanoutSampleLast
  io.scanoutSampleWords := core.io.scanoutSampleWords

  fbArbiter.io.inputs(0) <> core.io.fbMemWrite
  fbArbiter.io.inputs(1) <> core.io.fbColorWriteMem
  fbArbiter.io.inputs(2) <> core.io.fbAuxWriteMem
  fbArbiter.io.inputs(3) <> core.io.fbColorReadMem
  fbArbiter.io.inputs(4) <> core.io.fbAuxReadMem
  fbAxi.io.input <> fbArbiter.io.output
  texAxi.io.input <> core.io.texMem
  io.memFb << fbAxi.io.output
  texReloc.io.input << texAxi.io.output
  io.memTex << texReloc.io.output

  core.io.statusInputs.vRetrace := core.io.hdmi.status.newFrame
  core.io.statusInputs.memFifoFree := 0xffff
  core.io.statusInputs.pciInterrupt := False
  core.io.hdmi.clock := io.hdmi.clock
  core.io.hdmi.reset := io.hdmi.reset
  io.hdmi.video := core.io.hdmi.video
  io.hdmi.status := core.io.hdmi.status
  io.hdmi.underflow := core.io.hdmi.underflow
  io.hdmi.fifoLevel := core.io.hdmi.fifoLevel

  core.io.fbBaseAddr := GowinAddressMap.fbMemBase
  core.io.flushFbCaches := False
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
