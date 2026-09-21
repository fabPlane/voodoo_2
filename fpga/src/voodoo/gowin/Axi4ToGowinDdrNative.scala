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

/** One-outstanding AXI4 (32-bit INCR) to Gowin DDR3 native UI.
  *
  * Framebuffer wins over texture when both request. wr_data_mask is active-high
  * inhibit, the inverse of AXI wstrb. nativeDataWidth is 256 on the 32-bit Pro
  * Dock and 128 on the Console (one x16 chip).
  */
case class Axi4ToGowinDdrNative(
    fbConfig: Axi4Config,
    texConfig: Axi4Config,
    nativeDataWidth: Int = GowinAddressMap.ddrNativeDataWidth,
    nativeAddrWidth: Int = GowinAddressMap.ddrNativeAddrWidth
) extends Component {
  require(fbConfig.dataWidth == 32 && texConfig.dataWidth == 32)
  require(nativeDataWidth % 32 == 0 && nativeDataWidth >= 32)
  val nativeWords = nativeDataWidth / 32
  val beatBytes = GowinDdrNative.beatBytes(nativeDataWidth)
  // The Gowin native address is a physical DDR word address, not a byte
  // address. A 1:4 BL8 transfer always consumes eight of those addresses:
  // x16 Console => 2 bytes/address, x32 Pro Dock => 4 bytes/address.
  val nativeWordBytes = nativeDataWidth / 64
  val nativeAddrShift = log2Up(nativeWordBytes)
  val beatOffsetBits = log2Up(beatBytes)
  val lastIdx = nativeWords - 1
  require(nativeWordBytes >= 1 && (nativeWordBytes & (nativeWordBytes - 1)) == 0)

  val io = new Bundle {
    val fb = slave(Axi4(fbConfig))
    val tex = slave(Axi4(texConfig))
    val ddr = master(GowinDdrNative(addrWidth = nativeAddrWidth, dataWidth = nativeDataWidth))
  }

  object State extends SpinalEnum {
    val idle, wrCollect, wrIssue, wrResp, rdIssue, rdHold, rdUnpack = newElement()
  }

  val state = RegInit(State.idle)
  val useTex = RegInit(False)
  val addr = Reg(UInt(32 bits)) init (0)
  val beatsLeft = Reg(UInt(9 bits)) init (0)
  val groupIdx = Reg(UInt(log2Up(nativeWords) bits)) init (0)
  val words = Vec(Reg(Bits(32 bits)) init (0), nativeWords)
  val strbs = Vec(Reg(Bits(4 bits)) init (0), nativeWords)
  val readWords = Vec(Reg(Bits(32 bits)) init (0), nativeWords)
  val unpackIdx = Reg(UInt(log2Up(nativeWords) bits)) init (0)
  val fbId = if (fbConfig.useId) Reg(UInt(fbConfig.idWidth bits)) init (0) else null
  val texId = if (texConfig.useId) Reg(UInt(texConfig.idWidth bits)) init (0) else null

  io.fb.aw.ready := False
  io.fb.w.ready := False
  io.fb.b.valid := False
  io.fb.b.resp := 0
  if (fbConfig.useId) io.fb.b.id := fbId
  io.fb.ar.ready := False
  io.fb.r.valid := False
  io.fb.r.data := 0
  io.fb.r.resp := 0
  io.fb.r.last := False
  if (fbConfig.useId) io.fb.r.id := fbId

  io.tex.aw.ready := False
  io.tex.w.ready := False
  io.tex.b.valid := False
  io.tex.b.resp := 0
  if (texConfig.useId) io.tex.b.id := texId
  io.tex.ar.ready := False
  io.tex.r.valid := False
  io.tex.r.data := 0
  io.tex.r.resp := 0
  io.tex.r.last := False
  if (texConfig.useId) io.tex.r.id := texId

  io.ddr.cmd := 0
  io.ddr.cmdEn := False
  val nativeAddr = UInt(nativeAddrWidth bits)
  nativeAddr := (addr >> nativeAddrShift).resized
  // BL8 commands start on an eight-address boundary. AXI's low address bits
  // select a 32-bit lane through the byte mask instead of shifting the DDR
  // command itself into the middle of a physical burst.
  nativeAddr(2 downto 0) := 0
  io.ddr.addr := nativeAddr
  io.ddr.burst := False
  // Cat(Iterable) places the first element at the least-significant end.
  // Keep write lane i symmetric with rdData(i * 32, 32 bits) below.
  io.ddr.wrData := Cat(words)
  io.ddr.wrDataEn := False
  io.ddr.wrDataEnd := False
  io.ddr.wrDataMask := ~Cat(strbs)

  switch(state) {
    is(State.idle) {
      when(io.ddr.initCalibComplete) {
        when(io.fb.aw.valid) {
          useTex := False
          addr := io.fb.aw.addr.resized
          beatsLeft := (io.fb.aw.len.resize(9) + 1).resized
          if (fbConfig.useId) fbId := io.fb.aw.id
          io.fb.aw.ready := True
          groupIdx := io.fb.aw.addr(beatOffsetBits - 1 downto 2).resized
          words.foreach(_ := 0)
          strbs.foreach(_ := 0)
          state := State.wrCollect
        } elsewhen (io.fb.ar.valid) {
          useTex := False
          addr := io.fb.ar.addr.resized
          beatsLeft := (io.fb.ar.len.resize(9) + 1).resized
          if (fbConfig.useId) fbId := io.fb.ar.id
          io.fb.ar.ready := True
          unpackIdx := io.fb.ar.addr(beatOffsetBits - 1 downto 2).resized
          state := State.rdIssue
        } elsewhen (io.tex.aw.valid) {
          useTex := True
          addr := io.tex.aw.addr.resized
          beatsLeft := (io.tex.aw.len.resize(9) + 1).resized
          if (texConfig.useId) texId := io.tex.aw.id
          io.tex.aw.ready := True
          groupIdx := io.tex.aw.addr(beatOffsetBits - 1 downto 2).resized
          words.foreach(_ := 0)
          strbs.foreach(_ := 0)
          state := State.wrCollect
        } elsewhen (io.tex.ar.valid) {
          useTex := True
          addr := io.tex.ar.addr.resized
          beatsLeft := (io.tex.ar.len.resize(9) + 1).resized
          if (texConfig.useId) texId := io.tex.ar.id
          io.tex.ar.ready := True
          unpackIdx := io.tex.ar.addr(beatOffsetBits - 1 downto 2).resized
          state := State.rdIssue
        }
      }
    }
    is(State.wrCollect) {
      val wValid = Mux(useTex, io.tex.w.valid, io.fb.w.valid)
      val wData = Mux(useTex, io.tex.w.data, io.fb.w.data)
      val wStrb = Mux(useTex, io.tex.w.strb, io.fb.w.strb)
      val wLast = Mux(useTex, io.tex.w.last, io.fb.w.last)
      when(useTex) { io.tex.w.ready := True }
        .otherwise { io.fb.w.ready := True }
      when(wValid) {
        words(groupIdx) := wData
        strbs(groupIdx) := wStrb
        beatsLeft := beatsLeft - 1
        when(groupIdx === lastIdx || wLast || beatsLeft === 1) {
          groupIdx := 0
          state := State.wrIssue
        } otherwise {
          groupIdx := groupIdx + 1
        }
      }
    }
    is(State.wrIssue) {
      val fire = io.ddr.cmdReady && io.ddr.wrDataRdy
      io.ddr.cmd := GowinDdrNative.cmdWrite
      io.ddr.cmdEn := fire
      io.ddr.wrDataEn := fire
      io.ddr.wrDataEnd := True
      when(fire) {
        addr := addr + beatBytes
        when(beatsLeft === 0) {
          state := State.wrResp
        } otherwise {
          words.foreach(_ := 0)
          strbs.foreach(_ := 0)
          state := State.wrCollect
        }
      }
    }
    is(State.wrResp) {
      when(useTex) { io.tex.b.valid := True }
        .otherwise { io.fb.b.valid := True }
      val bReady = Mux(useTex, io.tex.b.ready, io.fb.b.ready)
      when(bReady) {
        state := State.idle
      }
    }
    is(State.rdIssue) {
      io.ddr.cmd := GowinDdrNative.cmdRead
      io.ddr.cmdEn := io.ddr.cmdReady
      when(io.ddr.cmdReady) {
        state := State.rdHold
      }
    }
    is(State.rdHold) {
      when(io.ddr.rdDataValid) {
        for (i <- 0 until nativeWords) {
          readWords(i) := io.ddr.rdData(i * 32, 32 bits)
        }
        state := State.rdUnpack
      }
    }
    is(State.rdUnpack) {
      val lastBeat = beatsLeft === 1
      when(useTex) {
        io.tex.r.valid := True
        io.tex.r.data := readWords(unpackIdx)
        io.tex.r.last := lastBeat
      } otherwise {
        io.fb.r.valid := True
        io.fb.r.data := readWords(unpackIdx)
        io.fb.r.last := lastBeat
      }
      val rReady = Mux(useTex, io.tex.r.ready, io.fb.r.ready)
      when(rReady) {
        beatsLeft := beatsLeft - 1
        when(lastBeat) {
          state := State.idle
        } elsewhen (unpackIdx === lastIdx) {
          addr := addr + beatBytes
          unpackIdx := 0
          state := State.rdIssue
        } otherwise {
          unpackIdx := unpackIdx + 1
        }
      }
    }
  }
}
