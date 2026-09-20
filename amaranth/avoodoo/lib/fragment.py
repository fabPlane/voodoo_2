"""``spinal.lib.Fragment`` for Amaranth.

Transliteration of the parts of ``spinal/lib/Fragment.scala`` (SpinalHDL
1.15.0) that the core reaches: a ``Fragment[T]`` is a bundle of a payload
(``fragment``) plus a ``last`` bit, and ``Stream[Fragment[T]]`` is the shape
every Bmb command stream has
(``ext/SpinalVoodoo/src/voodoo/core/TextureMemSubsystem.scala:28``, and ~65
``.last`` references across the core once ``avoodoo.lib.membus`` lands).

Only the bundle, the ``addFragmentLast`` builders and the fragment-aware
arbitration hook are ported. ``StreamFragmentPipe``, ``FragmentBuilder``,
``StreamFragmentWidthAdapter``, ``fragmentTransaction`` and the ``reduce``
family are unused by the core and left out (plan §6: port only what is used).
"""

from __future__ import annotations

from amaranth.lib import data

from avoodoo.lib.stream import (Counter, Stream, _assign, arbitration_from, fire,
                                payload_shape_of)

__all__ = ["FragmentLayout", "fragment_layout", "add_fragment_last",
           "add_fragment_last_counted", "is_fragment_layout"]


def fragment_layout(payload_shape):
    """``Fragment(T)`` -- ``{last: Bool, fragment: T}``, ``last`` at bit 0.

    Field order matters, and it is the opposite of what the name
    ``Fragment`` suggests. ``spinal/lib/Fragment.scala:475`` declares ``last``
    *first*::

        class Fragment[T <: Data](val fragmentType: HardType[T]) extends Bundle {
          val last = Bool()
          val fragment: T = fragmentType()
        }

    and ``MultiData.asBits`` (``spinal/core/MultiData.scala:47``) folds with
    ``ret = e.asBits ## ret``, so the first-declared element lands at the LSB.
    A packed Spinal ``Fragment`` is therefore ``{fragment, last}`` with ``last``
    at bit 0. Amaranth's :class:`~amaranth.lib.data.StructLayout` also puts its
    first key at the LSB, so the key order here must match the Scala
    declaration order to pack identically.

    This only shows up when a Fragment payload is cast to bits as a whole, or
    compared against a Spinal-derived constant -- flattened port *names* are
    the same either way. Caught by p1-membus, whose ``avoodoo/lib/membus.py``
    layouts and their five Verilator-diffed adapters confirm ``last``-at-LSB.
    """
    return data.StructLayout({"last": 1, "fragment": payload_shape})


#: Alias reading closer to the Scala ``Fragment(T)`` call site.
FragmentLayout = fragment_layout


def is_fragment_layout(shape) -> bool:
    """True when ``shape`` is a struct with ``fragment`` and ``last`` members."""
    return (isinstance(shape, data.StructLayout)
            and set(shape.members) == {"fragment", "last"})


def add_fragment_last(m, source, last):
    """``Stream.addFragmentLast(last)``.

    ``arbitrationFrom`` plus ``ret.last := last`` and
    ``ret.fragment := this.payload``; no registers, no back-pressure change.
    """
    out = Stream(fragment_layout(payload_shape_of(source)))
    arbitration_from(m, out, source)
    m.d.comb += [
        out.payload.last.eq(last),
        _assign(out.payload.fragment, source.payload),
    ]
    return out


def add_fragment_last_counted(m, source, length, *, name="fragmentCounter"):
    """``Stream.addFragmentLast(counter)``.

    Spinal increments the supplied ``Counter`` on every ``fire`` and takes
    ``last`` from ``counter.willOverflowIfInc``, so ``last`` is asserted on the
    ``length``-th beat of each group. Returns ``(stream, counter)``.
    """
    counter = Counter(m, length, name=name)
    with m.If(fire(source)):
        counter.increment(m)
    return add_fragment_last(m, source, counter.will_overflow_if_inc), counter
