package voodoo

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import voodoo.math._

/** SetupFp units against Java float arithmetic (round to nearest even), with
  * denormals flushed on both sides.
  */
class SetupFpTest extends AnyFunSuite {
  private def bits(f: Float): Long = java.lang.Float.floatToRawIntBits(f).toLong & 0xffffffffL
  private def flt(b: Long): Float = java.lang.Float.intBitsToFloat(b.toInt)
  private def ftz(f: Float): Float =
    if (f != 0f && scala.math.abs(f) < java.lang.Float.MIN_NORMAL) (if (f < 0) -0f else 0f) else f

  private def randFloat(r: scala.util.Random): Float = {
    // Mostly the setup engine's range: screen coordinates, colours, 1/w, s/w.
    val e = r.nextInt(60) - 30
    val m = 1.0 + r.nextDouble()
    val v = (m * scala.math.pow(2, e)).toFloat
    r.nextInt(8) match {
      case 0 => 0f
      case 1 => -v
      case _ => if (r.nextBoolean()) v else -v
    }
  }

  private def same(got: Long, want: Float): Boolean = {
    val w = ftz(want)
    if (w == 0f) flt(got) == 0f else got == bits(w)
  }

  test("multiply matches float") {
    SimConfig.withVerilator.compile(SetupFpMul(UInt(8 bits))).doSim { dut =>
      dut.clockDomain.forkStimulus(10)
      val r = new scala.util.Random(1)
      val n = 4000
      val in = Array.fill(n)((randFloat(r), randFloat(r)))
      var bad = 0
      var got = 0
      fork {
        while (got < n) {
          dut.clockDomain.waitSampling()
          if (dut.io.outValid.toBoolean) {
            val (a, b) = in(got)
            val res = dut.io.result.toLong
            if (!same(res, ftz(a) * ftz(b))) {
              if (bad < 10) println(f"mul $a * $b = ${flt(res)} want ${ftz(a) * ftz(b)}")
              bad += 1
            }
            got += 1
          }
        }
      }
      for ((a, b) <- in) {
        dut.io.valid #= true
        dut.io.a #= bits(a); dut.io.b #= bits(b); dut.io.tag #= 0
        dut.clockDomain.waitSampling()
      }
      dut.io.valid #= false
      dut.clockDomain.waitSampling(10)
      assert(got == n && bad == 0, s"$bad of $n wrong")
    }
  }

  test("add / subtract match float") {
    SimConfig.withVerilator.compile(SetupFpAdd(UInt(8 bits))).doSim { dut =>
      dut.clockDomain.forkStimulus(10)
      val r = new scala.util.Random(2)
      val n = 6000
      val in = Array.fill(n) {
        val a = randFloat(r)
        // Close operands too (cancellation).
        val b = if (a != 0f && r.nextInt(4) == 0) flt(bits(a) + r.nextInt(64) - 32) else randFloat(r)
        (a, b, r.nextBoolean())
      }
      var bad = 0
      var got = 0
      fork {
        while (got < n) {
          dut.clockDomain.waitSampling()
          if (dut.io.outValid.toBoolean) {
            val (a, b, neg) = in(got)
            val want = if (neg) ftz(a) - ftz(b) else ftz(a) + ftz(b)
            val res = dut.io.result.toLong
            if (!same(res, want)) {
              if (bad < 10) println(f"add $a ${if (neg) "-" else "+"} $b = ${flt(res)} want $want")
              bad += 1
            }
            got += 1
          }
        }
      }
      for ((a, b, neg) <- in) {
        dut.io.valid #= true
        dut.io.a #= bits(a); dut.io.b #= bits(b); dut.io.negB #= neg; dut.io.tag #= 0
        dut.clockDomain.waitSampling()
      }
      dut.io.valid #= false
      dut.clockDomain.waitSampling(10)
      assert(got == n && bad == 0, s"$bad of $n wrong")
    }
  }

  test("reciprocal seed is within 2^-7") {
    case class Seed() extends Component {
      val io = new Bundle { val a = in Bits (32 bits); val s = out Bits (32 bits) }
      io.s := SetupFpRecip.seed(io.a)
    }
    SimConfig.withVerilator.compile(Seed()).doSim { dut =>
      val r = new scala.util.Random(3)
      for (_ <- 0 until 2000) {
        var a = randFloat(r); while (a == 0f) a = randFloat(r)
        dut.io.a #= bits(a)
        sleep(1)
        val s = flt(dut.io.s.toLong)
        val err = scala.math.abs(s * a - 1.0)
        assert(err < 1.0 / 128, s"seed($a) = $s, error $err")
      }
    }
  }
}
