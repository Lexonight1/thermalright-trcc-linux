"""qtgui and video: the core ticks, qtgui keeps out of its way.

qtgui once had no video advance at all (a video theme sat on frame 0 — #249 on
a fourth surface), then grew per-device tickers at the video's rate.  Since
2026-09-25 the CORE ticks every playing video (``VideoLoop``), so qtgui's
tickers are gone: two tickers on one device play it at double speed, because
``Playback.advance`` counts calls, not time.

What qtgui still owns is the double-RENDER guard — its metrics ticker must not
render a device the video is already rendering at frame rate — so it tracks
which devices are playing from ``VideoStarted`` / ``VideoStopped``.

The window's ``_on_tick`` / ``_on_video_*`` bodies are exercised directly on an
uninitialised instance: no QApplication, no panels, no real device.
"""
from __future__ import annotations

from typing import Any

from trcc.ui.qtgui.app import MainWindow


class _FakeTicker:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class _FakeApp:
    def __init__(self, keys: list[str]) -> None:
        self.active_themes = dict.fromkeys(keys, object())
        self.dispatched: list[tuple[str, str]] = []

    def dispatch(self, cmd: Any) -> Any:
        name = type(cmd).__name__
        self.dispatched.append((name, getattr(cmd, "key", "")))
        if name == "ListDevices":
            # The REAL Result: the ticker asks which devices have a theme
            # instead of reading ``app.active_themes``, and a stub that
            # invents its own field set answers that by accident or not at
            # all.  ``active_themes`` stays here as the fixture's own record
            # of what was set up.
            from trcc.core.results import DeviceEntry, DevicesListResult

            return DevicesListResult(
                ok=True,
                devices=[DeviceEntry(key=k, connected=True,
                                     has_active_theme=True)
                         for k in self.active_themes],
            )
        return None


def _rendered(app: "_FakeApp") -> list[str]:
    """The devices the metrics ticker actually rendered.

    Asserting the WHOLE dispatch list made these tests fail when the ticker
    stopped reading ``app.active_themes`` and started asking ``ListDevices``
    — a bookkeeping dispatch, not a change in what gets rendered.  These tests
    are about which devices the ticker drives, so that is what they assert.
    """
    return [key for name, key in app.dispatched if name == "RenderAndSend"]


class _Event:
    def __init__(self, key: str, interval_ms: int = 33, frame_count: int = 30) -> None:
        self.key = key
        self.interval_ms = interval_ms
        self.frame_count = frame_count


def _window(app: _FakeApp) -> MainWindow:
    """A MainWindow with only the tick collaborators wired — no Qt setup."""
    win = MainWindow.__new__(MainWindow)
    win._app = app                      # type: ignore[assignment]
    win._playing = set()                # type: ignore[assignment]
    win._ticker = _FakeTicker()         # type: ignore[assignment]
    return win


def test_metrics_ticker_renders_every_device_when_no_video() -> None:
    app = _FakeApp(["0402:3922", "87ad:70db"])
    _window(app)._on_tick()
    assert _rendered(app) == ["0402:3922", "87ad:70db"]


def test_metrics_ticker_skips_a_device_that_is_playing_video() -> None:
    """The core's VideoLoop renders it at frame rate; rendering it again from
    the metrics ticker would double its wire traffic."""
    app = _FakeApp(["0402:3922", "87ad:70db"])
    win = _window(app)
    win._on_video_started(_Event("87ad:70db"))    # type: ignore[arg-type]
    win._on_tick()
    assert _rendered(app) == ["0402:3922"]


def test_a_stopped_video_returns_the_device_to_the_metrics_ticker() -> None:
    app = _FakeApp(["87ad:70db"])
    win = _window(app)
    win._on_video_started(_Event("87ad:70db"))    # type: ignore[arg-type]
    win._on_video_stopped(_Event("87ad:70db"))    # type: ignore[arg-type]
    win._on_tick()
    assert _rendered(app) == ["87ad:70db"]


def test_qtgui_never_advances_a_video_itself() -> None:
    """A second ticker would play the video at double speed (#249)."""
    app = _FakeApp(["87ad:70db"])
    win = _window(app)
    win._on_video_started(_Event("87ad:70db"))    # type: ignore[arg-type]
    win._on_tick()
    assert "TickDisplay" not in {name for name, _ in app.dispatched}
    assert not hasattr(win, "_on_video_tick")
