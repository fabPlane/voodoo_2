package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._
import spinal.lib.bus.bmb._
import voodoo.artix.AxiLite4ToBmbBridge

/** AxiLite4ToBmbBridge with posted writes: up to `maxWrites` writes are on
  * the BMB bus at once and their B responses are returned as the BMB write
  * responses come back (in order). The one-outstanding bridge waited for
  * each write's response before taking the next AW/W, about 7 core cycles a
  * register write: the host could not send more than ~7.5 M writes/s
  * (~134 ns each, 2026-10-30), Quake II's ~57k writes a frame ~7.5 ms.
  *
  * A read still goes alone: it is issued once every earlier write has been
  * answered, so it sees them (the PCIe target also holds reads until its
  * earlier writes' B are in). Timeouts and the wedged state work as in the
  * one-outstanding bridge: a stuck command or response completes with
  * SLVERR (every write in flight with it), the bridge wedges, later writes
  * are dropped with SLVERR and reads other than the status word fail.
  */
case class AxiLite4ToBmbPostedBridge(
    addressWidth: Int,
    bmbParams: BmbParameter,
    timeoutLog2: Int = 20,
    maxWrites: Int = 8
) extends Component {
  import AxiLite4ToBmbBridge._
  require(maxWrites >= 1)

  val io = new Bundle {
    val axi = slave(AxiLite4(axiConfig(addressWidth)))
    val cpuBus = master(Bmb(bmbParams))
    val wedged = out Bool ()
  }

  private val timeoutLimit = 1 << timeoutLog2
  private val cntW = log2Up(2 * maxWrites + 2)

  val inFlight = Reg(UInt(cntW bits)) init (0) // BMB commands awaiting a response
  val readBusy = RegInit(False) // the one in flight is a read
  val bOwed = Reg(UInt(cntW bits)) init (0) // B responses to give
  val bError = RegInit(False) // the owed B responses carry SLVERR
  val rPending = RegInit(False)
  val rError = RegInit(False)
  val rData = Reg(Bits(32 bits)) init (0)

  val wedged = RegInit(False)
  val requestTimeoutSeen = RegInit(False)
  val responseTimeoutSeen = RegInit(False)
  val droppedWrites = Reg(UInt(8 bits)) init (0)
  val stallCycles = Reg(UInt((timeoutLog2 + 1) bits)) init (0)

  // Request selection. A write needs both AW and W; writes win over reads.
  val writeReq = io.axi.aw.valid && io.axi.w.valid
  val readReq = io.axi.ar.valid && !writeReq
  val reqAddress = Mux(writeReq, io.axi.aw.addr, io.axi.ar.addr)
  val wordAddress = reqAddress(addressWidth - 1 downto 2) @@ U(0, 2 bits)

  val canWrite = !readBusy && !rPending && inFlight < maxWrites && bOwed < maxWrites
  val canRead = inFlight === 0 && !rPending

  val statusReadReq = readReq && wordAddress === statusAddress
  val localReadReq = statusReadReq || (readReq && wedged)
  val localWriteReq = writeReq && wedged

  val issue = (writeReq && canWrite && !localWriteReq) || (readReq && canRead && !localReadReq)
  val stallLimit = stallCycles === U(timeoutLimit - 1, stallCycles.getWidth bits)
  val requestTimeout = issue && stallLimit
  val cmdFire = io.cpuBus.cmd.fire
  val rspFire = io.cpuBus.rsp.fire && io.cpuBus.rsp.last && inFlight =/= 0

  io.cpuBus.cmd.valid := issue && !stallLimit
  io.cpuBus.cmd.last := True
  io.cpuBus.cmd.source := 0
  io.cpuBus.cmd.address := wordAddress
  io.cpuBus.cmd.length := 3
  io.cpuBus.cmd.data := io.axi.w.data
  io.cpuBus.cmd.mask := io.axi.w.strb
  io.cpuBus.cmd.opcode := Bmb.Cmd.Opcode.READ
  when(writeReq) { io.cpuBus.cmd.opcode := Bmb.Cmd.Opcode.WRITE }
  io.cpuBus.rsp.ready := True

  val status = B(32 bits, default -> False)
  status(0) := wedged
  status(1) := requestTimeoutSeen
  status(2) := responseTimeoutSeen
  status(4) := inFlight =/= 0
  status(5) := readBusy
  status(8, 8 bits) := droppedWrites.asBits

  val localWriteAccept = localWriteReq && bOwed < maxWrites
  val localReadAccept = localReadReq && canRead
  val writeAccept = writeReq && (cmdFire || localWriteAccept || requestTimeout)
  val readAccept = readReq && (cmdFire || localReadAccept || requestTimeout)
  io.axi.aw.ready := writeAccept
  io.axi.w.ready := writeAccept
  io.axi.ar.ready := readAccept

  io.axi.b.valid := bOwed =/= 0
  io.axi.b.setOKAY()
  when(bError) { io.axi.b.setSLVERR() }
  io.axi.r.valid := rPending
  io.axi.r.data := rData
  io.axi.r.setOKAY()
  when(rError) { io.axi.r.setSLVERR() }
  io.wedged := wedged

  val stalling = (issue && !cmdFire) || (inFlight =/= 0 && !rspFire)
  when(stalling) {
    when(!stallLimit) { stallCycles := stallCycles + 1 }
  } otherwise {
    stallCycles := 0
  }
  val responseTimeout = inFlight =/= 0 && !rspFire && stallLimit

  def countDroppedWrite(): Unit =
    when(droppedWrites =/= droppedWrites.maxValue) { droppedWrites := droppedWrites + 1 }

  // B responses owed, summed: writes answered by the core, writes failed
  // here (several can land in one cycle once wedged).
  val bRsp, bLocal, bReqTimeout = Bool()
  val bRspTimeout = UInt(cntW bits)
  bRsp := rspFire && !readBusy
  bLocal := False
  bReqTimeout := False
  bRspTimeout := 0
  val bErrSet = Bool()
  bErrSet := bRsp && io.cpuBus.rsp.isError
  // inFlight: + issued, - answered.
  val inFlightNext = UInt(cntW bits)
  inFlightNext := inFlight + U(cmdFire) - U(rspFire)

  when(cmdFire && !writeReq) { readBusy := True }
  when(rspFire && readBusy) {
    readBusy := False
    rPending := True
    rData := io.cpuBus.rsp.data
    rError := io.cpuBus.rsp.isError
  }
  when(io.axi.r.fire) { rPending := False }

  when(responseTimeout) {
    wedged := True
    responseTimeoutSeen := True
    when(readBusy) {
      readBusy := False
      rPending := True
      rData := 0
      rError := True
    } otherwise {
      bRspTimeout := inFlight // every write in flight fails
      bErrSet := True
    }
    inFlightNext := 0
  }
  when(requestTimeout) {
    wedged := True
    requestTimeoutSeen := True
    when(writeReq) {
      countDroppedWrite()
      bReqTimeout := True
      bErrSet := True
    } otherwise {
      rPending := True
      rData := 0
      rError := True
    }
  }
  when(localWriteAccept) {
    countDroppedWrite()
    bLocal := True
    bErrSet := True
  }
  when(localReadAccept) {
    rPending := True
    when(statusReadReq) {
      rData := status
      rError := False
    } otherwise {
      rData := 0
      rError := True
    }
  }

  inFlight := inFlightNext
  val bOwedNext = bOwed + U(bRsp).resize(cntW) + U(bLocal).resize(cntW) + U(bReqTimeout).resize(cntW) +
    bRspTimeout - U(io.axi.b.fire).resize(cntW)
  bOwed := bOwedNext
  when(bErrSet) { bError := True } elsewhen (bOwedNext === 0) { bError := False }
}
