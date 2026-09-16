package voodoo.artix

/** Physical memory map for the Artix-7 200T bring-up board (AMD AC701).
  *
  * Same split as the Gowin dock: colour/depth at the bottom of DDR3, textures
  * parked above so one MIG controller holds both. 12 MiB is enough for a
  * Voodoo 1 640x480 colour pair plus 8 MiB of textures.
  */
object ArtixAddressMap {
  val fbMemBytes = 4 * 1024 * 1024
  val texMemBytes = 8 * 1024 * 1024

  val fbMemBase = 0x0
  val texMemBase = fbMemBase + fbMemBytes
}
