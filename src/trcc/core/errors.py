"""Domain exception hierarchy."""
from __future__ import annotations


class TrccError(Exception):
    """Base for all TRCC domain errors."""


class DeviceNotFoundError(TrccError):
    """No device matched the requested identity."""


class DeviceNotConnectedError(TrccError):
    """Operation required a connected device; none was attached."""


class HandshakeError(TrccError):
    """Device handshake failed or returned invalid data."""


class TransportError(TrccError):
    """Underlying USB/transport layer failed."""


class DeviceDisconnectedError(TransportError):
    """Send failed N consecutive times with a disconnect-class errno.

    Raised by ``Device.send`` after the recovery tracker hits the
    consecutive-disconnect threshold.  The device's transport is
    closed before the raise so the next send attempt will get a
    fresh handle (or fail fast if the device is genuinely gone).

    Commands that catch :class:`TransportError` will catch this too
    (it's a subclass); use ``isinstance`` to publish the more specific
    :class:`DeviceDisconnected` event for the auto-detach path.
    """


class PermissionError_(TrccError):
    """Host OS denied access (missing udev rule, kernel driver, etc.)."""


class UnsupportedOperationError(TrccError):
    """Device or protocol doesn't support the requested operation."""


class ConfigError(TrccError):
    """Persistent settings / config file is invalid or unreadable."""


class ThemeError(TrccError):
    """Theme load / parse / export failed."""
