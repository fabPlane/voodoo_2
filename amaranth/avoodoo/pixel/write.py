"""``Write``: turn a pixel write into a masked 32-bit framebuffer-plane write.

1:1 port of ``ext/SpinalVoodoo/src/voodoo/pixel/Write.scala`` (SpinalHDL
1.15.0).

The module is a single combinational ``translateFrom``: no registers, no
state, latency 0.  Two of them are instantiated in ``PixelPipeline`` -- one
for the colour plane and one for the aux plane -- which is why the address
comes in on the payload (``fbBaseAddr`` / ``fbPixelStride``) rather than from
a register.

The Scala body, for reference::

    o.fbWrite.translateFrom(i.fromPipeline) { (out, in) =>
      val planeAddress = FramebufferAddressMath.planeAddress(
        in.fbBaseAddr, in.coords(0).asUInt, in.coords(1).asUInt, in.fbPixelStride)
      val laneHi = planeAddress(1)
      out.address := alignedWordAddress(planeAddress)
      out.data    := packWordLane(in.data, laneHi)
      out.mask    := wordLaneMask(laneHi)
    }

``planeAddress``, ``alignedWordAddress``, ``packWordLane`` and
``wordLaneMask`` are the shared helpers in :mod:`avoodoo.core.core_types`
(``FramebufferAddressMath.scala`` and the ``package object voodoo``).

Port names: the Scala bundles are called ``i`` and ``o``, not ``io``, so the
Verilog ports are ``i_fromPipeline_*`` / ``o_fbWrite_*`` and the Amaranth
signature nests the same way (``i__from_pipeline__valid``,
``o__fb_write__payload__address``).
"""

from __future__ import annotations

from amaranth import Module, unsigned
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from avoodoo.config import Config
from avoodoo.core.core_types import (
    FbRouting,
    FramebufferWriteReq,
    PixelCoords,
    TRACE_PIXEL_KEY_LAYOUT,
    aligned_word_address,
    pack_word_lane,
    plane_address,
    word_lane_mask,
)

__all__ = ["WriteInput", "WritePreDither", "aux_from_pre_dither", "Write"]


class WriteInput(data.StructLayout):
    """``Write.Input(c)``."""

    def __init__(self, c: Config) -> None:
        self.c = c
        members = {
            "coords": PixelCoords(c),
            "data": unsigned(16),
            "fb_base_addr": unsigned(c.address_width),
            "fb_pixel_stride": unsigned(11),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(members)


class WritePreDither(data.StructLayout):
    """``Write.PreDither(c)`` -- RGB888 plus every ``Write.Input`` field.

    Produced by the triangle, fastfill and LFB-bypass paths before the shared
    ``Dither`` instance.  ``fbBaseAddr`` / ``auxBaseAddr`` / ``fbPixelStride``
    are Scala ``def``s onto ``routing``; here they are
    ``view.routing.color_base_addr`` and friends.
    """

    def __init__(self, c: Config) -> None:
        self.c = c
        members = {
            "r": unsigned(8),
            "g": unsigned(8),
            "b": unsigned(8),
            "coords": PixelCoords(c),
            "enable_dithering": unsigned(1),
            "dither_algorithm": unsigned(1),
            "depth_alpha": unsigned(16),
            "rgb_write": unsigned(1),
            "aux_write": unsigned(1),
            "routing": FbRouting(c),
        }
        if c.trace.enabled:
            members["trace"] = TRACE_PIXEL_KEY_LAYOUT
        super().__init__(members)


def aux_from_pre_dither(m: Module, c: Config, pd, trace=None):
    """``Write.Input.auxFromPreDither(c, pd, trace)``.

    Returns a ``WriteInput`` view over a freshly declared signal, driven
    combinationally from ``pd``.

    The two sibling builders in the Scala companion --
    ``Write.Input.colorFromDither`` (needs ``Dither.Output``) and
    ``Write.PreDither.fromFramebufferAccess`` (needs
    ``FramebufferAccess.Output``) -- land with those modules in Phase 4; they
    are pure payload plumbing for ``PixelPipeline`` and nothing in ``Write``
    itself uses them.
    """
    from amaranth import Signal

    out = Signal(WriteInput(c))
    m.d.comb += [
        out.coords.eq(pd.coords),
        out.data.eq(pd.depth_alpha),
        out.fb_base_addr.eq(pd.routing.aux_base_addr),
        out.fb_pixel_stride.eq(pd.routing.pixel_stride),
    ]
    if c.trace.enabled and trace is not None:
        m.d.comb += out.trace.eq(trace)
    return out


class Write(wiring.Component):
    """``case class Write(c: Config) extends Component``."""

    def __init__(self, c: Config | None = None) -> None:
        c = c if c is not None else Config.voodoo1()
        self.c = c
        super().__init__(
            {
                # `val i = new Bundle { val fromPipeline = slave Stream(...) }`
                "i": In(wiring.Signature(
                    {"from_pipeline": Out(stream.Signature(WriteInput(c)))}
                )),
                # `val o = new Bundle { val fbWrite = master Stream(...) }`
                "o": Out(wiring.Signature(
                    {"fb_write": Out(stream.Signature(FramebufferWriteReq(c)))}
                )),
            }
        )

    def elaborate(self, platform):
        m = Module()

        src = self.i.from_pipeline
        dst = self.o.fb_write

        # `translateFrom` = `arbitrationFrom` + the payload transform.
        m.d.comb += [dst.valid.eq(src.valid), src.ready.eq(dst.ready)]

        # `in.coords(0).asUInt` / `in.coords(1).asUInt`: the SInt(12) screen
        # coordinates are reinterpreted, not sign-extended.
        plane_addr = plane_address(
            src.payload.fb_base_addr,
            src.payload.coords.x.as_unsigned(),
            src.payload.coords.y.as_unsigned(),
            src.payload.fb_pixel_stride,
        )
        lane_hi = plane_addr[1]

        m.d.comb += [
            dst.payload.address.eq(aligned_word_address(plane_addr)),
            dst.payload.data.eq(pack_word_lane(src.payload.data, lane_hi)),
            dst.payload.mask.eq(word_lane_mask(lane_hi)),
        ]
        return m
