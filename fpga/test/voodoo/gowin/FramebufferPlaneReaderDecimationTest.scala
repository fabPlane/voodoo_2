package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import voodoo.{Config, TraceConfig}
import voodoo.framebuffer.FramebufferPlaneReader

class FramebufferPlaneReaderDecimationTest extends AnyFunSuite {
  private val config = Config
    .voodoo1()
    .copy(
      addressWidth = 12 bits,
      memBurstLengthWidth = 6,
      maxFbDims = (16, 16),
      trace = TraceConfig(enabled = true)
    )

  test("scanout decimates prefetched lanes before the consumer FIFO") {
    SimConfig.withVerilator
      .compile(
        FramebufferPlaneReader(
          config,
          suppressStartupDirectMiss = true,
          laneStride = 4
        )
      )
      .doSim { dut =>
        def waitUntil(label: String, limit: Int = 100)(condition: => Boolean): Unit = {
          var cycles = 0
          while (!condition && cycles < limit) {
            dut.clockDomain.waitSampling()
            cycles += 1
          }
          assert(condition, s"timed out waiting for $label after $limit cycles")
        }

        dut.clockDomain.forkStimulus(10)
        dut.io.prefetchReq.valid #= false
        dut.io.prefetchReq.startAddress #= 0
        dut.io.prefetchReq.endAddress #= 0
        dut.io.readReq.valid #= false
        dut.io.readReq.address #= 0
        dut.io.readRsp.ready #= true
        dut.io.mem.cmd.ready #= false
        dut.io.mem.rsp.valid #= false
        dut.io.mem.rsp.last #= false
        dut.io.mem.rsp.fragment.data #= 0
        dut.io.mem.rsp.fragment.source #= 0
        dut.io.mem.rsp.fragment.context #= 0
        dut.io.mem.rsp.fragment.opcode #= 0
        dut.clockDomain.waitSampling()

        dut.io.prefetchReq.valid #= true
        dut.io.prefetchReq.startAddress #= 0x000
        dut.io.prefetchReq.endAddress #= 0x01e
        waitUntil("prefetch acceptance") { dut.io.prefetchReq.ready.toBoolean }
        dut.clockDomain.waitSampling()
        dut.io.prefetchReq.valid #= false

        waitUntil("fill command") { dut.io.mem.cmd.valid.toBoolean }
        assert(dut.io.mem.cmd.fragment.address.toLong == 0)
        assert(dut.io.mem.cmd.fragment.length.toInt == 31)
        dut.io.mem.cmd.ready #= true
        dut.clockDomain.waitSampling()
        dut.io.mem.cmd.ready #= false

        for (word <- 0 until 8) {
          val lo = 0x100 + word * 2
          val hi = lo + 1
          dut.io.mem.rsp.valid #= true
          dut.io.mem.rsp.fragment.data #= (hi.toLong << 16) | lo.toLong
          dut.io.mem.rsp.last #= word == 7
          dut.clockDomain.waitSamplingWhere(dut.io.mem.rsp.ready.toBoolean)
          dut.io.mem.rsp.valid #= false
          dut.clockDomain.waitSampling()
        }

        val expected = Seq(0x000 -> 0x100L, 0x008 -> 0x104L, 0x010 -> 0x108L, 0x018 -> 0x10cL)
        for ((address, value) <- expected) {
          dut.io.readReq.valid #= true
          dut.io.readReq.address #= address
          waitUntil(f"read 0x$address%x") { dut.io.readReq.ready.toBoolean }
          dut.clockDomain.waitSampling()
          dut.io.readReq.valid #= false
          waitUntil(f"response 0x$address%x") { dut.io.readRsp.valid.toBoolean }
          assert(dut.io.readRsp.data.toLong == value)
          dut.clockDomain.waitSampling()
        }

        assert(!dut.skipCachedLane.toBoolean)
      }
  }
}
