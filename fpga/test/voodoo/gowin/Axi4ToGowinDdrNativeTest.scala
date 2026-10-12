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
      val pendingReads = mutable.Queue[(BigInt, Long)]() // in order: (addr, due cycle)
      var cycle = 0L
      dut.clockDomain.onSamplings {
        cycle += 1
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
            val due = cycle + 6 + rnd.nextInt(10)
            pendingReads.enqueue((a, pendingReads.lastOption.map(_._2 max due).getOrElse(due)))
          }
        }
        dut.io.ddr.rdDataValid #= false
        if (pendingReads.nonEmpty && pendingReads.head._2 <= cycle) {
          val (a, _) = pendingReads.dequeue()
          dut.io.ddr.rdData #= mem.getOrElse(a, BigInt(0))
          dut.io.ddr.rdDataValid #= true
          dut.io.ddr.rdDataEnd #= true
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

  test("both ports keep several reads in flight; every word comes back to its port in order") {
    compiled.doSim("pipelined", seed = 2) { dut =>
      dut.clockDomain.forkStimulus(10)
      val mem = mutable.HashMap[BigInt, BigInt]()
      val rnd = new scala.util.Random(11)
      dut.io.ddr.cmdReady #= true
      dut.io.ddr.wrDataRdy #= true
      dut.io.ddr.rdDataValid #= false
      dut.io.ddr.rdDataEnd #= false
      dut.io.ddr.initCalibComplete #= true
      val pendingReads = mutable.Queue[(BigInt, Long)]()
      var cycle = 0L
      var maxInFlight = 0
      dut.clockDomain.onSamplings {
        cycle += 1
        dut.io.ddr.cmdReady #= rnd.nextInt(5) != 0
        dut.io.ddr.wrDataRdy #= rnd.nextInt(5) != 0
        if (dut.io.ddr.cmdEn.toBoolean) {
          assert(dut.io.ddr.cmdReady.toBoolean, "command while not ready")
          val a = dut.io.ddr.addr.toBigInt
          assert((a & 7) == 0, s"unaligned native addr $a")
          if (dut.io.ddr.cmd.toInt == 0) {
            assert(dut.io.ddr.wrDataEn.toBoolean && dut.io.ddr.wrDataRdy.toBoolean, "write cmd without data")
            val data = dut.io.ddr.wrData.toBigInt
            val mask = dut.io.ddr.wrDataMask.toBigInt
            var line = mem.getOrElse(a, BigInt(0))
            for (b <- 0 until 32 if !mask.testBit(b)) {
              val byte = (data >> (8 * b)) & 0xff
              line = (line & ~(BigInt(0xff) << (8 * b))) | (byte << (8 * b))
            }
            mem(a) = line
          } else {
            val due = cycle + 10 + rnd.nextInt(20)
            pendingReads.enqueue((a, pendingReads.lastOption.map(_._2 max due).getOrElse(due)))
            maxInFlight = maxInFlight max pendingReads.size
          }
        }
        dut.io.ddr.rdDataValid #= false
        if (pendingReads.nonEmpty && pendingReads.head._2 <= cycle) {
          val (a, _) = pendingReads.dequeue()
          dut.io.ddr.rdData #= mem.getOrElse(a, BigInt(0))
          dut.io.ddr.rdDataValid #= true
          dut.io.ddr.rdDataEnd #= true
        }
      }

      def word(port: Int, addr: Long, gen: Int): Long = ((addr * 2654435761L) ^ (port * 0x5a5a1234L) ^ gen) & 0xffffffffL
      // Per port: write a region, then read it back with many reads in
      // flight (ARs issued without waiting for R), random R backpressure.
      def runPort(port: Int, axi: spinal.lib.bus.amba4.axi.Axi4, base: Long, gen: Int): Unit = {
        val bursts = (0 until 40).map { i =>
          val beats = Seq(1, 2, 3, 8, 8, 8, 16, 64)(rnd.nextInt(8))
          val off = if (beats >= 8) 32 * rnd.nextInt(64) else 4 * rnd.nextInt(512 - beats)
          (base + off, beats)
        }
        // Writes, one burst at a time.
        for ((a, beats) <- bursts) {
          axi.aw.valid #= true
          axi.aw.addr #= a
          axi.aw.len #= beats - 1
          axi.aw.size #= 2
          axi.aw.burst #= 1
          axi.aw.id #= port + 2
          dut.clockDomain.waitSamplingWhere(axi.aw.ready.toBoolean)
          axi.aw.valid #= false
          for (i <- 0 until beats) {
            axi.w.valid #= true
            axi.w.data #= word(port, a + 4 * i, gen)
            axi.w.strb #= 0xf
            axi.w.last #= i == beats - 1
            dut.clockDomain.waitSamplingWhere(axi.w.ready.toBoolean)
          }
          axi.w.valid #= false
          dut.clockDomain.waitSamplingWhere(axi.b.valid.toBoolean)
        }
        // Expected memory image of this port's region after the writes.
        val expect = mutable.HashMap[Long, Long]()
        for ((a, beats) <- bursts; i <- 0 until beats) expect(a + 4 * i) = word(port, a + 4 * i, gen)
        val reads = bursts.reverse
        val got = mutable.Queue[(Long, Int)]() // (data, id) in arrival order
        val collector = fork {
          var n = reads.map(_._2).sum
          while (n > 0) {
            axi.r.ready #= rnd.nextInt(3) != 0
            dut.clockDomain.waitSampling()
            if (axi.r.valid.toBoolean && axi.r.ready.toBoolean) {
              got.enqueue((axi.r.data.toLong, axi.r.id.toInt))
              n -= 1
            }
          }
          axi.r.ready #= false
        }
        for ((a, beats) <- reads) {
          axi.ar.valid #= true
          axi.ar.addr #= a
          axi.ar.len #= beats - 1
          axi.ar.size #= 2
          axi.ar.burst #= 1
          axi.ar.id #= port + 2
          dut.clockDomain.waitSamplingWhere(axi.ar.ready.toBoolean)
        }
        axi.ar.valid #= false
        collector.join()
        val exp = for ((a, beats) <- reads; i <- 0 until beats) yield expect(a + 4 * i)
        val data = got.toSeq
        assert(data.size == exp.size)
        for (((d, id), (e, k)) <- data.zip(exp.zipWithIndex)) {
          assert(id == port + 2, s"port $port word $k: id $id")
          assert(d == e, f"port $port word $k: got 0x$d%08x exp 0x$e%08x")
        }
      }

      val fb = dut.io.fb
      val tx = dut.io.tex
      for (s <- Seq(fb.aw.valid, fb.w.valid, fb.ar.valid, tx.aw.valid, tx.w.valid, tx.ar.valid)) s #= false
      for (s <- Seq(fb.b.ready, tx.b.ready)) s #= true
      for (s <- Seq(fb.r.ready, tx.r.ready)) s #= false
      dut.clockDomain.waitSampling(3)
      for (gen <- 0 until 3) {
        val a = fork(runPort(0, fb, 0x200000L, gen))
        val b = fork(runPort(1, tx, 0x400000L, gen))
        a.join()
        b.join()
      }
      assert(maxInFlight >= 4, s"reads were not pipelined (max $maxInFlight in flight)")
    }
  }
}
