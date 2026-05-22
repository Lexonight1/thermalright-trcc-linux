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


class PermissionError_(TrccError):
    """Host OS denied access (missing udev rule, kernel driver, etc.)."""


class UnsupportedOperationError(TrccError):
    """Device or protocol doesn't support the requested operation."""


class ConfigError(TrccError):
    """Persistent settings / config file is invalid or unreadable."""


class ThemeError(TrccError):
    """Theme load / parse / export failed."""
