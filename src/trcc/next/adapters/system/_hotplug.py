"""Hotplug monitor implementations + the universal no-op fallback.

  Linux    → ``LinuxHotplugMonitor`` (pyudev netlink monitor)
  Windows  → ``WindowsHotplugMonitor`` (WMI ``__InstanceCreationEvent`` /
             ``__InstanceDeletionEvent`` on ``Win32_PnPEntity``)
  FreeBSD  → ``FreeBSDHotplugMonitor`` (devd seqpacket socket)
  macOS    → ``PollingHotplugMonitor`` (scan_devices() diff every 1 s)
  Others   → ``NoopHotplugMonitor``

macOS uses ``PollingHotplugMonitor`` instead of the IOKit native API
because:
  * The native path needs ``IOServiceAddMatchingNotification`` +
    ``CFRunLoop`` from a thread, ~250 lines of fragile ctypes.
  * The pyobjc-framework-IOKit alternative drags in ~50 MB of pyobjc.
  * Polling every 1 s gives ≤1 s detection latency on real hardware
    with zero new deps and a pure-Python listener that's actually
    debuggable on the reporter's box.

The polling monitor is OS-generic — any platform whose native
listener hasn't shipped can opt in.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
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
        log.debug("NoopHotplugMonitor.stop")
        self._running = False

    @property
    def is_running(self) -> bool:
        log.debug("NoopHotplugMonitor.is_running → %s", self._running)
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
            log.debug("LinuxHotplugMonitor: already running — start() ignored")
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
            log.debug("LinuxHotplugMonitor: not running — stop() ignored")
            return
        log.info("LinuxHotplugMonitor: stopping")
        self._stop_event.set()
        # The pyudev monitor poll has a timeout; the thread checks
        # _stop_event between polls and exits naturally.
        self._thread.join(timeout=2.0)
        self._thread = None
        self._bus = None
        log.info("LinuxHotplugMonitor: stopped")

    @property
    def is_running(self) -> bool:
        running = self._thread is not None and self._thread.is_alive()
        log.debug("LinuxHotplugMonitor.is_running → %s", running)
        return running

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
        # Per-method logging on Windows-specific monitors is deferred
        # until I've researched the canonical Windows logging pattern
        # (WMI/ETW conventions vs plain Python logging) on real hardware.
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


# =========================================================================
# FreeBSD — devd seqpacket socket
# =========================================================================


_DEVD_SOCKET_PATH = "/var/run/devd.seqpacket.pipe"
_DEVD_RECV_BUFSIZE = 8192
_DEVD_RECV_TIMEOUT_S = 0.5

# devd event format:
#   !system=USB subsystem=DEVICE type=ATTACH ugen=ugen3.4 cdev=ugen3.4
#     vendor=0x0402 product=0x3922 …
# Anchored on the canonical USB-device line.  ``vendor`` and ``product``
# appear in either order across releases; two separate regexes handle that.
_DEVD_USB_EVENT_RE = re.compile(
    r"!system=USB\s+subsystem=DEVICE\s+type=(?P<action>ATTACH|DETACH)",
)
_DEVD_VENDOR_RE = re.compile(r"vendor=0x(?P<vid>[0-9A-Fa-f]+)")
_DEVD_PRODUCT_RE = re.compile(r"product=0x(?P<pid>[0-9A-Fa-f]+)")


def _open_devd_socket() -> Any | None:
    """Connect to ``/var/run/devd.seqpacket.pipe``.

    Returns the connected socket on success, ``None`` when the socket
    doesn't exist (non-FreeBSD or devd not running).
    """
    import socket as _socket

    if not hasattr(_socket, "AF_UNIX"):
        return None
    try:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_SEQPACKET)
        sock.settimeout(_DEVD_RECV_TIMEOUT_S)
        sock.connect(_DEVD_SOCKET_PATH)
    except (OSError, AttributeError):
        log.debug("devd seqpacket socket unavailable", exc_info=True)
        return None
    return sock


class FreeBSDHotplugMonitor(HotplugMonitor):
    """Reads USB events from devd's seqpacket socket.

    One daemon thread reads newline-bounded messages from
    ``/var/run/devd.seqpacket.pipe``; each message is parsed via the
    shared ``_parse_devd_event`` helper and routed onto the bus when
    the vid:pid matches the TRCC registry.

    Degrades to a no-op (with a debug log) when the socket doesn't
    exist — same shape as ``LinuxHotplugMonitor`` falling back to noop
    without pyudev.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._bus: EventBus | None = None
        self._sock: Any = None
        self._known = _KNOWN_VID_PID

    def start(self, bus: EventBus) -> None:
        if self._thread is not None:
            return

        sock = _open_devd_socket()
        if sock is None:
            log.info(
                "FreeBSDHotplugMonitor: /var/run/devd.seqpacket.pipe "
                "unavailable — live USB events disabled",
            )
            return

        self._bus = bus
        self._sock = sock
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="trcc-next-hotplug-devd",
        )
        self._thread.start()
        log.info("FreeBSDHotplugMonitor: watching devd USB events")

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                log.debug("devd socket close failed", exc_info=True)
            self._sock = None
        self._thread.join(timeout=2.0)
        self._thread = None
        self._bus = None
        log.info("FreeBSDHotplugMonitor: stopped")

    @property
    def is_running(self) -> bool:
        running = self._thread is not None and self._thread.is_alive()
        log.debug("FreeBSDHotplugMonitor.is_running → %s", running)
        return running

    # ── Worker loop ──────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Recv loop — exits when the socket closes or stop is set."""
        while not self._stop_event.is_set():
            if self._sock is None:
                return
            try:
                raw = self._sock.recv(_DEVD_RECV_BUFSIZE)
            except TimeoutError:
                continue
            except OSError:
                log.debug("devd recv failed", exc_info=True)
                return
            if not raw:
                return                # socket closed
            try:
                message = raw.decode("utf-8", errors="replace")
            except UnicodeDecodeError:
                continue
            self._dispatch(message)

    # ── Pure dispatch helper (tested directly) ───────────────────────

    def _dispatch(self, message: str) -> None:
        parsed = _parse_devd_event(message)
        if parsed is None:
            return
        action, vid, pid = parsed
        if (vid, pid) not in self._known:
            return
        if self._bus is None:
            return

        key = f"{vid}:{pid}"
        if action == "ATTACH":
            log.info("Hotplug add: %s", key)
            self._bus.publish(DeviceAttached(key=key, vid=int(vid, 16),
                                             pid=int(pid, 16)))
        elif action == "DETACH":
            log.info("Hotplug remove: %s", key)
            self._bus.publish(DeviceDetached(key=key, vid=int(vid, 16),
                                             pid=int(pid, 16)))


def _parse_devd_event(message: str) -> tuple[str, str, str] | None:
    """Parse a devd USB event line into ``(action, vid, pid)``.

    Returns ``None`` when the message isn't a USB attach/detach event
    or is missing vendor/product fields.  Hex strings are normalized
    to lowercase 4-digit form so they match the registry's ``vid:pid``
    keys without case juggling at the call site.
    """
    event = _DEVD_USB_EVENT_RE.search(message)
    if event is None:
        return None
    vendor = _DEVD_VENDOR_RE.search(message)
    product = _DEVD_PRODUCT_RE.search(message)
    if vendor is None or product is None:
        return None
    vid = vendor.group("vid").lower().zfill(4)
    pid = product.group("pid").lower().zfill(4)
    return event.group("action"), vid, pid


# =========================================================================
# Polling fallback — OS-generic; used by macOS today, available to any
# platform that lacks a native hotplug listener
# =========================================================================


# Default poll interval — 1 s gives sub-second detection latency at
# negligible CPU cost.  Tests override to drive the loop deterministically.
_POLL_INTERVAL_S = 1.0


class PollingHotplugMonitor(HotplugMonitor):
    """Hotplug detection via periodic ``Platform.scan_devices()`` diff.

    Doesn't watch any OS event source — instead it asks the Platform
    for the live device list every ``_POLL_INTERVAL_S`` seconds, diffs
    against the previous snapshot, and publishes
    ``DeviceAttached`` / ``DeviceDetached`` for the difference.

    Sub-second responsiveness, zero new deps, debuggable in pure
    Python.  Quality matches a native listener for TRCC's use case
    (one hotplug per few minutes) at the cost of a per-second sysfs /
    usb-bus walk that ``scan_devices`` does anyway.

    DI seam: ``scan`` is a callable returning ``set[(vid, pid)]`` so
    tests drive the diff without touching the real platform.
    """

    def __init__(
        self,
        scan: Callable[[], set[tuple[int, int]]],
        *,
        interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._scan = scan
        self._interval_s = interval_s
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._bus: EventBus | None = None
        self._known = _KNOWN_VID_PID
        self._last_seen: set[tuple[int, int]] = set()

    def start(self, bus: EventBus) -> None:
        if self._thread is not None:
            return
        self._bus = bus
        self._stop_event.clear()
        # Prime the snapshot so freshly-already-present devices don't
        # trigger spurious "attach" events on first tick.
        try:
            self._last_seen = {vp for vp in self._scan() if vp in self._registry_set()}
        except Exception:
            log.exception("PollingHotplugMonitor: initial scan failed")
            self._last_seen = set()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="trcc-next-hotplug-poll",
        )
        self._thread.start()
        log.info("PollingHotplugMonitor: polling every %.1fs", self._interval_s)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        self._bus = None
        log.info("PollingHotplugMonitor: stopped")

    @property
    def is_running(self) -> bool:
        running = self._thread is not None and self._thread.is_alive()
        log.debug("PollingHotplugMonitor.is_running → %s", running)
        return running

    # ── Diff helper (pure — tested directly) ──────────────────────────

    def _registry_set(self) -> set[tuple[int, int]]:
        """Registry as int tuples, matching scan_devices's shape."""
        return {(int(vid, 16), int(pid, 16)) for vid, pid in self._known}

    def _tick(self) -> None:
        """One scan + diff + publish iteration."""
        try:
            current = {vp for vp in self._scan() if vp in self._registry_set()}
        except Exception:
            log.exception("PollingHotplugMonitor: scan failed")
            return
        added = current - self._last_seen
        removed = self._last_seen - current
        self._last_seen = current
        if self._bus is None:
            return
        for vid, pid in added:
            key = f"{vid:04x}:{pid:04x}"
            log.info("Hotplug add: %s", key)
            self._bus.publish(DeviceAttached(key=key, vid=vid, pid=pid))
        for vid, pid in removed:
            key = f"{vid:04x}:{pid:04x}"
            log.info("Hotplug remove: %s", key)
            self._bus.publish(DeviceDetached(key=key, vid=vid, pid=pid))

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._tick()
            self._stop_event.wait(self._interval_s)
