"""DeviceRegistry — a small typed container with rich `__getitem__`.

Wraps the per-kind device list `Trcc` holds today (`_lcd_devices`,
`_led_devices`) and lets callers look devices up by:

    registry[0]                  # by index
    registry['/dev/sg0']         # by device_path (str)
    registry[(0x0402, 0x3922)]   # by (vid, pid)

Same iteration / length / membership / truthiness as a list, plus the
three indexing modes. Replaces the ad-hoc ``next((d for d in ... if ...
== ...), None)`` comprehensions sprinkled across `Trcc`, `LCDCommands`,
and the facade callers.

Lean by design: one class, six dunders, no inheritance, no abstraction
beyond what `__getitem__` already gives.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, TypeVar, overload

if TYPE_CHECKING:
    from .lcd import LCDDevice
    from .led import LEDDevice

D = TypeVar('D', 'LCDDevice', 'LEDDevice')


class DeviceRegistry(list[D]):
    """Subclass of `list` with three extra indexing modes.

    Inherits every list operation (append, remove, clear, iter, len,
    bool, slicing, etc.) so existing `_lcd_devices.append(d)` /
    `.clear()` / `.remove(d)` / `for d in self._lcd_devices` keep
    working unchanged. Adds `__getitem__` overloads for path and
    (vid, pid) lookup on top.
    """

    __slots__ = ()

    # Overloads tell type-checkers which key type yields what — int and
    # str and (vid, pid) all return a single device; slice returns a list.
    @overload
    def __getitem__(self, key: int) -> D: ...
    @overload
    def __getitem__(self, key: slice) -> list[D]: ...
    @overload
    def __getitem__(self, key: str) -> D: ...
    @overload
    def __getitem__(self, key: tuple[int, int]) -> D: ...

    def __getitem__(self, key):
        # Integer index OR slice — stock list behaviour.
        if isinstance(key, (int, slice)):
            return super().__getitem__(key)

        # String → match on device_path.
        if isinstance(key, str):
            for d in self:
                if getattr(d, 'device_path', None) == key:
                    return d
                info = getattr(d, 'device_info', None)
                if info is not None and getattr(info, 'path', None) == key:
                    return d
            raise KeyError(f"No device with path: {key!r}")

        # (vid, pid) → match on device_info.
        if isinstance(key, tuple) and len(key) == 2:
            vid, pid = key
            for d in self:
                info = getattr(d, 'device_info', None)
                if info is not None and info.vid == vid and info.pid == pid:
                    return d
            raise KeyError(f"No device with VID:PID {vid:04x}:{pid:04x}")

        raise TypeError(
            f"DeviceRegistry indices must be int, str, or (vid, pid) tuple "
            f"— got {type(key).__name__}")

    def __contains__(self, key) -> bool:
        # Stock list `in` for object identity; rich lookup for str/tuple keys.
        if isinstance(key, (str, tuple)):
            try:
                self[key]
                return True
            except KeyError:
                return False
        return super().__contains__(key)

    def __iter__(self) -> Iterator[D]:
        return super().__iter__()

    def __repr__(self) -> str:
        return f"DeviceRegistry({list.__repr__(self)})"
