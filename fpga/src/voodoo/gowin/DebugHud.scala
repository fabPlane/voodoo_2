package voodoo.gowin

import spinal.core._
import spinal.lib._

/** Large, camera-readable live diagnostics. Same status as CoreDebugLog,
  * readable when USB CDC never enumerates. Short rows and generous margins
  * keep the text away from monitor overscan and oblique webcam framing.
  */
object DebugHud {
  val cols = 17
  val rows = 10
  val scale = 4
  val xOrigin = 48
  val yOrigin = 16
  val rowPitch = 44
  val width = cols * 8 * scale
  val height = yOrigin + (rows - 1) * rowPitch + 8 * scale

  val lines = Seq(
    "CAL=0 W=0 UF=0",
    "OV=0 PL=0 MODE=0",
    "RST=0 CUR=0",
    "F=0000 S=00",
    "HID=0000",
    "PC=00000000",
    "PF=0000 RQ=0000",
    "RS=0000 RI=0000",
    "RP=0000 RV=0000",
    "RR=0000",
  ).map(_.padTo(cols, ' '))
  require(lines.length == rows)
  require(lines.forall(_.length == cols))

  def digitAfter(line: String, label: String): Int = {
    val i = line.indexOf(label)
    require(i >= 0, label)
    i + label.length
  }

  val calBit = digitAfter(lines(0), "CAL=")
  val wedBit = digitAfter(lines(0), "W=")
  val underflowBit = digitAfter(lines(0), "UF=")
  val ovBit = digitAfter(lines(1), "OV=")
  val plBit = digitAfter(lines(1), "PL=")
  val modeBit = digitAfter(lines(1), "MODE=")
  val resetBit = digitAfter(lines(2), "RST=")
  val curNibble = digitAfter(lines(2), "CUR=")
  val fifoHex = digitAfter(lines(3), "F=")
  val statusHex = digitAfter(lines(3), "S=")
  val hidHex = digitAfter(lines(4), "HID=")
  val pcHex = digitAfter(lines(5), "PC=")
  val prefetchHex = digitAfter(lines(6), "PF=")
  val readReqHex = digitAfter(lines(6), "RQ=")
  val readRspHex = digitAfter(lines(7), "RS=")
  val readIssueHex = digitAfter(lines(7), "RI=")
  val physicalReadHex = digitAfter(lines(8), "RP=")
  val physicalReadRspHex = digitAfter(lines(8), "RV=")
  val cdcReadRspHex = digitAfter(lines(9), "RR=")
}

case class DebugHud() extends Component {
  val io = new Bundle {
    val x = in UInt (10 bits)
    val y = in UInt (10 bits)
    val de = in Bool ()
    val calib = in Bool ()
    val wedge = in Bool ()
    /** Sticky HDMI scanout FIFO underflow flag from the core. */
    val underflow = in Bool ()
    val fifoLevel = in UInt (16 bits)
    val overlay = in Bool ()
    val playing = in Bool ()
    val debugMode = in Bool ()
    val modeKey = in Bool ()
    val resetKey = in Bool ()
    val cursor = in UInt (4 bits)
    val hid = in Bits (16 bits)
    val pc = in UInt (32 bits)
    val scanoutPrefetchCount = in UInt (16 bits)
    val scanoutReadReqCount = in UInt (16 bits)
    val scanoutReadRspCount = in UInt (16 bits)
    val readIssueCount = in UInt (16 bits)
    val physicalReadCount = in UInt (16 bits)
    val physicalReadRspCount = in UInt (16 bits)
    val cdcReadRspCount = in UInt (16 bits)
    val ddrStatus = in Bits (8 bits)
    val uartLink = in Bool ()
    val uartLastCmd = in Bits (8 bits)
    val uartRxCount = in UInt (16 bits)
    val uartHidCount = in UInt (16 bits)
    val hit = out Bool ()
    val r = out UInt (8 bits)
    val g = out UInt (8 bits)
    val b = out UInt (8 bits)
  }

  val cell = 8 * DebugHud.scale
  val cellShift = log2Up(cell)
  val scaleShift = log2Up(DebugHud.scale)

  def bitChar(v: Bool): Bits = Mux(v, B(0x31, 8 bits), B(0x30, 8 bits))

  val chars0 = Vec.tabulate(DebugHud.cols) { i =>
    if (i == DebugHud.calBit) bitChar(io.calib)
    else if (i == DebugHud.wedBit) bitChar(io.wedge)
    else if (i == DebugHud.underflowBit) bitChar(io.underflow)
    else B(DebugHud.lines(0)(i).toInt, 8 bits)
  }
  val chars1 = Vec.tabulate(DebugHud.cols) { i =>
    if (i == DebugHud.ovBit) bitChar(io.overlay)
    else if (i == DebugHud.plBit) bitChar(io.playing)
    else if (i == DebugHud.modeBit) bitChar(io.modeKey)
    else B(DebugHud.lines(1)(i).toInt, 8 bits)
  }
  val chars2 = Vec.tabulate(DebugHud.cols) { i =>
    if (i == DebugHud.resetBit) bitChar(io.resetKey)
    else if (i == DebugHud.curNibble) CoreDebugLog.hexDigit(io.cursor)
    else B(DebugHud.lines(2)(i).toInt, 8 bits)
  }
  val chars3 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.fifoHex && i < DebugHud.fifoHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.fifoLevel, i - DebugHud.fifoHex, 4))
    } else if (i >= DebugHud.statusHex && i < DebugHud.statusHex + 2) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.ddrStatus.asUInt, i - DebugHud.statusHex, 2))
    } else B(DebugHud.lines(3)(i).toInt, 8 bits)
  }
  val chars4 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.hidHex && i < DebugHud.hidHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.hid.asUInt, i - DebugHud.hidHex, 4))
    } else B(DebugHud.lines(4)(i).toInt, 8 bits)
  }
  val chars5 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.pcHex && i < DebugHud.pcHex + 8) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.pc, i - DebugHud.pcHex, 8))
    } else B(DebugHud.lines(5)(i).toInt, 8 bits)
  }
  val chars6 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.prefetchHex && i < DebugHud.prefetchHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.scanoutPrefetchCount, i - DebugHud.prefetchHex, 4))
    } else if (i >= DebugHud.readReqHex && i < DebugHud.readReqHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.scanoutReadReqCount, i - DebugHud.readReqHex, 4))
    } else B(DebugHud.lines(6)(i).toInt, 8 bits)
  }
  val chars7 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.readRspHex && i < DebugHud.readRspHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.scanoutReadRspCount, i - DebugHud.readRspHex, 4))
    } else if (i >= DebugHud.readIssueHex && i < DebugHud.readIssueHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.readIssueCount, i - DebugHud.readIssueHex, 4))
    } else B(DebugHud.lines(7)(i).toInt, 8 bits)
  }
  val chars8 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.physicalReadHex && i < DebugHud.physicalReadHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.physicalReadCount, i - DebugHud.physicalReadHex, 4))
    } else if (i >= DebugHud.physicalReadRspHex && i < DebugHud.physicalReadRspHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.physicalReadRspCount, i - DebugHud.physicalReadRspHex, 4))
    } else B(DebugHud.lines(8)(i).toInt, 8 bits)
  }
  val chars9 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.cdcReadRspHex && i < DebugHud.cdcReadRspHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.cdcReadRspCount, i - DebugHud.cdcReadRspHex, 4))
    } else B(DebugHud.lines(9)(i).toInt, 8 bits)
  }

  val fontMem = Mem(Bits(8 bits), DemoFont.bytes.length)
  fontMem.init(DemoFont.bytes.map(v => B(v, 8 bits)))

  val xInBounds = io.x >= DebugHud.xOrigin && io.x < (DebugHud.xOrigin + DebugHud.width)
  val xRelative = (io.x - DebugHud.xOrigin).resize(10 bits)
  val col = (xRelative >> cellShift).resize(5 bits)
  val safeCol = Mux(col < DebugHud.cols, col, U(0, 5 bits))
  val row = UInt(log2Up(DebugHud.rows) bits)
  val fy0 = UInt(3 bits)
  val yInGlyph = Bool()
  row := 0
  fy0 := 0
  yInGlyph := False
  for (r <- 0 until DebugHud.rows) {
    val top = DebugHud.yOrigin + r * DebugHud.rowPitch
    when(io.y >= top && io.y < (top + cell)) {
      row := r
      fy0 := ((io.y - top) >> scaleShift).resize(3 bits)
      yInGlyph := True
    }
  }
  val inRegion = io.de && xInBounds && yInGlyph
  val ch = Bits(8 bits)
  ch := chars0(safeCol.resized)
  switch(row) {
    is(1) { ch := chars1(safeCol.resized) }
    is(2) { ch := chars2(safeCol.resized) }
    is(3) { ch := chars3(safeCol.resized) }
    is(4) { ch := chars4(safeCol.resized) }
    is(5) { ch := chars5(safeCol.resized) }
    is(6) { ch := chars6(safeCol.resized) }
    is(7) { ch := chars7(safeCol.resized) }
    is(8) { ch := chars8(safeCol.resized) }
    is(9) { ch := chars9(safeCol.resized) }
  }
  val glyph = Mux(
    ch.asUInt >= DemoFont.first && ch.asUInt < (DemoFont.first + DemoFont.count),
    (ch.asUInt - DemoFont.first).resize(8),
    U(0, 8 bits)
  )
  val fx0 = (xRelative >> scaleShift)(2 downto 0)
  val fx1 = RegNext(fx0) init (0)
  val fy1 = RegNext(fy0) init (0)
  val region1 = RegNext(inRegion) init (False)
  val fontRow = fontMem.readSync((glyph @@ fy1).resized)
  val fx2 = RegNext(fx1) init (0)
  val region2 = RegNext(region1) init (False)
  val pixel = fontRow(U(7, 3 bits) - fx2)

  io.hit := region2
  when(region2 && pixel) {
    io.r := 0xff
    io.g := 0xe0
    io.b := 0x40
  } elsewhen (region2) {
    io.r := 0x28
    io.g := 0x10
    io.b := 0x00
  } otherwise {
    io.r := 0
    io.g := 0
    io.b := 0
  }
}
