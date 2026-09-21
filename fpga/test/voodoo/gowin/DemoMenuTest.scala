package voodoo.gowin

import org.scalatest.funsuite.AnyFunSuite
import spinal.core._
import spinal.core.sim._

class DemoMenuTest extends AnyFunSuite {
  private def menu() =
    DemoMenu(
      DemoCatalog.mainCount,
      DemoCatalog.otherCount,
      DemoCatalog.mainPlayable,
      DemoCatalog.otherPlayable,
      DemoCatalog.otherDemosIndex
    )

  private lazy val compiled =
    SimConfig.withVerilator.compile(menu())

  private def init(dut: DemoMenu): Unit = {
    dut.io.pads #= 0
    dut.io.ready #= false
  }

  private def pulse(dut: DemoMenu, bit: Int): Unit = {
    dut.io.pads #= 1 << bit
    dut.clockDomain.waitSampling()
    dut.io.pads #= 0
    dut.clockDomain.waitSampling()
  }

  // Walk the main cursor down to a given index from the top of the list.
  private def navMain(dut: DemoMenu, index: Int): Unit =
    for (_ <- 0 until index) pulse(dut, SnesPad.Down)

  test("USB D-pad walks logo then Glide tests then OTHER DEMOS") {
    compiled.doSim("demo-menu-pad") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling(20)

      assert(dut.io.cursor.toInt == DemoCatalog.logoIndex)
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.test04Index, "D-pad must work before DDR calib")
      dut.io.ready #= true
      dut.clockDomain.waitSampling()
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.test16Index)
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.alphabetIndex)
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.hbPinwheelIndex)
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.hbDepthIndex)
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.hbAlphaIndex)
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.otherDemosIndex)
      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == DemoCatalog.logoIndex)
    }
  }

  test("OTHER DEMOS opens the submenu; B returns") {
    compiled.doSim("demo-menu-other") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling(20)
      dut.io.ready #= true
      dut.clockDomain.waitSampling()

      navMain(dut, DemoCatalog.otherDemosIndex)
      pulse(dut, SnesPad.A)
      assert(dut.io.other.toBoolean)
      assert(!dut.io.info.toBoolean)
      assert(!dut.io.playing.toBoolean)
      assert(dut.io.cursor.toInt == DemoCatalog.tombIndex)
      assert(dut.io.screen.toInt == DemoCatalog.otherMenuBase + DemoCatalog.tombIndex)

      pulse(dut, SnesPad.Down)
      assert(dut.io.cursor.toInt == 1)
      pulse(dut, SnesPad.B)
      assert(!dut.io.other.toBoolean)
      assert(dut.io.cursor.toInt == DemoCatalog.otherDemosIndex)
    }
  }

  test("Tomb in OTHER DEMOS opens its info page") {
    compiled.doSim("demo-menu-tomb") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling(20)
      dut.io.ready #= true
      dut.clockDomain.waitSampling()

      navMain(dut, DemoCatalog.otherDemosIndex)
      pulse(dut, SnesPad.A)
      pulse(dut, SnesPad.A)
      assert(dut.io.info.toBoolean)
      assert(dut.io.other.toBoolean)
      assert(!dut.io.playing.toBoolean)
      assert(dut.io.overlay.toBoolean)
      assert(dut.io.screen.toInt == DemoCatalog.otherInfoBase + DemoCatalog.tombIndex)

      pulse(dut, SnesPad.B)
      assert(!dut.io.info.toBoolean)
      assert(dut.io.other.toBoolean)
      assert(dut.io.cursor.toInt == DemoCatalog.tombIndex)
    }
  }

  test("3dfx logo starts the SST player") {
    compiled.doSim("demo-menu-logo") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling(20)
      dut.io.ready #= true
      dut.clockDomain.waitSampling()

      pulse(dut, SnesPad.A)
      assert(dut.io.playing.toBoolean)
      assert(!dut.io.info.toBoolean)
      assert(!dut.io.overlay.toBoolean)
      assert(dut.io.cursor.toInt == DemoCatalog.logoIndex)

      pulse(dut, SnesPad.B)
      assert(!dut.io.playing.toBoolean)
    }
  }

  test("USER/Down cannot escape from a running demo") {
    compiled.doSim("demo-menu-down-back") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.io.ready #= true
      dut.clockDomain.waitSampling(20)

      pulse(dut, SnesPad.A)
      assert(dut.io.playing.toBoolean)
      pulse(dut, SnesPad.Down)
      assert(dut.io.playing.toBoolean)
      pulse(dut, SnesPad.B)
      assert(!dut.io.playing.toBoolean)
    }
  }

  test("Glide test04 starts the SST player") {
    compiled.doSim("demo-menu-test04") { dut =>
      dut.clockDomain.forkStimulus(10)
      init(dut)
      dut.clockDomain.waitSampling(20)
      dut.io.ready #= true
      dut.clockDomain.waitSampling()

      pulse(dut, SnesPad.Down)
      pulse(dut, SnesPad.Start)
      assert(dut.io.playing.toBoolean)
      assert(!dut.io.info.toBoolean)
      assert(!dut.io.overlay.toBoolean)
      assert(dut.io.screen.toInt == DemoCatalog.test04Index)

      pulse(dut, SnesPad.Select)
      assert(!dut.io.playing.toBoolean)
      assert(dut.io.cursor.toInt == DemoCatalog.test04Index)
    }
  }

  test("player banks are logo then SDK traces then homebrew demos") {
    assert(DemoCatalog.playerFiles == Seq(
      "demo_logo_spin.bin",
      "demo_test04.bin",
      "demo_test16.bin",
      "demo_test_alphabet.bin",
      "demo_hb_pinwheel.bin",
      "demo_hb_depth.bin",
      "demo_hb_alpha.bin"
    ))
    assert(DemoCatalog.playerBankByCursor(DemoCatalog.test16Index) == 2)
    assert(DemoCatalog.playerBankByCursor(DemoCatalog.hbPinwheelIndex) == 4)
    assert(DemoCatalog.playerBankByCursor(DemoCatalog.hbAlphaIndex) == 6)
    assert(DemoCatalog.playerBankByCursor(DemoCatalog.otherDemosIndex) == 0)
    assert(DemoCatalog.playerLoopAt(0) == DemoCatalog.logoLoopAt)
    assert(DemoCatalog.playerLoopAt.drop(1).forall(_ == 0))
  }

  test("OTHER DEMOS lists the publisher archives") {
    assert(DemoCatalog.other.map(_.title) == Seq(
      "TOMB  RAIDER",
      "SCREAMER  2",
      "EXTREME  ASSAULT",
      "CARMAGEDDON  SPLAT",
      "ARCHIMEDEAN  DYNASTY",
      "TOSHINDEN",
      "NASCAR  RACING  2",
      "GRAND  THEFT  AUTO",
      "LORDS  OF  THE  REALM  2",
      "MONSTER  3D  DISC"
    ))
    assert(DemoCatalog.other.forall(!_.playable))
    assert(DemoOsd.otherMenuScreen(0).head.contains("OTHER  DEMOS"))
  }

  test("logo spin ROM is fbiInit then a looping chevron") {
    val writes = DemoTraceRom.load("demo_logo_spin.bin")
    assert(writes.head == ((0x21cL, 0x00110000L)))
    assert(writes.length > DemoCatalog.logoLoopAt)
    assert(writes(DemoCatalog.logoLoopAt)._1 == 0x148L)
    assert(writes.exists(_._1 == 0x100L), "needs ftriangleCMD")
    assert(writes.exists(_._1 == 0x128L), "needs swapbufferCMD")
  }

  test("test04 ROM is the gouraud SDK capture") {
    val writes = DemoTraceRom.load("demo_test04.bin")
    assert(writes.head == ((0x21cL, 0x00110000L)))
    assert(writes.exists(_._1 == 0x100L), "needs ftriangleCMD")
  }

  test("OSD screens elaborate") {
    SpinalConfig(mode = Verilog, targetDirectory = "simWorkspace/tmp").generate(TextOsd(DemoOsd.screens))
  }

  test("OSD paints navy then a glyph, not a blank frame") {
    SimConfig.withVerilator.compile(TextOsd(DemoOsd.screens)).doSim("text-osd-navy") { dut =>
      dut.clockDomain.forkStimulus(10)
      dut.io.enable #= true
      dut.io.de #= true
      dut.io.screen #= 0
      dut.io.x #= 0
      dut.io.y #= 0
      dut.clockDomain.waitSampling(4)
      assert(dut.io.r.toInt == 0x10 && dut.io.g.toInt == 0x18 && dut.io.b.toInt == 0x48)

      // Menu row 0 is " SPINALVOODOO..."; cell is 16px, so x=16 is 'S'.
      // 'S' top row 0x3c has bits 5:2 set; doubled pixels put that at x=20.
      dut.io.x #= 20
      dut.io.y #= 80
      dut.clockDomain.waitSampling(6)
      assert(dut.io.r.toInt == 0xf0 && dut.io.g.toInt == 0xf4 && dut.io.b.toInt == 0xff, s"glyph ${dut.io.r.toInt}")
    }
  }
}
