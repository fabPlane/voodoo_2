package voodoo.gowin

import spinal.core._
import voodoo.{Config, GenSupport}

object GowinGenSupport {
  def verilog(targetDirectory: String = "rtl/gowin"): SpinalConfig =
    SpinalConfig(
      mode = Verilog,
      targetDirectory = targetDirectory,
      inlineRom = true,
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
            // Keep the Console on the direct write path. The small two-slot
            // combiner can strand the auxiliary fast-fill stream after 128
            // native transactions on hardware, even though DDR and the CDC
            // are both idle and ready. The direct path made forward progress;
            // CoreGowin's longer watchdog now gives it enough time to drain.
            useFbWriteBuffer = false,
            fbWriteBufferLineWords = 16,
            // Drop the extra FB readers and wide TMU fill structures.
            useFbReadCache = false,
            useTexFillCache = false,
            texFillRequestWindow = 2,
            // Preserve 640x480 HDMI timing while halving framebuffer reads.
            hdmiScanoutPixelRepeatX = 4,
            // The Tang Console HDMI PLL supplies a 25.2 MHz DMT pixel clock.
            // Do not use the core's 27 MHz CEA 720x480p default here.
            hdmiScanoutDmt640x480 = true
          )
      )
    )
  GenSupport.mirrorRomSidecars(targetDirectory, report.toplevelName)
}
