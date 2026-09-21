package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._

class BarRv32Test extends AnyFunSuite {
  private def compile(program: Seq[BigInt], words: Int = 1024) =
    SimConfig.withVerilator.compile(BarRv32(program, wordCount = words))

  private lazy val smoke = compile(BarSmokeFirmware.words)
  private lazy val looped = compile(
    BarGlideFirmware.fromWrites(
      Seq((0x21cL, 0x00110000L), (0x080L, 1L), (0x120L, 0L))
    )
  )

  private def idleAxi(dut: BarRv32): Unit = {
    dut.io.run #= false
    dut.io.axi.aw.ready #= true
    dut.io.axi.w.ready #= true
    dut.io.axi.ar.ready #= true
    dut.io.axi.b.valid #= false
    dut.io.axi.b.resp #= 0
    dut.io.axi.r.valid #= false
    dut.io.axi.r.resp #= 0
    dut.io.axi.r.data #= 0
  }

  private def collect(dut: BarRv32, n: Int, limit: Int): Seq[(Long, Long)] = {
    val seen = scala.collection.mutable.ArrayBuffer[(Long, Long)]()
    var cycles = 0
    while (seen.length < n && cycles < limit) {
      val aw = dut.io.axi.aw.valid.toBoolean && dut.io.axi.aw.ready.toBoolean
      val w = dut.io.axi.w.valid.toBoolean && dut.io.axi.w.ready.toBoolean
      if (aw && w) {
        seen += ((dut.io.axi.aw.addr.toLong, dut.io.axi.w.data.toLong))
      }
      dut.io.axi.b.valid #= dut.io.axi.b.ready.toBoolean
      dut.clockDomain.waitSampling()
      cycles += 1
    }
    seen.toSeq
  }

  test("BRAM smoke test writes fbiInit then swapbufferCMD") {
    smoke.doSim("bar-rv32-smoke") { dut =>
      dut.clockDomain.forkStimulus(10)
      idleAxi(dut)
      dut.clockDomain.waitSampling(20)
      dut.io.run #= true
      val seen = collect(dut, 2, 256)
      assert(seen == Seq((0x21cL, 0x00110000L), (0x120L, 0L)), s"BAR writes: $seen")
    }
  }

  test("table walker sst_wr's packed Glide pairs onto the BAR") {
    looped.doSim("bar-rv32-glide-loop") { dut =>
      dut.clockDomain.forkStimulus(10)
      idleAxi(dut)
      dut.clockDomain.waitSampling(20)
      dut.io.run #= true
      val seen = collect(dut, 3, 1024)
      assert(
        seen == Seq((0x21cL, 0x00110000L), (0x080L, 1L), (0x120L, 0L)),
        s"BAR writes: $seen"
      )
    }
  }

  test("held in reset issues no AXI") {
    smoke.doSim("bar-rv32-halt") { dut =>
      dut.clockDomain.forkStimulus(10)
      idleAxi(dut)
      dut.clockDomain.waitSampling(40)
      assert(!dut.io.axi.aw.valid.toBoolean)
      assert(!dut.io.axi.w.valid.toBoolean)
      assert(!dut.io.axi.ar.valid.toBoolean)
    }
  }
}
