package voodoo.gowin

import spinal.core._
import spinal.lib._

/** 40x15 character overlay, 8x8 font pixel-doubled onto 640x480.
  *
  * Lookup is registered (one pixel of delay) so the 25 MHz HDMI domain
  * does not combinationally divide X/Y or walk two ROMs in one cycle.
  */
case class TextOsd(
    screens: Seq[Seq[String]],
    cols: Int = DemoOsd.cols,
    rows: Int = DemoOsd.rows,
    scale: Int = 2,
    yOffset: Int = 80
) extends Component {
  require(screens.nonEmpty)
  require(isPow2(scale) && scale >= 1)
  screens.foreach(s => require(s.length == rows && s.forall(_.length == cols)))

  val screenWidth = log2Up(screens.length max 2)
  val cell = 8 * scale
  val cellShift = log2Up(cell)
  val scaleShift = log2Up(scale)

  val io = new Bundle {
    val x = in UInt (10 bits)
    val y = in UInt (10 bits)
    val de = in Bool ()
    val enable = in Bool ()
    val screen = in UInt (screenWidth bits)
    val r = out UInt (8 bits)
    val g = out UInt (8 bits)
    val b = out UInt (8 bits)
  }

  val packed = screens.flatten.flatMap(_.map(_.toInt & 0xff))
  val charMem = Mem(Bits(8 bits), packed.length)
  charMem.init(packed.map(v => B(v, 8 bits)))
  val fontMem = Mem(Bits(8 bits), DemoFont.bytes.length)
  fontMem.init(DemoFont.bytes.map(v => B(v, 8 bits)))

  val yLocal = io.y - U(yOffset, 10 bits)
  val inY = io.y >= yOffset && io.y < (yOffset + rows * cell)
  val col = (io.x >> cellShift).resize(6 bits)
  val row = (yLocal >> cellShift).resize(4 bits)
  val inX = col < cols
  val inRegion = io.de && io.enable && inX && inY

  val screenIndex = io.screen.resize(log2Up(screens.length max 2) bits)
  val clampedScreen =
    if (screens.length == (1 << screenIndex.getWidth)) {
      screenIndex
    } else {
      val wide = screenIndex.resize(log2Up(screens.length + 1) bits)
      Mux(wide >= screens.length, U(0, screenIndex.getWidth bits), screenIndex)
    }
  val charAddr =
    (clampedScreen * U(cols * rows) + row * U(cols) + col.resized).resized
  val ch = charMem.readSync(charAddr)
  val glyph = Mux(
    ch.asUInt >= DemoFont.first && ch.asUInt < (DemoFont.first + DemoFont.count),
    (ch.asUInt - DemoFont.first).resize(8),
    U(0, 8 bits)
  )
  val fx0 = (io.x >> scaleShift)(2 downto 0)
  val fy0 = (yLocal >> scaleShift)(2 downto 0)
  val fx1 = RegNext(fx0) init (0)
  val fy1 = RegNext(fy0) init (0)
  val region1 = RegNext(inRegion) init (False)
  val enableDe1 = RegNext(io.enable && io.de) init (False)
  val fontRow = fontMem.readSync((glyph @@ fy1).resized)
  val fx2 = RegNext(fx1) init (0)
  val region2 = RegNext(region1) init (False)
  val enableDe2 = RegNext(enableDe1) init (False)
  val pixel = fontRow(U(7, 3 bits) - fx2)

  val r = UInt(8 bits)
  val g = UInt(8 bits)
  val b = UInt(8 bits)
  r := 0
  g := 0
  b := 0
  when(region2 && pixel) {
    r := 0xf0
    g := 0xf4
    b := 0xff
  } elsewhen (enableDe2) {
    r := 0x10
    g := 0x18
    b := 0x48
  }
  io.r := r
  io.g := g
  io.b := b
}
