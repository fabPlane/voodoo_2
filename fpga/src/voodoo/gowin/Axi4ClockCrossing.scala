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
  // A write address goes to the memory side only once its whole burst is in
  // the W FIFO: the DDR adapter's one write engine collects a burst's beats
  // before issuing it, and with W trickling in at the core clock that took
  // ~20 DDR cycles per 8-beat line and limited every write port (B116). The
  // pop-side occupancy lags the push side, so this can only wait longer;
  // beats of already released bursts still in the FIFO are not counted.
  // Bursts must fit the W FIFO (32-byte pieces: 8 beats); read-only ports
  // (W FIFO of 4) pass AW / W straight through.
  //
  // W beats leave the FIFO only for released addresses (wAllowed): the
  // register stages between this crossing and the adapter took beats out of
  // the FIFO before their address went, so a burst could never show whole in
  // the FIFO and its address was never released (B117 hung GLQuake at start).
  w.io.push << io.input.w
  //
  // The count of unreleased beats is registered: gray decode -> subtract ->
  // compare -> release -> wAllowed was a failing ddr_clk_out path (54.5 MHz
  // trials, -0.2 ns). A W pop lowers occupancy and wAllowed together, new
  // beats only raise it, so last cycle's count is too high only after a
  // release: no release in the cycle after one (the adapter's AW half pipe
  // takes one address every other cycle anyway).
  if (wFifoSize >= 8) new ClockingArea(outputCd) {
    val wAllowed = Reg(UInt(log2Up(wFifoSize) + 2 bits)) init (0)
    val wHere = RegNext(w.io.popOccupancy.resize(wAllowed.getWidth) - wAllowed) init (0)
    val awFired = RegNext(io.output.aw.fire) init (False)
    val wReady = !awFired && !wHere.msb && wHere >= (aw.io.pop.len.resize(wAllowed.getWidth) + 1)
    io.output.aw.valid := aw.io.pop.valid && wReady
    io.output.aw.payload := aw.io.pop.payload
    aw.io.pop.ready := io.output.aw.ready && wReady
    io.output.w.valid := w.io.pop.valid && wAllowed =/= 0
    io.output.w.payload := w.io.pop.payload
    w.io.pop.ready := io.output.w.ready && wAllowed =/= 0
    wAllowed := wAllowed +
      Mux(io.output.aw.fire, aw.io.pop.len.resize(wAllowed.getWidth) + 1, U(0, wAllowed.getWidth bits)) -
      Mux(io.output.w.fire, U(1, wAllowed.getWidth bits), U(0, wAllowed.getWidth bits))
  } else {
    io.output.aw << aw.io.pop
    io.output.w << w.io.pop
  }

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
