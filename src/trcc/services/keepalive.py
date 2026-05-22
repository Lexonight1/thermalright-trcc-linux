"""KeepaliveService — re-send the last frame to Bulk/LY devices.

Bulk and LY firmware don't latch frames the way SCSI does — once the
device's internal buffer ages out, the screen clears.  Legacy worked
around it with a ``run_static_loop`` background thread that re-shot the
last frame every few seconds.

This service mirrors that: ``store`` after every send, ``resend`` when
the keepalive ticker fires.  The ticker itself is the existing
``RenderAndSend`` loop dispatched on a timer — the caller decides cadence.
For pure-static themes that never re-render, ``KeepAliveLoop`` Command
runs a dedicated minimal loop that just resends the last bytes.

No background thread inside the service — keeps testing trivial.  The
CLI ``trcc display keepalive`` runs the loop in the foreground so the
user can Ctrl-C it; daemon mode uses a tick timer on the App.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class _KeepaliveState:
    """Last frame + when it was last sent (monotonic seconds)."""
    frame: bytes = b""
    last_sent_at: float = 0.0


class KeepaliveService:
    """Per-device last-frame cache for static-theme keepalive."""

    def __init__(self) -> None:
        self._state: dict[str, _KeepaliveState] = {}

    def store(self, key: str, frame: bytes) -> None:
        """Record the bytes we just sent so we can resend later."""
        state = self._state.setdefault(key, _KeepaliveState())
        state.frame = frame
        state.last_sent_at = time.monotonic()

    def last_frame(self, key: str) -> bytes | None:
        state = self._state.get(key)
        return state.frame if state and state.frame else None

    def seconds_since_send(self, key: str) -> float | None:
        state = self._state.get(key)
        if state is None or state.last_sent_at == 0.0:
            return None
        return time.monotonic() - state.last_sent_at

    def mark_sent(self, key: str) -> None:
        """Reset the "seconds since send" timer without overwriting bytes."""
        state = self._state.setdefault(key, _KeepaliveState())
        state.last_sent_at = time.monotonic()

    def forget(self, key: str) -> None:
        """Drop cached state for *key* — used on DisconnectDevice."""
        self._state.pop(key, None)
