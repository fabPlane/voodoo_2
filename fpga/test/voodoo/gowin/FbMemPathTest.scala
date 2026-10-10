package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.bmb._
import voodoo.Core
import voodoo.artix.BmbToAxi4Mem
import spinal.lib.bus.amba4.axi.Axi4CC

import scala.collection.mutable

/** The board's framebuffer memory path minus the clock crossing:
  * BMB (shared fb params) -> pipelined -> BmbToAxi4Mem -> staged AXI ->
  * Axi4ToGowinDdrNative -> behavioural DDR3 native port.
  * On hardware a full-screen clear only landed in alternate 256-byte chunks.
  */
case class FbMemPathHarness(withArbiter: Boolean = false, withCc: Boolean = false) extends Component {
  val c = GowinGenSupport.config
  val p = Core.fbMemSharedBmbParams(c)
  val pin = Core.fbMemBmbParams(c)
  val io = new Bundle {
    val bmb = slave(Bmb(if (withArbiter) pin else p))
    val rd = withArbiter generate slave(Bmb(pin))
    val ddr = master(GowinDdrNative())
  }
  val toAxi = BmbToAxi4Mem(p, CoreGowin.axiBurstLog2)
  private def cut(b: Bmb): Bmb = b.pipelined(cmdValid = true, cmdReady = true, rspValid = true)
  if (withArbiter) {
    // Same 5-way round-robin arbiter as CoreGowin: 1 = colour write, 3 = colour/scanout read.
    val arb = BmbArbiter(Seq.fill(5)(pin), p, lowerFirstPriority = false)
    for (i <- Seq(0, 2, 4)) { arb.io.inputs(i).cmd.valid := False; arb.io.inputs(i).cmd.payload.assignDontCare(); arb.io.inputs(i).rsp.ready := True }
    arb.io.inputs(1) <> cut(io.bmb)
    arb.io.inputs(3) <> cut(io.rd)
    toAxi.io.input <> cut(arb.io.output)
  } else {
    toAxi.io.input <> cut(io.bmb)
  }
  // Optional board-style clock crossing: this component's clock is the core
  // side, ddrClk drives the adapter (as in TangMega138kPro).
  val ddrClk = if (withCc) in Bool () else null
  val ddrRst = if (withCc) in Bool () else null
  val ddrCd = if (withCc) ClockDomain(ddrClk, ddrRst, config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH))
              else ClockDomain.current
  val ad = ddrCd(Axi4ToGowinDdrNative(CoreGowin.fbAxiConfig(c), CoreGowin.texAxiConfig(c)))
  val axiIntoAdapter = if (withCc) {
    val cc = Axi4CC(CoreGowin.fbAxiConfig(c), ClockDomain.current, ddrCd,
      arFifoSize = 4, awFifoSize = 4, rFifoSize = 64, wFifoSize = 64, bFifoSize = 4)
    cc.io.input << toAxi.io.output
    cc.io.output
  } else toAxi.io.output
  ddrCd(ad.io.fb << axiIntoAdapter.pipelined(StreamPipe.FULL, StreamPipe.FULL, StreamPipe.NONE, StreamPipe.FULL, StreamPipe.FULL))
  val texIdle = ddrCd(Axi4(CoreGowin.texAxiConfig(c)))
  ddrCd(texIdle.setIdle())
  ddrCd(ad.io.tex << texIdle)
  io.ddr <> ad.io.ddr
}

class FbMemPathTest extends AnyFunSuite {
  private lazy val compiled = SimConfig.withVerilator.compile(FbMemPathHarness())

  test("1 KiB BMB write bursts (fb write-buffer lines) land where they should") {
    compiled.doSim("fbpath", seed = 3) { dut =>
      dut.clockDomain.forkStimulus(10)
      val rnd = new scala.util.Random(11)
      val mem = mutable.HashMap[BigInt, BigInt]()
      val ddr = dut.io.ddr
      ddr.cmdReady #= true
      ddr.wrDataRdy #= true
      ddr.rdDataValid #= false
      ddr.rdDataEnd #= false
      ddr.initCalibComplete #= true
      val reads = mutable.Queue[(BigInt, Int)]()
      dut.clockDomain.onSamplings {
        ddr.cmdReady #= rnd.nextInt(4) != 0
        ddr.wrDataRdy #= rnd.nextInt(4) != 0
        if (ddr.cmdEn.toBoolean) {
          val a = ddr.addr.toBigInt
          if (ddr.cmd.toInt == 0) {
            val data = ddr.wrData.toBigInt; val mask = ddr.wrDataMask.toBigInt
            var line = mem.getOrElse(a, BigInt(0))
            for (b <- 0 until 32 if !mask.testBit(b)) {
              line = (line & ~(BigInt(0xff) << (8 * b))) | (((data >> (8 * b)) & 0xff) << (8 * b))
            }
            mem(a) = line
          } else reads.enqueue((a, 8))
        }
        ddr.rdDataValid #= false
        if (reads.nonEmpty) {
          val (a, d) = reads.dequeue()
          if (d > 0) reads.enqueue((a, d - 1))
          else { ddr.rdData #= mem.getOrElse(a, BigInt(0)); ddr.rdDataValid #= true; ddr.rdDataEnd #= true }
        }
      }
      val b = dut.io.bmb
      b.cmd.valid #= false
      b.rsp.ready #= true
      dut.clockDomain.waitSampling(3)
      def word(a: Long): Long = (a * 2654435761L + 12345) & 0xffffffffL

      def writeLine(addr: Long, words: Int): Unit = {
        for (i <- 0 until words) {
          b.cmd.valid #= true
          b.cmd.opcode #= Bmb.Cmd.Opcode.WRITE
          b.cmd.address #= addr
          b.cmd.length #= words * 4 - 1
          b.cmd.data #= word(addr + 4 * i)
          b.cmd.mask #= 0xf
          b.cmd.last #= i == words - 1
          b.cmd.source #= 0
          if (b.cmd.context.getWidth > 0) b.cmd.context #= 0
          dut.clockDomain.waitSamplingWhere(b.cmd.ready.toBoolean)
        }
        b.cmd.valid #= false
        dut.clockDomain.waitSamplingWhere(b.rsp.valid.toBoolean)
      }
      def readLine(addr: Long, words: Int): Seq[Long] = {
        b.cmd.valid #= true
        b.cmd.opcode #= Bmb.Cmd.Opcode.READ
        b.cmd.address #= addr
        b.cmd.length #= words * 4 - 1
        b.cmd.last #= true
        b.cmd.source #= 0
        if (b.cmd.context.getWidth > 0) b.cmd.context #= 0
        dut.clockDomain.waitSamplingWhere(b.cmd.ready.toBoolean)
        b.cmd.valid #= false
        val out = mutable.ArrayBuffer[Long]()
        while (out.size < words) {
          dut.clockDomain.waitSampling()
          if (b.rsp.valid.toBoolean) out += b.rsp.data.toLong
        }
        out.toSeq
      }
      // Five 1 KiB lines = one 640x480 RGB565 row pair region, plus misaligned starts.
      val lines = (0 until 5).map(i => (0x10000L + i * 1024L, 256)) ++ Seq((0x20100L, 256), (0x30040L, 64))
      for ((a, n) <- lines) writeLine(a, n)
      for ((a, n) <- lines) {
        val got = readLine(a, n)
        val bad = (0 until n).filter(i => got(i) != word(a + 4 * i))
        assert(bad.isEmpty, f"line 0x$a%x: ${bad.size} of $n words wrong, first at word ${bad.headOption.getOrElse(-1)}: " +
          bad.take(4).map(i => f"[$i] got 0x${got(i)}%08x exp 0x${word(a + 4 * i)}%08x").mkString(" "))
      }
    }
  }
  private lazy val compiledArb = SimConfig.withVerilator.compile(FbMemPathHarness(withArbiter = true))

  test("writes stay in place while scanout-style reads run concurrently through the arbiter") {
    compiledArb.doSim("fbpath-arb", seed = 5) { dut =>
      dut.clockDomain.forkStimulus(10)
      val rnd = new scala.util.Random(13)
      val mem = mutable.HashMap[BigInt, BigInt]()
      val ddr = dut.io.ddr
      ddr.cmdReady #= true; ddr.wrDataRdy #= true; ddr.rdDataValid #= false; ddr.rdDataEnd #= false
      ddr.initCalibComplete #= true
      val reads = mutable.Queue[(BigInt, Int)]()
      dut.clockDomain.onSamplings {
        ddr.cmdReady #= rnd.nextInt(4) != 0
        ddr.wrDataRdy #= rnd.nextInt(4) != 0
        if (ddr.cmdEn.toBoolean) {
          val a = ddr.addr.toBigInt
          if (ddr.cmd.toInt == 0) {
            val data = ddr.wrData.toBigInt; val mask = ddr.wrDataMask.toBigInt
            var line = mem.getOrElse(a, BigInt(0))
            for (b <- 0 until 32 if !mask.testBit(b))
              line = (line & ~(BigInt(0xff) << (8 * b))) | (((data >> (8 * b)) & 0xff) << (8 * b))
            mem(a) = line
          } else reads.enqueue((a, 8))
        }
        ddr.rdDataValid #= false
        if (reads.nonEmpty) {
          val (a, d) = reads.dequeue()
          if (d > 0) reads.enqueue((a, d - 1))
          else { ddr.rdData #= mem.getOrElse(a, BigInt(0)); ddr.rdDataValid #= true; ddr.rdDataEnd #= true }
        }
      }
      val w = dut.io.bmb; val r = dut.io.rd
      w.cmd.valid #= false; w.rsp.ready #= true; r.cmd.valid #= false; r.rsp.ready #= true
      dut.clockDomain.waitSampling(3)
      def word(a: Long): Long = (a * 2654435761L + 999) & 0xffffffffL
      @volatile var stop = false
      // scanout-like reader: 64-word line reads, random source
      val reader = fork {
        var a = 0x80000L
        while (!stop) {
          r.cmd.valid #= true; r.cmd.opcode #= Bmb.Cmd.Opcode.READ; r.cmd.address #= a
          r.cmd.length #= 255; r.cmd.last #= true; r.cmd.source #= rnd.nextInt(4)
          dut.clockDomain.waitSamplingWhere(r.cmd.ready.toBoolean)
          r.cmd.valid #= false
          var n = 0
          while (n < 64) { dut.clockDomain.waitSampling(); if (r.rsp.valid.toBoolean) n += 1 }
          a += 256
        }
      }
      def writeLine(addr: Long, words: Int, src: Int): Unit = {
        for (i <- 0 until words) {
          w.cmd.valid #= true; w.cmd.opcode #= Bmb.Cmd.Opcode.WRITE; w.cmd.address #= addr
          w.cmd.length #= words * 4 - 1; w.cmd.data #= word(addr + 4 * i); w.cmd.mask #= 0xf
          w.cmd.last #= i == words - 1; w.cmd.source #= src
          dut.clockDomain.waitSamplingWhere(w.cmd.ready.toBoolean)
        }
        w.cmd.valid #= false
        dut.clockDomain.waitSamplingWhere(w.rsp.valid.toBoolean)
      }
      val lines = (0 until 10).map(i => 0x10000L + i * 1024L)
      for ((a, i) <- lines.zipWithIndex) writeLine(a, 256, i % 4)
      stop = true
      reader.join()
      // check memory model directly (native line = 8 words = 32 B, native addr = byte/4)
      var bad = 0; var first = ""
      for (a <- lines; i <- 0 until 256) {
        val byteAddr = a + 4 * i
        val line = mem.getOrElse(BigInt(byteAddr / 32 * 8), BigInt(0))
        val got = ((line >> (32 * ((byteAddr % 32) / 4).toInt)) & 0xffffffffL).toLong
        if (got != word(byteAddr)) { if (bad == 0) first = f"0x$byteAddr%x got 0x$got%08x exp 0x${word(byteAddr)}%08x"; bad += 1 }
      }
      assert(bad == 0, s"$bad of ${lines.size * 256} words wrong, first $first")
    }
  }
  private lazy val compiledCc = SimConfig.withVerilator.compile(FbMemPathHarness(withCc = true))

  test("1 KiB write bursts survive the board's Axi4CC clock crossing (35 ns core, 10 ns DDR)") {
    compiledCc.doSim("fbpath-cc", seed = 9) { dut =>
      dut.clockDomain.forkStimulus(35)
      val ddrCd = ClockDomain(dut.ddrClk, dut.ddrRst, config = ClockDomainConfig(resetKind = SYNC, resetActiveLevel = HIGH))
      ddrCd.forkStimulus(10)
      val rnd = new scala.util.Random(17)
      val mem = mutable.HashMap[BigInt, BigInt]()
      val ddr = dut.io.ddr
      ddr.cmdReady #= true; ddr.wrDataRdy #= true; ddr.rdDataValid #= false; ddr.rdDataEnd #= false
      ddr.initCalibComplete #= true
      val reads = mutable.Queue[(BigInt, Int)]()
      ddrCd.onSamplings {
        ddr.cmdReady #= rnd.nextInt(4) != 0
        ddr.wrDataRdy #= rnd.nextInt(4) != 0
        if (ddr.cmdEn.toBoolean) {
          val a = ddr.addr.toBigInt
          if (ddr.cmd.toInt == 0) {
            val data = ddr.wrData.toBigInt; val mask = ddr.wrDataMask.toBigInt
            var line = mem.getOrElse(a, BigInt(0))
            for (b <- 0 until 32 if !mask.testBit(b))
              line = (line & ~(BigInt(0xff) << (8 * b))) | (((data >> (8 * b)) & 0xff) << (8 * b))
            mem(a) = line
          } else reads.enqueue((a, 8))
        }
        ddr.rdDataValid #= false
        if (reads.nonEmpty) {
          val (a, d) = reads.dequeue()
          if (d > 0) reads.enqueue((a, d - 1))
          else { ddr.rdData #= mem.getOrElse(a, BigInt(0)); ddr.rdDataValid #= true; ddr.rdDataEnd #= true }
        }
      }
      val b = dut.io.bmb
      b.cmd.valid #= false; b.rsp.ready #= true
      dut.clockDomain.waitSampling(20)
      def word(a: Long): Long = (a * 2654435761L + 4242) & 0xffffffffL
      def writeLine(addr: Long, words: Int): Unit = {
        for (i <- 0 until words) {
          b.cmd.valid #= true; b.cmd.opcode #= Bmb.Cmd.Opcode.WRITE; b.cmd.address #= addr
          b.cmd.length #= words * 4 - 1; b.cmd.data #= word(addr + 4 * i); b.cmd.mask #= 0xf
          b.cmd.last #= i == words - 1; b.cmd.source #= 0
          dut.clockDomain.waitSamplingWhere(b.cmd.ready.toBoolean)
        }
        b.cmd.valid #= false
        dut.clockDomain.waitSamplingWhere(b.rsp.valid.toBoolean)
      }
      val lines = (0 until 10).map(i => 0x10000L + i * 1024L)
      for (a <- lines) writeLine(a, 256)
      dut.clockDomain.waitSampling(200)
      var bad = 0; var first = ""; val badChunks = mutable.SortedSet[Long]()
      for (a <- lines; i <- 0 until 256) {
        val byteAddr = a + 4 * i
        val line = mem.getOrElse(BigInt(byteAddr / 32 * 8), BigInt(0))
        val got = ((line >> (32 * ((byteAddr % 32) / 4).toInt)) & 0xffffffffL).toLong
        if (got != word(byteAddr)) { if (bad == 0) first = f"0x$byteAddr%x got 0x$got%08x"; bad += 1; badChunks += byteAddr / 256 * 256 }
      }
      assert(bad == 0, s"$bad wrong words, first $first, bad 256B chunks: ${badChunks.take(8).map(x => f"0x$x%x").mkString(",")}")
    }
  }
  test("write-buffer runs of any length at any word offset (within a 4 KiB page) land in place") {
    compiled.doSim("fbpath-unaligned", seed = 31) { dut =>
      dut.clockDomain.forkStimulus(10)
      val rnd = new scala.util.Random(37)
      val mem = mutable.HashMap[BigInt, BigInt]()
      val ddr = dut.io.ddr
      ddr.cmdReady #= true; ddr.wrDataRdy #= true; ddr.rdDataValid #= false; ddr.rdDataEnd #= false
      ddr.initCalibComplete #= true
      dut.clockDomain.onSamplings {
        ddr.cmdReady #= rnd.nextInt(4) != 0
        ddr.wrDataRdy #= rnd.nextInt(4) != 0
        if (ddr.cmdEn.toBoolean && ddr.cmd.toInt == 0) {
          val a = ddr.addr.toBigInt; val data = ddr.wrData.toBigInt; val mask = ddr.wrDataMask.toBigInt
          var line = mem.getOrElse(a, BigInt(0))
          for (b <- 0 until 32 if !mask.testBit(b))
            line = (line & ~(BigInt(0xff) << (8 * b))) | (((data >> (8 * b)) & 0xff) << (8 * b))
          mem(a) = line
        }
      }
      val b = dut.io.bmb
      b.cmd.valid #= false; b.rsp.ready #= true
      dut.clockDomain.waitSampling(3)
      def word(a: Long): Long = (a * 2654435761L + 5) & 0xffffffffL
      val expect = mutable.HashMap[Long, Long]()
      var base = 0x40000L
      val runs = mutable.ArrayBuffer[(Long, Int)]()
      for (k <- 0 until 60) {
        val start = base + 4 * rnd.nextInt(64)        // any word offset
        // 1..256 words (<= 1 KiB line), never crossing a 4 KiB page (BMB rule,
        // which the write buffer now enforces).
        val words = scala.math.min(1 + rnd.nextInt(256), ((4096 - (start % 4096)) / 4).toInt)
        runs += ((start, words)); base = start + 4 * words + 4096
      }
      for ((a, n) <- runs) {
        for (i <- 0 until n) {
          b.cmd.valid #= true; b.cmd.opcode #= Bmb.Cmd.Opcode.WRITE; b.cmd.address #= a
          b.cmd.length #= n * 4 - 1; b.cmd.data #= word(a + 4 * i); b.cmd.mask #= 0xf
          b.cmd.last #= i == n - 1; b.cmd.source #= 1
          dut.clockDomain.waitSamplingWhere(b.cmd.ready.toBoolean)
          expect(a + 4 * i) = word(a + 4 * i)
        }
        b.cmd.valid #= false
        dut.clockDomain.waitSamplingWhere(b.rsp.valid.toBoolean)
      }
      var bad = 0; var first = ""
      for ((ba, e) <- expect) {
        val line = mem.getOrElse(BigInt(ba / 32 * 8), BigInt(0))
        val got = ((line >> (32 * ((ba % 32) / 4).toInt)) & 0xffffffffL).toLong
        if (got != e) { if (bad == 0) first = f"0x$ba%x got 0x$got%08x exp 0x$e%08x"; bad += 1 }
      }
      for ((ra, rn) <- runs) {
        val badIdx = (0 until rn).filter { i =>
          val ba = ra + 4 * i
          val line = mem.getOrElse(BigInt(ba / 32 * 8), BigInt(0))
          ((line >> (32 * ((ba % 32) / 4).toInt)) & 0xffffffffL).toLong != word(ba)
        }
        if (badIdx.nonEmpty) println(f"RUN start=0x$ra%x off=${(ra % 256) / 4} words=$rn bad=${badIdx.size} idx ${badIdx.head}..${badIdx.last}")
      }
      assert(bad == 0, s"$bad of ${expect.size} words wrong, first $first; runs ${runs.take(5)}")
    }
  }
}
