"""``MemBusRouting`` -- the core-local hand-rolled Bmb fan-out helpers.

Port of ``ext/SpinalVoodoo/src/voodoo/bus/BmbRouting.scala`` (17 call sites
across ``CoreCpuFrontdoor``, ``TextureMemSubsystem``, ``Lfb``,
``FramebufferMemSubsystem`` and ``Tmu``).

This is *not* ``spinal.lib.bus.bmb.BmbDecoder``.  It is three free functions
the core uses to splice one ``Bmb`` onto another by hand, with an externally
computed ``valid`` and address, so a decoder's address map and its rsp
arbitration are avoided entirely:

* :func:`drive_cmd_from` copies a command payload across, overriding the
  address (and optionally ``source``/``length``), resizing every field to the
  destination's width;
* :func:`route_rsp_to` gates a response onto a destination *inside a
  ``when``*, so several sources can drive the same destination as long as the
  ``selected`` conditions are mutually exclusive -- the caller owns that.

Two things the Scala does that are easy to lose in translation:

1. every field is ``.resize``d rather than width-checked, so a 26-bit
   framebuffer address is silently truncated into a 22-bit register bus and a
   22-bit one is zero-extended.  Reproduced verbatim: Amaranth's ``.eq()``
   truncates and zero-extends the same way;
2. ``routeRspTo`` drives ``src.rsp.ready := selected && dst.rsp.ready``
   *unconditionally* but the payload only under ``when(selected)``, so an
   unselected source is back-pressured while the destination keeps whatever
   the previously selected source left on it.  A caller that forgets to give
   the destination a default gets a latch check from Amaranth, exactly as it
   would get one from Spinal's ``PhaseCheck_noLatchNoOverride``.
"""

from __future__ import annotations

from amaranth import Module

from avoodoo.lib.membus import _has

__all__ = ["drive_cmd_from", "route_rsp_to"]


def drive_cmd_from(m: Module, dst, src, valid, address, source=None, length=None) -> None:
    """``BmbRouting.driveCmdFrom`` (both overloads).

    ``dst``/``src`` are ``MemBus`` interfaces.  ``valid``, ``address`` and the
    optional ``source``/``length`` replace the corresponding ``src`` fields;
    everything else is copied.  ``ready`` is **not** touched -- the caller
    wires it, which is what lets one source feed several destinations.
    """
    s = src.cmd.payload.fragment
    d = dst.cmd.payload.fragment
    if source is None:
        source = s.source if _has(s, "source") else None
    if length is None:
        length = s.length if _has(s, "length") else None

    m.d.comb += [
        dst.cmd.valid.eq(valid),
        d.opcode.eq(s.opcode),
        d.address.eq(address),
        dst.cmd.payload.last.eq(src.cmd.payload.last),
    ]
    if _has(d, "data") and _has(s, "data"):
        m.d.comb += d.data.eq(s.data)
    if _has(d, "mask") and _has(s, "mask"):
        m.d.comb += d.mask.eq(s.mask)
    if _has(d, "length") and length is not None:
        m.d.comb += d.length.eq(length)
    if _has(d, "source") and source is not None:
        m.d.comb += d.source.eq(source)
    if _has(d, "context"):
        m.d.comb += d.context.eq(s.context if _has(s, "context") else 0)


def route_rsp_to(m: Module, selected, src, dst) -> None:
    """``BmbRouting.routeRspTo``.

    ``src.rsp.ready`` is driven unconditionally; the destination's payload and
    ``valid`` only while ``selected``.
    """
    s = src.rsp.payload.fragment
    d = dst.rsp.payload.fragment
    m.d.comb += src.rsp.ready.eq(selected & dst.rsp.ready)
    with m.If(selected):
        m.d.comb += [
            dst.rsp.valid.eq(src.rsp.valid),
            d.opcode.eq(s.opcode),
            dst.rsp.payload.last.eq(src.rsp.payload.last),
        ]
        if _has(d, "data") and _has(s, "data"):
            m.d.comb += d.data.eq(s.data)
        if _has(d, "source"):
            m.d.comb += d.source.eq(s.source if _has(s, "source") else 0)
        if _has(d, "context"):
            m.d.comb += d.context.eq(s.context if _has(s, "context") else 0)
