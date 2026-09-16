package voodoo.artix

import spinal.core._
import voodoo.{Config, GenSupport}

object ArtixGenSupport {
  /** Xilinx-friendly defaults: synchronous active-high reset, as MIG and the PCIe block provide. */
  def verilog(targetDirectory: String = "rtl/artix"): SpinalConfig =
    SpinalConfig(
      mode = Verilog,
      targetDirectory = targetDirectory,
      defaultConfigForClockDomains = ClockDomainConfig(
        resetKind = SYNC,
        resetActiveLevel = HIGH
      )
    )
}

/** Same configuration De10TopGen builds for the board: buffered framebuffer writes, read cache. */
object CoreArtixGen extends App {
  val targetDirectory = "rtl/artix"
  val report = ArtixGenSupport
    .verilog(targetDirectory)
    .generate(
      CoreArtix(
        Config
          .voodoo1()
          .copy(
            useFbWriteBuffer = true,
            fbWriteBufferLineWords = 256,
            useFbReadCache = true
          )
      )
    )
  GenSupport.mirrorRomSidecars(targetDirectory, report.toplevelName)
}
