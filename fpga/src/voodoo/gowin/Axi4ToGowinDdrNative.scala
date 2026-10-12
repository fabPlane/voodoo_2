package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._

object GowinDdrNative {
  val cmdWrite = 0
  val cmdRead = 1
  def beatBytes(dataWidth: Int = GowinAddressMap.ddrNativeDataWidth): Int =
    dataWidth / 8
}

case class GowinDdrNative(
    addrWidth: Int = GowinAddressMap.ddrNativeAddrWidth,
    dataWidth: Int = GowinAddressMap.ddrNativeDataWidth
) extends Bundle with IMasterSlave {
  val cmdReady = Bool()
  val cmd = UInt(3 bits)
  val cmdEn = Bool()
  val addr = UInt(addrWidth bits)
  val burst = Bool()
  val wrDataRdy = Bool()
  val wrData = Bits(dataWidth bits)
  val wrDataEn = Bool()
  val wrDataEnd = Bool()
  val wrDataMask = Bits(dataWidth / 8 bits)
  val rdData = Bits(dataWidth bits)
  val rdDataValid = Bool()
  val rdDataEnd = Bool()
  val initCalibComplete = Bool()

  override def asMaster(): Unit = {
    out(cmd, cmdEn, addr, burst, wrData, wrDataEn, wrDataEnd, wrDataMask)
    in(cmdReady, wrDataRdy, rdData, rdDataValid, rdDataEnd, initCalibComplete)
  }
}

/** AXI4 (32-bit INCR) to the Gowin DDR3 native UI, with reads pipelined.
  *
  * Up to `readsInFlight` native read commands are outstanding: the
  * controller returns read data in command order and cannot be stalled, so
  * every issued read holds a slot in `rdData` (a credit) and an entry in
  * `rdMeta` saying which AXI port, ID and 32-bit words of the line it
  * answers. Writes collect one native line (masked lanes) and are issued with
  * their data; B is returned after the last line of the burst is issued.
  * Reads and writes share the command port, alternating when both wait.
  *
  * Ordering: the controller executes commands in order, and each port's
  * BmbToAxi4SharedBridge waits for outstanding responses before switching
  * between reads and writes, so a read never overtakes an earlier write it
  * depends on. Framebuffer wins over texture for new bursts.
  *
  * wr_data_mask is active-high inhibit, the inverse of AXI wstrb.
  * nativeDataWidth is 256 on the 32-bit Pro Dock and 128 on the Console.
  */
case class Axi4ToGowinDdrNative(
    fbConfig: Axi4Config,
    texConfig: Axi4Config,
    nativeDataWidth: Int = GowinAddressMap.ddrNativeDataWidth,
    nativeAddrWidth: Int = GowinAddressMap.ddrNativeAddrWidth,
    readsInFlight: Int = 8
) extends Component {
  require(fbConfig.dataWidth == 32 && texConfig.dataWidth == 32)
  require(nativeDataWidth % 32 == 0 && nativeDataWidth >= 32)
  require(isPow2(readsInFlight) && readsInFlight >= 2)
  val nativeWords = nativeDataWidth / 32
  val beatBytes = GowinDdrNative.beatBytes(nativeDataWidth)
  // The Gowin native address is a physical DDR word address, not a byte
  // address. A 1:4 BL8 transfer always consumes eight of those addresses:
  // x16 Console => 2 bytes/address, x32 Pro Dock => 4 bytes/address.
  val nativeWordBytes = nativeDataWidth / 64
  val nativeAddrShift = log2Up(nativeWordBytes)
  val beatOffsetBits = log2Up(beatBytes)
  val wordIdxBits = log2Up(nativeWords) max 1
  val lastIdx = nativeWords - 1
  require(nativeWordBytes >= 1 && (nativeWordBytes & (nativeWordBytes - 1)) == 0)
  private val idWidth = (if (fbConfig.useId) fbConfig.idWidth else 0) max
    (if (texConfig.useId) texConfig.idWidth else 0) max 1

  val io = new Bundle {
    val fb = slave(Axi4(fbConfig))
    val tex = slave(Axi4(texConfig))
    val ddr = master(GowinDdrNative(addrWidth = nativeAddrWidth, dataWidth = nativeDataWidth))
  }

  private def toNative(a: UInt): UInt = {
    val n = UInt(nativeAddrWidth bits)
    n := (a >> nativeAddrShift).resized
    // BL8 commands start on an eight-address boundary. AXI's low address
    // bits select a 32-bit lane (byte mask / read word index) instead.
    n(2 downto 0) := 0
    n
  }
  private def wordIdx(a: UInt): UInt = a(beatOffsetBits - 1 downto 2).resize(wordIdxBits)
  // Start of the next native line. Gowin built a 32-bit increment as a ~12 ns
  // LUT ripple chain at 100 MHz, so the carry into the upper bits is
  // registered one cycle ahead; callers only use it a cycle after the address
  // was loaded.
  private val splitBit = 14
  private def nextLine(a: UInt): UInt = {
    val hiPlusOne = RegNext(a(31 downto splitBit) + 1)
    val loWraps = RegNext(a(splitBit - 1 downto beatOffsetBits).andR)
    val n = UInt(32 bits)
    n(beatOffsetBits - 1 downto 0) := 0
    n(splitBit - 1 downto beatOffsetBits) := a(splitBit - 1 downto beatOffsetBits) + 1
    n(31 downto splitBit) := Mux(loWraps, hiPlusOne, a(31 downto splitBit))
    n
  }

  // Minimum spacing between native commands. Sustained back-to-back DDR3
  // traffic breaks the Pro Dock (hw/gowin/ddr3_test/README.md: gap <= 4 UI
  // cycles corrupts bursts or kills JTAG, gap >= 8 was clean over 1 GiB).
  // The one-outstanding bridge never got close; with reads pipelined, gap 0
  // reloaded the FPGA 8 s into GLQuake (B41), so keep 8.
  val cmdGapCycles = 8
  val cmdGapOk = Bool()
  if (cmdGapCycles == 0) cmdGapOk := True
  else {
    val cmdGap = Reg(UInt(log2Up(cmdGapCycles + 1) bits)) init (0)
    cmdGapOk := cmdGap === 0
    when(!cmdGapOk) { cmdGap := cmdGap - 1 }
    when(io.ddr.cmdEn) { cmdGap := cmdGapCycles }
  }

  // Default AXI outputs.
  for (axi <- Seq(io.fb, io.tex)) {
    axi.aw.ready := False
    axi.w.ready := False
    axi.b.valid := False
    axi.b.resp := 0
    axi.ar.ready := False
    axi.r.valid := False
    axi.r.data := 0
    axi.r.resp := 0
    axi.r.last := False
  }

  // ---------------------------------------------------------------- reads
  case class ReadMeta() extends Bundle {
    val useTex = Bool()
    val id = UInt(idWidth bits)
    val firstIdx = UInt(wordIdxBits bits)
    val lastWordIdx = UInt(wordIdxBits bits)
    val last = Bool()
  }

  val rd = new Area {
    val active = RegInit(False)
    val useTex = Reg(Bool()) init (False)
    val addr = Reg(UInt(32 bits)) init (0)
    val beatsLeft = Reg(UInt(9 bits)) init (0)
    val id = Reg(UInt(idWidth bits)) init (0)
    val addrNext = nextLine(addr)

    // Issued commands whose data has not been unpacked yet.
    val inFlight = Reg(UInt(log2Up(readsInFlight + 1) bits)) init (0)
    val meta = StreamFifo(ReadMeta(), readsInFlight)
    val data = StreamFifo(Bits(nativeDataWidth bits), readsInFlight)
    // As registers this FIFO's write enable fans out to 2048 bits.
    data.addTag(GowinKeepBsram)

    val firstIdx = wordIdx(addr)
    val wordsHere = (U(nativeWords, 5 bits) - firstIdx.resize(5)).resize(9)
    val lastLine = beatsLeft <= wordsHere
    val lastWordIdx = UInt(wordIdxBits bits)
    lastWordIdx := Mux(lastLine, (firstIdx.resize(9) + beatsLeft - 1).resize(wordIdxBits), U(lastIdx, wordIdxBits bits))

    // nextLine() needs a cycle after every address change.
    val addrSettled = RegInit(False)
    addrSettled := True
    val wantsCmd = active && addrSettled && inFlight =/= readsInFlight && meta.io.push.ready
    val issue = Bool() // driven by the command arbiter below

    meta.io.push.valid := issue
    meta.io.push.useTex := useTex
    meta.io.push.id := id
    meta.io.push.firstIdx := firstIdx
    meta.io.push.lastWordIdx := lastWordIdx
    meta.io.push.last := lastLine

    when(!active) {
      when(io.ddr.initCalibComplete) {
        when(io.fb.ar.valid) {
          io.fb.ar.ready := True
          addrSettled := False
          active := True
          useTex := False
          addr := io.fb.ar.addr.resized
          beatsLeft := (io.fb.ar.len.resize(9) + 1).resized
          if (fbConfig.useId) id := io.fb.ar.id.resized
        } elsewhen (io.tex.ar.valid) {
          io.tex.ar.ready := True
          addrSettled := False
          active := True
          useTex := True
          addr := io.tex.ar.addr.resized
          beatsLeft := (io.tex.ar.len.resize(9) + 1).resized
          if (texConfig.useId) id := io.tex.ar.id.resized
        }
      }
    } elsewhen (issue) {
      addrSettled := False
      addr := addrNext
      beatsLeft := beatsLeft - wordsHere
      when(lastLine) { active := False }
    }

    // The controller cannot be stalled: a credit was taken for every read.
    data.io.push.valid := io.ddr.rdDataValid
    data.io.push.payload := io.ddr.rdData

    // Unpack the head line into its port's R channel.
    val started = RegInit(False)
    val idxReg = Reg(UInt(wordIdxBits bits)) init (0)
    val head = meta.io.pop.payload
    val idx = Mux(started, idxReg, head.firstIdx)
    val words = data.io.pop.payload.subdivideIn(32 bits)
    val headValid = meta.io.pop.valid && data.io.pop.valid
    val endOfLine = idx === head.lastWordIdx
    val rReady = Mux(head.useTex, io.tex.r.ready, io.fb.r.ready)
    when(headValid) {
      when(head.useTex) {
        io.tex.r.valid := True
        io.tex.r.data := words(idx)
        io.tex.r.last := head.last && endOfLine
      } otherwise {
        io.fb.r.valid := True
        io.fb.r.data := words(idx)
        io.fb.r.last := head.last && endOfLine
      }
    }
    val lineDone = headValid && rReady && endOfLine
    meta.io.pop.ready := lineDone
    data.io.pop.ready := lineDone
    when(headValid && rReady) {
      started := !endOfLine
      idxReg := idx + 1
    }

    when(issue && !lineDone) { inFlight := inFlight + 1 }
      .elsewhen(!issue && lineDone) { inFlight := inFlight - 1 }
  }
  if (fbConfig.useId) io.fb.r.id := rd.head.id.resized
  if (texConfig.useId) io.tex.r.id := rd.head.id.resized

  // ---------------------------------------------------------------- writes
  val wr = new Area {
    object State extends SpinalEnum {
      val idle, collect, issue, resp = newElement()
    }
    val state = RegInit(State.idle)
    val useTex = Reg(Bool()) init (False)
    val addr = Reg(UInt(32 bits)) init (0)
    val addrNext = nextLine(addr)
    val beatsLeft = Reg(UInt(9 bits)) init (0)
    val id = Reg(UInt(idWidth bits)) init (0)
    val groupIdx = Reg(UInt(wordIdxBits bits)) init (0)
    val words = Vec(Reg(Bits(32 bits)) init (0), nativeWords)
    val strbs = Vec(Reg(Bits(4 bits)) init (0), nativeWords)
    // Do not spend a native DDR command on a line made entirely of padding.
    val groupHasData = Cat(strbs).orR
    val wantsCmd = state === State.issue && groupHasData && io.ddr.wrDataRdy
    val issue = Bool() // driven by the command arbiter below

    switch(state) {
      is(State.idle) {
        when(io.ddr.initCalibComplete) {
          when(io.fb.aw.valid) {
            io.fb.aw.ready := True
            useTex := False
            addr := io.fb.aw.addr.resized
            beatsLeft := (io.fb.aw.len.resize(9) + 1).resized
            if (fbConfig.useId) id := io.fb.aw.id.resized
            groupIdx := wordIdx(io.fb.aw.addr.resize(32))
            strbs.foreach(_ := 0)
            state := State.collect
          } elsewhen (io.tex.aw.valid) {
            io.tex.aw.ready := True
            useTex := True
            addr := io.tex.aw.addr.resized
            beatsLeft := (io.tex.aw.len.resize(9) + 1).resized
            if (texConfig.useId) id := io.tex.aw.id.resized
            groupIdx := wordIdx(io.tex.aw.addr.resize(32))
            strbs.foreach(_ := 0)
            state := State.collect
          }
        }
      }
      is(State.collect) {
        val wValid = Mux(useTex, io.tex.w.valid, io.fb.w.valid)
        val wData = Mux(useTex, io.tex.w.data, io.fb.w.data)
        val wStrb = Mux(useTex, io.tex.w.strb, io.fb.w.strb)
        val wLast = Mux(useTex, io.tex.w.last, io.fb.w.last)
        when(useTex) { io.tex.w.ready := True }
          .otherwise { io.fb.w.ready := True }
        when(wValid) {
          words(groupIdx) := wData
          // BmbAligner pads a short access to its aligned AXI burst with
          // WSTRB=0 beats; the data bus is repeated during padding and must
          // not be written to memory.
          strbs(groupIdx) := wStrb
          beatsLeft := beatsLeft - 1
          when(groupIdx === lastIdx || wLast || beatsLeft === 1) {
            groupIdx := 0
            state := State.issue
          } otherwise {
            groupIdx := groupIdx + 1
          }
        }
      }
      is(State.issue) {
        when(!groupHasData || issue) {
          addr := addrNext
          when(beatsLeft === 0) {
            state := State.resp
          } otherwise {
            strbs.foreach(_ := 0)
            state := State.collect
          }
        }
      }
      is(State.resp) {
        when(useTex) { io.tex.b.valid := True }
          .otherwise { io.fb.b.valid := True }
        when(Mux(useTex, io.tex.b.ready, io.fb.b.ready)) {
          state := State.idle
        }
      }
    }
  }
  if (fbConfig.useId) io.fb.b.id := wr.id.resized
  if (texConfig.useId) io.tex.b.id := wr.id.resized

  // ------------------------------------------------------ command arbiter
  val preferWrite = RegInit(False)
  val canIssue = io.ddr.cmdReady && cmdGapOk
  val grantWrite = canIssue && wr.wantsCmd && (preferWrite || !rd.wantsCmd)
  val grantRead = canIssue && rd.wantsCmd && !grantWrite
  wr.issue := grantWrite
  rd.issue := grantRead
  when(grantWrite) { preferWrite := False }
  when(grantRead) { preferWrite := True }

  io.ddr.cmdEn := grantWrite || grantRead
  io.ddr.cmd := Mux(grantWrite, U(GowinDdrNative.cmdWrite, 3 bits), U(GowinDdrNative.cmdRead, 3 bits))
  io.ddr.addr := Mux(grantWrite, toNative(wr.addr), toNative(rd.addr))
  io.ddr.burst := False
  // Cat(Iterable) places the first element at the least-significant end:
  // write lane i is symmetric with read word i.
  io.ddr.wrData := Cat(wr.words)
  io.ddr.wrDataEn := grantWrite
  io.ddr.wrDataEnd := True
  io.ddr.wrDataMask := ~Cat(wr.strbs)
}
