package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._

class DebugHudTest extends AnyFunSuite {
  test("camera-readable templates fit inside the safe margins") {
    assert(DebugHud.lines.length == DebugHud.rows)
    assert(DebugHud.lines.forall(_.length == DebugHud.cols), DebugHud.lines.mkString("|"))
    assert(DebugHud.xOrigin + DebugHud.width <= 640 - DebugHud.xOrigin)
    assert(DebugHud.height <= 480)
    assert(DebugHud.lines(0)(DebugHud.calBit) == '0')
    assert(DebugHud.lines(0)(DebugHud.underflowBit) == '0')
    assert(DebugHud.lines(1)(DebugHud.ovBit) == '0')
    assert(DebugHud.lines(1)(DebugHud.plBit) == '0')
    assert(DebugHud.lines(3)(DebugHud.statusHex) == '0')
    assert(DebugHud.lines(5)(DebugHud.pcHex) == '0')
    assert(DebugHud.lines(6)(DebugHud.prefetchHex) == '0')
    assert(DebugHud.lines(6)(DebugHud.readReqHex) == '0')
    assert(DebugHud.lines(7)(DebugHud.readRspHex) == '0')
    assert(DebugHud.lines(7)(DebugHud.readIssueHex) == '0')
    assert(DebugHud.lines(8)(DebugHud.physicalReadHex) == '0')
    assert(DebugHud.lines(8)(DebugHud.physicalReadRspHex) == '0')
    assert(DebugHud.lines(9)(DebugHud.cdcReadRspHex) == '0')
  }

  test("CAL=1 paints a glyph at the status bit") {
    SimConfig.withVerilator.compile(DebugHud()).doSim("debug-hud-cal") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.de #= true
      dut.io.calib #= true
      dut.io.wedge #= false
      dut.io.underflow #= false
      dut.io.fifoLevel #= 0
      dut.io.overlay #= true
      dut.io.playing #= false
      dut.io.debugMode #= true
      dut.io.modeKey #= false
      dut.io.resetKey #= false
      dut.io.cursor #= 0
      dut.io.hid #= 0
      dut.io.pc #= 0
      dut.io.scanoutPrefetchCount #= 0
      dut.io.scanoutReadReqCount #= 0
      dut.io.scanoutReadRspCount #= 0
      dut.io.readIssueCount #= 0
      dut.io.physicalReadCount #= 0
      dut.io.physicalReadRspCount #= 0
      dut.io.cdcReadRspCount #= 0
      dut.io.ddrStatus #= 0
      dut.io.uartLink #= false
      dut.io.uartLastCmd #= 0
      dut.io.uartRxCount #= 0
      dut.io.uartHidCount #= 0

      // '1' top row 0x18 has bits 4:3; choose font x=3 inside its 4x cell.
      dut.io.x #= DebugHud.xOrigin + DebugHud.calBit * 8 * DebugHud.scale + 13
      dut.io.y #= DebugHud.yOrigin
      dut.clockDomain.waitSampling(6)
      assert(dut.io.hit.toBoolean, "HUD region")
      assert(dut.io.r.toInt == 0xff && dut.io.g.toInt == 0xe0, s"glyph rgb ${dut.io.r.toInt}")
    }
  }

  test("UF=0 stays a dark cell, not a glyph") {
    SimConfig.withVerilator.compile(DebugHud()).doSim("debug-hud-underflow") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.de #= true
      dut.io.calib #= false
      dut.io.wedge #= false
      dut.io.underflow #= false
      dut.io.fifoLevel #= 0
      dut.io.overlay #= true
      dut.io.playing #= false
      dut.io.debugMode #= false
      dut.io.modeKey #= false
      dut.io.resetKey #= false
      dut.io.cursor #= 0
      dut.io.hid #= 0
      dut.io.pc #= 0
      dut.io.scanoutPrefetchCount #= 0
      dut.io.scanoutReadReqCount #= 0
      dut.io.scanoutReadRspCount #= 0
      dut.io.readIssueCount #= 0
      dut.io.physicalReadCount #= 0
      dut.io.physicalReadRspCount #= 0
      dut.io.cdcReadRspCount #= 0
      dut.io.ddrStatus #= 0
      dut.io.uartLink #= false
      dut.io.uartLastCmd #= 0
      dut.io.uartRxCount #= 0
      dut.io.uartHidCount #= 0
      dut.io.x #= DebugHud.xOrigin + DebugHud.underflowBit * 8 * DebugHud.scale
      dut.io.y #= DebugHud.yOrigin
      dut.clockDomain.waitSampling(6)
      assert(dut.io.hit.toBoolean)
      assert(dut.io.r.toInt == 0x28 && dut.io.g.toInt == 0x10, s"bg ${dut.io.r.toInt}")
    }
  }
}
