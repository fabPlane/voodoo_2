package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._

class Bl616LinkTest extends AnyFunSuite {
  private val cyclesPerBit = 8

  private lazy val compiled =
    SimConfig.withVerilator.compile(Bl616Link(cyclesPerBit))

  private def sendByte(dut: Bl616Link, value: Int): Unit = {
    dut.io.uartRx #= false
    dut.clockDomain.waitSampling(cyclesPerBit)
    for (i <- 0 until 8) {
      dut.io.uartRx #= ((value >> i) & 1) != 0
      dut.clockDomain.waitSampling(cyclesPerBit)
    }
    dut.io.uartRx #= true
    dut.clockDomain.waitSampling(cyclesPerBit)
  }

  private def sendFrame(dut: Bl616Link, payload: Seq[Int]): Unit = {
    sendByte(dut, 0xaa)
    sendByte(dut, (payload.length >> 8) & 0xff)
    sendByte(dut, payload.length & 0xff)
    payload.foreach(sendByte(dut, _))
    dut.clockDomain.waitSampling(cyclesPerBit * 2)
  }

  test("UART command 0x09 updates USB HID pads in SNES layout") {
    compiled.doSim("bl616-hid") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.uartRx #= true
      dut.io.joy1 #= 0
      dut.io.joy2 #= 0
      dut.clockDomain.waitSampling(4)

      val hid1 = (1 << SnesPad.Up) | (1 << SnesPad.A)
      sendFrame(dut, Seq(0x09, (hid1 >> 8) & 0xff, hid1 & 0xff, 0x00, 0x02))

      assert((dut.io.hid1.toLong & 0xffff) == hid1)
      assert((dut.io.hid2.toLong & 0xffff) == 0x0002)
    }
  }
}
