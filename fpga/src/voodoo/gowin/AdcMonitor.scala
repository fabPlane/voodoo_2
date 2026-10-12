package voodoo.gowin

import spinal.core._
import spinal.lib._

/** Gowin_ADC_Vcc / Gowin_ADC_Temp: ADCULC (voltage mode, vtest = VCC / 2)
  * and ADCLRC (temperature mode) wrappers made by GowinModGen, see
  * hw/gowin/generate_adc_ip.sh. Both run from the internal ADC oscillator.
  */
class GowinAdcBlackBox extends BlackBox {
  val io = new Bundle {
    val adcrdy = out Bool ()
    val adcvalue = out Bits (14 bits)
    val drstn = in Bool ()
    val adcreqi = in Bool ()
    val fscal_value = in Bits (10 bits)
    val offset_value = in Bits (12 bits)
  }
  noIoPrefix()
}

/** Samples the GW5AST core supply (VCC) and die temperature continuously.
  *
  * Each ADC is asked for a measurement (rising edge on adcreqi), the result
  * is taken when adcrdy rises (synchronised: the ADC runs on its own
  * oscillator) and the next request follows. Values are the ADC's 14-bit
  * results (voltage unsigned, temperature sign + magnitude): VCC [V] = vcc / 1024 (vtest is VCC / 2, voltage results
  * are / 2048), temperature [degC] = temp / 4. vccMin / tempMax hold the
  * extremes since configuration, which a supply dip between frames would
  * otherwise hide.
  */
case class AdcMonitor() extends Component {
  val io = new Bundle {
    val vcc = out SInt (16 bits)
    val temp = out SInt (16 bits)
    val vccMin = out SInt (16 bits)
    val tempMax = out SInt (16 bits)
    val samples = out UInt (32 bits)
  }

  // Hold the ADCs' digital part in reset for a while after configuration.
  // DRSTN is one hard-wired net shared by ADCULC and ADCLRC (PnR rejects two
  // drivers), so both get the same signal.
  val resetCnt = Reg(UInt(12 bits)) init (0)
  val running = resetCnt.andR
  when(!running) { resetCnt := resetCnt + 1 }

  // UG299 2.3.1: a rising edge on adcreqi starts a conversion; adcrdy falls
  // within 10 ADC clocks and rises 128-2048 ADC clocks later (internal OSC,
  // 2.5 MHz) with the result, then stays high until the next request.
  private def channel(adc: GowinAdcBlackBox, fscal: Int, offset: Int, signed: Boolean) = new Area {
    adc.io.fscal_value := B(fscal, 10 bits)
    adc.io.offset_value := S(offset, 12 bits).asBits
    adc.io.drstn := running

    val rdy = BufferCC(adc.io.adcrdy, init = False)
    val rdyRise = rdy && !RegNext(rdy, init = False)
    val req = RegInit(False)
    adc.io.adcreqi := req
    // Voltage results are unsigned; temperature is sign (bit 13) + magnitude.
    val raw = BufferCC(adc.io.adcvalue, init = B(0, 14 bits))
    val decoded = SInt(16 bits)
    if (signed) decoded := Mux(raw(13), -raw(12 downto 0).asUInt.resize(16).asSInt, raw(12 downto 0).asUInt.resize(16).asSInt)
    else decoded := raw.asUInt.resize(16).asSInt
    val value = Reg(SInt(16 bits)) init (0)
    val fresh = Bool()
    fresh := False

    object St extends SpinalEnum { val idle, waitLow, waitHigh, settle = newElement() }
    val st = RegInit(St.idle)
    val timer = Reg(UInt(22 bits)) init (0)
    timer := timer + 1
    switch(st) {
      is(St.idle) {
        when(running && timer(10)) { req := True; timer := 0; st := St.waitLow }
      }
      is(St.waitLow) { // ready falls: the conversion started
        when(!rdy) { req := False; timer := 0; st := St.waitHigh }
          .elsewhen(timer.msb) { req := False; timer := 0; st := St.idle }
      }
      is(St.waitHigh) {
        when(rdyRise) { timer := 0; st := St.settle }
          .elsewhen(timer.msb) { timer := 0; st := St.idle }
      }
      is(St.settle) { // let the synchronised value catch up
        when(timer(3)) {
          value := decoded
          fresh := True
          timer := 0
          st := St.idle
        }
      }
    }
  }

  val vccAdc = new GowinAdcBlackBox
  vccAdc.setDefinitionName("Gowin_ADC_Vcc")
  val tempAdc = new GowinAdcBlackBox
  tempAdc.setDefinitionName("Gowin_ADC_Temp")
  val vccCh = channel(vccAdc, fscal = 653, offset = 0, signed = false)
  val tempCh = channel(tempAdc, fscal = 730, offset = -1180, signed = true)

  val vccMin = Reg(SInt(16 bits)) init (S(0x7fff, 16 bits))
  val tempMax = Reg(SInt(16 bits)) init (S(-0x8000, 16 bits))
  val samples = Reg(UInt(32 bits)) init (0)
  when(vccCh.fresh) {
    samples := samples + 1
    when(vccCh.decoded < vccMin) { vccMin := vccCh.decoded }
  }
  when(tempCh.fresh && tempCh.decoded > tempMax) { tempMax := tempCh.decoded }

  io.vcc := vccCh.value
  io.temp := tempCh.value
  io.vccMin := vccMin
  io.tempMax := tempMax
  io.samples := samples
}
