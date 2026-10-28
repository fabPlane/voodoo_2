package voodoo.kintex

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._
import voodoo.gowin.PcieTlpBarTarget

import scala.collection.mutable

/** pcie_7x-format TLPs through both shims and the Gowin BAR target. */
case class XilinxShimHarness() extends Component {
  import XilinxPcieTlp._
  val io = new Bundle {
    val rxData = in Bits (dataWidth bits)
    val rxKeep = in Bits (keepWidth bits)
    val rxLast = in Bool ()
    val rxValid = in Bool ()
    val rxReady = out Bool ()
    val rxBarHit = in Bits (barHitWidth bits)
    val txData = out Bits (dataWidth bits)
    val txKeep = out Bits (keepWidth bits)
    val txLast = out Bool ()
    val txValid = out Bool ()
    val txReady = in Bool ()
    val axi = master(AxiLite4(PcieTlpBarTarget.axiConfig()))
  }
  val rx = XilinxPcieRxToGowin()
  val bar = PcieTlpBarTarget(timeoutLog2 = 10, postedWrites = true)
  val tx = GowinTxToXilinxPcie()
  rx.io.rxData := io.rxData
  rx.io.rxKeep := io.rxKeep
  rx.io.rxLast := io.rxLast
  rx.io.rxValid := io.rxValid
  io.rxReady := rx.io.rxReady
  rx.io.rxBarHit := io.rxBarHit
  bar.io.tlRxSop := rx.io.tlRxSop
  bar.io.tlRxEop := rx.io.tlRxEop
  bar.io.tlRxValid := rx.io.tlRxValid
  bar.io.tlRxData := rx.io.tlRxData
  bar.io.tlRxBardec := rx.io.tlRxBardec
  rx.io.tlRxWait := bar.io.tlRxWait
  tx.io.tlTxSop := bar.io.tlTxSop
  tx.io.tlTxEop := bar.io.tlTxEop
  tx.io.tlTxValid := bar.io.tlTxValid
  tx.io.tlTxData := bar.io.tlTxData
  bar.io.tlTxWait := tx.io.tlTxWait
  io.txData := tx.io.txData
  io.txKeep := tx.io.txKeep
  io.txLast := tx.io.txLast
  io.txValid := tx.io.txValid
  tx.io.txReady := io.txReady
  bar.io.tlTxCreditsCpl := B"32'x80000000"
  bar.io.tlCfgBusdev := U(0x0108, 13 bits)
  bar.io.localRdata := 0
  io.axi <> bar.io.axi
}

class XilinxPcieTlpShimTest extends AnyFunSuite {
  private lazy val compiled = SimConfig.withVerilator.compile(XilinxShimHarness())

  private def pcie(v: Long): Long =
    ((v & 0xffL) << 24) | ((v & 0xff00L) << 8) | ((v >> 8) & 0xff00L) | ((v >> 24) & 0xffL)

  private def init(dut: XilinxShimHarness): Unit = {
    dut.io.rxValid #= false
    dut.io.rxData #= 0
    dut.io.rxKeep #= 0
    dut.io.rxLast #= false
    dut.io.rxBarHit #= 0
    dut.io.txReady #= true
    dut.io.axi.aw.ready #= true
    dut.io.axi.w.ready #= true
    dut.io.axi.b.valid #= false
    dut.io.axi.b.resp #= 0
    dut.io.axi.ar.ready #= false
    dut.io.axi.r.valid #= false
    dut.io.axi.r.resp #= 0
    dut.io.axi.r.data #= 0
  }

  /** Sends one TLP as 64-bit beats (dword 2k low, 2k+1 high), honouring tready. */
  private def sendTlp(dut: XilinxShimHarness, dws: Seq[Long]): Unit = {
    val beats = dws.grouped(2).toSeq
    for ((b, k) <- beats.zipWithIndex) {
      val lo = BigInt(b(0) & 0xffffffffL)
      val hi = if (b.length > 1) BigInt(b(1) & 0xffffffffL) else BigInt(0)
      dut.io.rxData #= (hi << 32) | lo
      dut.io.rxKeep #= (if (b.length > 1) 0xff else 0x0f)
      dut.io.rxLast #= k == beats.length - 1
      dut.io.rxBarHit #= 1
      dut.io.rxValid #= true
      dut.clockDomain.waitSamplingWhere(dut.io.rxReady.toBoolean)
    }
    dut.io.rxValid #= false
    dut.io.rxLast #= false
  }

  /** Collects AXI-Lite writes (address, data) and answers B. */
  private def writeMonitor(dut: XilinxShimHarness): mutable.ArrayBuffer[(Long, Long)] = {
    val writes = mutable.ArrayBuffer[(Long, Long)]()
    var pendingB = 0
    dut.clockDomain.onSamplings {
      if (dut.io.axi.aw.valid.toBoolean && dut.io.axi.w.valid.toBoolean) {
        writes += ((dut.io.axi.aw.addr.toLong & 0xffffffL, dut.io.axi.w.data.toLong & 0xffffffffL))
        pendingB += 1
      }
      if (dut.io.axi.b.valid.toBoolean) pendingB -= 1
      dut.io.axi.b.valid #= pendingB > 0
    }
    writes
  }

  test("3DW memory write in pcie_7x beats becomes an AXI-Lite write") {
    compiled.doSim("x-write") { dut =>
      dut.clockDomain.forkStimulus(10)
      SimTimeout(20000)
      init(dut)
      val writes = writeMonitor(dut)
      dut.clockDomain.waitSampling(2)
      sendTlp(dut, Seq(0x40000001L, 0x0000000fL, 0x100L, pcie(0xdeadbeefL)))
      dut.clockDomain.waitSampling(20)
      assert(writes == Seq((0x100L, 0xdeadbeefL)))
    }
  }

  test("write-combined 9-dword TLP spans two Gowin beats, every dword written") {
    compiled.doSim("x-burst") { dut =>
      dut.clockDomain.forkStimulus(10)
      SimTimeout(20000)
      init(dut)
      val writes = writeMonitor(dut)
      dut.clockDomain.waitSampling(2)
      val payload = (0 until 6).map(i => 0x11110000L + i)
      // length 6, first BE f, last BE f
      sendTlp(dut, Seq(0x40000006L, 0x000000ffL, 0x200L) ++ payload.map(pcie))
      // and a second TLP straight behind it
      sendTlp(dut, Seq(0x40000001L, 0x0000000fL, 0x300L, pcie(0x5a5a5a5aL)))
      dut.clockDomain.waitSampling(60)
      val expect = payload.zipWithIndex.map { case (v, i) => (0x200L + 4 * i, v) } :+ ((0x300L, 0x5a5a5a5aL))
      assert(writes == expect, s"got ${writes.map { case (a, d) => f"$a%x=$d%08x" }}")
    }
  }

  test("memory read returns a CplD as two 64-bit beats with tlast") {
    compiled.doSim("x-read") { dut =>
      dut.clockDomain.forkStimulus(10)
      SimTimeout(20000)
      init(dut)
      dut.clockDomain.waitSampling(2)
      // 3 dwords: second beat carries one dword (tkeep 0x0F)
      sendTlp(dut, Seq(0x00000001L, 0x0102070fL, 0x200L))
      dut.clockDomain.waitSamplingWhere(dut.io.axi.ar.valid.toBoolean)
      assert((dut.io.axi.ar.addr.toLong & 0xffffffL) == 0x200L)
      dut.io.axi.ar.ready #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.ar.ready #= false
      dut.io.axi.r.valid #= true
      dut.io.axi.r.data #= 0xc0ffeeL
      dut.clockDomain.waitSampling()
      dut.io.axi.r.valid #= false

      // Record beats as the clock edge takes them; stall the first one a
      // few cycles to check that it is held.
      val beats = mutable.ArrayBuffer[(BigInt, Int, Boolean)]()
      dut.clockDomain.onSamplings {
        if (dut.io.txValid.toBoolean && dut.io.txReady.toBoolean)
          beats += ((dut.io.txData.toBigInt, dut.io.txKeep.toInt, dut.io.txLast.toBoolean))
      }
      dut.io.txReady #= false
      dut.clockDomain.waitSamplingWhere(dut.io.txValid.toBoolean)
      dut.clockDomain.waitSampling(3)
      dut.io.txReady #= true
      dut.clockDomain.waitSamplingWhere(beats.nonEmpty && beats.last._3)
      dut.clockDomain.waitSampling(4)
      assert(beats.length == 2, s"beats $beats")
      val dws = beats.flatMap { case (d, _, _) => Seq((d & 0xffffffffL).toLong, (d >> 32).toLong) }
      assert((dws(0) >>> 24) == 0x4a, f"CplD dw0 ${dws(0)}%08x")
      assert(((dws(2) >> 16) & 0xffff) == 0x0102, f"requester ${dws(2)}%08x")
      assert(((dws(2) >> 8) & 0xff) == 0x07, f"tag ${dws(2)}%08x")
      assert(dws(3) == pcie(0xc0ffeeL), f"data ${dws(3)}%08x")
      assert(beats.forall(_._2 == 0xff))
    }
  }
}
