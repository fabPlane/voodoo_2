"""Where each Amaranth signature leaf lives inside the emitted Verilog ports.

Amaranth and Spinal flatten a bundle to different depths, and the difference
is the one real obstacle to pairing the two port lists:

* Spinal emits **one Verilog port per scalar leaf**.  ``Stream[Fpxx]`` becomes
  ``io_op_valid``, ``io_op_ready``, ``io_op_payload_mant``,
  ``io_op_payload_exp``, ``io_op_payload_sign``.
* Amaranth stops at the **signature member**.  The same interface, written as
  ``In(stream.Signature(FpxxLayout(c)))``, emits ``op__valid``, ``op__ready``
  and a single packed ``op__payload`` of 32 bits, because the payload's shape
  is a ``data.StructLayout`` rather than a nested ``wiring.Signature``.

Forcing the Amaranth side to use nested signatures purely to make the port
lists line up would distort every ported module, so the harness does the work
instead: this module walks the Component's signature *and* the ``data.Layout``
shapes underneath it, and returns, for each fully-qualified leaf path, the
Verilog port it lives in plus its bit offset and width.  The wrapper then
slices.

Vec/array indices come out as decimal path segments (``coords__0``), which is
what the Spinal naming rule produces too (``ports.py`` splits
``i_payload_coords_0`` into ``[..., "coords", "0"]``).
"""

from __future__ import annotations

from dataclasses import dataclass

from amaranth.hdl import Shape
from amaranth.lib import data, wiring

__all__ = ["Field", "amaranth_fields"]


@dataclass(frozen=True)
class Field:
    path: str      # "op__payload__mant"
    port: str      # "op__payload"
    offset: int    # bit offset within the port
    width: int

    @property
    def packed(self) -> bool:
        return self.path != self.port


def _expand(prefix: str, port: str, shape, base: int, out: dict[str, Field]) -> None:
    layout = shape if isinstance(shape, data.Layout) else None
    if layout is None:
        out[prefix] = Field(prefix, port, base, Shape.cast(shape).width)
        return
    for key, field in layout:
        _expand(f"{prefix}__{key}", port, field.shape, base + field.offset, out)


def amaranth_fields(dut) -> dict[str, Field]:
    """Fully-qualified leaf path -> :class:`Field` for a ``wiring.Component``.

    A non-Component elaboratable that advertises ``ports()`` is treated as a
    flat list of named signals, which is what ``gen/gen_module.py`` expects of
    one too.
    """
    out: dict[str, Field] = {}
    if isinstance(dut, wiring.Component):
        for path, member in dut.signature.members.flatten():
            if not member.is_port:
                continue
            name = "__".join(str(p) for p in path)
            _expand(name, name, member.shape, 0, out)
        return out

    for sig in getattr(dut, "ports", lambda: [])():
        name = sig.name
        out[name] = Field(name, name, 0, len(sig))
    return out
