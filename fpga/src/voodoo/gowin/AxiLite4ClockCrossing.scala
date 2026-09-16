package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._

/** Two-clock AXI4-Lite crossing. Depth is small; the TLP BAR target only has
  * one outstanding transaction.
  */
case class AxiLite4ClockCrossing(
    config: AxiLite4Config,
    inputCd: ClockDomain,
    outputCd: ClockDomain,
    depth: Int = 4
) extends Component {
  val io = new Bundle {
    val input = slave(AxiLite4(config))
    val output = master(AxiLite4(config))
  }

  val aw = StreamFifoCC(io.input.aw.payload, depth, inputCd, outputCd)
  val w = StreamFifoCC(io.input.w.payload, depth, inputCd, outputCd)
  val ar = StreamFifoCC(io.input.ar.payload, depth, inputCd, outputCd)
  val b = StreamFifoCC(io.output.b.payload, depth, outputCd, inputCd)
  val r = StreamFifoCC(io.output.r.payload, depth, outputCd, inputCd)

  aw.io.push << io.input.aw
  io.output.aw << aw.io.pop
  w.io.push << io.input.w
  io.output.w << w.io.pop
  ar.io.push << io.input.ar
  io.output.ar << ar.io.pop
  b.io.push << io.output.b
  io.input.b << b.io.pop
  r.io.push << io.output.r
  io.input.r << r.io.pop
}
