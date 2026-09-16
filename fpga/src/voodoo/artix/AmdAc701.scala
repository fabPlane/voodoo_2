package voodoo.artix

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import voodoo.Config
import voodoo.gowin.{Axi4AddressRelocate, AxiLite4ClockCrossing}

/** AMD Artix-7 AC701 (XC7A200T-2FBG676) fabric.
  *
  * Pinout and IP split come from LiteX `xilinx_ac701`. Vendor IP (AXI PCIe,
  * MIG, ADV7511 I2C) lives in the Verilog board wrapper. This component is the
  * SpinalVoodoo side: PCIe BAR AXI4 -> AXI-Lite -> CoreArtix, two AXI4
  * masters into MIG (textures relocated above the framebuffer), parallel
  * RGB out to the ADV7511.
  *
  * Upstream SpinalVoodoo has no Artix target. CoreArtix is the port of
  * CoreDe10; this board top is what CoreArtix was missing.
  */
case class AmdAc701(c: Config) extends Component {
  noIoPrefix()

  val io = new Bundle {
    val pcieClk = in Bool ()
    val pcieReset = in Bool ()
    val pcieLinkup = in Bool ()
    val bar = slave(Axi4(Axi4ToAxiLite4.axiConfig(CoreArtix.barAddressWidth)))

    val memFb = master(Axi4(CoreArtix.fbAxiConfig(c)))
    val memTex = master(Axi4(CoreArtix.texAxiConfig(c)))
    val ddrCalib = in Bool ()

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
  }

  val core = CoreArtix(c)
  val pcieCd = ClockDomain(
    clock = io.pcieClk,
    reset = io.pcieReset,
    config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH)
  )

  val pcie = new ClockingArea(pcieCd) {
    val toLite = Axi4ToAxiLite4(CoreArtix.barAddressWidth)
    toLite.io.axi <> io.bar
  }

  val barCc = AxiLite4ClockCrossing(
    AxiLite4ToBmbBridge.axiConfig(CoreArtix.barAddressWidth),
    pcieCd,
    ClockDomain.current
  )
  val texReloc = Axi4AddressRelocate(
    CoreArtix.texAxiConfig(c),
    ArtixAddressMap.texMemBase
  )

  pcie.toLite.io.lite <> barCc.io.input
  core.io.bar <> barCc.io.output
  io.memFb << core.io.memFb
  texReloc.io.input << core.io.memTex
  io.memTex << texReloc.io.output

  core.io.hdmi.clock := io.hdmiClk
  core.io.hdmi.reset := io.hdmiReset
  io.hdmiR := core.io.hdmi.video.rgb.r
  io.hdmiG := core.io.hdmi.video.rgb.g
  io.hdmiB := core.io.hdmi.video.rgb.b
  io.hdmiHs := core.io.hdmi.video.hSync
  io.hdmiVs := core.io.hdmi.video.vSync
  io.hdmiDe := core.io.hdmi.video.de

  val beat = CounterFreeRun(1 << 24)
  io.ledLink := io.pcieLinkup
  io.ledCalib := io.ddrCalib
  io.ledWedge := core.io.hostWedged
  io.ledHeartbeat := beat.msb
}
