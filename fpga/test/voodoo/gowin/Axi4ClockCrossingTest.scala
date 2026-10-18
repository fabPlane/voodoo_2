package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._
import spinal.lib.bus.amba4.axi.{Axi4, Axi4Config}

import scala.collection.mutable

/** Axi4ClockCrossing between unrelated clocks: R beats pushed from the
  * memory side with random valid gaps and popped with random ready arrive
  * complete and in order (the registered room flag must never let the R FIFO
  * overflow), and AR requests cross the other way the same.
  */
class Axi4ClockCrossingTestTop(cfg: Axi4Config) extends Component {
  val coreCd = ClockDomain.external("core")
  val memCd = ClockDomain.external("mem")
  val io = new Bundle {
    val input = slave(Axi4(cfg))
    val output = master(Axi4(cfg))
  }
  val cc = Axi4ClockCrossing(cfg, coreCd, memCd,
    arFifoSize = 4, awFifoSize = 4, rFifoSize = 8, wFifoSize = 4, bFifoSize = 4)
  cc.io.input <> io.input
  cc.io.output <> io.output
}

class Axi4ClockCrossingTest extends AnyFunSuite {
  private val cfg = Axi4Config(
    addressWidth = 32, dataWidth = 32, idWidth = 4,
    useRegion = false, useLock = false, useCache = false, useQos = false, useProt = false
  )

  test("R and AR cross in order under random stalls on both sides") {
    SimConfig.withVerilator.compile(new Axi4ClockCrossingTestTop(cfg)).doSim(seed = 3) { dut =>
      dut.coreCd.forkStimulus(27)
      dut.memCd.forkStimulus(10)
      val rnd = new scala.util.Random(11)
      val n = 3000
      val in = dut.io.input
      val out = dut.io.output
      in.aw.valid #= false; in.w.valid #= false; in.b.ready #= true
      out.aw.ready #= true; out.w.ready #= true; out.b.valid #= false
      in.ar.valid #= false; in.r.ready #= false
      out.ar.ready #= false; out.r.valid #= false
      dut.coreCd.waitSampling(10)

      // R: memory side pushes 0..n-1, core side pops with random ready.
      var rGot = 0
      fork {
        var i = 0
        while (i < n) {
          out.r.valid #= rnd.nextInt(4) != 0
          out.r.data #= i
          out.r.id #= i & 15
          out.r.resp #= 0
          out.r.last #= true
          dut.memCd.waitSampling()
          if (out.r.valid.toBoolean && out.r.ready.toBoolean) i += 1
        }
        out.r.valid #= false
      }
      fork {
        while (true) {
          in.r.ready #= rnd.nextInt(3) == 0
          dut.coreCd.waitSampling()
          if (in.r.valid.toBoolean && in.r.ready.toBoolean) {
            assert(in.r.data.toLong == rGot, s"R beat $rGot arrived as ${in.r.data.toLong}")
            rGot += 1
          }
        }
      }
      // AR: core side pushes addresses, memory side pops with random ready.
      var arGot = 0
      fork {
        var i = 0
        while (i < n) {
          in.ar.valid #= rnd.nextInt(2) == 0
          in.ar.addr #= i * 4
          in.ar.id #= 0; in.ar.len #= 0; in.ar.size #= 2; in.ar.burst #= 1
          dut.coreCd.waitSampling()
          if (in.ar.valid.toBoolean && in.ar.ready.toBoolean) i += 1
        }
        in.ar.valid #= false
      }
      fork {
        while (true) {
          out.ar.ready #= rnd.nextInt(3) != 0
          dut.memCd.waitSampling()
          if (out.ar.valid.toBoolean && out.ar.ready.toBoolean) {
            assert(out.ar.addr.toLong == arGot * 4L, s"AR $arGot arrived as ${out.ar.addr.toLong}")
            arGot += 1
          }
        }
      }
      dut.coreCd.waitSamplingWhere(rGot == n && arGot == n)
    }
  }
}
