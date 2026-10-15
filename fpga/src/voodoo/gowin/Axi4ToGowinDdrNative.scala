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
  * Reads and writes share the command port, alternating when both wait
  * (io.policy bit 0: reads first, a write waits at most 32 cycles).
  *
  * Ordering: the controller executes commands in order, and each port's
  * BmbToAxi4SharedBridge waits for outstanding responses before switching
  * between reads and writes, so a read never overtakes an earlier write it
  * depends on, whatever the arbitration policy. fb and tex alternate for
  * new read bursts (fb first while the scanout is urgent, or always with
  * io.policy bit 1).
  *
  * wr_data_mask is active-high inhibit, the inverse of AXI wstrb.
  * nativeDataWidth is 256 on the 32-bit Pro Dock and 128 on the Console.
  */
case class Axi4ToGowinDdrNative(
    fbConfig: Axi4Config,
    texConfig: Axi4Config,
    nativeDataWidth: Int = GowinAddressMap.ddrNativeDataWidth,
    nativeAddrWidth: Int = GowinAddressMap.ddrNativeAddrWidth,
    readsInFlight: Int = 8,
    // Optional third, read-only port (the HDMI scanout on the Pro Dock):
    // its data then drains through its own AXI R channel / clock crossing
    // instead of queueing in the framebuffer port's in front of render fills.
    scanConfig: Axi4Config = null
) extends Component {
  require(fbConfig.dataWidth == 32 && texConfig.dataWidth == 32)
  require(scanConfig == null || scanConfig.dataWidth == 32)
  val hasScan = scanConfig != null
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
    (if (texConfig.useId) texConfig.idWidth else 0) max
    (if (hasScan && scanConfig.useId) scanConfig.idWidth else 0) max 1

  val io = new Bundle {
    val fb = slave(Axi4(fbConfig))
    val tex = slave(Axi4(texConfig))
    val scan = if (hasScan) slave(Axi4(scanConfig)) else null
    val ddr = master(GowinDdrNative(addrWidth = nativeAddrWidth, dataWidth = nativeDataWidth))
    // Run-time throttles (quasi-static): idle cycles after every native
    // command, and the most reads in flight (0 = readsInFlight).
    val cmdGap = in UInt (8 bits)
    val maxReads = in UInt (4 bits)
    // The display is running short of pixels: skip the command gap so its
    // reads are not starved by the throttle (the scanout's own rate was
    // always there in the stable builds; only rendering needs pacing).
    val urgent = in Bool ()
    // Run-time arbitration policy (quasi-static): bit 0 reads before writes
    // (a write still goes after waiting 32 cycles), bit 1 fb reads strictly
    // before tex reads (the pre-B72 order).
    val policy = in Bits (4 bits)
    // Free-running DDR-clock counters: 0 cycles, 1 read cmds, 2 write cmds,
    // 3 sum of returned read lines waiting for the AXI R side per cycle
    // (/ read cmds = mean wait after the data came back), 4 cycles fb AR
    // waited, 5 cycles the head returned line waited for the scan port's R,
    // 6 sum of reads in flight per cycle (/ read cmds = mean read latency),
    // 7 cycles the head returned line waited for the fb port's R.
    val stats = out(Vec(UInt(32 bits), 8))
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
  val urgentSync = RegNext(io.urgent, init = False)
  // The gap is io.cmdGap (8 by default on the board: see TangMega138kPro).
  // "Gap elapsed" is registered from the counter's next value: cmdGap === 0
  // fed the command grant combinationally and missed 100 MHz (B60).
  val cmdGap = Reg(UInt(8 bits)) init (0)
  val gapNext = UInt(8 bits)
  gapNext := Mux(cmdGap === 0, cmdGap, cmdGap - 1)
  when(io.ddr.cmdEn) { gapNext := io.cmdGap }
  cmdGap := gapNext
  val gapZero = RegNext(gapNext === 0, init = True)
  val cmdGapOk = gapZero || urgentSync
  val readLimit = RegNext(
    Mux(io.maxReads === 0 || io.maxReads > readsInFlight, U(readsInFlight), io.maxReads.resized)
  ).resize(log2Up(readsInFlight + 1) bits)

  // Default AXI outputs.
  for (axi <- Seq(io.fb, io.tex) ++ Option(io.scan)) {
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
  // Read port: 0 fb, 1 tex, 2 scan.
  case class ReadMeta() extends Bundle {
    val port = UInt(2 bits)
    val id = UInt(idWidth bits)
    val firstIdx = UInt(wordIdxBits bits)
    val lastWordIdx = UInt(wordIdxBits bits)
    val last = Bool()
  }

  val rd = new Area {
    val active = RegInit(False)
    val port = Reg(UInt(2 bits)) init (0)
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
    // Registered "a read slot is free" (inFlight after this cycle < limit):
    // the compare fed the command arbiter combinationally and missed 100 MHz.
    val slotFree = RegInit(True)
    val wantsCmd = active && addrSettled && (slotFree || urgentSync && inFlight =/= readsInFlight) && meta.io.push.ready
    val issue = Bool() // driven by the command arbiter below

    meta.io.push.valid := issue
    meta.io.push.port := port
    meta.io.push.id := id
    meta.io.push.firstIdx := firstIdx
    meta.io.push.lastWordIdx := lastWordIdx
    meta.io.push.last := lastLine

    // Ports take turns when several want a read. With fb strictly first, a
    // texture miss waited behind the scanout's line fetches and the render
    // fills (~170 core cycles per miss under GLQuake, B71) while the whole
    // pixel pipeline stalled on it. While the display is short of pixels its
    // port goes first (the scan port when there is one, else fb).
    val lastWasTex = RegInit(False)
    val policySync = RegNext(io.policy) init (0)
    val scanValid = if (hasScan) io.scan.ar.valid else False
    val takeTex = io.tex.ar.valid && (!io.fb.ar.valid || (!lastWasTex && !urgentSync && !policySync(1)))
    val lastWasScan = RegInit(False)
    // scan: first when urgent, otherwise every other grant while others wait.
    val takeScan = scanValid && (urgentSync || !lastWasScan || !(io.fb.ar.valid || io.tex.ar.valid))
    def accept(p: Int, ar: Stream[Axi4Ar], cfg: Axi4Config): Unit = {
      ar.ready := True
      addrSettled := False
      active := True
      port := p
      addr := ar.addr.resized
      beatsLeft := (ar.len.resize(9) + 1).resized
      if (cfg.useId) id := ar.id.resized
    }
    when(!active) {
      when(io.ddr.initCalibComplete) {
        if (hasScan) {
          when(takeScan) {
            lastWasScan := True
            accept(2, io.scan.ar, scanConfig)
          }
        }
        when(!takeScan) {
          when(io.fb.ar.valid && !takeTex) {
            lastWasTex := False
            lastWasScan := False
            accept(0, io.fb.ar, fbConfig)
          } elsewhen (io.tex.ar.valid) {
            lastWasTex := True
            lastWasScan := False
            accept(1, io.tex.ar, texConfig)
          }
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
    val ports = Seq(io.fb, io.tex) ++ Option(io.scan)
    val rReady = Bool()
    rReady := io.fb.r.ready
    for ((axi, p) <- ports.zipWithIndex) {
      when(head.port === p) { rReady := axi.r.ready }
      when(headValid && head.port === p) {
        axi.r.valid := True
        axi.r.data := words(idx)
        axi.r.last := head.last && endOfLine
      }
    }
    val lineDone = headValid && rReady && endOfLine
    meta.io.pop.ready := lineDone
    data.io.pop.ready := lineDone
    when(headValid && rReady) {
      started := !endOfLine
      idxReg := idx + 1
    }

    val inFlightNext = UInt(inFlight.getWidth bits)
    inFlightNext := inFlight
    when(issue && !lineDone) { inFlightNext := inFlight + 1 }
      .elsewhen(!issue && lineDone) { inFlightNext := inFlight - 1 }
    inFlight := inFlightNext
    slotFree := inFlightNext < readLimit
  }
  if (fbConfig.useId) io.fb.r.id := rd.head.id.resized
  if (texConfig.useId) io.tex.r.id := rd.head.id.resized
  if (hasScan && scanConfig.useId) io.scan.r.id := rd.head.id.resized
  if (hasScan) {
    if (scanConfig.useId) io.scan.b.id := 0
  }

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
    // Registered while the line is collected: OR-ing the 32 strobe bits fed
    // the command arbiter combinationally and missed 100 MHz (B55).
    val groupHasData = RegInit(False)
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
            groupHasData := False
            state := State.collect
          } elsewhen (io.tex.aw.valid) {
            io.tex.aw.ready := True
            useTex := True
            addr := io.tex.aw.addr.resized
            beatsLeft := (io.tex.aw.len.resize(9) + 1).resized
            if (texConfig.useId) id := io.tex.aw.id.resized
            groupIdx := wordIdx(io.tex.aw.addr.resize(32))
            strbs.foreach(_ := 0)
            groupHasData := False
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
          when(wStrb.orR) { groupHasData := True }
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
            groupHasData := False
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
  // While the display is short of pixels, reads go first.
  val readFirst = RegNext(io.policy(0)) init (False)
  val writeWait = Reg(UInt(6 bits)) init (0)
  val writeStarved = writeWait(5)
  val writeTurn = Mux(readFirst, writeStarved, preferWrite) && !urgentSync
  val grantWrite = canIssue && wr.wantsCmd && (writeTurn || !rd.wantsCmd)
  val grantRead = canIssue && rd.wantsCmd && !grantWrite
  wr.issue := grantWrite
  rd.issue := grantRead
  when(grantWrite) { preferWrite := False }
  when(grantRead) { preferWrite := True }
  when(grantWrite || !wr.wantsCmd) { writeWait := 0 }
    .elsewhen(!writeStarved) { writeWait := writeWait + 1 }

  private def counter(inc: UInt): UInt = {
    val r = Reg(UInt(32 bits)) init (0)
    r := r + inc.resized
    r
  }
  private def cnt(cond: Bool): UInt = counter(cond.asUInt)
  io.stats(0) := cnt(True)
  io.stats(1) := cnt(grantRead)
  io.stats(2) := cnt(grantWrite)
  io.stats(3) := counter(rd.data.io.occupancy)
  io.stats(4) := cnt(io.fb.ar.valid && !io.fb.ar.ready)
  io.stats(5) := cnt(rd.headValid && !rd.rReady && rd.head.port === 2)
  io.stats(6) := counter(rd.inFlight)
  io.stats(7) := cnt(rd.headValid && !rd.rReady && rd.head.port === 0)

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
