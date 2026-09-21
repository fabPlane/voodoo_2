package voodoo.gowin

import spinal.core._
import spinal.lib._

/** Periodic ASCII snapshot of Console bring-up, streamed over USB CDC and UART.
  *
  * One line every `period` cycles, paced so the USB TX cross-clock FIFO
  * (256 bytes) can drain. Format:
  *
  * `V2DBG t=XXXXXXXX dbg=X cal=X wed=X usb=X ov=X pl=X inf=X cur=X key=XX pc=XXXXXXXX bar=XXXXXXXX:XXXXXXXX n=XXXX hid=XXXX uart=X/XX rx=XXXX hc=XXXX\n`
  */
object CoreDebugLog {
  val template =
    "V2DBG t=00000000 dbg=0 cal=0 wed=0 usb=0 ov=0 pl=0 inf=0 cur=0 key=00 pc=00000000 bar=00000000:00000000 n=0000 hid=0000 uart=0/00 rx=0000 hc=0000\n"
  val lineBytes = template.length
  def after(label: String): Int = {
    val i = template.indexOf(label)
    require(i >= 0, label)
    i + label.length
  }
  val tickHex = after("t=")
  val debugBit = after("dbg=")
  val calBit = after("cal=")
  val wedBit = after("wed=")
  val usbBit = after("usb=")
  val ovBit = after("ov=")
  val plBit = after("pl=")
  val infBit = after("inf=")
  val curNibble = after("cur=")
  val keyBits = after("key=")
  val pcHex = after("pc=")
  val barAddrHex = after("bar=")
  val barDataHex = barAddrHex + 9
  val barCountHex = after("n=")
  val hidHex = after("hid=")
  val uartBit = after("uart=")
  val uartCmdHex = uartBit + 2
  val uartRxHex = after("rx=")
  val uartHidHex = after("hc=")

  def hexDigit(n: UInt): Bits = {
    val v = n.resize(8 bits)
    Mux(v < U(10), (v + U(0x30, 8 bits)).asBits, (v + U(0x37, 8 bits)).asBits)
  }

  def hexNibble(value: UInt, nibbleFromMsb: Int, width: Int): UInt = {
    val shift = (width - 1 - nibbleFromMsb) * 4
    (value >> shift).resize(4 bits)
  }
}

case class CoreDebugLog(period: Int = 1 << 22, pace: Int = 1024) extends Component {
  val io = new Bundle {
    val calib = in Bool ()
    val wedge = in Bool ()
    val overlay = in Bool ()
    val playing = in Bool ()
    val info = in Bool ()
    val cursor = in UInt (4 bits)
    val pc = in UInt (32 bits)
    val barAddr = in UInt (32 bits)
    val barData = in Bits (32 bits)
    val barCount = in UInt (16 bits)
    val hid = in Bits (16 bits)
    val usbOnline = in Bool ()
    val debugMode = in Bool ()
    val modeKey = in Bool ()
    val resetKey = in Bool ()
    val uartLink = in Bool ()
    val uartLastCmd = in Bits (8 bits)
    val uartRxCount = in UInt (16 bits)
    val uartHidCount = in UInt (16 bits)
    val tx = master(Flow(Bits(8 bits)))
  }

  val tick = Reg(UInt(32 bits)) init (0)
  tick := tick + 1

  val snapTick = Reg(UInt(32 bits)) init (0)
  val snapCal = Reg(Bool()) init (False)
  val snapWed = Reg(Bool()) init (False)
  val snapOv = Reg(Bool()) init (False)
  val snapPl = Reg(Bool()) init (False)
  val snapInf = Reg(Bool()) init (False)
  val snapCur = Reg(UInt(4 bits)) init (0)
  val snapPc = Reg(UInt(32 bits)) init (0)
  val snapBarAddr = Reg(UInt(32 bits)) init (0)
  val snapBarData = Reg(Bits(32 bits)) init (0)
  val snapBarCount = Reg(UInt(16 bits)) init (0)
  val snapHid = Reg(Bits(16 bits)) init (0)
  val snapUsb = Reg(Bool()) init (False)
  val snapDebug = Reg(Bool()) init (False)
  val snapModeKey = Reg(Bool()) init (False)
  val snapResetKey = Reg(Bool()) init (False)
  val snapUartLink = Reg(Bool()) init (False)
  val snapUartCmd = Reg(Bits(8 bits)) init (0)
  val snapUartRx = Reg(UInt(16 bits)) init (0)
  val snapUartHid = Reg(UInt(16 bits)) init (0)

  def capture(): Unit = {
    snapTick := tick
    snapCal := io.calib
    snapWed := io.wedge
    snapOv := io.overlay
    snapPl := io.playing
    snapInf := io.info
    snapCur := io.cursor
    snapPc := io.pc
    snapBarAddr := io.barAddr
    snapBarData := io.barData
    snapBarCount := io.barCount
    snapHid := io.hid
    snapUsb := io.usbOnline
    snapDebug := io.debugMode
    snapModeKey := io.modeKey
    snapResetKey := io.resetKey
    snapUartLink := io.uartLink
    snapUartCmd := io.uartLastCmd
    snapUartRx := io.uartRxCount
    snapUartHid := io.uartHidCount
  }

  val chars = Vec.tabulate(CoreDebugLog.lineBytes) { i =>
    if (i >= CoreDebugLog.tickHex && i < CoreDebugLog.tickHex + 8) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapTick, i - CoreDebugLog.tickHex, 8))
    } else if (i == CoreDebugLog.debugBit) {
      Mux(snapDebug, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.calBit) {
      Mux(snapCal, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.wedBit) {
      Mux(snapWed, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.ovBit) {
      Mux(snapOv, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.plBit) {
      Mux(snapPl, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.infBit) {
      Mux(snapInf, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.curNibble) {
      CoreDebugLog.hexDigit(snapCur)
    } else if (i == CoreDebugLog.keyBits) {
      Mux(snapModeKey, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.keyBits + 1) {
      Mux(snapResetKey, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i >= CoreDebugLog.pcHex && i < CoreDebugLog.pcHex + 8) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapPc, i - CoreDebugLog.pcHex, 8))
    } else if (i >= CoreDebugLog.barAddrHex && i < CoreDebugLog.barAddrHex + 8) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapBarAddr, i - CoreDebugLog.barAddrHex, 8))
    } else if (i >= CoreDebugLog.barDataHex && i < CoreDebugLog.barDataHex + 8) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapBarData.asUInt, i - CoreDebugLog.barDataHex, 8))
    } else if (i >= CoreDebugLog.barCountHex && i < CoreDebugLog.barCountHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapBarCount, i - CoreDebugLog.barCountHex, 4))
    } else if (i >= CoreDebugLog.hidHex && i < CoreDebugLog.hidHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapHid.asUInt, i - CoreDebugLog.hidHex, 4))
    } else if (i == CoreDebugLog.usbBit) {
      Mux(snapUsb, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i == CoreDebugLog.uartBit) {
      Mux(snapUartLink, B(0x31, 8 bits), B(0x30, 8 bits))
    } else if (i >= CoreDebugLog.uartCmdHex && i < CoreDebugLog.uartCmdHex + 2) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapUartCmd.asUInt, i - CoreDebugLog.uartCmdHex, 2))
    } else if (i >= CoreDebugLog.uartRxHex && i < CoreDebugLog.uartRxHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapUartRx, i - CoreDebugLog.uartRxHex, 4))
    } else if (i >= CoreDebugLog.uartHidHex && i < CoreDebugLog.uartHidHex + 4) {
      CoreDebugLog.hexDigit(CoreDebugLog.hexNibble(snapUartHid, i - CoreDebugLog.uartHidHex, 4))
    } else {
      B(CoreDebugLog.template(i).toInt, 8 bits)
    }
  }

  val emitting = RegInit(False)
  val emitIdx = Reg(UInt(log2Up(CoreDebugLog.lineBytes) bits)) init (0)
  val waitCnt = Counter(period)
  val paceCnt = Counter(pace)
  val started = RegInit(False)
  val fire = emitting && paceCnt.willOverflowIfInc

  io.tx.valid := fire
  io.tx.payload := chars(emitIdx)

  when(!started) {
    started := True
    capture()
    emitting := True
    emitIdx := 0
    paceCnt.clear()
  } elsewhen (emitting) {
    paceCnt.increment()
    when(fire) {
      when(emitIdx === CoreDebugLog.lineBytes - 1) {
        emitting := False
        waitCnt.clear()
      } otherwise {
        emitIdx := emitIdx + 1
      }
    }
  } otherwise {
    waitCnt.increment()
    when(waitCnt.willOverflow) {
      capture()
      emitting := True
      emitIdx := 0
      paceCnt.clear()
    }
  }
}
