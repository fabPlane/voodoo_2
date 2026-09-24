package voodoo.gowin

import spinal.core._
import voodoo.{Config, GenSupport}

object GowinGenSupport {
  def verilog(targetDirectory: String = "rtl/gowin"): SpinalConfig =
    SpinalConfig(
      mode = Verilog,
      targetDirectory = targetDirectory,
      defaultConfigForClockDomains = ClockDomainConfig(
        resetKind = SYNC,
        resetActiveLevel = HIGH
      )
    )
}

object CoreGowinGen extends App {
  val targetDirectory = "rtl/gowin"
  val report = GowinGenSupport
    .verilog(targetDirectory)
    .generate(
      CoreGowin(
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

object TangMega138kProGen extends App {
  val targetDirectory = "rtl/gowin"
  val report = GowinGenSupport
    .verilog(targetDirectory)
    .generate(
      TangMega138kPro(
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

object TangConsole138kGen extends App {
  val targetDirectory = "rtl/gowin"
  val report = GowinGenSupport
    .verilog(targetDirectory)
    .generate(
      TangConsole138k(
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
