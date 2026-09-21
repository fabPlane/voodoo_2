package voodoo.gowin

import spinal.core._
import spinal.lib._
import spinal.lib.bus.amba4.axilite._
import voodoo.artix.AxiLite4ToBmbBridge

/** Hand-assembled RV32I words. Enough for a BRAM smoke test and later
  * `riscv32-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib`.
  */
object Rv32 {
  val x0 = 0
  val sp = 2
  val t0 = 5
  val t1 = 6
  val a0 = 10
  val a1 = 11

  def iType(rd: Int, rs1: Int, imm: Int, funct3: Int, opcode: Int): Long = {
    val im = imm & 0xfff
    (im.toLong << 20) | ((rs1 & 31L) << 15) | ((funct3 & 7L) << 12) |
      ((rd & 31L) << 7) | (opcode & 0x7fL)
  }

  def sType(rs1: Int, rs2: Int, imm: Int, funct3: Int, opcode: Int): Long = {
    val im = imm & 0xfff
    (((im >> 5) & 0x7fL) << 25) | ((rs2 & 31L) << 20) | ((rs1 & 31L) << 15) |
      ((funct3 & 7L) << 12) | ((im & 0x1fL) << 7) | (opcode & 0x7fL)
  }

  def uType(rd: Int, imm20: Int, opcode: Int): Long =
    ((imm20.toLong & 0xfffffL) << 12) | ((rd & 31L) << 7) | (opcode & 0x7fL)

  def jType(rd: Int, imm: Int, opcode: Int): Long = {
    val i = imm & 0x1fffff
    val b20 = (i >> 20) & 1L
    val b10_1 = (i >> 1) & 0x3ffL
    val b11 = (i >> 11) & 1L
    val b19_12 = (i >> 12) & 0xffL
    (b20 << 31) | (b10_1 << 21) | (b11 << 20) | (b19_12 << 12) |
      ((rd & 31L) << 7) | (opcode & 0x7fL)
  }

  def bType(rs1: Int, rs2: Int, imm: Int, funct3: Int, opcode: Int): Long = {
    val i = imm & 0x1fff
    val b12 = (i >> 12) & 1L
    val b10_5 = (i >> 5) & 0x3fL
    val b4_1 = (i >> 1) & 0xfL
    val b11 = (i >> 11) & 1L
    (b12 << 31) | (b10_5 << 25) | ((rs2 & 31L) << 20) | ((rs1 & 31L) << 15) |
      ((funct3 & 7L) << 12) | (b4_1 << 8) | (b11 << 7) | (opcode & 0x7fL)
  }

  def lui(rd: Int, imm20: Int): Long = uType(rd, imm20, 0x37)
  def addi(rd: Int, rs1: Int, imm: Int): Long = iType(rd, rs1, imm, 0, 0x13)
  def sw(rs2: Int, imm: Int, rs1: Int): Long = sType(rs1, rs2, imm, 2, 0x23)
  def lw(rd: Int, imm: Int, rs1: Int): Long = iType(rd, rs1, imm, 2, 0x03)
  def jal(rd: Int, imm: Int): Long = jType(rd, imm, 0x6f)
  def bne(rs1: Int, rs2: Int, imm: Int): Long = bType(rs1, rs2, imm, 1, 0x63)

  /** Always two instructions so table offsets stay stable. */
  def li(rd: Int, value: Long): Seq[Long] = {
    val v = value & 0xffffffffL
    val bot = (v & 0xfff).toInt
    val top = ((v >> 12) & 0xfffff).toInt
    if ((bot & 0x800) != 0) {
      Seq(lui(rd, (top + 1) & 0xfffff), addi(rd, rd, bot - 0x1000))
    } else {
      Seq(lui(rd, top), addi(rd, rd, bot))
    }
  }
}

/** Same two SST-1 writes as `VID_GlideBeginFrame` / `VID_GlideSwap`. */
object BarSmokeFirmware {
  val words: Seq[BigInt] = Seq(
    Rv32.lui(Rv32.sp, 0x80001),
    Rv32.lui(Rv32.a1, 0x110),
    Rv32.sw(Rv32.a1, 0x21c, Rv32.x0),
    Rv32.sw(Rv32.x0, 0x120, Rv32.x0),
    Rv32.jal(Rv32.x0, 0)
  ).map(BigInt(_))
}

/** RV32 host that `sst_wr`s a packed Glide PCI stream onto the BAR.
  *
  * On the Console this is the captured `test04` gouraud session (same writes
  * CoreSim already matches to 86Box). WinQuake + `pak0.pak` still need DDR.
  */
object BarGlideFirmware {
  val tableWords = 64

  def fromWrites(writes: Seq[(Long, Long)]): Seq[BigInt] = {
    require(writes.nonEmpty)
    val table = GowinAddressMap.cpuRamBase + tableWords * 4
    val end = table + writes.length.toLong * 8
    val loop = Rv32.li(Rv32.sp, GowinAddressMap.cpuRamBase + GowinAddressMap.cpuRamBytes) ++
      Rv32.li(Rv32.a0, table) ++
      Rv32.li(Rv32.a1, end)
    val body = Seq(
      Rv32.lw(Rv32.t0, 0, Rv32.a0),
      Rv32.lw(Rv32.t1, 4, Rv32.a0),
      Rv32.sw(Rv32.t1, 0, Rv32.t0),
      Rv32.addi(Rv32.a0, Rv32.a0, 8),
      Rv32.bne(Rv32.a0, Rv32.a1, -16),
      Rv32.jal(Rv32.x0, 0)
    )
    val prog = loop ++ body
    require(prog.length <= tableWords, s"glide stub ${prog.length} > $tableWords")
    val nops = Seq.fill(tableWords - prog.length)(Rv32.addi(0, 0, 0))
    val pairs = writes.flatMap { case (addr, data) =>
      Seq(BigInt(addr & 0xffffffffL), BigInt(data & 0xffffffffL))
    }
    (prog ++ nops).map(BigInt(_)) ++ pairs
  }

  lazy val words: Seq[BigInt] = fromWrites(DemoTraceRom.load("demo_test04.bin"))
}

/** Tiny RV32I stand-in for the Andes A25. Instruction/data BRAM at
  * `GowinAddressMap.cpuRamBase`; 16 MiB AXI-Lite BAR at 0 (`sst_set_base(0)`).
  * Held in reset until `run`. Replacing this with VexRiscv or AE350 keeps the
  * same two maps.
  */
case class BarRv32(
    program: Seq[BigInt] = BarGlideFirmware.words,
    wordCount: Int = GowinAddressMap.cpuRamBytes / 4,
    addressWidth: Int = CoreGowin.barAddressWidth
) extends Component {
  require(program.nonEmpty && program.length <= wordCount)
  require(isPow2(wordCount))

  val resetPc = U(GowinAddressMap.cpuRamBase, 32 bits)
  val ramBits = log2Up(wordCount)

  val io = new Bundle {
    val run = in Bool ()
    val axi = master(AxiLite4(AxiLite4ToBmbBridge.axiConfig(addressWidth)))
    val pc = out UInt (32 bits)
  }

  object State extends SpinalEnum {
    val reset, fetch, decode, exec, axiAw, axiB, ramLoad = newElement()
  }

  val state = RegInit(State.reset)
  val pc = Reg(UInt(32 bits)) init (GowinAddressMap.cpuRamBase)
  val instr = Reg(Bits(32 bits)) init (0)
  val awSent = RegInit(False)
  val wSent = RegInit(False)
  val regs = Vec(Reg(Bits(32 bits)) init (0), 32)

  val ram = Mem(Bits(32 bits), wordCount)
  ram.initBigInt(program.padTo(wordCount, BigInt(0)))
  ram.addAttribute("syn_ramstyle", "block_ram")
  ram.addAttribute("ram_style", "block")

  val fetchEn = state === State.fetch && io.run
  val fetchWord = pc(ramBits + 1 downto 2)

  val inst = instr

  val opcode = inst(6 downto 0)
  val rd = inst(11 downto 7).asUInt
  val funct3 = inst(14 downto 12)
  val rs1Idx = inst(19 downto 15).asUInt
  val rs2Idx = inst(24 downto 20).asUInt
  val rs1 = regs(rs1Idx)
  val rs2 = regs(rs2Idx)
  val immI = inst(31 downto 20).asSInt.resize(32 bits)
  val immU = inst(31 downto 12) ## B(0, 12 bits)
  val immS = (inst(31 downto 25) ## inst(11 downto 7)).asSInt.resize(32 bits)
  val immJ = (inst(31) ## inst(19 downto 12) ## inst(20) ## inst(30 downto 21) ## False).asSInt
    .resize(32 bits)
  val immB = (inst(31) ## inst(7) ## inst(30 downto 25) ## inst(11 downto 8) ## False).asSInt
    .resize(32 bits)

  val isLui = opcode === 0x37
  val isAuipc = opcode === 0x17
  val isJal = opcode === 0x6f
  val isJalr = opcode === 0x67
  val isBranch = opcode === 0x63
  val isLoad = opcode === 0x03
  val isStore = opcode === 0x23
  val isOpImm = opcode === 0x13
  val isOp = opcode === 0x33

  val aluAdd = (rs1.asSInt + immI).asBits
  val storeAddr = (rs1.asSInt + immS).asUInt.resize(32 bits)
  val loadAddr = (rs1.asSInt + immI).asUInt.resize(32 bits)
  val memAddr = Mux(isStore, storeAddr, loadAddr)
  val isBar = memAddr(31 downto addressWidth) === 0
  val isRam = memAddr(31 downto 28) === 0x8
  val ramWord = memAddr(ramBits + 1 downto 2)
  val ramAddr = UInt(ramBits bits)
  ramAddr := fetchWord
  when(state === State.exec && isLoad && isRam) {
    ramAddr := ramWord
  }
  val ramEn = fetchEn || (state === State.exec && isLoad && isRam)
  val ramData = ram.readSync(ramAddr, ramEn)

  def writeRd(value: Bits): Unit = {
    when(rd =/= 0) {
      regs(rd) := value
    }
  }

  io.axi.aw.valid := False
  io.axi.aw.addr := memAddr.resized
  io.axi.aw.prot := 0
  io.axi.w.valid := False
  io.axi.w.data := rs2
  io.axi.w.strb := 0xf
  io.axi.b.ready := False
  io.axi.ar.valid := False
  io.axi.ar.addr := memAddr.resized
  io.axi.ar.prot := 0
  io.axi.r.ready := False

  val pcPlus4 = pc + 4
  val takeEq = rs1 === rs2
  val takeLt = rs1.asSInt < rs2.asSInt
  val takeLtu = rs1.asUInt < rs2.asUInt
  val takeBranch = Bool()
  takeBranch := False
  switch(funct3) {
    is(0) { takeBranch := takeEq }
    is(1) { takeBranch := !takeEq }
    is(4) { takeBranch := takeLt }
    is(5) { takeBranch := !takeLt }
    is(6) { takeBranch := takeLtu }
    is(7) { takeBranch := !takeLtu }
  }

  switch(state) {
    is(State.reset) {
      pc := resetPc
      awSent := False
      wSent := False
      when(io.run) {
        state := State.fetch
      }
    }
    is(State.fetch) {
      when(!io.run) {
        state := State.reset
      } otherwise {
        state := State.decode
      }
    }
    is(State.decode) {
      instr := ramData
      state := State.exec
    }
    is(State.exec) {
      when(isLui) {
        writeRd(immU)
        pc := pcPlus4
        state := State.fetch
      } elsewhen (isAuipc) {
        writeRd((pc + immU.asUInt).asBits)
        pc := pcPlus4
        state := State.fetch
      } elsewhen (isJal) {
        writeRd(pcPlus4.asBits)
        pc := (pc.asSInt + immJ).asUInt
        state := State.fetch
      } elsewhen (isJalr) {
        writeRd(pcPlus4.asBits)
        pc := (((rs1.asSInt + immI).asUInt)(31 downto 1) ## False).asUInt
        state := State.fetch
      } elsewhen (isBranch) {
        pc := Mux(takeBranch, (pc.asSInt + immB).asUInt, pcPlus4)
        state := State.fetch
      } elsewhen (isOpImm) {
        val shamt = immI(4 downto 0).asUInt
        val arith = Bits(32 bits)
        arith := aluAdd
        switch(funct3) {
          is(0) { arith := aluAdd }
          is(2) { arith := B((rs1.asSInt < immI) ? U(1, 32 bits) | U(0, 32 bits)) }
          is(3) { arith := B((rs1.asUInt < immI.asUInt) ? U(1, 32 bits) | U(0, 32 bits)) }
          is(4) { arith := rs1 ^ immI.asBits }
          is(6) { arith := rs1 | immI.asBits }
          is(7) { arith := rs1 & immI.asBits }
          is(1) { arith := rs1 |<< shamt }
          is(5) {
            when(inst(30)) {
              arith := (rs1.asSInt |>> shamt).asBits
            } otherwise {
              arith := rs1 |>> shamt
            }
          }
        }
        writeRd(arith)
        pc := pcPlus4
        state := State.fetch
      } elsewhen (isOp) {
        val shamt = rs2(4 downto 0).asUInt
        val arith = Bits(32 bits)
        arith := (rs1.asSInt + rs2.asSInt).asBits
        switch(funct3) {
          is(0) {
            when(inst(30)) {
              arith := (rs1.asSInt - rs2.asSInt).asBits
            } otherwise {
              arith := (rs1.asSInt + rs2.asSInt).asBits
            }
          }
          is(1) { arith := rs1 |<< shamt }
          is(2) { arith := B((rs1.asSInt < rs2.asSInt) ? U(1, 32 bits) | U(0, 32 bits)) }
          is(3) { arith := B((rs1.asUInt < rs2.asUInt) ? U(1, 32 bits) | U(0, 32 bits)) }
          is(4) { arith := rs1 ^ rs2 }
          is(5) {
            when(inst(30)) {
              arith := (rs1.asSInt |>> shamt).asBits
            } otherwise {
              arith := rs1 |>> shamt
            }
          }
          is(6) { arith := rs1 | rs2 }
          is(7) { arith := rs1 & rs2 }
        }
        writeRd(arith)
        pc := pcPlus4
        state := State.fetch
      } elsewhen (isStore && isBar) {
        awSent := False
        wSent := False
        state := State.axiAw
      } elsewhen (isStore && isRam) {
        // No write port: Gowin infers pROM/SDP instead of DPB (PA2122).
        pc := pcPlus4
        state := State.fetch
      } elsewhen (isLoad && isBar) {
        state := State.axiAw
      } elsewhen (isLoad && isRam) {
        state := State.ramLoad
      } otherwise {
        pc := pcPlus4
        state := State.fetch
      }
    }
    is(State.axiAw) {
      val writing = isStore
      when(writing) {
        io.axi.aw.valid := !awSent
        io.axi.w.valid := !wSent
        when(io.axi.aw.fire) {
          awSent := True
        }
        when(io.axi.w.fire) {
          wSent := True
        }
        when((awSent || io.axi.aw.fire) && (wSent || io.axi.w.fire)) {
          state := State.axiB
        }
      } otherwise {
        io.axi.ar.valid := True
        when(io.axi.ar.fire) {
          state := State.axiB
        }
      }
    }
    is(State.axiB) {
      when(isStore) {
        io.axi.b.ready := True
        when(io.axi.b.valid) {
          pc := pcPlus4
          awSent := False
          wSent := False
          state := State.fetch
        }
      } otherwise {
        io.axi.r.ready := True
        when(io.axi.r.valid) {
          writeRd(io.axi.r.data)
          pc := pcPlus4
          state := State.fetch
        }
      }
    }
    is(State.ramLoad) {
      writeRd(ramData)
      pc := pcPlus4
      state := State.fetch
    }
  }

  io.pc := pc
}
