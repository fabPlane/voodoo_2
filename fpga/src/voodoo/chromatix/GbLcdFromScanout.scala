package voodoo.chromatix

import spinal.core._
import spinal.lib._
import voodoo.hdmi.{ParallelRgbVideo, VideoTiming}

/** Convert parallel-RGB HDMI-style scanout into the ChromatiX Game Boy LCD
  * stream (clkena / RGB555 / mode / on / vsync on hClk).
  *
  * Samples a `outWidth` x `outHeight` window from the active region (default
  * 160x144, the ST7785 panel's native Game Boy frame). When the Voodoo
  * framebuffer is larger, nearest-neighbour downscales into that window so the
  * existing ChromatiX video pipeline (blend · OSD · colour correction · LCD ·
  * UVC) keeps working unchanged.
  */
case class GbLcdFromScanout(
    inTiming: VideoTiming = VideoTiming.dmt640x480,
    outWidth: Int = 160,
    outHeight: Int = 144
) extends Component {
  val io = new Bundle {
    val video = in(ParallelRgbVideo())
    val statusX = in UInt (10 bits)
    val statusY = in UInt (10 bits)
    val statusActive = in Bool ()
    val statusNewFrame = in Bool ()

    val gbClkena = out Bool ()
    val gbData = out Bits (15 bits)
    val gbMode = out Bits (2 bits)
    val gbOn = out Bool ()
    val gbVsync = out Bool ()
  }

  val srcW = inTiming.activeWidth
  val srcH = inTiming.activeHeight

  // Integer scale factors: pick every Nth pixel/line when downscaling.
  val xStep = Math.max(1, srcW / outWidth)
  val yStep = Math.max(1, srcH / outHeight)
  val sampleW = Math.min(outWidth, srcW / xStep)
  val sampleH = Math.min(outHeight, srcH / yStep)

  val x = io.statusX
  val y = io.statusY
  val inWindow =
    io.statusActive &&
      (x < U(sampleW * xStep, 10 bits)) &&
      (y < U(sampleH * yStep, 10 bits)) &&
      (x % U(xStep, 10 bits) === 0) &&
      (y % U(yStep, 10 bits) === 0)

  val r5 = io.video.rgb.r(7 downto 3)
  val g5 = io.video.rgb.g(7 downto 3)
  val b5 = io.video.rgb.b(7 downto 3)

  io.gbClkena := inWindow
  io.gbData := (r5 ## g5 ## b5).asBits
  io.gbOn := True

  // mode[1] is hsync for the ChromatiX memory writer; pulse at end of each
  // sampled line. mode[0] unused by the video pipeline.
  val endOfSampleLine =
    inWindow && (x === U((sampleW - 1) * xStep, 10 bits))
  io.gbMode := Cat(endOfSampleLine, False)
  io.gbVsync := io.statusNewFrame
}
