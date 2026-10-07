"""Bring a lost panel back without being told it returned.

A panel can come back with no event at all.  On 2026-10-06 a VM the panel is
passed through to handed it back to the host: usb-storage re-bound and
``/dev/sg0`` reappeared, but there was no usb ``add`` -- the only kind of
arrival the hotplug monitor hears.  Nothing ever tried again, so the panel
stayed dark until the process was restarted.

The Windows app never gives up on a panel: its native SCSI worker polls the
handshake every 3 s until the panel answers (``USBLCD.exe``, the ``Sleep(3000)``
loop at ``USBLCD.exe.c:20461-20471``).  This is that poll, with one change:
every failed attempt writes a connect failure to the log and to each window, so
the wait doubles from 3 s up to a minute instead of repeating 3 s for as long
as the panel is away.  Twenty minutes in a VM is ~25 attempts, not ~400.

**A `SendTask`, like the slideshow and screencast drivers** -- the scheduler
already gives every task its own thread and a clock tests can tick.  Its key
is namespaced (``reconnect:``) so registering it never stops the device's own
sender, and it is deliberately NOT one of the tasks ``App.stop_sender`` tears
down: releasing the dead device is the first thing a reconnect does.

The connect itself is the App's (``App.try_reconnect``), the same path a
replug takes, so the transport is resolved afresh by vid:pid.  Re-opening the
old ``/dev/sgN`` would be wrong: after a re-enumeration that node can be a
different disk.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from ._send_task import BaseSendTask

if TYPE_CHECKING:                                    # pragma: no cover
    from ..app import App

log = logging.getLogger(__name__)

#: How long an idle watcher sleeps between checks of nothing.
_IDLE_S = 3600.0


class ReconnectWatcher(BaseSendTask):
    """Retries one lost panel with a doubling wait until it is connected."""

    KEY_PREFIX = "reconnect:"
    DEFAULT_INTERVAL_S = 3.0
    #: The panel being away is the whole reason this task runs.
    NEEDS_PANEL = False
    #: It connects a panel; it draws nothing game mode could be holding.
    PAUSES_FOR_GAME = False
    #: The longest wait between two attempts.
    MAX_INTERVAL_S = 60.0
    #: ...for a panel that has never answered.  It may be held by a VM since
    #: boot -- or be one TRCC cannot drive at all, retried for as long as TRCC
    #: runs: a minute apart that is ~1.7 MB of log a day, five is ~350 KB.
    SLOW_MAX_INTERVAL_S = 300.0

    def __init__(self, app: App, device_key: str,
                 interval_s: float | None = None) -> None:
        super().__init__(app, device_key, interval_s)
        self._lock = threading.Lock()
        self._armed = False
        self._delay = self._interval
        # None until the first run after arming: the scheduler's clock is the
        # only "now" this task trusts (a test ticks it), so arm() cannot know it.
        self._next_at: float | None = None
        self._attempts = 0
        self._max = self.MAX_INTERVAL_S
        log.debug("ReconnectWatcher.__init__: %s (cap %.0f s)", device_key,
                  self.MAX_INTERVAL_S)

    @property
    def armed(self) -> bool:
        """Whether the panel is still being waited for."""
        log.debug("ReconnectWatcher.armed: %s %s", self._device_key, self._armed)
        return self._armed

    def arm(self, *, slow: bool = False) -> None:
        """Start waiting for the panel; a no-op while already waiting.

        Re-arming must not restart the backoff, or each failed attempt --
        which reports itself as a failed connect -- would reset it to 3 s.
        *slow* caps the wait at ``SLOW_MAX_INTERVAL_S`` instead.
        """
        with self._lock:
            if self._armed:
                log.debug("ReconnectWatcher.arm: %s already waiting",
                          self._device_key)
                return
            self._armed = True
            self._delay = self._interval
            self._next_at = None
            self._attempts = 0
            self._max = self.SLOW_MAX_INTERVAL_S if slow else self.MAX_INTERVAL_S
        log.info("ReconnectWatcher: waiting for %s to come back (first try in "
                 "%.0f s)", self._device_key, self._interval)
        self.wake()

    def disarm(self) -> None:
        """Stop waiting: it is back, or it was unplugged or disconnected."""
        with self._lock:
            was = self._armed
            self._armed = False
        log.info("ReconnectWatcher.disarm: %s (was waiting=%s)",
                 self._device_key, was)

    def _run(self, now: float) -> float:
        """Try once if it is time; return how long to wait before asking again."""
        with self._lock:
            if not self._armed:
                log.debug("ReconnectWatcher.run_once: %s idle", self._device_key)
                return _IDLE_S
            if self._next_at is None:
                self._next_at = now + self._delay
            if now < self._next_at:
                log.debug("ReconnectWatcher.run_once: %s next try in %.1f s",
                          self._device_key, self._next_at - now)
                return self._next_at - now
            self._attempts += 1
            attempt = self._attempts
        log.debug("ReconnectWatcher: %s attempt %d", self._device_key, attempt)
        if self._app.try_reconnect(self._device_key):
            log.info("ReconnectWatcher: %s is back (attempt %d)",
                     self._device_key, attempt)
            self.disarm()
            return _IDLE_S
        with self._lock:
            self._delay = min(self._delay * 2, self._max)
            self._next_at = now + self._delay
            delay = self._delay
        log.info("ReconnectWatcher: %s not back (attempt %d) — next try in "
                 "%.0f s", self._device_key, attempt, delay)
        return delay
