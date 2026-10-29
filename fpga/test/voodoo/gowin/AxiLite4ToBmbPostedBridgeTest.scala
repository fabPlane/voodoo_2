package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import voodoo.artix.AxiLite4ToBmbBridge

import scala.collection.mutable

/** Posted-write AXI4-Lite -> BMB bridge: writes stream with several in
  * flight and every one gets its B in order; a read waits for every earlier
  * write's response; the status word answers locally; a response that never
  * comes wedges the bridge with SLVERR. */
class AxiLite4ToBmbPostedBridgeTest extends AnyFunSuite {
  private val OKAY = 0
  private val SLVERR = 2

  private lazy val compiled = SimConfig.withIVerilog.compile(
    AxiLite4ToBmbPostedBridge(24, voodoo.Core.cpuBmbParams, timeoutLog2 = 6, maxWrites = 8))

  private def init(dut: AxiLite4ToBmbPostedBridge): Unit = {
    dut.io.axi.aw.valid #= false
    dut.io.axi.aw.addr #= 0
    dut.io.axi.aw.prot #= 0
    dut.io.axi.w.valid #= false
    dut.io.axi.w.data #= 0
    dut.io.axi.w.strb #= 0xf
    dut.io.axi.b.ready #= true
    dut.io.axi.ar.valid #= false
    dut.io.axi.ar.addr #= 0
    dut.io.axi.ar.prot #= 0
    dut.io.axi.r.ready #= true
    dut.io.cpuBus.cmd.ready #= false
    dut.io.cpuBus.rsp.valid #= false
    dut.io.cpuBus.rsp.last #= true
    dut.io.cpuBus.rsp.source #= 0
    dut.io.cpuBus.rsp.opcode #= 0
    dut.io.cpuBus.rsp.data #= 0
  }

  /** BMB slave: random cmd backpressure, in-order responses after 2..9
    * cycles; reads return ~address; respond = false stops answering. */
  private class Slave(dut: AxiLite4ToBmbPostedBridge, rnd: scala.util.Random) {
    val cmds = mutable.ArrayBuffer[(Boolean, Long, Long, Long)]() // (write, addr, data, cycle)
    var respond = true
    var cycle = 0L
    private val q = mutable.Queue[(Long, Long)]() // (data, due)
    fork {
      while (true) {
        dut.io.cpuBus.cmd.ready #= rnd.nextInt(4) != 0
        if (respond && q.nonEmpty && q.head._2 <= cycle) {
          dut.io.cpuBus.rsp.valid #= true
          dut.io.cpuBus.rsp.data #= q.head._1
        } else dut.io.cpuBus.rsp.valid #= false
        dut.clockDomain.waitSampling()
        cycle += 1
        if (dut.io.cpuBus.rsp.valid.toBoolean) q.dequeue()
        val c = dut.io.cpuBus.cmd
        if (c.valid.toBoolean && c.ready.toBoolean) {
          val w = c.opcode.toInt == 1
          val a = c.address.toLong
          cmds += ((w, a, c.data.toLong, cycle))
          q.enqueue(((if (w) 0L else (~a) & 0xffffffffL), cycle + 2 + rnd.nextInt(8)))
        }
      }
    }
  }

  private def collectB(dut: AxiLite4ToBmbPostedBridge, out: mutable.ArrayBuffer[Int]): Unit = fork {
    while (true) {
      dut.clockDomain.waitSampling()
      if (dut.io.axi.b.valid.toBoolean && dut.io.axi.b.ready.toBoolean) out += dut.io.axi.b.resp.toInt
    }
  }

  private def write(dut: AxiLite4ToBmbPostedBridge, a: Long, d: Long): Unit = {
    dut.io.axi.aw.valid #= true; dut.io.axi.aw.addr #= a
    dut.io.axi.w.valid #= true; dut.io.axi.w.data #= d
    dut.clockDomain.waitSamplingWhere(dut.io.axi.aw.ready.toBoolean)
    dut.io.axi.aw.valid #= false; dut.io.axi.w.valid #= false
  }

  private def read(dut: AxiLite4ToBmbPostedBridge, a: Long): (Long, Int) = {
    dut.io.axi.ar.valid #= true; dut.io.axi.ar.addr #= a
    dut.clockDomain.waitSamplingWhere(dut.io.axi.ar.ready.toBoolean)
    dut.io.axi.ar.valid #= false
    dut.clockDomain.waitSamplingWhere(dut.io.axi.r.valid.toBoolean)
    (dut.io.axi.r.data.toLong, dut.io.axi.r.resp.toInt)
  }

  test("posted writes stream in order with a B each; reads wait for them") {
    compiled.doSim(seed = 3) { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      val rnd = new scala.util.Random(9)
      val slave = new Slave(dut, rnd)
      val bs = mutable.ArrayBuffer[Int]()
      collectB(dut, bs)
      dut.clockDomain.waitSampling(2)
      var expect = mutable.ArrayBuffer[(Boolean, Long, Long)]()
      val t0 = slave.cycle
      for (k <- 0 until 400) {
        val a = (rnd.nextInt(0x4000) & ~3).toLong
        val d = rnd.nextLong() & 0xffffffffL
        write(dut, a, d)
        expect += ((true, a, d))
        if (rnd.nextInt(25) == 0) {
          val ra = (rnd.nextInt(0x4000) & ~3).toLong
          val (v, resp) = read(dut, ra)
          assert(resp == OKAY && v == ((~ra) & 0xffffffffL), f"read 0x$ra%x got 0x$v%x")
          // every write before the read was answered before the read issued
          val readIdx = slave.cmds.size - 1
          assert(!slave.cmds(readIdx)._1 && slave.cmds(readIdx)._2 == ra)
          assert(bs.size == expect.count(_._1), s"read issued with ${expect.count(_._1) - bs.size} writes unanswered")
          expect += ((false, ra, 0L))
        }
      }
      val writes = expect.count(_._1)
      dut.clockDomain.waitSamplingWhere(bs.size == writes)
      val cycles = slave.cycle - t0
      assert(bs.forall(_ == OKAY))
      assert(slave.cmds.map(c => (c._1, c._2, if (c._1) c._3 else 0L)) == expect, "BMB commands out of order or changed")
      println(s"[posted bridge] $writes writes, ${expect.size - writes} reads, $cycles cycles")
      // One-outstanding would need >= ~4 cycles a write with these latencies.
      assert(cycles < writes * 3, s"$cycles cycles for $writes writes: not streaming")
      // status word is local and never waits
      val (st, sresp) = read(dut, AxiLite4ToBmbBridge.statusAddress)
      assert(sresp == OKAY && (st & 1) == 0)
    }
  }

  test("a write whose response never comes fails with SLVERR and wedges; later writes are dropped") {
    compiled.doSim(seed = 4) { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      val slave = new Slave(dut, new scala.util.Random(2))
      val bs = mutable.ArrayBuffer[Int]()
      collectB(dut, bs)
      dut.clockDomain.waitSampling(2)
      slave.respond = false
      for (k <- 0 until 3) write(dut, 0x100 + 4 * k, k)
      dut.clockDomain.waitSamplingWhere(bs.size == 3)
      assert(bs.forall(_ == SLVERR), s"B responses $bs")
      val (st, _) = read(dut, AxiLite4ToBmbBridge.statusAddress)
      assert((st & 1) == 1 && ((st >> 2) & 1) == 1, f"status 0x$st%x: wedged + response timeout expected")
      val n = slave.cmds.size
      write(dut, 0x200, 7)
      dut.clockDomain.waitSamplingWhere(bs.size == 4)
      assert(bs(3) == SLVERR && slave.cmds.size == n, "write after wedge must be dropped")
      val (_, rresp) = read(dut, 0x300)
      assert(rresp == SLVERR)
    }
  }
}
