"""Triangle setup: bounding box, edge coefficients, and the start-value walk.

Scala source: ``ext/SpinalVoodoo/src/voodoo/raster/TriangleSetup.scala``
(498 lines).  Plan task Phase 3, ``docs/amaranth-rewrite-plan.md``.  The AFix
result formats of every expression below are worked out in
``docs/amaranth/afix-semantics.md`` §5-6; the section numbers in the comments
point there.

Interface and timing, unchanged from the Scala:

* ``i`` (slave ``Stream(Input)``) and ``o`` (master ``Stream(Output)``).
* A four-state FSM, ``Idle -> Prep -> Iterate x8 -> Done``: ten clocks from
  ``i.fire`` to ``o.valid``, and ``o`` stays valid until ``o.ready``.
  ``i.ready`` is ``state === Idle``, so the block accepts one triangle at a
  time with no pipelining.
* Eleven ``IterChannel`` accumulators (8 gradients + hiS + hiT + hiAlpha),
  each a shift-and-add multiplier that walks the interpolation start value
  from vertex A to the bounding-box corner, two bits of |dx| and |dy| per
  ``Iterate`` cycle.  That is why there are exactly 8 iterations: the
  magnitudes are 16 bits.

Three Spinal constructs have no direct Amaranth equivalent and are spelled
out in helpers at the top of this file, each naming the Scala line it comes
from:

* ``SInt``/``UInt`` arithmetic keeps its **operand width** (``a + b`` on two
  ``SInt(w)`` is ``SInt(w)``, wrapping), where Amaranth grows by a bit.  Every
  such site goes through :func:`_s_add` / :func:`_s_sub` / :func:`_sext`,
  which truncate back to the Spinal width.  Truncating the exact sum is
  identical to Spinal's left-associative chain of wrapping adds, because both
  are the sum modulo ``2**w``.
* ``|<<`` is a **width-preserving** left shift (``<<`` is the widening one);
  :func:`_shl_keep`.
* ``SInt >> n`` with a constant **narrows** the result by ``n`` bits and is
  arithmetic; :func:`_sar`.

Register reset, the trap the differential harness catches first: a plain
SpinalHDL ``Reg(...)`` has **no** reset.  ``pending`` and ``outputReg`` are
plain ``Reg``s (``TriangleSetup.scala:17-18``) and are therefore
``reset_less=True`` here; ``state``, ``iterIndex``, ``dxMagWork``,
``dyMagWork`` and the three registers inside every ``IterChannel`` are
``RegInit``/``.init(0)`` and keep Amaranth's default zero reset.  That split
is confirmed against the ``if(reset)`` block of the generated
``fpga/rtl/diff/TriangleSetup_spinal.v``.

The ``GenerationFlags.formal`` block (``TriangleSetup.scala:326-336``) is not
emitted in normal generation, so it is not in the RTL; its three bounding-box
invariants are asserted every cycle by ``tests/test_triangle_setup.py``
instead, which is also where ``TriangleSetupFormalTest``'s vertex-ordering and
``ceil`` overflow assumptions live.
"""

from __future__ import annotations

from amaranth.hdl import Cat, Const, Module, Mux, Signal, Value, signed, unsigned
from amaranth.lib import enum, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (
    GRADIENT_BUNDLE_ALL,
    coefficients_layout,
    triangle_setup_input_layout,
    triangle_setup_output_layout,
)
from avoodoo.lib.fixed import AFixShape, AFixValue, afix_mux
from avoodoo.lib.stream import fire

__all__ = ["SetupState", "TriangleSetup", "EDGE_PAIRS", "ITER_CHANNELS"]


#: ``Seq((0, 1), (1, 2), (2, 0))`` (``TriangleSetup.scala:238``) -- the three
#: directed edges of the triangle, in the order their coefficients are stored.
EDGE_PAIRS = ((0, 1), (1, 2), (2, 0))

#: ``allStarts`` (``TriangleSetup.scala:187-199``): the eleven interpolated
#: quantities, in order, as ``(channel name, where its result lands in
#: ``o.payload``)``.  The first eight are ``GradientBundle.all``.
ITER_CHANNELS = (
    "redStart", "greenStart", "blueStart", "depthStart", "alphaStart",
    "wStart", "sStart", "tStart", "hiSStart", "hiTStart", "hiAlphaStart",
)


class SetupState(enum.Enum, shape=unsigned(2)):
    """``object SetupState extends SpinalEnum`` (``TriangleSetup.scala:12-14``).

    Default (native/binary) encoding, so the values are the declaration
    indices -- matching ``localparam SetupState_Idle = 2'd0`` in the Spinal
    Verilog.
    """

    Idle = 0
    Prep = 1
    Iterate = 2
    Done = 3


# ---------------------------------------------------------------------------
# Spinal SInt/UInt width semantics
# ---------------------------------------------------------------------------


def _sext(value, width: int) -> Value:
    """``SInt.resize(width bits)``: sign-extend, or drop the high bits."""
    v = Value.cast(value)
    assert v.shape().signed, f"{v!r} is not signed"
    if len(v) >= width:
        return v[:width].as_signed()
    return Cat(v, v[-1].replicate(width - len(v))).as_signed()


def _zext(value, width: int) -> Value:
    """``UInt.resize(width bits)``: zero-extend, or drop the high bits."""
    v = Value.cast(value).as_unsigned()
    if len(v) >= width:
        return v[:width]
    return Cat(v, Const(0, width - len(v)))


def _s_add(a, b, width: int) -> Value:
    """``SInt + SInt`` at a fixed width: Spinal does not grow, Amaranth does."""
    return _sext(Value.cast(a) + Value.cast(b), width)


def _s_sub(a, b, width: int) -> Value:
    """``SInt - SInt`` at a fixed width (Spinal resizes both to ``max(w)``)."""
    return _sext(Value.cast(a) - Value.cast(b), width)


def _shl_keep(value, shift: int, width: int) -> Value:
    """``x |<< n``: shift left **keeping the width**, high bits lost."""
    return _sext(Value.cast(value) << shift, width)


def _sar(value, shift: int) -> Value:
    """``SInt >> n`` with a constant ``n``: arithmetic, and ``n`` bits narrower."""
    v = Value.cast(value)
    assert v.shape().signed, f"{v!r} is not signed"
    return v[shift:].as_signed()


# ---------------------------------------------------------------------------
# the three combinational helpers defined inside the Component
# ---------------------------------------------------------------------------


def _mul_by_subpixel(value, coeff) -> Value:
    """``def mulBySubpixel(value: SInt, coeff: UInt): SInt`` (scala:62-70).

    A shift-and-add multiply written out so the tool infers LUT logic rather
    than a DSP; the port keeps it verbatim rather than writing ``value *
    coeff``, because the two differ in the emitted netlist even though they
    agree numerically.  Result width is ``value.width + coeff.width``, and
    every partial sum wraps at that width.
    """
    value = Value.cast(value)
    coeff = Value.cast(coeff)
    width = len(value) + len(coeff)
    zero = Const(0, signed(width))
    terms = [
        Mux(coeff[bit], _shl_keep(_sext(value, width), bit, width), zero)
        for bit in range(len(coeff))
    ]
    # `terms.reduce(_ + _)`: left-associative, each add wrapping at `width`.
    # Summing exactly and truncating once is the same value mod 2**width.
    total = terms[0]
    for term in terms[1:]:
        total = _s_add(total, term, width)
    return total


def _signed_magnitude(value) -> Value:
    """``def signedMagnitude(value: SInt): UInt`` (scala:72-79).

    Note the result is ``value.getWidth`` bits, not ``+1``: the negation is
    done one bit wider and then truncated, so ``-2**(w-1)`` comes back as
    ``2**(w-1)`` truncated to ``w`` bits, i.e. itself.  Reproduced as is.
    """
    value = Value.cast(value)
    width = len(value)
    wide = _sext(value, width + 1)
    return Mux(
        wide[-1],
        _sext(-wide, width + 1).as_unsigned()[:width],
        wide.as_unsigned()[:width],
    )


def _sign_adjust(value, negate, width: int) -> Value:
    """``def signAdjust(value: SInt, negate: Bool, width: Int): SInt`` (scala:81-84)."""
    resized = _sext(value, width)
    return Mux(negate, _sext(-resized, width), resized)


def _scale_by_chunk(m: Module, term: Value, chunk: Value, name: str) -> Signal:
    """``def scaleByChunk(term: SInt, chunk: UInt): SInt`` (scala:86-101).

    Multiply by the two-bit ``chunk`` (0..3) without a multiplier.  Declared
    with a ``:= 0`` default and a ``switch``, and -- because Spinal treats a
    signal declared inside a ``when`` as complete within it -- emitted as an
    **unconditional** ``always @(*)`` in the Verilog.  Built unconditionally
    here for the same reason.
    """
    width = len(term)
    scaled = Signal(signed(width), name=name)
    m.d.comb += scaled.eq(0)
    with m.Switch(chunk):
        with m.Case(0b01):
            m.d.comb += scaled.eq(term)
        with m.Case(0b10):
            m.d.comb += scaled.eq(_shl_keep(term, 1, width))
        with m.Case(0b11):
            m.d.comb += scaled.eq(_s_add(term, _shl_keep(term, 1, width), width))
    return scaled


# ---------------------------------------------------------------------------
# IterChannel
# ---------------------------------------------------------------------------


class _IterChannel:
    """``case class IterChannel(inStart: AFix, dxRaw: SInt, dyRaw: SInt) extends Area``.

    ``TriangleSetup.scala:103-135``.  A Spinal ``Area`` is inlined into the
    parent, with its members prefixed by the val name (``redStart_acc``), so
    this is a plain Python helper rather than an ``Elaboratable``; the signal
    names below reproduce the Spinal Verilog's exactly.

    The walk it performs: ``acc`` starts at the (optionally sub-pixel
    corrected) vertex-A value, and each ``Iterate`` cycle adds
    ``dxTerm * dxMagWork[1:0] + dyTerm * dyMagWork[1:0]`` then shifts both
    terms left by two -- i.e. a radix-4 multiply of the gradients by the
    integer pixel deltas, most-significant chunk last.
    """

    def __init__(self, name: str, in_start: AFixValue, dx_raw, dy_raw):
        self.name = name
        self.in_start = in_start
        # `pending.grads.X.d(0).raw.asSInt`: the AFix storage word of a signed
        # format is already an Amaranth `signed(bitWidth)` (`AFixValue` types
        # it from the *shape*, not from whatever the record slice hands it),
        # so `.asSInt` is the identity here.
        self.dx_raw = Value.cast(dx_raw)
        self.dy_raw = Value.cast(dy_raw)
        assert self.dx_raw.shape().signed and self.dy_raw.shape().signed
        #: `inStart.raw` as a plain `Bits`, and its `.asSInt` view.
        self.start_raw = Value.cast(in_start.raw)
        self.start_bits = self.start_raw.as_unsigned()
        self.start_width = in_start.afix_shape.bit_width
        self.acc_width = self.start_width + 18
        self.acc = Signal(signed(self.acc_width), init=0, name=f"{name}_acc")
        self.dx_term = Signal(signed(self.acc_width), init=0, name=f"{name}_dxTerm")
        self.dy_term = Signal(signed(self.acc_width), init=0, name=f"{name}_dyTerm")
        self._scaled_dx = None
        self._scaled_dy = None

    # -- scala:110-118 -----------------------------------------------------

    def load(self, dx_sub_raw, dy_sub_raw, param_adjust, dx_neg, dy_neg) -> list:
        """``def load(dxSubRaw, dySubRaw, paramAdjust, dxNeg, dyNeg)``."""
        sw, aw = self.start_width, self.acc_width
        mul_width = sw + len(Value.cast(dx_sub_raw))
        # corrRaw = (dx*dxSub + dy*dySub) >> 4   -- SInt(startWidth)
        corr_raw = _sar(
            _s_add(
                _mul_by_subpixel(self.dx_raw, dx_sub_raw),
                _mul_by_subpixel(self.dy_raw, dy_sub_raw),
                mul_width,
            ),
            4,
        )
        # `(startRaw + corrRaw.resize(startWidth))(startWidth-1 downto 0)` -- Bits
        corrected_raw = _s_add(self.start_raw, corr_raw, sw).as_unsigned()
        adjusted_bits = Mux(param_adjust, corrected_raw, self.start_bits)
        return [
            self.acc.eq(_sext(adjusted_bits.as_signed(), aw)),
            self.dx_term.eq(_sign_adjust(self.dx_raw, dx_neg, aw)),
            self.dy_term.eq(_sign_adjust(self.dy_raw, dy_neg, aw)),
        ]

    # -- scala:120-132 -----------------------------------------------------

    def build_scaled(self, m: Module, dx_chunk, dy_chunk) -> None:
        """The two ``scaleByChunk`` nets, built once and unconditionally."""
        self._scaled_dx = _scale_by_chunk(m, self.dx_term, dx_chunk, f"{self.name}_scaledDx")
        self._scaled_dy = _scale_by_chunk(m, self.dy_term, dy_chunk, f"{self.name}_scaledDy")

    def step(self) -> list:
        """``def step(dxChunk: UInt, dyChunk: UInt)``."""
        aw = self.acc_width
        sum_width = aw + 2
        next_acc = _sext(
            _s_add(
                _s_add(_sext(self.acc, sum_width), _sext(self._scaled_dx, sum_width), sum_width),
                _sext(self._scaled_dy, sum_width),
                sum_width,
            ),
            aw,
        )
        return [
            self.acc.eq(next_acc),
            self.dx_term.eq(_shl_keep(self.dx_term, 2, aw)),
            self.dy_term.eq(_shl_keep(self.dy_term, 2, aw)),
        ]

    # -- scala:134 ---------------------------------------------------------

    @property
    def result_bits(self) -> Value:
        """``def resultBits: Bits`` -- the low ``startWidth`` bits of ``acc``."""
        return self.acc.as_unsigned()[: self.start_width]


# ---------------------------------------------------------------------------
# the component
# ---------------------------------------------------------------------------


class TriangleSetup(wiring.Component):
    """``case class TriangleSetup(c: Config, formalStrong: Boolean = false)``."""

    def __init__(self, c: Config | None = None, formal_strong: bool = False):
        self.c = c = c if c is not None else Config.voodoo1()
        self.formal_strong = formal_strong
        super().__init__(
            {
                "i": In(stream.Signature(triangle_setup_input_layout(c))),
                "o": Out(stream.Signature(triangle_setup_output_layout(c))),
            }
        )

    # -- scala:25-42 -------------------------------------------------------

    def _copy_setup_input(self, dst, src) -> list:
        """``def copySetupInput(dst: Input, src: Input)``.

        Field by field rather than one packed copy, exactly as the Scala
        writes it.  It happens to cover every field of ``Input``, so the two
        are bit-identical -- but ``.start.raw := .start.raw`` (a raw bit copy,
        §5.5) rather than ``:=`` on the ``AFix`` is what the Scala does, and
        that distinction matters for any field whose formats ever differ.
        """
        out = [dst.tri_with_sign.eq(src.tri_with_sign)]
        for name in GRADIENT_BUNDLE_ALL:
            d, s = getattr(dst.grads, name), getattr(src.grads, name)
            out += [d.start.raw.eq(s.start.raw), d.d.eq(s.d)]
        out += [
            dst.hi_alpha.start.raw.eq(src.hi_alpha.start.raw),
            dst.hi_alpha.d_ad_x.raw.eq(src.hi_alpha.d_ad_x.raw),
            dst.hi_alpha.d_ad_y.raw.eq(src.hi_alpha.d_ad_y.raw),
            dst.tex_hi.s_start.raw.eq(src.tex_hi.s_start.raw),
            dst.tex_hi.t_start.raw.eq(src.tex_hi.t_start.raw),
            dst.tex_hi.d_sd_x.raw.eq(src.tex_hi.d_sd_x.raw),
            dst.tex_hi.d_td_x.raw.eq(src.tex_hi.d_td_x.raw),
            dst.tex_hi.d_sd_y.raw.eq(src.tex_hi.d_sd_y.raw),
            dst.tex_hi.d_td_y.raw.eq(src.tex_hi.d_td_y.raw),
            dst.config.eq(src.config),
        ]
        if self.c.trace.enabled:
            out.append(dst.trace.eq(src.trace))
        return out

    # -- scala:44-60 -------------------------------------------------------

    def _copy_setup_output(self, dst, src) -> list:
        """``def copySetupOutput(dst: Output, src: Output)``.

        Deliberately **incomplete**: the eleven ``start`` fields are left out,
        because they come from the ``IterChannel`` accumulators instead
        (scala:204-214).  Do not "fix" it into a whole-bundle copy.
        """
        out = [
            dst.coeffs.eq(src.coeffs),
            dst.edge_start.eq(src.edge_start),
            dst.xrange.eq(src.xrange),
            dst.yrange.eq(src.yrange),
        ]
        for name in GRADIENT_BUNDLE_ALL:
            out.append(getattr(dst.grads, name).d.eq(getattr(src.grads, name).d))
        out += [
            dst.tex_hi.d_sd_x.eq(src.tex_hi.d_sd_x),
            dst.tex_hi.d_td_x.eq(src.tex_hi.d_td_x),
            dst.tex_hi.d_sd_y.eq(src.tex_hi.d_sd_y),
            dst.tex_hi.d_td_y.eq(src.tex_hi.d_td_y),
            dst.hi_alpha.d_ad_x.eq(src.hi_alpha.d_ad_x),
            dst.hi_alpha.d_ad_y.eq(src.hi_alpha.d_ad_y),
            dst.config.eq(src.config),
        ]
        if self.c.trace.enabled:
            out.append(dst.trace.eq(src.trace))
        return out

    def elaborate(self, platform):
        m = Module()
        c = self.c
        vfmt = AFixShape.from_q(c.vertex_format)
        cfmt = AFixShape.from_q(c.coefficient_format)

        # -- scala:16-21 ---------------------------------------------------
        state = Signal(SetupState, init=SetupState.Idle, name="state")
        # Plain `Reg(...)`: no reset in Spinal (see the module docstring).
        pending = Signal(triangle_setup_input_layout(c), reset_less=True, name="pending")
        output_reg = Signal(triangle_setup_output_layout(c), reset_less=True, name="outputReg")
        iter_index = Signal(3, init=0, name="iterIndex")
        dx_mag_work = Signal(16, init=0, name="dxMagWork")
        dy_mag_work = Signal(16, init=0, name="dyMagWork")

        # -- scala:137-199: the eleven IterChannels ------------------------
        g = pending.grads
        channels = [
            _IterChannel("redStart", g.red_grad.start, g.red_grad.d[0].raw, g.red_grad.d[1].raw),
            _IterChannel("greenStart", g.green_grad.start,
                         g.green_grad.d[0].raw, g.green_grad.d[1].raw),
            _IterChannel("blueStart", g.blue_grad.start,
                         g.blue_grad.d[0].raw, g.blue_grad.d[1].raw),
            _IterChannel("depthStart", g.depth_grad.start,
                         g.depth_grad.d[0].raw, g.depth_grad.d[1].raw),
            _IterChannel("alphaStart", g.alpha_grad.start,
                         g.alpha_grad.d[0].raw, g.alpha_grad.d[1].raw),
            _IterChannel("wStart", g.w_grad.start, g.w_grad.d[0].raw, g.w_grad.d[1].raw),
            _IterChannel("sStart", g.s_grad.start, g.s_grad.d[0].raw, g.s_grad.d[1].raw),
            _IterChannel("tStart", g.t_grad.start, g.t_grad.d[0].raw, g.t_grad.d[1].raw),
            _IterChannel("hiSStart", pending.tex_hi.s_start,
                         pending.tex_hi.d_sd_x.raw, pending.tex_hi.d_sd_y.raw),
            _IterChannel("hiTStart", pending.tex_hi.t_start,
                         pending.tex_hi.d_td_x.raw, pending.tex_hi.d_td_y.raw),
            _IterChannel("hiAlphaStart", pending.hi_alpha.start,
                         pending.hi_alpha.d_ad_x.raw, pending.hi_alpha.d_ad_y.raw),
        ]
        assert tuple(ch.name for ch in channels) == ITER_CHANNELS
        op = self.o.payload
        results = [getattr(op.grads, name).start.raw for name in GRADIENT_BUNDLE_ALL] + [
            op.tex_hi.s_start.raw,
            op.tex_hi.t_start.raw,
            op.hi_alpha.start.raw,
        ]

        # -- scala:201-214: the output side --------------------------------
        m.d.comb += [
            self.i.ready.eq(state == SetupState.Idle),
            self.o.valid.eq(state == SetupState.Done),
        ]
        m.d.comb += self._copy_setup_output(op, output_reg)
        for target, ch in zip(results, channels):
            m.d.comb += target.eq(ch.result_bits)

        # -- scala:216-219 -------------------------------------------------
        with m.If((state == SetupState.Idle) & fire(self.i)):
            m.d.sync += self._copy_setup_input(pending, self.i.payload)
            m.d.sync += state.eq(SetupState.Prep)

        # ==================================================================
        # scala:221-307 -- Prep.
        #
        # All of the arithmetic below is unconditional combinational logic in
        # the generated Verilog (Spinal hoists a signal declared inside a
        # `when` out of it); only the register writes are gated on the state.
        # ==================================================================
        tri = pending.tri_with_sign.tri
        sign_bit = pending.tri_with_sign.sign_bit

        # `reduceBalancedTree` on three elements splits (0,1),(2), i.e.
        # min(min(a, b), c).  AFix `min`/`max` are `Mux(l < r, ...)` and the
        # mux format is the bounding box (§5.4), so all three stay SQ(16,4).
        xmin_raw = tri[0][0].min(tri[1][0]).min(tri[2][0])
        xmax_raw = tri[0][0].max(tri[1][0]).max(tri[2][0])

        xrange0 = Signal(vfmt, name="xrange0")
        xrange1 = Signal(vfmt, name="xrange1")
        yrange0 = Signal(vfmt, name="yrange0")
        yrange1 = Signal(vfmt, name="yrange1")
        # floor(0) -> (2047, -2048, 0) 12 bits; ceil(0) and roundHalfDown(0)
        # -> (2048, -2048, 0) **13** bits, because maxRaw rounds up past
        # 2**11 - 1 (§6.3).  fixTo then has drop = -4 < 0, so the rounding
        # step is a no-op and only `sat` + the widening assignment happen
        # (§8.5) -- for ceil/roundHalfDown that `sat` is a real clamp.
        m.d.comb += xrange0.eq(xmin_raw.floor(0).fix_to(c.vertex_format))
        m.d.comb += xrange1.eq(xmax_raw.ceil(0).fix_to(c.vertex_format))
        m.d.comb += yrange0.eq(tri[0][1].round_half_down(0).fix_to(c.vertex_format))
        m.d.comb += yrange1.eq(tri[2][1].round_half_down(0).fix_to(c.vertex_format))

        # -- scala:233-261: the three edge functions -----------------------
        coeffs_vec = [Signal(coefficients_layout(c), name=f"coeffsVec_{i}") for i in range(3)]
        edge_bias_lsb = cfmt.from_raw_int(1)   # `edgeBiasLsb.raw := 1`
        coeff_zero = cfmt.from_raw_int(0)      # `coeffZero.raw := 0`

        for idx, (i0, i1) in enumerate(EDGE_PAIRS):
            v0, v1 = tri[i0], tri[i1]
            a_raw = v0[1] - v1[1]                       # SQ(16,4)-SQ(16,4) -> 17b
            b_raw = v1[0] - v0[0]
            c_raw = v0[0] * v1[1] - v1[0] * v0[1]       # 32b x 32b subtract -> 32b, exp -8
            a_coeff = afix_mux(sign_bit, -a_raw, a_raw)
            b_coeff = afix_mux(sign_bit, -b_raw, b_raw)
            c_coeff = afix_mux(sign_bit, -c_raw, c_raw)
            a_fixed = a_coeff.fix_to(c.coefficient_format)
            b_fixed = b_coeff.fix_to(c.coefficient_format)
            c_fixed = c_coeff.fix_to(c.coefficient_format)
            # The D3D/OpenGL top-left fill rule: an edge that is not top-left
            # is nudged one coefficient LSB negative so a shared edge is
            # owned by exactly one triangle.
            is_top_left = (a_fixed > coeff_zero) | (
                (a_fixed == coeff_zero) & (b_fixed > coeff_zero)
            )
            m.d.comb += coeffs_vec[idx].a.eq(a_fixed)
            m.d.comb += coeffs_vec[idx].b.eq(b_fixed)
            m.d.comb += coeffs_vec[idx].c.eq(
                afix_mux(
                    is_top_left,
                    c_fixed,
                    (c_fixed - edge_bias_lsb).fix_to(c.coefficient_format),
                )
            )

        # -- scala:263-270: edge value at the bounding-box corner's centre --
        half_pixel = AFixShape.sq(3, 4).const(0.5)      # `AFix.SQ(3 bits, 4 bits) := 0.5`
        x_center = Signal(vfmt, name="xCenter")
        y_center = Signal(vfmt, name="yCenter")
        m.d.comb += x_center.eq((xrange0 + half_pixel).fix_to(c.vertex_format))
        m.d.comb += y_center.eq((yrange0 + half_pixel).fix_to(c.vertex_format))

        edge_start_vec = [Signal(cfmt, name=f"edgeStartVec_{i}") for i in range(3)]
        for idx in range(3):
            coeff = coeffs_vec[idx]
            # The design's single `.truncated` (§6.6): a 51-bit exp -12
            # expression floored to exp -8 and then **wrapped** into the
            # 34-bit coefficient format -- no saturation.
            expr = coeff.a * x_center + coeff.b * y_center + coeff.c
            m.d.comb += edge_start_vec[idx].eq(expr.truncated())

        # -- scala:272-283: sub-pixel correction and the integer pixel delta
        param_adjust = pending.config.fbz_color_path.param_adjust
        frac_ax = tri[0][0].raw.as_unsigned()[0:4]
        dx_sub_raw = Signal(4, name="dxSubRaw")
        frac_ay = tri[0][1].raw.as_unsigned()[0:4]
        dy_sub_raw = Signal(4, name="dySubRaw")
        # `(U(8, 5 bits) - frac.resize(5 bits))(3 downto 0)`: the subtract
        # wraps at 5 bits and only the low 4 are kept, so the assignment's
        # truncation does the same job.
        m.d.comb += dx_sub_raw.eq(Const(8, 5) - _zext(frac_ax, 5))
        m.d.comb += dy_sub_raw.eq(Const(8, 5) - _zext(frac_ay, 5))

        start_x_pix = xrange0.floor(0).as_signed()          # SInt(12)
        start_y_pix = yrange0.floor(0).as_signed()          # SInt(12)
        a_x_pix = tri[0][0].round_half_down(0).as_signed()  # SInt(13)
        a_y_pix = tri[0][1].round_half_down(0).as_signed()  # SInt(13)
        # `SInt - SInt` resizes both to max(12, 13) = 13 and wraps there,
        # *then* `.resize(16)` sign-extends.  Not a 16-bit subtract.
        dx_pix = Signal(signed(16), name="dxPix")
        dy_pix = Signal(signed(16), name="dyPix")
        m.d.comb += dx_pix.eq(_sext(_s_sub(start_x_pix, a_x_pix, 13), 16))
        m.d.comb += dy_pix.eq(_sext(_s_sub(start_y_pix, a_y_pix, 13), 16))
        dx_neg = dx_pix[-1]
        dy_neg = dy_pix[-1]

        with m.If(state == SetupState.Prep):
            # scala:285-298
            for idx in range(3):
                m.d.sync += output_reg.coeffs[idx].eq(coeffs_vec[idx])
                m.d.sync += output_reg.edge_start[idx].eq(edge_start_vec[idx])
            m.d.sync += output_reg.xrange[0].eq(xrange0)
            m.d.sync += output_reg.xrange[1].eq(xrange1)
            m.d.sync += output_reg.yrange[0].eq(yrange0)
            m.d.sync += output_reg.yrange[1].eq(yrange1)
            for name in GRADIENT_BUNDLE_ALL:
                out_g, in_g = getattr(output_reg.grads, name), getattr(pending.grads, name)
                m.d.sync += out_g.start.raw.eq(in_g.start.raw)
                m.d.sync += out_g.d.eq(in_g.d)
            m.d.sync += output_reg.tex_hi.eq(pending.tex_hi)
            m.d.sync += output_reg.hi_alpha.eq(pending.hi_alpha)
            m.d.sync += output_reg.config.eq(pending.config)
            if c.trace.enabled:
                m.d.sync += output_reg.trace.eq(pending.trace)

            # scala:300-306
            m.d.sync += dx_mag_work.eq(_signed_magnitude(dx_pix))
            m.d.sync += dy_mag_work.eq(_signed_magnitude(dy_pix))
            for ch in channels:
                m.d.sync += ch.load(dx_sub_raw, dy_sub_raw, param_adjust, dx_neg, dy_neg)
            m.d.sync += iter_index.eq(0)
            m.d.sync += state.eq(SetupState.Iterate)

        # -- scala:309-320: Iterate ----------------------------------------
        dx_chunk = dx_mag_work[0:2]
        dy_chunk = dy_mag_work[0:2]
        for ch in channels:
            ch.build_scaled(m, dx_chunk, dy_chunk)

        with m.If(state == SetupState.Iterate):
            for ch in channels:
                m.d.sync += ch.step()
            m.d.sync += dx_mag_work.eq(dx_mag_work >> 2)
            m.d.sync += dy_mag_work.eq(dy_mag_work >> 2)
            with m.If(iter_index == 7):
                m.d.sync += state.eq(SetupState.Done)
            with m.Else():
                m.d.sync += iter_index.eq((iter_index + 1)[:3])

        # -- scala:322-324 -------------------------------------------------
        with m.If((state == SetupState.Done) & self.o.ready):
            m.d.sync += state.eq(SetupState.Idle)

        return m
