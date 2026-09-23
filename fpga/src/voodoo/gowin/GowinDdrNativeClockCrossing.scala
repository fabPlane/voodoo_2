package voodoo.gowin

import spinal.core._
import spinal.lib._

/** Minimal clock crossing for the one-outstanding Gowin native DDR adapter.
  *
  * Request and response payload registers remain stable while a toggle crosses
  * the clock boundary.  The synchronized toggle is therefore also the payload
  * settling time.  This costs far less routing than crossing every AXI channel
  * independently and exactly matches Axi4ToGowinDdrNative's serialization.
  */
case class GowinDdrNativeClockCrossing(
    addrWidth: Int,
    dataWidth: Int,
    inputCd: ClockDomain,
    outputCd: ClockDomain
) extends Component {
  val io = new Bundle {
    val input = slave(GowinDdrNative(addrWidth, dataWidth))
    val output = master(GowinDdrNative(addrWidth, dataWidth))
    /** Commands actually accepted by the physical controller clock domain. */
    val debugPhysicalCount = out UInt (16 bits)
    val debugPhysicalReadCount = out UInt (16 bits)
    val debugPhysicalReadRspCount = out UInt (16 bits)
    /**
      * [7:6] output FSM, [5] physical write-ready, [4] physical cmd-ready,
      * [3] calibration seen by input, [2] synchronized ack, [1] request
      * toggle, [0] input command-ready.
      */
    val debugStatus = out Bits (8 bits)
  }

  val reqToggle = RegInit(False)
  val reqCmd = Reg(UInt(3 bits)) init (0)
  val reqAddr = Reg(UInt(addrWidth bits)) init (0)
  val reqBurst = RegInit(False)
  val reqWriteData = Reg(Bits(dataWidth bits)) init (0)
  val reqWriteMask = Reg(Bits(dataWidth / 8 bits)) init (0)
  val reqWriteEnd = RegInit(False)

  // Multi-bit payloads are bundled-data CDC: each remains unchanged from
  // before reqToggle changes until the synchronized acknowledgement returns.
  // The two-flop toggle synchronizer supplies their settling interval.
  Seq(reqCmd, reqAddr, reqBurst, reqWriteData, reqWriteMask, reqWriteEnd)
    .foreach(_.addTag(crossClockDomain))

  val ackToggle = Bool()
  val rspData = Bits(dataWidth bits)
  val rspEnd = Bool()

  val inputArea = new ClockingArea(inputCd) {
    val ackSync = BufferCC(ackToggle, init = False)
    val calibSync = BufferCC(io.output.initCalibComplete, init = False)
    val readPending = RegInit(False)
    val ackPrev = RegNext(ackSync) init (False)
    val rspPulse = ackSync =/= ackPrev

    io.input.cmdReady := calibSync && ackSync === reqToggle
    io.input.wrDataRdy := calibSync && ackSync === reqToggle
    io.input.rdData := rspData
    io.input.rdDataValid := rspPulse && readPending
    io.input.rdDataEnd := rspEnd
    io.input.initCalibComplete := calibSync

    when(io.input.cmdEn && io.input.cmdReady) {
      reqCmd := io.input.cmd
      reqAddr := io.input.addr
      reqBurst := io.input.burst
      reqWriteData := io.input.wrData
      reqWriteMask := io.input.wrDataMask
      reqWriteEnd := io.input.wrDataEnd
      readPending := io.input.cmd === GowinDdrNative.cmdRead
      reqToggle := ~reqToggle
    }
  }

  val outputArea = new ClockingArea(outputCd) {
    object State extends SpinalEnum {
      val idle, issue, readWait = newElement()
    }
    val state = RegInit(State.idle)
    val reqSync = BufferCC(reqToggle, init = False)
    val ack = RegInit(False)
    // A user refresh that lands on an outstanding read never asserts
    // rd_data_valid. Waiting forever stalls scanout below its prefill and
    // the demo frame stays black. Drop that beat and accept the next command.
    val readTimer = Reg(UInt(7 bits)) init (0)
    val data = Reg(Bits(dataWidth bits)) init (0)
    val end = RegInit(False)
    val physicalCount = Reg(UInt(16 bits)) init (0)
    val physicalReadCount = Reg(UInt(16 bits)) init (0)
    val physicalReadRspCount = Reg(UInt(16 bits)) init (0)
    data.addTag(crossClockDomain)
    end.addTag(crossClockDomain)

    ackToggle := ack
    rspData := data
    rspEnd := end

    io.output.cmd := reqCmd
    io.output.addr := reqAddr
    io.output.burst := reqBurst
    io.output.wrData := reqWriteData
    io.output.wrDataMask := reqWriteMask
    io.output.wrDataEnd := reqWriteEnd
    io.output.cmdEn := False
    io.output.wrDataEn := False

    when(io.output.cmdEn) {
      physicalCount := physicalCount + 1
      when(io.output.cmd === GowinDdrNative.cmdRead) {
        physicalReadCount := physicalReadCount + 1
      }
    }
    when(io.output.rdDataValid) {
      physicalReadRspCount := physicalReadRspCount + 1
    }

    switch(state) {
      is(State.idle) {
        when(reqSync =/= ack) {
          state := State.issue
        }
      }
      is(State.issue) {
        when(reqCmd === GowinDdrNative.cmdWrite) {
          val fire = io.output.cmdReady && io.output.wrDataRdy
          io.output.cmdEn := fire
          io.output.wrDataEn := fire
          when(fire) {
            ack := reqSync
            state := State.idle
          }
        } otherwise {
          io.output.cmdEn := io.output.cmdReady
          when(io.output.cmdReady) {
            readTimer := 0
            state := State.readWait
          }
        }
      }
      is(State.readWait) {
        when(io.output.rdDataValid) {
          data := io.output.rdData
          end := io.output.rdDataEnd
          ack := reqSync
          readTimer := 0
          state := State.idle
        } elsewhen (readTimer === U(80, 7 bits)) {
          data := 0
          end := True
          ack := reqSync
          readTimer := 0
          state := State.idle
        } otherwise {
          readTimer := readTimer + 1
        }
      }
    }
  }

  val physicalCountSync = BufferCC(outputArea.physicalCount, init = U(0, 16 bits))
  val physicalReadCountSync = BufferCC(outputArea.physicalReadCount, init = U(0, 16 bits))
  val physicalReadRspCountSync = BufferCC(outputArea.physicalReadRspCount, init = U(0, 16 bits))
  val physicalCmdReadySync = BufferCC(io.output.cmdReady, init = False)
  val physicalWrReadySync = BufferCC(io.output.wrDataRdy, init = False)
  val outputStateSync = BufferCC(outputArea.state.asBits, init = B(0, 2 bits))
  io.debugPhysicalCount := physicalCountSync
  io.debugPhysicalReadCount := physicalReadCountSync
  io.debugPhysicalReadRspCount := physicalReadRspCountSync
  io.debugStatus := outputStateSync ## physicalWrReadySync ## physicalCmdReadySync ##
    inputArea.calibSync ## inputArea.ackSync ## reqToggle ## io.input.cmdReady
}
