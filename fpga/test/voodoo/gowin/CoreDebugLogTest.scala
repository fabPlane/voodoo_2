package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._

class CoreDebugLogTest extends AnyFunSuite {
  private lazy val compiled =
    SimConfig.withVerilator.compile(CoreDebugLog(period = 64, pace = 2))

  private def collectLine(dut: CoreDebugLog, limit: Int): String = {
    val buf = new StringBuilder
    var cycles = 0
    while (!buf.toString.contains('\n') && cycles < limit) {
      if (dut.io.tx.valid.toBoolean) {
        buf += dut.io.tx.payload.toInt.toChar
      }
      dut.clockDomain.waitSampling()
      cycles += 1
    }
    buf.toString
  }

  test("first line snapshots calib, wedge, pc and BAR traffic") {
    compiled.doSim("core-debug-log-line") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.calib #= true
      dut.io.wedge #= false
      dut.io.overlay #= true
      dut.io.playing #= false
      dut.io.info #= true
      dut.io.cursor #= 1
      dut.io.pc #= 0x80000004L
      dut.io.barAddr #= 0x21cL
      dut.io.barData #= 0x00110000L
      dut.io.barCount #= 2
      dut.io.hid #= 0x0100
      dut.io.usbOnline #= true
      dut.io.debugMode #= true
      dut.io.modeKey #= false
      dut.io.resetKey #= true
      dut.io.uartLink #= true
      dut.io.uartLastCmd #= 9
      dut.io.uartRxCount #= 0x1234
      dut.io.uartHidCount #= 7
      dut.clockDomain.waitSampling()

      val line = collectLine(dut, 2048)
      assert(line.endsWith("\n"), s"incomplete line: $line")
      assert(line.contains("cal=1"), line)
      assert(line.contains("wed=0"), line)
      assert(line.contains("ov=1"), line)
      assert(line.contains("pl=0"), line)
      assert(line.contains("inf=1"), line)
      assert(line.contains("cur=1"), line)
      assert(line.contains("pc=80000004"), line)
      assert(line.contains("bar=0000021C:00110000"), line)
      assert(line.contains("n=0002"), line)
      assert(line.contains("hid=0100"), line)
      assert(line.contains("usb=1"), line)
      assert(line.startsWith("V2DBG "), line)
      assert(line.contains("dbg=1"), line)
      assert(line.contains("key=01"), line)
      assert(line.contains("uart=1/09"), line)
      assert(line.contains("rx=1234"), line)
      assert(line.contains("hc=0007"), line)
    }
  }

  test("second line sees later BAR writes") {
    compiled.doSim("core-debug-log-update") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.calib #= false
      dut.io.wedge #= true
      dut.io.overlay #= false
      dut.io.playing #= true
      dut.io.info #= false
      dut.io.cursor #= 0
      dut.io.pc #= 0
      dut.io.barAddr #= 0
      dut.io.barData #= 0
      dut.io.barCount #= 0
      dut.io.hid #= 0
      dut.io.usbOnline #= false
      dut.io.debugMode #= false
      dut.io.modeKey #= false
      dut.io.resetKey #= false
      dut.io.uartLink #= false
      dut.io.uartLastCmd #= 0
      dut.io.uartRxCount #= 0
      dut.io.uartHidCount #= 0
      dut.clockDomain.waitSampling()

      val first = collectLine(dut, 2048)
      assert(first.contains("cal=0"), first)
      dut.io.calib #= true
      dut.io.wedge #= false
      dut.io.barAddr #= 0x120L
      dut.io.barData #= 1
      dut.io.barCount #= 3
      val second = collectLine(dut, 4096)
      assert(second.contains("cal=1"), second)
      assert(second.contains("wed=0"), second)
      assert(second.contains("bar=00000120:00000001"), second)
      assert(second.contains("n=0003"), second)
    }
  }
}
