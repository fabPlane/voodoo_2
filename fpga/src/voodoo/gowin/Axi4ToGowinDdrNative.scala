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
    // 16: with ~65 DDR cycles per read, 8 in flight capped reads near
    // 390 MB/s, which GLQuake's fill and texture bursts reached (B110).
    // 32: shared by the texture, colour-fill, depth-fill and scanout ports,
    // and the fill readers' span windows grew to 32 (B125).
    readsInFlight: Int = 32,
    // Optional third, read-only port (the HDMI scanout on the Pro Dock):
    // its data then drains through its own AXI R channel / clock crossing
    // instead of queueing in the framebuffer port's in front of render fills.
    scanConfig: Axi4Config = null,
    // Optional fourth, read-only port (framebuffer depth fills on the Pro
    // Dock): its own R channel next to the fb port's (colour fills, LFB).
    auxConfig: Axi4Config = null,
    // Optional fifth, read-only port (colour fills + LFB reads on the Pro
    // Dock), so the fb port only carries render writes.
    colConfig: Axi4Config = null,
    // Optional sixth, write-only port (depth write-buffer drains on the Pro
    // Dock): its own W channel next to the fb port's (colour writes, LFB).
    auxWConfig: Axi4Config = null,
    // One-line stager per read port (lines for different ports unpack in
    // parallel); false = one shared unpacker, ~1.3k fewer flip-flops (five
    // 256-bit lines) for ~0.4% GLQuake speed (B130d).
    readStagers: Boolean = true
) extends Component {
  require(fbConfig.dataWidth == 32 && texConfig.dataWidth == 32)
  require(scanConfig == null || scanConfig.dataWidth == 32)
  require(auxConfig == null || auxConfig.dataWidth == 32)
  require(colConfig == null || colConfig.dataWidth == 32)
  require(auxWConfig == null || auxWConfig.dataWidth == 32)
  val hasScan = scanConfig != null
  val hasAux = auxConfig != null
  val hasCol = colConfig != null
  val hasAuxW = auxWConfig != null
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
    (if (hasScan && scanConfig.useId) scanConfig.idWidth else 0) max
    (if (hasAux && auxConfig.useId) auxConfig.idWidth else 0) max
    (if (hasCol && colConfig.useId) colConfig.idWidth else 0) max
    (if (hasAuxW && auxWConfig.useId) auxWConfig.idWidth else 0) max 1

  val io = new Bundle {
    val fb = slave(Axi4(fbConfig))
    val tex = slave(Axi4(texConfig))
    val scan = if (hasScan) slave(Axi4(scanConfig)) else null
    val aux = if (hasAux) slave(Axi4(auxConfig)) else null
    val col = if (hasCol) slave(Axi4(colConfig)) else null
    val auxW = if (hasAuxW) slave(Axi4(auxWConfig)) else null
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
    // waited, 5 cycles the head returned line waited for the aux port's R,
    // 6 sum of reads in flight per cycle (/ read cmds = mean read latency),
    // 7 cycles the head returned line waited for the col port's R.
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
    Mux(io.maxReads === 0 || io.maxReads.resize(log2Up(readsInFlight + 1) max 5 bits) > readsInFlight,
      U(readsInFlight), io.maxReads.resized)
  ).resize(log2Up(readsInFlight + 1) bits)

  // Default AXI outputs.
  for (axi <- Seq(io.fb, io.tex) ++ Option(io.scan) ++ Option(io.aux) ++ Option(io.col) ++ Option(io.auxW)) {
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
  // Read port: 0 fb, 1 tex, 2 scan, 3 aux, 4 col.
  case class ReadMeta() extends Bundle {
    val port = UInt(3 bits)
    val id = UInt(idWidth bits)
    val firstIdx = UInt(wordIdxBits bits)
    val lastWordIdx = UInt(wordIdxBits bits)
    val last = Bool()
  }

  val rd = new Area {
    val active = RegInit(False)
    val port = Reg(UInt(3 bits)) init (0)
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
    // Registered like slotFree: the inFlight compare fed the grant and the
    // write path's enables combinationally (ddr_clk misses, B93).
    val slotFreeUrgent = RegInit(True)
    // meta holds exactly the inFlight commands (pushed on issue, popped on
    // lineDone), so the registered slot flags already imply room in it; its
    // pointer compare on this path missed 100 MHz into the controller's
    // write-queue enables (B134).
    val wantsCmd = active && addrSettled && (slotFree || urgentSync && slotFreeUrgent)
    val issue = Bool() // driven by the command arbiter below

    // Pushed the cycle after issue: as registers, meta's write enables fan
    // out to every entry, and grant -> them missed 100 MHz (B137). The line
    // comes back >= ~15 cycles later, so the entry is always there first.
    val metaIn = ReadMeta()
    metaIn.port := port
    metaIn.id := id
    metaIn.firstIdx := firstIdx
    metaIn.lastWordIdx := lastWordIdx
    metaIn.last := lastLine
    meta.io.push.valid := RegNext(issue) init (False)
    meta.io.push.payload := RegNext(metaIn)

    // Ports take turns (round robin) when several want a read. With fb
    // strictly first, a texture miss waited behind the scanout's line
    // fetches and the render fills (~170 core cycles per miss, B71). While
    // the display is short of pixels its port goes first (scan when there
    // is one, else fb); policy bit 1 restores fb before tex.
    val policySync = RegNext(io.policy) init (0)
    case class ReadPort(idx: Int, ar: Stream[Axi4Ar], cfg: Axi4Config)
    val readPorts = Seq(ReadPort(0, io.fb.ar, fbConfig), ReadPort(1, io.tex.ar, texConfig)) ++
      (if (hasScan) Seq(ReadPort(2, io.scan.ar, scanConfig)) else Nil) ++
      (if (hasAux) Seq(ReadPort(3, io.aux.ar, auxConfig)) else Nil) ++
      (if (hasCol) Seq(ReadPort(4, io.col.ar, colConfig)) else Nil)
    val portValid = Vec(Bool(), 8)
    for (i <- 0 until 8) portValid(i) := readPorts.find(_.idx == i).map(_.ar.valid).getOrElse(False)
    val lastGrant = Reg(UInt(3 bits)) init (0)
    val rrGrant = UInt(3 bits)
    rrGrant := lastGrant
    for (k <- 8 to 1 by -1) when(portValid(lastGrant + U(k % 8, 3 bits))) { rrGrant := lastGrant + U(k % 8, 3 bits) }
    val urgentPort = if (hasScan) U(2, 3 bits) else U(0, 3 bits)
    val grant = UInt(3 bits)
    grant := rrGrant
    when(urgentSync && portValid(urgentPort)) { grant := urgentPort }
      .elsewhen(policySync(1) && portValid(0) && rrGrant === 1) { grant := 0 }
    val anyValid = portValid.orR
    // The grant is registered and taken the cycle after: the round-robin
    // chain fed the 32-bit address / length / ID muxes into the read
    // registers in one cycle (ddr_clk_out misses, B109). An AXI request
    // stays valid until accepted, so the registered winner is still there.
    val grantReg = RegNext(grant) init (0)
    val grantValid = RegNext(!active && io.ddr.initCalibComplete && anyValid) init (False)
    when(!active) {
      when(grantValid) {
        lastGrant := grantReg
        addrSettled := False
        active := True
        port := grantReg
        switch(grantReg) {
          for (rp <- readPorts) is(rp.idx) {
            rp.ar.ready := True
            addr := rp.ar.addr.resized
            beatsLeft := (rp.ar.len.resize(9) + 1).resized
            if (rp.cfg.useId) id := rp.ar.id.resized
          }
          default {}
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

    // readStagers: each read port has a one-line stager that unpacks into its
    // R channel; the head line moves to its port's stager when that is empty. With one
    // shared unpacker (8 DDR cycles per line, one port at a time) returned
    // lines waited ~34 DDR cycles in `data` behind other ports' lines,
    // against ~15 cycles of controller latency (B128 + own DDR3).
    val head = meta.io.pop.payload
    val headValid = meta.io.pop.valid && data.io.pop.valid
    val ports = Seq(io.fb, io.tex) ++ Option(io.scan) ++ Option(io.aux) ++ Option(io.col)
    val portIdx = Seq(0, 1) ++ (if (hasScan) Seq(2) else Nil) ++ (if (hasAux) Seq(3) else Nil) ++
      (if (hasCol) Seq(4) else Nil)
    val lineDone = Bool()
    val rReady = Bool()
    rReady := False
    meta.io.pop.ready := lineDone
    data.io.pop.ready := lineDone
    val stagedId: Int => UInt = if (readStagers) {
      class Stager(val axi: Axi4, val p: Int) extends Area {
        val valid = RegInit(False)
        val line = Reg(Bits(nativeDataWidth bits))
        val id = Reg(UInt(idWidth bits)) init (0)
        val idx = Reg(UInt(wordIdxBits bits)) init (0)
        val lastWordIdx = Reg(UInt(wordIdxBits bits)) init (0)
        val last = Reg(Bool()) init (False)
        val endOfLine = idx === lastWordIdx
        when(valid) {
          axi.r.valid := True
          axi.r.data := line.subdivideIn(32 bits)(idx)
          axi.r.last := last && endOfLine
        }
        when(axi.r.fire) {
          idx := idx + 1
          when(endOfLine) { valid := False }
        }
      }
      val stagers = ports.zip(portIdx).map { case (a, p) => new Stager(a, p) }
      // rReady: the head's stager is empty (registered state only).
      for (st <- stagers) when(head.port === st.p) { rReady := !st.valid }
      lineDone := headValid && rReady
      for (st <- stagers) when(lineDone && head.port === st.p) {
        st.valid := True
        st.line := data.io.pop.payload
        st.id := head.id
        st.idx := head.firstIdx
        st.lastWordIdx := head.lastWordIdx
        st.last := head.last
      }
      p => stagers.find(_.p == p).get.id
    } else {
      // Unpack the head line straight into its port's R channel.
      val started = RegInit(False)
      val idxReg = Reg(UInt(wordIdxBits bits)) init (0)
      val idx = Mux(started, idxReg, head.firstIdx)
      val words = data.io.pop.payload.subdivideIn(32 bits)
      val endOfLine = idx === head.lastWordIdx
      for ((axi, p) <- ports.zip(portIdx)) {
        when(head.port === p) { rReady := axi.r.ready }
        when(headValid && head.port === p) {
          axi.r.valid := True
          axi.r.data := words(idx)
          axi.r.last := head.last && endOfLine
        }
      }
      lineDone := headValid && rReady && endOfLine
      when(headValid && rReady) {
        started := !endOfLine
        idxReg := idx + 1
      }
      _ => head.id
    }

    val inFlightNext = UInt(inFlight.getWidth bits)
    inFlightNext := inFlight
    when(issue && !lineDone) { inFlightNext := inFlight + 1 }
      .elsewhen(!issue && lineDone) { inFlightNext := inFlight - 1 }
    inFlight := inFlightNext
    slotFree := inFlightNext < readLimit
    slotFreeUrgent := inFlightNext =/= readsInFlight
  }
  if (fbConfig.useId) io.fb.r.id := rd.stagedId(0).resized
  if (texConfig.useId) io.tex.r.id := rd.stagedId(1).resized
  if (hasScan && scanConfig.useId) io.scan.r.id := rd.stagedId(2).resized
  if (hasAux && auxConfig.useId) io.aux.r.id := rd.stagedId(3).resized
  if (hasAux && auxConfig.useId) io.aux.b.id := 0
  if (hasCol && colConfig.useId) io.col.r.id := rd.stagedId(4).resized
  if (hasCol && colConfig.useId) io.col.b.id := 0
  if (hasScan) {
    if (scanConfig.useId) io.scan.b.id := 0
  }

  // ---------------------------------------------------------------- writes
  case class WritePort(idx: Int, axi: Axi4, cfg: Axi4Config)
  // Two stages, so collecting a line's beats overlaps issuing the previous
  // line: with one serial engine (address, 8 beats, command, B, idle) a 32-byte
  // line took 12+ DDR cycles and the colour and depth write ports, which share
  // it, were blocked 6-7% of cycles under GLQuake (B126). The collector takes
  // a burst's beats into a line; a full line moves to the issue buffer (if it
  // is free), which waits for the command arbiter and, after a burst's last
  // line, returns B.
  val wr = new Area {
    // Write ports: 0 fb (colour writes, LFB), 1 tex (texture downloads),
    // 2 auxW (depth writes) when present.
    val writePorts = Seq(WritePort(0, io.fb, fbConfig), WritePort(1, io.tex, texConfig)) ++
      (if (hasAuxW) Seq(WritePort(2, io.auxW, auxWConfig)) else Nil)

    // ---- collector
    val collecting = RegInit(False)
    val port = Reg(UInt(2 bits)) init (0)
    // fb and auxW take turns; texture downloads go when neither waits.
    val auxWNext = RegInit(False)
    val cAddr = Reg(UInt(32 bits)) init (0)
    val cAddrNext = nextLine(cAddr)
    val beatsLeft = Reg(UInt(9 bits)) init (0)
    val cId = Reg(UInt(idWidth bits)) init (0)
    val groupIdx = Reg(UInt(wordIdxBits bits)) init (0)
    val cWords = Vec(Reg(Bits(32 bits)) init (0), nativeWords)
    val cStrbs = Vec(Reg(Bits(4 bits)) init (0), nativeWords)
    // Do not spend a native DDR command on a line made entirely of padding.
    // Registered while the line is collected: OR-ing the 32 strobe bits fed
    // the command arbiter combinationally and missed 100 MHz (B55).
    val cHasData = RegInit(False)

    // ---- issue buffer
    val iValid = RegInit(False)
    val iResp = RegInit(False) // command done, B being returned
    val iPort = Reg(UInt(2 bits)) init (0)
    val iLast = Reg(Bool()) init (False)
    val addr = Reg(UInt(32 bits)) init (0)
    val id = Reg(UInt(idWidth bits)) init (0)
    val words = Vec(Reg(Bits(32 bits)) init (0), nativeWords)
    val strbs = Vec(Reg(Bits(4 bits)) init (0), nativeWords)
    val iHasData = RegInit(False)
    val wantsCmd = iValid && !iResp && iHasData && io.ddr.wrDataRdy
    val issue = Bool() // driven by the command arbiter below

    def start(wp: WritePort): Unit = {
      wp.axi.aw.ready := True
      port := wp.idx
      cAddr := wp.axi.aw.addr.resized
      beatsLeft := (wp.axi.aw.len.resize(9) + 1).resized
      if (wp.cfg.useId) cId := wp.axi.aw.id.resized
      groupIdx := wordIdx(wp.axi.aw.addr.resize(32))
      cStrbs.foreach(_ := 0)
      cHasData := False
      collecting := True
    }
    val wValid = Bool()
    val wData = Bits(32 bits)
    val wStrb = Bits(4 bits)
    val wLast = Bool()
    val bReady = Bool()
    wValid := False; wData := 0; wStrb := 0; wLast := False; bReady := False
    for (wp <- writePorts) when(port === wp.idx) {
      wValid := wp.axi.w.valid
      wData := wp.axi.w.data
      wStrb := wp.axi.w.strb
      wLast := wp.axi.w.last
    }
    for (wp <- writePorts) when(iPort === wp.idx) { bReady := wp.axi.b.ready }

    // Issue buffer: free once its line is issued (or was all padding) and,
    // for a burst's last line, its B has been taken.
    val iDone = Bool()
    iDone := False
    when(iValid && !iResp && (!iHasData || issue)) {
      when(iLast) { iResp := True } otherwise { iDone := True }
    }
    when(iResp) {
      for (wp <- writePorts) when(iPort === wp.idx) { wp.axi.b.valid := True }
      when(bReady) { iDone := True }
    }

    when(!collecting) {
      when(io.ddr.initCalibComplete) {
        if (hasAuxW) {
          when(io.auxW.aw.valid && (auxWNext || !io.fb.aw.valid)) {
            start(writePorts(2))
            auxWNext := False
          } elsewhen (io.fb.aw.valid) {
            start(writePorts(0))
            auxWNext := True
          } elsewhen (io.tex.aw.valid) {
            start(writePorts(1))
          }
        } else {
          when(io.fb.aw.valid) {
            start(writePorts(0))
          } elsewhen (io.tex.aw.valid) {
            start(writePorts(1))
          }
        }
      }
    }

    // A beat that completes the line is only taken when the issue buffer is
    // empty (registered: deciding on this cycle's grant put cmd_ready ->
    // grant -> collector enables in one path, B128 missed 100 MHz), and not
    // in the cycle right after the burst's address was loaded: nextLine() is
    // only valid a cycle later.
    val justStarted = RegNext(!collecting && io.ddr.initCalibComplete &&
      (io.fb.aw.valid || io.tex.aw.valid || (if (hasAuxW) io.auxW.aw.valid else False))) init (False)
    val completes = groupIdx === lastIdx || wLast || beatsLeft === 1
    val wReady = collecting && (!completes || (!iValid && !justStarted))
    for (wp <- writePorts) when(port === wp.idx) { wp.axi.w.ready := wReady }
    val beat = wValid && wReady
    when(iDone) { iValid := False; iResp := False }
    when(beat) {
      cWords(groupIdx) := wData
      // BmbAligner pads a short access to its aligned AXI burst with
      // WSTRB=0 beats; the data bus is repeated during padding and must
      // not be written to memory.
      cStrbs(groupIdx) := wStrb
      when(wStrb.orR) { cHasData := True }
      beatsLeft := beatsLeft - 1
      groupIdx := groupIdx + 1
      when(completes) {
        // Hand the line (with this beat) to the issue buffer.
        iValid := True
        iResp := False
        iPort := port
        id := cId
        addr := cAddr
        for (k <- 0 until nativeWords) {
          val here = groupIdx === k
          words(k) := Mux(here, wData, cWords(k))
          strbs(k) := Mux(here, wStrb, cStrbs(k))
        }
        iHasData := cHasData || wStrb.orR
        val last = beatsLeft === 1
        iLast := last
        groupIdx := 0
        cStrbs.foreach(_ := 0)
        cHasData := False
        cAddr := cAddrNext
        when(last) { collecting := False }
      }
    }
  }
  if (fbConfig.useId) io.fb.b.id := wr.id.resized
  if (texConfig.useId) io.tex.b.id := wr.id.resized
  if (hasAuxW && auxWConfig.useId) io.auxW.b.id := wr.id.resized
  if (hasAuxW && auxWConfig.useId) io.auxW.r.id := 0 // write-only port

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
  io.stats(5) := cnt(rd.headValid && !rd.rReady && rd.head.port === 3)
  io.stats(6) := counter(rd.inFlight)
  io.stats(7) := cnt(rd.headValid && !rd.rReady && rd.head.port === 4)

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
