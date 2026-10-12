package voodoo.gowin

import spinal.core._
import spinal.lib._

/** Live video to the host over PCIe: the HDMI scanout stream is packed to
  * RGB565 and written by DMA (posted MWr TLPs) into a ring of frame slots in
  * host memory, so the host sees exactly what the board displays, in real time.
  *
  * Host side: sw/pcie/voodoo_dma (kernel module, DMA buffer) and
  * sw/pcie/voodoo-stream. Control registers live in BAR0 window 31
  * (PcieTlpBarTarget), see FrameDmaWriter.
  */
object FrameDma {
  val wordBits = 256 // 16 RGB565 pixels, 8 dwords
  val wordsPerTlp = 8 // 256-byte payload (the link's MPS)
  val magic = 0x444d4131L // "DMA1"
  def fifoWord = Bits(wordBits + 1 bits) // sof ## data
}

/** Pixel-clock side: packs active video into 16-pixel words.
  * Captures whole frames starting at vsync (every `decimate`+1-th frame). If
  * the FIFO fills mid-frame the rest of that frame is dropped, then the frame
  * is padded to a whole TLP (8 words) so the DMA side stays TLP-aligned and
  * resyncs on the next start-of-frame word.
  */
case class ScanoutTap() extends Component {
  val io = new Bundle {
    val r, g, b = in UInt (8 bits)
    val de = in Bool ()
    val vs = in Bool ()
    val enable = in Bool ()
    val decimate = in UInt (8 bits)
    val push = master(Stream(FrameDma.fifoWord))
    val overflow = out Bool ()
  }
  val r = RegNext(io.r); val g = RegNext(io.g); val b = RegNext(io.b)
  val de = RegNext(io.de) init (False)
  val vs = RegNext(io.vs) init (False)
  val vsRise = vs && !RegNext(vs).init(False)

  val capturing = RegInit(False)
  val decimCnt = Reg(UInt(8 bits)) init (0)
  val sofPending = RegInit(False)
  val pack = Reg(Bits(FrameDma.wordBits bits)) init (0)
  val packIdx = Reg(UInt(4 bits)) init (0)
  val wordsInFrame = Reg(UInt(3 bits)) init (0) // mod 8
  val padLeft = Reg(UInt(3 bits)) init (0)

  val pixel565 = (r(7 downto 3) ## g(7 downto 2) ## b(7 downto 3))
  val packed = pixel565 ## pack(FrameDma.wordBits - 1 downto 16) // pixel 0 ends up in bits 15:0
  val wordDone = capturing && de && packIdx === 15

  io.push.valid := False
  io.push.payload := (sofPending ## packed)
  io.overflow := False

  when(capturing && de) {
    pack := packed
    packIdx := packIdx + 1
  }
  when(wordDone) {
    io.push.valid := True
    when(io.push.ready) {
      sofPending := False
      wordsInFrame := wordsInFrame + 1
    } otherwise {
      // FIFO full: drop the rest of this frame, pad it to a TLP boundary.
      capturing := False
      io.overflow := True
      padLeft := (U(0, 3 bits) - wordsInFrame)
    }
  }
  when(!capturing && padLeft =/= 0) {
    io.push.valid := True
    io.push.payload := B(0, FrameDma.wordBits + 1 bits)
    when(io.push.ready) { padLeft := padLeft - 1 }
  }
  when(vsRise) {
    packIdx := 0
    wordsInFrame := 0
    when(padLeft === 0 && io.enable && decimCnt === 0) {
      capturing := True
      sofPending := True
      decimCnt := io.decimate
    } otherwise {
      capturing := False
      when(decimCnt =/= 0) { decimCnt := decimCnt - 1 }
    }
  }
  when(!io.enable) { decimCnt := 0 }
}

/** One TLP transmit port (Gowin native TL TX: first dword in bits 255:224,
  * one valid bit per dword MSB-first, beat taken when !wait). */
case class TlTx() extends Bundle with IMasterSlave {
  val sop, eop = Bool()
  val valid = Bits(PcieTlpBarTarget.tlpValidWidth bits)
  val data = Bits(PcieTlpBarTarget.tlpDataWidth bits)
  val waitReq = Bool()
  override def asMaster(): Unit = { out(sop, eop, valid, data); in(waitReq) }
}

/** Two TLP sources onto the TX port, a whole packet at a time; source 0
  * (BAR read completions) wins when both start together. */
case class TlTxArbiter() extends Component {
  val io = new Bundle {
    val inputs = Vec(slave(TlTx()), 2)
    val output = master(TlTx())
  }
  val locked = RegInit(False)
  val owner = Reg(UInt(1 bits)) init (0)
  val req = io.inputs.map(i => i.valid.orR)
  val pick = UInt(1 bits)
  pick := Mux(locked, owner, Mux(req(0), U(0, 1 bits), U(1, 1 bits)))
  val cur = io.inputs(pick)
  io.output.sop := cur.sop
  io.output.eop := cur.eop
  io.output.valid := cur.valid
  io.output.data := cur.data
  for ((src, i) <- io.inputs.zipWithIndex) src.waitReq := io.output.waitReq || pick =/= i
  val beatTaken = cur.valid.orR && !io.output.waitReq
  when(beatTaken) {
    when(cur.eop) { locked := False } otherwise { locked := True; owner := pick }
  }
}

/** TLP-clock side: FIFO words -> 256-byte MWr TLPs into host frame slots.
  *
  * Registers (BAR0 window 31, byte offsets):
  *   0x00 R  magic "DMA1"
  *   0x04 RW ctrl: [0] enable, [15:8] skip frames between captures
  *   0x08 RW buffer base (bus address, 4 KiB aligned, < 4 GiB: 3-DW headers)
  *   0x0C RW slot count
  *   0x10 RW frame size in 32-byte words (640x480 RGB565 = 19200)
  *   0x14 RW status address: after each frame, four dwords: (seq << 8 | slot)
  *           then io.extraStatus(0..2) (board monitors, e.g. supply voltage)
  *   0x18 RW slot stride in bytes
  *   0x20 R  frames written   0x24 R overflows   0x28 R resyncs   0x2C R TLPs
  * Disabling drains and discards the FIFO and restarts at slot 0.
  */
case class FrameDmaWriter() extends Component {
  import FrameDma._
  val io = new Bundle {
    val words = slave(Stream(fifoWord))
    val wordsAvailable = in UInt (16 bits)
    val tx = master(TlTx())
    val postedCreditOk = in Bool ()
    val busdev = in UInt (13 bits)
    val regWrite = in Bool ()
    val regAddr = in UInt (8 bits)
    val regWdata = in Bits (32 bits)
    val regRdata = out Bits (32 bits)
    val overflowPulse = in Bool ()
    val enable = out Bool ()
    val decimate = out UInt (8 bits)
    // Written after the status dword (sampled when the frame completes).
    val extraStatus = in Vec (Bits(32 bits), 3)
  }

  val ctrl = Reg(Bits(32 bits)) init (0)
  val base = Reg(UInt(32 bits)) init (0)
  val slots = Reg(UInt(8 bits)) init (1)
  val frameWords = Reg(UInt(20 bits)) init (19200)
  val statusAddr = Reg(UInt(32 bits)) init (0)
  val stride = Reg(UInt(32 bits)) init (0)
  val framesDone = Reg(UInt(32 bits)) init (0)
  val overflows = Reg(UInt(32 bits)) init (0)
  val resyncs = Reg(UInt(32 bits)) init (0)
  val tlps = Reg(UInt(32 bits)) init (0)
  val enable = ctrl(0)
  io.enable := enable
  io.decimate := ctrl(15 downto 8).asUInt
  when(io.overflowPulse) { overflows := overflows + 1 }

  when(io.regWrite) {
    switch(io.regAddr) {
      is(0x04) { ctrl := io.regWdata }
      is(0x08) { base := io.regWdata.asUInt }
      is(0x0c) { slots := io.regWdata(7 downto 0).asUInt }
      is(0x10) { frameWords := io.regWdata(19 downto 0).asUInt }
      is(0x14) { statusAddr := io.regWdata.asUInt }
      is(0x18) { stride := io.regWdata.asUInt }
    }
  }
  io.regRdata := 0
  switch(io.regAddr) {
    is(0x00) { io.regRdata := B(magic, 32 bits) }
    is(0x04) { io.regRdata := ctrl }
    is(0x08) { io.regRdata := base.asBits }
    is(0x0c) { io.regRdata := slots.resize(32).asBits }
    is(0x10) { io.regRdata := frameWords.resize(32).asBits }
    is(0x14) { io.regRdata := statusAddr.asBits }
    is(0x18) { io.regRdata := stride.asBits }
    is(0x20) { io.regRdata := framesDone.asBits }
    is(0x24) { io.regRdata := overflows.asBits }
    is(0x28) { io.regRdata := resyncs.asBits }
    is(0x2c) { io.regRdata := tlps.asBits }
  }

  // Frame position.
  val slot = Reg(UInt(8 bits)) init (0)
  val slotBase = Reg(UInt(32 bits)) init (0)
  val curAddr = Reg(UInt(32 bits)) init (0)
  val wordsDone = Reg(UInt(20 bits)) init (0)
  val statusPending = RegInit(False)
  val statusValue = Reg(Bits(32 bits)) init (0)
  val statusExtra = Vec(Reg(Bits(32 bits)) init (0), 3)

  def swap(dw: Bits): Bits = dw(7 downto 0) ## dw(15 downto 8) ## dw(23 downto 16) ## dw(31 downto 24)
  def dw(word: Bits, i: Int): Bits = word(32 * i + 31 downto 32 * i)
  val reqId = (io.busdev @@ U(0, 3 bits)).asBits

  val inSof = io.words.payload.msb
  val inData = io.words.payload(wordBits - 1 downto 0)

  object S extends SpinalEnum { val idle, data, status = newElement() }
  val state = RegInit(S.idle)
  val beat = Reg(UInt(4 bits)) init (0)
  val carry = Reg(Bits(96 bits)) init (0) // dwords 5..7 of the previous word, dw5 in 31:0

  io.words.ready := False
  io.tx.sop := False
  io.tx.eop := False
  io.tx.valid := 0
  io.tx.data := 0

  // A start-of-frame word at a TLP start while mid-frame: the tap dropped part
  // of a frame. Restart the current slot so frames stay where the host expects.
  val resync = inSof && wordsDone =/= 0
  val tlpAddr = Mux(resync, slotBase, curAddr)

  def mwrHeader(lengthDw: Int, addr: UInt, firstLastBe: Int): Bits =
    B(0x40000000L | lengthDw, 32 bits) ## (reqId ## B(0, 8 bits) ## B(firstLastBe, 8 bits)) ## (addr(31 downto 2) ## B"00")

  switch(state) {
    is(S.idle) {
      when(!enable) {
        io.words.ready := True // drain
        slot := 0
        slotBase := base
        curAddr := base
        wordsDone := 0
        statusPending := False
      } elsewhen (statusPending && io.postedCreditOk) {
        state := S.status
      } elsewhen (io.wordsAvailable >= wordsPerTlp && io.words.valid && io.postedCreditOk) {
        state := S.data
        beat := 0
      }
    }
    is(S.data) {
      val first = beat === 0
      val last = beat === wordsPerTlp
      val head = Mux(first, mwrHeader(wordsPerTlp * 8, tlpAddr, 0xff), swap(dw(carry, 0)) ## swap(dw(carry, 1)) ## swap(dw(carry, 2)))
      val body = (0 until 5).map(i => swap(dw(inData, i))).reduce(_ ## _)
      io.tx.sop := first
      io.tx.eop := last
      io.tx.valid := Mux(last, B"11100000", B"11111111")
      io.tx.data := Mux(last, head ## B(0, 160 bits), head ## body)
      when(!io.tx.waitReq) {
        when(!last) {
          io.words.ready := True
          carry := inData(255 downto 160)
          when(first && resync) {
            resyncs := resyncs + 1
            curAddr := slotBase
            wordsDone := 0
          }
          beat := beat + 1
        } otherwise {
          tlps := tlps + 1
          val doneNow = wordsDone + wordsPerTlp
          when(doneNow >= frameWords) {
            // Frame complete: report it, move to the next slot.
            statusPending := True
            statusValue := ((framesDone + 1).resize(24) ## slot).asBits
            statusExtra := io.extraStatus
            framesDone := framesDone + 1
            wordsDone := 0
            val wrap = slot + 1 >= slots
            slot := Mux(wrap, U(0, 8 bits), slot + 1)
            slotBase := Mux(wrap, base, slotBase + stride)
            curAddr := Mux(wrap, base, slotBase + stride)
          } otherwise {
            wordsDone := doneNow
            curAddr := curAddr + wordsPerTlp * 32
          }
          state := S.idle
        }
      }
    }
    is(S.status) {
      io.tx.sop := True
      io.tx.eop := True
      io.tx.valid := B"11111110"
      io.tx.data := mwrHeader(4, statusAddr, 0xff) ## swap(statusValue) ##
        swap(statusExtra(0)) ## swap(statusExtra(1)) ## swap(statusExtra(2)) ## B(0, 32 bits)
      when(!io.tx.waitReq) {
        statusPending := False
        state := S.idle
      }
    }
  }
}
