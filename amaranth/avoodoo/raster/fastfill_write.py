"""``FastfillWrite``: ``Fastfill.Output`` -> ``Write.PreDither`` with register capture.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/raster/FastfillWrite.scala``
(81 lines, SpinalHDL 1.15.0), plan Phase 3.

The Scala's own explanation of why it captures: the rendering registers are
latched at fastfill command fire time to avoid a FIFO synchronisation
hazard -- ``pipelineBusyReg`` has a 2-cycle latency, so the register FIFO
can drain the *next* write before the busy signal stalls it.  On the fire
cycle the live register is still valid; ``RegNextWhen`` latches it for the
cycles after.

The body, statement for statement::

    val ffActive = RegInit(False)
    when(io.cmdFire)  { ffActive := True }
    when(!io.running) { ffActive := False }        // written last: wins

    val captured = RegNextWhen(io.regs, io.cmdFire) // plain Reg: no reset
    val regs = ffActive ? captured | io.regs

    io.output.translateFrom(io.pixels) { (out, pxIn) =>
      out.r/g/b        := regs.color1 (23..16 / 15..8 / 7..0)
      out.coords       := pxIn.coords
      when(regs.fbzMode.yOrigin) { out.coords(1) := yOriginSwapValue - coords(1) }
      out.enableDithering / ditherAlgorithm / rgbWrite / auxWrite := regs.fbzMode.*
      out.depthAlpha   := regs.zaColor(15 downto 0)
      out.routing      := regs.routing
      if (c.trace.enabled) out.trace := Trace.originPixelKey(Trace.Origin.fastfill)
    }

Fidelity notes:

* ``captured`` is a plain ``Reg`` (``RegNextWhen`` without ``init``) and is
  therefore ``reset_less=True`` here (plan §6); ``ffActive`` is ``RegInit``
  and resets.
* ``outputY`` is ``yOriginSwapValue.resize(nonFraction bits).asSInt - coordsY``:
  a 12-bit ``SInt`` subtraction that does **not** grow
  (:func:`avoodoo.lib.intops.s_sub`).  ``yOriginSwapValue`` is 10 bits
  unsigned, zero-extended to 12 *before* the ``asSInt``.
* ``regs`` is the Scala's name for the live/captured mux; the port of the
  same name is ``io.regs``, which Amaranth flattens to ``regs`` too, so the
  internal signal is spelled ``regsSel`` to keep the emitted Verilog free of
  a ``$``-disambiguated duplicate.

``FastfillWrite.Regs`` is the register view both this module and
:mod:`avoodoo.raster.fastfill_word_writer` take on their ``regs`` input;
``Regs.fromRegisterBank`` (``FastfillWrite.scala:71``) is ``PixelPipeline``
plumbing and lands with that module in Phase 7.
"""

from __future__ import annotations

from amaranth import Module, Mux, Signal, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import FbRouting, FbzMode, TraceOrigin
from avoodoo.lib.intops import s_sub, zext
from avoodoo.pixel.write import WritePreDither
from avoodoo.raster.fastfill import FastfillOutput

__all__ = ["FastfillWriteRegs", "FastfillWrite"]


class FastfillWriteRegs(data.StructLayout):
    """``FastfillWrite.Regs(c)`` (``FastfillWrite.scala:58``).

    The three Scala ``def``s -- ``drawColorBufferBase``, ``drawAuxBufferBase``,
    ``fbPixelStride`` -- are ``view.routing.color_base_addr`` /
    ``aux_base_addr`` / ``pixel_stride`` here.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        super().__init__({
            "color1": unsigned(32),
            "za_color": unsigned(32),
            "fbz_mode": FbzMode(),
            "routing": FbRouting(c),
            "y_origin_swap_value": unsigned(10),
        })


class FastfillWrite(wiring.Component):
    """``case class FastfillWrite(c: Config) extends Component``.

    Attributes
    ----------
    pixels : In(stream.Signature(FastfillOutput(c)))
        ``slave Stream(Fastfill.Output(c))``.
    output : Out(stream.Signature(WritePreDither(c)))
        ``master Stream(Write.PreDither(c))``.
    cmd_fire, running : In(1)
        ``io.cmdFire`` / ``io.running`` -- the ``Fastfill`` trigger beat and
        its ``running`` flag, wired by ``PixelPipeline``.
    regs : In(FastfillWriteRegs(c))
        ``in(FastfillWrite.Regs(c))`` -- the live register-bank view.
    """

    def __init__(self, c: Config | None = None) -> None:
        self.c = c = Config.voodoo1() if c is None else c
        super().__init__({
            "pixels": In(stream.Signature(FastfillOutput(c))),
            "output": Out(stream.Signature(WritePreDither(c))),
            "cmd_fire": In(1),
            "running": In(1),
            "regs": In(FastfillWriteRegs(c)),
        })

    def elaborate(self, platform):
        m = Module()
        c = self.c
        width = c.vertex_format.non_fraction

        # Capture-on-fire: latch all registers at command fire, mux live vs
        # captured (scala:27-33).
        ff_active = Signal(name="ffActive")                      # RegInit(False)
        with m.If(self.cmd_fire):
            m.d.sync += ff_active.eq(1)
        with m.If(~self.running):
            m.d.sync += ff_active.eq(0)

        captured = Signal(FastfillWriteRegs(c), reset_less=True, name="captured")
        with m.If(self.cmd_fire):
            m.d.sync += captured.eq(self.regs)
        regs = Signal(FastfillWriteRegs(c), name="regsSel")
        m.d.comb += regs.eq(Mux(ff_active, captured, self.regs))

        # `def outputY(coordsY: SInt): SInt` (scala:34-35): 12-bit SInt
        # subtraction, no growth.
        def output_y(coords_y):
            swap = zext(regs.y_origin_swap_value, width).as_signed()
            return s_sub(swap, coords_y)

        # `io.output.translateFrom(io.pixels) { (out, pxIn) => ... }` (scala:37-54)
        src, dst = self.pixels, self.output
        m.d.comb += [dst.valid.eq(src.valid), src.ready.eq(dst.ready)]
        out, px_in = dst.payload, src.payload
        m.d.comb += [
            out.r.eq(regs.color1[16:24]),
            out.g.eq(regs.color1[8:16]),
            out.b.eq(regs.color1[0:8]),
            # `out.coords.assignFromVec(pxIn.coords)`: PixelCoords.x <- coords(0)
            out.coords.x.eq(px_in.coords[0]),
            out.coords.y.eq(px_in.coords[1]),
        ]
        with m.If(regs.fbz_mode.y_origin):
            m.d.comb += out.coords.y.eq(output_y(px_in.coords[1]))
        m.d.comb += [
            out.enable_dithering.eq(regs.fbz_mode.enable_dithering),
            out.dither_algorithm.eq(regs.fbz_mode.dither_algorithm),
            out.depth_alpha.eq(regs.za_color[0:16]),
            out.rgb_write.eq(regs.fbz_mode.rgb_buffer_mask),
            out.aux_write.eq(regs.fbz_mode.aux_buffer_mask),
            out.routing.eq(regs.routing),
        ]
        if c.trace.enabled:
            # `Trace.originPixelKey(Trace.Origin.fastfill)` (Trace.scala:46)
            m.d.comb += [
                out.trace.primitive.valid.eq(1),
                out.trace.primitive.origin.eq(TraceOrigin.fastfill),
                out.trace.primitive.draw_id.eq(0),
                out.trace.primitive.primitive_id.eq(0),
                out.trace.pixel_seq.eq(0),
            ]
        return m
