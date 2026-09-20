"""Float -> fixed converter, 1:1 port of ``Fpxx2AFix``.

Scala source: ``ext/SpinalVoodoo/src/voodoo/math/Fpxx2AFix.scala`` (71 lines).
Semantics write-up: ``docs/amaranth/afix-semantics.md`` §9.  Plan task 1.3.

Instantiated exactly once in the design, by ``PciFifo`` (``PciFifo.scala:200``)
as ``Fpxx2AFix(20 bits, 30 bits, FpxxConfig.float32(), pipeStages = 1)``, for
the register bank's float aliases.  Downstream, ``PciFifo`` reads
``result.number.raw`` as a plain 51-bit two's-complement word and applies its
own per-register shift, so the ``AFix`` format here is really just a container
with a declared binary point.

The conversion is deliberately crude and the port keeps every rough edge:

* No NaN/Inf special-casing at all.  ``exp = 255`` makes ``shift = -109``,
  which takes the ``shift < 0`` branch and saturates.
* No denormal handling: the hidden bit is forced by ``{2'b01, mant}``.
* Truncation toward -inf (an arithmetic right shift), never rounding.
* ``ge0`` uses ``max(bias - fracNrBits, 0)``; with float32 and 30 fractional
  bits that is ``exp >= 97``.  Anything smaller converts to zero with
  ``overflow`` **clear** -- underflow is not flagged, only overflow is.

**Pipelining.** ``pipeStages >= 1`` inserts SpinalHDL's
``spinal.lib.misc.pipeline.StageLink`` between the two nodes, which the
generated Verilog shows to be a plain m2s (skid-free) register stage::

    n0_ready = n1_ready | ~n1_valid
    n1_valid <= n0_valid        when n0_ready   (sync reset to 0)
    n1_<payload> <= n0_<payload> when n0_ready  (NO reset)

The payload registers are reset-less on purpose: Spinal's plain ``Reg`` has no
reset, and giving them Amaranth's default zero reset would make the two
implementations disagree on ``result.payload`` during and just after reset,
which the differential test would (correctly) call a mismatch.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Mux, Signal, signed
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out
from amaranth.utils import ceil_log2

from avoodoo.lib.fixed import AFixShape
from avoodoo.lib.fpxx import FpxxConfig, FpxxFlagsLayout, FpxxLayout

__all__ = ["Fpxx2AFix", "result_layout"]


def result_layout(int_nr_bits: int, frac_nr_bits: int, *, generate_flags: bool = False):
    """The ``io.result`` payload bundle, in the Scala's declaration order."""
    fields: dict = {"number": AFixShape.sq(int_nr_bits, frac_nr_bits)}
    if generate_flags:
        fields["flags"] = FpxxFlagsLayout()
    fields["overflow"] = 1
    return data.StructLayout(fields)


class Fpxx2AFix(wiring.Component):
    """``class Fpxx2AFix(intNrBits, fracNrBits, c, pipeStages, generateFlags)``."""

    def __init__(
        self,
        int_nr_bits: int = 20,
        frac_nr_bits: int = 30,
        c: FpxxConfig | None = None,
        pipe_stages: int = 1,
        generate_flags: bool = False,
    ):
        c = c if c is not None else FpxxConfig.float32()
        assert c.ieee_like, "Can only handle IEEE compliant floats"
        assert int_nr_bits + frac_nr_bits + 2 > c.mant_size, "Not enough bits for SInt size"

        self.c = c
        self.int_nr_bits = int_nr_bits
        self.frac_nr_bits = frac_nr_bits
        self.pipe_stages = pipe_stages
        self.generate_flags = generate_flags

        super().__init__(
            {
                "op": In(stream.Signature(FpxxLayout(c))),
                "result": Out(
                    stream.Signature(
                        result_layout(int_nr_bits, frac_nr_bits,
                                      generate_flags=generate_flags)
                    )
                ),
            }
        )

    def elaborate(self, platform):
        m = Module()
        c = self.c
        int_bits, frac_bits = self.int_nr_bits, self.frac_nr_bits
        total = int_bits + frac_bits            # 50
        mant_w = c.mant_size + 2                # 25: hidden bit + a sign position

        # -- n0 ------------------------------------------------------------
        op = self.op.payload
        n0_sign = op.sign
        n0_ge0 = Signal(name="n0_ge0")
        m.d.comb += n0_ge0.eq((op.exp >= max(c.bias - frac_bits, 0)) & ~op.is_zero())

        # `_shift := (intNrBits - 1 + bias) - (U"00" @@ exp).asSInt`, SInt(exp_size + 2)
        n0_shift = Signal(signed(c.exp_size + 2), name="n0_shift")
        m.d.comb += n0_shift.eq(
            Const(int_bits - 1 + c.bias, signed(c.exp_size + 2))
            - Cat(op.exp, Const(0, 2)).as_signed()
        )

        # `mant_full := (U"01" @@ mant).asSInt`; `mant_2c := sign ? -mant_full | mant_full`
        n0_mant_full = Signal(signed(mant_w), name="n0_mant_full")
        m.d.comb += n0_mant_full.eq(Cat(op.mant, Const(0b01, 2)))
        n0_mant_2c = Signal(signed(mant_w), name="n0_mant_2c")
        m.d.comb += n0_mant_2c.eq(Mux(n0_sign, -n0_mant_full, n0_mant_full))

        n0_flags = op.flags() if self.generate_flags else None

        # -- the link ------------------------------------------------------
        n0_ready = Signal(name="n0_ready")
        if self.pipe_stages >= 1:
            n1_valid = Signal(name="n1_valid")                      # RegInit(False)
            n1_ge0 = Signal(reset_less=True, name="n1_ge0")
            n1_shift = Signal(signed(c.exp_size + 2), reset_less=True, name="n1_shift")
            n1_mant_2c = Signal(signed(mant_w), reset_less=True, name="n1_mant_2c")
            n1_flags = (
                Signal(FpxxFlagsLayout(), reset_less=True, name="n1_flags")
                if self.generate_flags else None
            )

            m.d.comb += n0_ready.eq(self.result.ready | ~n1_valid)
            with m.If(n0_ready):
                m.d.sync += [
                    n1_valid.eq(self.op.valid),
                    n1_ge0.eq(n0_ge0),
                    n1_shift.eq(n0_shift),
                    n1_mant_2c.eq(n0_mant_2c),
                ]
                if self.generate_flags:
                    m.d.sync += n1_flags.eq(n0_flags)
        else:  # DirectLink
            n1_valid = self.op.valid
            n1_ge0, n1_shift, n1_mant_2c = n0_ge0, n0_shift, n0_mant_2c
            n1_flags = n0_flags
            m.d.comb += n0_ready.eq(self.result.ready)

        # -- n1 ------------------------------------------------------------
        clip_w = ceil_log2(total)                                   # log2Up(50) = 6
        shift_clipped = n1_shift[0:clip_w]

        int_ = Signal(signed(total + 1), name="n1_int")
        ovfl = Signal(name="n1_ovfl")
        msb = n1_mant_2c[mant_w - 1]

        # `(mant_2c @@ S(0, (1 bit) + int + frac - mant_2c.width)) |>> shift_clipped`
        widened = Cat(Const(0, total + 1 - mant_w), n1_mant_2c).as_signed()

        with m.If((n1_shift >= total) | ~n1_ge0):
            m.d.comb += [int_.eq(0), ovfl.eq(0)]
        with m.Elif(n1_shift < 0):
            # `S(width, (width-1) -> mant_2c.msb, default -> !mant_2c.msb)`:
            # +max when mant_2c is positive, -min when it is negative.
            m.d.comb += [int_.eq(Cat((~msb).replicate(total), msb)), ovfl.eq(1)]
        with m.Else():
            m.d.comb += [int_.eq(widened >> shift_clipped), ovfl.eq(0)]

        m.d.comb += [
            self.result.payload.number.raw.eq(int_),
            self.result.payload.overflow.eq(ovfl),
            self.result.valid.eq(n1_valid),
            self.op.ready.eq(n0_ready),
        ]
        if self.generate_flags:
            m.d.comb += self.result.payload.flags.eq(n1_flags)
        return m
