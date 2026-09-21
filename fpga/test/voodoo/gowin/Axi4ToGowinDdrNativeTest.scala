package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core.sim._
import voodoo.artix.Axi4ToAxiLite4

import scala.collection.mutable.ArrayBuffer

class Axi4ToGowinDdrNativeTest extends AnyFunSuite {
  private val axiConfig = Axi4ToAxiLite4.axiConfig(addressWidth = 32)

  private lazy val compiled = SimConfig.withVerilator.compile(
    Axi4ToGowinDdrNative(
      axiConfig,
      axiConfig,
      nativeDataWidth = 128,
      nativeAddrWidth = 28
    )
  )

  private def init(dut: Axi4ToGowinDdrNative): Unit = {
    for (axi <- Seq(dut.io.fb, dut.io.tex)) {
      axi.aw.valid #= false
      axi.aw.addr #= 0
      axi.aw.id #= 0
      axi.aw.prot #= 0
      axi.aw.burst #= 1
      axi.aw.len #= 0
      axi.aw.size #= 2
      axi.w.valid #= false
      axi.w.data #= 0
      axi.w.strb #= 0xf
      axi.w.last #= false
      axi.b.ready #= false
      axi.ar.valid #= false
      axi.ar.addr #= 0
      axi.ar.id #= 0
      axi.ar.prot #= 0
      axi.ar.burst #= 1
      axi.ar.len #= 0
      axi.ar.size #= 2
      axi.r.ready #= true
    }
    dut.io.ddr.cmdReady #= true
    dut.io.ddr.wrDataRdy #= true
    dut.io.ddr.rdData #= 0
    dut.io.ddr.rdDataValid #= false
    dut.io.ddr.rdDataEnd #= false
    dut.io.ddr.initCalibComplete #= true
  }

  private def boot(dut: Axi4ToGowinDdrNative): Unit = {
    dut.clockDomain.forkStimulus(10)
    init(dut)
    dut.clockDomain.waitSampling(5)
  }

  private def sendWrite(
      dut: Axi4ToGowinDdrNative,
      address: Long,
      words: Seq[Long]
  ): Seq[(Long, BigInt, Long)] = {
    val seen = ArrayBuffer[(Long, BigInt, Long)]()
    var monitorDone = false
    fork {
      while (!monitorDone) {
        if (dut.io.ddr.cmdEn.toBoolean && dut.io.ddr.wrDataEn.toBoolean) {
          seen += ((
            dut.io.ddr.addr.toLong,
            dut.io.ddr.wrData.toBigInt,
            dut.io.ddr.wrDataMask.toLong
          ))
        }
        dut.clockDomain.waitSampling()
      }
    }

    dut.io.fb.aw.addr #= address
    dut.io.fb.aw.len #= words.length - 1
    dut.io.fb.aw.valid #= true
    dut.clockDomain.waitSamplingWhere(dut.io.fb.aw.ready.toBoolean)
    dut.io.fb.aw.valid #= false

    words.zipWithIndex.foreach { case (word, i) =>
      dut.io.fb.w.data #= word
      dut.io.fb.w.strb #= 0xf
      dut.io.fb.w.last #= i == words.length - 1
      dut.io.fb.w.valid #= true
      dut.clockDomain.waitSamplingWhere(dut.io.fb.w.ready.toBoolean)
      dut.io.fb.w.valid #= false
    }

    var cycles = 0
    while (!dut.io.fb.b.valid.toBoolean && cycles < 100) {
      dut.clockDomain.waitSampling()
      cycles += 1
    }
    assert(dut.io.fb.b.valid.toBoolean, s"write response timed out; native writes=$seen")
    monitorDone = true
    dut.io.fb.b.ready #= true
    dut.clockDomain.waitSampling()
    dut.io.fb.b.ready #= false
    seen.toSeq
  }

  test("unaligned AXI writes use DDR word addresses, lanes, and fresh masks") {
    compiled.doSim("gowin-ddr-write-lanes") { dut =>
      boot(dut)
      val a = 0x11223344L
      val b = 0x55667788L
      val c = 0x99aabbccL
      val first = sendWrite(dut, 0x1c, Seq(a, b, c))

      assert(first.map(_._1) == Seq(0x08L, 0x10L), s"DDR addresses: $first")
      assert(first.head._2 == BigInt(a) << 96, f"first data 0x${first.head._2}%032x")
      assert(first.head._3 == 0x0fffL, f"first mask 0x${first.head._3}%04x")
      assert(
        first(1)._2 == ((BigInt(c) << 32) | BigInt(b)),
        f"second data 0x${first(1)._2}%032x"
      )
      assert(first(1)._3 == 0xff00L, f"second mask 0x${first(1)._3}%04x")

      // A following one-word burst must not inherit valid lanes from the
      // previous transaction.
      val second = sendWrite(dut, 0x28, Seq(0xdeadbeefL))
      assert(second.length == 1)
      assert(second.head._1 == 0x10L)
      assert(second.head._2 == BigInt(0xdeadbeefL) << 64)
      assert(second.head._3 == 0xf0ffL, f"fresh mask 0x${second.head._3}%04x")
    }
  }

  test("unaligned AXI reads start at the selected lane and cross a DDR beat") {
    compiled.doSim("gowin-ddr-read-lanes") { dut =>
      boot(dut)
      val expected = Seq(0x11223344L, 0x55667788L, 0x99aabbccL)
      val nativeData = Seq(
        BigInt(expected(0)) << 96,
        (BigInt(expected(2)) << 32) | BigInt(expected(1))
      )
      val commands = ArrayBuffer[Long]()

      fork {
        nativeData.foreach { data =>
          dut.clockDomain.waitSamplingWhere(dut.io.ddr.cmdEn.toBoolean)
          commands += dut.io.ddr.addr.toLong
          dut.io.ddr.rdData #= data
          dut.io.ddr.rdDataEnd #= true
          dut.io.ddr.rdDataValid #= true
          dut.clockDomain.waitSampling()
          dut.io.ddr.rdDataValid #= false
        }
      }

      dut.io.fb.ar.addr #= 0x1c
      dut.io.fb.ar.len #= expected.length - 1
      dut.io.fb.ar.valid #= true
      dut.clockDomain.waitSamplingWhere(dut.io.fb.ar.ready.toBoolean)
      dut.io.fb.ar.valid #= false

      val seen = ArrayBuffer[Long]()
      var cycles = 0
      while (seen.length < expected.length && cycles < 100) {
        if (dut.io.fb.r.valid.toBoolean && dut.io.fb.r.ready.toBoolean) {
          seen += dut.io.fb.r.data.toLong
        }
        dut.clockDomain.waitSampling()
        cycles += 1
      }

      assert(seen.toSeq == expected, s"read words: $seen")
      assert(commands.toSeq == Seq(0x08L, 0x10L), s"DDR addresses: $commands")
    }
  }
}
