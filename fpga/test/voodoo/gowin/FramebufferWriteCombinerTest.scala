package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._
import spinal.lib.bus.bmb._
import spinal.lib.bus.bmb.sim.BmbMemoryAgent
import voodoo.{Config, Core, FramebufferPlaneBuffer}
import voodoo.artix.BmbToAxi4Mem

case class FramebufferBurstPathHarness(c: Config) extends Component {
  val io = new Bundle {
    val color = slave(Stream(FramebufferPlaneBuffer.WriteReq(c)))
    val aux = slave(Stream(FramebufferPlaneBuffer.WriteReq(c)))
    val flush = in Bool ()
    val ddr = master(GowinDdrNative(dataWidth = 128))
  }

  val color = FramebufferPlaneBuffer(c)
  val aux = FramebufferPlaneBuffer(c)
  for (port <- Seq(color, aux)) {
    port.io.readReq.valid := False
    port.io.readReq.address := 0
    port.io.readRsp.ready := True
    port.io.flush := io.flush
  }
  color.io.writeReq << io.color
  aux.io.writeReq << io.aux

  val arbiter = BmbArbiter(
    inputsParameter = Seq.fill(2)(FramebufferPlaneBuffer.bmbParams(c)),
    outputParameter = Core.fbMemBmbParams(c),
    lowerFirstPriority = true
  )
  arbiter.io.inputs(0) <> color.io.mem
  arbiter.io.inputs(1) <> aux.io.mem

  val bridge = BmbToAxi4Mem(Core.fbMemBmbParams(c), CoreGowin.axiBurstLog2)
  bridge.io.input <> arbiter.io.output
  val adapter = Axi4ToGowinDdrNative(
    CoreGowin.fbAxiConfig(c),
    CoreGowin.texAxiConfig(c),
    nativeDataWidth = 128
  )
  adapter.io.fb << bridge.io.output
  adapter.io.tex.aw.valid := False
  adapter.io.tex.aw.payload.assignDontCare()
  adapter.io.tex.w.valid := False
  adapter.io.tex.w.payload.assignDontCare()
  adapter.io.tex.ar.valid := False
  adapter.io.tex.ar.payload.assignDontCare()
  adapter.io.tex.b.ready := True
  adapter.io.tex.r.ready := True
  io.ddr <> adapter.io.ddr
}

case class FramebufferBurstCdcHarness(c: Config) extends Component {
  val io = new Bundle {
    val color = slave(Stream(FramebufferPlaneBuffer.WriteReq(c)))
    val aux = slave(Stream(FramebufferPlaneBuffer.WriteReq(c)))
    val flush = in Bool ()
    val ddrClk = in Bool ()
    val ddrReset = in Bool ()
    val ddr = master(GowinDdrNative(dataWidth = 128))
  }

  val path = FramebufferBurstPathHarness(c)
  path.io.color <> io.color
  path.io.aux <> io.aux
  path.io.flush := io.flush

  val ddrCd = ClockDomain(
    clock = io.ddrClk,
    reset = io.ddrReset,
    config = ClockDomain.current.config
  )
  val cdc = GowinDdrNativeClockCrossing(
    addrWidth = GowinAddressMap.ddrNativeAddrWidth,
    dataWidth = 128,
    inputCd = ClockDomain.current,
    outputCd = ddrCd
  )
  cdc.io.input <> path.io.ddr
  io.ddr <> cdc.io.output
}

/** Regression for the Tang Console's small framebuffer write combiner.
  * The hardware failure appears while clearing a 100x100 rectangle with a
  * much wider framebuffer stride, so exercise the same row-end rotations.
  */
class FramebufferWriteCombinerTest extends AnyFunSuite {
  private def config = Config.voodoo1().copy(
    addressWidth = 20 bits,
    fbWriteBufferLineWords = 16,
    fbWriteBufferCount = 2,
    useFbWriteBuffer = true,
    useFbReadCache = false,
    useTexFillCache = false
  )

  test("direct framebuffer writes retain data and byte enables through the native path") {
    val c = config.copy(useFbWriteBuffer = false)
    SimConfig.withVerilator.compile(FramebufferBurstPathHarness(c)).doSim { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.color.valid #= false
      dut.io.aux.valid #= false
      dut.io.flush #= false
      dut.io.ddr.cmdReady #= true
      dut.io.ddr.wrDataRdy #= true
      dut.io.ddr.rdData #= 0
      dut.io.ddr.rdDataValid #= false
      dut.io.ddr.rdDataEnd #= false
      dut.io.ddr.initCalibComplete #= true
      dut.clockDomain.waitSampling(5)

      dut.io.color.address #= 0x40
      dut.io.color.data #= 0x1234abcdL
      dut.io.color.mask #= 0xf
      dut.io.color.valid #= true
      dut.clockDomain.waitSamplingWhere(dut.io.color.ready.toBoolean)
      dut.io.color.valid #= false

      dut.clockDomain.waitSamplingWhere(dut.io.ddr.cmdEn.toBoolean)
      assert(dut.io.ddr.wrData.toBigInt != 0, "native write data was lost")
      assert(dut.io.ddr.wrDataMask.toLong != 0xffffL, "all native write lanes were inhibited")
    }
  }

  test("16-word combiner drains a strided 100x100 fast-fill") {
    val c = config

    SimConfig.withVerilator.compile(FramebufferPlaneBuffer(c)).doSim { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.readReq.valid #= false
      dut.io.readReq.address #= 0
      dut.io.readRsp.ready #= true
      dut.io.writeReq.valid #= false
      dut.io.writeReq.address #= 0
      dut.io.writeReq.data #= 0
      dut.io.writeReq.mask #= 0xf
      dut.io.flush #= false

      val memory = new BmbMemoryAgent(1 << c.addressWidth.value)
      memory.addPort(dut.io.mem, 0, dut.clockDomain, withDriver = true, withStall = false)
      dut.clockDomain.waitSampling(5)

      for (y <- 0 until 100; word <- 0 until 50) {
        dut.io.writeReq.address #= y * 0x600 + word * 4
        dut.io.writeReq.data #= ((y << 16) | word)
        dut.io.writeReq.valid #= true
        var waited = 0
        do {
          dut.clockDomain.waitSampling()
          waited += 1
          assert(waited < 2000, f"combiner stalled at y=$y word=$word address=0x${y * 0x600 + word * 4}%x")
        } while (!dut.io.writeReq.ready.toBoolean)
        dut.io.writeReq.valid #= false
      }

      dut.io.flush #= true
      dut.clockDomain.waitSampling()
      dut.io.flush #= false
      var drainCycles = 0
      while (dut.io.busy.toBoolean && drainCycles < 5000) {
        dut.clockDomain.waitSampling()
        drainCycles += 1
      }
      assert(!dut.io.busy.toBoolean, s"combiner failed to drain after $drainCycles cycles")
    }
  }

  test("paired color and aux clears cross the complete native write path") {
    val c = config
    SimConfig.withVerilator.compile(FramebufferBurstPathHarness(c)).doSim { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.color.valid #= false
      dut.io.aux.valid #= false
      dut.io.flush #= false
      dut.io.ddr.cmdReady #= true
      dut.io.ddr.wrDataRdy #= true
      dut.io.ddr.rdData #= 0
      dut.io.ddr.rdDataValid #= false
      dut.io.ddr.rdDataEnd #= false
      dut.io.ddr.initCalibComplete #= true
      dut.clockDomain.waitSampling(5)

      var nativeWrites = 0
      fork {
        while (true) {
          if (dut.io.ddr.cmdEn.toBoolean && dut.io.ddr.wrDataEn.toBoolean) nativeWrites += 1
          dut.clockDomain.waitSampling()
        }
      }

      for (y <- 0 until 100; word <- 0 until 50) {
        val address = y * 0x600 + word * 4
        for (stream <- Seq(dut.io.color, dut.io.aux)) {
          stream.address #= address
          stream.data #= ((y << 16) | word)
          stream.mask #= 0xf
          stream.valid #= true
        }
        var colorPending = true
        var auxPending = true
        var waited = 0
        while (colorPending || auxPending) {
          dut.clockDomain.waitSampling()
          if (colorPending && dut.io.color.ready.toBoolean) {
            colorPending = false
            dut.io.color.valid #= false
          }
          if (auxPending && dut.io.aux.ready.toBoolean) {
            auxPending = false
            dut.io.aux.valid #= false
          }
          waited += 1
          assert(waited < 10000, s"full path stalled at y=$y word=$word native=$nativeWrites")
        }
      }

      dut.io.flush #= true
      dut.clockDomain.waitSampling()
      dut.io.flush #= false
      dut.clockDomain.waitSampling(1000)
      assert(nativeWrites >= 2400, s"too few native writes completed: $nativeWrites")
    }
  }

  test("paired clear crosses asynchronous native DDR clock boundary") {
    val c = config
    SimConfig.withVerilator.compile(FramebufferBurstCdcHarness(c)).doSim { dut =>
      dut.clockDomain.forkStimulus(50) // 20 MHz core clock
      dut.ddrCd.forkStimulus(17) // approximately 60 MHz controller clock
      dut.io.color.valid #= false
      dut.io.aux.valid #= false
      dut.io.flush #= false
      dut.io.ddr.cmdReady #= true
      dut.io.ddr.wrDataRdy #= true
      dut.io.ddr.rdData #= 0
      dut.io.ddr.rdDataValid #= false
      dut.io.ddr.rdDataEnd #= false
      dut.io.ddr.initCalibComplete #= true
      dut.clockDomain.waitSampling(5)

      var physicalWrites = 0
      fork {
        while (true) {
          if (dut.io.ddr.cmdEn.toBoolean && dut.io.ddr.wrDataEn.toBoolean) physicalWrites += 1
          dut.ddrCd.waitSampling()
        }
      }

      for (y <- 0 until 100; word <- 0 until 50) {
        val address = y * 0x600 + word * 4
        for (stream <- Seq(dut.io.color, dut.io.aux)) {
          stream.address #= address
          stream.data #= ((y << 16) | word)
          stream.mask #= 0xf
          stream.valid #= true
        }
        var colorPending = true
        var auxPending = true
        var waited = 0
        while (colorPending || auxPending) {
          dut.clockDomain.waitSampling()
          if (colorPending && dut.io.color.ready.toBoolean) {
            colorPending = false
            dut.io.color.valid #= false
          }
          if (auxPending && dut.io.aux.ready.toBoolean) {
            auxPending = false
            dut.io.aux.valid #= false
          }
          waited += 1
          assert(
            waited < 10000,
            s"CDC path stalled at y=$y word=$word physical=$physicalWrites"
          )
        }
      }

      dut.io.flush #= true
      dut.clockDomain.waitSampling()
      dut.io.flush #= false
      dut.clockDomain.waitSampling(1000)
      assert(physicalWrites >= 2400, s"too few physical writes completed: $physicalWrites")
    }
  }
}
