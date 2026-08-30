"""DeviceSender — the per-device send worker (actor).

One ``DeviceSender`` owns one device's outbound USB wire.  Its single
responsibility is *output scheduling*: serialize every write, cache the last
frame, and keepalive-resend it on volatile wires (Bulk/LY firmware reverts to
the logo after ~2-3 s without a fresh frame).

It is a :class:`SendTask` — pure policy with no thread of its own.  A
``SendScheduler`` drives it (``wait`` → ``run_once``); because there is exactly
one scheduler thread per task, ``DeviceSender`` is the *sole* caller of
``device.send`` and serialization is by construction — no wire lock needed.
The only lock here guards the latest-wins inbox slot shared between the
producer (``submit``, any thread) and the consumer (``run_once``, the scheduler
thread).

The sender knows nothing about ``Wire`` / ``needs_keepalive`` — it is simply
told ``volatile=True/False`` at construction (DIP).  See
``doc/SEND_FOUNDATION.md``.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from ..core.logs import per_frame
from ..core.ports import SendTask

if TYPE_CHECKING:
    from ..core.ports import Device

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

# Resend cadence for volatile wires — below the ~2-3 s firmware-revert
# threshold by an order of magnitude (legacy ``run_static_loop`` parity).
_DEFAULT_KEEPALIVE_S = 0.150
# How long a non-volatile task sleeps between idle wakeups — it only ever acts
# on a producer ``submit``, so this is just a periodic liveness ceiling.
_IDLE_WAIT_S = 3600.0


class DeviceSender(SendTask):
    """Serializes writes to one device + keepalives volatile wires."""

    __slots__ = (
        "_device",
        "_has_pending",
        "_interval",
        "_last",
        "_last_sent_at",
        "_lock",
        "_on_failure",
        "_pending",
        "_volatile",
        "_waiter",
        "_wake",
        "_wire_lock",
    )

    @property
    def device(self) -> Device:
        """The device this worker drives.

        Read by ``App.start_sender`` to tell "a sender exists" from "the RIGHT
        sender exists" — a distinction that cost a user five minutes of dead
        panel per replug before it was drawn (#254).
        """
        log.debug("DeviceSender.device: %s", self._device.key)
        return self._device

    def __init__(
        self, device: Device, *, volatile: bool,
        keepalive_interval: float = _DEFAULT_KEEPALIVE_S,
        on_failure: Callable[[str, BaseException | None], None] | None = None,
    ) -> None:
        self._device = device
        self._volatile = volatile
        # Notified when a fire-and-forget write (keepalive / wait=False frame)
        # fails — no waiter to raise to, so the App publishes the disconnect /
        # error event the Command ``except`` would have (DIP: a named callback,
        # the sender stays free of EventBus/Command deps).
        self._on_failure = on_failure
        self._interval = keepalive_interval
        self._lock = threading.Lock()
        # Serializes the worker's wire writes against ``exclusive()`` holders
        # (multi-frame uploads like boot animation) so they never interleave.
        self._wire_lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: Any = None
        self._has_pending = False
        self._last: Any = None
        self._last_sent_at = 0.0
        # Optional one-shot result channel for ``submit(wait=True)`` callers
        # (CLI/API one-shots that report success).  ``(Event, box)`` where the
        # worker fills ``box["ok"]`` or ``box["exc"]`` — so a wire exception
        # propagates back to the caller's existing ``except`` (no Command churn).
        self._waiter: tuple[threading.Event, dict[str, Any]] | None = None
        log.info(
            "DeviceSender %s: created (volatile=%s interval=%.3fs)",
            device.key, volatile, keepalive_interval,
        )

    # ── SendTask contract ────────────────────────────────────────────────

    @property
    def key(self) -> str:
        return self._device.key

    def wait(self, timeout: float) -> None:
        """Block until a producer submits or *timeout* elapses."""
        self._wake.wait(timeout)
        self._wake.clear()

    def wake(self) -> None:
        """Interrupt a pending :meth:`wait` (scheduler teardown)."""
        self._wake.set()

    def run_once(self, now: float) -> float:
        """Write a pending frame, else keepalive-resend; return next-wait secs.

        Single-consumer: only the scheduler thread calls this, so
        ``device.send`` is never re-entered.  The device write happens
        OUTSIDE the inbox lock so a slow USB write never blocks ``submit``.
        """
        with self._lock:
            payload = self._pending if self._has_pending else None
            had_pending = self._has_pending
            self._has_pending = False
            self._pending = None
            waiter = self._waiter
            self._waiter = None

        if had_pending:
            try:
                ok = self._raw_write(payload, now, reason="frame")
            except Exception as exc:
                if waiter is not None:
                    waiter[1]["exc"] = exc      # re-raised in submit()
                    waiter[0].set()
                else:
                    self._fail(exc, reason="frame")     # fire-and-forget
            else:
                if waiter is not None:
                    waiter[1]["ok"] = ok
                    waiter[0].set()
                elif not ok:
                    self._fail(None, reason="frame")
        elif self._volatile and self._device.is_connected:
            with self._lock:
                last, last_at = self._last, self._last_sent_at
            if last is not None and (now - last_at) >= self._interval:
                try:
                    if not self._raw_write(last, now, reason="keepalive"):
                        self._fail(None, reason="keepalive")
                except Exception as exc:
                    self._fail(exc, reason="keepalive")

        return self._interval if self._volatile else _IDLE_WAIT_S

    def _fail(self, exc: BaseException | None, *, reason: str) -> None:
        """A fire-and-forget write failed (no waiter to raise to) — surface it.

        Logs, then notifies the injected ``on_failure`` so the App publishes
        ErrorOccurred + DeviceDisconnected, mirroring the Command except path.
        The keepalive ``is_connected`` guard above stops the ~150 ms flood once
        the device's recovery threshold has closed it.
        """
        if exc is not None:
            log.warning("DeviceSender %s: %s write failed: %s",
                        self._device.key, reason, exc)
        else:
            log.warning("DeviceSender %s: %s write returned False",
                        self._device.key, reason)
        if self._on_failure is not None:
            self._on_failure(self._device.key, exc)

    # ── Producer side ────────────────────────────────────────────────────

    def submit(self, payload: Any, *, wait: bool = False,
               timeout: float = 5.0) -> bool:
        """Queue *payload* as the next frame (latest-wins) and wake the worker.

        Non-blocking by default (``wait=False``) — callable from any thread;
        returns ``True`` once queued.  An older un-sent frame is superseded;
        only the most recent submit reaches the wire.

        ``wait=True`` blocks until the worker has written *this* frame and
        returns the device's success bool — for one-shot CLI/API sends that
        report a result.  Requires a running scheduler thread; returns
        ``False`` on timeout.
        """
        done: threading.Event | None = None
        box: dict[str, Any] = {}
        with self._lock:
            superseded = self._has_pending
            self._pending = payload
            self._has_pending = True
            if wait:
                done = threading.Event()
                if self._waiter is not None:
                    # Newer frame supersedes a prior waiting one — release it
                    # (its frame won't reach the wire, but a newer one will).
                    old_done, old_box = self._waiter
                    old_box["ok"] = True
                    old_done.set()
                self._waiter = (done, box)
        if superseded:
            frame_log.debug("DeviceSender %s: submit superseded a pending frame",
                            self._device.key)
        self._wake.set()
        if done is None:
            return True
        if not done.wait(timeout):
            log.warning("DeviceSender %s: submit(wait) timed out after %.1fs",
                        self._device.key, timeout)
            return False
        if "exc" in box:
            raise box["exc"]   # the wire exception, on the caller's thread
        return bool(box.get("ok", False))

    def last(self) -> Any:
        """The last payload successfully written (the keepalive frame)."""
        with self._lock:
            return self._last

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the wire exclusively for a multi-frame upload (boot animation).

        Blocks the worker's frame/keepalive writes for the duration so they
        can't interleave the upload's chunks.  Cheap — only boot-anim /
        screencast-setup paths contend for it.
        """
        log.debug("DeviceSender %s: exclusive wire — acquiring", self._device.key)
        with self._wire_lock:
            yield
        log.debug("DeviceSender %s: exclusive wire — released", self._device.key)

    # ── Internal ─────────────────────────────────────────────────────────

    def _raw_write(self, payload: Any, now: float, *, reason: str) -> bool:
        """Write to the wire (may raise) and record it as the keepalive frame.

        Caller (``run_once``) owns exception handling — relays it to a
        ``wait=True`` waiter or logs it for fire-and-forget / keepalive.
        """
        # DEBUG: per-frame (keepalive ~150 ms) — never INFO (flood).
        frame_log.debug("DeviceSender %s: write (%s)", self._device.key, reason)
        with self._wire_lock:
            ok = self._device.send(payload)
        if ok:
            with self._lock:
                self._last = payload
                self._last_sent_at = now
        else:
            log.warning("DeviceSender %s: device.send returned False (%s)",
                        self._device.key, reason)
        return ok
