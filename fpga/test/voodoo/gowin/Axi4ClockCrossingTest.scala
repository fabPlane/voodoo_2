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
class Axi4ClockCrossingTestTop(cfg: Axi4Config, wFifo: Int = 4) extends Component {
  val coreCd = ClockDomain.external("core")
  val memCd = ClockDomain.external("mem")
  val io = new Bundle {
    val input = slave(Axi4(cfg))
    val output = master(Axi4(cfg))
  }
  val cc = Axi4ClockCrossing(cfg, coreCd, memCd,
    arFifoSize = 4, awFifoSize = 4, rFifoSize = 8, wFifoSize = wFifo, bFifoSize = 4)
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

  test("AW / W bursts cross whole and in order when W is pulled ahead of its address") {
    SimConfig.withVerilator.compile(new Axi4ClockCrossingTestTop(cfg, wFifo = 32)).doSim(seed = 4) { dut =>
      dut.coreCd.forkStimulus(25)
      dut.memCd.forkStimulus(10)
      val rnd = new scala.util.Random(13)
      val in = dut.io.input
      val out = dut.io.output
      in.ar.valid #= false; in.r.ready #= true; out.ar.ready #= true; out.r.valid #= false
      in.aw.valid #= false; in.w.valid #= false; in.b.ready #= true
      out.aw.ready #= false; out.w.ready #= false; out.b.valid #= false
      dut.coreCd.waitSampling(10)
      val n = 400
      val lens = Array.fill(n)(rnd.nextInt(8))
      fork {
        for (k <- 0 until n) {
          in.aw.valid #= true; in.aw.addr #= k * 64; in.aw.len #= lens(k)
          in.aw.id #= 0; in.aw.size #= 2; in.aw.burst #= 1
          dut.coreCd.waitSamplingWhere(in.aw.ready.toBoolean)
          in.aw.valid #= false
          if (rnd.nextInt(3) == 0) dut.coreCd.waitSampling(rnd.nextInt(4))
        }
      }
      fork {
        for (k <- 0 until n; i <- 0 to lens(k)) {
          if (rnd.nextInt(3) == 0) { in.w.valid #= false; dut.coreCd.waitSampling(1 + rnd.nextInt(2)) }
          in.w.valid #= true; in.w.data #= (k << 8) | i; in.w.strb #= 0xf; in.w.last #= i == lens(k)
          dut.coreCd.waitSamplingWhere(in.w.ready.toBoolean)
        }
        in.w.valid #= false
      }
      // Memory side: W beats are taken whenever offered (like the register
      // stages in front of the DDR adapter, which pull beats ahead of their
      // address); each address then claims its beats in order.
      val wBeats = mutable.Queue[Long]()
      var memCycles = 0L
      fork { while (true) { dut.memCd.waitSampling(); memCycles += 1 } }
      fork {
        while (true) {
          out.w.ready #= rnd.nextInt(4) != 0
          dut.memCd.waitSampling()
          if (out.w.valid.toBoolean && out.w.ready.toBoolean) wBeats.enqueue(out.w.data.toLong)
        }
      }
      var got = 0
      var stuck = false
      fork {
        while (got < n && !stuck) {
          out.aw.ready #= true
          dut.memCd.waitSamplingWhere(out.aw.valid.toBoolean || memCycles > 100000)
          if (memCycles > 100000) stuck = true
          else {
            out.aw.ready #= false
            val k = (out.aw.addr.toLong / 64).toInt
            assert(k == got, s"AW $got arrived as $k")
            dut.memCd.waitSamplingWhere(wBeats.size > lens(k) || memCycles > 100000)
            if (memCycles > 100000) stuck = true
            else {
              for (i <- 0 to lens(k)) {
                val d = wBeats.dequeue()
                assert(d == ((k << 8) | i), s"burst $k beat $i: $d")
              }
              got += 1
            }
          }
        }
      }
      dut.coreCd.waitSamplingWhere(got == n || stuck)
      assert(!stuck, s"write path deadlocked after $got bursts")
    }
  }
}
