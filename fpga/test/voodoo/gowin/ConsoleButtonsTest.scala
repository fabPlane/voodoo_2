package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core.sim._

class ConsoleButtonsTest extends AnyFunSuite {
  private lazy val compiled = SimConfig.withVerilator.compile(
    ConsoleButtons(armCycles = 8, selectHoldCycles = 6)
  )

  test("holding MODE at startup latches diagnostics and blocks menu buttons") {
    compiled.doSim("console-buttons-debug") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.modeN #= false
      dut.io.resetN #= true
      dut.clockDomain.waitSampling(16)
      assert(dut.io.debugMode.toBoolean)
      assert(!dut.io.menuDown.toBoolean)

      dut.io.resetN #= false
      dut.clockDomain.waitSampling(6)
      assert(dut.io.resetPressed.toBoolean)
      assert(!dut.io.menuSelect.toBoolean)
    }
  }

  test("a MODE glitch during the startup window does not latch diagnostics") {
    compiled.doSim("console-buttons-glitch") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.modeN #= true
      dut.io.resetN #= true
      dut.clockDomain.waitSampling(3)
      dut.io.modeN #= false
      dut.clockDomain.waitSampling(2)
      dut.io.modeN #= true
      dut.clockDomain.waitSampling(16)
      assert(!dut.io.debugMode.toBoolean)
    }
  }

  test("a MODE press after the startup window remains a local menu pulse") {
    compiled.doSim("console-buttons-normal") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.modeN #= true
      dut.io.resetN #= true
      dut.clockDomain.waitSampling(16)
      assert(!dut.io.debugMode.toBoolean)

      dut.io.modeN #= false
      var sawPulse = false
      for (_ <- 0 until 8) {
        dut.clockDomain.waitSampling()
        sawPulse ||= dut.io.menuDown.toBoolean
      }
      assert(sawPulse)
      assert(!dut.io.debugMode.toBoolean)
    }
  }

  test("holding MODE after startup selects exactly once without entering diagnostics") {
    compiled.doSim("console-buttons-long-select") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.modeN #= true
      dut.io.resetN #= true
      dut.clockDomain.waitSampling(12)

      dut.io.modeN #= false
      var selectPulses = 0
      for (_ <- 0 until 20) {
        dut.clockDomain.waitSampling()
        if (dut.io.menuSelect.toBoolean) selectPulses += 1
      }
      assert(selectPulses == 1)
      assert(!dut.io.debugMode.toBoolean)
    }
  }
}
