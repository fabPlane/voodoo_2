package voodoo.diff

import spinal.core._
import voodoo.Config

/** AfixOps: one micro-component exercising every `AFix` operation the Voodoo core uses, on the
  * named Q formats from `Config.voodoo1()`.
  *
  * Purpose: Phase 0.2 of docs/amaranth-rewrite-plan.md. `avoodoo.lib.fixed` must reproduce AFix
  * width growth, rounding and saturation bit-exactly; this module is the Spinal reference for the
  * op-by-op differential test. Every operation is purely combinational, so the diff driver can
  * sweep random operands with no handshake.
  *
  * Naming: all ports are declared at component level (no `io` Bundle), so Spinal emits the Scala
  * `val` name verbatim as the Verilog port name.
  *
  * Encoding contract: an AFix value of format `Q(width, fraction, signed)` is carried on a port of
  * exactly `width` bits holding the raw two's-complement (signed) or unsigned integer; the implied
  * scale is 2^-fraction. Output widths are whatever AFix computed during elaboration, which is the
  * thing under test — read them from the generated Verilog / the committed port map JSON
  * (fpga/src/voodoo/diff/portmaps/AfixOps.json), do not assume.
  *
  * Formats covered (all from ext/SpinalVoodoo/src/voodoo/Config.scala unless noted):
  *   vertex      SQ(16, 4)   12.4  screen coordinates
  *   vColor      SQ(24, 12)  12.12 iterated colour
  *   vDepth      SQ(32, 12)  20.12 iterated depth
  *   coefficient SQ(34, 8)   edge coefficients
  *   texCoords   SQ(32, 18)  14.18 s/t
  *   w           SQ(32, 30)  2.30  1/w
  *   texHi       SQ(46, 30)  wide s/t accumulator (texCoordsHiFormat)
  *   texAccum    SQ(48, 18)  s/t gradient out (texCoordsAccumFormat)
  *   wAccum      SQ(48, 30)  w accumulator
  *   colorMul    Q(19, 8, signed) ColorCombine.colorMulFormat
  *   u8          UQ(8, 0)    ColorCombine final clamp target
  *   ufrac8      UQ(0, 8)    ColorCombine blend factor
  */
case class AfixOps(c: Config = Config.voodoo1()) extends Component {
  import AfixOps.FormatSpec

  // --------------------------------------------------------------------------------------------
  // Format table. `key` is the port-name stem; every input port is `a_<key>` / `b_<key>`.
  // --------------------------------------------------------------------------------------------
  val formats: Seq[FormatSpec] = Seq(
    FormatSpec("vertex", c.vertexFormat), //      SQ(16, 4)
    FormatSpec("vcolor", c.vColorFormat), //      SQ(24, 12)
    FormatSpec("vdepth", c.vDepthFormat), //      SQ(32, 12)
    FormatSpec("coeff", c.coefficientFormat), //  SQ(34, 8)
    FormatSpec("tex", c.texCoordsFormat), //      SQ(32, 18)
    FormatSpec("w", c.wFormat), //                SQ(32, 30)
    FormatSpec("texhi", c.texCoordsHiFormat), //  SQ(46, 30)
    FormatSpec("texaccum", c.texCoordsAccumFormat), // SQ(48, 18)
    FormatSpec("waccum", c.wAccumFormat), //      SQ(48, 30)
    FormatSpec("colormul", AfixOps.colorMulFormat), // Q(19, 8, true)
    FormatSpec("u8", UQ(8, 0)) //                 UQ(8, 0)
    // NOTE: UQ(0, 8) (ColorCombine's blend factor as a QFormat) is deliberately absent.
    // `AFix(UQ(0, 8))` elaborates to a ZERO-bit value in SpinalHDL 1.15: QFormat.width is the
    // total width, so nonFraction = 0 - 8 = -8 and the raw Bits ends up 0 wide. The core only
    // ever names UQ(0, 8) as a `fixTo` target (ColorCombine.clampAFixToFactor, which is dead
    // code for the same reason); the live factor format is AFix.UQ(1 bits, 8 bits) == UQ(9, 8),
    // exercised in section 7 below.
  )

  /** Build an input port sized to `AFix(fmt).raw` and reinterpret it as an AFix of that format.
    *
    * The port width is taken from the elaborated AFix, never from `QFormat.width`: Spinal's
    * QFormat -> AFix mapping is not symmetric between signed and unsigned formats, and the raw
    * width is exactly what the Amaranth port has to reproduce.
    */
  private def mkIn(name: String, fmt: QFormat): AFix = {
    val a = AFix(fmt)
    val w = a.raw.getWidth
    if (a.signed) {
      val p = in(SInt(w bits)).setName(name)
      a.assignFromBits(p.asBits)
    } else {
      val p = in(UInt(w bits)).setName(name)
      a.assignFromBits(p.asBits)
    }
    a
  }

  /** Publish an AFix result on a plain SInt/UInt port sized to the AFix's own raw width.
    *
    * Zero-width results (see the UQ(0, 8) note above) are skipped: Verilog has no 0-bit port.
    */
  private def emit(name: String, v: AFix): Unit = {
    if (v.raw.getWidth == 0) {
      SpinalInfo(s"AfixOps: skipping zero-width result '$name'")
    } else if (v.signed) {
      val p = out(SInt(v.raw.getWidth bits)).setName(name)
      p := v.raw.asSInt
    } else {
      val p = out(UInt(v.raw.getWidth bits)).setName(name)
      p := v.raw.asUInt
    }
  }

  private def emitS(name: String, v: SInt): Unit = {
    val p = out(SInt(v.getWidth bits)).setName(name)
    p := v
  }

  private def emitU(name: String, v: UInt): Unit = {
    val p = out(UInt(v.getWidth bits)).setName(name)
    p := v
  }

  // --------------------------------------------------------------------------------------------
  // Inputs: two operands per format.
  // --------------------------------------------------------------------------------------------
  val ops: Map[String, (AFix, AFix)] = formats.map { f =>
    f.key -> (mkIn(s"a_${f.key}", f.fmt), mkIn(s"b_${f.key}", f.fmt))
  }.toMap

  // --------------------------------------------------------------------------------------------
  // 1. Arithmetic: +, -, * — width growth is the thing under test.
  //    add/sub grow the integer part by one bit, mul sums both widths.
  // --------------------------------------------------------------------------------------------
  formats.foreach { f =>
    val (a, b) = ops(f.key)
    emit(s"add_${f.key}", a + b)
    emit(s"sub_${f.key}", a - b)
    emit(s"mul_${f.key}", a * b)
    emit(s"neg_${f.key}", -a)
  }

  // --------------------------------------------------------------------------------------------
  // 2. fixTo back to the source format, once per rounding mode the core uses.
  //    `.fixTo(fmt)` (no RoundType) is the form used ~52 times in the core; the explicit modes
  //    cover .floor / .ceil / .roundHalfDown / .round as fixTo arguments.
  // --------------------------------------------------------------------------------------------
  formats.foreach { f =>
    val (a, b) = ops(f.key)
    val sum = a + b
    emit(s"fixto_default_${f.key}", sum.fixTo(f.fmt))
    emit(s"fixto_floor_${f.key}", sum.fixTo(f.fmt, RoundType.FLOOR))
    emit(s"fixto_ceil_${f.key}", sum.fixTo(f.fmt, RoundType.CEIL))
    emit(s"fixto_roundhalfdown_${f.key}", sum.fixTo(f.fmt, RoundType.ROUNDDOWN))
    emit(s"fixto_roundhalfup_${f.key}", sum.fixTo(f.fmt, RoundType.ROUNDUP))
    emit(s"fixto_roundtoeven_${f.key}", sum.fixTo(f.fmt, RoundType.ROUNDTOEVEN))
    emit(s"fixto_scrap_${f.key}", sum.fixTo(f.fmt, RoundType.SCRAP))
  }

  // --------------------------------------------------------------------------------------------
  // 3. Standalone rounding to an integer exponent (`.floor(0)` / `.ceil(0)` / `.roundHalfDown(0)`),
  //    exactly as TriangleSetup / SpanWalker / PixelPipeline call them, plus the `.asSInt` /
  //    `.asUInt` reinterpretation that always follows.
  // --------------------------------------------------------------------------------------------
  formats.filter(_.fmt.signed).foreach { f =>
    val (a, _) = ops(f.key)
    emit(s"floor0_${f.key}", a.floor(0))
    emit(s"ceil0_${f.key}", a.ceil(0))
    emit(s"roundhalfdown0_${f.key}", a.roundHalfDown(0))
    emit(s"roundhalfup0_${f.key}", a.roundHalfUp(0))
    emit(s"round0_${f.key}", a.round(0, false))
    emit(s"floortozero0_${f.key}", a.floorToZero(0))
    emit(s"ceiltoinf0_${f.key}", a.ceilToInf(0))
    emitS(s"floor0_assint_${f.key}", a.floor(0).asSInt)
  }

  // --------------------------------------------------------------------------------------------
  // 4. Saturation. Three call shapes appear in the core:
  //      .sat(n)                          RegisterBank: drop n MSBs with saturation
  //      .sat(satMax, satMin)             ColorCombine: clamp to 0..255 at the value's own exp
  //      .sat(satMax, satMin, exp)        ColorCombine: clamp to 0..255 scaled by 2^exp
  // --------------------------------------------------------------------------------------------
  formats.foreach { f =>
    val (a, _) = ops(f.key)
    emit(s"sat1_${f.key}", a.sat(1))
    emit(s"sat255_${f.key}", a.sat(255, 0))
    emit(s"sat255e0_${f.key}", a.sat(255, 0, ExpNumber(0)))
  }

  // --------------------------------------------------------------------------------------------
  // 5. `.truncated`: assignment-side truncation tag (TriangleSetup edge accumulation). A wide
  //    product is assigned into a narrow declared format without an explicit fixTo.
  // --------------------------------------------------------------------------------------------
  {
    val (av, bv) = ops("vertex")
    val (ac, _) = ops("coeff")
    val wide = ac * av + ac * bv + ac
    val narrow = AFix(c.coefficientFormat)
    narrow := wide.truncated
    emit("truncated_coeff", narrow)
    emit("truncated_src", wide)
  }

  // --------------------------------------------------------------------------------------------
  // 6. Cross-format fixTo pairs taken verbatim from the core.
  // --------------------------------------------------------------------------------------------
  {
    // Rasterizer: state.hiS (SQ(46,30)) + stepX.tex (SQ(32,18)) -> fixTo(texCoordsHiFormat)
    val (hi, _) = ops("texhi")
    val (step, _) = ops("tex")
    emit("raster_histep_fixto_texhi", (hi + step).fixTo(c.texCoordsHiFormat))
    // Rasterizer: output.grads.sGrad := state.hiS.fixTo(texCoordsAccumFormat)
    emit("raster_hi_fixto_texaccum", hi.fixTo(c.texCoordsAccumFormat))

    // SpanWalker / Rasterizer: coords(0) + one -> fixTo(vertexFormat)
    val (x, _) = ops("vertex")
    val one = AFix(c.vertexFormat)
    one := 1.0
    emit("raster_x_plus_one_fixto_vertex", (x + one).fixTo(c.vertexFormat))
    // TriangleSetup: xrange0 = xminRaw.floor(0).fixTo(vertexFormat), xrange1 = ceil(0).fixTo(...)
    emit("setup_floor_fixto_vertex", x.floor(0).fixTo(c.vertexFormat))
    emit("setup_ceil_fixto_vertex", x.ceil(0).fixTo(c.vertexFormat))
    // TriangleSetup: yrange0 = tri(0)(1).roundHalfDown(0).fixTo(vertexFormat)
    emit("setup_roundhalfdown_fixto_vertex", x.roundHalfDown(0).fixTo(c.vertexFormat))
    // TriangleSetup: xCenter = (xrange0 + halfPixel).fixTo(vertexFormat)
    val half = AFix(c.vertexFormat)
    half := 0.5
    emit("setup_halfpixel_fixto_vertex", (x.floor(0).fixTo(c.vertexFormat) + half).fixTo(c.vertexFormat))

    // SpanWalker: nxt := (cur + grad).fixTo(lowGradFormats(idx)) for colour and depth lanes
    val (col, colGrad) = ops("vcolor")
    emit("span_color_fixto_vcolor", (col + colGrad).fixTo(c.vColorFormat))
    val (dep, depGrad) = ops("vdepth")
    emit("span_depth_fixto_vdepth", (dep + depGrad).fixTo(c.vDepthFormat))
    val (wa, wg) = ops("waccum")
    emit("span_w_fixto_waccum", (wa + wg).fixTo(c.wAccumFormat))
    // SpanWalker: nxt := (cur + dy - dx).fixTo(fmt), and the (-grad).fixTo(fmt) negate path
    emit("span_dydx_fixto_vcolor", (col + colGrad - colGrad).fixTo(c.vColorFormat))
    emit("span_neg_fixto_vcolor", (-colGrad).fixTo(c.vColorFormat))
    emit("span_neg_fixto_coeff", (-ops("coeff")._2).fixTo(c.coefficientFormat))
  }

  // --------------------------------------------------------------------------------------------
  // 7. ColorCombine's exact chain: SQ(10,0) * UQ(1,8) -> fixTo(colorMulFormat),
  //    then + SQ(10,8), then sat(255, 0, 0 exp) -> fixTo(UQ(8,0)) / fixTo(UQ(0,8)).
  // --------------------------------------------------------------------------------------------
  {
    val sub = mkIn("a_cc_sub", SQ(11, 0)) // Color.s9 widened: AFix.SQ(10 bits, 0 bits)
    val factor = mkIn("a_cc_factor", UQ(9, 8)) // AFix.UQ(1 bits, 8 bits)
    val local = mkIn("a_cc_local", SQ(19, 8)) // AFix.SQ(10 bits, 8 bits)

    val mul = (sub * factor).fixTo(AfixOps.colorMulFormat)
    emit("cc_mul_fixto_colormul", mul)
    val add = (mul + local).fixTo(AfixOps.colorMulFormat)
    emit("cc_add_fixto_colormul", add)
    val saturated = add.sat(255, 0, ExpNumber(0))
    emit("cc_sat255", saturated)
    emit("cc_sat_fixto_u8", saturated.fixTo(UQ(8, 0)))
    emitU("cc_sat_fixto_u8_asuint", saturated.fixTo(UQ(8, 0)).asUInt)
    // ColorCombine.clampAFixToFactor: `.sat(255, 0).fixTo(UQ(0, 8))`. `sat(255, 0)` clamps the
    // RAW value to 0..255 at the source exponent (8 bits, exp -8 here); the following
    // fixTo(UQ(0, 8)) then produces a zero-width AFix, so only the saturation half is portable.
    emit("cc_sat255_raw", add.sat(255, 0))
  }

  // --------------------------------------------------------------------------------------------
  // 8. RegisterBank: `io.pciFifoFree.sat(1)` — UInt (not AFix) saturating shrink.
  // --------------------------------------------------------------------------------------------
  {
    val pciFifoFree = in(UInt(7 bits)).setName("a_pcififofree")
    emitU("regbank_sat1", pciFifoFree.sat(1))
  }
}

object AfixOps {

  /** ColorCombine.scala:226 — `QFormat(19, 8, true)`: SQ(10,0) * UQ(0,8) result format. */
  val colorMulFormat: QFormat = QFormat(19, 8, true)

  case class FormatSpec(key: String, fmt: QFormat)
}
