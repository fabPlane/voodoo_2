package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._

object PcieTlpBarTarget {
  val tlpDataWidth = 256
  val tlpValidWidth = 8
  val barDecWidth = 6
  val windowAddress = 0x250
  val windowAddressWidth = 20
  val voodooAddressWidth = 24

  def axiConfig(addressWidth: Int = voodooAddressWidth) =
    AxiLite4Config(addressWidth = addressWidth, dataWidth = 32)
}

/** Gowin PCIe Controller TLP BAR0 target.
  *
  * Turns 32-bit Memory Read/Write TLPs on BAR0 into AXI4-Lite. Gowin BAR
  * windows top out at 1 MiB, so a host-visible window register at byte 0x250
  * selects which megabyte of the 16 MiB Voodoo map the TLP offset lands in.
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
    timeoutLog2: Int = 20
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
    val window = out UInt (4 bits)
  }

  def dword(data: Bits, index: Int): Bits =
    data(255 - index * 32 downto 224 - index * 32)

  object State extends SpinalEnum {
    val idle, axiWrite, axiRead, cpl, drop = newElement()
  }

  val state = RegInit(State.idle)
  val window = Reg(UInt(4 bits)) init (0)
  val reqId = Reg(Bits(16 bits)) init (0)
  val reqTag = Reg(Bits(8 bits)) init (0)
  val firstBe = Reg(Bits(4 bits)) init (0)
  val tlpAddr = Reg(UInt(addressWidth bits)) init (0)
  val writeData = Reg(Bits(32 bits)) init (0)
  val readData = Reg(Bits(32 bits)) init (0)
  val lowerAddr = Reg(Bits(7 bits)) init (0)
  val stall = Reg(UInt((timeoutLog2 + 1) bits)) init (0)
  val awSent = RegInit(False)
  val wSent = RegInit(False)
  val arSent = RegInit(False)

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
  val capturedData = Bits(32 bits)
  when(fourDw) {
    capturedAddr := header3.asUInt.resized
    capturedData := dword(io.tlRxData, 4)
  } otherwise {
    capturedAddr := header2.asUInt.resized
    capturedData := header3
  }

  val offset20 = capturedAddr(windowAddressWidth - 1 downto 0)
  val headerFirstBe = header1(3 downto 0)
  val inRegisterSpace = window < 4
  val isWindowReg = offset20 === windowAddress &&
    (inRegisterSpace || (hasData && headerFirstBe === B"0001"))
  val mappedAddr = (window @@ offset20).resized

  io.window := window
  io.tlRxWait := False
  io.tlTxSop := False
  io.tlTxEop := False
  io.tlTxValid := 0
  io.tlTxData := 0

  io.axi.aw.valid := False
  io.axi.aw.addr := tlpAddr.resized
  io.axi.aw.prot := 0
  io.axi.w.valid := False
  io.axi.w.data := writeData
  io.axi.w.strb := firstBe
  io.axi.b.ready := False
  io.axi.ar.valid := False
  io.axi.ar.addr := tlpAddr.resized
  io.axi.ar.prot := 0
  io.axi.r.ready := False

  val cplCreditOk = io.tlTxCreditsCpl(31)

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
    io.tlTxData := dw0 ## dw1 ## dw2 ## (if (withData) data else B(0, 32 bits)) ## B(0, 128 bits)
  }

  switch(state) {
    is(State.idle) {
      stall := 0
      when(rxBeat && io.tlRxSop && bar0 && isMem) {
        reqId := header1(31 downto 16)
        reqTag := header1(15 downto 8)
        firstBe := header1(3 downto 0)
        lowerAddr := capturedAddr.asBits(6 downto 0)
        awSent := False
        wSent := False
        arSent := False
        when(isWindowReg) {
          when(hasData) {
            window := capturedData(3 downto 0).asUInt
            when(!io.tlRxEop) {
              state := State.drop
            }
          } otherwise {
            readData := window.resize(32 bits).asBits
            tlpAddr := 0
            state := State.cpl
          }
        } otherwise {
          tlpAddr := mappedAddr
          writeData := capturedData
          when(hasData) {
            state := State.axiWrite
          } otherwise {
            state := State.axiRead
          }
        }
      } elsewhen (rxBeat && io.tlRxSop && !bar0) {
        when(!io.tlRxEop) {
          state := State.drop
        }
      }
    }
    is(State.axiWrite) {
      io.tlRxWait := True
      io.axi.aw.valid := !awSent
      io.axi.w.valid := !wSent
      io.axi.b.ready := True
      when(io.axi.aw.fire) {
        awSent := True
      }
      when(io.axi.w.fire) {
        wSent := True
      }
      when(io.axi.b.valid) {
        awSent := False
        wSent := False
        state := State.idle
      }
      when(stall === (1 << timeoutLog2) - 1) {
        awSent := False
        wSent := False
        state := State.idle
      } otherwise {
        stall := stall + 1
      }
    }
    is(State.axiRead) {
      io.tlRxWait := True
      io.axi.ar.valid := !arSent
      io.axi.r.ready := True
      when(io.axi.ar.fire) {
        arSent := True
      }
      when(io.axi.r.valid) {
        readData := io.axi.r.data
        arSent := False
        state := State.cpl
      }
      when(stall === (1 << timeoutLog2) - 1) {
        readData := 0
        arSent := False
        state := State.cpl
      } otherwise {
        stall := stall + 1
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
    is(State.drop) {
      io.tlRxWait := False
      when(rxBeat && io.tlRxEop) {
        state := State.idle
      }
    }
  }
}
