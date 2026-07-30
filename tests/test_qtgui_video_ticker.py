"""qtgui's per-device video tickers + the double-render guard.

qtgui had ONE ticker running at ``AppSettings.refresh_interval_s`` (2 s by
default) that dispatched ``RenderAndSend`` for every device with an active
theme, and **nothing anywhere in qtgui advanced a playback cursor** — the three
advance sites were gui, cli and api only.  So a video theme rendered over and
over on frame 0: frozen video, the same defect as #249 on a fourth surface.

Fixing it needed more than pointing that ticker at ``TickDisplay``: at 2 s per
tick the video would advance one frame every two seconds, which reads as a
broken video rather than a still.  qtgui therefore grew per-device tickers at
the video's own rate, mirroring the gui skin's separate animation timer — which
in turn means a device could be rendered by BOTH its video ticker and the
metrics ticker.  These tests pin the guard against that.

The window's ``_on_tick`` / ``_on_video_*`` bodies are exercised directly on an
uninitialised instance: they touch only ``_app`` / ``_video`` / ``_ticker``, so
this needs no QApplication, no panels and no real device — keeping a UI
invariant testable at unit speed.
"""
from __future__ import annotations

from typing import Any

from trcc.core.commands import RenderAndSend, TickDisplay
from trcc.ui.qtgui.app import MainWindow


class _FakeTicker:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class _FakeUpdater:
    """Stands in for PeriodicUpdater — only ``is_active`` is read by _on_tick."""

    def __init__(self, active: bool = True) -> None:
        self._active = active
        self.started: list[int] = []
        self.stopped = 0

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self, interval_ms: int, callback: Any) -> None:
        self.started.append(interval_ms)
        self._active = True

    def stop(self) -> None:
        self.stopped += 1
        self._active = False


class _FakeApp:
    def __init__(self, keys: list[str]) -> None:
        self.active_themes = dict.fromkeys(keys, object())
        self.dispatched: list[tuple[str, str]] = []

    def dispatch(self, cmd: Any) -> Any:
        self.dispatched.append((type(cmd).__name__, cmd.key))
        return None


class _Event:
    def __init__(self, key: str, interval_ms: int = 33, frame_count: int = 30) -> None:
        self.key = key
        self.interval_ms = interval_ms
        self.frame_count = frame_count


def _window(app: _FakeApp) -> MainWindow:
    """A MainWindow with only the tick collaborators wired — no Qt setup."""
    win = MainWindow.__new__(MainWindow)
    win._app = app                      # type: ignore[assignment]
    win._video = {}                     # type: ignore[assignment]
    win._ticker = _FakeTicker()         # type: ignore[assignment]
    return win


def test_metrics_ticker_renders_every_device_when_no_video() -> None:
    """Baseline: without a playback the metrics ticker drives every device."""
    app = _FakeApp(["0402:3922", "87ad:70db"])

    _window(app)._on_tick()

    assert app.dispatched == [
        ("RenderAndSend", "0402:3922"),
        ("RenderAndSend", "87ad:70db"),
    ]


def test_metrics_ticker_skips_a_device_its_video_ticker_owns() -> None:
    """THE GUARD: a video device is rendered once per frame, not twice.

    Its own ticker already renders it at frame rate; letting the metrics ticker
    render it too would double that device's wire traffic for no benefit.  Same
    rule the gui skin states as "animation timer owns the wire".
    """
    app = _FakeApp(["0402:3922", "87ad:70db"])
    win = _window(app)
    win._video["0402:3922"] = _FakeUpdater(active=True)   # type: ignore[index]

    win._on_tick()

    assert app.dispatched == [("RenderAndSend", "87ad:70db")], (
        "the video-driven device must not also be rendered by the metrics ticker"
    )


def test_a_stopped_video_ticker_returns_the_device_to_the_metrics_ticker() -> None:
    """An inactive updater must not strand its device with nothing rendering it."""
    app = _FakeApp(["0402:3922"])
    win = _window(app)
    win._video["0402:3922"] = _FakeUpdater(active=False)  # type: ignore[index]

    win._on_tick()

    assert app.dispatched == [("RenderAndSend", "0402:3922")]


def test_video_started_paces_the_device_at_the_events_interval() -> None:
    """The per-device ticker runs at the video's rate, not refresh_interval_s.

    ``interval_ms`` rides on the event (derived from the playback's fps
    server-side), so qtgui never queries MediaService for it.  33 ms is 30 fps;
    the metrics ticker's 2 s default would have shown one frame every two
    seconds.

    Seeds the updater so no real QTimer needs a parented widget — constructing
    one requires a fully-initialised QMainWindow, which is what this module
    deliberately avoids.
    """
    app = _FakeApp(["0402:3922"])
    win = _window(app)
    seeded = _FakeUpdater(active=False)
    win._video["0402:3922"] = seeded                      # type: ignore[index]

    win._on_video_started(_Event("0402:3922", interval_ms=33))

    assert seeded.started == [33], "must pace at the event's interval"
    assert seeded.is_active
    # The callback it was given dispatches the ANIMATION tick for this device.
    win._on_video_tick("0402:3922")
    assert ("TickDisplay", "0402:3922") in app.dispatched


def test_restarting_a_video_repaces_the_same_updater() -> None:
    """A re-started video must not leave two tickers firing for one device.

    PeriodicUpdater drops its previous connection on restart; this pins that
    qtgui reuses the existing updater rather than stacking a second one.
    """
    app = _FakeApp(["0402:3922"])
    win = _window(app)
    seeded = _FakeUpdater(active=True)
    win._video["0402:3922"] = seeded                      # type: ignore[index]

    win._on_video_started(_Event("0402:3922", interval_ms=33))
    win._on_video_started(_Event("0402:3922", interval_ms=66))

    assert len(win._video) == 1                           # type: ignore[arg-type]
    assert win._video["0402:3922"] is seeded              # type: ignore[index]
    assert seeded.started == [33, 66]


def test_video_stopped_drops_the_devices_ticker() -> None:
    """Stopping playback releases the ticker so it can't keep firing."""
    app = _FakeApp(["0402:3922"])
    win = _window(app)
    fake = _FakeUpdater(active=True)
    win._video["0402:3922"] = fake                        # type: ignore[index]

    win._on_video_stopped(_Event("0402:3922"))

    assert fake.stopped == 1
    assert "0402:3922" not in win._video                  # type: ignore[operator]


def test_video_tick_dispatches_the_animation_command_not_the_rerender_one() -> None:
    """Role check: the per-device ticker must use TickDisplay.

    If it used RenderAndSend the cursor would never move and qtgui would be
    back to frozen video — the bug this whole path exists to fix.
    """
    app = _FakeApp(["0402:3922"])

    _window(app)._on_video_tick("0402:3922")

    assert app.dispatched == [("TickDisplay", "0402:3922")]
    assert RenderAndSend.__name__ not in [c for c, _ in app.dispatched]
    assert TickDisplay.__name__ in [c for c, _ in app.dispatched]
