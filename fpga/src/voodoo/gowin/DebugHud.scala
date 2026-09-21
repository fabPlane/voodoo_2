package voodoo.gowin

import spinal.core._
import spinal.lib._

/** Four live 40-character rows at the top of HDMI. Same status as CoreDebugLog,
  * readable when USB CDC never enumerates.
  */
object DebugHud {
  val cols = 40
  val rows = 4
  val scale = 2
  val height = rows * 8 * scale

  val line0 = "CAL=0 W=0 UF=0 F=0000 OV=0 PL=0".padTo(cols, ' ')
  val line1 = "MODE=0 RST=0 CUR=0 HID=0000".padTo(cols, ' ')
  val line2 = "PC=00000000 PF=0000 RQ=0000 RS=0000".padTo(cols, ' ')
  val line3 = "RI=0000 RP=0000 RV=0000 RR=0000 S=00".padTo(cols, ' ')
  require(line0.length == cols)
  require(line1.length == cols)
  require(line2.length == cols)
  require(line3.length == cols)

  def digitAfter(line: String, label: String): Int = {
    val i = line.indexOf(label)
    require(i >= 0, label)
    i + label.length
  }

  val calBit = digitAfter(line0, "CAL=")
  val wedBit = digitAfter(line0, "W=")
  val underflowBit = digitAfter(line0, "UF=")
  val fifoHex = digitAfter(line0, "F=")
  val ovBit = digitAfter(line0, "OV=")
  val plBit = digitAfter(line0, "PL=")
  val modeBit = digitAfter(line1, "MODE=")
  val resetBit = digitAfter(line1, "RST=")
  val curNibble = digitAfter(line1, "CUR=")
  val hidHex = digitAfter(line1, "HID=")
  val pcHex = digitAfter(line2, "PC=")
  val prefetchHex = digitAfter(line2, "PF=")
  val readReqHex = digitAfter(line2, "RQ=")
  val readRspHex = digitAfter(line2, "RS=")
  val readIssueHex = digitAfter(line3, "RI=")
  val physicalReadHex = digitAfter(line3, "RP=")
  val physicalReadRspHex = digitAfter(line3, "RV=")
  val cdcReadRspHex = digitAfter(line3, "RR=")
  val statusHex = digitAfter(line3, "S=")
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
    else if (i >= DebugHud.fifoHex && i < DebugHud.fifoHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.fifoLevel, i - DebugHud.fifoHex, 4))
    }
    else if (i == DebugHud.ovBit) bitChar(io.overlay)
    else if (i == DebugHud.plBit) bitChar(io.playing)
    else B(DebugHud.line0(i).toInt, 8 bits)
  }
  val chars1 = Vec.tabulate(DebugHud.cols) { i =>
    if (i == DebugHud.modeBit) bitChar(io.modeKey)
    else if (i == DebugHud.resetBit) bitChar(io.resetKey)
    else if (i == DebugHud.curNibble) CoreDebugLog.hexDigit(io.cursor)
    else if (i >= DebugHud.hidHex && i < DebugHud.hidHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.hid.asUInt, i - DebugHud.hidHex, 4))
    } else B(DebugHud.line1(i).toInt, 8 bits)
  }
  val chars2 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.pcHex && i < DebugHud.pcHex + 8) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.pc, i - DebugHud.pcHex, 8))
    } else if (i >= DebugHud.prefetchHex && i < DebugHud.prefetchHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.scanoutPrefetchCount, i - DebugHud.prefetchHex, 4))
    } else if (i >= DebugHud.readReqHex && i < DebugHud.readReqHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.scanoutReadReqCount, i - DebugHud.readReqHex, 4))
    } else if (i >= DebugHud.readRspHex && i < DebugHud.readRspHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.scanoutReadRspCount, i - DebugHud.readRspHex, 4))
    } else {
      B(DebugHud.line2(i).toInt, 8 bits)
    }
  }
  val chars3 = Vec.tabulate(DebugHud.cols) { i =>
    if (i >= DebugHud.readIssueHex && i < DebugHud.readIssueHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.readIssueCount, i - DebugHud.readIssueHex, 4))
    } else if (i >= DebugHud.physicalReadHex && i < DebugHud.physicalReadHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.physicalReadCount, i - DebugHud.physicalReadHex, 4))
    } else if (i >= DebugHud.physicalReadRspHex && i < DebugHud.physicalReadRspHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.physicalReadRspCount, i - DebugHud.physicalReadRspHex, 4))
    } else if (i >= DebugHud.cdcReadRspHex && i < DebugHud.cdcReadRspHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.cdcReadRspCount, i - DebugHud.cdcReadRspHex, 4))
    } else if (i >= DebugHud.statusHex && i < DebugHud.statusHex + 2) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(io.ddrStatus.asUInt, i - DebugHud.statusHex, 2))
    } else {
      B(DebugHud.line3(i).toInt, 8 bits)
    }
  }

  val fontMem = Mem(Bits(8 bits), DemoFont.bytes.length)
  fontMem.init(DemoFont.bytes.map(v => B(v, 8 bits)))

  val col = (io.x >> cellShift).resize(6 bits)
  val row = (io.y >> cellShift).resize(2 bits)
  val inX = col < DebugHud.cols
  val inY = io.y < DebugHud.height
  val inRegion = io.de && inX && inY
  val ch = Bits(8 bits)
  ch := chars0(col.resized)
  switch(row) {
    is(1) { ch := chars1(col.resized) }
    is(2) { ch := chars2(col.resized) }
    is(3) { ch := chars3(col.resized) }
  }
  val glyph = Mux(
    ch.asUInt >= DemoFont.first && ch.asUInt < (DemoFont.first + DemoFont.count),
    (ch.asUInt - DemoFont.first).resize(8),
    U(0, 8 bits)
  )
  val fx0 = (io.x >> scaleShift)(2 downto 0)
  val fy0 = (io.y >> scaleShift)(2 downto 0)
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
