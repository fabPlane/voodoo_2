package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib.bus.amba4.axi.Axi4Config

import scala.collection.mutable

/** Long AXI bursts through Axi4ToGowinDdrNative against a behavioural Gowin
  * DDR3 native port (256-bit data, word address, BL8 = 8 addresses / command,
  * wr_data_mask active-high inhibit). On the board, 64-beat framebuffer
  * bursts landed in the wrong place; single beats were fine.
  */
class Axi4ToGowinDdrNativeTest extends AnyFunSuite {
  private val cfg = Axi4Config(
    addressWidth = 32, dataWidth = 32, idWidth = 4,
    useRegion = false, useLock = false, useCache = false, useQos = false, useProt = false
  )
  private lazy val compiled = SimConfig.withVerilator.compile(Axi4ToGowinDdrNative(cfg, cfg))

  test("64-beat write bursts then 64-beat read bursts round-trip at many addresses") {
    compiled.doSim("bursts", seed = 1) { dut =>
      dut.clockDomain.forkStimulus(10)
      val mem = mutable.HashMap[BigInt, BigInt]() // native word address (x8 per command) -> 256-bit line
      val rnd = new scala.util.Random(7)
      // --- DDR native model ---
      dut.io.ddr.cmdReady #= true
      dut.io.ddr.wrDataRdy #= true
      dut.io.ddr.rdDataValid #= false
      dut.io.ddr.rdDataEnd #= false
      dut.io.ddr.initCalibComplete #= true
      val pendingReads = mutable.Queue[(BigInt, Int)]()
      dut.clockDomain.onSamplings {
        dut.io.ddr.cmdReady #= rnd.nextInt(4) != 0
        dut.io.ddr.wrDataRdy #= rnd.nextInt(4) != 0
        if (dut.io.ddr.cmdEn.toBoolean) {
          val a = dut.io.ddr.addr.toBigInt
          assert((a & 7) == 0, s"unaligned native addr $a")
          if (dut.io.ddr.cmd.toInt == 0) {
            assert(dut.io.ddr.wrDataEn.toBoolean, "write cmd without data")
            val data = dut.io.ddr.wrData.toBigInt
            val mask = dut.io.ddr.wrDataMask.toBigInt
            var line = mem.getOrElse(a, BigInt(0))
            for (b <- 0 until 32 if !mask.testBit(b)) {
              val byte = (data >> (8 * b)) & 0xff
              line = (line & ~(BigInt(0xff) << (8 * b))) | (byte << (8 * b))
            }
            mem(a) = line
          } else {
            pendingReads.enqueue((a, 6 + rnd.nextInt(10)))
          }
        }
        dut.io.ddr.rdDataValid #= false
        if (pendingReads.nonEmpty) {
          val (a, d) = pendingReads.dequeue()
          if (d > 0) pendingReads.enqueue((a, d - 1)) // crude latency (keeps order for single outstanding)
          else {
            dut.io.ddr.rdData #= mem.getOrElse(a, BigInt(0))
            dut.io.ddr.rdDataValid #= true
            dut.io.ddr.rdDataEnd #= true
          }
        }
      }
      // --- AXI master on the fb port, tex idle ---
      val fb = dut.io.fb
      val tx = dut.io.tex
      for (s <- Seq(fb.aw.valid, fb.w.valid, fb.ar.valid, tx.aw.valid, tx.w.valid, tx.ar.valid)) s #= false
      fb.b.ready #= true
      fb.r.ready #= true
      tx.b.ready #= true
      tx.r.ready #= true
      dut.clockDomain.waitSampling(3)

      def word(addr: Long): Long = (addr * 2654435761L) & 0xffffffffL
      def writeBurst(addr: Long, beats: Int): Unit = {
        fb.aw.valid #= true
        fb.aw.addr #= addr
        fb.aw.len #= beats - 1
        fb.aw.size #= 2
        fb.aw.burst #= 1
        fb.aw.id #= 3
        dut.clockDomain.waitSamplingWhere(fb.aw.ready.toBoolean)
        fb.aw.valid #= false
        for (i <- 0 until beats) {
          fb.w.valid #= true
          fb.w.data #= word(addr + 4 * i)
          fb.w.strb #= 0xf
          fb.w.last #= i == beats - 1
          dut.clockDomain.waitSamplingWhere(fb.w.ready.toBoolean)
        }
        fb.w.valid #= false
        dut.clockDomain.waitSamplingWhere(fb.b.valid.toBoolean)
      }
      def readBurst(addr: Long, beats: Int): Seq[Long] = {
        fb.ar.valid #= true
        fb.ar.addr #= addr
        fb.ar.len #= beats - 1
        fb.ar.size #= 2
        fb.ar.burst #= 1
        fb.ar.id #= 5
        dut.clockDomain.waitSamplingWhere(fb.ar.ready.toBoolean)
        fb.ar.valid #= false
        val out = mutable.ArrayBuffer[Long]()
        while (out.size < beats) {
          dut.clockDomain.waitSampling()
          if (fb.r.valid.toBoolean) out += fb.r.data.toLong
        }
        out.toSeq
      }
      // 256-byte bursts covering 5 rows of a 1280-byte-stride 640x480 buffer,
      // plus bursts straddling the adapter's 16 KiB address split.
      val bases = (0 until 25).map(i => 0x100000L + i * 256L) ++
        Seq(0x3f00L, 0x4000L, 0x7f00L, 0x13f00L)
      for (b <- bases) writeBurst(b, 64)
      for (b <- bases) {
        val got = readBurst(b, 64)
        for (i <- 0 until 64) {
          val e = word(b + 4 * i)
          assert(got(i) == e, f"burst 0x$b%x beat $i: got 0x${got(i)}%08x exp 0x$e%08x")
        }
      }
    }
  }
}
