"""Hotplug monitor implementations + the universal no-op fallback.

  Linux    → ``LinuxHotplugMonitor`` (pyudev netlink monitor)
  Windows  → ``WindowsHotplugMonitor`` (WMI ``__InstanceCreationEvent`` /
             ``__InstanceDeletionEvent`` on ``Win32_PnPEntity``)
  macOS    → ``NoopHotplugMonitor`` (IOKit listener pending B.8)
  BSD      → ``NoopHotplugMonitor`` (devd listener pending B.5)

Each per-OS implementation degrades to the noop when its backend
isn't installed (pyudev / wmi optional deps).
"""
from __future__ import annotations

import logging
import re
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


# Shared registry set used by both Linux + Windows monitors.  Hex
# strings without leading zeros so they match the raw event payloads.
_KNOWN_VID_PID: set[tuple[str, str]] = {
    (f"{vid:04x}", f"{pid:04x}") for vid, pid in ALL_DEVICES
}


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
        self._known = _KNOWN_VID_PID

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


# =========================================================================
# Windows — WMI __InstanceCreationEvent / __InstanceDeletionEvent
# =========================================================================


# Windows DeviceID format: USB\VID_0402&PID_3922\<serial>
# Parses out the four-hex-digit vid + pid from the leading segment.
_WIN_DEVICE_ID_RE = re.compile(
    r"USB\\VID_(?P<vid>[0-9A-Fa-f]{4})&PID_(?P<pid>[0-9A-Fa-f]{4})",
)


def _import_wmi() -> Any | None:
    try:
        import wmi  # pyright: ignore[reportMissingImports]
    except ImportError:
        return None
    return wmi


# The blocking timeout passed to ``watcher(timeout_ms=...)``.  Half a
# second balances responsiveness on stop vs. wakeup overhead.
_WIN_WATCHER_TIMEOUT_MS = 500


class WindowsHotplugMonitor(HotplugMonitor):
    """Watches WMI ``Win32_PnPEntity`` for USB add/remove events.

    Two background threads — one watching creation, one watching
    deletion — translate WMI events into ``DeviceAttached`` /
    ``DeviceDetached`` for registry-known vid:pid combos.

    Degrades to a no-op (logged once) when the ``wmi`` package isn't
    installed.  ``pythoncom.CoInitialize`` is called per worker thread
    so each thread can independently talk to WMI.
    """

    def __init__(self) -> None:
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._bus: EventBus | None = None
        self._known = _KNOWN_VID_PID

    def start(self, bus: EventBus) -> None:
        if self._threads:
            return                              # idempotent

        wmi = _import_wmi()
        if wmi is None:
            log.info(
                "WindowsHotplugMonitor: wmi package not installed — install "
                "with `pip install wmi` for live USB add/remove events",
            )
            return

        self._bus = bus
        self._stop_event.clear()

        for action, kind in (("add", "creation"), ("remove", "deletion")):
            thread = threading.Thread(
                target=self._watch_loop,
                args=(wmi, action, kind),
                daemon=True,
                name=f"trcc-next-hotplug-{kind}",
            )
            thread.start()
            self._threads.append(thread)
        log.info("WindowsHotplugMonitor: watching Win32_PnPEntity events")

    def stop(self) -> None:
        if not self._threads:
            return
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        self._bus = None
        log.info("WindowsHotplugMonitor: stopped")

    @property
    def is_running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    # ── Worker loop ──────────────────────────────────────────────────

    def _watch_loop(self, wmi: Any, action: str, kind: str) -> None:
        """Drain ``Win32_PnPEntity`` ``creation``/``deletion`` events.

        ``wmi.watch_for`` returns a callable that blocks until an event
        arrives; we pass a small timeout so ``stop()`` doesn't have to
        wait the full lifetime of the watcher.
        """
        # Each thread that touches WMI needs its own COM apartment.
        try:
            import pythoncom  # type: ignore[import-not-found,import-untyped]
            pythoncom.CoInitialize()
        except ImportError:
            pass

        try:
            connection = wmi.WMI()
            watcher = connection.Win32_PnPEntity.watch_for(notification_type=kind)
        except Exception:
            log.exception("WindowsHotplugMonitor: %s watcher setup failed", kind)
            return

        # pywin32 exposes timeout exceptions on the wmi module
        timeout_exc = getattr(wmi, "x_wmi_timed_out", Exception)

        while not self._stop_event.is_set():
            try:
                event = watcher(timeout_ms=_WIN_WATCHER_TIMEOUT_MS)
            except timeout_exc:
                continue
            except Exception:
                log.exception("WindowsHotplugMonitor: %s watcher raised", kind)
                continue
            if event is None:
                continue
            device_id = getattr(event, "DeviceID", "") or ""
            self._dispatch(action, str(device_id))

    # ── Pure dispatch helper (tested directly) ───────────────────────

    def _dispatch(self, action: str, device_id: str) -> None:
        """Parse a Windows DeviceID + publish a bus event if it matches."""
        if (vid_pid := _parse_windows_device_id(device_id)) is None:
            return
        vid, pid = vid_pid
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


def _parse_windows_device_id(device_id: str) -> tuple[str, str] | None:
    """Extract ``(vid, pid)`` from a Windows ``USB\\VID_xxxx&PID_yyyy\\…`` ID.

    Returns ``None`` when the string isn't a USB DeviceID (PCI / HID hub
    parents arrive on the same watcher and must be ignored).
    """
    match = _WIN_DEVICE_ID_RE.search(device_id)
    if match is None:
        return None
    return match.group("vid").lower(), match.group("pid").lower()
