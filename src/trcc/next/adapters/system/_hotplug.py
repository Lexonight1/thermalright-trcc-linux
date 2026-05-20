"""Hotplug monitor implementations + the universal no-op fallback.

LinuxHotplug uses pyudev — degrades to NoopHotplugMonitor when pyudev
isn't installed.  Windows / macOS / BSD all return the noop today; each
OS gets its own listener in a follow-up.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from ...core.events import (
    DeviceAttached,
    DeviceDetached,
)
from ...core.ports import HotplugMonitor
from ...core.registry import ALL_DEVICES

if TYPE_CHECKING:
    from ...core.events import EventBus

log = logging.getLogger(__name__)


# =========================================================================
# Noop — every OS that doesn't (yet) support hotplug returns this
# =========================================================================


class NoopHotplugMonitor(HotplugMonitor):
    """Hotplug monitor that does nothing.

    Used on Windows / macOS / BSD until per-OS listeners ship.  Lets the
    daemon's start/stop sequence stay unconditional — no `if hotplug:`
    guards in App or daemon.
    """

    def __init__(self, *, reason: str = "no implementation for this platform") -> None:
        self._reason = reason
        self._running = False

    def start(self, bus: EventBus) -> None:
        del bus
        if self._running:
            return
        log.info("Hotplug monitor disabled: %s", self._reason)
        self._running = True

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running


# =========================================================================
# Linux — pyudev-backed udev monitor
# =========================================================================


def _import_pyudev() -> Any | None:
    try:
        import pyudev  # type: ignore[import-not-found]
    except ImportError:
        return None
    return pyudev


class LinuxHotplugMonitor(HotplugMonitor):
    """Linux udev monitor — filters to TRCC's registry-known VID/PID set.

    Spawns one daemon thread that consumes ``pyudev.Monitor`` events
    and translates ``add``/``remove`` actions into ``DeviceAttached`` /
    ``DeviceDetached`` events on the bus.

    Degrades to a no-op (with a debug log) when pyudev isn't installed
    — keeps daemon startup working in minimal Linux setups.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._bus: EventBus | None = None
        # Lower-case hex string set for fast match — pyudev returns hex
        # without the leading zeros, so canonicalize once here.
        self._known: set[tuple[str, str]] = {
            (f"{vid:04x}", f"{pid:04x}") for vid, pid in ALL_DEVICES
        }

    def start(self, bus: EventBus) -> None:
        if self._thread is not None:
            return                        # idempotent — already running

        pyudev = _import_pyudev()
        if pyudev is None:
            log.info(
                "LinuxHotplugMonitor: pyudev not installed — install with "
                "`pip install pyudev` for live USB add/remove events",
            )
            return

        self._bus = bus
        self._stop_event.clear()

        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="usb")

        self._thread = threading.Thread(
            target=self._poll_loop, args=(monitor,),
            daemon=True, name="trcc-next-hotplug",
        )
        self._thread.start()
        log.info("LinuxHotplugMonitor: watching usb add/remove events")

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        # The pyudev monitor poll has a timeout; the thread checks
        # _stop_event between polls and exits naturally.
        self._thread.join(timeout=2.0)
        self._thread = None
        self._bus = None
        log.info("LinuxHotplugMonitor: stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Internal: poll loop ──────────────────────────────────────────

    def _poll_loop(self, monitor: Any) -> None:
        """Drain pyudev events until ``stop`` is called.

        ``poll(timeout=…)`` returns ``None`` on timeout so we get a
        chance to honor ``_stop_event`` between events without sitting
        in a blocking ``recv``.
        """
        try:
            monitor.start()
        except Exception:
            log.exception("LinuxHotplugMonitor: pyudev monitor.start failed")
            return

        while not self._stop_event.is_set():
            try:
                device = monitor.poll(timeout=0.5)
            except Exception:
                log.exception("LinuxHotplugMonitor: monitor.poll raised")
                continue
            if device is None:
                continue
            self._dispatch_device_event(device)

    def _dispatch_device_event(self, device: Any) -> None:
        """Translate one pyudev event into a bus event if it matches."""
        action = device.action
        vid = (device.get("ID_VENDOR_ID") or "").lower()
        pid = (device.get("ID_MODEL_ID") or "").lower()
        if not vid or not pid:
            return
        if (vid, pid) not in self._known:
            return
        if self._bus is None:
            return

        key = f"{vid}:{pid}"
        if action == "add":
            log.info("Hotplug add: %s", key)
            self._bus.publish(DeviceAttached(key=key, vid=int(vid, 16),
                                             pid=int(pid, 16)))
        elif action == "remove":
            log.info("Hotplug remove: %s", key)
            self._bus.publish(DeviceDetached(key=key, vid=int(vid, 16),
                                             pid=int(pid, 16)))
        # other actions (change, bind, …) are intentionally ignored
