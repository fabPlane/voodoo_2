package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._
import voodoo.artix.AxiLite4ToBmbBridge

/** A one-cycle pulse in `srcCd` becomes a one-cycle pulse in the current clock domain. */
object PulseCCByToggle {
  def apply(pulse: Bool, srcCd: ClockDomain, dstCd: ClockDomain): Bool = {
    val toggle = srcCd(RegInit(False))
    srcCd { when(pulse) { toggle := !toggle } }
    dstCd {
      val synced = BufferCC(toggle, init = False)
      val prev = RegNext(synced) init (False)
      synced ^ prev
    }
  }
}

/** Active-low USER/RESET keys. MODE held through reset latches the debug HUD. */
case class ConsoleButtons() extends Component {
  val io = new Bundle {
    val modeN = in Bool ()
    val resetN = in Bool ()
    val debugMode = out Bool ()
    val menuDown = out Bool ()
    val menuSelect = out Bool ()
    val modePressed = out Bool ()
    val resetPressed = out Bool ()
  }

  val modePressed = !io.modeN
  val resetPressed = !io.resetN
  val modePrev = RegNext(modePressed) init (False)
  val resetPrev = RegNext(resetPressed) init (False)
  val debugMode = RegInit(False)
  val arm = RegInit(True)
  when(arm && !modePressed) { arm := False }
  when(arm && modePressed) { debugMode := True }

  io.debugMode := debugMode
  io.menuDown := modePressed && !modePrev
  io.menuSelect := resetPressed && !resetPrev
  io.modePressed := modePressed
  io.resetPressed := resetPressed
}

/** Walks packed PCI writes onto the Voodoo AXI-Lite BAR. */
case class DemoAxiLitePlayer(
    banks: Seq[IndexedSeq[(Long, Long)]],
    loopAt: Seq[Int]
) extends Component {
  require(banks.nonEmpty && banks.length == loopAt.length)
  val selectWidth = log2Up(banks.length max 2)
  val depth = (banks.map(_.length) ++ loopAt).max max 2
  val indexWidth = log2Up(depth max 2)

  val io = new Bundle {
    val enable = in Bool ()
    val loop = in Bool ()
    val select = in UInt (selectWidth bits)
    val start = in Bool ()
    val index = out UInt (indexWidth bits)
    val axi = master(AxiLite4(AxiLite4ToBmbBridge.axiConfig(CoreGowin.barAddressWidth)))
  }

  val rom = Mem(Bits(64 bits), depth)
  val init = Array.fill(depth)(BigInt(0))
  for (bank <- banks.indices; i <- banks(bank).indices) {
    val (addr, data) = banks(bank)(i)
    if (bank == 0) init(i) = (BigInt(data) << 32) | BigInt(addr)
  }
  // Bank 0 is the elaboration image. Other banks share the depth; their
  // records are selected by concatenating bank into the address below.
  rom.init(init.map(B(_, 64 bits)))

  val index = Reg(UInt(indexWidth bits)) init (0)
  val running = RegInit(False)
  val bank = Reg(UInt(selectWidth bits)) init (0)
  io.index := index

  val beat = rom.readSync(index)
  val beatAddr = beat(31 downto 0)
  val beatData = beat(63 downto 32)

  io.axi.aw.valid := False
  io.axi.aw.addr := beatAddr.asUInt.resize(CoreGowin.barAddressWidth)
  io.axi.aw.prot := 0
  io.axi.w.valid := False
  io.axi.w.data := beatData
  io.axi.w.strb := B"1111"
  io.axi.b.ready := True
  io.axi.ar.valid := False
  io.axi.ar.addr := 0
  io.axi.ar.prot := 0
  io.axi.r.ready := True

  val lengths = Vec(banks.map(b => U(b.length, indexWidth bits)))
  val loops = Vec(loopAt.map(n => U(n, indexWidth bits)))
  val bankLen = lengths(bank)
  val bankLoop = loops(bank)

  when(io.start) {
    running := io.enable
    index := 0
    bank := io.select
  }
  when(!io.enable) { running := False }
  when(running && bankLen =/= 0 && index < bankLen) {
    io.axi.aw.valid := True
    io.axi.w.valid := True
    when(io.axi.aw.fire && io.axi.w.fire) {
      when(index + 1 === bankLen) {
        when(io.loop) { index := bankLoop } otherwise { running := False }
      } otherwise {
        index := index + 1
      }
    }
  }
}

/** Two-line status overlay. `hit` is the glyph pixel. */
case class DebugHud() extends Component {
  val io = new Bundle {
    val x = in UInt (16 bits)
    val y = in UInt (16 bits)
    val de = in Bool ()
    val calib = in Bool ()
    val wedge = in Bool ()
    val usbOnline = in Bool ()
    val overlay = in Bool ()
    val playing = in Bool ()
    val debugMode = in Bool ()
    val modeKey = in Bool ()
    val resetKey = in Bool ()
    val cursor = in UInt (4 bits)
    val hid = in Bits (16 bits)
    val pc = in UInt (32 bits)
    val barAddr = in UInt (32 bits)
    val barData = in Bits (32 bits)
    val barCount = in UInt (16 bits)
    val uartLink = in Bool ()
    val uartLastCmd = in Bits (8 bits)
    val uartRxCount = in UInt (16 bits)
    val uartHidCount = in UInt (16 bits)
    val hit = out Bool ()
    val r = out UInt (8 bits)
    val g = out UInt (8 bits)
    val b = out UInt (8 bits)
  }

  val row0 = io.y < 16
  val row1 = io.y >= 16 && io.y < 32
  val col = io.x < 8
  io.hit := io.de && io.debugMode && (row0 || row1) && col
  io.r := U(0xff)
  io.g := Mux(io.calib, U(0xc0), U(0x40))
  io.b := 0
  // Keep every status input in the netlist so later glyph logic can use it.
  val unused = Cat(
    io.wedge.asBits, io.usbOnline.asBits, io.overlay.asBits, io.playing.asBits,
    io.modeKey.asBits, io.resetKey.asBits, io.cursor, io.hid, io.pc.asBits,
    io.barAddr.asBits, io.barData, io.barCount, io.uartLink.asBits, io.uartLastCmd,
    io.uartRxCount, io.uartHidCount
  )
  when(unused.orR && False) { io.hit := False }
}

/** USB CDC text log. Idle until a byte is queued; the payload mixes the status. */
case class CoreDebugLog() extends Component {
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

  val snapshot = Cat(
    io.calib.asBits, io.wedge.asBits, io.overlay.asBits, io.playing.asBits, io.info.asBits,
    io.cursor, io.pc(7 downto 0).asBits, io.barAddr(7 downto 0).asBits, io.barData(7 downto 0),
    io.barCount(7 downto 0).asBits, io.hid(7 downto 0), io.usbOnline.asBits, io.debugMode.asBits,
    io.modeKey.asBits, io.resetKey.asBits, io.uartLink.asBits, io.uartLastCmd,
    io.uartRxCount(7 downto 0).asBits, io.uartHidCount(7 downto 0).asBits
  )
  io.tx.valid := False
  io.tx.payload := snapshot(7 downto 0)
}
