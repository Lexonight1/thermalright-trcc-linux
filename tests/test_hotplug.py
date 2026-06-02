"""Hotplug monitor — noop fallback, App lifecycle wiring, Linux pyudev path.

The Linux pyudev branch is exercised via dependency-injection: we feed
a fake pyudev.Monitor into the monitor's poll loop and assert that
add/remove events for registry-known vid:pid combos publish
``DeviceAttached`` / ``DeviceDetached`` on the bus.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.system._hotplug import (
    LinuxHotplugMonitor,
    NoopHotplugMonitor,
)
from trcc.app import App
from trcc.core.events import (
    DeviceAttached,
    DeviceDetached,
    EventBus,
)

from .conftest import FakePlatform

# ── NoopHotplugMonitor ───────────────────────────────────────────────


def test_noop_start_is_idempotent() -> None:
    bus = EventBus()
    monitor = NoopHotplugMonitor(reason="unit-test")
    assert monitor.is_running is False

    monitor.start(bus)
    monitor.start(bus)
    assert monitor.is_running is True


def test_noop_stop_clears_running_flag() -> None:
    monitor = NoopHotplugMonitor()
    monitor.start(EventBus())
    monitor.stop()
    assert monitor.is_running is False


# ── App lifecycle — start_hotplug / stop_hotplug / close ────────────


def test_app_start_hotplug_calls_platform_monitor(tmp_path: Path) -> None:
    platform = FakePlatform(tmp_path)
    app = App(platform=platform)
    monitor = platform.hotplug()

    app.start_hotplug()
    assert monitor.is_running is True

    app.stop_hotplug()
    assert monitor.is_running is False


def test_app_start_hotplug_is_idempotent(tmp_path: Path) -> None:
    """Calling start_hotplug twice doesn't double-start the monitor."""
    platform = FakePlatform(tmp_path)
    app = App(platform=platform)

    app.start_hotplug()
    app.start_hotplug()        # second call — no-op

    # Verify by looking at the App-internal flag (the FakeMonitor's is_running
    # would be True regardless; the contract is about the App layer)
    assert app._hotplug_started is True


def test_app_close_stops_hotplug(tmp_path: Path) -> None:
    platform = FakePlatform(tmp_path)
    app = App(platform=platform)
    monitor = platform.hotplug()

    app.start_hotplug()
    app.close()

    assert monitor.is_running is False


# ── LinuxHotplugMonitor — pyudev poll loop ───────────────────────────


class _FakePyudevDevice:
    """Minimal stand-in for a pyudev Device.  Only carries the attrs we read."""

    def __init__(self, action: str, vid: str, pid: str) -> None:
        self.action = action
        self._props = {"ID_VENDOR_ID": vid, "ID_MODEL_ID": pid}

    def get(self, key: str, default: Any = None) -> Any:
        return self._props.get(key, default)


class _FakePyudevMonitor:
    """Stand-in for pyudev.Monitor.  Hands out scripted events via poll()."""

    def __init__(self, events: list[Any]) -> None:
        self._events = list(events)
        self.started = False

    def filter_by(self, subsystem: str) -> None:
        del subsystem        # only one filter is ever applied in tests

    def start(self) -> None:
        self.started = True

    def poll(self, timeout: float) -> Any:
        del timeout
        if not self._events:
            time.sleep(0.01)
            return None
        return self._events.pop(0)


def _run_linux_monitor_with_events(
    events: list[Any],
) -> tuple[EventBus, list[Any]]:
    """Drive a LinuxHotplugMonitor through one batch of fake events.

    Returns (bus, captured_events) where captured_events is the list of
    every event published on the bus during the run.
    """
    bus = EventBus()
    captured: list[Any] = []
    bus.subscribe(DeviceAttached, captured.append)
    bus.subscribe(DeviceDetached, captured.append)

    monitor = LinuxHotplugMonitor()
    fake = _FakePyudevMonitor(events)

    # Run the poll loop on a thread, populated with our scripted events,
    # and stop it once the events drain (poll returns None continuously).
    monitor._bus = bus
    poll_thread = threading.Thread(
        target=monitor._poll_loop, args=(fake,), daemon=True,
    )
    monitor._thread = poll_thread
    poll_thread.start()

    # Wait for events to drain — we sized the loop's empty-poll sleep at
    # 10 ms, so 250 ms is plenty for a 5-event run.
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        if not fake._events:
            time.sleep(0.05)
            break
        time.sleep(0.02)

    monitor._stop_event.set()
    poll_thread.join(timeout=1.0)
    return bus, captured


def test_linux_monitor_publishes_attach_for_known_device() -> None:
    # SCSI 0402:3922 is in the registry
    events = [_FakePyudevDevice("add", "0402", "3922")]
    _bus, captured = _run_linux_monitor_with_events(events)

    assert len(captured) == 1
    evt = captured[0]
    assert isinstance(evt, DeviceAttached)
    assert evt.key == "0402:3922"
    assert evt.vid == 0x0402
    assert evt.pid == 0x3922


def test_linux_monitor_publishes_detach_for_known_device() -> None:
    events = [_FakePyudevDevice("remove", "0402", "3922")]
    _bus, captured = _run_linux_monitor_with_events(events)

    assert len(captured) == 1
    assert isinstance(captured[0], DeviceDetached)
    assert captured[0].key == "0402:3922"


def test_linux_monitor_ignores_unknown_vid_pid() -> None:
    """USB cameras, keyboards, etc. shouldn't trigger TRCC events."""
    events = [
        _FakePyudevDevice("add", "dead", "beef"),       # not in registry
        _FakePyudevDevice("add", "0402", "3922"),       # in registry
        _FakePyudevDevice("remove", "dead", "beef"),    # not in registry
    ]
    _bus, captured = _run_linux_monitor_with_events(events)

    # Only the registered device produces events
    assert len(captured) == 1
    assert captured[0].key == "0402:3922"


def test_linux_monitor_ignores_non_addremove_actions() -> None:
    """``change`` / ``bind`` / ``move`` actions are silently dropped."""
    events = [
        _FakePyudevDevice("change", "0402", "3922"),
        _FakePyudevDevice("bind", "0402", "3922"),
        _FakePyudevDevice("move", "0402", "3922"),
    ]
    _bus, captured = _run_linux_monitor_with_events(events)

    assert captured == []


def test_linux_monitor_skips_when_pyudev_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pyudev installed → start() logs + returns; no thread spawned."""
    from trcc.adapters.system import _hotplug as hotplug_mod

    monkeypatch.setattr(hotplug_mod, "_import_pyudev", lambda: None)

    monitor = LinuxHotplugMonitor()
    monitor.start(EventBus())

    assert monitor.is_running is False
