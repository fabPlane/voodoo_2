package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core.sim._

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

  private lazy val compiled =
    SimConfig.withVerilator.compile(DdrTracePlayer(addressWidth = 24))

  private def wordsFrom(bytes: Array[Byte]): Array[BigInt] = {
    bytes.grouped(8).map { chunk =>
      var v = BigInt(0)
      chunk.zipWithIndex.foreach { case (b, k) =>
        v |= BigInt(b & 0xff) << (8 * k)
      }
      v
    }.toArray
  }

  private def serveMem(dut: DdrTracePlayer, mem: Array[BigInt]): Unit = {
    fork {
      dut.io.memCmd.ready #= true
      dut.io.memRsp.valid #= false
      dut.io.memRsp.payload #= 0
      while (true) {
        dut.clockDomain.waitSampling()
        if (dut.io.memCmd.valid.toBoolean) {
          val idx = (dut.io.memCmd.payload.toLong / 8L).toInt
          val word = if (idx >= 0 && idx < mem.length) mem(idx) else BigInt(0)
          dut.io.memCmd.ready #= false
          dut.clockDomain.waitSampling()
          dut.io.memRsp.valid #= true
          dut.io.memRsp.payload #= word
          dut.clockDomain.waitSamplingWhere(dut.io.memRsp.ready.toBoolean)
          dut.io.memRsp.valid #= false
          dut.io.memCmd.ready #= true
        }
      }
    }
  }

  private def idleAxi(dut: DdrTracePlayer): Unit = {
    dut.io.enable #= false
    dut.io.start #= false
    dut.io.loop #= false
    dut.io.select #= 0
    dut.io.base #= 0
    dut.io.memCmd.ready #= true
    dut.io.memRsp.valid #= false
    dut.io.memRsp.payload #= 0
    dut.io.axi.aw.ready #= true
    dut.io.axi.w.ready #= true
    dut.io.axi.b.valid #= false
    dut.io.axi.b.resp #= 0
    dut.io.axi.ar.ready #= false
    dut.io.axi.r.valid #= false
    dut.io.axi.r.resp #= 0
    dut.io.axi.r.data #= 0
  }

  private def play(dut: DdrTracePlayer, select: Int, expected: Seq[(Long, Long)]): Unit = {
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
    val mem = wordsFrom(GlidePlayImage.pack(Seq((bank0, 0))))
    compiled.doSim("ddr-player-slot0") { dut =>
      dut.clockDomain.forkStimulus(10)
      idleAxi(dut)
      serveMem(dut, mem)
      dut.clockDomain.waitSampling(4)
      play(dut, 0, bank0)
    }
  }

  test("select plays a later slot") {
    val mem = wordsFrom(GlidePlayImage.pack(Seq((bank0, 0), (bank1, 0))))
    compiled.doSim("ddr-player-slot1") { dut =>
      dut.clockDomain.forkStimulus(10)
      idleAxi(dut)
      serveMem(dut, mem)
      dut.clockDomain.waitSampling(4)
      play(dut, 1, bank1)
    }
  }

  test("bad magic raises fault") {
    compiled.doSim("ddr-player-fault") { dut =>
      dut.clockDomain.forkStimulus(10)
      idleAxi(dut)
      serveMem(dut, Array(BigInt(0xdeadbeefL)))
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
