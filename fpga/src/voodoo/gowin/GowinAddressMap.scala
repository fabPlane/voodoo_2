package voodoo.gowin

/** Physical memory map for the Sipeed Tang Mega 138K Pro Dock.
  *
  * Colour, depth and textures all sit in the module DDR3 for this board: the dock
  * has no HyperRAM. 12 MiB at the bottom of the 1 GiB device is enough for a
  * Voodoo 1 640x480 colour pair plus 8 MiB of textures.
  */
object GowinAddressMap {
  val fbMemBytes = 4 * 1024 * 1024
  val texMemBytes = 8 * 1024 * 1024

  val fbMemBase = 0x0
  val texMemBase = fbMemBase + fbMemBytes

  val ddrNativeAddrWidth = 29
  val ddrNativeDataWidth = 256

  /** First DDR3 on the Retro Console: MT41J256M16, x16, 512 MiB.
    * Beats stay 256 bits so the existing native bridge applies. A 512 MiB
    * device then has a 24-bit beat address (2^24 * 32 bytes).
    */
  val consoleDdrNativeAddrWidth = 24
  val consoleDdrNativeDataWidth = 256
}
