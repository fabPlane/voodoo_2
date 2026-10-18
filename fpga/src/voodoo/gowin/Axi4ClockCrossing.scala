package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axi._

/** Two-clock AXI4 crossing, like Axi4CC, except that the R and B FIFOs
  * (pushed from the memory side) accept data on a registered "room" flag.
  *
  * In Axi4CC a FIFO's push ready is its full flag, compared combinationally
  * against the gray pop pointer synchronised from the other clock, and the
  * adapter's R / B register enables hang off it. With the FIFO BSRAMs placed
  * away from the DDR glue that was the most common failing path on
  * ddr_clk_out (B104-B108). Here the push side sees room only while the
  * FIFO held fewer than depth - 2 entries last cycle; the synchronised pop
  * pointer lags, so the occupancy it gives is never too low and one push
  * can follow the sample without overflowing.
  */
case class Axi4ClockCrossing(
    config: Axi4Config,
    inputCd: ClockDomain,
    outputCd: ClockDomain,
    arFifoSize: Int,
    awFifoSize: Int,
    rFifoSize: Int,
    wFifoSize: Int,
    bFifoSize: Int
) extends Component {
  require(rFifoSize >= 4 && bFifoSize >= 4)
  val io = new Bundle {
    val input = slave(Axi4(config))
    val output = master(Axi4(config))
  }

  val ar = StreamFifoCC(io.input.ar.payload, arFifoSize, inputCd, outputCd)
  val aw = StreamFifoCC(io.input.aw.payload, awFifoSize, inputCd, outputCd)
  val w = StreamFifoCC(io.input.w.payload, wFifoSize, inputCd, outputCd)
  val r = StreamFifoCC(io.output.r.payload, rFifoSize, outputCd, inputCd)
  val b = StreamFifoCC(io.output.b.payload, bFifoSize, outputCd, inputCd)

  ar.io.push << io.input.ar
  io.output.ar << ar.io.pop
  aw.io.push << io.input.aw
  io.output.aw << aw.io.pop
  w.io.push << io.input.w
  io.output.w << w.io.pop

  private def registeredRoomPush[T <: Data](in: Stream[T], fifo: StreamFifoCC[T], depth: Int): Unit =
    new ClockingArea(outputCd) {
      val room = RegNext(fifo.io.pushOccupancy < depth - 2) init (False)
      fifo.io.push.valid := in.valid && room
      fifo.io.push.payload := in.payload
      in.ready := room
    }
  registeredRoomPush(io.output.r, r, rFifoSize)
  registeredRoomPush(io.output.b, b, bFifoSize)
  io.input.r << r.io.pop
  io.input.b << b.io.pop
}
