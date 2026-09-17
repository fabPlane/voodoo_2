package voodoo.gowin

import spinal.core._
import spinal.lib._

/** USB / onboard-key menu. Root list is SDK traces plus OTHER DEMOS; A on
  * that folder opens the publisher-demo submenu. A/Start runs a playable
  * item, B/Select returns. Unplayable entries open a per-item info page.
  */
case class DemoMenu(
    mainCount: Int,
    otherCount: Int,
    mainPlayable: Seq[Boolean],
    otherPlayable: Seq[Boolean],
    folderIndex: Int
) extends Component {
  require(mainCount >= 1 && mainPlayable.length == mainCount)
  require(otherCount >= 1 && otherPlayable.length == otherCount)
  require(folderIndex >= 0 && folderIndex < mainCount)
  require(!mainPlayable(folderIndex))

  val cursorWidth = log2Up(mainCount max otherCount max 2)
  val screenCount = 2 * (mainCount + otherCount)
  val screenWidth = log2Up(screenCount max 2)

  object Page extends SpinalEnum {
    val main, other = newElement()
  }
  object State extends SpinalEnum {
    val menu, playing, info = newElement()
  }

  val io = new Bundle {
    val pads = in Bits (SnesPad.width bits)
    val ready = in Bool ()
    val cursor = out UInt (cursorWidth bits)
    val screen = out UInt (screenWidth bits)
    val overlay = out Bool ()
    val start = out Bool ()
    val playing = out Bool ()
    val info = out Bool ()
    val other = out Bool ()
  }

  val page = RegInit(Page.main)
  val state = RegInit(State.menu)
  val mainCursor = Reg(UInt(cursorWidth bits)) init (0)
  val otherCursor = Reg(UInt(cursorWidth bits)) init (0)
  val prev = Reg(Bits(SnesPad.width bits)) init (0)
  val startPulse = RegInit(False)
  val lastMain = U(mainCount - 1, cursorWidth bits)
  val lastOther = U(otherCount - 1, cursorWidth bits)
  val rise = io.pads & ~prev
  val folderNow = mainCursor === U(folderIndex, cursorWidth bits)
  val playableNow = (0 until mainCount)
    .map(i => (mainCursor === U(i, cursorWidth bits)) && Bool(mainPlayable(i)))
    .reduce(_ || _)
  val otherPlayableNow = (0 until otherCount)
    .map(i => (otherCursor === U(i, cursorWidth bits)) && Bool(otherPlayable(i)))
    .reduce(_ || _)

  prev := io.pads
  startPulse := False

  switch(state) {
    is(State.menu) {
      switch(page) {
        is(Page.main) {
          when(rise(SnesPad.Down) || rise(SnesPad.Right)) {
            mainCursor := Mux(mainCursor === lastMain, U(0, cursorWidth bits), mainCursor + 1)
          }
          when(rise(SnesPad.Up) || rise(SnesPad.Left)) {
            mainCursor := Mux(mainCursor === 0, lastMain, mainCursor - 1)
          }
          when(rise(SnesPad.A) || rise(SnesPad.Start)) {
            when(folderNow) {
              page := Page.other
            } elsewhen (playableNow) {
              when(io.ready) {
                startPulse := True
                state := State.playing
              }
            } otherwise {
              state := State.info
            }
          }
        }
        is(Page.other) {
          when(rise(SnesPad.Down) || rise(SnesPad.Right)) {
            otherCursor := Mux(otherCursor === lastOther, U(0, cursorWidth bits), otherCursor + 1)
          }
          when(rise(SnesPad.Up) || rise(SnesPad.Left)) {
            otherCursor := Mux(otherCursor === 0, lastOther, otherCursor - 1)
          }
          when(rise(SnesPad.B) || rise(SnesPad.Select)) {
            page := Page.main
          }
          when(rise(SnesPad.A) || rise(SnesPad.Start)) {
            when(otherPlayableNow) {
              when(io.ready) {
                startPulse := True
                state := State.playing
              }
            } otherwise {
              state := State.info
            }
          }
        }
      }
    }
    is(State.playing) {
      when(rise(SnesPad.B) || rise(SnesPad.Select)) {
        state := State.menu
      }
    }
    is(State.info) {
      when(rise(SnesPad.B) || rise(SnesPad.Select)) {
        state := State.menu
      }
    }
  }

  val cursor = Mux(page === Page.other, otherCursor, mainCursor)
  val screen = UInt(screenWidth bits)
  screen := mainCursor.resized
  when(page === Page.other) {
    screen := U(mainCount, screenWidth bits) + otherCursor.resized
  }
  when(state === State.info) {
    when(page === Page.other) {
      screen := U(2 * mainCount + otherCount, screenWidth bits) + otherCursor.resized
    } otherwise {
      screen := U(mainCount + otherCount, screenWidth bits) + mainCursor.resized
    }
  }

  io.cursor := cursor
  io.screen := screen
  io.overlay := state =/= State.playing
  io.start := startPulse
  io.playing := state === State.playing
  io.info := state === State.info
  io.other := page === Page.other
}
