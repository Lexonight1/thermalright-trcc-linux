"""Auto-recovery primitives for Device.send paths.

Pure-Python helper at the core/ layer: no I/O, no Qt, no Platform
deps — just errno classification + a small bookkeeping class.  Used
by every :class:`Device` subclass in ``adapters/device/``.

Ports the consecutive-disconnect-tracking logic from
``legacy/adapters/device/factory.py:108-330`` into next/'s Device
shape.  Three concerns:

1. **Errno classification** — walking ``__cause__`` chains for
   USB / kernel errors that indicate the device handle is gone
   (vs. transient send failures that may succeed on retry).
2. **Consecutive-failure tracking** — one tracker per Device,
   incremented on disconnect-class errors, reset on success.
3. **Rate-limited warning** — at most one WARNING log per
   ``_DISCONNECT_WARN_INTERVAL_S`` (default 30 s) so a unplugged
   device doesn't fill the rotation with 15-lines-per-second.

The tracker doesn't own the transport — callers (Device subclasses)
close their own transport when the tracker signals the threshold
has been hit, then raise :class:`DeviceDisconnectedError`.  Commands
catch that subclass and publish the per-device disconnect event.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# Generic, OS-agnostic fallback for an EACCES USB error.  The per-OS specifics
# (udev rules / WinUSB / sudo) live on ``Platform.permission_denied_hint`` and
# are injected into the tracker via ``set_permission_hint`` — core never sniffs
# the OS.  See [[project_architecture_boundary_gate]].
_GENERIC_PERMISSION_HINT = "ensure you have permission to access USB devices"


# USB / kernel errno constants.  Matches legacy ``factory.py:41-54``.
_ERRNO_EACCES = 13          # Permission denied — udev rules missing
_ERRNO_EBUSY = 16           # Device claimed by another process

# Errnos that mean "the file descriptor / USB handle is gone".  These
# never succeed on retry — recovery requires close + re-open.
_ERRNO_EIO = 5              # I/O error — stale handle after a resume re-enumerate
_ERRNO_EBADF = 9            # Bad file descriptor
_ERRNO_ENXIO = 6            # No such device or address (SCSI generic gone)
_ERRNO_ENODEV = 19          # No such device (USB unplugged / re-enumerated)
_ERRNO_ESHUTDOWN = 108      # Transport endpoint is shut down (USB tear-down)
# EIO is included because suspend/resume re-enumerates the USB device, leaving
# the open handle stale: every write returns EIO and never recovers on retry —
# only a close + re-open + re-handshake heals it (GitHub #189).
_DISCONNECT_ERRNOS = frozenset(
    (_ERRNO_EIO, _ERRNO_EBADF, _ERRNO_ENXIO, _ERRNO_ENODEV, _ERRNO_ESHUTDOWN),
)

# After this many CONSECUTIVE disconnect-class failures, the tracker
# returns :class:`Verdict.THRESHOLD` so the caller closes the stale
# transport.  Three keeps us tolerant of one-off USB hiccups without
# spamming dead-handle retries.
DISCONNECT_FAILURE_THRESHOLD = 3

# Maximum seconds between identical "device disconnected" WARNING lines.
# Below this, identical warnings drop to DEBUG.  Mirrors legacy's
# behaviour: state-transition log preserved, retry-spam suppressed.
_DISCONNECT_WARN_INTERVAL_S = 30.0


def _has_usb_errno(exc: BaseException, errno_val: int) -> bool:
    """True if the exception (or its ``__cause__`` chain) has *errno_val*.

    USB errors from pyusb / hidapi usually wrap an OSError that
    carries the actual errno; walking the chain catches the inner
    one no matter how many wrappers the upper transport added.
    """
    cur: BaseException | None = exc
    while cur is not None:
        if getattr(cur, "errno", None) == errno_val:
            return True
        cur = cur.__cause__
    return False


def is_disconnect_error(exc: BaseException) -> bool:
    """True if *exc* matches a 'device handle is gone' errno.

    These never succeed on retry — recovery requires closing the
    transport and re-opening (or failing fast if the device is
    physically gone).
    """
    log.debug("is_disconnect_error: exc=%s", type(exc).__name__)
    cur: BaseException | None = exc
    while cur is not None:
        if getattr(cur, "errno", None) in _DISCONNECT_ERRNOS:
            return True
        cur = cur.__cause__
    return False


class RecoveryTracker:
    """Per-device consecutive-disconnect counter + rate-limited logger.

    One instance lives on each :class:`Device` and is updated by the
    device's ``send()`` implementation on every send attempt.  Pure
    bookkeeping — no I/O beyond the rate-limited log; the caller
    decides what to do with the verdict (close transport, raise, etc.).

    Three observable states on :meth:`note_error`:

    * ``"non-disconnect"`` — a transient send failure (USB busy,
      kernel buffer full, etc.).  Logged once at WARNING, counter
      NOT incremented.  Caller usually wants to ``return False``.
    * ``"disconnect"`` — disconnect-class errno but counter is still
      below threshold.  Rate-limited WARNING, counter +1.  Caller
      may return False and retry on the next tick.
    * ``"threshold"`` — counter just hit
      :data:`DISCONNECT_FAILURE_THRESHOLD`.  Caller MUST close its
      transport and raise :class:`DeviceDisconnectedError`.

    :meth:`note_success` resets the counter and returns the previous
    value so the caller can log a recovery message at INFO when
    coming back from a transient blip.
    """

    __slots__ = ("_failures", "_label", "_last_warn_at", "_permission_hint")

    def __init__(self, label: str) -> None:
        self._label = label
        self._failures = 0
        self._last_warn_at = 0.0
        # Per-OS EACCES remediation, injected by the composition root (which
        # has the Platform).  Generic until set so the tracker never sniffs.
        self._permission_hint = _GENERIC_PERMISSION_HINT

    def set_permission_hint(self, hint: str) -> None:
        """Inject the OS-specific EACCES hint (from ``Platform``)."""
        log.debug("set_permission_hint: label=%s hint=%r", self._label, hint)
        self._permission_hint = hint

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def note_success(self) -> int:
        """Reset the counter; return the previous value.

        Callers log a single INFO line like ``"send recovered after N
        disconnect failure(s)"`` when the return value is nonzero.
        """
        log.debug("note_success: label=%s failures=%d",
                  self._label, self._failures)
        previous = self._failures
        self._failures = 0
        return previous

    def note_error(self, exc: BaseException) -> str:
        """Classify *exc*, update state, log appropriately.

        Returns one of ``"non-disconnect"`` / ``"disconnect"`` /
        ``"threshold"`` — the caller dispatches on this.

        Permission-denied (``EACCES``) and device-busy (``EBUSY``)
        get their own one-line hint because the user-actionable fix
        differs from a generic disconnect.
        """
        log.debug("note_error: label=%s exc=%s", self._label, type(exc).__name__)
        if _has_usb_errno(exc, _ERRNO_EACCES):
            log.warning(
                "%s: permission denied — %s",
                self._label, self._permission_hint,
            )
            return "non-disconnect"
        if _has_usb_errno(exc, _ERRNO_EBUSY):
            log.warning(
                "%s: device busy — another process may be holding the handle",
                self._label,
            )
            return "non-disconnect"
        if not is_disconnect_error(exc):
            log.warning("%s: send failed: %s", self._label, exc)
            return "non-disconnect"

        self._failures += 1
        now = time.monotonic()
        if (self._failures == 1
                or now - self._last_warn_at >= _DISCONNECT_WARN_INTERVAL_S):
            log.warning(
                "%s: send failed (%s) — device may be disconnected "
                "(consecutive failures=%d)",
                self._label, exc, self._failures,
            )
            self._last_warn_at = now
        else:
            log.debug(
                "%s: send failed (%s) — suppressed; consecutive=%d",
                self._label, exc, self._failures,
            )

        if self._failures >= DISCONNECT_FAILURE_THRESHOLD:
            log.warning(
                "%s: %d consecutive disconnect failures — closing transport",
                self._label, self._failures,
            )
            return "threshold"
        return "disconnect"
