package voodoo.artix

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.amba4.axilite._
import spinal.lib.bus.bmb._
import voodoo.{Config, Core}
import voodoo.hdmi.HdmiScanoutPort

/** Artix-7 wrapper around Core. This is the port of voodoo.de10.CoreDe10.
  *
  * What changed against the DE10 version:
  *   - Host path: the HPS lightweight bridge (H2fLwMmio, Avalon-like) becomes an AXI4-Lite slave.
  *     On the phase-1 PCIe dev board this is BAR0 of the Xilinx PCIe block; on the card the PCI
  *     target and the T113 SPI link produce the same bus.
  *   - Memory path: the six BMB->Avalon-MM bridges into the HPS DDR3 (De10MemBackend) become a
  *     BMB arbiter plus BMB->AXI4 bridges. The framebuffer buses share one AXI4 master, textures
  *     get their own. Both go to MIG DDR3 through an AXI interconnect in the Vivado design.
  *   - Video: unchanged. HdmiScanoutPort is parallel RGB and drives the ADV7123 DAC directly.
  */
object AxiLite4ToBmbBridge {
  /** Byte offset of the bridge status word, same as the DE10 H2f bridge so host tooling carries over.
    * bit 0 wedged, bit 1 request timeout seen, bit 2 response timeout seen, bit 4 command in flight,
    * bit 5 read in flight, bits 15:8 dropped writes.
    */
  val statusAddress = 0x254

  def axiConfig(addressWidth: Int) = AxiLite4Config(addressWidth = addressWidth, dataWidth = 32)
}

/** AXI4-Lite slave to BMB master, one transaction outstanding.
  *
  * Like the DE10 bridge it never lets the host hang: if the core does not accept a command or
  * does not answer within 2^timeoutLog2 cycles the AXI transaction completes with SLVERR, the
  * bridge marks itself wedged, drops further writes and answers further reads with SLVERR. The
  * status word stays readable so the host can see what happened.
  */
case class AxiLite4ToBmbBridge(
    addressWidth: Int,
    bmbParams: BmbParameter,
    timeoutLog2: Int = 20
) extends Component {
  import AxiLite4ToBmbBridge._

  val io = new Bundle {
    val axi = slave(AxiLite4(axiConfig(addressWidth)))
    val cpuBus = master(Bmb(bmbParams))
    val wedged = out Bool ()
  }

  private val timeoutLimit = 1 << timeoutLog2

  // AXI response holding registers.
  val bPending = RegInit(False)
  val bError = RegInit(False)
  val rPending = RegInit(False)
  val rError = RegInit(False)
  val rData = Reg(Bits(32 bits)) init (0)

  val busy = RegInit(False)
  val busyIsWrite = RegInit(False)
  val wedged = RegInit(False)
  val requestTimeoutSeen = RegInit(False)
  val responseTimeoutSeen = RegInit(False)
  val droppedWrites = Reg(UInt(8 bits)) init (0)
  val stallCycles = Reg(UInt((timeoutLog2 + 1) bits)) init (0)

  // Request selection. A write needs both AW and W; writes win over reads.
  val writeReq = io.axi.aw.valid && io.axi.w.valid
  val readReq = io.axi.ar.valid && !writeReq
  val hasReq = writeReq || readReq
  val reqAddress = Mux(writeReq, io.axi.aw.addr, io.axi.ar.addr)
  val wordAddress = reqAddress(addressWidth - 1 downto 2) @@ U(0, 2 bits)
  val canAccept = !busy && !bPending && !rPending

  val statusReadReq = readReq && wordAddress === statusAddress
  val localReadReq = statusReadReq || (readReq && wedged)
  val localWriteReq = writeReq && wedged
  val localReq = localReadReq || localWriteReq

  val issue = hasReq && canAccept && !localReq
  val stallLimit = stallCycles === U(timeoutLimit - 1, stallCycles.getWidth bits)
  val requestTimeout = issue && stallLimit
  val cmdFire = io.cpuBus.cmd.fire

  io.cpuBus.cmd.valid := issue && !stallLimit
  io.cpuBus.cmd.last := True
  io.cpuBus.cmd.source := 0
  io.cpuBus.cmd.address := wordAddress
  io.cpuBus.cmd.length := 3
  io.cpuBus.cmd.data := io.axi.w.data
  io.cpuBus.cmd.mask := io.axi.w.strb
  io.cpuBus.cmd.opcode := Bmb.Cmd.Opcode.READ
  when(writeReq) {
    io.cpuBus.cmd.opcode := Bmb.Cmd.Opcode.WRITE
  }
  io.cpuBus.rsp.ready := True

  val status = B(32 bits, default -> False)
  status(0) := wedged
  status(1) := requestTimeoutSeen
  status(2) := responseTimeoutSeen
  status(4) := busy
  status(5) := busy && !busyIsWrite
  status(8, 8 bits) := droppedWrites.asBits

  val localAccept = hasReq && canAccept && localReq
  val writeAccept = writeReq && (cmdFire || localAccept || requestTimeout)
  val readAccept = readReq && (cmdFire || localAccept || requestTimeout)
  io.axi.aw.ready := writeAccept
  io.axi.w.ready := writeAccept
  io.axi.ar.ready := readAccept

  io.axi.b.valid := bPending
  io.axi.b.setOKAY()
  when(bError) {
    io.axi.b.setSLVERR()
  }
  io.axi.r.valid := rPending
  io.axi.r.data := rData
  io.axi.r.setOKAY()
  when(rError) {
    io.axi.r.setSLVERR()
  }
  io.wedged := wedged

  when(io.axi.b.fire) {
    bPending := False
  }
  when(io.axi.r.fire) {
    rPending := False
  }

  val stalling = (issue && !cmdFire) || (busy && !io.cpuBus.rsp.fire)
  when(stalling) {
    when(!stallLimit) {
      stallCycles := stallCycles + 1
    }
  } otherwise {
    stallCycles := 0
  }

  def completeWithError(isWrite: Bool): Unit = {
    when(isWrite) {
      bPending := True
      bError := True
    } otherwise {
      rPending := True
      rData := 0
      rError := True
    }
  }

  def countDroppedWrite(): Unit = {
    when(droppedWrites =/= droppedWrites.maxValue) {
      droppedWrites := droppedWrites + 1
    }
  }

  when(cmdFire) {
    busy := True
    busyIsWrite := writeReq
  }

  val responseTimeout = busy && !io.cpuBus.rsp.fire && stallLimit
  when(busy && io.cpuBus.rsp.fire && io.cpuBus.rsp.last) {
    busy := False
    when(busyIsWrite) {
      bPending := True
      bError := io.cpuBus.rsp.isError
    } otherwise {
      rPending := True
      rData := io.cpuBus.rsp.data
      rError := io.cpuBus.rsp.isError
    }
  }
  when(responseTimeout) {
    busy := False
    wedged := True
    responseTimeoutSeen := True
    completeWithError(busyIsWrite)
  }
  when(requestTimeout) {
    wedged := True
    requestTimeoutSeen := True
    when(writeReq) {
      countDroppedWrite()
    }
    completeWithError(writeReq)
  }
  when(localAccept) {
    when(localWriteReq) {
      countDroppedWrite()
      completeWithError(True)
    }
    when(statusReadReq) {
      rPending := True
      rData := status
      rError := False
    } elsewhen (localReadReq) {
      completeWithError(False)
    }
  }
}

/** BMB master to AXI4 master for MIG: aligns and splits every burst to 2^burstLog2 bytes so
  * nothing crosses a 4 KiB boundary or exceeds AXI's 256-beat limit, then bridges to AXI4.
  */
/** Splits read bursts at 4 KiB pages. BMB and AXI bursts may not cross a
  * page, and the library bridges (BmbAligner, BmbLengthFixer, even
  * BmbAlignedSpliter via Bmb.addToAddress) only increment in-page address
  * bits, so a crossing burst wrapped to the page start. The core's write
  * buffer never crosses, but framebuffer reader spans (scanout lines, pixel
  * spans) can: on the Gowin board every 640-pixel scanline crossing a page
  * showed data from 4 KiB earlier. Writes pass through. Pieces keep the
  * input's source/context; the memory side must answer in order (it does on
  * the fb/tex ports here), and only the final piece's response is `last`.
  */
case class BmbPageSplitter(p: BmbParameter, pendingMax: Int = 16) extends Component {
  val io = new Bundle {
    val input = slave(Bmb(p))
    val output = master(Bmb(p))
  }
  private val pageBits = 12
  private val totalWidth = p.access.lengthWidth + 1

  // A read is taken into registers and cut into pieces from there, so no
  // address arithmetic reaches input.cmd.ready (that path closed timing badly
  // on the congested Gowin build). Writes pass straight through.
  val busy = RegInit(False)
  val cur = Reg(Fragment(BmbCmd(p)))
  val remaining = Reg(UInt(totalWidth bits))
  val toPageEnd = U(1 << pageBits, pageBits + 1 bits) - cur.address(pageBits - 1 downto 0).resize(pageBits + 1)
  val finalPiece = remaining.resize(totalWidth max (pageBits + 1)) <= toPageEnd.resized
  val pieceBytes = finalPiece ? remaining | toPageEnd.resize(totalWidth)

  // One entry per output command: is it the input burst's final piece?
  val finals = StreamFifo(Bool(), pendingMax)

  val passWrite = !busy && io.input.cmd.isWrite
  io.output.cmd.valid := finals.io.push.ready && (busy || (io.input.cmd.valid && io.input.cmd.isWrite))
  io.output.cmd.payload := io.input.cmd.payload
  when(busy) {
    io.output.cmd.payload := cur
    io.output.cmd.length.allowOverride()
    io.output.cmd.length := (pieceBytes - 1).resized
  }
  io.input.cmd.ready := !busy && (io.input.cmd.isRead || (io.output.cmd.ready && finals.io.push.ready))

  when(!busy && io.input.cmd.valid && io.input.cmd.isRead) {
    busy := True
    cur := io.input.cmd.payload
    remaining := io.input.cmd.length.resize(totalWidth) + 1
  }
  when(busy && io.output.cmd.fire) {
    cur.address := cur.address + pieceBytes.resized
    remaining := remaining - pieceBytes
    when(finalPiece) { busy := False }
  }

  finals.io.push.valid := io.output.cmd.fire && io.output.cmd.last
  finals.io.push.payload := !busy || finalPiece

  io.input.rsp.valid := io.output.rsp.valid
  io.input.rsp.payload := io.output.rsp.payload
  io.input.rsp.last.allowOverride()
  io.input.rsp.last := io.output.rsp.last && finals.io.pop.payload
  io.output.rsp.ready := io.input.rsp.ready
  finals.io.pop.ready := io.output.rsp.fire && io.output.rsp.last
}

object BmbToAxi4Mem {
  // Input bursts may cross 4 KiB pages: BmbPageSplitter cuts reads first, so
  // BmbAligner and BmbLengthFixer (in-page increments only) are safe.
  def alignedParameter(p: BmbParameter, burstLog2: Int): BmbParameter = {
    val aligned = BmbAligner.outputParameter(p.access, burstLog2)
    BmbLengthFixer.outputParameter(aligned, burstLog2).toBmbParameter()
  }

  def axiConfig(p: BmbParameter, burstLog2: Int): Axi4Config =
    BmbToAxi4SharedBridge.getAxi4Config(alignedParameter(p, burstLog2))
}

/** `splitPages`: needed where bursts can cross a 4 KiB page (framebuffer
  * reader spans). Texture fetches are small aligned reads and skip it. */
/** `pendingMax`: transactions in flight per source in the AXI bridge; its
  * response-info queues are flip-flops, so the default 31 cost ~1.3k
  * registers per port on Gowin. The DDR adapter keeps 8 reads in flight. */
case class BmbToAxi4Mem(p: BmbParameter, burstLog2: Int, splitPages: Boolean = true, pendingMax: Int = 31) extends Component {
  val io = new Bundle {
    val input = slave(Bmb(p))
    val output = master(Axi4(BmbToAxi4Mem.axiConfig(p, burstLog2)))
  }

  val aligner = BmbAligner(p, burstLog2)
  val fixer = BmbLengthFixer(aligner.op.toBmbParameter(), burstLog2)
  val bridge = BmbToAxi4SharedBridge(fixer.op.toBmbParameter(), pendingMax = pendingMax)

  if (splitPages) {
    val pageSplit = BmbPageSplitter(p)
    pageSplit.io.input <> io.input
    aligner.io.input <> pageSplit.io.output
  } else {
    aligner.io.input <> io.input
  }
  fixer.io.input <> aligner.io.output
  bridge.io.input <> fixer.io.output
  io.output << bridge.io.output.toAxi4()
}

object CoreArtix {
  val barAddressWidth = Core.cpuBmbParams.access.addressWidth

  /** 256-byte AXI bursts (64 beats of 32 bits). */
  val axiBurstLog2 = 8

  def fbAxiConfig(c: Config) = BmbToAxi4Mem.axiConfig(Core.fbMemSharedBmbParams(c), axiBurstLog2)
  def texAxiConfig(c: Config) = BmbToAxi4Mem.axiConfig(Core.texMemBmbParams(c), axiBurstLog2)
}

case class CoreArtix(c: Config) extends Component {
  val io = new Bundle {
    val bar = slave(AxiLite4(AxiLite4ToBmbBridge.axiConfig(CoreArtix.barAddressWidth)))
    val memFb = master(Axi4(CoreArtix.fbAxiConfig(c)))
    val memTex = master(Axi4(CoreArtix.texAxiConfig(c)))
    val hdmi = master(HdmiScanoutPort(c))
    val hostWedged = out Bool ()
  }

  val core = Core(c)
  val barBridge = AxiLite4ToBmbBridge(CoreArtix.barAddressWidth, Core.cpuBmbParams)

  // Same five-way framebuffer arbitration CoreSim uses; the DE10 backend had one Avalon port per bus.
  val fbArbiter = BmbArbiter(
    inputsParameter = Seq.fill(5)(Core.fbMemBmbParams(c)),
    outputParameter = Core.fbMemSharedBmbParams(c),
    lowerFirstPriority = true
  )
  val fbAxi = BmbToAxi4Mem(Core.fbMemSharedBmbParams(c), CoreArtix.axiBurstLog2)
  val texAxi = BmbToAxi4Mem(Core.texMemBmbParams(c), CoreArtix.axiBurstLog2, splitPages = false)

  io.bar <> barBridge.io.axi
  core.io.cpuBus <> barBridge.io.cpuBus
  io.hostWedged := barBridge.io.wedged

  fbArbiter.io.inputs(0) <> core.io.fbMemWrite
  fbArbiter.io.inputs(1) <> core.io.fbColorWriteMem
  fbArbiter.io.inputs(2) <> core.io.fbAuxWriteMem
  fbArbiter.io.inputs(3) <> core.io.fbColorReadMem
  fbArbiter.io.inputs(4) <> core.io.fbAuxReadMem
  fbAxi.io.input <> fbArbiter.io.output
  texAxi.io.input <> core.io.texMem
  io.memFb << fbAxi.io.output
  io.memTex << texAxi.io.output

  core.io.statusInputs.vRetrace := core.io.hdmi.status.frameDone
  core.io.statusInputs.memFifoFree := 0xffff
  core.io.statusInputs.pciInterrupt := False
  core.io.hdmi.clock := io.hdmi.clock
  core.io.hdmi.reset := io.hdmi.reset
  io.hdmi.video := core.io.hdmi.video
  io.hdmi.status := core.io.hdmi.status
  io.hdmi.underflow := core.io.hdmi.underflow
  io.hdmi.fifoLevel := core.io.hdmi.fifoLevel

  core.io.fbBaseAddr := 0
  core.io.flushFbCaches := False
}
