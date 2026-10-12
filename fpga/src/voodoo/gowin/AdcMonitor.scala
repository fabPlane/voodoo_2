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
  * oscillator) and the next request follows. Values are the ADC's signed
  * 14-bit results: VCC [V] = vcc / 1024 (vtest is VCC / 2, voltage results
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

  private def channel(adc: GowinAdcBlackBox, fscal: Int, offset: Int) = new Area {
    adc.io.fscal_value := B(fscal, 10 bits)
    adc.io.offset_value := S(offset, 12 bits).asBits
    // Hold the digital part in reset for a while after configuration.
    val resetCnt = Reg(UInt(12 bits)) init (0)
    val running = resetCnt.andR
    when(!running) { resetCnt := resetCnt + 1 }
    adc.io.drstn := running

    val rdy = BufferCC(adc.io.adcrdy, init = False)
    val rdyRise = rdy && !RegNext(rdy, init = False)
    val req = RegInit(False)
    adc.io.adcreqi := req
    val value = Reg(SInt(16 bits)) init (0)
    val fresh = Bool()
    fresh := False
    // Request, wait for ready, take the value (two synchronised reads that
    // agree, as adcvalue crosses clock domains), drop the request, repeat.
    val gap = Reg(UInt(8 bits)) init (0)
    when(running) {
      when(!req && !rdy) {
        gap := gap + 1
        when(gap.andR) { req := True }
      }
      when(req && rdyRise) {
        req := False
      }
    }
    val v0 = RegNext(adc.io.adcvalue)
    val v1 = RegNext(v0)
    val take = RegNext(rdyRise, init = False)
    when(take && v0 === v1) {
      value := v1.asSInt.resize(16)
      fresh := True
    }
  }

  val vccAdc = new GowinAdcBlackBox
  vccAdc.setDefinitionName("Gowin_ADC_Vcc")
  val tempAdc = new GowinAdcBlackBox
  tempAdc.setDefinitionName("Gowin_ADC_Temp")
  val vccCh = channel(vccAdc, fscal = 653, offset = 0)
  val tempCh = channel(tempAdc, fscal = 730, offset = -1180)

  val vccMin = Reg(SInt(16 bits)) init (S(0x7fff, 16 bits))
  val tempMax = Reg(SInt(16 bits)) init (S(-0x8000, 16 bits))
  val samples = Reg(UInt(32 bits)) init (0)
  when(vccCh.fresh) {
    samples := samples + 1
    when(vccCh.value < vccMin) { vccMin := vccCh.value }
  }
  when(tempCh.fresh && tempCh.value > tempMax) { tempMax := tempCh.value }

  io.vcc := vccCh.value
  io.temp := tempCh.value
  io.vccMin := vccMin
  io.tempMax := tempMax
  io.samples := samples
}
