package voodoo.diff

import spinal.core._
import spinal.lib._
import voodoo.bus.StreamWhile
import voodoo.math.{Fpxx, FpxxConfig}

/** Tiny wrapper Components around upstream constructs that are not Components themselves.
  *
  * `StreamWhile` is an `object` with an `apply` that builds a `Composite` inside an enclosing
  * Component, and `Fpxx` is a `Bundle` (a value type), so neither can be elaborated on its own.
  * These wrappers give the differential harness (docs/amaranth-rewrite-plan.md §4.2) a real
  * top-level module to generate, lint and diff against the Amaranth port.
  *
  * Upstream sources:
  *   - ext/SpinalVoodoo/src/voodoo/bus/StreamWhile.scala
  *   - ext/SpinalVoodoo/src/voodoo/math/Fpxx.scala
  */

/** StreamWhileDiffWrap: the decrement-to-zero loop from ext/SpinalVoodoo/test/StreamWhileTest.scala.
  *
  * Keeps the exact shape the core uses (Rasterizer.SpanRasterizer): one trigger stream in, one
  * output stream out, plus the `running` flag StreamWhile returns alongside the stream.
  *
  * Ports (top level, no `io_` prefix — matches Rasterizer's style):
  *   start_valid / start_ready / start_payload  : UInt(8)
  *   output_valid / output_ready / output_payload : UInt(8)
  *   running                                    : Bool
  */
case class StreamWhileDiffWrap(width: Int = 8, maxIterations: Int = 16) extends Component {
  val start = slave Stream (UInt(width bits))
  val output = master Stream (UInt(width bits))
  val running = out Bool ()

  private val res = StreamWhile(
    start,
    stateType = HardType(UInt(width bits)),
    outputType = HardType(UInt(width bits)),
    maxIterations = maxIterations,
    init = (value: UInt) => value,
    step = (_: UInt, value: UInt, out: UInt) => {
      out := value
      val nextValue = value - 1
      val isLast = value === 1
      (nextValue, isLast)
    }
  )

  output << res.output
  running := res.running
}

/** FpxxDiffWrap: every combinational `Fpxx` operation the core relies on, on ports.
  *
  * `Fpxx` is a Bundle, not a Component. PciFifo is the only user (via `Fpxx2AFix`, with
  * `FpxxConfig.float32()`), so float32 is the default here.
  *
  * Input port `a` / `b` are the packed IEEE-ish encoding {sign, exp, mant} (MSB..LSB), exactly the
  * layout `Fpxx.asBits` produces, so the Amaranth side can drive a single 32-bit word.
  *
  * Ports:
  *   a, b                : Bits(1 + exp_size + mant_size) — packed Fpxx
  *   aSign/aExp/aMant    : unpacked view of `a` (checks the field order of the Bundle)
  *   aIsZero, aIsNan, aIsInf, aIsSubnormal : predicates
  *   aAbs, aNeg          : packed Fpxx results of `.abs()` and `unary_~`
  *   aFullMant           : `.full_mant()` (mant_size+1 bits, implicit leading 1)
  *   aFlagsNan/Inf/Sign  : the `Fpxx.Flags()` bundle produced by `.flags()`
  *   bIsZero, bIsNan     : same predicates on the second operand (two operands so the harness can
  *                         drive a pair without a second module)
  */
case class FpxxDiffWrap(fc: FpxxConfig = FpxxConfig.float32()) extends Component {
  val io = new Bundle {
    val a = in Bits (fc.full_size bits)
    val b = in Bits (fc.full_size bits)

    val aSign = out Bool ()
    val aExp = out UInt (fc.exp_size bits)
    val aMant = out UInt (fc.mant_size bits)

    val aIsZero = out Bool ()
    val aIsNan = out Bool ()
    val aIsInf = out Bool ()
    val aIsSubnormal = out Bool ()

    val aAbs = out Bits (fc.full_size bits)
    val aNeg = out Bits (fc.full_size bits)
    val aFullMant = out UInt (fc.mant_size + 1 bits)

    val aFlagsNan = out Bool ()
    val aFlagsInf = out Bool ()
    val aFlagsSign = out Bool ()

    val bIsZero = out Bool ()
    val bIsNan = out Bool ()
    val bIsInf = out Bool ()

    // Constants the Bundle can build: set_nan / set_inf / set_zero
    val constNan = out Bits (fc.full_size bits)
    val constInf = out Bits (fc.full_size bits)
    val constZero = out Bits (fc.full_size bits)
  }

  private val a = Fpxx(fc)
  a.assignFromBits(io.a)
  private val b = Fpxx(fc)
  b.assignFromBits(io.b)

  io.aSign := a.sign
  io.aExp := a.exp
  io.aMant := a.mant

  io.aIsZero := a.is_zero()
  io.aIsNan := a.is_nan()
  io.aIsInf := a.is_infinite()
  io.aIsSubnormal := a.is_subnormal()

  io.aAbs := a.abs().asBits
  io.aNeg := (~a).asBits
  io.aFullMant := a.full_mant()

  private val flags = a.flags()
  io.aFlagsNan := flags.nan
  io.aFlagsInf := flags.inf
  io.aFlagsSign := flags.sign

  io.bIsZero := b.is_zero()
  io.bIsNan := b.is_nan()
  io.bIsInf := b.is_infinite()

  private val nanConst = Fpxx(fc)
  nanConst.sign := False
  nanConst.set_nan()
  io.constNan := nanConst.asBits

  private val infConst = Fpxx(fc)
  infConst.sign := False
  infConst.set_inf()
  io.constInf := infConst.asBits

  private val zeroConst = Fpxx(fc)
  zeroConst.sign := False
  zeroConst.set_zero()
  io.constZero := zeroConst.asBits
}
