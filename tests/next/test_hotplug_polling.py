"""PollingHotplugMonitor — diff-driven hotplug fallback.

OS-generic — drives the diff against an injected ``scan`` callable so
tests run anywhere.  macOS is the current consumer; any platform whose
native listener isn't wired yet can opt in.
"""
from __future__ import annotations

import pytest

from trcc.next.adapters.system._hotplug import PollingHotplugMonitor
from trcc.next.core.events import (
    DeviceAttached,
    DeviceDetached,
    EventBus,
)


class _ScriptedScan:
    """Returns the next scripted snapshot on each call.

    When the script is exhausted the *last* snapshot is reused — so
    long-running polling-thread tests don't false-positive on a
    spurious DETACH when the script runs out.
    """

    def __init__(self, snapshots: list[set[tuple[int, int]]]) -> None:
        self._snapshots = list(snapshots)
        self._last: set[tuple[int, int]] = set()

    def __call__(self) -> set[tuple[int, int]]:
        if self._snapshots:
            self._last = self._snapshots.pop(0)
        return self._last


@pytest.fixture
def bus_with_capture() -> tuple[EventBus, list[object]]:
    bus = EventBus()
    captured: list[object] = []
    bus.subscribe(DeviceAttached, captured.append)
    bus.subscribe(DeviceDetached, captured.append)
    return bus, captured


# =========================================================================
# Diff semantics (via _tick — direct, no thread)
# =========================================================================


def test_first_tick_with_pre_attached_device_publishes_nothing(
    bus_with_capture: tuple[EventBus, list[object]],
) -> None:
    """A device already present when monitor starts shouldn't fire 'attach'.

    Otherwise every GUI launch would spam attach events for every
    cooler that's already plugged in.
    """
    bus, captured = bus_with_capture
    scan = _ScriptedScan([
        {(0x0402, 0x3922)},                # already present
        {(0x0402, 0x3922)},                # tick — unchanged
    ])
    monitor = PollingHotplugMonitor(scan=scan)
    monitor._bus = bus
    monitor._last_seen = scan()            # prime as start() does
    monitor._tick()

    assert captured == []


def test_attach_event_fires_when_device_appears(
    bus_with_capture: tuple[EventBus, list[object]],
) -> None:
    bus, captured = bus_with_capture
    scan = _ScriptedScan([
        set(),                              # initial — empty
        {(0x0402, 0x3922)},                # tick 1 — device appears
    ])
    monitor = PollingHotplugMonitor(scan=scan)
    monitor._bus = bus
    monitor._last_seen = scan()            # prime: empty
    monitor._tick()

    assert len(captured) == 1
    evt = captured[0]
    assert isinstance(evt, DeviceAttached)
    assert evt.key == "0402:3922"
    assert evt.vid == 0x0402
    assert evt.pid == 0x3922


def test_detach_event_fires_when_device_disappears(
    bus_with_capture: tuple[EventBus, list[object]],
) -> None:
    bus, captured = bus_with_capture
    scan = _ScriptedScan([
        {(0x0402, 0x3922)},                # initial — present
        set(),                              # tick — gone
    ])
    monitor = PollingHotplugMonitor(scan=scan)
    monitor._bus = bus
    monitor._last_seen = scan()            # prime: device present
    monitor._tick()

    assert len(captured) == 1
    assert isinstance(captured[0], DeviceDetached)
    assert captured[0].key == "0402:3922"


def test_simultaneous_attach_and_detach_both_publish(
    bus_with_capture: tuple[EventBus, list[object]],
) -> None:
    """If one device leaves and another arrives between ticks, both fire."""
    bus, captured = bus_with_capture
    scan = _ScriptedScan([
        {(0x0402, 0x3922)},                # initial — only SCSI
        {(0x0416, 0x5302)},                # tick — only HID Type 2
    ])
    monitor = PollingHotplugMonitor(scan=scan)
    monitor._bus = bus
    monitor._last_seen = scan()
    monitor._tick()

    actions: set[tuple[str, str]] = set()
    for evt in captured:
        assert isinstance(evt, (DeviceAttached, DeviceDetached))
        actions.add((type(evt).__name__, evt.key))
    assert actions == {
        ("DeviceDetached", "0402:3922"),
        ("DeviceAttached", "0416:5302"),
    }


def test_unknown_vid_pid_filtered_out_of_diff(
    bus_with_capture: tuple[EventBus, list[object]],
) -> None:
    """A USB camera attaching shouldn't fire a TRCC event."""
    bus, captured = bus_with_capture
    scan = _ScriptedScan([
        set(),
        {(0xDEAD, 0xBEEF)},                # not in registry
    ])
    monitor = PollingHotplugMonitor(scan=scan)
    monitor._bus = bus
    monitor._last_seen = set()
    monitor._tick()

    assert captured == []


def test_scan_exception_does_not_break_loop(
    bus_with_capture: tuple[EventBus, list[object]],
) -> None:
    """A failed scan should log + continue, not crash the thread."""
    bus, captured = bus_with_capture

    def boom() -> set[tuple[int, int]]:
        raise OSError("scan failed")

    monitor = PollingHotplugMonitor(scan=boom)
    monitor._bus = bus
    monitor._tick()                          # should not raise

    assert captured == []


# =========================================================================
# start() / stop() lifecycle (uses real threading; short interval)
# =========================================================================


def test_start_primes_initial_snapshot(
    bus_with_capture: tuple[EventBus, list[object]],
) -> None:
    """start() captures the current state so pre-attached devices
    don't trigger ATTACH on the first poll tick."""
    bus, captured = bus_with_capture

    snapshot = {(0x0402, 0x3922)}
    scan = _ScriptedScan([snapshot, snapshot, snapshot])
    monitor = PollingHotplugMonitor(scan=scan, interval_s=0.01)
    monitor.start(bus)
    try:
        # Give the thread a couple of ticks
        import time
        time.sleep(0.05)
        assert captured == []                # no spurious attach
    finally:
        monitor.stop()

    assert not monitor.is_running


def test_stop_is_idempotent_when_never_started() -> None:
    monitor = PollingHotplugMonitor(scan=lambda: set())
    monitor.stop()                            # must not raise
    assert monitor.is_running is False


def test_start_is_idempotent() -> None:
    monitor = PollingHotplugMonitor(scan=lambda: set(), interval_s=0.01)
    bus = EventBus()
    monitor.start(bus)
    try:
        first_thread = monitor._thread
        monitor.start(bus)                    # second call → no-op
        assert monitor._thread is first_thread
    finally:
        monitor.stop()
