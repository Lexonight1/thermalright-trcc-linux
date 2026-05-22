"""DeviceCommands — generic base class for device command surfaces.

Both `LCDCommands` and `LEDCommands` hold a device list + EventBus and
share the same shape: index lookup, bounds-check, "device not found"
error result. Centralizing that here cuts ~50 sites of repeated
`if (dev := self._get(idx)) is None: return X(success=False, ...)`
to one helper per subclass and one shared `_get()`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from .results import OpResult

if TYPE_CHECKING:
    from .events import EventBus

log = logging.getLogger(__name__)

T = TypeVar('T')
R = TypeVar('R', bound=OpResult)


class DeviceCommands(Generic[T]):
    """Base for command surfaces that operate on a list of devices.

    Subclasses set `_KIND` ('LCD' or 'LED') for log messages and the
    error text returned by `_missing()`. Constructor + state are shared.
    """

    _KIND: ClassVar[str] = 'device'

    def __init__(self, devices: list[T], events: EventBus) -> None:
        self._devices = devices
        self._events = events

    def _get(self, idx: int) -> T | None:
        """Bounds-checked device lookup. Logs + returns None on miss."""
        if not 0 <= idx < len(self._devices):
            log.warning(
                '%s index %d out of range (have %d)',
                self._KIND, idx, len(self._devices),
            )
            return None
        return self._devices[idx]

    def _missing(self, idx: int, result_cls: type[R]) -> R:
        """Build a typed not-found result for the given device index."""
        return result_cls(success=False, error=f'{self._KIND} {idx} not found')
