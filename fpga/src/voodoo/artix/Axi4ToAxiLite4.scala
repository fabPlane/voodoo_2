package voodoo.artix

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.amba4.axilite._

/** 32-bit AXI4 to AXI4-Lite, one outstanding, single-beat.
  *
  * The 7-series AXI PCIe IP exposes BAR0 as AXI4. Host register access is
  * 32-bit MMIO (AWLEN/ARLEN = 0). Extra beats are ignored.
  */
object Axi4ToAxiLite4 {
  def axiConfig(addressWidth: Int, idWidth: Int = 4) = Axi4Config(
    addressWidth = addressWidth,
    dataWidth = 32,
    idWidth = idWidth,
    useLock = false,
    useQos = false,
    useRegion = false,
    useCache = false,
    useProt = true,
    useBurst = true,
    useLen = true,
    useSize = true
  )
}

case class Axi4ToAxiLite4(
    addressWidth: Int,
    idWidth: Int = 4
) extends Component {
  val axiConfig = Axi4ToAxiLite4.axiConfig(addressWidth, idWidth)
  val liteConfig = AxiLite4Config(addressWidth = addressWidth, dataWidth = 32)

  val io = new Bundle {
    val axi = slave(Axi4(axiConfig))
    val lite = master(AxiLite4(liteConfig))
  }

  object State extends SpinalEnum {
    val idle, write, read = newElement()
  }

  val state = RegInit(State.idle)
  val id = Reg(UInt(idWidth bits)) init (0)
  val addr = Reg(UInt(addressWidth bits)) init (0)
  val wdata = Reg(Bits(32 bits)) init (0)
  val wstrb = Reg(Bits(4 bits)) init (0)
  val awSent = RegInit(False)
  val wSent = RegInit(False)
  val arSent = RegInit(False)

  io.axi.aw.ready := False
  io.axi.w.ready := False
  io.axi.b.valid := False
  io.axi.b.id := id
  io.axi.b.setOKAY()
  io.axi.ar.ready := False
  io.axi.r.valid := False
  io.axi.r.id := id
  io.axi.r.data := io.lite.r.data
  io.axi.r.setOKAY()
  io.axi.r.last := True

  io.lite.aw.valid := False
  io.lite.aw.addr := addr
  io.lite.aw.prot := 0
  io.lite.w.valid := False
  io.lite.w.data := wdata
  io.lite.w.strb := wstrb
  io.lite.b.ready := False
  io.lite.ar.valid := False
  io.lite.ar.addr := addr
  io.lite.ar.prot := 0
  io.lite.r.ready := False

  switch(state) {
    is(State.idle) {
      awSent := False
      wSent := False
      arSent := False
      when(io.axi.aw.valid && io.axi.w.valid) {
        id := io.axi.aw.id
        addr := io.axi.aw.addr.resized
        wdata := io.axi.w.data
        wstrb := io.axi.w.strb
        io.axi.aw.ready := True
        io.axi.w.ready := True
        state := State.write
      } elsewhen (io.axi.ar.valid) {
        id := io.axi.ar.id
        addr := io.axi.ar.addr.resized
        io.axi.ar.ready := True
        state := State.read
      }
    }
    is(State.write) {
      io.lite.aw.valid := !awSent
      io.lite.w.valid := !wSent
      io.lite.b.ready := True
      when(io.lite.aw.fire) {
        awSent := True
      }
      when(io.lite.w.fire) {
        wSent := True
      }
      when(io.lite.b.valid) {
        io.axi.b.valid := True
        when(io.lite.b.isSLVERR || io.lite.b.isDECERR) {
          io.axi.b.setSLVERR()
        }
        when(io.axi.b.ready) {
          state := State.idle
        }
      }
    }
    is(State.read) {
      io.lite.ar.valid := !arSent
      io.lite.r.ready := True
      when(io.lite.ar.fire) {
        arSent := True
      }
      when(io.lite.r.valid) {
        io.axi.r.valid := True
        when(io.lite.r.isSLVERR || io.lite.r.isDECERR) {
          io.axi.r.setSLVERR()
        }
        when(io.axi.r.ready) {
          state := State.idle
        }
      }
    }
  }
}
