"""Amaranth mirror of ``fpga/src/voodoo/diff/AfixOps.scala``.

The differential reference for :mod:`avoodoo.lib.fixed`: one combinational
module exercising every ``AFix`` operation the Voodoo core uses, on the named
Q formats of ``Config.voodoo1()``, so ``make diff MOD=AfixOps`` can compare it
against SpinalHDL 1.15.0's own elaboration port by port (plan §4.2, task 0.2).

Read the Scala alongside this file: the section numbering, the port-name stems
and the order of operations are deliberately identical, and the *output widths
are not written down anywhere here* — they fall out of ``AFixShape``'s
bound-derived width rule, which is the thing under test.

This is diff-only code, which plan §6 would rather keep out of ``avoodoo/``;
it lives here because the operations it wires up are ``fixed.py``'s own and
because ``tools/diff/registry.py`` names this path.  It instantiates nothing
and is never part of a build.

Port naming: the Spinal names below are written the way ``AfixOps.scala``
spells them (``add_vertex``), but the *emitted* Amaranth port names double
every underscore (``add__vertex``).  That is not a whim: ``ports.py`` splits a
Spinal port name on ``_`` into a path, and the harness joins an Amaranth path
with ``__`` -- so ``add_vertex`` has the path ``["add", "vertex"]`` and only
``add__vertex`` pairs with it.  Component-level ports have no bundle nesting
for the split to reflect, so this is mechanical padding rather than structure;
:func:`_port_name` is the one place it happens.  Zero-width
results are skipped on both sides (``AFix(UQ(0, 8))`` — see
``docs/amaranth/afix-semantics.md`` §8.1); none of the formats below produce
one.
"""

from __future__ import annotations

from amaranth import Elaboratable, Module, Signal
from amaranth.lib import data

from avoodoo.config import QFormat, SQ, UQ, Config
from avoodoo.lib.fixed import AFixShape, AFixValue, RoundType, sat_unsigned

__all__ = ["AfixOps", "COLOR_MUL_FORMAT"]

#: ``ColorCombine.scala:226`` — the SQ(10,0) * UQ(0,8) product format.
COLOR_MUL_FORMAT = QFormat(19, 8, True)


def _port_name(spinal_name: str) -> str:
    """``add_vertex`` -> ``add__vertex``; see the module docstring."""
    return spinal_name.replace("_", "__")


class AfixOps(Elaboratable):
    """Every AFix operation in the core, on the core's own formats."""

    def __init__(self, c: Config | None = None):
        self.c = c = c if c is not None else Config.voodoo1()

        # Format table; `key` is the port-name stem (AfixOps.scala `formats`).
        # UQ(0, 8) is deliberately absent on both sides: it elaborates to a
        # zero-bit AFix and Verilog has no zero-bit port.
        self.formats: list[tuple[str, QFormat]] = [
            ("vertex", c.vertex_format),                 # SQ(16, 4)
            ("vcolor", c.v_color_format),                # SQ(24, 12)
            ("vdepth", c.v_depth_format),                # SQ(32, 12)
            ("coeff", c.coefficient_format),             # SQ(34, 8)
            ("tex", c.tex_coords_format),                # SQ(32, 18)
            ("w", c.w_format),                           # SQ(32, 30)
            ("texhi", c.tex_coords_hi_format),           # SQ(46, 30)
            ("texaccum", c.tex_coords_accum_format),     # SQ(48, 18)
            ("waccum", c.w_accum_format),                # SQ(48, 30)
            ("colormul", COLOR_MUL_FORMAT),              # Q(19, 8, true)
            ("u8", UQ(8, 0)),
        ]

        self._inputs: dict[str, Signal] = {}
        self._outputs: dict[str, Signal] = {}
        self._assignments: list[tuple[Signal, object]] = []

        # Operands of these formats reach the arithmetic through a
        # `data.StructLayout` field instead of straight off the port, so the
        # lockstep diff covers the way the core actually stores an AFix -- in
        # a Bundle.  That path is not cosmetic: `data.View` hands the field's
        # shape an *unsigned* slice of the packed signal, and a regression
        # there reads every negative operand as a large positive one (fixed in
        # AFixValue.__init__; found by the SpanRasterizer diff, not by this
        # module, which is why it is wired up here now).  The ports are
        # untouched, so both sides still have exactly 282 of them.
        layout_routed = ("vertex", "coeff", "colormul")

        shapes: dict[str, AFixShape] = {}
        for key, fmt in self.formats:
            shape = AFixShape.from_q(fmt)
            for side in ("a", "b"):
                name = f"{side}_{key}"
                self._inputs[name] = Signal(shape.bit_width, name=_port_name(name))
                shapes[name] = shape

        self._bundled = {n: s for n, s in shapes.items()
                         if n.split("_", 1)[1] in layout_routed}
        self.bundle = Signal(data.StructLayout(self._bundled), name="bundle")

        self.ops: dict[str, tuple[AFixValue, AFixValue]] = {}
        for key, _fmt in self.formats:
            self.ops[key] = (self._operand(f"a_{key}", shapes[f"a_{key}"]),
                             self._operand(f"b_{key}", shapes[f"b_{key}"]))

        self._build()

    # -- port plumbing -----------------------------------------------------

    def _operand(self, name: str, shape: AFixShape) -> AFixValue:
        """The AFix an input port carries, via the bundle where routed."""
        if name in self._bundled:
            return self.bundle[name]
        return AFixValue.from_raw(shape, self._inputs[name])

    def _mk_in(self, name: str, shape: AFixShape) -> AFixValue:
        """An input port sized to ``AFix(fmt).raw``, reinterpreted as that AFix.

        The width comes from the elaborated shape, never from ``QFormat.width``
        (they differ for unsigned formats), exactly as the Scala's ``mkIn``.
        """
        port = Signal(shape.bit_width, name=_port_name(name))
        self._inputs[name] = port
        return AFixValue.from_raw(shape, port)

    def _emit(self, name: str, value: AFixValue) -> None:
        """Publish an AFix result on a port of its own raw width."""
        if value.afix_shape.bit_width == 0:
            return  # zero-width result: skipped on the Spinal side too
        self._emit_value(name, value.raw)

    def _emit_value(self, name: str, value) -> None:
        port = Signal(len(value), name=_port_name(name))
        self._outputs[name] = port
        self._assignments.append((port, value))

    def ports(self) -> list[Signal]:
        return list(self._inputs.values()) + list(self._outputs.values())

    def elaborate(self, platform):
        m = Module()
        for name in self._bundled:
            m.d.comb += self.bundle[name].raw.eq(self._inputs[name])
        for port, value in self._assignments:
            m.d.comb += port.eq(value)
        return m

    # -- the operations ----------------------------------------------------

    def _build(self) -> None:
        c = self.c

        # 1. Arithmetic: width growth is the thing under test.
        for key, _fmt in self.formats:
            a, b = self.ops[key]
            self._emit(f"add_{key}", a + b)
            self._emit(f"sub_{key}", a - b)
            self._emit(f"mul_{key}", a * b)
            self._emit(f"neg_{key}", -a)

        # 2. fixTo back to the source format, once per rounding mode.
        #    Note every one of these has drop == 0 (the sum keeps the source
        #    exp), so the modes coincide by construction -- what this group
        #    really pins down is the saturation and the final assignment.
        modes = [
            ("default", RoundType.FLOOR),          # getTrunc.rounding (§1.3)
            ("floor", RoundType.FLOOR),
            ("ceil", RoundType.CEIL),
            ("roundhalfdown", RoundType.ROUND_HALF_DOWN),
            ("roundhalfup", RoundType.ROUND_HALF_UP),
            ("roundtoeven", RoundType.ROUND_HALF_TO_EVEN),
            ("scrap", RoundType.SCRAP),
        ]
        for key, fmt in self.formats:
            a, b = self.ops[key]
            total = a + b
            for label, mode in modes:
                self._emit(f"fixto_{label}_{key}", total.fix_to(fmt, mode))

        # 3. Standalone rounding to an integer exponent, plus the .asSInt that
        #    always follows it in TriangleSetup / SpanWalker / PixelPipeline.
        for key, fmt in self.formats:
            if not fmt.signed:
                continue
            a, _b = self.ops[key]
            self._emit(f"floor0_{key}", a.floor(0))
            self._emit(f"ceil0_{key}", a.ceil(0))
            self._emit(f"roundhalfdown0_{key}", a.round_half_down(0))
            self._emit(f"roundhalfup0_{key}", a.round_half_up(0))
            self._emit(f"round0_{key}", a.round(0))
            self._emit(f"floortozero0_{key}", a.floor_to_zero(0))
            self._emit(f"ceiltoinf0_{key}", a.ceil_to_inf(0))
            self._emit_value(f"floor0_assint_{key}", a.floor(0).as_signed())

        # 4. The three saturation call shapes.
        for key, _fmt in self.formats:
            a, _b = self.ops[key]
            self._emit(f"sat1_{key}", a.sat_bits(1))
            self._emit(f"sat255_{key}", a.sat(255, 0))
            self._emit(f"sat255e0_{key}", a.sat(255, 0, exp=0))

        # 5. `.truncated`: TriangleSetup's edge accumulation, 51 bits wrapped
        #    into SQ(34, 8) with no fixTo (§6.6).
        av, bv = self.ops["vertex"]
        ac, _bc = self.ops["coeff"]
        wide = ac * av + ac * bv + ac
        self._emit("truncated_coeff",
                   wide.converted_to(c.coefficient_format, truncated=True))
        self._emit("truncated_src", wide)

        # 6. Cross-format fixTo pairs taken verbatim from the core.
        hi, _ = self.ops["texhi"]
        step, _ = self.ops["tex"]
        self._emit("raster_histep_fixto_texhi", (hi + step).fix_to(c.tex_coords_hi_format))
        self._emit("raster_hi_fixto_texaccum", hi.fix_to(c.tex_coords_accum_format))

        x, _ = self.ops["vertex"]
        vertex_shape = AFixShape.from_q(c.vertex_format)
        one = vertex_shape.const(1.0)
        self._emit("raster_x_plus_one_fixto_vertex", (x + one).fix_to(c.vertex_format))
        self._emit("setup_floor_fixto_vertex", x.floor(0).fix_to(c.vertex_format))
        self._emit("setup_ceil_fixto_vertex", x.ceil(0).fix_to(c.vertex_format))
        self._emit("setup_roundhalfdown_fixto_vertex",
                   x.round_half_down(0).fix_to(c.vertex_format))
        half = vertex_shape.const(0.5)
        self._emit("setup_halfpixel_fixto_vertex",
                   (x.floor(0).fix_to(c.vertex_format) + half).fix_to(c.vertex_format))

        col, col_grad = self.ops["vcolor"]
        self._emit("span_color_fixto_vcolor", (col + col_grad).fix_to(c.v_color_format))
        dep, dep_grad = self.ops["vdepth"]
        self._emit("span_depth_fixto_vdepth", (dep + dep_grad).fix_to(c.v_depth_format))
        wa, wg = self.ops["waccum"]
        self._emit("span_w_fixto_waccum", (wa + wg).fix_to(c.w_accum_format))
        self._emit("span_dydx_fixto_vcolor",
                   (col + col_grad - col_grad).fix_to(c.v_color_format))
        self._emit("span_neg_fixto_vcolor", (-col_grad).fix_to(c.v_color_format))
        self._emit("span_neg_fixto_coeff", (-self.ops["coeff"][1]).fix_to(c.coefficient_format))

        # 7. ColorCombine's exact chain.
        sub = self._mk_in("a_cc_sub", AFixShape.from_q(SQ(11, 0)))     # AFix.SQ(10 b, 0 b)
        factor = self._mk_in("a_cc_factor", AFixShape.from_q(UQ(9, 8)))  # AFix.UQ(1 b, 8 b)
        local = self._mk_in("a_cc_local", AFixShape.from_q(SQ(19, 8)))

        mul = (sub * factor).fix_to(COLOR_MUL_FORMAT)
        self._emit("cc_mul_fixto_colormul", mul)
        add = (mul + local).fix_to(COLOR_MUL_FORMAT)
        self._emit("cc_add_fixto_colormul", add)
        saturated = add.sat(255, 0, exp=0)
        self._emit("cc_sat255", saturated)
        self._emit("cc_sat_fixto_u8", saturated.fix_to(UQ(8, 0)))
        self._emit_value("cc_sat_fixto_u8_asuint", saturated.fix_to(UQ(8, 0)).as_unsigned())
        # clampAFixToFactor's saturation half; the fixTo(UQ(0, 8)) that follows
        # it in the core is the §8.1 zero-width trap, so only this is portable.
        self._emit("cc_sat255_raw", add.sat(255, 0))

        # 8. RegisterBank: a plain UInt saturating shrink, not an AFix one.
        pci_fifo_free = Signal(7, name=_port_name("a_pcififofree"))
        self._inputs["a_pcififofree"] = pci_fifo_free
        self._emit_value("regbank_sat1", sat_unsigned(pci_fifo_free, 1))
