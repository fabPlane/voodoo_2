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

The array case is why this walks ``signature.members`` by hand instead of
calling ``SignatureMembers.flatten()``: ``flatten()`` puts ``dimensions`` on
the array member's own row and then yields the element sub-paths with no
index, so all five elements of ``In(MemBusSignature(p)).array(5)`` come back
as ``inputs__cmd__valid``.  Amaranth's Verilog puts the index at the level
``.array`` was declared on (``inputs__0__cmd__valid``), including for
``.array(1)``, and nests multiple dimensions left to right (``grid__0__2``).
``tests/diff/test_amports.py`` pins that against actually emitted Verilog.
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
        width = Shape.cast(shape).width
        if width == 0:
            # A zero-width leaf -- `stream.Signature(0)`, the Amaranth spelling
            # of Spinal's `Stream(NoData)`. Spinal emits no port for it at all,
            # so there is nothing to pair and nothing to compare; yosys does
            # emit `input [-1:0] cmd__payload`, which the wrapper leaves
            # unconnected. Dropping it here keeps it out of both the
            # "unpaired leaves" and the bit-coverage checks in pair_ports().
            return
        out[prefix] = Field(prefix, port, base, width)
        return
    for key, field in layout:
        _expand(f"{prefix}__{key}", port, field.shape, base + field.offset, out)


def _index_suffixes(dimensions) -> list[str]:
    """``()`` -> ``[""]``; ``(3,)`` -> ``["__0", "__1", "__2"]``; ``(2, 3)`` -> 6 entries.

    ``.array(n)`` puts the index in the port name **at the level it was
    declared on**, not at the end: ``In(sig).array(3)`` on member ``inputs``
    gives ``inputs__0__cmd__valid``, not ``inputs__cmd__valid__0``.  Multiple
    dimensions nest left to right (``grid__0__2``).  The index is present even
    for ``.array(1)`` (``one__0``), so there is no n == 1 special case.
    """
    out = [""]
    for size in dimensions:
        out = [f"{prefix}__{i}" for prefix in out for i in range(size)]
    return out


def _walk(members, prefix: str, out: dict[str, Field]) -> None:
    """Recurse through a ``SignatureMembers``, expanding ``.array(...)``.

    ``SignatureMembers.flatten()`` cannot be used for this: it reports the
    array's ``dimensions`` on the *array member's own* row and then yields the
    element sub-paths with no index at all, so ``inputs__0__cmd__valid`` and
    ``inputs__1__cmd__valid`` both come back as ``inputs__cmd__valid``.  An
    override table cannot repair that -- n Spinal ports would have to map onto
    one leaf name -- so the walk is done here instead.
    """
    for name, member in members.items():
        for suffix in _index_suffixes(member.dimensions):
            path = f"{prefix}{name}{suffix}"
            if member.is_port:
                _expand(path, path, member.shape, 0, out)
            else:
                _walk(member.signature.members, f"{path}__", out)


def amaranth_fields(dut) -> dict[str, Field]:
    """Fully-qualified leaf path -> :class:`Field` for a ``wiring.Component``.

    A non-Component elaboratable that advertises ``ports()`` is treated as a
    flat list of named signals, which is what ``gen/gen_module.py`` expects of
    one too.
    """
    out: dict[str, Field] = {}
    if isinstance(dut, wiring.Component):
        _walk(dut.signature.members, "", out)
        return out

    for sig in getattr(dut, "ports", lambda: [])():
        name = sig.name
        out[name] = Field(name, name, 0, len(sig))
    return out
