"""AppProxy — client-side drop-in for ``App`` that talks to the daemon.

UIs hold a typed ``App`` and never distinguish; in daemon mode the
``_boot.trcc_next()`` factory hands them an ``AppProxy`` instead.  Only
``dispatch(cmd) -> Result`` is real — every call serializes the Command,
sends it over the Unix socket, and reconstructs the typed Result.

Other ``App`` attributes (``platform`` / ``settings`` / ``events`` /
``devices`` / ``display``) are not exposed by the proxy.  UIs that need
state should dispatch a Command (``DiscoverDevices`` / ``GetPlatformInfo``
/ ``ReadSensors``) — that's the API contract daemon mode honors.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeVar

from . import ipc
from .core.commands import Command
from .core.results import Result

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


R = TypeVar("R", bound=Result)


class AppProxy:
    """Forwards every ``dispatch(cmd)`` call to a running daemon.

    Construction is cheap (no socket connection until the first
    dispatch) so import-time use of ``_boot.trcc_next()`` doesn't pay
    a round-trip per invocation.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._timeout = timeout

    def dispatch(self, cmd: Command[R]) -> R:
        """Serialize *cmd*, round-trip through the daemon, return the Result."""
        envelope = ipc.encode_command(cmd)
        response = ipc.one_shot_request(envelope, timeout=self._timeout)
        result = ipc.decode_result(response)
        log.debug("AppProxy.dispatch: %s -> %s",
                  type(cmd).__name__, type(result).__name__)
        return result  # type: ignore[return-value]   # caller's TypeVar binds the subclass

    # ── Attributes that a real App exposes but the proxy can't ──────────

    def __getattr__(self, name: str) -> object:
        raise AttributeError(
            f"AppProxy has no attribute {name!r} — daemon mode only exposes "
            "dispatch(cmd); use a Command to query App state remotely"
        )
