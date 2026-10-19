package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._
import spinal.lib.bus.amba4.axi._
import spinal.lib.bus.amba4.axilite._

import scala.collection.mutable
import scala.util.Random

/** The board's BAR -> (Voodoo | raw DDR) split, as wired in TangMega138kPro:
  * AxiLite4RawSplit -> core AXI-Lite, and -> AxiLite4ToAxi4Single ->
  * Axi4TwoToOne (input 0 = the Voodoo's fb AXI port) -> memory.
  */
case class RawDdrWindowHarness() extends Component {
  val c = GowinGenSupport.config
  val fbConfig = CoreGowin.fbAxiConfig(c)
  val split = AxiLite4RawSplit(PcieTlpBarTarget.rawAddressWidth)
  val toAxi = AxiLite4ToAxi4Single(split.outConfig, fbConfig)
  val merge = Axi4TwoToOne(fbConfig)
  val io = new Bundle {
    val bar = slave(AxiLite4(split.inConfig))
    val core = master(AxiLite4(split.outConfig))
    val fb = slave(Axi4(fbConfig))
    val mem = master(Axi4(merge.outConfig))
  }
  split.io.input <> io.bar
  io.core <> split.io.core
  split.io.raw <> toAxi.io.input
  merge.io.inputs(1) << toAxi.io.output
  merge.io.inputs(0) << io.fb
  io.mem << merge.io.output
}

class RawDdrWindowTest extends AnyFunSuite {
  lazy val compiled = SimConfig.withVerilator.compile(RawDdrWindowHarness())

  /** Byte memory behind io.mem: any burst length, ID echoed, strobes honoured. */
  class MemModel(dut: RawDdrWindowHarness, rnd: Random) {
    val bytes = mutable.HashMap[Long, Int]()
    def word(a: Long): Long = (0 until 4).map(i => bytes.getOrElse(a + i, 0).toLong << (8 * i)).sum
    val m = dut.io.mem
    m.aw.ready #= false; m.w.ready #= false; m.b.valid #= false
    m.ar.ready #= false; m.r.valid #= false
    val cd = dut.clockDomain
    fork { // writes
      while (true) {
        m.aw.ready #= true
        cd.waitSamplingWhere(m.aw.valid.toBoolean)
        val addr = m.aw.addr.toLong; val len = m.aw.len.toInt; val id = m.aw.id.toInt
        m.aw.ready #= false
        for (beat <- 0 to len) {
          cd.waitSampling(rnd.nextInt(2))
          m.w.ready #= true
          cd.waitSamplingWhere(m.w.valid.toBoolean)
          m.w.ready #= false
          val d = m.w.data.toLong; val s = m.w.strb.toInt
          for (i <- 0 until 4 if ((s >> i) & 1) == 1) bytes(addr + beat * 4 + i) = ((d >> (8 * i)) & 0xff).toInt
          assert(m.w.last.toBoolean == (beat == len), s"w.last wrong at beat $beat of $len")
        }
        m.w.ready #= false
        m.b.valid #= true; m.b.id #= id; m.b.resp #= 0
        cd.waitSamplingWhere(m.b.ready.toBoolean)
        m.b.valid #= false
      }
    }
    fork { // reads
      while (true) {
        m.ar.ready #= true
        cd.waitSamplingWhere(m.ar.valid.toBoolean)
        val addr = m.ar.addr.toLong; val len = m.ar.len.toInt; val id = m.ar.id.toInt
        m.ar.ready #= false
        cd.waitSampling(rnd.nextInt(4))
        for (beat <- 0 to len) {
          m.r.valid #= true; m.r.id #= id; m.r.resp #= 0
          m.r.data #= word(addr + beat * 4); m.r.last #= beat == len
          cd.waitSamplingWhere(m.r.ready.toBoolean)
        }
        m.r.valid #= false
      }
    }
  }

  /** Voodoo-side AXI-Lite register file: records accesses, reads return addr ^ 0x5a5a0000. */
  class CoreModel(dut: RawDdrWindowHarness) {
    val writes = mutable.ArrayBuffer[(Long, Long)]()
    val reads = mutable.ArrayBuffer[Long]()
    val a = dut.io.core
    val cd = dut.clockDomain
    a.aw.ready #= false; a.w.ready #= false; a.b.valid #= false; a.ar.ready #= false; a.r.valid #= false
    fork {
      while (true) {
        a.aw.ready #= true
        cd.waitSamplingWhere(a.aw.valid.toBoolean)
        val addr = a.aw.addr.toLong
        a.aw.ready #= false
        a.w.ready #= true
        cd.waitSamplingWhere(a.w.valid.toBoolean)
        writes += addr -> a.w.data.toLong
        a.w.ready #= false
        a.b.valid #= true; a.b.resp #= 0
        cd.waitSamplingWhere(a.b.ready.toBoolean)
        a.b.valid #= false
      }
    }
    fork {
      while (true) {
        a.ar.ready #= true
        cd.waitSamplingWhere(a.ar.valid.toBoolean)
        val addr = a.ar.addr.toLong
        reads += addr
        a.ar.ready #= false
        a.r.valid #= true; a.r.resp #= 0; a.r.data #= (addr ^ 0x5a5a0000L)
        cd.waitSamplingWhere(a.r.ready.toBoolean)
        a.r.valid #= false
      }
    }
  }

  /** Host side: one transaction at a time, like PcieTlpBarTarget; AW and W
    * are raised in random order. */
  class BarDriver(dut: RawDdrWindowHarness, rnd: Random) {
    val b = dut.io.bar
    val cd = dut.clockDomain
    b.aw.valid #= false; b.w.valid #= false; b.ar.valid #= false; b.b.ready #= true; b.r.ready #= true
    def write(addr: Long, data: Long, strb: Int = 0xf): Unit = {
      b.aw.addr #= addr; b.aw.prot #= 0; b.w.data #= data; b.w.strb #= strb
      val awFirst = rnd.nextBoolean()
      if (awFirst) b.aw.valid #= true else b.w.valid #= true
      cd.waitSampling(rnd.nextInt(3))
      b.aw.valid #= true; b.w.valid #= true
      var awDone = false; var wDone = false; var bDone = false
      while (!bDone) {
        cd.waitSampling()
        if (b.aw.valid.toBoolean && b.aw.ready.toBoolean) { awDone = true; b.aw.valid #= false }
        if (b.w.valid.toBoolean && b.w.ready.toBoolean) { wDone = true; b.w.valid #= false }
        if (b.b.valid.toBoolean) bDone = true
      }
      assert(awDone && wDone)
    }
    def read(addr: Long): Long = {
      b.ar.addr #= addr; b.ar.prot #= 0; b.ar.valid #= true
      cd.waitSamplingWhere(b.ar.ready.toBoolean)
      b.ar.valid #= false
      cd.waitSamplingWhere(b.r.valid.toBoolean)
      b.r.data.toLong
    }
  }

  def run(seed: Int)(body: (RawDdrWindowHarness, BarDriver, MemModel, CoreModel, Random) => Unit): Unit =
    compiled.doSim(seed = seed) { dut =>
      val rnd = new Random(seed)
      dut.clockDomain.forkStimulus(10)
      dut.io.fb.aw.valid #= false; dut.io.fb.w.valid #= false; dut.io.fb.ar.valid #= false
      dut.io.fb.b.ready #= true; dut.io.fb.r.ready #= true
      val mem = new MemModel(dut, rnd)
      val core = new CoreModel(dut)
      val bar = new BarDriver(dut, rnd)
      dut.clockDomain.waitSampling(5)
      body(dut, bar, mem, core, rnd)
    }

  val raw = 1L << 24

  test("raw window writes land in memory and read back; the Voodoo sees none of it") {
    run(1) { (_, bar, mem, core, rnd) =>
      val ref = mutable.HashMap[Long, Long]()
      for (_ <- 0 until 200) {
        val a = rnd.nextInt(1 << 22).toLong & ~3L
        val d = rnd.nextInt().toLong & 0xffffffffL
        bar.write(raw | a, d); ref(a) = d
      }
      for ((a, d) <- ref) {
        assert(mem.word(a) == d, f"mem @$a%06x")
        assert(bar.read(raw | a) == d, f"readback @$a%06x")
      }
      assert(core.writes.isEmpty && core.reads.isEmpty)
    }
  }

  test("byte strobes on the raw window only touch their bytes") {
    run(2) { (_, bar, mem, _, _) =>
      bar.write(raw | 0x100, 0x11223344L)
      bar.write(raw | 0x100, 0xaabbccddL, strb = 0x5)
      assert(bar.read(raw | 0x100) == 0x11bb33ddL)
    }
  }

  test("windows 0-15 still go to the Voodoo with the same address") {
    run(3) { (_, bar, mem, core, rnd) =>
      for (i <- 0 until 50) {
        val a = (rnd.nextInt(1 << 24).toLong & ~3L)
        if (rnd.nextBoolean()) {
          bar.write(a, i); assert(core.writes.last == (a -> i.toLong))
        } else {
          assert(bar.read(a) == (a ^ 0x5a5a0000L)); assert(core.reads.last == a)
        }
      }
      assert(mem.bytes.isEmpty)
    }
  }

  test("raw accesses interleave with Voodoo fb bursts without mixing up responses") {
    run(4) { (dut, bar, mem, _, rnd) =>
      val fb = dut.io.fb
      val cd = dut.clockDomain
      var fbDone = false
      // Voodoo side: 16-beat bursts to 0x200000+, then read them back.
      val fbThread = fork {
        val bursts = (0 until 20).map(i => 0x200000L + i * 0x400)
        for (base <- bursts) {
          fb.aw.valid #= true; fb.aw.addr #= base; fb.aw.len #= 15; fb.aw.size #= 2; fb.aw.burst #= 1
          cd.waitSamplingWhere(fb.aw.ready.toBoolean); fb.aw.valid #= false
          for (beat <- 0 until 16) {
            fb.w.valid #= true; fb.w.data #= (base + beat) & 0xffffffffL; fb.w.strb #= 0xf; fb.w.last #= beat == 15
            cd.waitSamplingWhere(fb.w.ready.toBoolean)
          }
          fb.w.valid #= false
          cd.waitSamplingWhere(fb.b.valid.toBoolean)
        }
        for (base <- bursts) {
          fb.ar.valid #= true; fb.ar.addr #= base; fb.ar.len #= 15; fb.ar.size #= 2; fb.ar.burst #= 1
          cd.waitSamplingWhere(fb.ar.ready.toBoolean); fb.ar.valid #= false
          for (beat <- 0 until 16) {
            cd.waitSamplingWhere(fb.r.valid.toBoolean)
            assert(fb.r.data.toLong == ((base + beat) & 0xffffffffL), f"fb read @${base + beat * 4}%06x")
          }
        }
        fbDone = true
      }
      val ref = mutable.HashMap[Long, Long]()
      while (!fbDone) {
        val a = rnd.nextInt(1 << 20).toLong & ~3L
        if (ref.isEmpty || rnd.nextBoolean()) {
          val d = rnd.nextInt().toLong & 0xffffffffL
          bar.write(raw | a, d); ref(a) = d
        } else {
          val (ra, rd) = ref.toSeq(rnd.nextInt(ref.size))
          assert(bar.read(raw | ra) == rd, f"raw readback @$ra%06x")
        }
      }
      fbThread.join()
    }
  }
}
