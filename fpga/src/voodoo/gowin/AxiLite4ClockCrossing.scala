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
  // Output side registered both ways (valid and ready): the pops' ready came
  // combinationally from the raw-window split / merge into the fb crossing
  // and missed 48 MHz into the FIFOs' read registers (Tang dock B137).
  private def popPipe[T <: Data](s: Stream[T]): Stream[T] = s.s2mPipe().m2sPipe()
  val outRegs = new ClockingArea(outputCd) {
    val awOut = popPipe(aw.io.pop)
    val wOut = popPipe(w.io.pop)
    val arOut = popPipe(ar.io.pop)
  }
  io.output.aw << outRegs.awOut
  w.io.push << io.input.w
  io.output.w << outRegs.wOut
  ar.io.push << io.input.ar
  io.output.ar << outRegs.arOut
  b.io.push << io.output.b
  io.input.b << b.io.pop
  r.io.push << io.output.r
  io.input.r << r.io.pop
}
