"""Amaranth mirrors of ``fpga/src/voodoo/diff/DiffWrappers.scala``.

These are *diff-only* tops: they exist so the differential harness has a real
module to elaborate on both sides for constructs that are not Components in
Spinal (``StreamWhile`` is an ``object``, ``Fpxx`` a ``Bundle``).  Plan §6 says
``avoodoo/`` stays free of simulation-only code, so they live under ``tools/``.

Each wrapper must expose the *same* interface as its Scala twin.  The port
names differ by the harness's mechanical rule only (camelCase -> snake_case,
``_`` -> ``__`` between signature levels); see ``tools/diff/naming.py``.
"""

from __future__ import annotations

from amaranth import Cat, Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from avoodoo.lib.fpxx import FpxxConfig, FpxxLayout

__all__ = ["FpxxDiffWrap"]

# NB: ``StreamWhileDiffWrap`` is NOT here. ``StreamWhile`` is a real module of
# the port, so its diff wrapper ships with it in ``avoodoo/bus/stream_while.py``
# (agent p1-stream); the registry points straight at that one.


class FpxxDiffWrap(wiring.Component):
    """Mirror of ``FpxxDiffWrap`` in ``DiffWrappers.scala``.

    Every combinational ``Fpxx`` operation the core relies on, on ports.  ``a``
    and ``b`` carry the packed encoding, exactly as ``Fpxx.asBits`` produces it.
    """

    def __init__(self, c: FpxxConfig | None = None):
        c = c if c is not None else FpxxConfig.float32()
        self.c = c
        super().__init__(
            {
                "a": In(c.full_size),
                "b": In(c.full_size),
                "a_sign": Out(1),
                "a_exp": Out(c.exp_size),
                "a_mant": Out(c.mant_size),
                "a_is_zero": Out(1),
                "a_is_nan": Out(1),
                "a_is_inf": Out(1),
                "a_is_subnormal": Out(1),
                "a_abs": Out(c.full_size),
                "a_neg": Out(c.full_size),
                "a_full_mant": Out(c.mant_size + 1),
                "a_flags_nan": Out(1),
                "a_flags_inf": Out(1),
                "a_flags_sign": Out(1),
                "b_is_zero": Out(1),
                "b_is_nan": Out(1),
                "b_is_inf": Out(1),
                "const_nan": Out(c.full_size),
                "const_inf": Out(c.full_size),
                "const_zero": Out(c.full_size),
            }
        )

    def elaborate(self, platform):
        m = Module()
        c = self.c
        layout = FpxxLayout(c)

        a = layout(self.a)
        b = layout(self.b)

        m.d.comb += [
            self.a_sign.eq(a.sign),
            self.a_exp.eq(a.exp),
            self.a_mant.eq(a.mant),
            self.a_is_zero.eq(a.is_zero()),
            self.a_is_nan.eq(a.is_nan()),
            self.a_is_inf.eq(a.is_infinite()),
            self.a_is_subnormal.eq(a.is_subnormal()),
            self.a_abs.eq(a.abs().as_value()),
            self.a_neg.eq((~a).as_value()),
            self.a_full_mant.eq(a.full_mant()),
        ]

        flags = a.flags()
        m.d.comb += [
            self.a_flags_nan.eq(flags.nan),
            self.a_flags_inf.eq(flags.inf),
            self.a_flags_sign.eq(flags.sign),
            self.b_is_zero.eq(b.is_zero()),
            self.b_is_nan.eq(b.is_nan()),
            self.b_is_inf.eq(b.is_infinite()),
        ]

        # The Scala builds three scratch Fpxx values, drives `sign := False` and
        # then calls set_nan / set_inf / set_zero on each. Same here, so any
        # difference in what those methods leave untouched shows up in the diff.
        nan_const = Signal(layout, name="nanConst")
        inf_const = Signal(layout, name="infConst")
        zero_const = Signal(layout, name="zeroConst")
        m.d.comb += [nan_const.sign.eq(0), *nan_const.set_nan()]
        m.d.comb += [inf_const.sign.eq(0), *inf_const.set_inf()]
        m.d.comb += [zero_const.sign.eq(0), *zero_const.set_zero()]
        m.d.comb += [
            self.const_nan.eq(nan_const.as_value()),
            self.const_inf.eq(inf_const.as_value()),
            self.const_zero.eq(zero_const.as_value()),
        ]

        # `Cat`/`Const` are used above only through the Fpxx view helpers; keep
        # the imports honest for readers grepping this file.
        _ = (Cat, Const)
        return m
