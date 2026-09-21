"""``voodoo.core`` -- the assembly layer, plus :class:`Core` itself.

``Core.scala`` sits at the package root in the Scala tree, and the plan's
layout (``docs/amaranth-rewrite-plan.md`` §3) names ``avoodoo/core.py`` for
it.  Python cannot have both a ``core/`` package and a ``core.py`` module
next to each other -- the package wins and the module is unreachable -- so
the port lives at ``avoodoo/core/core.py`` and this package resolves
``avoodoo.core.Core`` lazily (PEP 562).  Lazily, because
:mod:`avoodoo.core.core_types` is imported by nearly every module ``Core``
is built from; an eager ``from .core import Core`` here would make that
first import pull the whole tree in and fail on the cycle.
"""

from __future__ import annotations


def __getattr__(name: str):
    if name == "Core":
        from avoodoo.core.core import Core

        return Core
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
