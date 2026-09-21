package voodoo.gowin

import spinal.core._
import spinal.lib._

object Bl616Link {
  val baud = 2000000
  /** Same as NESTang. TangCore firmware probes this after loading a core. */
  val coreId = 0x01
  val confStr: String = "VoodooTang;;"

  def cyclesPerBit(clkHz: Int = 50000000, baudRate: Int = baud): Int = clkHz / baudRate
}

/** 8N1 UART receiver. Samples at mid-bit. */
case class UartRx(cyclesPerBit: Int) extends Component {
  require(cyclesPerBit >= 4)

  val io = new Bundle {
    val rx = in Bool ()
    val data = out Bits (8 bits)
    val valid = out Bool ()
  }

  object State extends SpinalEnum {
    val idle, start, data, stop = newElement()
  }

  val rxSync = BufferCC(io.rx, init = True)
  val state = RegInit(State.idle)
  val divWidth = log2Up(cyclesPerBit)
  val clkDiv = Reg(UInt(divWidth bits)) init (0)
  val bitIdx = Reg(UInt(3 bits)) init (0)
  val shifter = Reg(Bits(8 bits)) init (0)
  val valid = RegInit(False)

  io.data := shifter
  io.valid := valid
  valid := False

  switch(state) {
    is(State.idle) {
      when(!rxSync) {
        state := State.start
        clkDiv := (cyclesPerBit / 2) - 1
      }
    }
    is(State.start) {
      clkDiv := clkDiv - 1
      when(clkDiv === 0) {
        when(!rxSync) {
          state := State.data
          clkDiv := cyclesPerBit - 1
          bitIdx := 0
        } otherwise {
          state := State.idle
        }
      }
    }
    is(State.data) {
      clkDiv := clkDiv - 1
      when(clkDiv === 0) {
        shifter(bitIdx) := rxSync
        clkDiv := cyclesPerBit - 1
        when(bitIdx === 7) {
          state := State.stop
        } otherwise {
          bitIdx := bitIdx + 1
        }
      }
    }
    is(State.stop) {
      clkDiv := clkDiv - 1
      when(clkDiv === 0) {
        valid := True
        state := State.idle
      }
    }
  }
}

/** 8N1 UART transmitter. Idle high. */
case class UartTx(cyclesPerBit: Int) extends Component {
  require(cyclesPerBit >= 4)

  val io = new Bundle {
    val data = in Bits (8 bits)
    val valid = in Bool ()
    val ready = out Bool ()
    val tx = out Bool ()
  }

  val sending = RegInit(False)
  val divWidth = log2Up(cyclesPerBit)
  val clkDiv = Reg(UInt(divWidth bits)) init (0)
  val idx = Reg(UInt(4 bits)) init (0)
  val bits = Reg(Bits(10 bits)) init (0)
  val tx = RegInit(True)

  io.tx := tx
  io.ready := !sending

  when(!sending) {
    when(io.valid) {
      sending := True
      bits := B(1, 1 bits) ## io.data ## B(0, 1 bits)
      idx := 0
      clkDiv := cyclesPerBit - 1
      tx := False
    }
  } otherwise {
    clkDiv := clkDiv - 1
    when(clkDiv === 0) {
      clkDiv := cyclesPerBit - 1
      when(idx === 9) {
        sending := False
        tx := True
      } otherwise {
        idx := idx + 1
        tx := bits(idx + U(1, idx.getWidth bits))
      }
    }
  }
}

/** TangCore BL616 UART: 2 Mbps framed `0xAA len[15:0] payload`. Command 0x09 is
  * USB HID (`hid1`/`hid2`, SNES layout in the low 12 bits). 0x01/0x02 get a
  * core-id / config-string reply so firmware keeps talking.
  */
case class Bl616Link(cyclesPerBit: Int = Bl616Link.cyclesPerBit()) extends Component {
  val io = new Bundle {
    val uartRx = in Bool ()
    val uartTx = out Bool ()
    val hid1 = out Bits (16 bits)
    val hid2 = out Bits (16 bits)
    val joy1 = in Bits (16 bits)
    val joy2 = in Bits (16 bits)
  }

  val rx = UartRx(cyclesPerBit)
  val tx = UartTx(cyclesPerBit)
  rx.io.rx := io.uartRx
  io.uartTx := tx.io.tx

  val hid1 = Reg(Bits(16 bits)) init (0)
  val hid2 = Reg(Bits(16 bits)) init (0)
  io.hid1 := hid1
  io.hid2 := hid2

  object RxState extends SpinalEnum {
    val idle, len1, len2, cmd, param, respReq = newElement()
  }
  val rxState = RegInit(RxState.idle)
  val cmdReg = Reg(UInt(8 bits)) init (0)
  val lenReg = Reg(UInt(16 bits)) init (0)
  val dataCnt = Reg(UInt(16 bits)) init (0)
  val wantCoreId = RegInit(False)
  val wantConf = RegInit(False)

  when(rx.io.valid) {
    switch(rxState) {
      is(RxState.idle) {
        when(rx.io.data === 0xaa) {
          rxState := RxState.len1
        }
      }
      is(RxState.len1) {
        lenReg(15 downto 8) := rx.io.data.asUInt
        when(rx.io.data.asUInt < 8) {
          rxState := RxState.len2
        } otherwise {
          rxState := RxState.idle
        }
      }
      is(RxState.len2) {
        lenReg(7 downto 0) := rx.io.data.asUInt
        rxState := RxState.cmd
      }
      is(RxState.cmd) {
        cmdReg := rx.io.data.asUInt
        dataCnt := 0
        when(rx.io.data === 1 || rx.io.data === 2) {
          rxState := RxState.respReq
        } elsewhen (lenReg > 1) {
          rxState := RxState.param
        } otherwise {
          rxState := RxState.idle
        }
      }
      is(RxState.param) {
        dataCnt := dataCnt + 1
        when(dataCnt + 2 === lenReg) {
          rxState := RxState.idle
        }
        when(cmdReg === 9) {
          when(dataCnt === 0) { hid1(15 downto 8) := rx.io.data }
          when(dataCnt === 1) { hid1(7 downto 0) := rx.io.data }
          when(dataCnt === 2) { hid2(15 downto 8) := rx.io.data }
          when(dataCnt === 3) { hid2(7 downto 0) := rx.io.data }
        }
      }
      is(RxState.respReq) {
        // handled below with rx.valid already consumed
      }
    }
  }

  when(rxState === RxState.respReq) {
    when(cmdReg === 1) {
      wantCoreId := True
    } elsewhen (cmdReg === 2) {
      wantConf := True
    }
    rxState := RxState.idle
  }

  val confBytes = Vec(Bl616Link.confStr.map(c => B(c.toInt & 0xff, 8 bits)))
  val joyInterval = cyclesPerBit * 40000
  val joyTimer = Reg(UInt(log2Up(joyInterval + 1) bits)) init (0)
  val joy1Reg = Reg(Bits(16 bits)) init (0)
  val joy2Reg = Reg(Bits(16 bits)) init (0)

  object TxState extends SpinalEnum {
    val idle, header, coreId, conf, joy = newElement()
  }
  val txState = RegInit(TxState.idle)
  val txIdx = Reg(UInt(8 bits)) init (0)
  val frameLen = Reg(UInt(16 bits)) init (0)
  val txType = Reg(UInt(8 bits)) init (0)
  val txValid = RegInit(False)
  val txData = Reg(Bits(8 bits)) init (0)

  tx.io.valid := txValid
  tx.io.data := txData
  when(txValid && tx.io.ready) {
    txValid := False
  }

  when(joyTimer =/= 0) {
    joyTimer := joyTimer - 1
  }

  val canTx = tx.io.ready && !txValid
  switch(txState) {
    is(TxState.idle) {
      txIdx := 0
      when(wantCoreId) {
        wantCoreId := False
        txType := 1
        frameLen := 2
        txState := TxState.header
      } elsewhen (wantConf) {
        wantConf := False
        txType := 2
        frameLen := Bl616Link.confStr.length + 1
        txState := TxState.header
      } elsewhen (joyTimer === 0 && (io.joy1 =/= joy1Reg || io.joy2 =/= joy2Reg)) {
        joyTimer := joyInterval
        joy1Reg := io.joy1
        joy2Reg := io.joy2
        txType := 3
        frameLen := 5
        txState := TxState.header
      }
    }
    is(TxState.header) {
      when(canTx) {
        txValid := True
        switch(txIdx(1 downto 0)) {
          is(0) { txData := 0xaa }
          is(1) { txData := frameLen(15 downto 8).asBits }
          is(2) { txData := frameLen(7 downto 0).asBits }
          is(3) {
            txData := txType.asBits
            txIdx := 0
            switch(txType) {
              is(1) { txState := TxState.coreId }
              is(2) { txState := TxState.conf }
              default { txState := TxState.joy }
            }
          }
        }
        when(txIdx(1 downto 0) =/= 3) {
          txIdx := txIdx + 1
        }
      }
    }
    is(TxState.coreId) {
      when(canTx) {
        txValid := True
        txData := B(Bl616Link.coreId, 8 bits)
        txState := TxState.idle
      }
    }
    is(TxState.conf) {
      when(canTx) {
        txValid := True
        txData := confBytes(txIdx.resized)
        when(txIdx === Bl616Link.confStr.length - 1) {
          txState := TxState.idle
        } otherwise {
          txIdx := txIdx + 1
        }
      }
    }
    is(TxState.joy) {
      when(canTx) {
        txValid := True
        switch(txIdx(1 downto 0)) {
          is(0) { txData := joy1Reg(15 downto 8) }
          is(1) { txData := joy1Reg(7 downto 0) }
          is(2) { txData := joy2Reg(15 downto 8) }
          is(3) { txData := joy2Reg(7 downto 0) }
        }
        when(txIdx === 3) {
          txState := TxState.idle
        } otherwise {
          txIdx := txIdx + 1
        }
      }
    }
  }
}
