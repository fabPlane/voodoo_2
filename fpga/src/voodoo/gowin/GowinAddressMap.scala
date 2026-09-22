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
  // The revision-C Console module carries the same two x16 DDR3 chips as the
  // Tang Mega 138K module. Gowin's proven 1:4 controller exposes them as one
  // 256-bit / 29-bit native port.
  val consoleDdrNativeAddrWidth = 29
  val consoleDdrNativeDataWidth = 256

  /** Soft RV32 / Andes A25 view. BAR is identity-mapped so `sst_set_base(0)`
    * matches host_probe / vid_glide. BarRv32 reset firmware stays in 32 KiB
    * BRAM. The A25 ELF (`sw/quake-riscv/build-rv32/quake-sw`) wants 32 MiB of
    * DDR at cpuRamBase (phys 16 MiB) plus shareware pak0 at cpuPakBase.
    */
  val cpuBarBase = 0x0L
  val cpuRamBase = 0x80000000L
  val cpuRamBytes = 32 * 1024
  val cpuDdrPhys = 16 * 1024 * 1024
  val cpuDdrBytes = 32 * 1024 * 1024
  val cpuPakBase = 0x82000000L
  val cpuPakBytes = 20 * 1024 * 1024
  val cpuHidBase = 0x10000000L
}
