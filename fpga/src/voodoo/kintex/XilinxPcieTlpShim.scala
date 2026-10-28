package voodoo.kintex

import spinal.core._
import spinal.lib._
import voodoo.gowin.PcieTlpBarTarget

/** 7-series Integrated Block for PCI Express (pcie_7x, PG054), 64-bit
  * AXI4-Stream TLP interface, to the Gowin TL beats PcieTlpBarTarget and
  * FrameDmaWriter speak.
  *
  * Both sides carry whole dwords in the same layout (header fields as in the
  * PCIe spec, payload in PCIe byte order: lowest-addressed byte in 31:24),
  * so only the dword order and beat width change:
  *   Xilinx: 64 bits a beat, dword 2k in bits 31:0 and 2k+1 in 63:32 of
  *           beat k, tkeep 0x0F on a last beat holding one dword, tlast.
  *   Gowin:  256 bits a beat, dword i in bits 255-32i:224-32i, valid bit 7-i
  *           set for dword i, sop / eop, wait (beat taken when !wait).
  * The 64-bit interface never straddles two TLPs in a beat (the 128-bit one
  * can), so a TLP always starts at dword 0 of an RX beat.
  *
  * Assumed byte order inside a payload dword (PG054 TLP figures) matches
  * Gowin's. If BAR0+0x254 reads back byte-reversed on hardware (0x3ff0ff0f
  * instead of 0x0ffff03f), set swapPayload.
  */
object XilinxPcieTlp {
  val dataWidth = 64
  val keepWidth = 8
  // m_axis_rx_tuser[8:2]: BAR 0-5 and expansion ROM hit.
  val barHitWidth = 7

  def gowinMask(dwords: UInt): Bits = {
    val m = Bits(PcieTlpBarTarget.tlpValidWidth bits)
    for (i <- 0 until PcieTlpBarTarget.tlpValidWidth)
      m(PcieTlpBarTarget.tlpValidWidth - 1 - i) := dwords > i
    m
  }
}

/** pcie_7x m_axis_rx -> Gowin TL RX. Collects up to four 64-bit beats into
  * one 256-bit beat, emitted when eight dwords are in or on tlast. The
  * emitted beat sits in a register until the target takes it; the input
  * stalls only when a beat completes while that register is still full.
  */
case class XilinxPcieRxToGowin() extends Component {
  import PcieTlpBarTarget._
  val io = new Bundle {
    val rxData = in Bits (XilinxPcieTlp.dataWidth bits)
    val rxKeep = in Bits (XilinxPcieTlp.keepWidth bits)
    val rxLast = in Bool ()
    val rxValid = in Bool ()
    val rxReady = out Bool ()
    val rxBarHit = in Bits (XilinxPcieTlp.barHitWidth bits)

    val tlRxSop = out Bool ()
    val tlRxEop = out Bool ()
    val tlRxValid = out Bits (tlpValidWidth bits)
    val tlRxData = out Bits (tlpDataWidth bits)
    val tlRxBardec = out Bits (barDecWidth bits)
    val tlRxWait = in Bool ()
  }

  val acc = Reg(Vec(Bits(32 bits), 8))
  val count = Reg(UInt(4 bits)) init (0) // dwords collected, always even here
  val atSop = RegInit(True) // the next RX beat starts a TLP
  val collectSop = RegInit(False) // the beat being collected started one
  val bar = Reg(Bits(barDecWidth bits)) init (0)

  val outValid = RegInit(False)
  val outSop = Reg(Bool()) init (False)
  val outEop = Reg(Bool()) init (False)
  val outMask = Reg(Bits(tlpValidWidth bits)) init (0)
  val outData = Reg(Bits(tlpDataWidth bits)) init (0)
  val outBar = Reg(Bits(barDecWidth bits)) init (0)

  val outTaken = outValid && !io.tlRxWait
  val outFree = !outValid || outTaken
  when(outTaken) { outValid := False }

  val lo = io.rxData(31 downto 0)
  val hi = io.rxData(63 downto 32)
  val hiValid = io.rxKeep(4)
  val completes = io.rxLast || count === 6
  io.rxReady := !completes || outFree

  val merged = Vec(Bits(32 bits), 8)
  for (i <- 0 until 8) {
    merged(i) := acc(i)
    when(count === i) { merged(i) := lo }
    when(count + 1 === i) { merged(i) := hi }
  }
  val beatBar = Mux(atSop, io.rxBarHit(barDecWidth - 1 downto 0), bar)
  val beatSop = Mux(count === 0, atSop, collectSop)

  when(io.rxValid && io.rxReady) {
    acc := merged
    when(atSop) { bar := beatBar }
    collectSop := beatSop
    when(completes) {
      outValid := True
      outSop := beatSop
      outEop := io.rxLast
      outMask := XilinxPcieTlp.gowinMask(count + Mux(hiValid, U(2), U(1)))
      outData := Cat(merged.reverse) // Cat puts its first element lowest: dword 0 on top
      outBar := beatBar
      count := 0
      atSop := io.rxLast
    } otherwise {
      count := count + 2
      atSop := False
    }
  }

  io.tlRxSop := outSop
  io.tlRxEop := outEop
  io.tlRxValid := Mux(outValid, outMask, B(0, tlpValidWidth bits))
  io.tlRxData := outData
  io.tlRxBardec := outBar
}

/** Gowin TL TX -> pcie_7x s_axis_tx. One 256-bit beat is buffered and sent
  * as 64-bit beats; the next Gowin beat is taken as the last piece leaves.
  * Gowin TX beats hold contiguous dwords from dword 0 (valid mask from the
  * top bit), and every beat but a TLP's last is full.
  */
case class GowinTxToXilinxPcie() extends Component {
  import PcieTlpBarTarget._
  val io = new Bundle {
    val tlTxSop = in Bool ()
    val tlTxEop = in Bool ()
    val tlTxValid = in Bits (tlpValidWidth bits)
    val tlTxData = in Bits (tlpDataWidth bits)
    val tlTxWait = out Bool ()

    val txData = out Bits (XilinxPcieTlp.dataWidth bits)
    val txKeep = out Bits (XilinxPcieTlp.keepWidth bits)
    val txLast = out Bool ()
    val txValid = out Bool ()
    val txReady = in Bool ()
  }

  val buf = Reg(Vec(Bits(32 bits), 8))
  val bufValid = RegInit(False)
  val bufEop = Reg(Bool()) init (False)
  val bufCount = Reg(UInt(4 bits)) init (0)
  val pos = Reg(UInt(3 bits)) init (0) // next dword pair, in dwords / 2

  val dwPos = pos @@ U"0"
  val lastPiece = dwPos + 2 >= bufCount
  val pieceOut = bufValid && io.txReady
  val draining = pieceOut && lastPiece
  val canLoad = !bufValid || draining
  val inBeat = io.tlTxValid.orR
  io.tlTxWait := !canLoad

  io.txValid := bufValid
  io.txData := buf(dwPos(2 downto 0) + 1) ## buf(dwPos(2 downto 0))
  io.txKeep := Mux(dwPos + 1 < bufCount, B"xFF", B"x0F")
  io.txLast := bufEop && lastPiece

  when(pieceOut) { pos := pos + 1 }
  when(draining) { bufValid := False }
  when(canLoad && inBeat) {
    bufValid := True
    bufEop := io.tlTxEop
    bufCount := CountOne(io.tlTxValid).resized
    pos := 0
    for (i <- 0 until 8) buf(i) := io.tlTxData(255 - 32 * i downto 224 - 32 * i)
  }
}
