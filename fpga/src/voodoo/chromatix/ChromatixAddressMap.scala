package voodoo.chromatix

/** Physical memory map for the ModRetro Chromatic (GW5A-25 + APS6408L 8 MiB OPI PSRAM).
  *
  * ChromatiX already parks the Game Boy blend framebuffer near 0x10000 and (in BIOS
  * mode) the RISC-V main RAM in the upper half. For `--with-voodoo` we own most of
  * the device: a compact colour+depth region and a reduced texture aperture. Full
  * Voodoo-1 640x480 double-buffered RGB + Z needs ~1.84 MiB; textures are capped
  * at 4 MiB so the pair still fits in 8 MiB with headroom for OSD/video scratch.
  *
  * LUT fit on GW5A-25 is a separate problem — the full SpinalVoodoo Core is sized
  * for DE10-Nano / Tang 138K class parts. This map is what the PSRAM can hold.
  */
object ChromatixAddressMap {
  val psramBytes = 8 * 1024 * 1024

  /** Colour + depth planes (enough for 640x480 RGB565 double + 16-bit Z). */
  val fbMemBytes = 2 * 1024 * 1024

  /** Reduced texture aperture (half of the DE10/Artix/Gowin 8 MiB window). */
  val texMemBytes = 4 * 1024 * 1024

  val fbMemBase = 0x0
  val texMemBase = fbMemBase + fbMemBytes

  require(fbMemBase + fbMemBytes + texMemBytes <= psramBytes)

  /** AXI address width into the 8 MiB PSRAM (byte addresses). */
  val memAddressWidth = 23
}
