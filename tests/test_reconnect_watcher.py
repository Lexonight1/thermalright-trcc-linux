"""The reconnect watcher's policy: when it tries, and when it stops.

Driven with a scripted ``try_reconnect`` and a hand-ticked clock -- the policy
is the unit here.  The App end of it (a panel a VM hands back with no event)
is in ``test_replug_sender_recovery.py``.
"""
from __future__ import annotations

from typing import Any

from trcc.services.reconnect_watcher import ReconnectWatcher

_KEY = "0402:3922"


class _App:
    """Answers ``try_reconnect`` from a script and records each call's time."""

    def __init__(self, *outcomes: bool) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def try_reconnect(self, key: str) -> bool:
        assert key == _KEY
        self.calls += 1
        return self.outcomes.pop(0)


def _watcher(app: _App) -> ReconnectWatcher:
    watcher = ReconnectWatcher(app, _KEY)  # type: ignore[arg-type]
    watcher.arm()
    return watcher


def _attempt_times(watcher: ReconnectWatcher, app: _App, until: float) -> list[float]:
    """Tick the clock every 0.5 s and note when an attempt happened."""
    times: list[float] = []
    now = 0.0
    while now <= until:
        before = app.calls
        watcher.run_once(now)
        if app.calls > before:
            times.append(now)
        now += 0.5
    return times


def test_the_wait_doubles_from_three_seconds_to_a_minute() -> None:
    app = _App(*[False] * 9)
    watcher = _watcher(app)

    times = _attempt_times(watcher, app, until=333)

    assert times == [3, 9, 21, 45, 93, 153, 213, 273, 333]


def test_it_stops_once_the_panel_is_back() -> None:
    app = _App(False, False, True)
    watcher = _watcher(app)

    _attempt_times(watcher, app, until=300)

    assert app.calls == 3
    assert not watcher.armed


def test_arming_again_while_waiting_keeps_the_backoff() -> None:
    """Each failed attempt reports itself as a failed connect, which arms the
    watcher again -- a reset there would pin it at 3 s forever."""
    app = _App(*[False] * 4)
    watcher = _watcher(app)

    times: list[float] = []
    now = 0.0
    while now <= 50:
        before = app.calls
        watcher.run_once(now)
        if app.calls > before:
            times.append(now)
            watcher.arm()
        now += 0.5

    assert times == [3, 9, 21, 45]


def test_a_disarmed_watcher_never_tries() -> None:
    app: Any = _App()
    watcher = _watcher(app)
    watcher.disarm()

    assert _attempt_times(watcher, app, until=300) == []


def test_a_panel_that_never_answered_is_tried_at_most_every_five_minutes() -> None:
    app = _App(*[False] * 9)
    watcher = ReconnectWatcher(app, _KEY)  # type: ignore[arg-type]
    watcher.arm(slow=True)

    times = _attempt_times(watcher, app, until=981)

    assert times == [3, 9, 21, 45, 93, 189, 381, 681, 981]
