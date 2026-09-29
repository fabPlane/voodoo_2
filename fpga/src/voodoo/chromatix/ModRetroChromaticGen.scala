package voodoo.chromatix

import spinal.core._
import voodoo.{Config, GenSupport}

object ChromatixGenSupport {
  def verilog(targetDirectory: String = "rtl/chromatix"): SpinalConfig =
    SpinalConfig(
      mode = Verilog,
      targetDirectory = targetDirectory,
      defaultConfigForClockDomains = ClockDomainConfig(
        resetKind = SYNC,
        resetActiveLevel = HIGH
      )
    )
}

object CoreChromatixGen extends App {
  val targetDirectory = "rtl/chromatix"
  val report = ChromatixGenSupport
    .verilog(targetDirectory)
    .generate(
      CoreChromatix(
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

object ModRetroChromaticGen extends App {
  val targetDirectory = "rtl/chromatix"
  val report = ChromatixGenSupport
    .verilog(targetDirectory)
    .generate(
      ModRetroChromatic(
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
