# SpinalHDL `AFix` semantics as used by SpinalVoodoo

Research output of **Phase 0 task 0.2** of `docs/amaranth-rewrite-plan.md`.
Written so that `avoodoo/lib/fixed.py` can be implemented bit-exactly **without
reading the Scala**. Status: draft v1, 2026-09-20.

Sources of truth:

- SpinalHDL **1.15.0** sources (`spinalhdl-core_2.12-1.15.0-sources.jar`,
  fetched from Maven Central): `spinal/core/AFix.scala` (1314 lines),
  `spinal/core/UInt.scala`, `spinal/core/SInt.scala`,
  `spinal/core/FixedPoint.scala`, `spinal/core/QFormat.scala`,
  `spinal/core/Trait.scala` (`trait Num`).
  `fpga/project.scala` pins 1.15.0 and compiles the submodule sources with it,
  so 1.15.0 is the version that produces today's bitstreams. (The submodule's
  own `project.scala` says 1.13.0; the only `AFix.scala` differences between
  1.13.0 and 1.15.0 are `AFix.U/S(width, resolution)` overloads that the core
  does not use, and `resize()` gaining a `newExp == exp` no-op path. Nothing
  else differs.)
- The core: `ext/SpinalVoodoo/src/voodoo/**` (14 files touch AFix).
- Cross-checks against the committed Verilog `fpga/rtl/artix/CoreArtix.v`
  (signal widths quoted below are from that file).

Everything numeric in this document was reproduced with a Python model of
`AFix` and verified against the generated Verilog where a width was visible.

---

## 1. The data model

```scala
class AFix(val maxRaw: BigInt, val minRaw: BigInt, val exp: Int)
    extends MultiData with Num[AFix] with BitwiseOp[AFix]
```

An `AFix` is **a raw two's-complement bit vector plus a statically known,
arbitrary integer value range**. It is *not* just an (integer bits, fraction
bits) pair:

- `maxRaw`, `minRaw`: integers. The represented value range is
  `[minRaw * 2^exp, maxRaw * 2^exp]`. These are **arbitrary integers**, not
  necessarily `2^k - 1` / `-2^k`.
- `exp`: the weight of the LSB is `2^exp`. Negative `exp` ⇒ fractional bits.
- `raw: Bits(bitWidth bits)`: the storage. Interpreted as **signed** iff
  `signed`.

Derived fields, verbatim from `AFix.scala:133-162`:

```scala
val signed    = (maxRaw < 0) || (minRaw < 0)
val signWidth = if (signed) 1 else 0

private val maxShifted = maxRaw.abs - (if (maxRaw < 0) signWidth else 0)
private val maxBits    = maxShifted.bitLength
private val minShifted = minRaw.abs - (if (minRaw < 0) signWidth else 0)
private val minBits    = minShifted.bitLength

val bitWidth   = Math.max(maxBits, minBits) + signWidth
val fracWidth  = Math.max(-exp, 0)
val wholeWidth = bitWidth - fracWidth - signWidth
val intWidth   = bitWidth - fracWidth
val numWidth   = bitWidth - signWidth
val maxExp     = exp + numWidth
val leftExp    = exp + bitWidth

private val maxRepr: BigInt = BigInt(2).pow(numWidth) - 1
private val minRepr: BigInt = if (signed) -BigInt(2).pow(numWidth) else 0
```

`BigInt.bitLength` is the number of bits excluding sign, i.e. `0` for `0`,
`floor(log2(n)) + 1` for `n > 0`. Note `bitWidth` can be **0** (a constant-zero
`AFix`; this actually happens in the core, see §8.1).

Consequences the port must honour:

1. `signed` is decided **only** by whether either bound is negative. An `AFix`
   with `minRaw == 0` is unsigned even if it came from a signed computation.
2. `bitWidth` is derived from the *bounds*, so the representable range of the
   bit vector is usually **wider** than `[minRaw, maxRaw]`. Example: `a + b`
   for two `SQ(16,4)` gives `maxRaw = 65534`, `minRaw = -65536`, `bitWidth =
   17`, while a 17-bit signed vector could hold `65535`.
3. Saturation and comparisons use the **bounds**, not the representable range.
   Non-power-of-two bounds are normal (e.g. `2048 / -2048`, `522753 /
   -523264`), and they change which `sat()` clamps are emitted.

### 1.1 `QFormat` (`spinal/core/QFormat.scala`)

```scala
object UQ { def apply(width: Int, fraction: Int): QFormat = new QFormat(width, fraction, false) }
object SQ { def apply(width: Int, fraction: Int): QFormat = new QFormat(width, fraction, true ) }
case class QFormat(width: Int, fraction: Int, signed: Boolean)
```

`width` is the **total** bit width including the sign bit. `SQ(16,4)` = 16 bits
total, 4 fractional, 1 sign, 11 whole. The core's `Config` fields are
`QFormat`s; `ColorCombine` also builds `QFormat(19, 8, true)` directly.

`AFix(Q: QFormat)` (`AFix.scala:76-81`):

```scala
val integerWidth  = if (Q.signed) Q.width - Q.fraction - 1 else Q.width - Q.fraction
val fractionWidth = -Q.fraction
AFix(integerWidth exp, fractionWidth exp, Q.signed)
```

which lands on `maxRaw = 2^(width - signed) - 1`, `minRaw = -2^(width - signed)`
or `0`, `exp = -fraction`, `bitWidth = width`.

**Beware**: `UQ(0, 8)` is `QFormat(width = 0, fraction = 8)` — a *zero-bit*
format, not "0 integer bits, 8 fraction bits". The `AFix.UQ`/`AFix.SQ`
*factories* take `BitCount`s and mean something different (§2).

### 1.2 `RoundType` (`spinal/core/FixedPoint.scala:29-42`)

```scala
sealed trait RoundType
object RoundType {
  case object CEIL        extends RoundType // Wikipedia: RoundUp          (→ +inf)
  case object FLOOR       extends RoundType // Wikipedia: RoundDown        (→ -inf)
  case object FLOORTOZERO extends RoundType // RoundToZero
  case object CEILTOINF   extends RoundType // RoundToInf
  case object ROUNDUP     extends RoundType // RoundHalfUp    (.roundHalfUp)
  case object ROUNDDOWN   extends RoundType // RoundHalfDown  (.roundHalfDown)
  case object ROUNDTOZERO extends RoundType // RoundHalfToZero
  case object ROUNDTOINF  extends RoundType // RoundHalfToInf
  case object ROUNDTOEVEN extends RoundType // RoundHalfToEven
  case object ROUNDTOODD  extends RoundType // RoundHalfToOdd
  case object SCRAP       extends RoundType // sticky-OR into the LSB
}
```

**The core uses exactly four: `FLOOR`, `CEIL`, `ROUNDDOWN`, and `FLOOR` again
as the global default.** `ROUNDUP` appears nowhere; neither do the
to-zero/to-inf/even/odd/scrap variants. (`.round`, `Num.round` and
`AFix.round(bits: BitCount, …)` are also unused.)

### 1.3 Global rounding/saturation policy

```scala
object AFixRounding extends ScopeProperty[TagAFixTruncated] {
  override def default: TagAFixTruncated = new TagAFixTruncated(false, false, RoundType.FLOOR)
}
private def getTrunc: TagAFixTruncated =
  this.getTag(classOf[TagAFixTruncated]).getOrElse(AFixRounding.get)
```

`grep -rn "AFixRounding\|FixPointProperty\|FixPointConfig"` over
`ext/SpinalVoodoo/src` and `fpga/src` returns **nothing**, so the default is in
force everywhere:

| default | value |
|---|---|
| `rounding` | `RoundType.FLOOR` |
| `saturation` (used as the `align` argument of `ceil`/`round*`) | `false` |
| `overflow` | `false` |

So every implicit rounding in the core is **floor**, and every `ceil`/`round*`
is called with `align = false` (no `sat(1)` post-step — see §6.3).

---

## 2. Construction forms used by the core

| Form | Result `(maxRaw, minRaw, exp)` | Used in |
|---|---|---|
| `AFix(Q: QFormat)` | see §1.1 | everywhere (`Bundles`, `Rasterizer`, `SpanWalker`, `TriangleSetup`, `Tmu`, `Fog`, `ColorCombine`, `RegisterBank`) |
| `AFix.SQ(i: BitCount, f: BitCount)` = `AFix(i exp, -f exp, signed=true)` | `(2^(i+f)-1, -2^(i+f), -f)`, width `i+f+1` | `ColorCombine` (`SQ(10 bits, 0 bits)`), `TriangleSetup` (`SQ(3 bits, 4 bits)`), `Fpxx2AFix` |
| `AFix.UQ(i: BitCount, f: BitCount)` = `AFix(i exp, -f exp, signed=false)` | `(2^(i+f)-1, 0, -f)`, width `i+f` | `ColorCombine` (`UQ(0 bits, 8 bits)`, `UQ(1 bits, 8 bits)`), `Bundles.Color.ufactor()` |
| `AFix(s: SInt)` | `(2^(w-1)-1, -2^(w-1), 0)`, `raw := s.asBits` | `ColorCombine` stage 2/6 (`AFix(zero - local)`, `AFix(n6(A_LOCAL))`) |
| `<Double>.SQ(i bits, f bits)` literal (`core.scala:233`) → `AF(value, i, f, signed)` | format as `AFix.SQ`, raw set to `round(value * 2^f)` toward zero | `SpanWalker`: `val zero = 0.SQ(1 bits, 0 bits)` ⇒ `(1, -2, 0)`, 2 bits |
| `x.raw := <bits>` after constructing the format | format unchanged | `TriangleSetup` (`edgeBiasLsb.raw := 1`), `SpanWalker.pixelToVertex`, `BmbBusInterface.field`, `ColorCombine.invertFactor` |
| `x := <BigDecimal>` | raw `= value * 2^-exp` (see `AFix.scala:1240-1251`) | `SpanWalker` (`one := 1.0`, `negOne := -1.0`), `TriangleSetup` (`halfPixel := 0.5`), `ColorCombine` (`zeroFactor := 0.0`) |
| `afix.assignFromBits(bits)` | raw bit copy (from `MultiData`) | `Fpxx2AFix` output |

**Not used anywhere in the core**: `AFix(u: UInt)`, `AFix(b: Bool)`,
`AFix(UFix/SFix)`, `AFix(num: BigInt)`, `AFix.U(...)`, `AFix.S(...)`,
`.asAFix`, `.toAFix`, `AFix.holding` (directly — it is reached via `Mux`),
`AF(...)` (directly).

---

## 3. Named formats (`ext/SpinalVoodoo/src/voodoo/Config.scala`, `Config.voodoo1()`)

| Config field | QFormat | `maxRaw` | `minRaw` | `exp` | `bitWidth` |
|---|---|---:|---:|---:|---:|
| `vertexFormat` | `SQ(16,4)` | 32767 | -32768 | -4 | 16 |
| `coefficientFormat` | `SQ(34,8)` | 8589934591 | -8589934592 | -8 | 34 |
| `vColorFormat` | `SQ(24,12)` | 8388607 | -8388608 | -12 | 24 |
| `vDepthFormat` | `SQ(32,12)` | 2147483647 | -2147483648 | -12 | 32 |
| `texCoordsFormat` | `SQ(32,18)` | 2147483647 | -2147483648 | -18 | 32 |
| `texCoordsHiFormat` | `SQ(46,30)` | 35184372088831 | -35184372088832 | -30 | 46 |
| `wFormat` | `SQ(32,30)` | 2147483647 | -2147483648 | -30 | 32 |
| `texCoordsAccumFormat` | `SQ(48,18)` | 140737488355327 | -140737488355328 | -18 | 48 |
| `wAccumFormat` | `SQ(48,30)` | 140737488355327 | -140737488355328 | -30 | 48 |

Ad-hoc formats:

| Where | Expression | `(maxRaw, minRaw, exp)` | width |
|---|---|---|---:|
| `ColorCombine` `C_AFTER_SUB` / `A_AFTER_SUB` | `AFix.SQ(10 bits, 0 bits)` | `(1023, -1024, 0)` | 11 |
| `ColorCombine` `C_FACTOR_PRE` / `A_FACTOR_PRE`, `Color.ufactor()` | `AFix.UQ(0 bits, 8 bits)` | `(255, 0, -8)` | 8 |
| `ColorCombine` `C_FACTOR` / `A_FACTOR` | `AFix.UQ(1 bits, 8 bits)` | `(511, 0, -8)` | 9 |
| `ColorCombine` `colorMulFormat` | `QFormat(19, 8, true)` | `(262143, -262144, -8)` | 19 |
| `TriangleSetup` `halfPixel` | `AFix.SQ(3 bits, 4 bits)` | `(127, -128, -4)` | 8 |
| `SpanWalker` `zero` | `0.SQ(1 bits, 0 bits)` | `(1, -2, 0)` | 2 |
| `Fpxx2AFix` output `number` | `AFix.SQ(20 bits, 30 bits)` | `(2^50-1, -2^50, -30)` | 51 |

Verilog cross-checks: `ColorCombine` in `CoreArtix.v` has
`n2_C_AFTER_SUB_r [10:0]`, `n4_C_FACTOR_r [8:0]`, `n5_C_AFTER_MUL_r [18:0]`,
`n6_C_AFTER_ADD_r [18:0]`, `n7_C_CLAMPED_r [7:0]`; `TriangleSetup` has
`o_payload_coeffs_*_a/b/c [33:0]` and `o_payload_edgeStart_* [33:0]` with a
`[50:0]` intermediate.

---

## 4. Operation inventory

Counts are `grep` line counts over `ext/SpinalVoodoo/src/voodoo/**`
(`fpga/src/voodoo/**` contains **no** AFix at all — the Gowin/Artix ports are
AFix-free, so `fixed.py` is only needed for the core).

| Operation | # | Files | Concrete formats |
|---|---:|---|---|
| `AFix(QFormat)` construction | ~90 | `Bundles`, `Rasterizer`, `SpanWalker`, `TriangleSetup`, `Tmu`, `Fog`, `ColorCombine`, `RegisterBank`, `bus/BmbBusInterface` | all of §3 |
| `AFix.SQ/UQ(BitCount, BitCount)` | 13 | `ColorCombine`, `Bundles`, `TriangleSetup`, `Fpxx2AFix` | see §3 |
| `AFix(SInt)` | 8 | `ColorCombine` 414, 416, 420, 422, 526, 531, 543, 547 | `SInt(9 bits)` ⇒ `(255,-256,0)`, 9 bits |
| `+` | ~24 | `SpanWalker` (accumulators), `Rasterizer` (x step), `TriangleSetup` (edge eval), `ColorCombine` (stage 6) | see §5.1 |
| `-` | ~8 | `TriangleSetup` (`v0.y-v1.y`, `v1.x-v0.x`, cross product, edge bias), `SpanWalker` (`cur + b - a`) | see §5.1 |
| `*` | 5 | `TriangleSetup` (`v0.x*v1.y`, `v1.x*v0.y`, `a*xCenter`, `b*yCenter`), `ColorCombine` (`sub * factor`) | `SQ(16,4)*SQ(16,4)`, `SQ(34,8)*SQ(16,4)`, `SQ(11,0)*UQ(9,8)` |
| unary `-` | 5 | `TriangleSetup` 246-248, `SpanWalker` 139, 147 | negation of `SQ(17,4)`, `SQ(32,8)`, `SQ(34,8)`, grad formats |
| `>=`, `>`, `===` | 6 | `SpanWalker.insideTriangle` (`edge >= zero`), `TriangleSetup.isTopLeft` | `SQ(34,8)` vs `SQ(2,0)`; `SQ(34,8)` vs `SQ(34,8)` |
| `.min` / `.max` (from `Num`, = `Mux`) | 2 | `TriangleSetup` 226-227 (`reduceBalancedTree`) | `SQ(16,4)` |
| `Mux(cond, a, b)` on AFix | 3 | `TriangleSetup` 246-248 | mixes `x` and `-x`, see §5.4 |
| `.fixTo(QFormat)` | 53 | `SpanWalker` (23), `ColorCombine` (13), `TriangleSetup` (10), `Rasterizer` (7) | §6.4 |
| `.floor(0)` | 16 | `SpanWalker` (11), `TriangleSetup` (3), `PixelPipeline` (2) | `SQ(16,4) → exp 0` |
| `.ceil(0)` | 1 | `TriangleSetup:229` | `SQ(16,4) → exp 0` |
| `.roundHalfDown(0)` | 4 | `TriangleSetup:230,231,280,281` | `SQ(16,4) → exp 0` |
| `.sat(satMax, satMin, exp)` | 5 | `ColorCombine:105-109` | see §6.5 |
| `.sat(satMax, satMin)` | 2 | `ColorCombine:360, 369` | see §6.5 |
| `.truncated` | 1 | `TriangleSetup:269` (`edgeStartVec(idx) := (…).truncated`) | 51-bit expr → `SQ(34,8)` |
| `.asUInt` | 4 | `PixelPipeline:127,129`, `ColorCombine:371` | note the negative-saturation behaviour, §5.6 |
| `.asSInt` | 13 × `floor(0).asSInt` | `SpanWalker` (×11), `TriangleSetup` (×2); plus `.raw.asSInt` in `Fog`, `FramebufferAccess`, `PciFifo`, `TriangleSetup` | |
| `.raw` (read or drive) | 111 | everywhere | plain `Bits(bitWidth)` |
| `.intWidth` / `.fracWidth` / `.signed` / `.bitWidth` (elaboration-time) | 6 | `BmbBusInterface` (`withFloatAlias`, `field`), `RegisterBank:77` | |
| AFix inside `Bundle`/`Vec`/`Stream`/`Reg`/`Payload` | many | `Bundles.Coefficients/Color`, `Rasterizer.*`, `SpanWalker.Cursor/TriangleConst`, `Tmu.*`, `Fog.Output`, `ColorCombine` Payloads | plain `MultiData` with one element, `raw` |
| `.resize(ExpNumber)` | 0 (reached implicitly by `:=`) | — | §5.5 |
| `.saturated`, `.rounded`, `.round`, `<<`, `>>`, `<<|`, `>>|`, `|<<`, `&`, `\|`, `^`, `~`, `/`, `%`, `.abs`, `.trim`, `.sat(m: Int)`, `.scrap`, `.asAlwaysPositive`, `.isNegative/.isPositive/.isZero`, `.tag`, `.init`, `AFix.holding` | 0 | — | **not needed in `fixed.py` v1** |

(`RegisterBank:207` `io.pciFifoFree.sat(1)` is `UInt.sat`, not AFix.)

---

## 5. Operator semantics (exact)

Throughout, `alignRanges(l, r)` scales the coarser operand's bounds up to the
finer `exp`, and `alignLR(l, r)` shifts the coarser operand's `raw` left by the
same amount (`AFix.scala:180-212`):

```scala
val expDiff = l.exp - r.exp                      // > 0 ⇒ l is coarser
if (expDiff > 0) (l.maxRaw*2^expDiff, l.minRaw*2^expDiff, r.maxRaw, r.minRaw)
else if (expDiff < 0) (l.maxRaw, l.minRaw, r.maxRaw*2^-expDiff, r.minRaw*2^-expDiff)
```

`trim(b, w)` = `b.takeLow(w)` if `b.getWidth > w` else `b` — i.e. **low bits
kept, high bits dropped** (only relevant for mixed-signedness `*`, where it is
always value-safe).

### 5.1 `+`, `-`

```scala
def +(right: AFix): AFix = {
  val (lMax, lMin, rMax, rMin) = alignRanges(this, right)
  val ret = new AFix(lMax+rMax, lMin+rMin, Math.min(this.exp, right.exp))
  ...
}
def -(right: AFix): AFix = {
  val (lMax, lMin, rMax, rMin) = alignRanges(this, right)
  val ret = new AFix(lMax-rMin, lMin-rMax, Math.min(this.exp, right.exp))
  ...
}
```

- `exp_out = min(l.exp, r.exp)`.
- Bounds add (for `-`, cross-subtract). **Width follows from the bounds**, so
  the growth is 1 bit only when the bounds actually need it.
- The raw datapath is a same-width add/sub on `resize(ret.bitWidth)`-ed
  operands (sign- or zero-extended per operand signedness), then `trim` to
  `ret.bitWidth` (a no-op in practice).
- `+^` and `-^` are aliases of `+`/`-`. `+|`/`-|` (saturating-bound variants)
  are unused.

Worked examples from the core:

| Expression | Operands | Result |
|---|---|---|
| `coords(0) + one` (`SpanWalker:136,160`) | `SQ(16,4)` + `SQ(16,4)` | `(65534, -65536, -4)`, **17** bits |
| `edge + coeff.a` (`SpanWalker:140`) | `SQ(34,8)` + `SQ(34,8)` | `(17179869182, -17179869184, -8)`, **35** bits |
| `hiS + dSdX` (`SpanWalker:153`) | `SQ(46,30)` ×2 | `(70368744177662, -70368744177664, -30)`, **47** bits |
| `cur + coeff.b - coeff.a` (`SpanWalker:180`) | `SQ(34,8)` ×3 | `(25769803774, -25769803775, -8)`, **36** bits |
| `v0(1) - v1(1)` (`TriangleSetup:243`) | `SQ(16,4)` ×2 | `(65535, -65535, -4)`, **17** bits |
| `xrange0 + halfPixel` (`TriangleSetup:265`) | `SQ(16,4)` + `SQ(8,4)` | `(32894, -32896, -4)`, **17** bits |
| `mul + localAFix` (`ColorCombine:527`) | `SQ(19,8)` ×2 | `(524286, -524288, -8)`, **20** bits |

Note `-` on identical formats gives `(65535, -65535)`, which is **not** the
symmetric-representable range: 17 bits, `maxRaw` is one below `2^16`.

### 5.2 `*`

```scala
def *(right: AFix): AFix = {
  val (lMax, lMin, rMax, rMin) = (this.maxRaw, this.minRaw, right.maxRaw, right.minRaw)
  val possibleLimits = List(lMax*rMax, lMax*rMin, lMin*rMax, lMin*rMin)
  val ret = new AFix(possibleLimits.max, possibleLimits.min, this.exp + right.exp)
  ...
}
```

- No range alignment: `exp_out = l.exp + r.exp`, raw multiply of the raw
  vectors, `trim` to `ret.bitWidth`.
- Bounds are the min/max over the four corner products — so an unsigned operand
  keeps the result asymmetric.

| Expression | Operands | Result |
|---|---|---|
| `v0(0) * v1(1)` (`TriangleSetup:245`) | `SQ(16,4)` × `SQ(16,4)` | `(1073741824, -1073709056, -8)`, **32** bits |
| `coeff.a * xCenter` (`TriangleSetup:269`) | `SQ(34,8)` × `SQ(16,4)` | `(2^48, -(2^48 - 2^33), -12)` = `(281474976710656, -281474976677888, -12)`, **50** bits |
| `sub * factor` (`ColorCombine:511/513`) | `SQ(11,0)` (`(1023,-1024,0)`) × `UQ(9,8)` (`(511,0,-8)`) | `(522753, -523264, -8)`, **20** bits |

### 5.3 Unary negation

```scala
def unary_-(): AFix = negate(True)
def negate(enable: Bool, plusOneEnable: Bool = null): AFix = {
  val ret = new AFix(-this.minRaw max this.maxRaw, -this.maxRaw min this.minRaw, this.exp)
  if (this.minRaw >= 0) ret.raw := U(this.raw).twoComplement(enable, plusOneEnable).asBits
  else                  ret.raw := S(this.raw).twoComplement(enable, plusOneEnable).asBits.resized
  ret
}
```

- `maxRaw_out = max(-minRaw, maxRaw)`, `minRaw_out = min(-maxRaw, minRaw)`.
  The bounds are the **union** of the input range and its mirror, so `-x` on a
  symmetric signed range is the same format, and on `(65535, -65535, -4)` it is
  again `(65535, -65535, -4)`, 17 bits.
- `exp` unchanged. Hardware is a two's-complement negate (invert + 1).

### 5.4 Comparisons, `min`/`max`, and muxing

`===`, `=/=`, `<`, `<=`, `>`, `>=` (`AFix.scala:491-551`): both sides are
`alignLR`-shifted to the finer `exp`, then compared as `UInt`/`SInt` with
`.resized` on both (Spinal widens to the common width, unsigned operands going
through `intoSInt` when mixed). There is **no range checking**: the comparison
is a plain integer compare of the aligned raw values.

`Num.min`/`Num.max` (`Trait.scala:968-971`) are `Mux(this < right, …)`.

Muxing (`Mux`, `switch`, `when`-assignment to a common target) uses
`getMuxType` → `AFix.holdingParams` (`AFix.scala:115-127`):

```scala
val ex = values.map(_.exp).min
( values.map(e => e.maxRaw << (e.exp - ex).max(0)).max,
  values.map(e => e.minRaw << (e.exp - ex).max(0)).min,
  ex )
```

i.e. the mux output format is the **bounding box** of the inputs at the finest
`exp`, and each input is assigned into it (`toMuxInput` → `assignFrom`, §5.5).

In the core: `Mux(signBit, -a_raw, a_raw)` (`TriangleSetup:246-248`) where both
arms are `(65535, -65535, -4)` ⇒ output identical. `x.min(y)`/`x.max(y)` over
three `SQ(16,4)` vertices ⇒ `SQ(16,4)`.

### 5.5 Assignment `:=` (`assignFromImpl`, `AFix.scala:1145-1189`)

This is where most implicit conversion happens; the port must model it.

```
given  this (target)  and  af (source), trunc = af's TagAFixTruncated (if any)

1. if (this.exp > af.exp && trunc.isEmpty) -> elaboration ERROR
                              ("precision would be lost")
2. if (af.exp > this.exp)            af_rounded = af.resize(this.exp)   // exact, shift left
   else if (af.exp < this.exp && trunc.isDefined)
                                     af_rounded = (af >> this.exp)._round(trunc.rounding) << this.exp
3. if (trunc.saturation)             af_sat = af_rounded.sat(this)
   else if (trunc.overflow)          af_sat = clone(this); af_sat.raw := af_rounded.raw.resized  // wrap
4. if (target range does not cover source range) && no trunc tag -> elaboration ERROR
5. raw assignment: sign-extend if both signed, else zero-extend/truncate (`resized`)
```

Practical rules for the port:

- **Same `exp`, target range ⊇ source range** (the overwhelmingly common case
  after a `fixTo`): a plain sign/zero-extending raw copy.
- **Source coarser** (`af.exp > this.exp`): raw is shifted **left** by
  `af.exp - this.exp` — exact, no rounding.
- **Source finer** without a `.truncated`/`.saturated` tag: **error**. The core
  never does this (every such site has a `fixTo` or `.truncated`).
- `x := <UInt>` / `x := <SInt>` (`AFix.scala:1172-1173`) is a **raw bit copy**
  with no scaling and no range check:
  `this.raw.compositAssignFrom(u.asBits, …)`. `ColorCombine.clampSIntToFactor`
  relies on this: an 8-bit `UInt` in 0..255 assigned to a `UQ(0 bits, 8 bits)`
  becomes the value `n/256`.

`resize(newExp: ExpNumber)` (`AFix.scala:669-685`) only ever *adds* low bits
(`assert(newExp.value <= exp)`); bounds scale by `2^(exp-newExp)`.

### 5.6 `.asUInt` / `.asSInt` / `.raw`

```scala
def asUInt(): UInt = {            // AFix.scala:1194-1209
  if (this.signed) { when(this.raw.msb) { out := 0 } otherwise { out := this.raw.asUInt }; out }
  else this.raw.asUInt
}
def asSInt(): SInt = if (this.signed) this.raw.asSInt else this.raw.asUInt.intoSInt
```

- `asUInt` on a **signed** `AFix` **clamps negatives to 0** (it is not a
  reinterpret!) and keeps `bitWidth` bits. Used by
  `PixelPipeline.pixelX`/`spanY` (`value.floor(0).asUInt.resize(...)`) and
  `ColorCombine.clampAFixToU8`. This clamp is load-bearing: `pixelX` of a
  negative coordinate yields 0, not a wrapped large value.
- `asSInt` on an unsigned `AFix` widens by one bit (`intoSInt`).
- Neither looks at `exp` — they are bit-level views.
- `.raw` is the `Bits(bitWidth)` storage and is read/written directly 111
  times; `Fpxx2AFix`, `PciFifo`, `BmbBusInterface`, `TriangleSetup`,
  `SpanWalker` and `ColorCombine.invertFactor` all bypass the value model this
  way.

---

## 6. Rounding, `fixTo`, saturation

### 6.1 Bound propagation: `_roundFixedBigInt`

Every rounding method computes its output bounds by applying the *same*
rounding to `maxRaw` and `minRaw` (`AFix.scala:214-217`):

```scala
private def roundedBounds(exp: Int, roundType: RoundType): (BigInt, BigInt) = {
  val drop = exp - this.exp
  (_roundFixedBigInt(maxRaw, drop, roundType), _roundFixedBigInt(minRaw, drop, roundType))
}
```

`_roundFixedBigInt` (`AFix.scala:220-330`) is written in terms of
`scaled = i / 2^exp` (Scala `BigInt./` truncates **toward zero**),
`halfBit = i.abs.testBit(exp-1)`, `halfDropped = (i.abs & (2^(exp-1)-1)) != 0`,
`droppedBits = halfBit || halfDropped`, and `positive = i.signum >= 0`.

For the four modes the core uses, this is **exactly** ordinary arithmetic
rounding (verified exhaustively for `drop ∈ [1,6]`, `i ∈ [-300, 300]`):

| Mode | `_roundFixedBigInt(i, d, …)` equals |
|---|---|
| `FLOOR` | `floor(i / 2^d)` = `i >> d` |
| `CEIL` | `ceil(i / 2^d)` |
| `ROUNDUP` (`roundHalfUp`) | `floor((i + 2^(d-1)) / 2^d)` — ties toward `+inf` |
| `ROUNDDOWN` (`roundHalfDown`) | `ceil((i - 2^(d-1)) / 2^d)` — ties toward `-inf` |

`drop = exp_target - exp_source`. **If `drop < 0` every rounding method returns
`CombInit(this)` unchanged** (same `exp`, same bounds) — it does not up-convert.
`drop == 0` is the identity.

### 6.2 Hardware primitives and their widths

Each `AFix` rounding method delegates to `UInt`/`SInt` primitives, then
`.resize(widthOf(res.raw))` where `res` was built from the rounded bounds. So
**the emitted width is always the bound-derived `bitWidth` of the result
`AFix`**, not the primitive's natural width. The primitives (with `n = drop`):

| Method | signed path (`SInt.scala`) | unsigned path (`UInt.scala`) | natural width |
|---|---|---|---|
| `floor(n)` | `this >> n` (arithmetic) | `this >> n` | `w-n` |
| `ceil(n, align)` | `_ceil`: `sign ? (floor + |low n bits| ≠ 0) : positiveCeil` | `_ceil`: `+^1` if low `n` bits set | `w-n+1` (then `.sat(1)` iff `align`) |
| `roundUp(n, align)` | `(this(w-1 downto n-1) +^ 1)._floor(1)` | same shape | `w-n+1` (then `.sat(1)` iff `align`) |
| `roundDown(n, align)` | `sub0p5 = this +^ (-2^(n-1))`, then `ceil` of that | `this(n-1) && this(n-2 downto 0).orR ? (hi +^ 1) : hi.expand` | `w-n+1` (then `.sat(1)` iff `align`) |

I verified these bit-level constructions reproduce the arithmetic definitions
of §6.1 for all values of a 6-bit signed word at `drop = 2`.

`align` comes from `getTrunc.saturation`, which is `false` for this design, so
**no `sat(1)` step is ever inserted**.

### 6.3 Worked rounding examples

Source `SQ(6,2)` (raw 6 bits, `exp = -2`), target `exp = 0` (`drop = 2`):

| raw | value | `floor` | `ceil` | `roundHalfUp` | `roundHalfDown` |
|---:|---:|---:|---:|---:|---:|
| 7 | +1.75 | 1 | 2 | 2 | 2 |
| 6 | +1.5 | 1 | 2 | **2** | **1** |
| 5 | +1.25 | 1 | 2 | 1 | 1 |
| 4 | +1.0 | 1 | 1 | 1 | 1 |
| 2 | +0.5 | 0 | 1 | **1** | **0** |
| 1 | +0.25 | 0 | 1 | 0 | 0 |
| 0 | 0.0 | 0 | 0 | 0 | 0 |
| -1 | -0.25 | -1 | 0 | 0 | 0 |
| -2 | -0.5 | -1 | 0 | **0** | **-1** |
| -3 | -0.75 | -1 | 0 | -1 | -1 |
| -5 | -1.25 | -2 | -1 | -1 | -1 |
| -6 | -1.5 | -2 | -1 | **-1** | **-2** |
| -7 | -1.75 | -2 | -1 | -2 | -2 |
| -8 | -2.0 | -2 | -2 | -2 | -2 |

Halves are the only rows where the two half-modes differ: `roundHalfUp` breaks
ties toward `+inf`, `roundHalfDown` toward `-inf`. Neither is ties-to-even and
neither is symmetric about zero.

Bound propagation on the core's real cases, source `vertexFormat = SQ(16,4)`
(`maxRaw = 32767`, `minRaw = -32768`, `drop = 4`):

| Call (`TriangleSetup`) | rounded bounds | result format |
|---|---|---|
| `.floor(0)` | `(2047, -2048)` | `exp 0`, **12** bits |
| `.ceil(0)` | `(2048, -2048)` | `exp 0`, **13** bits (non-power-of-two `maxRaw`!) |
| `.roundHalfDown(0)` | `(2048, -2048)` | `exp 0`, **13** bits |

The `ceil`/`roundHalfDown` results need 13 bits precisely because `maxRaw`
rounds up to `2048`, which no longer fits in 12 signed bits. This is the
range-based width rule in action and a classic off-by-one trap for a
width-only reimplementation.

### 6.4 `fixTo`

```scala
def fixTo(af: AFix, roundType: RoundType): AFix = this._round(roundType, af.exp).sat(af)
def fixTo(af: AFix): AFix = this.fixTo(af, getTrunc.rounding)          // ⇒ FLOOR here
def fixTo(Q: QFormat): AFix = this.fixTo(Q, getTrunc.rounding)
def fixTo(Q: QFormat, roundType: RoundType): AFix = {
  val res = AFix(Q)
  res := this.fixTo(res, roundType)
  res
}
```

**Every `fixTo` in the core passes a `QFormat`** (`c.vertexFormat`,
`c.coefficientFormat`, `lowGradFormats(i)`, `c.texCoordsHiFormat`,
`c.texCoordsAccumFormat`, `colorMulFormat`, `UQ(8,0)`, `UQ(0,8)`), and none
passes an explicit `RoundType`. So in this design:

> `x.fixTo(Q)` = **floor** `x` to `exp = -Q.fraction`, **saturate** to
> `AFix(Q)`'s bounds, and the result is **exactly `AFix(Q)`** (`Q.width` bits).

The two-step nature matters for the intermediate: `_round(...)` alone can
produce bounds wider than the target, `.sat(af)` intersects them, and the final
`res := …` re-widens to the target format (and would raise an elaboration error
if the intersection were still out of range — it never is).

Worked examples (intermediate = after `_round` + `sat`; final = `AFix(Q)`):

| Call | source | intermediate | final |
|---|---|---|---|
| `(coords(0) + one).fixTo(vertexFormat)` | `(65534,-65536,-4)` 17b | `(32767,-32768,-4)` 16b | `SQ(16,4)` |
| `(edge + a).fixTo(coefficientFormat)` | `(1.7e10,-1.7e10,-8)` 35b | `(8589934591,-8589934592,-8)` 34b | `SQ(34,8)` |
| `(sub * factor).fixTo(colorMulFormat)` | `(522753,-523264,-8)` 20b | `(262143,-262144,-8)` 19b | `QFormat(19,8,true)` |
| `AFix(SInt(9 bits)).fixTo(colorMulFormat)` | `(255,-256,0)` 9b | `(255,-256,0)` 9b (`drop = -8 < 0` ⇒ unchanged; `sat` rescales the target bounds down) | `QFormat(19,8,true)` (assignment shifts raw left by 8) |
| `state.hiS.fixTo(texCoordsAccumFormat)` | `SQ(46,30)` | `(8589934591,-8589934592,-18)` 34b (floor, `drop = 12`) | `SQ(48,18)` |
| `xminRaw.floor(0).fixTo(vertexFormat)` | `(2047,-2048,0)` 12b | unchanged (`drop = -4`) | `SQ(16,4)` |

Note the last two rows: when the source is *coarser* than the target,
`_round` is a no-op and the widening happens in the final assignment
(`resize`, exact).

### 6.5 `sat`

Three overloads; the core uses two (`AFix.scala:735-787`):

```scala
def sat(satMax: BigInt, satMin: BigInt, exp: ExpNumber): AFix = {
  val expDiff = this.exp - exp.value
  if (expDiff > 0)      sat(satMax / 2^expDiff, satMin / 2^expDiff)           // BigInt / truncates toward 0
  else if (expDiff < 0) sat(satMax * 2^-expDiff + (if (satMax != 0) 2^-expDiff - 1 else 0),
                            satMin * 2^-expDiff + (if (satMin != 0) 2^-expDiff - 1 else 0))
  else                  sat(satMax, satMin)
}

def sat(satMax: BigInt, satMin: BigInt): AFix = {
  val ret = new AFix(satMax.min(this.maxRaw), satMin.max(this.minRaw), exp)
  when (this > AFix(satMax, exp exp))       { ret.raw := ret.maxRaw }
  elsewhen (this < AFix(satMin, exp exp))   { ret.raw := ret.minRaw }
  otherwise                                 { ret.raw := this.raw.(as|)resize(ret.bitWidth) }
  ret
}
```

Key points:

- `exp` is **unchanged** by `sat`. Only the bounds (and hence `bitWidth`)
  change, to the **intersection** `(min(satMax, maxRaw), max(satMin, minRaw))`.
- The three-argument form re-expresses value-level bounds `satMax*2^exp` in the
  source's own raw grid. The `+ (2^-expDiff - 1)` fudge on the `expDiff < 0`
  branch (target coarser than source) makes the clamp inclusive of the whole
  coarse step, and is skipped when the bound is exactly 0 — an asymmetry the
  port must copy verbatim.
- If the source range is already inside the target, the `when` chain is
  constant-false and the result is just a resize.
- There is a `tagAutoResize` pre-pass (for values tagged `.resized`); the core
  never tags, so it can be ignored.
- `sat(m: Int)` (drop `m` MSBs) is **not** used on AFix in the core.

Core uses (`ColorCombine`):

| Call | source | result |
|---|---|---|
| `redGrad.sat(255, 0, 0 exp)` | `vColorFormat SQ(24,12)` | `(1048575, 0, -12)`, **20** bits unsigned (255 scaled to the `2^-12` grid ⇒ `255*4096 + 4095`) |
| `depthGrad.sat(255, 0, 12 exp)` | `vDepthFormat SQ(32,12)` | `(2147483647, 0, -12)`, **31** bits unsigned (the clamp is above the source max ⇒ only the negative clamp bites) |
| `alphaGradHi.sat(255, 0, 0 exp)` | `texCoordsHiFormat SQ(46,30)` | `(274877906943, 0, -30)`, **38** bits unsigned |
| `v.sat(255, 0)` (`clampAFixToFactor`) | `SQ(11,0)` | `(255, 0, 0)`, **8** bits unsigned |
| `v.sat(255, 0, 0 exp)` (`clampAFixToU8`) | `colorMulFormat SQ(19,8)` | `(65535, 0, -8)`, **16** bits unsigned |

Note how `sat` flips the result to **unsigned** (`minRaw = 0`), which then
changes `bitWidth` and the behaviour of every downstream operator.

### 6.6 `.truncated` / `.saturated` / `.rounded` tags

```scala
def truncated(saturation: Boolean, overflow: Boolean, rounding: RoundType): AFix = {
  val copy = cloneOf(this); copy.raw := this.raw
  copy.addTag(new TagAFixTruncated(saturation, overflow, rounding)); copy
}
def truncated: AFix = this.truncated(saturation = false, overflow = true, getTrunc.rounding)
def saturated:  AFix = this.truncated(saturation = true,  overflow = false, getTrunc.rounding)
def rounded:    AFix = this.truncated(saturation = false, overflow = false, getTrunc.rounding)
```

The tag changes nothing by itself; it is consumed by the **next `:=`** (§5.5):
it permits precision loss, chooses the rounding mode, and picks saturate
(`sat(this)`) vs wrap (`raw.resized`) vs neither.

The core has exactly **one** use, `TriangleSetup:269`:

```scala
edgeStartVec(idx) := (coeff.a * xCenter + coeff.b * yCenter + coeff.c).truncated
```

Source: two `SQ(34,8) * SQ(16,4)` products (each `exp = -12`, 50 bits) summed
with each other and with `SQ(34,8)` ⇒ `(563087392374768, -563087392309248,
-12)`, **51** bits. Target `AFix(coefficientFormat)` = `SQ(34,8)`, `exp = -8`. With
`truncated` = (saturation=false, overflow=true, rounding=FLOOR):

1. `af.exp (-12) < this.exp (-8)`, tag present ⇒
   `af_rounded = (af >> -8)._round(FLOOR) << -8`. `>>`/`<<` on `Int` only move
   the decimal point (`AFix.scala:552-568`), so this is
   `floor` to `exp = -8`: bounds become `(35192962023423, -35192962019328)`,
   **47** bits.
2. `overflow` ⇒ `af_sat = clone(target); af_sat.raw := af_rounded.raw.resized`
   — a plain **truncation to 34 bits, wrapping** (no saturation).

Verilog confirms: `_zz_outputReg_edgeStart_0_9 [50:0]` →
`o_payload_edgeStart_0 [33:0]`.

`.saturated` and `.rounded` are unused.

---

## 7. Cross-reference by file

| File | AFix surface |
|---|---|
| `Config.scala` | the nine named `QFormat`s (§3) |
| `Bundles.scala` | `Coefficients(a,b,c: AFix(coefficientFormat))`, `Color.ufactor()` = three `AFix.UQ(0 bits, 8 bits)`, `vertex2d/triangle/TriangleWithSign` = `Vec` of `AFix(fmt)` |
| `bus/BmbBusInterface.scala` | `field`/`fieldAt` build a `UInt`/`SInt` of `proto.bitWidth` from the register bank and then do `afix.raw := base.asBits` (raw copy); `withFloatAlias()` reads `afix.intWidth` / `afix.fracWidth` as elaboration-time metadata |
| `bus/PciFifo.scala` | instantiates `Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1)` and consumes `result.number.raw.asSInt` |
| `frontend/RegisterBank.scala` | 33 register fields typed as `AFix(config.*Format)` (vertex, colour, depth, texcoord, W, and their dX/dY gradients); `AFix(texCoordsHiFormat).raw.getWidth` used as a constant |
| `raster/TriangleSetup.scala` | the arithmetic core: `-`, `*`, unary `-`, `Mux`, `min`/`max`, `floor(0)`, `ceil(0)`, `roundHalfDown(0)`, 7 × `fixTo`, the single `.truncated`, comparisons for the top-left rule; `IterChannel` then works on `inStart.raw` as plain `SInt` |
| `raster/Rasterizer.scala` | `InputGradient(start: AFix, d: Vec(2)(AFix))`, per-pixel `+` and `fixTo` in the `StreamWhile` step, `toPixelCoord` = `(value.asBits.asSInt >> fraction).resize(...)` (a raw shift, deliberately **not** `floor`) |
| `raster/SpanWalker.scala` | 23 × `fixTo` on the cursor accumulators, 11 × `floor(0)` (mostly `.asSInt`), `zero = 0.SQ(1 bits, 0 bits)` for the edge tests, `one`/`negOne` from `BigDecimal` assignment, `pixelToVertex` writing `raw` directly |
| `texture/Tmu.scala` | AFix only as bundle members (`s`, `t`, `w`, `dSdX`…) — no AFix arithmetic; the maths is done on `raw` |
| `pixel/ColorCombine.scala` | densest AFix user: `sat` ×7, `fixTo` ×9, `*`, `+`, `AFix(SInt)`, `asUInt`, raw manipulation in `invertFactor` |
| `pixel/Fog.scala` | `depth: AFix(vDepthFormat)` in the bundle; consumed as `payload.depth.raw.asSInt` |
| `pixel/FramebufferAccess.scala` | `payload.depth.raw.asSInt >> 12` with a manual 0..0xFFFF clamp |
| `core/PixelPipeline.scala` | `pixelX(value) = value.floor(0).asUInt.resize(...)` and the same for `spanY` |
| `math/Fpxx2AFix.scala` | produces `AFix.SQ(intNrBits, fracNrBits)` via `assignFromBits` |

---

## 8. Traps found while reading (must be reproduced, or fixed deliberately)

### 8.1 `ColorCombine.clampAFixToFactor` is a constant zero

```scala
def clampAFixToFactor(v: AFix): AFix =
  v.sat(satMax = 255, satMin = 0).fixTo(UQ(0, 8))
```

`UQ(0, 8)` here is `spinal.core.UQ`, i.e. `QFormat(width = 0, fraction = 8)` —
a **zero-bit** format, not `AFix.UQ(0 bits, 8 bits)`. `AFix(QFormat(0,8,false))`
has `maxRaw = minRaw = 0`, `exp = -8`, `bitWidth = 0`, so the `fixTo` saturates
everything to 0 and the helper always returns 0.

Confirmed in the generated Verilog (`CoreArtix.v`, module `ColorCombine`):

```verilog
MSelect_AOTHER : begin
  n3_C_FACTOR_PRE_r = 8'h0;
end
```

So the `AOTHER` multiply-factor path (RGB **and** alpha) is hard zero today.
`clampAFixToU8` next to it uses `UQ(8, 0)` = `QFormat(8,0,false)`, which *is*
the intended 8-bit unsigned integer, so the bug is a one-character-class slip.
**The Amaranth port must reproduce the zero** to stay bit-identical at L2/L3;
changing it is a separate, screenshot-visible change.

### 8.2 `asUInt` saturates, it does not reinterpret

See §5.6. `pixelX`/`spanY`/`clampAFixToU8` depend on the negative→0 clamp.

### 8.3 Range-derived widths, not format-derived

`ceil(0)` and `roundHalfDown(0)` of an `SQ(16,4)` are **13** bits, not 12,
because `maxRaw` rounds up to `2048`. Any implementation that tracks only
`(i_bits, f_bits)` gets this wrong, and the error propagates into every
downstream `fixTo` saturation comparison.

### 8.4 `sat` makes values unsigned

`x.sat(255, 0, …)` sets `minRaw = 0` ⇒ `signed == false` ⇒ `bitWidth` loses the
sign bit and all later comparisons/products take the unsigned path.

### 8.5 `fixTo` with `drop < 0` silently skips rounding

`_round` returns the value unchanged when the target `exp` is *coarser-to-finer*
(`drop < 0`); the conversion then happens in the final assignment as an exact
left shift. Do not raise an error there.

### 8.6 `BigInt` division truncates toward zero

Used in `_roundFixedBigInt` (`scaled = i / 2^exp`) and in the `sat(…, exp)`
rescale. Python's `//` floors — the port must use trunc-toward-zero
(`int(a / b)` semantics, or `abs` + sign) in exactly those two places.
(The net effect in `_roundFixedBigInt` is still exact floor/ceil/half-up/
half-down, as verified in §6.1, but the intermediate differs.)

---

## 9. `Fpxx` and `Fpxx2AFix`

`ext/SpinalVoodoo/src/voodoo/math/Fpxx.scala` (259 lines) — a parameterised
float bundle, used only as `FpxxConfig.float32()` in this design.

**Layout** (`case class Fpxx(c: FpxxConfig)`), declared LSB-first so the packed
bit order is the IEEE one:

```
mant : UInt(c.mant_size bits)   // 23
exp  : UInt(c.exp_size bits)    // 8
sign : Bool                     // 1     ⇒ bits [31]=sign, [30:23]=exp, [22:0]=mant
```

`FpxxConfig(exp_size, mant_size, nan_encoding = IEEENan(), inf_encoding =
IEEEInfinity(), exp_bias = IEEEBias(), signed_zero = true)`;
`bias = 2^(exp_size-1) - 1 = 127`; `full_size = 32`; `ieee_like = true` for
`float32()`.

Predicates: `is_zero = (exp === 0 && mant === 0)`,
`is_nan = exp.andR && mant.orR`, `is_infinite = exp.andR && !mant.orR`,
`is_subnormal = (exp === 0)` (i.e. **denormals are lumped with zero by
`is_zero` only when the mantissa is also 0**; a denormal is `is_subnormal` but
not `is_zero`). `full_mant() = mant.resize(mant_size+1) | (1 << mant_size)`
always sets the hidden bit — so **denormals are treated as if normalised**;
there is no denormal handling anywhere. Helpers `abs()`, `unary_~` (sign
flip), `set_nan/set_inf/set_zero`, `flags()` (nan/inf/sign). `FpxxHost` is the
simulation-side host model (not RTL).

`Fpxx2AFix.scala` (71 lines) — the only float→fixed converter, instantiated
once, as `Fpxx2AFix(20 bits, 30 bits, float32, pipeStages = 1)` in `PciFifo`
for the register float aliases.

Two pipeline nodes (`spinal.lib.misc.pipeline`, `StageLink` when
`pipeStages >= 1`):

- **n0**: `ge0 = op.exp >= max(bias - fracNrBits, 0) && !op.is_zero()`
  (with `bias = 127`, `fracNrBits = 30` ⇒ `max(97, 0) = 97`);
  `shift = (intNrBits - 1 + bias) - exp` as `SInt(exp_size + 2)`
  (= `146 - exp`); `mant_full = (U"01" @@ mant).asSInt` (hidden bit forced,
  25 bits); `mant_2c = sign ? -mant_full | mant_full`.
- **n1**: `int : SInt(intNrBits + fracNrBits + 1)` = 51 bits.
  - `shift >= 50 || !ge0` ⇒ `int := 0`, `ovfl := False` (this is the
    zero / too-small / denormal path),
  - `shift < 0` ⇒ saturate: `int := {msb = mant_2c.msb, rest = !mant_2c.msb}`
    (i.e. `+max` or `-min`), `ovfl := True`,
  - otherwise ⇒ `int := (mant_2c @@ zeros) |>> shift_clipped` (arithmetic right
    shift, `|>>` keeps the width).
  - `io.result.number.assignFromBits(int.asBits)`.

**AFix operations used: only the output type `AFix.SQ(intNrBits, fracNrBits)`
and `assignFromBits`.** No AFix arithmetic, no rounding, no saturation via
AFix. The conversion truncates toward `-inf` (arithmetic shift) and has **no
NaN/Inf special-casing at all** — a NaN or Inf input goes down the `shift < 0`
saturating path because `exp = 255` makes `shift = -109`. `fixed.py` therefore
needs nothing extra for `fpxx.py`; the port is a straight transliteration of
integer shifts, with `AFix.SQ(20 bits, 30 bits)` = `(2^50-1, -2^50, -30)`,
51 bits.

Downstream, `PciFifo` reads `result.number.raw.asSInt` and applies its own
`floatCorrected(rawSInt, shift)` per-register shift, so the AFix format there
is really just a 51-bit container.

---

## 10. Recommendation

### 10.1 `amaranth.lib.fixed` cannot be used — it does not exist

Checked 2026-09-20:

- Latest Amaranth release on PyPI: **0.5.10** (2026-09-10).
- `amaranth/lib/` at tag `v0.5.10` contains `cdc, coding, crc, data, enum,
  fifo, io, memory, meta, stream, wiring` — **no `fixed.py`**.
- `amaranth/lib/` on `main` likewise has no `fixed.py`
  (`raw.githubusercontent.com/.../main/amaranth/lib/fixed.py` → 404).
- Docs: `https://amaranth-lang.org/docs/amaranth/latest/stdlib/fixed.html` →
  404; the stdlib index lists no fixed-point module.
- **RFC 41 ("Add an RFC for fixed point types", amaranth-lang/rfcs#41) is still
  an open pull request**, last updated 2026-05-03. Not merged, not implemented.

Even if it lands, the proposed API would not be bit-exact for us:

- `fixed.Shape(shape, f_bits)` / `SQ(i_bits, f_bits)` / `UQ(i_bits, f_bits)`
  model **(width, fractional bits)** only. There is no `maxRaw`/`minRaw`, so
  the range-based widths of §6.3/§8.3 and the range-based `sat` of §6.5 cannot
  be expressed.
- The RFC's `SQ(i_bits, f_bits)` counts the sign bit **inside** `i_bits`, the
  opposite of `AFix.SQ(i bits, f bits)` (which adds a sign bit on top) — a
  guaranteed off-by-one if the names are reused naively.
- Rounding is explicitly deferred ("we provide only `reshape()` (truncation)
  … additional rounding strategies may be added in a future RFC"), and the
  open question section has not settled on a default. We need
  floor/ceil/half-down with Spinal's exact bound propagation.

**Decision: write our own `avoodoo/lib/fixed.py`.** Do not depend on, or wrap,
`amaranth.lib.fixed`. (Revisit only if the diff tests ever need it; the surface
we need is small.)

### 10.2 Minimal API for `avoodoo/lib/fixed.py`

Everything the core needs, and nothing more. The shape/value split mirrors
Amaranth's `ShapeCastable`/`ValueCastable` conventions so `AFixShape` can be
used as a `data.StructLayout` member and in `wiring.Signature`s.

```python
class RoundType(enum.Enum):            # only these four are reachable today;
    FLOOR = "floor"                    # keep the rest out until needed
    CEIL = "ceil"
    ROUND_HALF_UP = "roundup"          # RoundType.ROUNDUP
    ROUND_HALF_DOWN = "rounddown"      # RoundType.ROUNDDOWN

@dataclass(frozen=True)
class QFormat:
    width: int; fraction: int; signed: bool
    @property
    def nonFraction(self) -> int: ...      # width - fraction, used by the core

def SQ(width: int, fraction: int) -> QFormat: ...     # spinal.core.SQ
def UQ(width: int, fraction: int) -> QFormat: ...     # spinal.core.UQ  (careful: UQ(0,8)!)

class AFixShape(ShapeCastable):
    def __init__(self, max_raw: int, min_raw: int, exp: int): ...
    # derived, per §1 -- exact same formulas
    signed: bool; bit_width: int; frac_width: int; whole_width: int
    int_width: int; num_width: int; max_exp: int; left_exp: int
    max_value: Fraction; min_value: Fraction; step: Fraction

    @classmethod
    def from_q(cls, q: QFormat) -> "AFixShape": ...        # AFix(QFormat)
    @classmethod
    def sq(cls, int_bits: int, frac_bits: int) -> "AFixShape": ...   # AFix.SQ(BitCount, BitCount)
    @classmethod
    def uq(cls, int_bits: int, frac_bits: int) -> "AFixShape": ...   # AFix.UQ(BitCount, BitCount)
    @classmethod
    def holding(cls, shapes: Iterable["AFixShape"]) -> "AFixShape": ...  # mux/Mux bounding box
    def as_shape(self) -> Shape: ...                       # signed/unsigned(bit_width)
    def __call__(self, target) -> "AFixValue": ...         # ShapeCastable protocol
    def const(self, value) -> ...                          # Decimal/Fraction/int literal (":= BigDecimal")

class AFixValue(ValueCastable):
    shape: AFixShape
    raw: Value                                             # the Bits(bit_width) storage

    # construction from plain Amaranth values
    @staticmethod
    def from_sint(v: Value) -> "AFixValue": ...            # AFix(SInt)
    @staticmethod
    def from_raw(shape: AFixShape, raw: Value) -> "AFixValue": ...  # x.raw := bits

    # arithmetic (all pure; return new AFixValue with computed shape)
    def __add__(self, other) -> "AFixValue": ...
    def __sub__(self, other) -> "AFixValue": ...
    def __mul__(self, other) -> "AFixValue": ...
    def __neg__(self) -> "AFixValue": ...

    # comparisons -> Value (1 bit)
    def __eq__/__ne__/__lt__/__le__/__gt__/__ge__(self, other) -> Value: ...
    def min(self, other) -> "AFixValue": ...
    def max(self, other) -> "AFixValue": ...

    # rounding: exp is the TARGET exponent (Spinal passes an absolute exp, not a count)
    def floor(self, exp: int = 0) -> "AFixValue": ...
    def ceil(self, exp: int = 0, align: bool = False) -> "AFixValue": ...
    def round_half_up(self, exp: int = 0, align: bool = False) -> "AFixValue": ...
    def round_half_down(self, exp: int = 0, align: bool = False) -> "AFixValue": ...
    def _round(self, rt: RoundType, exp: int = 0, align: bool = False) -> "AFixValue": ...

    # saturation
    def sat(self, sat_max: int, sat_min: int, exp: int | None = None) -> "AFixValue": ...

    # conversion
    def fix_to(self, target: QFormat | AFixShape,
               rounding: RoundType = RoundType.FLOOR) -> "AFixValue": ...
    def resize(self, new_exp: int) -> "AFixValue": ...      # new_exp <= exp, exact
    def as_unsigned(self) -> Value: ...                     # .asUInt  -- CLAMPS negatives to 0
    def as_signed(self) -> Value: ...                       # .asSInt

    # assignment, honouring the tag rules of §5.5
    def eq(self, other, *, truncated: bool = False, saturated: bool = False,
           rounding: RoundType = RoundType.FLOOR) -> list[Statement]: ...

# elaboration-time reference model, for the pytest sweeps (no Amaranth values)
def round_bigint(i: int, drop: int, rt: RoundType) -> int: ...
def rounded_bounds(shape: AFixShape, exp: int, rt: RoundType) -> tuple[int, int]: ...
def add_shape/sub_shape/mul_shape/neg_shape/sat_shape/fix_to_shape(...) -> AFixShape: ...
```

Notes for the implementer:

- Keep the **shape algebra** (`*_shape` functions) separate from RTL emission
  so the pytest sweep can compare shapes and values against this document's
  tables without elaborating anything.
- `align` is plumbed through but is **always `False`** for this design; assert
  that rather than implementing `sat(1)` for now.
- The default rounding for `fix_to` and for tagged assignment is `FLOOR` — do
  not expose a global scope property; pass it explicitly.
- `eq()` must reject a precision-losing assignment without a tag (mirroring
  Spinal's `PendingError`), because that check is what keeps the transliteration
  honest.
- Unit tests that must exist: the §3 format table, the §5.1/§5.2 operand
  tables, the §6.3 rounding table on both signs including halves, the §6.3
  `ceil`/`roundHalfDown` 13-bit widths, the §6.4 `fixTo` table, the §6.5 `sat`
  table (including the `+2^-expDiff - 1` fudge and the `satMax == 0` exception),
  the §6.6 `.truncated` wrap, and the §8.1 zero-width `UQ(0, 8)` degenerate
  case.
- The diff micro-module for `make diff MOD=AFixOps` should exercise, per
  §4: `+`, `-`, `*`, unary `-`, `>=`, `min`/`max`, `Mux`, `floor(0)`,
  `ceil(0)`, `roundHalfDown(0)`, `fixTo(Q)` for three target formats,
  `sat(255, 0, exp)` for `exp ∈ {0, 12}`, `.truncated` assignment, and
  `asUInt`/`asSInt` — all on the real core formats of §3.
