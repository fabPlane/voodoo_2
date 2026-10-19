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
  // Console 138K only uses the first x16 DDR3 chip. Gowin 1:4 native UI
  // there is 128-bit / 28-bit address. The Pro Dock stays 256 / 29.
  val consoleDdrNativeAddrWidth = 28
  val consoleDdrNativeDataWidth = 128
}
