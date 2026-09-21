package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core.sim._
import voodoo.Config
import voodoo.hdmi.{HdmiCdcFramebufferScanout, VideoTiming}

import scala.collection.mutable
import scala.language.reflectiveCalls

class HdmiScanoutPrefetchTest extends AnyFunSuite {
  test("repeated 640-pixel scanout uses boundary-safe prefetch chunks") {
    val timing = VideoTiming(
      activeWidth = 640,
      activeHeight = 1,
      hFrontPorch = 1,
      hSync = 1,
      hBackPorch = 1,
      vFrontPorch = 1,
      vSync = 1,
      vBackPorch = 1
    )

    SimConfig.withVerilator
      .compile(
        HdmiCdcFramebufferScanout(
          Config.voodoo1(),
          timing,
          fifoDepth = 512,
          prefillLevel = 384,
          refillLowLevel = 128,
          refillHighLevel = 448,
          pixelRepeatX = 2
        )
      )
      .doSim { dut =>
        dut.clockDomain.forkStimulus(10)
        dut.io.hdmiClock #= false
        dut.io.hdmiReset #= true
        dut.io.regs.frontBase #= 0x96000
        dut.io.regs.backBase #= 0
        dut.io.regs.pixelStride #= 640
        dut.io.regs.displayWidth #= 640
        dut.io.regs.displayHeight #= 1
        dut.io.regs.framebufferEnable #= true
        dut.io.regs.testPatternEnable #= false
        for (i <- 0 until 33) dut.io.regs.gammaLut(i) #= 0
        dut.io.prefetchReq.ready #= true
        dut.io.readReq.ready #= true
        dut.io.readRsp.valid #= false
        dut.io.readRsp.payload.data #= 0

        val spans = mutable.ArrayBuffer[(Long, Long)]()
        var cycles = 0
        while (spans.size < 5 && cycles < 1000) {
          dut.clockDomain.waitSampling()
          if (dut.io.prefetchReq.valid.toBoolean && dut.io.prefetchReq.ready.toBoolean) {
            val start = dut.io.prefetchReq.startAddress.toLong
            if (start >= 0x96000L)
              spans += (start -> dut.io.prefetchReq.endAddress.toLong)
          }
          cycles += 1
        }

        val expected = Set(
            0x96000L -> 0x960fcL,
            0x96100L -> 0x961fcL,
            0x96200L -> 0x962fcL,
            0x96300L -> 0x963fcL,
            0x96400L -> 0x964fcL
        )
        assert(spans.toSet == expected)
        assert(spans.forall { case (start, end) => (start >> 12) == (end >> 12) })
      }
  }
}
