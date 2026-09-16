package voodoo.artix

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import scala.collection.mutable
import scala.language.reflectiveCalls

/** Port of test/de10/H2fLwToBmbBridgeTest.scala to the AXI4-Lite bridge. */
class AxiLite4ToBmbBridgeTest extends AnyFunSuite {
  private val statusAddress = AxiLite4ToBmbBridge.statusAddress
  private val OKAY = 0
  private val SLVERR = 2

  private lazy val compiled =
    SimConfig.withVerilator.compile(
      AxiLite4ToBmbBridge(24, voodoo.Core.cpuBmbParams, timeoutLog2 = 3)
    )

  private def init(dut: AxiLite4ToBmbBridge): Unit = {
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

  private def waitSamplingWhere(dut: AxiLite4ToBmbBridge, maxCycles: Int, clue: String)(
      cond: => Boolean
  ): Unit = {
    var cycles = 0
    var done = false
    while (!done && cycles < maxCycles) {
      dut.clockDomain.waitSampling()
      cycles += 1
      done = cond
    }
    assert(done, clue)
  }

  private def axiWrite(dut: AxiLite4ToBmbBridge, address: Long, data: Long, maxWait: Int = 64): Int = {
    dut.io.axi.aw.valid #= true
    dut.io.axi.aw.addr #= address
    dut.io.axi.w.valid #= true
    dut.io.axi.w.data #= data
    dut.io.axi.w.strb #= 0xf
    waitSamplingWhere(dut, maxWait, f"write 0x$address%06x was not accepted") {
      dut.io.axi.aw.ready.toBoolean
    }
    dut.io.axi.aw.valid #= false
    dut.io.axi.w.valid #= false
    waitSamplingWhere(dut, maxWait, f"write 0x$address%06x got no response") {
      dut.io.axi.b.valid.toBoolean
    }
    dut.io.axi.b.resp.toInt
  }

  private def axiRead(dut: AxiLite4ToBmbBridge, address: Long, maxWait: Int = 64): (Long, Int) = {
    dut.io.axi.ar.valid #= true
    dut.io.axi.ar.addr #= address
    waitSamplingWhere(dut, maxWait, f"read 0x$address%06x was not accepted") {
      dut.io.axi.ar.ready.toBoolean
    }
    dut.io.axi.ar.valid #= false
    waitSamplingWhere(dut, maxWait, f"read 0x$address%06x got no response") {
      dut.io.axi.r.valid.toBoolean
    }
    (dut.io.axi.r.data.toLong & 0xffffffffL, dut.io.axi.r.resp.toInt)
  }

  case class SeenCmd(address: Long, isWrite: Boolean, data: Long, mask: Int)

  /** Memory-like BMB slave: records every command, answers after a fixed delay. */
  private def bmbResponder(dut: AxiLite4ToBmbBridge, latency: Int): mutable.ArrayBuffer[SeenCmd] = {
    val seen = mutable.ArrayBuffer[SeenCmd]()
    val mem = mutable.Map[Long, Long]()
    val pending = mutable.Queue[SeenCmd]()
    dut.io.cpuBus.cmd.ready #= true
    dut.clockDomain.onSamplings {
      if (dut.io.cpuBus.cmd.valid.toBoolean && dut.io.cpuBus.cmd.ready.toBoolean) {
        val cmd = SeenCmd(
          dut.io.cpuBus.cmd.address.toLong,
          dut.io.cpuBus.cmd.opcode.toInt == 1,
          dut.io.cpuBus.cmd.data.toLong & 0xffffffffL,
          dut.io.cpuBus.cmd.mask.toInt
        )
        seen += cmd
        pending.enqueue(cmd)
      }
    }
    fork {
      while (true) {
        if (pending.isEmpty) {
          dut.clockDomain.waitSampling()
        } else {
          val cmd = pending.dequeue()
          dut.clockDomain.waitSampling(latency)
          if (cmd.isWrite) mem(cmd.address) = cmd.data
          dut.io.cpuBus.rsp.valid #= true
          dut.io.cpuBus.rsp.data #= mem.getOrElse(cmd.address, 0L)
          dut.clockDomain.waitSamplingWhere(dut.io.cpuBus.rsp.ready.toBoolean)
          dut.io.cpuBus.rsp.valid #= false
        }
      }
    }
    seen
  }

  test("write then read round trip through a responding BMB slave") {
    compiled.doSim("roundtrip") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      val seen = bmbResponder(dut, latency = 3)
      dut.clockDomain.waitSampling()

      assert(axiWrite(dut, 0x100, 0xdeadbeefL) == OKAY)
      val (value, resp) = axiRead(dut, 0x100)
      assert(resp == OKAY)
      assert(value == 0xdeadbeefL, f"read back 0x$value%08x")

      assert(seen.size == 2, s"expected 2 BMB commands, saw $seen")
      assert(seen(0) == SeenCmd(0x100, isWrite = true, 0xdeadbeefL, 0xf))
      assert(seen(1).address == 0x100 && !seen(1).isWrite)

      val (status, statusResp) = axiRead(dut, statusAddress)
      assert(statusResp == OKAY)
      assert(status == 0L, f"expected clean status, got 0x$status%08x")
    }
  }

  test("read command that is never accepted returns SLVERR and wedges") {
    compiled.doSim("request-timeout") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling()

      val (value, resp) = axiRead(dut, 0x400000, maxWait = 32)
      assert(resp == SLVERR)
      assert(value == 0L)

      val (status, statusResp) = axiRead(dut, statusAddress, maxWait = 16)
      assert(statusResp == OKAY)
      assert((status & 0x1L) != 0, f"expected wedged bit in status 0x$status%08x")
      assert((status & 0x2L) != 0, f"expected request-timeout bit in status 0x$status%08x")
    }
  }

  test("accepted read that never responds returns SLVERR, later writes are dropped") {
    compiled.doSim("response-timeout") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.io.cpuBus.cmd.ready #= true
      dut.clockDomain.waitSampling()

      val (value, resp) = axiRead(dut, 0x400000, maxWait = 32)
      assert(resp == SLVERR)
      assert(value == 0L)

      assert(axiWrite(dut, 0x110, 0x300L, maxWait = 16) == SLVERR)

      val (status, statusResp) = axiRead(dut, statusAddress, maxWait = 16)
      assert(statusResp == OKAY)
      assert((status & 0x1L) != 0, f"expected wedged bit in status 0x$status%08x")
      assert((status & 0x4L) != 0, f"expected response-timeout bit in status 0x$status%08x")
      assert(((status >> 8) & 0xffL) == 1L, f"expected one dropped write in status 0x$status%08x")
    }
  }
}
