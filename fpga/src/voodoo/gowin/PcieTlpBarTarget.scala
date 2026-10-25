package voodoo.gowin

import spinal.core._
import spinal.core.sim._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._

object PcieTlpBarTarget {
  val tlpDataWidth = 256
  val tlpValidWidth = 8
  val barDecWidth = 6
  val windowAddress = 0x250
  val windowAddressWidth = 20
  val voodooAddressWidth = 24
  // Windows 16-30 reach raw DDR (bit 24 set): see TangMega138kPro / RawDdrWindow.
  val rawAddressWidth = 25
  val windowBits = 5
  // Window 31 is local registers (FrameDmaWriter), answered here without AXI.
  val localWindow = 31

  def axiConfig(addressWidth: Int = voodooAddressWidth) =
    AxiLite4Config(addressWidth = addressWidth, dataWidth = 32)
}

/** Gowin PCIe Controller TLP BAR0 target.
  *
  * Turns 32-bit Memory Read/Write TLPs on BAR0 into AXI4-Lite. Gowin BAR
  * windows top out at 1 MiB, so a host-visible window register at byte 0x250
  * selects which megabyte of the 16 MiB Voodoo map the TLP offset lands in.
  * Windows 16-31 set address bit 24; the top level sends those to raw DDR
  * (host state load / framebuffer readback for trace replay).
  * It decodes as a byte write (first BE 0001) at 0x250 in any window - the
  * Voodoo never takes 8-bit writes - or as any access to 0x250 while the
  * window is in register space (0-3). Offset 0x250 of the LFB / texture
  * windows is therefore ordinary memory.
  * Status at 0x254 is forwarded to the AXI-Lite bridge, same as DE10/Artix.
  *
  * TLP data is Gowin native: first header dword in bits 255:224.
  */
case class PcieTlpBarTarget(
    addressWidth: Int = PcieTlpBarTarget.voodooAddressWidth,
    timeoutLog2: Int = 20,
    // Posted writes: a write TLP is done once AXI takes its address and data;
    // its B is counted (bOut) instead of waited for, and a read waits for
    // bOut == 0 before it goes to AXI, so it still sees every earlier write.
    // Waiting for each B capped the host at ~one write per AXI round trip
    // (B165b: the rasterizer idled 33% of cycles with the PCI FIFO empty).
    postedWrites: Boolean = false
) extends Component {
  import PcieTlpBarTarget._

  val io = new Bundle {
    val tlRxSop = in Bool ()
    val tlRxEop = in Bool ()
    val tlRxValid = in Bits (tlpValidWidth bits)
    val tlRxData = in Bits (tlpDataWidth bits)
    val tlRxBardec = in Bits (barDecWidth bits)
    val tlRxWait = out Bool ()

    val tlTxSop = out Bool ()
    val tlTxEop = out Bool ()
    val tlTxValid = out Bits (tlpValidWidth bits)
    val tlTxData = out Bits (tlpDataWidth bits)
    val tlTxWait = in Bool ()
    val tlTxCreditsCpl = in Bits (32 bits)
    val tlCfgBusdev = in UInt (13 bits)

    val axi = master(AxiLite4(axiConfig(addressWidth)))
    val window = out UInt (windowBits bits)
    // Window 31 register port: write pulse, combinational read.
    val localWrite = out Bool ()
    val localAddr = out UInt (8 bits) // writes (with localWrite)
    // Reads: localRdata is sampled in the read state for this address, a
    // register (a mux with the header's address put TLP decode -> local
    // read mux -> readData on one path for timing, B151).
    val localRaddr = out UInt (8 bits)
    val localWdata = out Bits (32 bits)
    val localRdata = in Bits (32 bits)
  }

  def dword(data: Bits, index: Int): Bits =
    data(255 - index * 32 downto 224 - index * 32)

  object State extends SpinalEnum {
    val idle, axiWrite, waitB, axiRead, localRead, cpl, drop, burst = newElement()
  }

  val state = RegInit(State.idle)
  val window = Reg(UInt(windowBits bits)) init (0)
  // Decoded when the window is written (window -> compare -> tlpAddr /
  // writeData enables missed 75 MHz in the TLP's decode cycle, B156).
  val windowInRegisterSpace = RegInit(True) // window 0 at reset
  val windowIsLocal = RegInit(False)
  val reqId = Reg(Bits(16 bits)) init (0)
  val reqTag = Reg(Bits(8 bits)) init (0)
  val firstBe = Reg(Bits(4 bits)) init (0)
  val tlpAddr = Reg(UInt(addressWidth bits)) init (0)
  val writeData = Reg(Bits(32 bits)) init (0)
  val readData = Reg(Bits(32 bits)) init (0)
  val lowerAddr = Reg(Bits(7 bits)) init (0)
  // Multi-dword posted writes (write-combined host stores): the rest of the
  // payload after the first dword, one AXI write per dword at the next
  // address. The SOP beat is taken when the TLP starts, so its last four
  // dwords (4-7) are kept in sopRest; later beats are read in place, held by
  // tlRxWait until their last dword goes out. burstPos indexes the next
  // dword in the beat, burstLeft counts the dwords still to write.
  val sopRest = Reg(Vec(Bits(32 bits), 4))
  val burstInSop = RegInit(False) // next dword comes from sopRest
  val burstPos = Reg(UInt(3 bits)) init (0)
  val burstLeft = Reg(UInt(10 bits)) init (0)
  val burstLastBe = Reg(Bits(4 bits)) init (0)
  val sopEop = RegInit(False) // the SOP beat ended the TLP
  // Timeouts. Every read must complete inside the host root port's
  // completion timeout (50 us - 50 ms, not configurable on the AMD dock
  // slot), and PCIe ordering makes a read wait behind every posted write
  // before it. A stalled link there doesn't just fail the read: the AMD
  // platform shuts down on the spot (seen with a hung core under GLQuake).
  // So reads give up after ~2^(timeoutLog2-4) cycles (0.87 ms at 75 MHz) and
  // writes after ~2^(timeoutLog2-3) (1.75 ms), well above normal latencies
  // (LFB read ~61 us, status < 0.2 us idle). Either timeout enters wedged
  // mode, below. Gowin maps a wide increment to a LUT ripple chain, so count
  // ticks of a free-running prescaler instead.
  private val readTimeoutLog2 = timeoutLog2 - 4
  private val writeTimeoutLog2 = timeoutLog2 - 3
  private val prescaleBits = 8.min(readTimeoutLog2 - 1)
  val prescale = Reg(UInt(prescaleBits bits)) init (0)
  prescale := prescale + 1
  val prescaleTick = RegNext(prescale.andR) init (False)
  val stall = Reg(UInt((writeTimeoutLog2 - prescaleBits + 1) bits)) init (0)
  val readTimeout = stall(readTimeoutLog2 - prescaleBits)
  val writeTimeout = stall.msb

  // AXI-side state, independent of the TLP state machine. A request valid is
  // held until its handshake (dropping it would break AXI and leave a stale
  // response in flight). If the TLP side gives up on a transaction, its
  // response is counted here and discarded when it eventually arrives, so
  // it can't be mistaken for the next transaction's response.
  val awPend = RegInit(False)
  val wPend = RegInit(False)
  val arPend = RegInit(False)
  val dropB = Reg(UInt(7 bits)) init (0)
  // Posted writes whose B has not come back yet.
  val bOut = Reg(UInt(6 bits)) init (0)
  val bOutFull = bOut.andR
  val dropR = Reg(UInt(4 bits)) init (0)
  val axiIdle = !awPend && !wPend && !arPend

  // Wedged mode: after a timeout the core side is presumed stuck. TLPs keep
  // flowing - writes are dropped, reads answer all-ones at once - so the link
  // never stalls. It ends by itself once every abandoned AXI transaction has
  // completed (the core recovered). Window 31 offset 0x40 reports it:
  // [0] wedged now, [1] write timeout seen, [2] read timeout seen,
  // [31:16] TLPs dropped; a write there clears bits 1-2 and the count.
  val wedged = RegInit(False)
  wedged.simPublic(); bOut.simPublic()
  val writeTimeoutSeen = RegInit(False)
  val readTimeoutSeen = RegInit(False)
  val droppedTlps = Reg(UInt(16 bits)) init (0)
  val wedgeStatus = droppedTlps.asBits ## B(0, 13 bits) ## readTimeoutSeen ## writeTimeoutSeen ## wedged

  val rxBeat = io.tlRxValid.orR
  val bar0 = io.tlRxBardec(0)
  val header0 = dword(io.tlRxData, 0)
  val header1 = dword(io.tlRxData, 1)
  val header2 = dword(io.tlRxData, 2)
  val header3 = dword(io.tlRxData, 3)

  val fmt = header0(31 downto 29)
  val typ = header0(28 downto 24)
  val isMem = typ === 0
  val hasData = fmt(1)
  val fourDw = fmt(0)
  val capturedAddr = UInt(addressWidth bits)
  // TLP payload dwords are in PCIe byte order: the lowest-addressed byte
  // sits in bits 31:24. Swap to little-endian so a host store of V lands
  // as V (and first-BE bit i matches byte lane i). Found on hardware: every
  // read came back byte-reversed (SST status 0x0ffff03f read as 0x3ff0ff0f).
  def pcieSwap(dw: Bits): Bits = dw(7 downto 0) ## dw(15 downto 8) ## dw(23 downto 16) ## dw(31 downto 24)
  val capturedPayload = Bits(32 bits)
  val capturedData = pcieSwap(capturedPayload)
  when(fourDw) {
    capturedAddr := header3.asUInt.resized
    capturedPayload := dword(io.tlRxData, 4)
  } otherwise {
    capturedAddr := header2.asUInt.resized
    capturedPayload := header3
  }

  val offset20 = capturedAddr(windowAddressWidth - 1 downto 0)
  val headerFirstBe = header1(3 downto 0)
  val inRegisterSpace = windowInRegisterSpace
  val isWindowReg = offset20 === windowAddress &&
    (inRegisterSpace || (hasData && headerFirstBe === B"0001"))
  val mappedAddr = (window @@ offset20).resized

  io.window := window
  val isLocal = windowIsLocal && !isWindowReg
  val isWedgeReg = isLocal && offset20(7 downto 0) === 0x40
  io.localWrite := False
  // Local reads take a cycle: the address is latched, then the read mux is
  // sampled from the latch (TLP address -> window 31 read mux -> readData
  // missed 75 MHz in one cycle, B134/B137).
  // The window register and wedged reads are answered from that state too,
  // so readData never takes a value decoded from the TLP header directly.
  object ReadSrc extends SpinalEnum { val local, wedge, window, ones = newElement() }
  val localReadAddr = Reg(UInt(8 bits)) init (0)
  val localReadSrc = Reg(ReadSrc()) init (ReadSrc.local)
  io.localAddr := offset20(7 downto 0)
  io.localRaddr := localReadAddr
  io.localWdata := capturedData
  io.tlRxWait := False
  io.tlTxSop := False
  io.tlTxEop := False
  io.tlTxValid := 0
  io.tlTxData := 0

  io.axi.aw.valid := awPend
  io.axi.aw.addr := tlpAddr.resized
  io.axi.aw.prot := 0
  io.axi.w.valid := wPend
  io.axi.w.data := writeData
  io.axi.w.strb := firstBe
  io.axi.b.ready := True
  io.axi.ar.valid := arPend
  io.axi.ar.addr := tlpAddr.resized
  io.axi.ar.prot := 0
  io.axi.r.ready := True
  when(io.axi.aw.fire) { awPend := False }
  when(io.axi.w.fire) { wPend := False }
  when(io.axi.ar.fire) { arPend := False }
  val bForUs = io.axi.b.valid && dropB === 0
  var bOutIncSig: Bool = null
  val rForUs = io.axi.r.valid && dropR === 0
  val dropBInc, dropRInc = False
  val dropBDec = io.axi.b.valid && dropB =/= 0
  val dropRDec = io.axi.r.valid && dropR =/= 0
  // Posted mode: on a timeout every outstanding B becomes one to discard.
  val dropAllOut = False
  // (A B taken this cycle is ours and already off the count.)
  val bOutLeft = (bOut - bForUs.asUInt.resize(6 bits)).resize(7 bits)
  dropB := dropB + Mux(dropAllOut, bOutLeft, dropBInc.asUInt.resize(7 bits)) - dropBDec.asUInt.resized
  if (postedWrites) {
    val bOutInc = False
    val bOutDec = bForUs
    bOut := Mux(dropAllOut, U(0, 6 bits), bOut + bOutInc.asUInt.resized - bOutDec.asUInt.resized)
    bOutIncSig = bOutInc
  } else bOut := 0
  dropR := dropR + dropRInc.asUInt.resized - dropRDec.asUInt.resized

  val cplCreditOk = io.tlTxCreditsCpl(31)

  val unwedge = wedged && axiIdle && dropB === 0 && dropR === 0
  when(unwedge) { wedged := False }
  def dropTlp(): Unit = {
    when(!droppedTlps.andR) { droppedTlps := droppedTlps + 1 }
  }

  def driveCpl(data: Bits, withData: Boolean): Unit = {
    val dw0 = Bits(32 bits)
    val dw1 = Bits(32 bits)
    val dw2 = Bits(32 bits)
    dw0 := 0
    dw1 := 0
    dw2 := 0
    dw0(31 downto 29) := (if (withData) B"010" else B"000")
    dw0(28 downto 24) := B"01010"
    dw0(9 downto 0) := (if (withData) B(1, 10 bits) else B(0, 10 bits))
    dw1(31 downto 16) := (io.tlCfgBusdev @@ U(0, 3 bits)).asBits
    dw1(11 downto 0) := (if (withData) B(4, 12 bits) else B(0, 12 bits))
    dw2(31 downto 16) := reqId
    dw2(15 downto 8) := reqTag
    dw2(6 downto 0) := lowerAddr
    io.tlTxSop := True
    io.tlTxEop := True
    io.tlTxValid := (if (withData) B"11110000" else B"11100000")
    io.tlTxData := dw0 ## dw1 ## dw2 ## (if (withData) pcieSwap(data) else B(0, 32 bits)) ## B(0, 128 bits)
  }

  // tlpAddr / writeData only drive AXI channels that are pending, so with
  // none pending they can load on every TLP start: their enables then skip
  // the TLP decode and wedged (-2.4 ns on clk100 in one B162 placement).
  when(state === State.idle && axiIdle && rxBeat && io.tlRxSop) {
    tlpAddr := mappedAddr
    writeData := capturedData
  }

  switch(state) {
    is(State.idle) {
      stall := 0
      if (postedWrites) {
        // A posted write still waiting for AXI to take it: a core that never
        // does must wedge the target, not stall the link.
        when(!axiIdle && !wedged) {
          stall := stall
          when(prescaleTick) { stall := stall + 1 }
          when(writeTimeout) {
            wedged := True
            writeTimeoutSeen := True
            dropAllOut := True
          }
        }
      }
      // Don't take a new TLP while an abandoned request still waits for its
      // AXI handshake (tlpAddr / writeData drive the pending channels) -
      // unless wedged, when TLPs never reach AXI.
      val canTake = (if (postedWrites) axiIdle && !bOutFull else axiIdle) || wedged
      io.tlRxWait := !canTake
      when(canTake && rxBeat && io.tlRxSop && bar0 && isMem) {
        reqId := header1(31 downto 16)
        reqTag := header1(15 downto 8)
        firstBe := header1(3 downto 0)
        lowerAddr := capturedAddr.asBits(6 downto 0)
        when(isWindowReg) {
          when(hasData) {
            val newWindow = capturedData(windowBits - 1 downto 0).asUInt
            window := newWindow
            windowInRegisterSpace := newWindow < 4
            windowIsLocal := newWindow === localWindow
            when(!io.tlRxEop) {
              state := State.drop
            }
          } otherwise {
            localReadSrc := ReadSrc.window
            state := State.localRead
          }
        } elsewhen (isLocal) {
          when(hasData) {
            io.localWrite := True
            when(isWedgeReg) {
              writeTimeoutSeen := False
              readTimeoutSeen := False
              droppedTlps := 0
            }
            when(!io.tlRxEop) {
              state := State.drop
            }
          } otherwise {
            localReadAddr := offset20(7 downto 0)
            localReadSrc := Mux(isWedgeReg, ReadSrc.wedge, ReadSrc.local)
            state := State.localRead
          }
        } elsewhen (wedged) {
          dropTlp()
          when(hasData) {
            when(!io.tlRxEop) {
              state := State.drop
            }
          } otherwise {
            localReadSrc := ReadSrc.ones
            state := State.localRead
          }
        } otherwise {
          when(hasData) {
            awPend := True
            wPend := True
            if (postedWrites) {
              bOutIncSig := True
              val lengthDw = header0(9 downto 0).asUInt
              when(lengthDw =/= 1) {
                // Length 0 encodes 1024 dwords; Gowin hands us TLPs no
                // longer than the max payload (512 bytes) anyway.
                burstLeft := lengthDw - 1
                for (k <- 0 until 4) sopRest(k) := dword(io.tlRxData, 4 + k)
                burstInSop := True
                sopEop := io.tlRxEop
                burstPos := Mux(fourDw, U(5, 3 bits), U(4, 3 bits))
                burstLastBe := header1(7 downto 4)
                state := State.burst
              } elsewhen (!io.tlRxEop) { state := State.drop }
            } else {
              state := State.axiWrite
            }
          } otherwise {
            if (postedWrites) {
              state := State.waitB
            } else {
              arPend := True
              state := State.axiRead
            }
          }
        }
      } elsewhen (canTake && rxBeat && io.tlRxSop && !bar0) {
        when(!io.tlRxEop) {
          state := State.drop
        }
      }
    }
    is(State.localRead) {
      io.tlRxWait := True
      switch(localReadSrc) {
        is(ReadSrc.local) { readData := io.localRdata }
        is(ReadSrc.wedge) { readData := wedgeStatus }
        is(ReadSrc.window) { readData := window.resize(32 bits).asBits }
        is(ReadSrc.ones) { readData := B(0xffffffffL, 32 bits) }
      }
      state := State.cpl
    }
    is(State.axiWrite) {
      io.tlRxWait := True
      when(bForUs) {
        state := State.idle
      } elsewhen (writeTimeout) {
        dropBInc := True     // its B, if it ever comes, is discarded
        wedged := True
        writeTimeoutSeen := True
        state := State.idle
      } otherwise {
        when(prescaleTick) { stall := stall + 1 }
      }
    }
    is(State.waitB) {
      // Posted mode: a read goes to AXI once every earlier write's B is in.
      io.tlRxWait := True
      when(bOut === 0 && axiIdle) {
        arPend := True
        stall := 0
        state := State.axiRead
      } elsewhen (writeTimeout) {
        wedged := True
        writeTimeoutSeen := True
        dropAllOut := True
        readData := B(0xffffffffL, 32 bits)
        state := State.cpl
      } otherwise {
        when(prescaleTick) { stall := stall + 1 }
      }
    }
    is(State.axiRead) {
      io.tlRxWait := True
      when(rForUs) {
        readData := io.axi.r.data
        state := State.cpl
      } elsewhen (readTimeout) {
        dropRInc := True     // discard the late R
        wedged := True
        readTimeoutSeen := True
        readData := B(0xffffffffL, 32 bits)
        state := State.cpl
      } otherwise {
        when(prescaleTick) { stall := stall + 1 }
      }
    }
    is(State.cpl) {
      io.tlRxWait := True
      when(cplCreditOk) {
        driveCpl(readData, withData = true)
        when(!io.tlTxWait) {
          state := State.idle
        }
      }
    }
    is(State.burst) {
      if (postedWrites) {
        // A later beat stays on the bus (wait high) until its last dword.
        io.tlRxWait := True
        val inPlace = !burstInSop
        val haveDw = burstInSop || rxBeat
        when(wedged) {
          // A write timed out meanwhile: drop the rest of this TLP (a held
          // beat is consumed by drop).
          state := Mux(burstInSop && sopEop, State.idle, State.drop)
        } elsewhen (!axiIdle || bOutFull) {
          // As in idle: a core that never takes the write must wedge the
          // target, not stall the link.
          when(prescaleTick) { stall := stall + 1 }
          when(writeTimeout) {
            wedged := True
            writeTimeoutSeen := True
            dropAllOut := True
          }
        } elsewhen (haveDw) {
          stall := 0
          val dw = Mux(burstInSop, sopRest(burstPos(1 downto 0)),
            io.tlRxData.subdivideIn(32 bits).reverse(burstPos))
          tlpAddr := tlpAddr + 4
          writeData := pcieSwap(dw)
          firstBe := Mux(burstLeft === 1, burstLastBe, B"1111")
          awPend := True
          wPend := True
          bOutIncSig := True
          burstLeft := burstLeft - 1
          burstPos := burstPos + 1
          val beatDone = burstPos === 7 || burstLeft === 1
          when(inPlace && beatDone) { io.tlRxWait := False } // take the beat
          when(burstPos === 7) { burstInSop := False }
          when(burstLeft === 1) { state := State.idle }
        }
      } else state := State.idle
    }
    is(State.drop) {
      io.tlRxWait := False
      when(rxBeat && io.tlRxEop) {
        state := State.idle
      }
    }
  }
}
