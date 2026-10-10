package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.amba4.axilite._

/** Splits the BAR's AXI-Lite by address bit `addressWidth - 1`: clear goes to
  * the Voodoo (16 MiB map), set goes to `raw` (raw DDR, for trace state load
  * and framebuffer readback). Writes pair AW with W before choosing a side.
  */
case class AxiLite4RawSplit(addressWidth: Int) extends Component {
  val inConfig = AxiLite4Config(addressWidth = addressWidth, dataWidth = 32)
  val outConfig = AxiLite4Config(addressWidth = addressWidth - 1, dataWidth = 32)
  val io = new Bundle {
    val input = slave(AxiLite4(inConfig))
    val core = master(AxiLite4(outConfig))
    val raw = master(AxiLite4(outConfig))
  }

  val outs = Seq(io.core, io.raw)
  def sel(addr: UInt): Bool = addr.msb

  // Write: wait until both AW and W are present, then hand each to the chosen
  // side; pop the input pair once both have been accepted.
  val wrPair = io.input.aw.valid && io.input.w.valid
  val wrRaw = sel(io.input.aw.addr)
  val awDone = RegInit(False)
  val wDone = RegInit(False)
  for ((o, isRaw) <- outs.zip(Seq(False, True))) {
    val mine = wrPair && wrRaw === isRaw
    o.aw.valid := mine && !awDone
    o.aw.addr := io.input.aw.addr.resized
    o.aw.prot := io.input.aw.prot
    o.w.valid := mine && !wDone
    o.w.data := io.input.w.data
    o.w.strb := io.input.w.strb
  }
  val awTaken = Mux(wrRaw, io.raw.aw.fire, io.core.aw.fire)
  val wTaken = Mux(wrRaw, io.raw.w.fire, io.core.w.fire)
  val wrDone = (awDone || awTaken) && (wDone || wTaken)
  io.input.aw.ready := wrDone
  io.input.w.ready := wrDone
  when(awTaken) { awDone := True }
  when(wTaken) { wDone := True }
  when(wrDone) { awDone := False; wDone := False }

  // Read: AR alone picks the side.
  val rdRaw = sel(io.input.ar.addr)
  for ((o, isRaw) <- outs.zip(Seq(False, True))) {
    o.ar.valid := io.input.ar.valid && rdRaw === isRaw
    o.ar.addr := io.input.ar.addr.resized
    o.ar.prot := io.input.ar.prot
  }
  io.input.ar.ready := Mux(rdRaw, io.raw.ar.ready, io.core.ar.ready)

  // Responses: the BAR target has one transaction in flight at a time, so at
  // most one side answers; core wins if both ever do.
  io.input.b.valid := io.core.b.valid || io.raw.b.valid
  io.input.b.resp := Mux(io.core.b.valid, io.core.b.resp, io.raw.b.resp)
  io.core.b.ready := io.input.b.ready
  io.raw.b.ready := io.input.b.ready && !io.core.b.valid
  io.input.r.valid := io.core.r.valid || io.raw.r.valid
  io.input.r.data := Mux(io.core.r.valid, io.core.r.data, io.raw.r.data)
  io.input.r.resp := Mux(io.core.r.valid, io.core.r.resp, io.raw.r.resp)
  io.core.r.ready := io.input.r.ready
  io.raw.r.ready := io.input.r.ready && !io.core.r.valid
}

/** AXI-Lite (32-bit, one beat) to AXI4 single-beat transactions at
  * `baseAddr + addr`, for the raw-DDR window.
  */
case class AxiLite4ToAxi4Single(liteConfig: AxiLite4Config, axiConfig: Axi4Config) extends Component {
  require(axiConfig.dataWidth == 32)
  val io = new Bundle {
    val input = slave(AxiLite4(liteConfig))
    val output = master(Axi4(axiConfig))
  }
  io.output.aw.valid := io.input.aw.valid
  io.output.aw.payload.assignDontCare()
  io.output.aw.addr := io.input.aw.addr.resized
  if (axiConfig.useId) io.output.aw.id := 0
  if (axiConfig.useLen) io.output.aw.len := 0
  if (axiConfig.useSize) io.output.aw.size := 2
  if (axiConfig.useBurst) io.output.aw.burst := Axi4.burst.INCR
  if (axiConfig.useProt) io.output.aw.prot := io.input.aw.prot
  if (axiConfig.useCache) io.output.aw.cache := 0
  if (axiConfig.useLock) io.output.aw.lock := 0
  if (axiConfig.useQos) io.output.aw.qos := 0
  if (axiConfig.useRegion) io.output.aw.region := 0
  io.input.aw.ready := io.output.aw.ready

  io.output.w.valid := io.input.w.valid
  io.output.w.payload.assignDontCare()
  io.output.w.data := io.input.w.data
  if (axiConfig.useStrb) io.output.w.strb := io.input.w.strb
  if (axiConfig.useLast) io.output.w.last := True
  io.input.w.ready := io.output.w.ready

  io.input.b.valid := io.output.b.valid
  io.input.b.resp := (if (axiConfig.useResp) io.output.b.resp else B"00")
  io.output.b.ready := io.input.b.ready

  io.output.ar.valid := io.input.ar.valid
  io.output.ar.payload.assignDontCare()
  io.output.ar.addr := io.input.ar.addr.resized
  if (axiConfig.useId) io.output.ar.id := 0
  if (axiConfig.useLen) io.output.ar.len := 0
  if (axiConfig.useSize) io.output.ar.size := 2
  if (axiConfig.useBurst) io.output.ar.burst := Axi4.burst.INCR
  if (axiConfig.useProt) io.output.ar.prot := io.input.ar.prot
  if (axiConfig.useCache) io.output.ar.cache := 0
  if (axiConfig.useLock) io.output.ar.lock := 0
  if (axiConfig.useQos) io.output.ar.qos := 0
  if (axiConfig.useRegion) io.output.ar.region := 0
  io.input.ar.ready := io.output.ar.ready

  io.input.r.valid := io.output.r.valid
  io.input.r.data := io.output.r.data
  io.input.r.resp := (if (axiConfig.useResp) io.output.r.resp else B"00")
  io.output.r.ready := io.input.r.ready
}

/** Two AXI4 masters (Voodoo framebuffer port, host raw window) onto one AXI4
  * port; the output ID is one bit wider (input index in the top bit).
  */
case class Axi4TwoToOne(inConfig: Axi4Config) extends Component {
  val outConfig = inConfig.copy(idWidth = inConfig.idWidth + 1)
  val io = new Bundle {
    val inputs = Vec(slave(Axi4(inConfig)), 2)
    val output = master(Axi4(outConfig))
  }
  val rd = Axi4ReadOnlyArbiter(outConfig, 2)
  val wr = Axi4WriteOnlyArbiter(outConfig, 2, routeBufferSize = 4)
  for (i <- 0 until 2) {
    rd.io.inputs(i).ar << io.inputs(i).ar
    io.inputs(i).r << rd.io.inputs(i).r
    wr.io.inputs(i).aw << io.inputs(i).aw
    wr.io.inputs(i).w << io.inputs(i).w
    io.inputs(i).b << wr.io.inputs(i).b
  }
  io.output.ar << rd.io.output.ar
  rd.io.output.r << io.output.r
  io.output.aw << wr.io.output.aw
  io.output.w << wr.io.output.w
  wr.io.output.b << io.output.b
}
