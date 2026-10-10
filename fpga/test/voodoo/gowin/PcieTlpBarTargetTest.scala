package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._

class PcieTlpBarTargetTest extends AnyFunSuite {
  private lazy val compiled =
    SimConfig.withVerilator.compile(PcieTlpBarTarget(timeoutLog2 = 6))

  private def dword(value: Long): BigInt = BigInt(value) & 0xffffffffL

  private def pack(dws: Seq[Long]): BigInt = {
    var acc = BigInt(0)
    for (i <- 0 until 8) {
      val dw = if (i < dws.length) dword(dws(i)) else BigInt(0)
      acc |= dw << (224 - i * 32)
    }
    acc
  }

  // Payload dwords are in PCIe byte order (lowest-address byte in 31:24).
  private def pcie(v: Long): Long =
    ((v & 0xffL) << 24) | ((v & 0xff00L) << 8) | ((v >> 8) & 0xff00L) | ((v >> 24) & 0xffL)

  private def unpackDw(data: BigInt, index: Int): Long = {
    val shifted = data >> (224 - index * 32)
    (shifted & 0xffffffffL).toLong
  }

  private def init(dut: PcieTlpBarTarget): Unit = {
    dut.io.tlRxSop #= false
    dut.io.tlRxEop #= false
    dut.io.tlRxValid #= 0
    dut.io.tlRxData #= 0
    dut.io.tlRxBardec #= 0
    dut.io.tlTxWait #= false
    dut.io.tlTxCreditsCpl #= 0x80000001L
    dut.io.tlCfgBusdev #= 0x0108
    dut.io.axi.aw.ready #= false
    dut.io.axi.w.ready #= false
    dut.io.axi.b.valid #= false
    dut.io.axi.b.resp #= 0
    dut.io.axi.ar.ready #= false
    dut.io.axi.r.valid #= false
    dut.io.axi.r.resp #= 0
    dut.io.axi.r.data #= 0
  }

  private def beat(dut: PcieTlpBarTarget, dws: Seq[Long], bar0: Boolean = true): Unit = {
    dut.io.tlRxSop #= true
    dut.io.tlRxEop #= true
    dut.io.tlRxValid #= 0xff
    dut.io.tlRxData #= pack(dws)
    dut.io.tlRxBardec #= (if (bar0) 1 else 0)
    dut.clockDomain.waitSampling()
    dut.io.tlRxSop #= false
    dut.io.tlRxEop #= false
    dut.io.tlRxValid #= 0
  }

  test("memory write on BAR0 becomes an AXI-Lite write") {
    compiled.doSim("tlp-write") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling()

      val addr = 0x100L
      val data = 0xdeadbeefL
      beat(
        dut,
        Seq(
          0x40000001L, // 3DW MWr, length 1
          0x0000000fL, // req id 0, tag 0, BE 0xf
          addr,
          pcie(data)
        )
      )

      var cycles = 0
      while (!dut.io.axi.aw.valid.toBoolean && cycles < 32) {
        dut.clockDomain.waitSampling()
        cycles += 1
      }
      assert(dut.io.axi.aw.valid.toBoolean, "AXI AW never rose")
      assert(dut.io.axi.w.valid.toBoolean, "AXI W never rose")
      assert((dut.io.axi.aw.addr.toLong & 0xffffffL) == addr)
      assert((dut.io.axi.w.data.toLong & 0xffffffffL) == data)

      dut.io.axi.aw.ready #= true
      dut.io.axi.w.ready #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.aw.ready #= false
      dut.io.axi.w.ready #= false
      dut.io.axi.b.valid #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.b.valid #= false
      dut.clockDomain.waitSampling()
    }
  }

  test("memory read on BAR0 returns a CplD with AXI data") {
    compiled.doSim("tlp-read") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling()

      beat(
        dut,
        Seq(
          0x00000001L, // 3DW MRd, length 1
          0x0102000fL, // req 0x0102, tag 0, BE 0xf
          0x200L
        )
      )

      var cycles = 0
      while (!dut.io.axi.ar.valid.toBoolean && cycles < 32) {
        dut.clockDomain.waitSampling()
        cycles += 1
      }
      assert(dut.io.axi.ar.valid.toBoolean)
      assert((dut.io.axi.ar.addr.toLong & 0xffffffL) == 0x200L)
      dut.io.axi.ar.ready #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.ar.ready #= false
      dut.io.axi.r.valid #= true
      dut.io.axi.r.data #= 0xc0ffeel
      dut.clockDomain.waitSampling()
      dut.io.axi.r.valid #= false

      cycles = 0
      while (dut.io.tlTxValid.toInt == 0 && cycles < 32) {
        dut.clockDomain.waitSampling()
        cycles += 1
      }
      assert(dut.io.tlTxSop.toBoolean && dut.io.tlTxEop.toBoolean)
      val dw0 = unpackDw(dut.io.tlTxData.toBigInt, 0)
      val dw2 = unpackDw(dut.io.tlTxData.toBigInt, 2)
      val dw3 = unpackDw(dut.io.tlTxData.toBigInt, 3)
      assert((dw0 >> 24) == 0x4a, f"CplD header 0x$dw0%08x")
      assert(((dw2 >> 16) & 0xffff) == 0x0102)
      assert((dw3 & 0xffffffffL) == pcie(0xc0ffeel))
    }
  }

  test("window register 0x250 selects the megabyte mapped onto BAR0") {
    compiled.doSim("tlp-window") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling()

      beat(dut, Seq(0x40000001L, 0x0000000fL, 0x250L, pcie(0x4L)))
      dut.clockDomain.waitSampling(4)
      assert(dut.io.window.toInt == 4)

      beat(dut, Seq(0x40000001L, 0x0000000fL, 0x100L, pcie(0x11L)))
      var cycles = 0
      while (!dut.io.axi.aw.valid.toBoolean && cycles < 32) {
        dut.clockDomain.waitSampling()
        cycles += 1
      }
      assert((dut.io.axi.aw.addr.toLong & 0xffffffL) == 0x400100L)
    }
  }
  test("outside register space only a byte write to 0x250 moves the window") {
    compiled.doSim("tlp-window-lfb") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling()

      beat(dut, Seq(0x40000001L, 0x0000000fL, 0x250L, pcie(0x4L)))
      dut.clockDomain.waitSampling(4)
      assert(dut.io.window.toInt == 4)

      // 32-bit write to LFB offset 0x250 is data, not the window register.
      beat(dut, Seq(0x40000001L, 0x0000000fL, 0x250L, pcie(0x1234L)))
      var cycles = 0
      while (!dut.io.axi.aw.valid.toBoolean && cycles < 32) {
        dut.clockDomain.waitSampling()
        cycles += 1
      }
      assert((dut.io.axi.aw.addr.toLong & 0xffffffL) == 0x400250L)
      assert(dut.io.window.toInt == 4)
      dut.io.axi.aw.ready #= true
      dut.io.axi.w.ready #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.aw.ready #= false
      dut.io.axi.w.ready #= false
      dut.io.axi.b.valid #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.b.valid #= false
      dut.clockDomain.waitSampling(2)

      // Byte write (first BE 0001) at 0x250 selects window 0 again.
      beat(dut, Seq(0x40000001L, 0x00000001L, 0x250L, pcie(0x0L)))
      dut.clockDomain.waitSampling(4)
      assert(dut.io.window.toInt == 0)
    }
  }
  test("a timed-out read's late response is discarded, not given to the next read") {
    compiled.doSim("tlp-read-timeout") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling()
      def waitCpl(): Long = {
        var c = 0
        while (dut.io.tlTxValid.toInt == 0 && c < 4000) { dut.clockDomain.waitSampling(); c += 1 }
        assert(dut.io.tlTxValid.toInt != 0, "no completion")
        val v = unpackDw(dut.io.tlTxData.toBigInt, 3)
        dut.clockDomain.waitSampling()
        v
      }
      // Read 1: AR accepted, but R withheld past the read timeout.
      beat(dut, Seq(0x00000001L, 0x0000000fL, 0x200L))
      dut.io.axi.ar.ready #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.ar.ready #= false
      assert(waitCpl() == 0xffffffffL)
      // Its late R arrives now and must be swallowed.
      dut.io.axi.r.valid #= true
      dut.io.axi.r.data #= 0xbadL
      dut.clockDomain.waitSampling()
      dut.io.axi.r.valid #= false
      dut.clockDomain.waitSampling(2)
      // Read 2 gets its own data.
      beat(dut, Seq(0x00000001L, 0x0000000fL, 0x204L))
      dut.io.axi.ar.ready #= true
      dut.clockDomain.waitSampling()
      dut.io.axi.ar.ready #= false
      dut.clockDomain.waitSampling(3)
      dut.io.axi.r.valid #= true
      dut.io.axi.r.data #= 0x600dL
      dut.clockDomain.waitSampling()
      dut.io.axi.r.valid #= false
      assert(waitCpl() == pcie(0x600dL))
    }
  }
}
