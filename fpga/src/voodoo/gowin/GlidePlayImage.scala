package voodoo.gowin

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Packed Glide replay image for SD / JTAG / DDR.
  *
  * Little-endian layout:
  *   0  u32 magic   'V2GL' (0x4C473256)
  *   4  u32 version 1
  *   8  u32 slots   1..16
  *  12  u32 reserved
  *  16  slot[16] { off u32, count u32, loopAt u32, flags u32 }
  * 272  packed addr:u32,data:u32 records (same as DemoTraceRom)
  */
object GlidePlayImage {
  val magic = 0x4c473256L
  val version = 1
  val maxSlots = 16
  val headerBytes = 16
  val slotBytes = 16
  val tableBytes = headerBytes + maxSlots * slotBytes

  case class Slot(offset: Int, count: Int, loopAt: Int, flags: Int = 0) {
    require(offset >= tableBytes && (offset % 8) == 0, s"slot offset $offset")
    require(count > 0, "empty slot")
    require(loopAt >= 0 && loopAt < count, s"loopAt $loopAt")
  }

  case class Image(slots: Seq[Slot], records: IndexedSeq[(Long, Long)]) {
    require(slots.nonEmpty && slots.length <= maxSlots)
  }

  def pack(slots: Seq[(IndexedSeq[(Long, Long)], Int)]): Array[Byte] = {
    require(slots.nonEmpty && slots.length <= maxSlots)
    val recs = slots.flatMap(_._1)
    val bytes = tableBytes + recs.length * 8
    val buf = ByteBuffer.allocate(bytes).order(ByteOrder.LITTLE_ENDIAN)
    buf.putInt(magic.toInt)
    buf.putInt(version)
    buf.putInt(slots.length)
    buf.putInt(0)
    var off = tableBytes
    slots.foreach { case (writes, loopAt) =>
      require(writes.nonEmpty)
      require(loopAt >= 0 && loopAt < writes.length)
      buf.putInt(off)
      buf.putInt(writes.length)
      buf.putInt(loopAt)
      buf.putInt(0)
      off += writes.length * 8
    }
    while (buf.position() < tableBytes) {
      buf.putInt(0)
    }
    recs.foreach { case (addr, data) =>
      buf.putInt(addr.toInt)
      buf.putInt(data.toInt)
    }
    buf.array()
  }

  def unpack(raw: Array[Byte]): Image = {
    require(raw.length >= tableBytes, s"V2GL truncated (${raw.length})")
    val buf = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN)
    val mag = buf.getInt.toLong & 0xffffffffL
    require(mag == magic, f"bad V2GL magic $mag%08x")
    val ver = buf.getInt
    require(ver == version, s"unsupported V2GL version $ver")
    val n = buf.getInt
    require(n >= 1 && n <= maxSlots, s"slot count $n")
    buf.getInt()
    val slots = (0 until n).map { _ =>
      Slot(buf.getInt(), buf.getInt(), buf.getInt(), buf.getInt())
    }
    val recs = Vector.newBuilder[(Long, Long)]
    val recBuf = ByteBuffer.wrap(raw, tableBytes, raw.length - tableBytes).order(ByteOrder.LITTLE_ENDIAN)
    while (recBuf.remaining() >= 8) {
      val addr = recBuf.getInt.toLong & 0xffffffffL
      val data = recBuf.getInt.toLong & 0xffffffffL
      recs += ((addr, data))
    }
    Image(slots, recs.result())
  }

  def slotWrites(image: Image, index: Int): IndexedSeq[(Long, Long)] = {
    val slot = image.slots(index)
    val start = (slot.offset - tableBytes) / 8
    image.records.slice(start, start + slot.count)
  }
}
