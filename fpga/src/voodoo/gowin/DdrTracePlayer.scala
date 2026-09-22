package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._
import voodoo.artix.AxiLite4ToBmbBridge

/** Walk a V2GL image in memory and issue AXI-Lite BAR writes.
  *
  * `io.memCmd` / `io.memRsp` are a 64-bit word read port (byte address,
  * 8-byte aligned). On the Console that port is the JTAG/SD preload window
  * at `GowinAddressMap.glidePlayPhys`. Tests back it with a Mem.
  */
case class DdrTracePlayer(
    addressWidth: Int = CoreGowin.barAddressWidth,
    memAddrWidth: Int = 32
) extends Component {
  val slotWidth = log2Up(GlidePlayImage.maxSlots)

  val io = new Bundle {
    val enable = in Bool ()
    val start = in Bool ()
    val loop = in Bool ()
    val select = in UInt (slotWidth bits)
    val base = in UInt (memAddrWidth bits)
    val memCmd = master(Stream(UInt(memAddrWidth bits)))
    val memRsp = slave(Stream(Bits(64 bits)))
    val axi = master(AxiLite4(AxiLite4ToBmbBridge.axiConfig(addressWidth)))
    val done = out Bool ()
    val fault = out Bool ()
    val index = out UInt (32 bits)
  }

  object State extends SpinalEnum {
    val idle, reqHeader, waitHeader, reqSlotA, waitSlotA, reqSlotB, waitSlotB,
        reqRec, waitRec, issue, waitB, doneSt, faultSt = newElement()
  }

  val state = RegInit(State.idle)
  val slot = Reg(UInt(slotWidth bits)) init (0)
  val recOff = Reg(UInt(memAddrWidth bits)) init (0)
  val recLeft = Reg(UInt(32 bits)) init (0)
  val recTotal = Reg(UInt(32 bits)) init (0)
  val loopAt = Reg(UInt(32 bits)) init (0)
  val recIndex = Reg(UInt(32 bits)) init (0)
  val word = Reg(Bits(64 bits)) init (0)
  val awSent = RegInit(False)
  val wSent = RegInit(False)

  val addr = word(addressWidth + 31 downto 32).asUInt
  val data = word(31 downto 0)
  val issuing = io.enable && state === State.issue

  io.memCmd.valid := False
  io.memCmd.payload := 0
  io.memRsp.ready := False

  io.axi.aw.valid := issuing && !awSent
  io.axi.aw.addr := addr
  io.axi.aw.prot := 0
  io.axi.w.valid := issuing && !wSent
  io.axi.w.data := data
  io.axi.w.strb := 0xf
  io.axi.b.ready := state === State.waitB
  io.axi.ar.valid := False
  io.axi.ar.addr := 0
  io.axi.ar.prot := 0
  io.axi.r.ready := True

  def fireRead(byteAddr: UInt): Unit = {
    io.memCmd.valid := True
    io.memCmd.payload := byteAddr.resized
  }

  val slotBase = io.base + U(GlidePlayImage.headerBytes, memAddrWidth bits) +
    (slot.resize(memAddrWidth) << 4)

  switch(state) {
    is(State.idle) {
      when(io.start) {
        slot := io.select.resized
        recIndex := 0
        state := State.reqHeader
      }
    }
    is(State.reqHeader) {
      fireRead(io.base)
      when(io.memCmd.ready) {
        state := State.waitHeader
      }
    }
    is(State.waitHeader) {
      io.memRsp.ready := True
      when(io.memRsp.valid) {
        val mag = io.memRsp.payload(31 downto 0).asUInt
        val ver = io.memRsp.payload(63 downto 32).asUInt
        when(mag === U(GlidePlayImage.magic, 32 bits) && ver === U(GlidePlayImage.version, 32 bits)) {
          state := State.reqSlotA
        } otherwise {
          state := State.faultSt
        }
      }
    }
    is(State.reqSlotA) {
      fireRead(slotBase)
      when(io.memCmd.ready) {
        state := State.waitSlotA
      }
    }
    is(State.waitSlotA) {
      io.memRsp.ready := True
      when(io.memRsp.valid) {
        val off = io.memRsp.payload(31 downto 0).asUInt
        val count = io.memRsp.payload(63 downto 32).asUInt
        recOff := (io.base + off.resized).resized
        recLeft := count
        recTotal := count
        recIndex := 0
        when(count === 0 || off < U(GlidePlayImage.tableBytes)) {
          state := State.faultSt
        } otherwise {
          state := State.reqSlotB
        }
      }
    }
    is(State.reqSlotB) {
      fireRead(slotBase + U(8, memAddrWidth bits))
      when(io.memCmd.ready) {
        state := State.waitSlotB
      }
    }
    is(State.waitSlotB) {
      io.memRsp.ready := True
      when(io.memRsp.valid) {
        loopAt := io.memRsp.payload(31 downto 0).asUInt
        state := State.reqRec
      }
    }
    is(State.reqRec) {
      fireRead(recOff)
      when(io.memCmd.ready) {
        state := State.waitRec
      }
    }
    is(State.waitRec) {
      io.memRsp.ready := True
      when(io.memRsp.valid) {
        word := io.memRsp.payload
        awSent := False
        wSent := False
        state := State.issue
      }
    }
    is(State.issue) {
      when(io.axi.aw.fire) {
        awSent := True
      }
      when(io.axi.w.fire) {
        wSent := True
      }
      when((awSent || io.axi.aw.fire) && (wSent || io.axi.w.fire)) {
        state := State.waitB
      }
    }
    is(State.waitB) {
      when(io.axi.b.valid) {
        awSent := False
        wSent := False
        recIndex := recIndex + 1
        recOff := recOff + 8
        recLeft := recLeft - 1
        when(recLeft === 1) {
          when(io.loop && recTotal =/= loopAt) {
            recOff := recOff - (((recTotal - 1) - loopAt) << 3).resized
            recLeft := recTotal - loopAt
            recIndex := loopAt
            state := State.reqRec
          } otherwise {
            state := State.doneSt
          }
        } otherwise {
          state := State.reqRec
        }
      }
    }
    is(State.doneSt) {
      when(io.start) {
        slot := io.select.resized
        recIndex := 0
        state := State.reqHeader
      }
    }
    is(State.faultSt) {
      when(io.start) {
        recIndex := 0
        state := State.reqHeader
      }
    }
  }

  io.done := state === State.doneSt
  io.fault := state === State.faultSt
  io.index := recIndex
}
