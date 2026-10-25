package voodoo

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib.bus.bmb._
import voodoo.frontend._

/** SetupEngine against a port of 86Box's vid_voodoo_reg.c vertex handling and
  * vid_voodoo_setup.c voodoo_triangle_setup: random strips and fans, setup
  * modes and culling. Each triangle's float register writes must match the
  * reference (vertices and start values exactly, gradients to float rounding:
  * the engine multiplies by a Newton reciprocal of the area where 86Box
  * divides).
  */
class SetupEngineTest extends AnyFunSuite {
  private def bits(f: Float): Long = java.lang.Float.floatToRawIntBits(f).toLong & 0xffffffffL
  private def flt(b: Long): Float = java.lang.Float.intBitsToFloat(b.toInt)

  // 86Box state.
  private class Ref {
    var mode = 0L
    val verts = Array.fill(4)(new Array[Float](14)) // X Y R G B A Z Wb W0 S0 T0 W1 S1 T1
    val ages = new Array[Int](3)
    var nextAge = 0
    var num = 0
    var pingpong = false
    // Expected writes per triangle: (address with tag) -> value; plus sign.
    val tris = scala.collection.mutable.ArrayBuffer[(Map[Int, Float], Boolean)]()

    def begin(): Unit = {
      for (i <- 0 until 3) verts(i) = verts(3).clone()
      nextAge = 0; ages(0) = nextAge; nextAge += 1
      num = 1; pingpong = false
    }
    def draw(): Unit = {
      if (nextAge < 3) {
        val n = nextAge; verts(n) = verts(3).clone(); ages(n) = nextAge; nextAge += 1
      } else {
        var n = 0
        if ((mode & (1 << 16)) == 0) {
          n = if (ages(0) < ages(1) && ages(0) < ages(2)) 0 else if (ages(1) < ages(0) && ages(1) < ages(2)) 1 else 2
        } else {
          n = if (ages(1) < ages(0) && ages(0) < ages(2)) 0
          else if (ages(2) < ages(0) && ages(0) < ages(1)) 0
          else if (ages(0) < ages(1) && ages(1) < ages(2)) 1
          else if (ages(2) < ages(1) && ages(1) < ages(0)) 1
          else 2
        }
        verts(n) = verts(3).clone(); ages(n) = nextAge; nextAge += 1
      }
      num += 1
      if (num == 3) { setup(); pingpong = !pingpong; num = 2 }
    }
    def setup(): Unit = {
      val v = verts
      val (va, vb, vc) =
        if (v(0)(1) < v(1)(1)) {
          if (v(1)(1) < v(2)(1)) (0, 1, 2) else if (v(0)(1) < v(2)(1)) (0, 2, 1) else (2, 0, 1)
        } else {
          if (v(1)(1) < v(2)(1)) { if (v(0)(1) < v(2)(1)) (1, 0, 2) else (1, 2, 0) } else (2, 1, 0)
        }
      var dxAB = v(0)(0) - v(1)(0); var dxBC = v(1)(0) - v(2)(0)
      var dyAB = v(0)(1) - v(1)(1); var dyBC = v(1)(1) - v(2)(1)
      var area = dxAB * dyBC - dxBC * dyAB
      if (area == 0f) return
      if ((mode & (1 << 17)) != 0) {
        var cullSign = (mode & (1 << 18)) != 0
        if ((mode & ((1 << 17) | (1 << 19))) == (1 << 17) && pingpong) cullSign = !cullSign
        val sign = area < 0f
        if (cullSign && sign) return
        if (!cullSign && !sign) return
      }
      dxAB = v(va)(0) - v(vb)(0); dxBC = v(vb)(0) - v(vc)(0)
      dyAB = v(va)(1) - v(vb)(1); dyBC = v(vb)(1) - v(vc)(1)
      area = dxAB * dyBC - dxBC * dyAB
      dxAB /= area; dxBC /= area; dyAB /= area; dyBC /= area
      val w = scala.collection.mutable.Map[Int, Float]()
      val vs = Seq(va, vb, vc)
      for ((s, i) <- vs.zipWithIndex) { w(0x488 + 8 * i) = v(s)(0); w(0x48c + 8 * i) = v(s)(1) }
      def param(k: Int, start: Int, tag: Int): Unit = {
        val a = v(va)(k); val b = v(vb)(k); val c = v(vc)(k)
        w(tag | start) = a
        w(tag | (start + 0x20)) = (a - b) * dyBC - (b - c) * dyAB
        w(tag | (start + 0x40)) = (b - c) * dxAB - (a - b) * dxBC
      }
      val m = mode
      def bit(n: Int) = (m & (1L << n)) != 0
      if (bit(0)) { param(2, 0xa0, 0x400); param(3, 0xa4, 0x400); param(4, 0xa8, 0x400) }
      if (bit(1)) param(5, 0xb0, 0x400)
      if (bit(2)) param(6, 0xac, 0x400)
      if (bit(3) && !bit(4)) param(7, 0xbc, if (bit(6)) 0 else 0x400)
      if (bit(4)) param(8, 0xbc, if (bit(6)) 0 else 0x400)
      if (bit(5)) { val t = if (bit(7)) 0 else 0x400; param(9, 0xb4, t); param(10, 0xb8, t) }
      if (bit(6)) param(11, 0xbc, 0xc00)
      if (bit(7)) { param(12, 0xb4, 0xc00); param(13, 0xb8, 0xc00) }
      tris += ((w.toMap, area < 0f))
    }
  }

  test("random strips and fans match 86Box's setup") {
    val p = RegisterBank.bmbParams(Config.voodoo1())
    SimConfig.withVerilator.compile(SetupEngine(p)).doSim { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.input.cmd.valid #= false
      dut.io.input.rsp.ready #= true
      dut.io.output.cmd.ready #= true
      dut.io.output.rsp.valid #= false
      // Downstream: record writes, answer each command one cycle later.
      val written = scala.collection.mutable.ArrayBuffer[(Int, Long)]()
      var owed = 0
      fork {
        while (true) {
          dut.clockDomain.waitSampling()
          dut.io.output.rsp.valid #= owed > 0
          dut.io.output.rsp.source #= 0
          dut.io.output.rsp.opcode #= 0
          dut.io.output.rsp.last #= true
          if (owed > 0 && dut.io.output.rsp.ready.toBoolean) owed -= 1
          if (dut.io.output.cmd.valid.toBoolean && dut.io.output.cmd.ready.toBoolean) {
            written += ((dut.io.output.cmd.address.toInt, dut.io.output.cmd.data.toLong))
            owed += 1
          }
        }
      }
      def write(addr: Int, data: Long): Unit = {
        dut.io.input.cmd.valid #= true
        dut.io.input.cmd.opcode #= 1
        dut.io.input.cmd.address #= addr
        dut.io.input.cmd.data #= data
        dut.io.input.cmd.mask #= 0xf
        dut.io.input.cmd.length #= 3
        dut.io.input.cmd.last #= true
        dut.io.input.cmd.source #= 0
        var c = 0
        do { dut.clockDomain.waitSampling(); c += 1; assert(c < 2000, "write never taken") }
        while (!(dut.io.input.cmd.valid.toBoolean && dut.io.input.cmd.ready.toBoolean))
        dut.io.input.cmd.valid #= false
      }
      val ref = new Ref
      val r = new scala.util.Random(5)
      val regs = Seq(0x264, 0x268, 0x270, 0x274, 0x278, 0x27c, 0x280, 0x284, 0x288, 0x28c, 0x290, 0x294, 0x298, 0x29c)
      def vertex(): Unit = {
        val vals = Array(
          (r.nextFloat() * 640).toFloat, (r.nextFloat() * 480).toFloat,
          r.nextFloat() * 255, r.nextFloat() * 255, r.nextFloat() * 255, r.nextFloat() * 255,
          r.nextFloat() * 65535, r.nextFloat(), r.nextFloat(), r.nextFloat() * 256, r.nextFloat() * 256,
          r.nextFloat(), r.nextFloat() * 256, r.nextFloat() * 256)
        if (r.nextInt(10) == 0) vals(1) = ref.verts(3)(1) // equal y
        if (r.nextInt(6) == 0) {
          // Packed colour.
          val c = Array.fill(4)(r.nextInt(256))
          write(0x26c, (c(3).toLong << 24) | (c(0) << 16) | (c(1) << 8) | c(2))
          ref.verts(3)(2) = c(0); ref.verts(3)(3) = c(1); ref.verts(3)(4) = c(2); ref.verts(3)(5) = c(3)
          for (k <- Seq(0, 1) ++ (6 until 14)) { write(regs(k), bits(vals(k))); ref.verts(3)(k) = vals(k) }
        } else {
          for (k <- 0 until 14) { write(regs(k), bits(vals(k))); ref.verts(3)(k) = vals(k) }
        }
      }
      var checkedTris = 0
      for (run <- 0 until 60) {
        val m = (r.nextInt(256).toLong) | (if (r.nextBoolean()) 1L << 16 else 0) |
          (if (r.nextInt(3) == 0) 1L << 17 else 0) | (if (r.nextBoolean()) 1L << 18 else 0) |
          (if (r.nextInt(4) == 0) 1L << 19 else 0)
        write(0x260, m); ref.mode = m
        vertex(); write(0x2a4, 0); ref.begin()
        for (_ <- 0 until 2 + r.nextInt(6)) { vertex(); write(0x2a0, 0); ref.draw() }
      }
      dut.clockDomain.waitSampling(500)
      // Split the engine's writes into triangles at each triangleCMD.
      val tris = scala.collection.mutable.ArrayBuffer[(Map[Int, Long], Boolean)]()
      var cur = Map[Int, Long]()
      for ((a, d) <- written) {
        if (a == 0x480) { tris += ((cur, (d >> 31) == 1)); cur = Map() } else cur += (a -> d)
      }
      assert(cur.isEmpty, "writes after the last triangleCMD")
      assert(tris.length == ref.tris.length, s"engine sent ${tris.length} triangles, 86Box ${ref.tris.length}")
      def close(got: Float, want: Float, scale: Float): Boolean =
        got == want || scala.math.abs(got - want) <= 2e-4f * scala.math.max(scala.math.abs(want), scale)
      for (((got, sign), i) <- tris.zipWithIndex) {
        val (want, wantSign) = ref.tris(i)
        assert(sign == wantSign, s"triangle $i sign")
        assert(got.keySet == want.keySet, s"triangle $i registers ${got.keySet.toSeq.sorted.map(_.toHexString)} want ${want.keySet.toSeq.sorted.map(_.toHexString)}")
        // Gradients scale with the parameter range over the triangle.
        for ((a, wv) <- want) {
          val gv = flt(got(a))
          val off = a & 0x3ff
          val isGrad = off >= 0xc0
          if (isGrad) {
            val startOff = if (off >= 0xe0) off - 0x40 else off - 0x20
            val range = scala.math.abs(want((a & 0xc00) | startOff)) + 1f
            assert(close(gv, wv, range * 0.01f), f"triangle $i reg ${a}%x = $gv want $wv")
          } else assert(gv == wv, f"triangle $i reg ${a}%x = $gv want $wv")
        }
        checkedTris += 1
      }
      println(s"checked $checkedTris triangles")
      assert(checkedTris > 50)
    }
  }
}
