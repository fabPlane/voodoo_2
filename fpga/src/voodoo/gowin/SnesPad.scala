package voodoo.gowin

/** SNES-layout pad bits used by TangCore USB HID (`hid1`/`hid2`) and DS2.
  *
  * Low 12 bits: `(R L X A RT LT DN UP START SELECT Y B)`.
  */
object SnesPad {
  val B = 0
  val Y = 1
  val Select = 2
  val Start = 3
  val Up = 4
  val Down = 5
  val Left = 6
  val Right = 7
  val A = 8
  val X = 9
  val L = 10
  val R = 11
  val width = 12
}
