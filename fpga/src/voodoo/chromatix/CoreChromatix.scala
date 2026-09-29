package voodoo.chromatix

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.amba4.axilite._
import spinal.lib.bus.bmb._
import voodoo.{Config, Core}
import voodoo.artix.{AxiLite4ToBmbBridge, BmbToAxi4Mem, CoreArtix}
import voodoo.gowin.Axi4AddressRelocate
import voodoo.hdmi.HdmiScanoutPort

/** Chromatic wrapper around Core. Same host/memory contracts as CoreGowin
  * (AXI4-Lite BAR + AXI4 framebuffer/texture masters + parallel RGB scanout),
  * with texture addresses relocated into ChromatixAddressMap so a single 8 MiB
  * PSRAM holds both planes.
  *
  * LiteX Instantiates the generated Verilog and bridges the AXI ports onto the
  * ChromatiX PSRAM arbiter / Wishbone fabric.
  */
object CoreChromatix {
  val barAddressWidth = CoreArtix.barAddressWidth
  val axiBurstLog2 = CoreArtix.axiBurstLog2

  def fbAxiConfig(c: Config) = CoreArtix.fbAxiConfig(c)
  def texAxiConfig(c: Config) = CoreArtix.texAxiConfig(c)
}

case class CoreChromatix(c: Config) extends Component {
  val io = new Bundle {
    val bar = slave(AxiLite4(AxiLite4ToBmbBridge.axiConfig(CoreChromatix.barAddressWidth)))
    val memFb = master(Axi4(CoreChromatix.fbAxiConfig(c)))
    val memTex = master(Axi4(CoreChromatix.texAxiConfig(c)))
    val hdmi = master(HdmiScanoutPort(c))
    val hostWedged = out Bool ()
  }

  val core = Core(c)
  val barBridge = AxiLite4ToBmbBridge(CoreChromatix.barAddressWidth, Core.cpuBmbParams)

  val fbArbiter = BmbArbiter(
    inputsParameter = Seq.fill(5)(Core.fbMemBmbParams(c)),
    outputParameter = Core.fbMemBmbParams(c),
    lowerFirstPriority = true
  )
  val fbAxi = BmbToAxi4Mem(Core.fbMemBmbParams(c), CoreChromatix.axiBurstLog2)
  val texAxi = BmbToAxi4Mem(Core.texMemBmbParams(c), CoreChromatix.axiBurstLog2)
  val texReloc = Axi4AddressRelocate(
    CoreChromatix.texAxiConfig(c),
    ChromatixAddressMap.texMemBase
  )

  io.bar <> barBridge.io.axi
  core.io.cpuBus <> barBridge.io.cpuBus
  io.hostWedged := barBridge.io.wedged

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

  core.io.fbBaseAddr := ChromatixAddressMap.fbMemBase
  core.io.flushFbCaches := False
}
