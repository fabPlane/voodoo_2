package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._

import scala.collection.mutable
import scala.util.Random

/** ScanoutTap -> FIFO -> FrameDmaWriter, as in TangMega138kPro but on one
  * clock and with a small FIFO so overflow is easy to provoke. */
case class FrameDmaHarness(fifoDepth: Int) extends Component {
  val tap = ScanoutTap()
  val fifo = StreamFifo(FrameDma.fifoWord, fifoDepth)
  val dma = FrameDmaWriter()
  val io = new Bundle {
    val r, g, b = in UInt (8 bits)
    val de, vs = in Bool ()
    val tx = master(TlTx())
    val postedCreditOk = in Bool ()
    val regWrite = in Bool ()
    val regAddr = in UInt (8 bits)
    val regWdata = in Bits (32 bits)
    val regRdata = out Bits (32 bits)
  }
  tap.io.r := io.r; tap.io.g := io.g; tap.io.b := io.b
  tap.io.de := io.de; tap.io.vs := io.vs
  tap.io.enable := dma.io.enable
  tap.io.decimate := dma.io.decimate
  fifo.io.push << tap.io.push
  dma.io.words << fifo.io.pop
  dma.io.wordsAvailable := fifo.io.occupancy.resized
  dma.io.overflowPulse := tap.io.overflow
  dma.io.busdev := 0x123
  dma.io.postedCreditOk := io.postedCreditOk
  dma.io.regWrite := io.regWrite
  dma.io.regAddr := io.regAddr
  dma.io.regWaddr := io.regAddr
  dma.io.regWdata := io.regWdata
  io.regRdata := dma.io.regRdata
  io.tx <> dma.io.tx
  dma.io.extraStatus(0) := B(0x11223344L, 32 bits)
  dma.io.extraStatus(1) := B(0x55667788L, 32 bits)
  dma.io.extraStatus(2) := B(0x99aabbccL, 32 bits)
}

class FrameDmaTest extends AnyFunSuite {
  val W = 64; val H = 8 // 512 px = 32 words = 4 TLPs per frame
  val frameWords = W * H / 16
  val base = 0x10000000L; val stride = 0x1000L; val slots = 3
  val statusAddr = base + slots * stride

  class Host(dut: FrameDmaHarness, rnd: Random, waitRate: Double) {
    val mem = mutable.HashMap[Long, Long]() // dword address -> value
    val statuses = mutable.ArrayBuffer[Long]()
    val cd = dut.clockDomain
    val tx = dut.io.tx
    tx.waitReq #= false
    def swap(v: Long) = ((v & 0xff) << 24) | ((v & 0xff00) << 8) | ((v >> 8) & 0xff00) | ((v >> 24) & 0xff)
    def dw(data: BigInt, i: Int): Long = ((data >> (224 - 32 * i)) & 0xffffffffL).toLong
    fork {
      var pkt = mutable.ArrayBuffer[Long]()
      var inPkt = false
      while (true) {
        val stall = rnd.nextDouble() < waitRate
        tx.waitReq #= stall
        cd.waitSampling()
        val valid = tx.valid.toInt
        if (!stall && valid != 0) {
          val sop = tx.sop.toBoolean; val eop = tx.eop.toBoolean
          if (sop) { assert(!inPkt, "sop inside a packet"); pkt.clear(); inPkt = true }
          assert(inPkt, "beat outside a packet")
          val n = Integer.bitCount(valid)
          assert(valid == ((0xff << (8 - n)) & 0xff), f"valid mask $valid%02x not MSB-first")
          val d = tx.data.toBigInt
          for (i <- 0 until n) pkt += dw(d, i)
          if (eop) { inPkt = false; decode(pkt.toSeq) }
        }
      }
    }
    def decode(p: Seq[Long]): Unit = {
      val h0 = p(0); val h1 = p(1); val addr = p(2)
      assert(((h0 >> 24) & 0xff) == 0x40, f"not a 3DW MWr: $h0%08x")
      val len = (h0 & 0x3ff).toInt
      assert(p.size == 3 + len, s"payload ${p.size - 3} dwords, length field $len")
      assert(((h1 >> 16) & 0xffff) == (0x123 << 3), f"requester id ${h1 >> 16}%04x")
      if (len == 4) {
        assert((h1 & 0xff) == 0xff, "status write BE")
        assert(addr == statusAddr)
        assert(p.drop(4).map(swap) == Seq(0x11223344L, 0x55667788L, 0x99aabbccL), "extra status dwords")
        statuses += swap(p(3))
      } else {
        assert(len == 64 && (h1 & 0xff) == 0xff)
        for (i <- 0 until len) mem(addr + 4 * i) = swap(p(3 + i))
      }
    }
    def pixel(slot: Int, x: Int, y: Int): Int = {
      val byte = base + slot * stride + 2L * (y * W + x)
      val v = mem.getOrElse(byte & ~3L, -1L)
      if (v < 0) -1 else ((v >> (8 * (byte & 3))) & 0xffff).toInt
    }
  }

  def rgb(frame: Int, x: Int, y: Int) = ((x * 4 + frame * 8) & 0xff, (y * 16 + frame) & 0xff, (x ^ y ^ frame) & 0xff)
  def to565(c: (Int, Int, Int)) = ((c._1 >> 3) << 11) | ((c._2 >> 2) << 5) | (c._3 >> 3)

  /** Drives frames: vsync pulse, a few blank lines, H lines of W pixels with
    * hblank between. Returns when `frames` frames have been sent. */
  def video(dut: FrameDmaHarness, frames: Int, firstFrame: Int = 0, hblank: Int = 16): Unit = {
    val cd = dut.clockDomain
    for (f <- firstFrame until firstFrame + frames) {
      dut.io.vs #= true; dut.io.de #= false; cd.waitSampling(4)
      dut.io.vs #= false; cd.waitSampling(20)
      for (y <- 0 until H) {
        for (x <- 0 until W) {
          val c = rgb(f, x, y)
          dut.io.de #= true; dut.io.r #= c._1; dut.io.g #= c._2; dut.io.b #= c._3
          cd.waitSampling()
        }
        dut.io.de #= false; cd.waitSampling(hblank)
      }
      cd.waitSampling(40)
    }
  }

  def reg(dut: FrameDmaHarness, addr: Int, value: Long): Unit = {
    dut.io.regAddr #= addr; dut.io.regWdata #= value; dut.io.regWrite #= true
    dut.clockDomain.waitSampling(); dut.io.regWrite #= false
  }
  def rd(dut: FrameDmaHarness, addr: Int): Long = {
    dut.io.regAddr #= addr; sleep(1); dut.io.regRdata.toLong
  }

  def run(seed: Int, fifoDepth: Int, waitRate: Double)(body: (FrameDmaHarness, Host) => Unit): Unit =
    SimConfig.withVerilator.compile(FrameDmaHarness(fifoDepth)).doSim(seed = seed) { dut =>
      val rnd = new Random(seed)
      dut.clockDomain.forkStimulus(10)
      dut.io.de #= false; dut.io.vs #= false; dut.io.regWrite #= false
      dut.io.postedCreditOk #= true
      dut.io.r #= 0; dut.io.g #= 0; dut.io.b #= 0
      val host = new Host(dut, rnd, waitRate)
      dut.clockDomain.waitSampling(5)
      assert(rd(dut, 0x00) == FrameDma.magic)
      reg(dut, 0x08, base); reg(dut, 0x0c, slots); reg(dut, 0x10, frameWords)
      reg(dut, 0x14, statusAddr); reg(dut, 0x18, stride)
      body(dut, host)
    }

  test("frames land in successive slots with correct pixels and status words") {
    run(1, fifoDepth = 64, waitRate = 0.2) { (dut, host) =>
      reg(dut, 0x04, 1)
      video(dut, 5)
      dut.clockDomain.waitSampling(500)
      // Frames 0..4 -> slots 0,1,2,0,1; slots hold frames 3,4,2.
      assert(host.statuses.toSeq == Seq(1L << 8 | 0, 2L << 8 | 1, 3L << 8 | 2, 4L << 8 | 0, 5L << 8 | 1))
      for ((slot, frame) <- Seq(0 -> 3, 1 -> 4, 2 -> 2); y <- 0 until H; x <- 0 until W)
        assert(host.pixel(slot, x, y) == to565(rgb(frame, x, y)), s"slot $slot frame $frame ($x,$y)")
      assert(rd(dut, 0x20) == 5 && rd(dut, 0x24) == 0 && rd(dut, 0x28) == 0 && rd(dut, 0x2c) == 20)
    }
  }

  test("skip field captures every (skip+1)-th frame") {
    run(2, fifoDepth = 64, waitRate = 0.0) { (dut, host) =>
      reg(dut, 0x04, 1 | (2 << 8))
      video(dut, 7)
      dut.clockDomain.waitSampling(500)
      // Frames 0, 3, 6.
      assert(host.statuses.size == 3)
      for (y <- 0 until H; x <- 0 until W) assert(host.pixel(2, x, y) == to565(rgb(6, x, y)))
    }
  }

  test("a stalled link drops whole frames, counts overflows and resyncs onto the next frame") {
    run(3, fifoDepth = 16, waitRate = 0.0) { (dut, host) =>
      reg(dut, 0x04, 1)
      video(dut, 1, firstFrame = 0)
      dut.clockDomain.waitSampling(200)
      // Credits run out for frame 1: the tap overflows mid-frame.
      dut.io.postedCreditOk #= false
      val v = fork { video(dut, 1, firstFrame = 1, hblank = 0) }
      dut.clockDomain.waitSampling(W * H - 64) // FIFO (16) fills well before this
      dut.io.postedCreditOk #= true
      v.join()
      video(dut, 2, firstFrame = 2)
      dut.clockDomain.waitSampling(500)
      assert(rd(dut, 0x24) >= 1, "overflow not counted")
      val st = host.statuses.toSeq
      // Frame 0 in slot 0; frame 1 was cut short, so 2 and 3 restart cleanly and fill the next slots.
      assert(st.head == (1L << 8 | 0))
      val last = st.last; val lastSlot = (last & 0xff).toInt
      for (y <- 0 until H; x <- 0 until W)
        assert(host.pixel(lastSlot, x, y) == to565(rgb(3, x, y)), s"after resync ($x,$y)")
      val prevSlot = (st(st.size - 2) & 0xff).toInt
      for (y <- 0 until H; x <- 0 until W)
        assert(host.pixel(prevSlot, x, y) == to565(rgb(2, x, y)), s"frame 2 ($x,$y)")
    }
  }

  test("disable stops traffic and restarts at slot 0") {
    run(4, fifoDepth = 64, waitRate = 0.1) { (dut, host) =>
      reg(dut, 0x04, 1)
      video(dut, 2)
      dut.clockDomain.waitSampling(300)
      reg(dut, 0x04, 0)
      val n = host.statuses.size
      video(dut, 1, firstFrame = 2)
      dut.clockDomain.waitSampling(300)
      assert(host.statuses.size == n)
      reg(dut, 0x04, 1)
      video(dut, 1, firstFrame = 3)
      dut.clockDomain.waitSampling(300)
      assert((host.statuses.last & 0xff) == 0)
      for (y <- 0 until H; x <- 0 until W) assert(host.pixel(0, x, y) == to565(rgb(3, x, y)))
    }
  }
}
