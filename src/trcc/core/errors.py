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


class PermissionError_(TransportError):
    """Host OS denied access (missing udev rule, kernel driver, etc.).

    A TransportError, because a denied open IS the transport failing.  It was
    a sibling, so every ``except TransportError`` -- ConnectDevice, the send
    paths, ``trcc report``'s probe -- let it escape: a permission-denied
    device raised out of ``dispatch`` instead of failing with a hint (#173).
    The advice itself is the Platform's (``permission_denied_hint``); the
    transport that raises this states only what happened.
    """


class UnsupportedOperationError(TrccError):
    """Device or protocol doesn't support the requested operation."""


class DaemonUnavailableError(TransportError):
    """The daemon could not be reached, or died mid-session.

    A ``TransportError`` because that is what it is: the socket to the process
    that owns USB is gone.  It is raised rather than returned as a
    ``Result(ok=False)`` for a measured reason — ``DiscoverResult(ok=False)``
    carries ``products=[]``, and **198 of 492 dispatch sites never check
    ``.ok``**, so a Result would render "no devices found" on a screen whose
    daemon just died.  Raising cannot be silently ignored.

    This is not a new failure mode: a dead daemon already raised, as a bare
    ``ConnectionRefusedError`` / ``TimeoutError`` with no indication of what
    the app was even talking to.  This gives that raise a name.
    """


class RemoteCommandError(TrccError):
    """A Command raised inside the daemon, and the client is told so.

    In-process, a Command that raises propagates to the caller.  Over the
    socket it could not: ``_dispatch_envelope`` catches everything (it must —
    one client's bad Command cannot be allowed to kill the daemon) and used to
    answer with a BASE ``Result``, which decodes to ``Result(ok=False)`` and
    then throws ``AttributeError: 'Result' object has no attribute 'devices'``
    the moment the caller reads the field it asked for.  Verified end to end.

    So the failure was never survivable — it was just illegible.  Re-raising
    here restores the in-process shape: a Command that raises, raises, and the
    message names the original exception instead of a missing attribute.
    """


class UnknownUserInterfaceError(TrccError):
    """No :class:`~trcc.ui.UserInterface` is registered under that name.

    Raised by the ``UIS`` registry's miss policy.  Like an unregistered wire
    and unlike an unknown OS, this is a DEFECT rather than something to
    degrade through: a caller asking for a UI that does not exist has a typo
    or a stale entry point, and silently substituting another face of the app
    would be worse than stopping.
    """


class ConfigError(TrccError):
    """Persistent settings / config file is invalid or unreadable."""


class ThemeError(TrccError):
    """Theme load / parse / export failed."""


class HttpFetchError(RuntimeError):
    """Raised on any transport or HTTP-level fetch failure.

    Subclasses ``RuntimeError`` (not ``TrccError``) deliberately: the
    HTTP adapter and several call sites catch it as ``RuntimeError``,
    and the broad ``except TrccError`` handlers in ``commands.py``
    must NOT swallow network failures — those map to user-facing
    "couldn't reach the server" messages, not generic command errors.
    Lives in core so Commands import it without reaching into the
    ``adapters.repo`` layer.
    """
