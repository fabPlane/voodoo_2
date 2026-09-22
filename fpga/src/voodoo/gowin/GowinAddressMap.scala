package voodoo.gowin

/** Physical memory map for the Sipeed Tang Mega 138K Pro Dock and the
  * Retro Console 138K. Colour, depth and textures sit in module DDR3.
  *
  * The Console / Mega parts have about 1 GiB of DDR3. Voodoo keeps the first
  * 12 MiB. The soft RV32 / Andes A25 view maps a large DDR window at
  * `cpuRamBase` so WinQuake + shareware pak fit (the 32 KiB BarRv32 BRAM
  * is only a smoke ROM). Packed Glide replays loaded from SD or JTAG live
  * in the `glidePlayPhys` window.
  */
object GowinAddressMap {
  val fbMemBytes = 4 * 1024 * 1024
  val texMemBytes = 8 * 1024 * 1024

  val fbMemBase = 0x0
  val texMemBase = fbMemBase + fbMemBytes

  val ddrNativeAddrWidth = 29
  val ddrNativeDataWidth = 256
  // Console 138K only uses the first x16 DDR3 chip (LiteX / nand2mario).
  // Gowin 1:4 native UI is 128-bit / 28-bit address.
  val consoleDdrNativeAddrWidth = 28
  val consoleDdrNativeDataWidth = 128

  /** Soft RV32 / Andes A25 view. BAR is identity-mapped so `sst_set_base(0)`
    * matches host_probe / vid_glide. BarRv32 reset firmware stays in 32 KiB
    * BRAM. The software Quake ELF wants DDR at cpuRamBase (phys cpuDdrPhys).
    */
  val cpuBarBase = 0x0L
  val cpuRamBase = 0x80000000L
  val cpuRamBytes = 32 * 1024
  val cpuDdrPhys = 16 * 1024 * 1024
  val cpuDdrBytes = 256 * 1024 * 1024
  val cpuPakBase = 0x90000000L
  val cpuPakBytes = 32 * 1024 * 1024
  val cpuHidBase = 0x10000000L

  /** V2GL image written by `sw/glide-sd` (FAT copy or JTAG poke). */
  val glidePlayPhys = cpuDdrPhys + cpuDdrBytes
  val glidePlayBytes = 256 * 1024 * 1024
}
