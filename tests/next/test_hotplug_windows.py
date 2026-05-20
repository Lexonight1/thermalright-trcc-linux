"""WindowsHotplugMonitor — DeviceID parsing + bus dispatch.

The WMI watcher loop runs in a background thread and blocks in
``watcher(timeout_ms=...)`` — that part can only be exercised on a real
Windows box (reporter-verified).  The pure dispatch logic
(``_parse_windows_device_id`` + ``_dispatch``) gets full coverage here
on the Linux dev box.
"""
from __future__ import annotations

import pytest

from trcc.next.adapters.system._hotplug import (
    WindowsHotplugMonitor,
    _parse_windows_device_id,
)
from trcc.next.core.events import (
    DeviceAttached,
    DeviceDetached,
    EventBus,
)

# ── _parse_windows_device_id ────────────────────────────────────────


def test_parses_canonical_usb_device_id() -> None:
    assert _parse_windows_device_id(
        r"USB\VID_0402&PID_3922\6&abcd1234&0",
    ) == ("0402", "3922")


def test_parses_uppercase_vid_pid() -> None:
    """Windows sometimes uppercases the hex digits."""
    assert _parse_windows_device_id(
        r"USB\VID_0416&PID_5302\1234567",
    ) == ("0416", "5302")


def test_ignores_pci_device_id() -> None:
    assert _parse_windows_device_id(r"PCI\VEN_10DE&DEV_2684\3&abcd") is None


def test_ignores_hid_parent_id() -> None:
    """HID hub events fire on the same watcher and must be filtered out."""
    assert _parse_windows_device_id(r"HID\VID_046D&PID_C52B&MI_01") is None


def test_ignores_empty_device_id() -> None:
    assert _parse_windows_device_id("") is None


def test_ignores_malformed_id() -> None:
    """Truncated or hex-short DeviceIDs return None instead of raising."""
    assert _parse_windows_device_id(r"USB\VID_04&PID_392\xyz") is None
    assert _parse_windows_device_id("USB stuff with no vid/pid pattern") is None


# ── WindowsHotplugMonitor._dispatch — pure path ─────────────────────


@pytest.fixture
def monitor_with_bus() -> tuple[WindowsHotplugMonitor, list[object]]:
    """Monitor wired to a bus that captures every published event."""
    bus = EventBus()
    captured: list[object] = []
    bus.subscribe(DeviceAttached, captured.append)
    bus.subscribe(DeviceDetached, captured.append)
    monitor = WindowsHotplugMonitor()
    monitor._bus = bus
    return monitor, captured


def test_dispatch_publishes_attached_for_known_device(
    monitor_with_bus: tuple[WindowsHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    # SCSI 0402:3922 is in the registry
    monitor._dispatch("add", r"USB\VID_0402&PID_3922\1234")

    assert len(captured) == 1
    evt = captured[0]
    assert isinstance(evt, DeviceAttached)
    assert evt.key == "0402:3922"
    assert evt.vid == 0x0402
    assert evt.pid == 0x3922


def test_dispatch_publishes_detached_for_known_device(
    monitor_with_bus: tuple[WindowsHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    monitor._dispatch("remove", r"USB\VID_0402&PID_3922\1234")

    assert len(captured) == 1
    assert isinstance(captured[0], DeviceDetached)
    assert captured[0].key == "0402:3922"


def test_dispatch_ignores_unknown_vid_pid(
    monitor_with_bus: tuple[WindowsHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    monitor._dispatch("add", r"USB\VID_DEAD&PID_BEEF\1")
    assert captured == []


def test_dispatch_silently_skips_non_usb_ids(
    monitor_with_bus: tuple[WindowsHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    monitor._dispatch("add", r"PCI\VEN_10DE&DEV_2684")
    monitor._dispatch("remove", r"HID\VID_046D&PID_C52B&MI_01")
    assert captured == []


def test_dispatch_handles_action_strings_unknown_actions(
    monitor_with_bus: tuple[WindowsHotplugMonitor, list[object]],
) -> None:
    """An action string we don't recognise → silently drop."""
    monitor, captured = monitor_with_bus
    monitor._dispatch("change", r"USB\VID_0402&PID_3922\1234")
    monitor._dispatch("", r"USB\VID_0402&PID_3922\1234")
    assert captured == []


# ── start() degrades when wmi is missing ────────────────────────────


def test_start_no_op_without_wmi_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """No wmi package installed → start() logs once and returns idle."""
    from trcc.next.adapters.system import _hotplug as hotplug_mod

    monkeypatch.setattr(hotplug_mod, "_import_wmi", lambda: None)

    monitor = WindowsHotplugMonitor()
    monitor.start(EventBus())

    assert monitor.is_running is False


def test_start_then_stop_is_idempotent_when_disabled() -> None:
    """stop() doesn't crash when start() failed (no wmi installed)."""
    monitor = WindowsHotplugMonitor()
    # Skip starting — verify stop is safe regardless
    monitor.stop()
    assert monitor.is_running is False
