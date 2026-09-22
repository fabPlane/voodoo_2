package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._
import spinal.lib._

class DdrTracePlayerDut extends Component {
  val player = DdrTracePlayer(addressWidth = 24)
  val io = player.io
  val ram = Mem(Bits(64 bits), 64)
  val pending = Reg(Bool()) init (False)
  val latched = Reg(Bits(64 bits)) init (0)
  player.io.memCmd.ready := !pending
  when(player.io.memCmd.fire) {
    pending := True
    latched := ram.readAsync(player.io.memCmd.payload(8 downto 3))
  }
  player.io.memRsp.valid := pending
  player.io.memRsp.payload := latched
  when(player.io.memRsp.fire) {
    pending := False
  }
}

class DdrTracePlayerTest extends AnyFunSuite {
  private val bank0 = IndexedSeq(
    (0x21cL, 0x00110000L),
    (0x148L, 0x0000ff00L),
    (0x124L, 0x00000000L)
  )
  private val bank1 = IndexedSeq((0x200L, 0x00000001L))

  test("pack/unpack keeps slots and records") {
    val bytes = GlidePlayImage.pack(Seq((bank0, 0), (bank1, 0)))
    val img = GlidePlayImage.unpack(bytes)
    assert(img.slots.length == 2)
    assert(GlidePlayImage.slotWrites(img, 0) == bank0)
    assert(GlidePlayImage.slotWrites(img, 1) == bank1)
    assert(bytes.length == GlidePlayImage.tableBytes + (bank0.length + bank1.length) * 8)
  }

  test("image lands at the documented DDR window") {
    assert(GowinAddressMap.glidePlayPhys == 0x11000000L)
    assert(GowinAddressMap.cpuDdrBytes == 256 * 1024 * 1024)
    assert(GowinAddressMap.cpuPakBase == 0x90000000L)
  }

  private lazy val compiled = SimConfig.withVerilator.compile(new DdrTracePlayerDut)

  private def loadRam(dut: DdrTracePlayerDut, bytes: Array[Byte]): Unit = {
    bytes.grouped(8).zipWithIndex.foreach { case (chunk, i) =>
      var v = BigInt(0)
      chunk.zipWithIndex.foreach { case (b, k) =>
        v |= BigInt(b & 0xff) << (8 * k)
      }
      dut.ram.setBigInt(i, v)
    }
  }

  private def idleAxi(dut: DdrTracePlayerDut): Unit = {
    dut.io.enable #= false
    dut.io.start #= false
    dut.io.loop #= false
    dut.io.select #= 0
    dut.io.base #= 0
    dut.io.axi.aw.ready #= true
    dut.io.axi.w.ready #= true
    dut.io.axi.b.valid #= false
    dut.io.axi.b.resp #= 0
    dut.io.axi.ar.ready #= false
    dut.io.axi.r.valid #= false
    dut.io.axi.r.resp #= 0
    dut.io.axi.r.data #= 0
  }

  private def play(dut: DdrTracePlayerDut, select: Int, expected: Seq[(Long, Long)]): Unit = {
    dut.io.select #= select
    dut.io.enable #= true
    dut.io.start #= true
    dut.clockDomain.waitSampling()
    dut.io.start #= false

    val seen = scala.collection.mutable.ArrayBuffer[(Long, Long)]()
    var cycles = 0
    while (!dut.io.done.toBoolean && !dut.io.fault.toBoolean && cycles < 400) {
      val fire = dut.io.axi.aw.valid.toBoolean && dut.io.axi.aw.ready.toBoolean &&
        dut.io.axi.w.valid.toBoolean && dut.io.axi.w.ready.toBoolean
      if (fire) {
        seen += ((dut.io.axi.aw.addr.toLong, dut.io.axi.w.data.toLong))
      }
      dut.io.axi.b.valid #= dut.io.axi.b.ready.toBoolean
      dut.clockDomain.waitSampling()
      cycles += 1
    }
    assert(!dut.io.fault.toBoolean, "player faulted")
    assert(dut.io.done.toBoolean, s"never done after $cycles cycles, seen=$seen")
    assert(seen.toSeq == expected, s"got $seen")
  }

  test("replays V2GL slot 0 then raises done") {
    val bytes = GlidePlayImage.pack(Seq((bank0, 0)))
    compiled.doSim("ddr-player-slot0") { dut =>
      dut.clockDomain.forkStimulus(10)
      loadRam(dut, bytes)
      idleAxi(dut)
      dut.clockDomain.waitSampling(8)
      play(dut, 0, bank0)
    }
  }

  test("select plays a later slot") {
    val bytes = GlidePlayImage.pack(Seq((bank0, 0), (bank1, 0)))
    compiled.doSim("ddr-player-slot1") { dut =>
      dut.clockDomain.forkStimulus(10)
      loadRam(dut, bytes)
      idleAxi(dut)
      dut.clockDomain.waitSampling(8)
      play(dut, 1, bank1)
    }
  }

  test("bad magic raises fault") {
    compiled.doSim("ddr-player-fault") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.ram.setBigInt(0, BigInt(0xdeadbeefL))
      idleAxi(dut)
      dut.io.enable #= true
      dut.clockDomain.waitSampling(4)
      dut.io.start #= true
      dut.clockDomain.waitSampling()
      dut.io.start #= false
      var cycles = 0
      while (!dut.io.fault.toBoolean && cycles < 40) {
        dut.clockDomain.waitSampling()
        cycles += 1
      }
      assert(dut.io.fault.toBoolean)
      assert(!dut.io.done.toBoolean)
    }
  }
}
