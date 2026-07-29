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


class _FakePyudevContext:
    """Stand-in for pyudev.Context — list_devices yields the coldplug set."""

    def __init__(self, present: list[Any] | None = None) -> None:
        self._present = list(present or [])

    def list_devices(self, **kwargs: Any) -> list[Any]:
        del kwargs            # tests only ever filter by subsystem="usb"
        return self._present


def _run_linux_monitor_with_events(
    events: list[Any], present: list[Any] | None = None,
) -> tuple[EventBus, list[Any]]:
    """Drive a LinuxHotplugMonitor through one batch of fake events.

    ``present`` is the coldplug set (devices already enumerated when the
    monitor starts).  Returns (bus, captured_events) where captured_events is
    the list of every event published on the bus during the run.
    """
    bus = EventBus()
    captured: list[Any] = []
    bus.subscribe(DeviceAttached, captured.append)
    bus.subscribe(DeviceDetached, captured.append)

    monitor = LinuxHotplugMonitor()
    fake = _FakePyudevMonitor(events)
    ctx = _FakePyudevContext(present)

    # Run the poll loop on a thread, populated with our scripted events,
    # and stop it once the events drain (poll returns None continuously).
    monitor._bus = bus
    poll_thread = threading.Thread(
        target=monitor._poll_loop, args=(fake, ctx), daemon=True,
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


def test_linux_monitor_coldplugs_present_device_on_start() -> None:
    """A registry-known device already present when the monitor starts is
    announced via DeviceAttached (coldplug) with no add event — the boot-race
    half of #139.  Deduped to one announcement per key."""
    present = [
        _FakePyudevDevice("add", "0402", "3922"),  # the usb_device node
        _FakePyudevDevice("add", "0402", "3922"),  # a sibling interface node
    ]
    _bus, captured = _run_linux_monitor_with_events([], present=present)

    assert len(captured) == 1
    assert isinstance(captured[0], DeviceAttached)
    assert captured[0].key == "0402:3922"


def test_linux_monitor_coldplug_skips_unknown_devices() -> None:
    """Coldplug only announces registry-known devices."""
    present = [_FakePyudevDevice("add", "dead", "beef")]
    _bus, captured = _run_linux_monitor_with_events([], present=present)

    assert captured == []


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


# ── App reaction to hotplug events (#254, #246) ──────────────────────
#
# The monitor tests above prove the events are PUBLISHED.  These prove the
# App reacts: before the fix nothing subscribed to DeviceDetached, so an
# unplugged device kept a dead transport in app.devices, its DeviceSender
# spun forever, and the replug hit "already connected" and never recovered.

_REPLUG_SPEC = {"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100}
_REPLUG_KEY = "0402:3922"


def _connected_app(tmp_path: Path) -> App:
    from trcc.core.commands import ConnectDevice

    from .mock_platform import MockPlatform

    app = App(MockPlatform([_REPLUG_SPEC], tmp_path))
    assert app.dispatch(ConnectDevice(key=_REPLUG_KEY)).ok
    return app


def _detach(app: App) -> None:
    app.events.publish(DeviceDetached(key=_REPLUG_KEY, vid=0x0402, pid=0x3922))


def _attach(app: App) -> None:
    app.events.publish(DeviceAttached(key=_REPLUG_KEY, vid=0x0402, pid=0x3922))


def test_detach_releases_the_device(tmp_path: Path) -> None:
    """DeviceDetached must drop the dead handle — it used to be ignored."""
    app = _connected_app(tmp_path)
    _detach(app)
    assert _REPLUG_KEY not in app.devices
    assert _REPLUG_KEY not in app.senders, "sender must stop spinning"


def test_replug_reconnects_with_a_fresh_device(tmp_path: Path) -> None:
    """The #254 / #246 cycle: unplug then replug must yield a NEW, live
    device, not the corpse of the old one."""
    app = _connected_app(tmp_path)
    original = app.devices[_REPLUG_KEY]

    _detach(app)
    _attach(app)

    rebuilt = app.devices.get(_REPLUG_KEY)
    assert rebuilt is not None, "replug did not reconnect"
    assert rebuilt is not original, "reused the stale device object"
    assert rebuilt.is_connected


def test_replug_reconnects_even_if_the_detach_event_was_missed(
    tmp_path: Path,
) -> None:
    """udev events can coalesce or drop, and some monitors only report adds.

    A present-but-disconnected entry must be torn down and rebuilt rather
    than short-circuited by the idempotency guard.
    """
    app = _connected_app(tmp_path)
    original = app.devices[_REPLUG_KEY]
    original.disconnect()          # device died; no DeviceDetached arrived
    assert not original.is_connected

    _attach(app)

    rebuilt = app.devices.get(_REPLUG_KEY)
    assert rebuilt is not None and rebuilt is not original
    assert rebuilt.is_connected


def test_attach_stays_idempotent_for_a_live_device(tmp_path: Path) -> None:
    """Coldplug replays + duplicate adds must NOT churn a healthy device."""
    app = _connected_app(tmp_path)
    original = app.devices[_REPLUG_KEY]

    _attach(app)
    _attach(app)

    assert app.devices[_REPLUG_KEY] is original, "healthy device was rebuilt"


def test_detach_keeps_content_so_the_panel_repaints_on_replug(
    tmp_path: Path,
) -> None:
    """Unlike App.detach, the hotplug release preserves active theme / LED
    runtime / media — otherwise the panel comes back blank (the same choice
    _on_system_resumed makes)."""
    app = _connected_app(tmp_path)
    app.active_themes[_REPLUG_KEY] = object()

    _detach(app)

    assert _REPLUG_KEY in app.active_themes, (
        "hotplug release must not drop the active theme — that is what the "
        "heavier App.detach is for"
    )
