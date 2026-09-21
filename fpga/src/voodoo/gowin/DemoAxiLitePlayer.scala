package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._
import voodoo.artix.AxiLite4ToBmbBridge

/** Walks a ROM of 32-bit Voodoo PCI writes and issues them as AXI4-Lite
  * transactions. One outstanding write. Pulse `start` to replay a bank.
  * With `loop`, the player wraps to `loopAt` instead of raising `done`.
  */
case class DemoAxiLitePlayer(
    banks: Seq[IndexedSeq[(Long, Long)]],
    loopAt: Seq[Int] = Seq.empty,
    addressWidth: Int = CoreGowin.barAddressWidth
) extends Component {
  require(banks.nonEmpty && banks.forall(_.nonEmpty), "demo player needs at least one write per bank")
  val loopIdx = if (loopAt.isEmpty) banks.map(_ => 0) else loopAt
  require(loopIdx.length == banks.length)

  val commands = banks.flatten
  val count = commands.length
  val indexWidth = log2Up(count + 1)
  val selectWidth = log2Up(banks.length max 2)
  val starts = banks.scanLeft(0)(_ + _.length).dropRight(1)
  val ends = banks.scanLeft(0)(_ + _.length).tail.map(_ - 1)
  val loops = starts.zip(loopIdx).map { case (s, l) => s + l }

  val io = new Bundle {
    val enable = in Bool ()
    val start = in Bool ()
    val loop = in Bool ()
    val select = in UInt (selectWidth bits)
    val axi = master(AxiLite4(AxiLite4ToBmbBridge.axiConfig(addressWidth)))
    val done = out Bool ()
    val index = out UInt (indexWidth bits)
  }

  val mem = Mem(Bits(64 bits), count)
  mem.initBigInt(commands.map { case (addr, data) =>
    (BigInt(addr) << 32) | (BigInt(data) & 0xffffffffL)
  })
  mem.addAttribute("syn_ramstyle", "block_ram")
  mem.addAttribute("rom_style", "block")

  val startVec = Vec(starts.map(s => U(s, indexWidth bits)))
  val endVec = Vec(ends.map(e => U(e, indexWidth bits)))
  val loopVec = Vec(loops.map(l => U(l, indexWidth bits)))
  val index = Reg(UInt(indexWidth bits)) init (0)
  val endIndex = Reg(UInt(indexWidth bits)) init (ends.head)
  val loopIndex = Reg(UInt(indexWidth bits)) init (loops.head)
  val awSent = RegInit(False)
  val wSent = RegInit(False)
  val done = RegInit(True)
  val armed = RegInit(False)
  val sel = io.select.resized

  val word = mem.readAsync(index.resized)
  val addr = word(addressWidth + 31 downto 32).asUInt
  val data = word(31 downto 0)
  val waitingB = awSent && wSent
  val issuing = io.enable && armed && !done && !waitingB

  when(io.start) {
    index := startVec(sel)
    endIndex := endVec(sel)
    loopIndex := loopVec(sel)
    done := False
    armed := True
    awSent := False
    wSent := False
  }

  io.axi.aw.valid := issuing && !awSent
  io.axi.aw.addr := addr
  io.axi.aw.prot := 0
  io.axi.w.valid := issuing && !wSent
  io.axi.w.data := data
  io.axi.w.strb := 0xf
  io.axi.b.ready := waitingB && !done
  io.axi.ar.valid := False
  io.axi.ar.addr := 0
  io.axi.ar.prot := 0
  io.axi.r.ready := True

  when(!io.start) {
    when(io.axi.aw.fire) {
      awSent := True
    }
    when(io.axi.w.fire) {
      wSent := True
    }
    when(waitingB && io.axi.b.valid) {
      awSent := False
      wSent := False
      when(index === endIndex) {
        when(io.loop) {
          index := loopIndex
        } otherwise {
          done := True
        }
      } otherwise {
        index := index + 1
      }
    }
  }

  io.done := done
  io.index := index
}

/** Exclusive 2:1 AXI-Lite master mux. The idle master is held not-ready. */
object AxiLiteMasterMux {
  def apply(sel: Bool, a: AxiLite4, b: AxiLite4, s: AxiLite4): Unit = {
    s.aw.valid := Mux(sel, b.aw.valid, a.aw.valid)
    s.aw.payload := Mux(sel, b.aw.payload, a.aw.payload)
    a.aw.ready := !sel && s.aw.ready
    b.aw.ready := sel && s.aw.ready

    s.w.valid := Mux(sel, b.w.valid, a.w.valid)
    s.w.payload := Mux(sel, b.w.payload, a.w.payload)
    a.w.ready := !sel && s.w.ready
    b.w.ready := sel && s.w.ready

    a.b.valid := !sel && s.b.valid
    b.b.valid := sel && s.b.valid
    a.b.payload := s.b.payload
    b.b.payload := s.b.payload
    s.b.ready := Mux(sel, b.b.ready, a.b.ready)

    s.ar.valid := Mux(sel, b.ar.valid, a.ar.valid)
    s.ar.payload := Mux(sel, b.ar.payload, a.ar.payload)
    a.ar.ready := !sel && s.ar.ready
    b.ar.ready := sel && s.ar.ready

    a.r.valid := !sel && s.r.valid
    b.r.valid := sel && s.r.valid
    a.r.payload := s.r.payload
    b.r.payload := s.r.payload
    s.r.ready := Mux(sel, b.r.ready, a.r.ready)
  }
}
