"""``App.start_session`` — the partner of ``App.close``.

Its four calls used to be copy-pasted into ``run_daemon``, ``run_gui`` and
``run_qtgui``; the API had none of them, and the daemon's copy was missing
``metrics_loop.start()`` — a reporter ran ``trccd.service`` and watched a
connected device stay permanently blank (#148).

The coldplug half is the one the daemon never had at all: only Linux's hotplug
monitor replays already-present devices, so on Windows / macOS / BSD a daemon
came up owning USB with nothing connected.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.conftest import FakeCpu, FakePlatform
from trcc.app import App


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


def _spy_on_coldplug(monkeypatch) -> list[object]:
    """Record each ``discover_and_connect`` call, still running the real one.

    Replacing it outright would drop the ``_coldplug_done`` side effect that
    ``start_session`` reads — the double would then model the contract wrong
    and the test would fail against correct code.
    """
    calls: list[object] = []
    real = App.discover_and_connect

    def spy(self: App, on_progress=None) -> None:
        calls.append(on_progress)
        real(self, on_progress)

    monkeypatch.setattr(App, "discover_and_connect", spy)
    return calls


def test_start_session_runs_the_coldplug(app: App, monkeypatch) -> None:
    """The half the daemon never had — without it a device is never connected
    on any OS whose hotplug monitor reports only NEW devices."""
    calls = _spy_on_coldplug(monkeypatch)

    app.start_session()

    assert calls == [None], "start_session must coldplug"


def test_start_session_starts_all_three_loops(app: App) -> None:
    """#148 was one missing line out of these three."""
    app.start_session()
    try:
        assert app.metrics_loop.is_running, "metrics loop not started (#148)"
        assert app.led_animation_loop.is_running, "LED animation loop not started"
        assert app.platform.hotplug().is_running, "hotplug listener not started"
    finally:
        app.close()


def test_a_coldplug_that_already_ran_is_not_repeated(app: App, monkeypatch) -> None:
    """gui discovers on its splash worker and then calls start_session.  A
    second coldplug would re-attempt every device that failed and record its
    failure twice."""
    calls = _spy_on_coldplug(monkeypatch)

    app.discover_and_connect()          # the splash worker's call
    app.start_session()

    assert len(calls) == 1, "start_session repeated a coldplug the splash did"


def test_start_session_is_idempotent(app: App, monkeypatch) -> None:
    seen = _spy_on_coldplug(monkeypatch)
    try:
        app.start_session()
        app.start_session()
        assert len(seen) == 1
        assert app.metrics_loop.is_running
    finally:
        app.close()


def test_on_progress_reaches_the_coldplug(app: App, monkeypatch) -> None:
    """qtgui passes nothing; gui's splash passes a Qt signal so per-device
    status shows while connecting."""
    seen = _spy_on_coldplug(monkeypatch)

    sink: list[str] = []
    app.start_session(sink.append)

    assert seen == [sink.append]


def test_close_undoes_start_session(app: App) -> None:
    """The two are a pair; close must stop everything start_session began."""
    app.start_session()
    app.close()

    assert not app.metrics_loop.is_running
    assert not app.led_animation_loop.is_running
    assert not app.platform.hotplug().is_running


# ── The refresh interval must reach the SWEEP, not just the broadcast ──


def test_set_refresh_interval_changes_the_observed_sweep_RATE(app: App) -> None:
    """The user's one CPU lever, driven end to end and COUNTED.

    Before the fix, publishes fell 0.50/s -> 0.08/s on a real App while sweeps
    stayed at 0.50/s: ``_interval_s`` was written once at ``start_polling``,
    which early-returns while its thread is alive.

    This asserts the RATE, not the field.  The first version of this gate read
    back ``sensors._interval_s`` and called it proved — the same value
    assertion this very module's docstring criticises, and it cannot tell a
    stored number from a thread that acted on it.  The seam that actually
    broke is *Command dispatched -> thread re-cadences*, and only counting
    sweeps crosses it.

    The bar is arithmetic, not a fudge: at the 2 s default at most 2 sweeps
    can land in a 3.5 s window, so seeing 3 is reachable ONLY at 1 s.

    MUTATION CHECK: drop the ``set_interval`` push from ``MetricsLoop._loop``
    and this fails having seen 1-2 sweeps in the window.
    """
    from trcc.core.commands import SetRefreshInterval
    from trcc.core.models import DEFAULT_REFRESH_INTERVAL_S

    sensors = app.platform.sensors()
    sweeps: list[float] = []
    real = sensors._poll_once

    def counted() -> None:
        sweeps.append(time.monotonic())
        real()

    sensors._poll_once = counted                    # type: ignore[method-assign]

    assert DEFAULT_REFRESH_INTERVAL_S >= 2.0, (
        "this gate's arithmetic assumes the default is >= 2 s; at a faster "
        "default, 3 sweeps in 3.5 s no longer distinguishes the two cadences"
    )

    app.metrics_loop.start()
    try:
        assert app.dispatch(SetRefreshInterval(seconds=1.0)).ok

        # Let the push land, then start counting from a clean mark.
        deadline = time.monotonic() + 3.0
        while sensors._interval_s != 1.0 and time.monotonic() < deadline:
            time.sleep(0.01)
        sweeps.clear()
        window_end = time.monotonic() + 3.5
        while time.monotonic() < window_end:
            time.sleep(0.05)

        assert len(sweeps) >= 3, (
            f"only {len(sweeps)} sweep(s) in 3.5 s — at the {DEFAULT_REFRESH_INTERVAL_S}s "
            f"default at most 2 can land, so the sweep never took the 1 s "
            f"cadence the user asked for"
        )
    finally:
        app.metrics_loop.stop()


# ── The broadcast must carry a SWEPT reading, and a FRESH one ──
#
# ``MetricsLoop.start`` starts the sensor poll thread and then starts its own,
# which publishes at the TOP of its loop.  The two then run as INDEPENDENT
# timers on one period.  Both defects below follow from that, and one fix
# closes both: publish when a sweep completes instead of on a private clock.
#
# Measured 2026-09-19 driving the real ``MetricsLoop`` against the host's real
# sensors:
#
#   * first publish, 3 trials -> one carried ``n=0`` readings and
#     ``cpu_temp=0.0``, with real values arriving 2.05 s later.  The first
#     sweep includes discovery and can take >45 ms, so it is a RACE and it is
#     intermittent — a user sees 0 °C at launch on some starts and not others.
#   * staleness, 200 publishes at a 1 s interval -> a SAWTOOTH from 0.041 s to
#     0.937 s, mean **0.500 s**, wrapping about every 50 s.  Half an interval
#     of lag on average, cycling through the whole range.


class _SlowCpu(FakeCpu):
    """``FakeCpu`` whose FIRST read is slow enough to LOSE the start race.

    The race is real but intermittent on a fast box: whether the first publish
    beats the first sweep depends on how long discovery takes.  Gating it by
    starting repeatedly and hoping is a flaky test that proves nothing on a
    quick machine.  Forcing the losing condition makes it deterministic — the
    sweep is simply slower than the publisher's head start, which is the state
    a real first sweep is in while it discovers.

    It SUBCLASSES the fake rather than reimplementing a source: the first
    version of this was a bare class with ``temp``/``usage`` only, and
    ``_poll_once`` died on ``self._cpu.freq`` — so the sweep failed outright
    and the gate went red for a reason that had nothing to do with the race it
    was written to catch.
    """

    def __init__(self, delay_s: float = 0.25) -> None:
        super().__init__()
        self._delay_s = delay_s
        self._first = True

    def temp(self) -> float | None:
        if self._first:
            self._first = False
            time.sleep(self._delay_s)
        return super().temp()


def _subscribe_publishes(app: App) -> list[tuple[int, float]]:
    """(reading_count, cache age at publish) for every ``SensorsUpdated``."""
    from trcc.core.events import SensorsUpdated

    sensors = app.platform.sensors()
    seen: list[tuple[int, float]] = []

    def record(event: SensorsUpdated) -> None:
        with sensors._lock:
            age = time.monotonic() - sensors._last_poll
        seen.append((event.reading_count, age))

    app.events.subscribe(SensorsUpdated, record)
    return seen


def test_the_first_broadcast_is_never_an_unswept_cache(app: App) -> None:
    """No ``SensorsUpdated`` may carry readings no sweep has produced.

    The publisher wins the race against a slow first sweep and broadcasts the
    EMPTY bootstrap cache: subscribers — the system-info panel, the activity
    sidebar, every LCD metric overlay — then display 0 °C and hold it for a
    full refresh interval.

    MUTATION CHECK: restore the publish-then-wait order in ``MetricsLoop._loop``
    and this fails with ``0 readings``.
    """
    app.platform.sensors()._cpu = _SlowCpu()
    seen = _subscribe_publishes(app)

    app.metrics_loop.start()
    try:
        deadline = time.monotonic() + 3.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        app.metrics_loop.stop()

    assert seen, "no SensorsUpdated at all within 3 s — the loop never published"
    first_count, _ = seen[0]
    assert first_count > 0, (
        "the first SensorsUpdated carried an UNSWEPT cache — every subscriber "
        "shows 0 for a full refresh interval, and only on the starts where "
        "the publisher wins the race"
    )


def test_a_broadcast_carries_a_freshly_swept_reading(app: App) -> None:
    """Published readings must come from the sweep that just ran.

    Two independent timers on one period drift, so the age of the data at
    publish walks the whole 0..interval range — mean half an interval.  Bound
    it at a QUARTER, which a sweep-driven publisher clears by orders of
    magnitude (its age is one sweep duration) and which two free-running
    timers cannot hold: their mean alone is twice this bar.

    MUTATION CHECK: drop ``on_sweep`` from the ``start_polling`` call in
    ``MetricsLoop.start`` and the loop free-runs again; this fails on the mean.
    """
    from trcc.core.commands import SetRefreshInterval

    assert app.dispatch(SetRefreshInterval(seconds=1.0)).ok
    seen = _subscribe_publishes(app)

    app.metrics_loop.start()
    try:
        time.sleep(4.5)
    finally:
        app.metrics_loop.stop()

    # Only publishes that CARRY readings: the age of data that does not exist
    # is not a number, and an unswept first broadcast is the gate above's
    # business.  Mixing them put a 6,598 s sentinel in the mean.
    ages = [age for count, age in seen if count > 0]
    assert len(ages) >= 3, (
        f"only {len(ages)} publish(es) with readings in 4.5 s at a 1 s "
        f"interval (saw {len(seen)} total)")
    mean_age = sum(ages) / len(ages)
    assert mean_age < 0.25, (
        f"published readings average {mean_age:.3f}s old on a 1.0s interval "
        f"(samples: {[f'{a:.3f}' for a in ages]}) — the broadcast is running "
        f"on its own clock instead of the sweep's"
    )


def test_broadcasts_continue_when_the_poll_thread_never_starts(
    app: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep drives the cadence, but it must not be the ONLY thing that can.

    ``MetricsLoop`` now waits on a sweep instead of its own clock, and
    ``start_polling`` is wrapped in a try/except that only warns — so a broken
    sensor adapter would leave the loop waiting for a sweep that never comes
    and every subscriber frozen forever.  The bounded wait is what prevents
    that, and nothing else asserts it: replace it with a blocking
    ``_wake.wait()`` and the whole suite still passes, because in every other
    test the sweeps do arrive.

    The degraded cadence is HALVED (``_SWEEP_GRACE``), which is the deliberate
    trade — a fallback that ties with a healthy sweep's period fires just
    before it and double-publishes, one of them a full interval stale.

    MUTATION CHECK: drop the timeout from the ``_wake.wait`` in
    ``MetricsLoop._loop`` and this fails having seen no broadcast.
    """
    from trcc.core.commands import SetRefreshInterval

    assert app.dispatch(SetRefreshInterval(seconds=1.0)).ok

    def refuse(*_a: object, **_k: object) -> None:
        raise RuntimeError("sensor adapter is broken")

    monkeypatch.setattr(app.platform.sensors(), "start_polling", refuse)
    seen = _subscribe_publishes(app)

    app.metrics_loop.start()
    try:
        deadline = time.monotonic() + 4.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        app.metrics_loop.stop()

    assert seen, (
        "no SensorsUpdated at all with the poll thread dead — the loop is "
        "waiting on a sweep that will never arrive and every subscriber is "
        "frozen for the life of the process"
    )
    count, _ = seen[0]
    assert count > 0, (
        "the fallback published an empty reading set — with no poll thread, "
        "read_all() is supposed to sweep inline on this thread"
    )


def test_building_an_app_builds_no_sensors(tmp_path: Path) -> None:
    """Constructing the App must not build the sensor stack.

    Every one-shot command builds an App -- ``trcc kill`` and
    ``daemon-status`` among them -- and on a Windows install building the
    sensors spawns LibreHardwareMonitor and waits up to 10 s for it.  The
    overlay's "unsupported here" set forced that build on every App from
    2026-09-15 until it became a callable asked only when a metric is blank.
    """
    from trcc.adapters.render.qt import QtRenderer

    platform = FakePlatform(tmp_path)
    App(platform=platform, renderer=QtRenderer())

    assert platform._sensors is None, "constructing the App built the sensors"

