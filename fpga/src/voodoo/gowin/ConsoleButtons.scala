package voodoo.gowin

import spinal.core._
import spinal.lib._

/** Synchronizes the two case buttons and owns the diagnostic-mode gesture.
  *
  * Holding MODE during the short power-on window permanently selects diagnostic
  * mode for this FPGA configuration.  Neither button is then forwarded to the
  * menu, so a held/stuck switch cannot accidentally leave the running core.
  */
case class ConsoleButtons(
    armCycles: Int = 1 << 22,
    selectHoldCycles: Int = 1 << 24
) extends Component {
  require(armCycles >= 2)
  require(selectHoldCycles >= 2)

  val io = new Bundle {
    val modeN = in Bool ()
    val resetN = in Bool ()
    val debugMode = out Bool ()
    val modePressed = out Bool ()
    val resetPressed = out Bool ()
    val menuDown = out Bool ()
    val menuSelect = out Bool ()
  }

  val mode = BufferCC(~io.modeN, init = False)
  val reset = BufferCC(~io.resetN, init = False)
  val modePrev = RegNext(mode) init (False)
  val resetPrev = RegNext(reset) init (False)
  val modeRise = mode && !modePrev
  val resetRise = reset && !resetPrev

  val debug = RegInit(False)
  val armed = RegInit(True)
  val armCount = Reg(UInt(log2Up(armCycles) bits)) init (0)
  val holdCount = Reg(UInt(log2Up(selectHoldCycles) bits)) init (0)
  val holdSelect = RegInit(False)
  when(armed) {
    when(armCount === U(armCycles - 1, armCount.getWidth bits)) {
      armed := False
      // Must hold MODE for the whole window. A one-cycle bounce at
      // TangCore load used to latch diagnostics forever and, with
      // board_top holding iosys in reset, the USB pad looked dead.
      when(mode) {
        debug := True
      }
    } otherwise {
      armCount := armCount + 1
    }
  }

  // The Console exposes MODE much more conveniently than RESET. A short press
  // advances the menu; holding it selects the highlighted entry. Generate the
  // select pulse once per hold. Diagnostic mode suppresses both paths below.
  when(!armed && mode) {
    when(holdCount =/= U(selectHoldCycles - 1, holdCount.getWidth bits)) {
      holdCount := holdCount + 1
    } elsewhen (!holdSelect) {
      holdSelect := True
    }
  } otherwise {
    holdCount := 0
    holdSelect := False
  }
  val holdSelectPrev = RegNext(holdSelect) init (False)
  val holdSelectRise = holdSelect && !holdSelectPrev

  io.debugMode := debug
  io.modePressed := mode
  io.resetPressed := reset
  io.menuDown := modeRise && !armed && !debug
  io.menuSelect := (resetRise || holdSelectRise) && !armed && !debug
}
