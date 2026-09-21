"""Register category metadata (``ext/SpinalVoodoo/src/voodoo/bus/RegisterCategory.scala``).

Two orthogonal flags from the Voodoo hardware spec, tracked per register
address by :class:`avoodoo.bus.bus_interface.BusInterface` and consumed by
``PciFifo``:

``fifo_bypass``
    ``fifoBypass`` -- ``False`` (datasheet "FIFO=Yes") queues the write in the
    64-entry PCI FIFO; ``True`` ("FIFO=No") writes the register directly.
``sync_required``
    ``syncRequired`` -- ``True`` ("Sync=Yes") makes PciFifo hold the drain
    until the pixel pipeline is idle before the write lands.

This is the canonical home of the type; :mod:`avoodoo.frontend.regdef`
re-exports it so the register *table* stays free of the bus package's
Amaranth-carrying modules (this file imports nothing but the standard
library, like the table itself).

The four factories of the Scala companion object are here under the same
names, snake_cased.  ``fifoNoSync`` and ``readOnly`` are the same value
(RegisterCategory.scala:36 / :55); the ``spinal_name`` field keeps the
spelling the declaration site used so the generated register map reads like
the Scala.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "RegisterCategory",
    "FIFO_NO_SYNC",
    "FIFO_WITH_SYNC",
    "BYPASS_FIFO",
    "READ_ONLY",
    "fifo_no_sync",
    "fifo_with_sync",
    "bypass_fifo",
    "read_only",
]


@dataclass(frozen=True)
class RegisterCategory:
    """``case class RegisterCategory(fifoBypass: Boolean, syncRequired: Boolean)``."""

    fifo_bypass: bool
    sync_required: bool
    spinal_name: str

    def as_tuple(self) -> tuple[bool, bool]:
        return (self.fifo_bypass, self.sync_required)


#: ``RegisterCategory.fifoNoSync`` -- FIFO=Yes, Sync=No (triangle geometry).
FIFO_NO_SYNC = RegisterCategory(False, False, "fifoNoSync")
#: ``RegisterCategory.fifoWithSync`` -- FIFO=Yes, Sync=Yes (mode changes).
FIFO_WITH_SYNC = RegisterCategory(False, True, "fifoWithSync")
#: ``RegisterCategory.bypassFifo`` -- FIFO=No (init / video timing).
BYPASS_FIFO = RegisterCategory(True, False, "bypassFifo")
#: ``RegisterCategory.readOnly`` -- structurally identical to ``fifoNoSync``.
READ_ONLY = RegisterCategory(False, False, "readOnly")


def fifo_no_sync() -> RegisterCategory:
    """``def fifoNoSync = RegisterCategory(fifoBypass = false, syncRequired = false)``."""
    return FIFO_NO_SYNC


def fifo_with_sync() -> RegisterCategory:
    """``def fifoWithSync = RegisterCategory(fifoBypass = false, syncRequired = true)``."""
    return FIFO_WITH_SYNC


def bypass_fifo() -> RegisterCategory:
    """``def bypassFifo = RegisterCategory(fifoBypass = true, syncRequired = false)``."""
    return BYPASS_FIFO


def read_only() -> RegisterCategory:
    """``def readOnly = RegisterCategory(fifoBypass = false, syncRequired = false)``."""
    return READ_ONLY
