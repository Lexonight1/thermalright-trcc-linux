"""macOS never learned it had woken up (#283).

``SystemResumed`` makes ``App`` reconnect every attached device, because the
USB transport a device opened before the machine slept is stale on wake:
writes silently no-op and the panel stays blank until a restart.  That is
#189, fixed on Linux by subscribing to logind's ``PrepareForSleep``.

macOS has no logind.  It builds ``PollingHotplugMonitor`` and NO power
listener, so ``SystemResumed`` was never published there and the reporter
saw exactly the #189 symptom: "the display will intermittently revert to its
initial state and cannot be restored after waking from sleep".

The detector compares the WALL clock to the MONOTONIC one.  Both stop while
the machine sleeps, but the wall clock is read back from the RTC on wake and
catches up; the monotonic one does not.  The gap between them is the sleep.
Taking the DIFFERENCE is what makes it immune to the process merely being
starved of CPU — under starvation both advance together and the difference
stays at zero, where a naive "did wall time jump?" check would fire.
"""
from __future__ import annotations

import pytest

from trcc.core.events import EventBus, SystemResumed


class _Clock:
    """A wall clock and a monotonic clock that can be moved independently."""

    def __init__(self) -> None:
        self.wall = 1_000_000.0
        self.mono = 500.0

    def advance(self, wall: float, mono: float) -> None:
        self.wall += wall
        self.mono += mono


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    from trcc.adapters.system import _hotplug

    c = _Clock()
    monkeypatch.setattr(_hotplug.time, "time", lambda: c.wall)
    monkeypatch.setattr(_hotplug.time, "monotonic", lambda: c.mono)
    return c


@pytest.fixture
def woke(clock):
    """(detector, events) — returns how many wakes were announced."""
    from trcc.adapters.system._hotplug import _ClockJumpWakeDetector

    bus = EventBus()
    seen: list = []
    bus.subscribe(SystemResumed, seen.append)
    detector = _ClockJumpWakeDetector()
    detector.start(bus)
    return detector, seen


def test_idle_ticks_announce_nothing(clock, woke) -> None:
    detector, seen = woke
    for _ in range(5):
        clock.advance(1.0, 1.0)
        detector.tick()
    assert seen == []


def test_cpu_STARVATION_is_not_a_wake(clock, woke) -> None:
    """THE case a naive wall-clock check gets wrong.

    Thirty seconds where the process simply did not run: the wall clock
    advanced thirty seconds and so did the monotonic one, so nothing slept.
    Reconnecting here would blank a working panel for no reason.
    """
    detector, seen = woke
    clock.advance(30.0, 30.0)
    detector.tick()
    assert seen == []


def test_an_ntp_step_is_not_a_wake(clock, woke) -> None:
    """A two-second correction sits under the threshold on purpose."""
    detector, seen = woke
    clock.advance(3.0, 1.0)
    detector.tick()
    assert seen == []


def test_a_clock_stepped_BACKWARD_is_not_a_wake(clock, woke) -> None:
    """A negative gap is a correction, never a resume."""
    detector, seen = woke
    clock.advance(-59.0, 1.0)
    detector.tick()
    assert seen == []


@pytest.mark.parametrize("slept", [60.0, 300.0, 7200.0])
def test_a_real_sleep_announces_exactly_one_wake(clock, woke, slept) -> None:
    detector, seen = woke
    clock.advance(slept + 1.0, 1.0)
    detector.tick()
    assert len(seen) == 1

    for _ in range(3):                       # and does not repeat afterwards
        clock.advance(1.0, 1.0)
        detector.tick()
    assert len(seen) == 1


def test_before_start_it_is_inert(clock) -> None:
    """No bus means no publish — ``tick`` must not raise or buffer."""
    from trcc.adapters.system._hotplug import _ClockJumpWakeDetector

    detector = _ClockJumpWakeDetector()
    clock.advance(9999.0, 1.0)
    detector.tick()          # must not raise

    bus = EventBus()
    seen: list = []
    bus.subscribe(SystemResumed, seen.append)
    detector.start(bus)
    clock.advance(1.0, 1.0)
    detector.tick()
    assert seen == [], "start() must PRIME the marks, not inherit a stale gap"


def test_stop_makes_it_inert_again(clock, woke) -> None:
    detector, seen = woke
    detector.stop()
    clock.advance(9999.0, 1.0)
    detector.tick()
    assert seen == []


# ── end to end: the panel actually comes back ────────────────────────────


def test_a_sleep_reconnects_every_attached_device(clock, tmp_home) -> None:
    """The whole point — ``SystemResumed`` is only useful if it recovers.

    Driven on a real ``App`` with a real ``EventBus`` and the real
    ``ConnectDevice`` path, because the detector publishing an event proves
    nothing about the panel coming back.
    """
    from pathlib import Path

    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.adapters.system._hotplug import _ClockJumpWakeDetector
    from trcc.app import App
    from trcc.core.commands import ConnectDevice
    from trcc.core.events import DeviceConnected

    from .conftest import FakePlatform, _CliRenderer

    key = "0402:3922"
    app = App(platform=FakePlatform(Path(tmp_home)),
              send_scheduler=SyncSendScheduler(),
              renderer=_CliRenderer())        # type: ignore[arg-type]
    handshake = bytearray(0xE100)
    handshake[0] = 100                        # FBL=100 -> 320x320
    app.platform.scsi.read_script.append(bytes(handshake))   # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=key)).ok

    reconnected: list[str] = []
    app.events.subscribe(DeviceConnected, lambda e: reconnected.append(e.key))

    detector = _ClockJumpWakeDetector()
    detector.start(app.events)

    clock.advance(1.0, 1.0)
    detector.tick()
    assert reconnected == [], "an ordinary tick must not churn the device"

    for _ in range(6):                        # the reconnect re-handshakes
        app.platform.scsi.read_script.append(bytes(handshake))  # type: ignore[attr-defined]
    clock.advance(300.0, 1.0)                 # five minutes asleep
    detector.tick()

    assert reconnected == [key]


def test_only_macos_gets_this_detector() -> None:
    """Linux already hears ``PrepareForSleep`` from logind.

    Wiring the detector into a monitor Linux also used would reconnect every
    device TWICE per wake.  ``PollingHotplugMonitor`` is built by macOS and
    nothing else, which is why it is the right host for it.
    """
    import inspect

    from trcc.adapters.system import linux, macos

    assert "PollingHotplugMonitor" in inspect.getsource(macos)
    assert "PollingHotplugMonitor" not in inspect.getsource(linux)


def test_the_polling_monitor_actually_TICKS_the_detector(clock) -> None:
    """The join, which nothing else covers.

    The detector is unit-tested above and the monitor's diff is tested
    elsewhere, and NEITHER notices if ``_poll_loop`` stops calling
    ``self._wake.tick()``.  MEASURED: deleting that line left all eleven
    other tests in this file green, which is how a fix silently reverts.

    One full iteration is forced by having the scan callable set the stop
    event, so the loop runs exactly once and exits — no sleeps, no races.
    """
    from trcc.adapters.system._hotplug import PollingHotplugMonitor

    bus = EventBus()
    seen: list = []
    bus.subscribe(SystemResumed, seen.append)

    monitor = PollingHotplugMonitor(scan=lambda: set(), interval_s=0.01)
    monitor.start(bus)
    try:
        clock.advance(300.0, 1.0)             # slept while the loop waited
        monitor._scan = _StopAfterOne(monitor)
        monitor._poll_loop()
    finally:
        monitor.stop()

    assert len(seen) == 1, (
        "_poll_loop must tick the wake detector — deleting that call is "
        "invisible to every other test here"
    )


class _StopAfterOne:
    """A scan callable that ends the loop it is running inside."""

    def __init__(self, monitor) -> None:
        self._monitor = monitor

    def __call__(self):
        self._monitor._stop_event.set()
        return set()
