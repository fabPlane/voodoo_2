package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._
import voodoo.artix.AxiLite4ToBmbBridge

class DemoAxiLitePlayerTest extends AnyFunSuite {
  private val bank0 = IndexedSeq(
    (0x21cL, 0x00110000L),
    (0x148L, 0x0000ff00L),
    (0x124L, 0x00000000L)
  )
  private val bank1 = IndexedSeq(
    (0x200L, 0x00000001L)
  )

  private lazy val compiled =
    SimConfig.withVerilator.compile(DemoAxiLitePlayer(Seq(bank0, bank1), addressWidth = 24))

  private def init(dut: DemoAxiLitePlayer): Unit = {
    dut.io.enable #= false
    dut.io.start #= false
    dut.io.loop #= false
    dut.io.select #= 0
    dut.io.axi.aw.ready #= true
    dut.io.axi.w.ready #= true
    dut.io.axi.b.valid #= false
    dut.io.axi.b.resp #= 0
    dut.io.axi.ar.ready #= false
    dut.io.axi.r.valid #= false
    dut.io.axi.r.resp #= 0
    dut.io.axi.r.data #= 0
  }

  private def boot(dut: DemoAxiLitePlayer): Unit = {
    dut.clockDomain.forkStimulus(10)
    init(dut)
    dut.clockDomain.waitSampling(20)
  }

  private def play(dut: DemoAxiLitePlayer, select: Int, expected: Seq[(Long, Long)]): Unit = {
    dut.io.select #= select
    dut.io.enable #= true
    dut.io.start #= true
    dut.clockDomain.waitSampling()
    dut.io.start #= false
    dut.clockDomain.waitSampling()
    assert(!dut.io.done.toBoolean, s"start did not arm bank $select")

    val seen = scala.collection.mutable.ArrayBuffer[(Long, Long)]()
    var cycles = 0
    while (!dut.io.done.toBoolean && cycles < 64) {
      val fire = dut.io.axi.aw.valid.toBoolean && dut.io.axi.aw.ready.toBoolean &&
        dut.io.axi.w.valid.toBoolean && dut.io.axi.w.ready.toBoolean
      if (fire) {
        seen += ((dut.io.axi.aw.addr.toLong, dut.io.axi.w.data.toLong))
      }
      dut.io.axi.b.valid #= dut.io.axi.b.ready.toBoolean
      dut.clockDomain.waitSampling()
      cycles += 1
    }

    assert(dut.io.done.toBoolean, s"player never finished bank $select")
    assert(seen.toSeq == expected, s"bank $select: $seen")
  }

  test("replays each ROM write then raises done") {
    compiled.doSim("demo-player") { dut =>
      boot(dut)
      play(dut, 0, bank0)
    }
  }

  test("start with select replays a later bank") {
    compiled.doSim("demo-player-bank1") { dut =>
      boot(dut)
      play(dut, 1, bank1)
    }
  }

  test("start can replay bank 0 after bank 1") {
    compiled.doSim("demo-player-restart") { dut =>
      boot(dut)
      play(dut, 1, bank1)
      dut.io.enable #= false
      dut.clockDomain.waitSampling()
      play(dut, 0, bank0)
    }
  }

  test("loop wraps to loopAt instead of done") {
    val bank = IndexedSeq((0x21cL, 1L), (0x128L, 1L), (0x080L, 1L))
    SimConfig.withVerilator
      .compile(DemoAxiLitePlayer(Seq(bank), loopAt = Seq(1), addressWidth = 24))
      .doSim("demo-player-loop") { dut =>
        boot(dut)
        dut.io.select #= 0
        dut.io.enable #= true
        dut.io.loop #= true
        dut.io.start #= true
        dut.clockDomain.waitSampling()
        dut.io.start #= false

        val seen = scala.collection.mutable.ArrayBuffer[Long]()
        var cycles = 0
        while (seen.length < 5 && cycles < 64) {
          val fire = dut.io.axi.aw.valid.toBoolean && dut.io.axi.aw.ready.toBoolean &&
            dut.io.axi.w.valid.toBoolean && dut.io.axi.w.ready.toBoolean
          if (fire) {
            seen += dut.io.axi.aw.addr.toLong
          }
          dut.io.axi.b.valid #= dut.io.axi.b.ready.toBoolean
          dut.clockDomain.waitSampling()
          cycles += 1
        }
        assert(!dut.io.done.toBoolean, "looping player raised done")
        assert(seen.toSeq == Seq(0x21cL, 0x128L, 0x080L, 0x128L, 0x080L), s"loop: $seen")
      }
  }

  test("AXI-Lite master mux elaborates") {
    class MuxDut extends Component {
      val sel = in Bool ()
      val cfg = AxiLite4ToBmbBridge.axiConfig(24)
      val a = slave(AxiLite4(cfg))
      val b = slave(AxiLite4(cfg))
      val s = master(AxiLite4(cfg))
      AxiLiteMasterMux(sel, a, b, s)
    }
    SpinalConfig(mode = Verilog, targetDirectory = "simWorkspace/tmp").generate(new MuxDut)
  }
}
