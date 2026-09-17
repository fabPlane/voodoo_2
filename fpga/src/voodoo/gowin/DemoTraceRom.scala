package voodoo.gowin

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.{Files, Path, Paths}

/** Packed Glide PCI writes for the Console demo player.
  *
  * Each record is little-endian `addr:u32, data:u32`. Texture aperture writes
  * stay in the 16 MiB Voodoo map (0x800000+); the AXI-Lite BAR is 24-bit so
  * they pass through untranslated.
  */
object DemoTraceRom {
  val relativeDir = "hw/gowin/console"

  def packedPath(name: String): Path = {
    val cwd = Paths.get(".").toAbsolutePath.normalize
    val candidates = Seq(
      cwd.resolve(relativeDir).resolve(name),
      cwd.resolve("fpga").resolve(relativeDir).resolve(name),
      cwd.getParent.resolve("fpga").resolve(relativeDir).resolve(name)
    )
    candidates.find(Files.exists(_)).getOrElse {
      throw new RuntimeException(
        s"$relativeDir/$name not found from $cwd; run fpga/hw/gowin/console/pack_demo_trace.py"
      )
    }
  }

  def load(name: String): IndexedSeq[(Long, Long)] = {
    val bytes = Files.readAllBytes(packedPath(name))
    require(bytes.length % 8 == 0, s"demo trace length ${bytes.length} is not a multiple of 8")
    val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
    val out = Vector.newBuilder[(Long, Long)]
    while (bb.hasRemaining) {
      val addr = bb.getInt.toLong & 0xffffffffL
      val data = bb.getInt.toLong & 0xffffffffL
      out += ((addr, data))
    }
    out.result()
  }

  def load(): IndexedSeq[(Long, Long)] = load("demo_test04.bin")

  def banks(): Seq[IndexedSeq[(Long, Long)]] = DemoCatalog.playerFiles.map(load)
}
